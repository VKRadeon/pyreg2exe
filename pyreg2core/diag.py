# -*- coding: utf-8 -*-
"""
Diagnostics: logging to a file and a console writer that cannot crash.

The loader runs windowless, where ``sys.stdout``/``sys.stderr`` may be ``None``
or may reject non-ASCII text; the builder keeps its console (PROJECT_CONTEXT.md
§6.9).  Both cases are handled here once, so no other module writes to a stream.
"""

import datetime
import os
import sys
import tempfile

LOG_DIR_NAME = "pyreg2exe"


def log_directory():
    """``%TEMP%\\pyreg2exe``; falls back to the current directory."""
    try:
        base = tempfile.gettempdir()
    except Exception:
        base = os.getcwd()
    return os.path.join(base, LOG_DIR_NAME)


def ensure_log_directory():
    directory = log_directory()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return None
    return directory


def safe_write(text, stream=None):
    """
    Write ``text`` plus a newline, never raising.

    Returns True when the text reached the stream.
    """
    target = stream if stream is not None else sys.stderr
    if target is None:
        return False
    try:
        target.write(text + "\n")
        target.flush()
        return True
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "ascii"
        try:
            target.write(
                (text + "\n").encode(encoding, "backslashreplace")
                .decode(encoding, "replace"))
            target.flush()
            return True
        except Exception:
            return False
    except (OSError, ValueError):
        return False


class Log:
    """Append-only text log plus optional console echo."""

    def __init__(self, path=None, verbose=False, quiet=False, echo=True):
        self.path = path
        self.verbose = bool(verbose)
        self.quiet = bool(quiet)
        self.echo = bool(echo) and not self.quiet
        self._handle = None
        if path:
            try:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                self._handle = open(path, "a", encoding="utf-8", newline="\n")
            except OSError:
                self._handle = None
                self.path = None

    # -- writing -------------------------------------------------------

    def _emit(self, level, message):
        line = "{0} {1:7s} {2}".format(
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level.upper(), message)
        if self._handle is not None:
            try:
                self._handle.write(line + "\n")
                self._handle.flush()
            except (OSError, ValueError):
                pass
        if self.echo:
            stream = sys.stderr if level in ("warning", "error") else sys.stdout
            safe_write("[" + level + "] " + str(message), stream)

    def debug(self, message):
        if self.verbose:
            self._emit("debug", message)

    def info(self, message):
        self._emit("info", message)

    def warning(self, message):
        self._emit("warning", message)

    def error(self, message):
        self._emit("error", message)

    def diagnostics(self, items, limit=None):
        """Write ``Diagnostic`` objects (warnings first, then errors)."""
        warnings = [item for item in items if not item.is_error]
        errors = [item for item in items if item.is_error]
        for item in warnings + errors:
            if item.is_error:
                self.error(str(item))
            else:
                self.warning(str(item))
        if limit is not None and len(errors) > limit:
            self.error("... {0} more error(s)".format(len(errors) - limit))

    def close(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except (OSError, ValueError):
                pass
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def patch_log_path(pid=None):
    """Path of the per-run log of the loader."""
    ensure_log_directory()
    return os.path.join(log_directory(),
                        "patch-{0}.log".format(os.getpid() if pid is None
                                               else pid))


def open_patch_log(verbose=False, quiet=False):
    """Create the loader log; a failure to create it is not fatal."""
    log = Log(patch_log_path(), verbose=verbose, quiet=quiet,
              echo=verbose)
    if log.path:
        log.info("pyreg2exe loader started, log: {0}".format(log.path))
    return log
