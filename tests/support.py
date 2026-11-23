# -*- coding: utf-8 -*-
"""
Shared helpers for the pyreg2exe test suite.

Standard library only (PROJECT_CONTEXT.md §10.2): the tests must run on a
machine without pytest, coverage or any other third-party package.
"""

import codecs
import contextlib
import os
import sys
import tempfile
import unittest
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pyreg2core import overlay                       # noqa: E402
from pyreg2core.diag import Log                      # noqa: E402
from pyreg2core.reg_types import Mode, RegRoot       # noqa: E402

HEADER = "Windows Registry Editor Version 5.00"
SANDBOX_ROOT = "Software\\__pyreg2exe_tests__"
IS_WINDOWS = os.name == "nt"


def reg_text(*lines, **kwargs):
    """Build .reg text with CRLF line endings and an optional header."""
    header = kwargs.pop("header", True)
    if kwargs:
        raise TypeError("unexpected keyword arguments: {0}".format(kwargs))
    parts = [HEADER, ""] if header else []
    parts.extend(lines)
    return "\r\n".join(parts) + "\r\n"


def temp_dir(prefix="pyreg2exe-test-"):
    """Create a temporary directory object usable as a context manager."""
    return tempfile.TemporaryDirectory(prefix=prefix)


def fake_pe(size=4096):
    """Byte string that satisfies ``overlay.validate_pe_image``."""
    if size < 0x88:
        raise ValueError("size too small for a fake PE image")
    blob = bytearray(b"\x00" * size)
    blob[0:2] = b"MZ"
    pe_offset = 0x80
    blob[0x3C:0x40] = pe_offset.to_bytes(4, "little")
    blob[pe_offset:pe_offset + 4] = b"PE\0\0"
    return bytes(blob)


def patch_bytes(text, mode=Mode.HKCU, bom=True):
    """Serialise .reg text into a standalone overlay tail."""
    data = text.encode("utf-16-le")
    if bom:
        data = codecs.BOM_UTF16_LE + data
    return overlay.encode_blob(data, mode)


def write_patch_file(path, text, mode=Mode.HKCU, bom=True):
    """Write a standalone ``.regpatch`` file and return its path."""
    with open(path, "wb") as handle:
        handle.write(patch_bytes(text, mode=mode, bom=bom))
    return path


def write_reg_file(path, text, encoding="utf-16-le", bom=True):
    """Write a .reg file with the requested encoding."""
    data = text.encode(encoding)
    if bom and encoding == "utf-16-le":
        data = codecs.BOM_UTF16_LE + data
    elif bom and encoding == "utf-8":
        data = codecs.BOM_UTF8 + data
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class LogCapture(Log):
    """Log that records messages instead of writing them anywhere."""

    def __init__(self):
        super().__init__(path=None, quiet=True, echo=False)
        self.messages = []

    def _emit(self, level, message):
        self.messages.append((level, str(message)))

    @property
    def text(self):
        return "\n".join(message for _, message in self.messages)

    def levels(self, level):
        return [message for name, message in self.messages if name == level]


def quiet_log():
    return Log(path=None, quiet=True, echo=False)


def require_windows(testcase):
    if not IS_WINDOWS:
        testcase.skipTest("the registry is only available on Windows")


def _delete_tree(handle, path):
    import winreg

    try:
        key = winreg.OpenKey(handle, path, 0,
                             winreg.KEY_READ | winreg.KEY_WRITE)
    except FileNotFoundError:
        return
    children = []
    try:
        index = 0
        while True:
            try:
                children.append(winreg.EnumKey(key, index))
            except OSError:
                break
            index += 1
    finally:
        winreg.CloseKey(key)

    for child in children:
        _delete_tree(handle, path + "\\" + child)
    try:
        winreg.DeleteKey(handle, path)
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def sandbox():
    """Yield a unique HKCU key path and remove the whole tree afterwards."""
    if not IS_WINDOWS:
        raise unittest.SkipTest("the registry is only available on Windows")
    import winreg

    path = "{0}\\{1}".format(SANDBOX_ROOT, uuid.uuid4().hex[:12])
    try:
        yield path
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, path)
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SANDBOX_ROOT)
        except OSError:
            pass
        assert not key_exists(SANDBOX_ROOT), "sandbox key was not removed"


class SandboxMixin:
    """Mixin for TestCase classes that need an isolated HKCU key."""

    def make_sandbox(self):
        require_windows(self)
        manager = sandbox()
        path = manager.__enter__()
        self.addCleanup(lambda: manager.__exit__(None, None, None))
        return path


def key_exists(path, root=RegRoot.HKCU):
    import winreg

    handles = {
        RegRoot.HKCU: winreg.HKEY_CURRENT_USER,
        RegRoot.HKLM: winreg.HKEY_LOCAL_MACHINE,
    }
    try:
        key = winreg.OpenKey(handles[root], path, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return False
    winreg.CloseKey(key)
    return True


def read_value(path, name, root=RegRoot.HKCU):
    """Return ``(data, type)`` of a value in the sandbox."""
    import winreg

    handles = {
        RegRoot.HKCU: winreg.HKEY_CURRENT_USER,
        RegRoot.HKLM: winreg.HKEY_LOCAL_MACHINE,
    }
    with winreg.OpenKey(handles[root], path, 0, winreg.KEY_READ) as key:
        return winreg.QueryValueEx(key, name)


def value_names(path, root=RegRoot.HKCU):
    import winreg

    handles = {
        RegRoot.HKCU: winreg.HKEY_CURRENT_USER,
        RegRoot.HKLM: winreg.HKEY_LOCAL_MACHINE,
    }
    with winreg.OpenKey(handles[root], path, 0, winreg.KEY_READ) as key:
        names = []
        index = 0
        while True:
            try:
                names.append(winreg.EnumValue(key, index)[0])
            except OSError:
                return names
            index += 1
