# -*- coding: utf-8 -*-
"""
regfile (pyreg2exe edition) — minimal parser and applier for .reg files.

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

This edition is trimmed for pyreg2exe: parsing, splitting by root,
and applying operations. File-level helpers (apply_reg_file, read_value,
write_value, etc.) are intentionally omitted.
"""

import os
import re


# ═══════════════════════════════════════════════════════════════
#  READING .REG FILES
# ═══════════════════════════════════════════════════════════════

def _read_reg_file(path):
    """Read a .reg file and return its decoded text."""
    with open(path, "rb") as f:
        raw = f.read()
    return _read_reg_content_from_bytes(raw)


def _read_reg_content_from_bytes(data):
    """
    Decode .reg bytes into a string. Auto-detect encoding.

    Supported:
        - UTF-16 LE with BOM (standard for regedit exports)
        - UTF-16 BE with BOM
        - UTF-8 with BOM
        - UTF-16 LE without BOM
        - UTF-8 without BOM
        - Windows-1251 as a fallback
    """
    if not data:
        return ""

    # UTF-16 LE with BOM
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")

    # UTF-16 BE with BOM
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")

    # UTF-8 with BOM
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")

    # UTF-16 LE without BOM (every second byte is 0x00 for ASCII)
    if len(data) >= 4 and data[1] == 0 and data[3] == 0:
        try:
            return data.decode("utf-16-le", errors="strict")
        except UnicodeDecodeError:
            pass

    # UTF-8 without BOM
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass

    # Fallback — Windows-1251
    return data.decode("cp1251", errors="replace")


# ═══════════════════════════════════════════════════════════════
#  PARSING .REG FILES
# ═══════════════════════════════════════════════════════════════

def _parse_reg_file(content):
    """
    Parse .reg content and return a list of operations.

    Operation formats:
        ("create_key", root, path)
        ("set_value", root, path, name, value, value_type)
        ("delete_value", root, path, name)
        ("delete_key", root, path)
    """
    operations = []
    current_root = None
    current_path = None

    lines = _join_multiline_values(content.splitlines())

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            continue
        if line.startswith(";"):
            continue
        if line.lower().startswith("windows registry editor"):
            continue
        if line.upper() == "REGEDIT4":
            continue

        # Section line
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()

            # Delete key: [-HKEY_...]
            if section.startswith("-"):
                section = section[1:].strip()
                try:
                    root_str, path_str = _split_reg_path(section)
                except ValueError:
                    current_root = None
                    current_path = None
                    continue
                operations.append(("delete_key", root_str, path_str))
                current_root = None
                current_path = None
                continue

            # Regular section
            try:
                root_str, path_str = _split_reg_path(section)
            except ValueError:
                current_root = None
                current_path = None
                continue

            operations.append(("create_key", root_str, path_str))
            current_root = root_str
            current_path = path_str
            continue

        # Value line — must be inside a section
        if current_root is None or current_path is None:
            continue

        parsed = _parse_value_line(line)
        if parsed is None:
            continue

        name, value, value_type = parsed

        if value_type == "DELETE":
            operations.append(
                ("delete_value", current_root, current_path, name))
        else:
            operations.append(
                ("set_value", current_root, current_path,
                 name, value, value_type))

    return operations


def _join_multiline_values(lines):
    """
    Join lines that end with a backslash into a single line.

    Example:
        '"Data"=hex:01,02,\\'
        '  03,04'
    ->  '"Data"=hex:01,02,03,04'
    """
    result = []
    buffer = ""

    for line in lines:
        stripped = line.rstrip()

        if stripped.endswith("\\"):
            buffer += stripped[:-1]
            continue

        if buffer:
            buffer += stripped
            result.append(buffer)
            buffer = ""
        else:
            result.append(line)

    if buffer:
        result.append(buffer)

    return result


def _split_reg_path(section):
    """Split 'HKEY_CURRENT_USER\\Software\\MyApp' into (root, path)."""
    KNOWN_ROOTS = (
        "HKEY_CLASSES_ROOT",
        "HKEY_CURRENT_USER",
        "HKEY_LOCAL_MACHINE",
        "HKEY_USERS",
        "HKEY_CURRENT_CONFIG",
        "HKEY_DYN_DATA",
        "HKEY_PERFORMANCE_DATA",
        "HKCR", "HKCU", "HKLM", "HKU", "HKCC", "HKDD", "HKPD",
    )

    upper = section.upper()
    for root in KNOWN_ROOTS:
        if upper.startswith(root):
            rest = section[len(root):]
            rest = rest.lstrip("\\")
            return root, rest

    raise ValueError(f"Unknown root: {section}")


def _parse_value_line(line):
    """
    Parse a single value line inside a section.

    Supported:
        "Name"="VKRadeon"              -> ("Name", "VKRadeon", "REG_SZ")
        "Number"=dword:00000001        -> ("Number", 1, "REG_DWORD")
        @="Default Value"              -> ("", "Default Value", "REG_SZ")
        "Data"=hex:01,02,03            -> ("Data", b"\\x01\\x02\\x03", "REG_BINARY")
        "Expand"=hex(2):25,00,...      -> ("Expand", "%VAR%", "REG_EXPAND_SZ")
        "Multi"=hex(7):61,00,00,00     -> ("Multi", ["a"], "REG_MULTI_SZ")
        "Qword"=hex(b):01,00,...       -> ("Qword", 1, "REG_QWORD")
        "Old"=-                        -> ("Old", None, "DELETE")
    """
    if "=" not in line:
        return None

    eq_pos = line.index("=")
    name_part = line[:eq_pos].strip()
    value_part = line[eq_pos + 1:].strip()

    # Name
    if name_part == "@":
        name = ""
    elif name_part.startswith('"') and name_part.endswith('"'):
        name = name_part[1:-1]
    else:
        return None

    # Delete value
    if value_part == "-":
        return name, None, "DELETE"

    # REG_SZ (quoted)
    if value_part.startswith('"') and value_part.endswith('"'):
        value = value_part[1:-1]
        value = value.replace("\\\\", "\\")
        return name, value, "REG_SZ"

    lower = value_part.lower()

    # REG_DWORD
    if lower.startswith("dword:"):
        try:
            num = int(value_part[6:], 16)
            return name, num, "REG_DWORD"
        except ValueError:
            return None

    # REG_QWORD
    if lower.startswith("hex(b):"):
        raw_bytes = _parse_hex_bytes(value_part[7:])
        if raw_bytes is None or len(raw_bytes) != 8:
            return None
        num = int.from_bytes(raw_bytes, "little")
        return name, num, "REG_QWORD"

    # hex(N):
    if lower.startswith("hex("):
        try:
            close = value_part.index(")")
        except ValueError:
            return None
        try:
            subtype = int(value_part[4:close])
        except ValueError:
            return None

        hex_data = value_part[close + 1:]
        if hex_data.startswith(":"):
            hex_data = hex_data[1:]

        raw_bytes = _parse_hex_bytes(hex_data)
        if raw_bytes is None:
            return None

        if subtype == 2:
            text = _decode_utf16_le(raw_bytes)
            if text is None:
                return None
            return name, text, "REG_EXPAND_SZ"

        if subtype == 7:
            items = _decode_utf16_le_multi(raw_bytes)
            if items is None:
                return None
            return name, items, "REG_MULTI_SZ"

        if subtype == 1:
            text = _decode_utf16_le(raw_bytes)
            if text is None:
                return None
            return name, text, "REG_SZ"

        if subtype == 0:
            return name, raw_bytes, "REG_NONE"

        return name, raw_bytes, "REG_BINARY"

    # REG_BINARY
    if lower.startswith("hex:"):
        raw_bytes = _parse_hex_bytes(value_part[4:])
        if raw_bytes is None:
            return None
        return name, raw_bytes, "REG_BINARY"

    # REG_SZ without quotes (e.g. %CD% or plain text)
    if not lower.startswith(("dword:", "hex", "hex(")):
        value = value_part.replace("\\\\", "\\")
        return name, value, "REG_SZ"

    return None


def _parse_hex_bytes(s):
    """Parse '01,02,03' into bytes. Return None on error."""
    s = s.strip()
    if not s:
        return b""

    parts = s.replace(" ", "").split(",")
    out = bytearray()

    for part in parts:
        if not part:
            continue
        try:
            out.append(int(part, 16))
        except ValueError:
            return None

    return bytes(out)


def _decode_utf16_le(raw_bytes):
    """Decode bytes as UTF-16 LE and strip the trailing null."""
    if len(raw_bytes) < 2 or len(raw_bytes) % 2 != 0:
        return None
    try:
        text = raw_bytes.decode("utf-16-le", errors="strict")
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
        text = raw_bytes.decode("utf-16-le", errors="strict")
    except UnicodeDecodeError:
        return None

    if text.endswith("\x00\x00"):
        text = text[:-2]
    elif text.endswith("\x00"):
        text = text[:-1]

    if not text:
        return []

    return text.split("\x00")


# ═══════════════════════════════════════════════════════════════
#  SPLITTING OPERATIONS BY ROOT
# ═══════════════════════════════════════════════════════════════

def split_operations_by_root(operations):
    """
    Split a list of operations into three groups by registry root.

    Returns:
        (hkcu_ops, hklm_ops, other_ops)
    """
    hkcu_ops = []
    hklm_ops = []
    other_ops = []

    for op in operations:
        if len(op) < 2:
            other_ops.append(op)
            continue

        root = op[1].upper()

        if root in ("HKEY_CURRENT_USER", "HKCU"):
            hkcu_ops.append(op)
        elif root in ("HKEY_LOCAL_MACHINE", "HKLM"):
            hklm_ops.append(op)
        else:
            other_ops.append(op)

    return hkcu_ops, hklm_ops, other_ops


# ═══════════════════════════════════════════════════════════════
#  VARIABLE EXPANSION
# ═══════════════════════════════════════════════════════════════

# Reg2exe-compatible markers mapped to Windows environment variables.
# The special marker <reg2exepath> is handled separately (maps to %CD%).
_REG2EXE_MARKERS = {
    "<reg2exepath>": None,
    "<reg2exewinpath>": "WINDIR",
    "<reg2exesyspath>": "SYSTEMROOT",
    "<reg2exetemppath>": "TEMP",
    "<reg2exeprogspath>": "PROGRAMFILES",
    "<reg2exeappdatapath>": "APPDATA",
    "<reg2exelocalappdatapath>": "LOCALAPPDATA",
    "<reg2exeusername>": "USERNAME",
    "<reg2execomputername>": "COMPUTERNAME",
    "<reg2exeprofilespath>": "USERPROFILE",
}


def _expand_variables(s, variables=None):
    """
    Expand %VAR% and <reg2exe...> markers in a string.

    Variables provided in `variables` (dict) take priority over
    environment variables. The special key "%CD%" is used by
    <reg2exepath>.
    """
    if not s:
        return s
    if variables is None:
        variables = {}

    vars_lower = {k.lower(): v for k, v in variables.items()}

    # Standard %VAR% expansion
    def _replace_percent(match):
        name = match.group(1)
        upper = name.upper()
        lower = name.lower()

        key = f"%{lower}%"
        if key in vars_lower:
            return vars_lower[key]
        if upper in os.environ:
            return os.environ[upper]
        if upper == "CD":
            return vars_lower.get("%cd%", match.group(0))
        return match.group(0)

    s = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", _replace_percent, s)

    # Reg2exe marker expansion
    def _replace_marker(match):
        marker = match.group(0).lower()

        if marker == "<reg2exepath>":
            return vars_lower.get("%cd%", match.group(0))

        env_name = _REG2EXE_MARKERS.get(marker)
        if env_name and env_name in os.environ:
            return os.environ[env_name]

        return match.group(0)

    s = re.sub(r"<reg2exe[a-z]+>", _replace_marker, s, flags=re.IGNORECASE)

    return s


# ═══════════════════════════════════════════════════════════════
#  APPLYING OPERATIONS
# ═══════════════════════════════════════════════════════════════

_TYPE_TO_WINREG = None


def _get_type_map():
    """Lazily build the value-type mapping (imports winreg on first use)."""
    global _TYPE_TO_WINREG
    if _TYPE_TO_WINREG is None:
        import winreg
        _TYPE_TO_WINREG = {
            "REG_SZ": winreg.REG_SZ,
            "REG_EXPAND_SZ": winreg.REG_EXPAND_SZ,
            "REG_BINARY": winreg.REG_BINARY,
            "REG_DWORD": winreg.REG_DWORD,
            "REG_QWORD": winreg.REG_QWORD,
            "REG_MULTI_SZ": winreg.REG_MULTI_SZ,
            "REG_NONE": winreg.REG_NONE,
        }
    return _TYPE_TO_WINREG


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
    return mapping.get(root.upper())


def apply_operations(operations, variables=None):
    """
    Apply a list of parsed operations to the registry.

    Returns:
        list[str]: list of error messages. Empty list on success.
    """
    errors = []

    for op in operations:
        op_type = op[0]
        try:
            if op_type == "create_key":
                _, root_str, path = op
                _winreg_create_key(root_str, path)

            elif op_type == "set_value":
                _, root_str, path, name, value, vtype = op

                if isinstance(name, str):
                    name = _expand_variables(name, variables)

                if isinstance(value, str):
                    value = _expand_variables(value, variables)
                elif isinstance(value, list):
                    value = [
                        _expand_variables(item, variables)
                        if isinstance(item, str) else item
                        for item in value
                    ]

                _winreg_set_value(root_str, path, name, value, vtype)

            elif op_type == "delete_value":
                _, root_str, path, name = op
                if isinstance(name, str):
                    name = _expand_variables(name, variables)
                _winreg_delete_value(root_str, path, name)

            elif op_type == "delete_key":
                _, root_str, path = op
                _winreg_delete_key(root_str, path, recursive=True)

        except PermissionError as e:
            errors.append(
                f"Access denied: {op_type} "
                f"{op[1:] if len(op) > 1 else ''}: {e}"
            )
        except Exception as e:
            errors.append(f"Error: {op_type} {op[1:]}: {e}")

    return errors


def _winreg_create_key(root_str, path):
    """Create a registry key if it does not exist."""
    import winreg

    root = _normalize_root(root_str)
    if root is None:
        raise ValueError(f"Unknown root: {root_str}")
    if not path:
        return

    key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE)
    winreg.CloseKey(key)


def _winreg_set_value(root_str, path, name, value, value_type):
    """Write a value to the registry."""
    import winreg

    root = _normalize_root(root_str)
    if root is None:
        raise ValueError(f"Unknown root: {root_str}")

    type_map = _get_type_map()
    wtype = type_map.get(value_type)
    if wtype is None:
        raise ValueError(f"Unknown value type: {value_type}")

    # Normalize value for winreg
    if value_type == "REG_MULTI_SZ":
        if not isinstance(value, list):
            value = [str(value)]
        value = [str(x) for x in value]
        if not value or value[-1] != "":
            value = value + [""]
    elif value_type in ("REG_DWORD", "REG_QWORD"):
        value = int(value)
    elif value_type in ("REG_BINARY", "REG_NONE"):
        if not isinstance(value, (bytes, bytearray)):
            value = bytes(value)
    else:
        value = str(value)

    key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE)
    try:
        winreg.SetValueEx(key, name, 0, wtype, value)
    finally:
        winreg.CloseKey(key)


def _winreg_delete_value(root_str, path, name):
    """Delete a value. Silently ignore if missing."""
    import winreg

    root = _normalize_root(root_str)
    if root is None:
        raise ValueError(f"Unknown root: {root_str}")

    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        return

    try:
        winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    finally:
        winreg.CloseKey(key)


def _winreg_delete_key(root_str, path, recursive=True):
    """Delete a key. If recursive, also delete all subkeys."""
    import winreg

    root = _normalize_root(root_str)
    if root is None:
        raise ValueError(f"Unknown root: {root_str}")
    if not path:
        return

    if recursive:
        _winreg_delete_key_recursive(root, path)
    else:
        try:
            winreg.DeleteKey(root, path)
        except FileNotFoundError:
            pass


def _winreg_delete_key_recursive(root, path):
    """Recursively delete a key and all its subkeys."""
    import winreg

    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WRITE)
    except FileNotFoundError:
        return

    subkeys = []
    i = 0
    while True:
        try:
            subkeys.append(winreg.EnumKey(key, i))
            i += 1
        except OSError:
            break

    winreg.CloseKey(key)

    for subkey in subkeys:
        _winreg_delete_key_recursive(root, path + "\\" + subkey)

    try:
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass