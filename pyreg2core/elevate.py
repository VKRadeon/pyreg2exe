# -*- coding: utf-8 -*-
"""
Privilege helpers (PROJECT_CONTEXT.md §6.7, B2).

The elevated child is started through ``ShellExecuteExW`` with the verb
``runas`` and the parent **waits** for it, so that:

* the exit code of the child can be relayed to the caller;
* the onefile bootstrap of the parent does not tear down the temporary
  extraction directory before the child has finished.

``subprocess.list2cmdline`` is used for argument quoting because it implements
the same rules as ``CommandLineToArgvW``, which the child uses to parse them.
"""

import os
import subprocess

from . import selfpath
from .errors import ElevationError

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
ERROR_CANCELLED = 1223
SW_HIDE = 0
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
INFINITE = 0xFFFFFFFF

_WINDOWS = os.name == "nt"


def is_admin():
    """True when the current process is elevated."""
    if not _WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def quote_arguments(arguments):
    """Render an argument list as one command-line string."""
    return subprocess.list2cmdline([str(item) for item in arguments])


def _shell_execute_info():
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    return SHELLEXECUTEINFOW


def run_elevated_and_wait(arguments, exe_path=None, show_window=SW_HIDE):
    """
    Start ``exe_path`` elevated with ``arguments`` and wait for it.

    Returns the child's exit code.  Raises ``ElevationError`` when the prompt
    is declined or the child cannot be started.
    """
    if not _WINDOWS:
        raise ElevationError("elevation is only supported on Windows")

    exe = exe_path or selfpath.running_image_path()
    if not exe or not os.path.isfile(exe):
        raise ElevationError(
            "cannot determine the path of the executable to elevate")

    import ctypes
    from ctypes import wintypes

    parameters = quote_arguments(arguments)
    info = _shell_execute_info()()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = parameters or None
    info.lpDirectory = os.path.dirname(exe) or None
    info.nShow = show_window

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            raise ElevationError(
                "the elevation prompt was cancelled; nothing was applied "
                "to the machine-wide keys")
        raise ElevationError(
            "cannot start the elevated copy (Windows error {0})".format(code))

    process = info.hProcess
    if not process:
        raise ElevationError("ShellExecuteExW returned no process handle")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        waited = kernel32.WaitForSingleObject(process, INFINITE)
        if waited not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            raise ElevationError(
                "waiting for the elevated copy failed (Windows error "
                "{0})".format(ctypes.get_last_error()))
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            raise ElevationError(
                "cannot read the exit code of the elevated copy (Windows "
                "error {0})".format(ctypes.get_last_error()))
        return int(exit_code.value)
    finally:
        kernel32.CloseHandle(process)
