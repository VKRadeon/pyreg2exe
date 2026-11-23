# -*- coding: utf-8 -*-
"""
Locating our own executable (PROJECT_CONTEXT.md §6.2, B3).

The old code used ``os.path.abspath(sys.argv[0])``, which breaks when the
loader is started through ``PATH``, by a shortcut, by a onefile bootstrap or
with a relative path.  We therefore build an ordered list of candidates and
let the caller pick the first one that actually carries a valid overlay:

1. ``__compiled__.original_argv0`` — Nuitka's record of how we were started
   (for ``--onefile`` this is the *patch* executable, not the extracted copy);
2. ``GetModuleFileNameW(NULL)`` — the running image, used to relaunch elevated;
3. ``sys.argv[0]`` — resolved against the working directory and ``PATH``;
4. ``sys.executable`` — when it is not a Python interpreter;
5. ``__main__.__file__`` — plain source runs.

ctypes is imported lazily so that the pure modules stay importable anywhere.
"""

import os
import sys

_WINDOWS = os.name == "nt"


def is_compiled():
    """True when we run from a Nuitka/PyInstaller build."""
    if "__compiled__" in globals():
        return True
    if getattr(sys, "frozen", False):
        return True
    return False


def running_image_path():
    """Full path of the image that is executing right now."""
    if _WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            written = kernel32.GetModuleFileNameW(None, buffer, size)
            if written:
                if written >= size.value:
                    size = wintypes.DWORD(65536)
                    buffer = ctypes.create_unicode_buffer(size.value)
                    written = kernel32.GetModuleFileNameW(None, buffer, size)
                if 0 < written < size.value:
                    return os.path.abspath(buffer.value)
        except Exception:
            pass
    return _argv0_path()


def _argv0_path():
    candidate = sys.argv[0] if sys.argv else ""
    if not candidate:
        return ""
    candidate = os.path.abspath(candidate)
    if os.path.isfile(candidate):
        return candidate
    try:
        import shutil
        found = shutil.which(sys.argv[0])
        if found:
            return os.path.abspath(found)
    except Exception:
        pass
    return candidate


def compiled_argv0():
    """``__compiled__.original_argv0`` when running from a Nuitka build."""
    metadata = globals().get("__compiled__")
    if metadata is None:
        return ""
    for attribute in ("original_argv0", "original_filename"):
        value = getattr(metadata, attribute, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _main_module_file():
    module = sys.modules.get("__main__")
    path = getattr(module, "__file__", None)
    if path:
        return os.path.abspath(path)
    return ""


def candidate_paths():
    """Ordered, de-duplicated list of plausible paths of this program."""
    candidates = []
    compiled_argv0_value = compiled_argv0()
    if compiled_argv0_value:
        candidates.append(os.path.abspath(compiled_argv0_value))
    candidates.append(running_image_path())
    candidates.append(_argv0_path())
    executable = sys.executable or ""
    if executable and "python" not in os.path.basename(executable).lower():
        candidates.append(os.path.abspath(executable))
    candidates.append(_main_module_file())

    unique = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.abspath(candidate))
    return unique


def existing_candidates():
    """``candidate_paths`` filtered down to files that exist right now."""
    return [path for path in candidate_paths() if os.path.isfile(path)]


def own_directory():
    """Directory of the running image; used when no overlay is found."""
    for path in existing_candidates():
        return os.path.dirname(path)
    return os.getcwd()
