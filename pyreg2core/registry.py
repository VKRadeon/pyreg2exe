# -*- coding: utf-8 -*-
"""
The only module that touches the registry (PROJECT_CONTEXT.md §7.10).

Rules enforced here:

* values are expanded according to their type, names never are (B14);
* the minimum access right is requested for every action;
* every handle is closed on every path, including errors;
* a failing operation is recorded and the run continues; the aggregate result
  is a failure, which the loader maps to exit code 2 (B1);
* a missing key or value during deletion is a success (idempotent).
"""

from .errors import RegistryError
from .expand import VariableExpander
from .reg_types import ApplyReport, OpKind, RegType

try:
    import winreg
except ImportError:          # pragma: no cover - non-Windows hosts
    winreg = None

VIEW_FLAGS = {
    "default": 0,
    "32": 0x0200,            # KEY_WOW64_32KEY
    "64": 0x0100,            # KEY_WOW64_64KEY
}

_VIEW_ALIASES = {
    "": "default",
    "default": "default",
    "auto": "default",
    "32": "32",
    "32-bit": "32",
    "x86": "32",
    "wow64": "32",
    "64": "64",
    "64-bit": "64",
    "x64": "64",
}


def normalize_view(view):
    """Map a user-supplied view name onto ``default``/``32``/``64``."""
    key = "default" if view is None else str(view).strip().lower()
    if key in _VIEW_ALIASES:
        return _VIEW_ALIASES[key]
    raise RegistryError(
        "unknown registry view {0!r} (expected default, 32 or 64)"
        .format(view))


class RegistryApplier:
    """Applies parsed operations to the live registry."""

    def __init__(self, expander=None, view="default", stop_on_error=False):
        if winreg is None:
            raise RegistryError("the registry is only available on Windows")
        self.view = normalize_view(view)
        self.view_flag = VIEW_FLAGS[self.view]
        self.expander = expander or VariableExpander()
        self.stop_on_error = bool(stop_on_error)
        self._roots = {
            "HKCR": winreg.HKEY_CLASSES_ROOT,
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKU": winreg.HKEY_USERS,
            "HKCC": winreg.HKEY_CURRENT_CONFIG,
        }
        self._types = {
            RegType.NONE: winreg.REG_NONE,
            RegType.SZ: winreg.REG_SZ,
            RegType.EXPAND_SZ: winreg.REG_EXPAND_SZ,
            RegType.BINARY: winreg.REG_BINARY,
            RegType.DWORD: winreg.REG_DWORD,
            RegType.QWORD: winreg.REG_QWORD,
            RegType.MULTI_SZ: winreg.REG_MULTI_SZ,
        }

    # ------------------------------------------------------------------
    #  Public entry point
    # ------------------------------------------------------------------

    def apply(self, operations):
        report = ApplyReport()
        for operation in operations:
            try:
                self._apply_one(operation, report)
            except PermissionError as exc:
                report.error(operation.line,
                             "access denied: {0}: {1}".format(
                                 operation.describe(), exc))
            except FileNotFoundError:
                pass                      # already gone: idempotent success
            except OSError as exc:
                report.error(operation.line, "{0}: {1}".format(
                    operation.describe(), exc))
            except RegistryError as exc:
                report.error(operation.line, "{0}: {1}".format(
                    operation.describe(), exc))
            if report.failed and self.stop_on_error:
                break
        return report

    # ------------------------------------------------------------------
    #  Operations
    # ------------------------------------------------------------------

    def _apply_one(self, operation, report):
        kind = operation.kind
        if kind is OpKind.CREATE_KEY:
            self._create_key(operation.root, operation.path)
            report.keys_created += 1
            return
        if kind is OpKind.SET_VALUE:
            self._set_value(operation)
            report.values_written += 1
            return
        if kind is OpKind.DELETE_VALUE:
            if self._delete_value(operation.root, operation.path,
                                  operation.name):
                report.values_deleted += 1
            return
        if kind is OpKind.DELETE_KEY:
            if self._delete_key(operation.root, operation.path):
                report.keys_deleted += 1
            return
        raise RegistryError("internal error: unknown operation {0}".format(
            kind))

    def _root(self, root):
        handle = self._roots.get(root.name)
        if handle is None:
            raise RegistryError(
                "internal error: unknown registry root {0}".format(root))
        return handle

    def _create_key(self, root, path):
        key = winreg.CreateKeyEx(self._root(root), path, 0,
                                 winreg.KEY_WRITE | self.view_flag)
        winreg.CloseKey(key)

    def _set_value(self, operation):
        if operation.vtype is None:
            raise RegistryError("internal error: value without a type")
        value = self.expander.expand_value(operation.value, operation.vtype)
        data = self._normalize_value(value, operation.vtype)

        key = winreg.CreateKeyEx(self._root(operation.root), operation.path, 0,
                                 winreg.KEY_WRITE | self.view_flag)
        try:
            winreg.SetValueEx(key, operation.name or "", 0,
                              self._types[operation.vtype], data)
        finally:
            winreg.CloseKey(key)

    @staticmethod
    def _normalize_value(value, vtype):
        if vtype is RegType.MULTI_SZ:
            items = [str(item) for item in (value or [])]
            if not items or items[-1] != "":
                items = items + [""]
            return items
        if vtype in (RegType.DWORD, RegType.QWORD):
            number = int(value)
            limit = (0xFFFFFFFF if vtype is RegType.DWORD
                     else 0xFFFFFFFFFFFFFFFF)
            if number < 0 or number > limit:
                raise RegistryError("{0} value {1} is out of range".format(
                    vtype.reg_name, number))
            return number
        if vtype in (RegType.BINARY, RegType.NONE):
            return bytes(value or b"")
        return str(value)

    def _delete_value(self, root, path, name):
        try:
            key = winreg.OpenKey(self._root(root), path, 0,
                                 winreg.KEY_SET_VALUE | self.view_flag)
        except FileNotFoundError:
            return False
        try:
            winreg.DeleteValue(key, name or "")
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
        return True

    def _delete_key(self, root, path):
        if not path:
            raise RegistryError("refusing to delete a registry root")
        return self._delete_tree(self._root(root), path)

    def _delete_tree(self, root_handle, path):
        """Iterative post-order deletion; True when the key existed."""
        existed = False
        stack = [(root_handle, path)]
        while stack:
            handle, subkey = stack.pop()
            try:
                key = winreg.OpenKey(handle, subkey, 0,
                                     winreg.KEY_READ | winreg.KEY_WRITE
                                     | self.view_flag)
            except FileNotFoundError:
                continue
            existed = True
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

            if children:
                stack.append((handle, subkey))
                for child in children:
                    stack.append((handle, subkey + "\\" + child))
                continue
            try:
                winreg.DeleteKey(handle, subkey)
            except FileNotFoundError:
                pass
        return existed

