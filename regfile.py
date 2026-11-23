# -*- coding: utf-8 -*-
"""
regfile (pyreg2exe edition) — DEPRECATED compatibility shim.

Copyright (C) 2026 Deepseek + VKRadeon

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

Inspired by Reg2exe (Jan Vorel, 2001-2008, Visual Basic 6, GPLv2).

Since 1.1.0 the implementation lives in ``pyreg2core``; this module only
keeps the historical tuple-based API working for scripts and packaging that
import it directly (PROJECT_CONTEXT.md §6.8).  New code should use
``pyreg2core.parser``, ``pyreg2core.registry`` and ``pyreg2core.expand``.

Deprecation is signalled by ``__deprecated__``, this note and the README
changelog — deliberately not by a ``DeprecationWarning``, which would break
callers running with ``-W error``.
"""

from pyreg2core import encoding as _encoding
from pyreg2core import parser as _parser
from pyreg2core.expand import MARKERS as _REG2EXE_MARKERS
from pyreg2core.expand import VariableExpander
from pyreg2core.reg_types import OpKind, Operation, RegRoot, RegType
from pyreg2core.registry import RegistryApplier

__deprecated__ = True

_TYPE_BY_NAME = {reg_type.reg_name: reg_type for reg_type in RegType}

# ═══════════════════════════════════════════════════════════════
#  READING .REG FILES
# ═══════════════════════════════════════════════════════════════


def _read_reg_file(path):
    """Read a .reg file and return its decoded text."""
    return _encoding.decode_file(path)


def _read_reg_content_from_bytes(data):
    """Decode .reg bytes into a string (BOM detection, strict)."""
    return _encoding.decode_bytes(data)


# ═══════════════════════════════════════════════════════════════
#  PARSING .REG FILES  (legacy tuples)
# ═══════════════════════════════════════════════════════════════


def _parse_reg_file(content):
    """
    Parse .reg content and return a list of legacy operation tuples:

        ("create_key", root, path)
        ("set_value", root, path, name, value, value_type)
        ("delete_value", root, path, name)
        ("delete_key", root, path)
    """
    result = _parser.parse_reg_text(content, lenient=True)
    return [_to_legacy(op) for op in result.operations]


def _to_legacy(operation):
    root = operation.root.long_name
    if operation.kind is OpKind.CREATE_KEY:
        return ("create_key", root, operation.path)
    if operation.kind is OpKind.DELETE_KEY:
        return ("delete_key", root, operation.path)
    if operation.kind is OpKind.DELETE_VALUE:
        return ("delete_value", root, operation.path, operation.name)
    return ("set_value", root, operation.path, operation.name,
            operation.value, operation.vtype.reg_name)


def _parse_value_line(line):
    """Parse one value line: (name, value, type_name) or None."""
    return _parser.parse_value_line(line, RegRoot.HKCU, "", lenient=True,
                                    line_number=0)


def _parse_hex_bytes(text):
    """Parse '01,02,03' into bytes. Return None on error."""
    return _parser.parse_hex_payload(text, lenient=True)


def _decode_utf16_le(raw_bytes):
    """Decode bytes as UTF-16 LE and strip one trailing null."""
    if len(raw_bytes) < 2 or len(raw_bytes) % 2 != 0:
        return None
    try:
        text = bytes(raw_bytes).decode("utf-16-le", errors="strict")
    except UnicodeDecodeError:
        return None
    if text.endswith("\x00"):
        text = text[:-1]
    return text


def _decode_utf16_le_multi(raw_bytes):
    """Decode bytes as REG_MULTI_SZ (list of null-separated strings)."""
    if len(raw_bytes) % 2 != 0:
        return None
    try:
        text = bytes(raw_bytes).decode("utf-16-le", errors="strict")
    except UnicodeDecodeError:
        return None
    return _parser.split_multi_sz(text)


def _split_reg_path(section):
    """Split 'HKEY_CURRENT_USER\\Software\\MyApp' into (root, path)."""
    root, path = RegRoot.from_section(section)
    return root.long_name, path


def split_operations_by_root(operations):
    """Split legacy tuples into (hkcu_ops, hklm_ops, other_ops)."""
    hkcu_ops, hklm_ops, other_ops = [], [], []
    for op in operations:
        if len(op) < 2:
            other_ops.append(op)
            continue
        try:
            root = RegRoot.parse(op[1])
        except ValueError:
            other_ops.append(op)
            continue
        if root is RegRoot.HKCU:
            hkcu_ops.append(op)
        elif root is RegRoot.HKLM:
            hklm_ops.append(op)
        else:
            other_ops.append(op)
    return hkcu_ops, hklm_ops, other_ops


# ═══════════════════════════════════════════════════════════════
#  VARIABLE EXPANSION
# ═══════════════════════════════════════════════════════════════


def _make_expander(variables=None):
    """Build an expander from the legacy ``variables`` dictionary."""
    variables = variables or {}
    base_dir = None
    for key, value in variables.items():
        if str(key).lower() in ("%cd%", "<reg2exepath>"):
            base_dir = value
            break
    return VariableExpander(base_dir=base_dir, extra=variables)


def _expand_variables(text, variables=None):
    """Expand %VAR% and <reg2exe...> markers in a string."""
    return _make_expander(variables).expand(text)


def _plain_expander():
    """Expander that never resolves environment variables."""
    return VariableExpander(environ={})


# ═══════════════════════════════════════════════════════════════
#  APPLYING OPERATIONS
# ═══════════════════════════════════════════════════════════════


def _coerce_type(value_type):
    if isinstance(value_type, RegType):
        return value_type
    if isinstance(value_type, str):
        reg_type = _TYPE_BY_NAME.get(value_type)
        if reg_type is None:
            raise ValueError("Unknown value type: {0}".format(value_type))
        return reg_type
    return RegType(int(value_type))


def _from_legacy(op):
    """Convert one legacy tuple into an ``Operation``."""
    if not isinstance(op, (tuple, list)) or not op:
        raise ValueError("Unknown operation: {0!r}".format(op))
    kind = op[0]
    if kind == "create_key":
        return Operation(OpKind.CREATE_KEY, RegRoot.parse(op[1]), op[2])
    if kind == "delete_key":
        return Operation(OpKind.DELETE_KEY, RegRoot.parse(op[1]), op[2])
    if kind == "delete_value":
        return Operation(OpKind.DELETE_VALUE, RegRoot.parse(op[1]), op[2],
                         name=op[3])
    if kind == "set_value":
        value = op[4]
        if isinstance(value, bytearray):
            value = bytes(value)
        return Operation(OpKind.SET_VALUE, RegRoot.parse(op[1]), op[2],
                         name=op[3], value=value, vtype=_coerce_type(op[5]))
    raise ValueError("Unknown operation: {0!r}".format(kind))


def apply_operations(operations, variables=None):
    """
    Apply a list of legacy operations to the registry.

    Returns a list of error messages; empty on success.
    """
    errors = []
    parsed = []
    for op in operations:
        try:
            parsed.append(_from_legacy(op))
        except ValueError as exc:
            errors.append(str(exc))

    applier = RegistryApplier(expander=_make_expander(variables))
    report = applier.apply(parsed)
    errors.extend(str(item) for item in report.errors)
    return errors


def _raise_first(report):
    errors = report.errors
    if errors:
        raise OSError(str(errors[0]))


def _winreg_create_key(root_str, path):
    """Create a registry key if it does not exist."""
    applier = RegistryApplier(expander=_plain_expander())
    report = applier.apply([Operation(OpKind.CREATE_KEY,
                                      RegRoot.parse(root_str), path)])
    _raise_first(report)


def _winreg_set_value(root_str, path, name, value, value_type):
    """Write a value to the registry."""
    applier = RegistryApplier(expander=_plain_expander())
    report = applier.apply([Operation(OpKind.SET_VALUE,
                                      RegRoot.parse(root_str), path,
                                      name=name, value=value,
                                      vtype=_coerce_type(value_type))])
    _raise_first(report)


def _winreg_delete_value(root_str, path, name):
    """Delete a value. Silently ignore if missing."""
    applier = RegistryApplier(expander=_plain_expander())
    report = applier.apply([Operation(OpKind.DELETE_VALUE,
                                      RegRoot.parse(root_str), path,
                                      name=name)])
    _raise_first(report)


def _winreg_delete_key(root_str, path, recursive=True):
    """Delete a key; recursive deletion also removes all subkeys."""
    root = RegRoot.parse(root_str)
    if not path:
        return
    if recursive:
        applier = RegistryApplier(expander=_plain_expander())
        report = applier.apply([Operation(OpKind.DELETE_KEY, root, path)])
        _raise_first(report)
        return

    import winreg
    try:
        winreg.DeleteKey(_normalize_root(root_str), path)
    except FileNotFoundError:
        pass


def _normalize_root(root):
    """Convert a string root name to a winreg.HKEY_* constant."""
    import winreg

    mapping = {
        "HKCR": winreg.HKEY_CLASSES_ROOT,
        "HKEY_CLASSES_ROOT": winreg.HKEY_CLASSES_ROOT,
        "HKCU": winreg.HKEY_CURRENT_USER,
        "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
        "HKLM": winreg.HKEY_LOCAL_MACHINE,
        "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        "HKU": winreg.HKEY_USERS,
        "HKEY_USERS": winreg.HKEY_USERS,
        "HKCC": winreg.HKEY_CURRENT_CONFIG,
        "HKEY_CURRENT_CONFIG": winreg.HKEY_CURRENT_CONFIG,
    }
    return mapping.get(str(root).upper())


_TYPE_TO_WINREG = None


def _get_type_map():
    """Return {type name: winreg constant}, importing winreg on first use."""
    global _TYPE_TO_WINREG
    if _TYPE_TO_WINREG is None:
        import winreg
        _TYPE_TO_WINREG = {
            reg_type.reg_name: getattr(winreg, reg_type.reg_name)
            for reg_type in RegType
        }
    return _TYPE_TO_WINREG

