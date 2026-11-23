# -*- coding: utf-8 -*-
"""
Parser for .reg documents (PROJECT_CONTEXT.md §7.3-§7.8).

The parser is pure: it takes text and returns a ``ParseResult`` with the
operations in file order plus located diagnostics.  It never touches the
registry, so it is the reference implementation for the C port.

Strict mode (default) turns every deviation into an error.  ``lenient=True``
reproduces the historical behaviour of pyreg2exe: the construct is skipped with
a warning instead of failing the build.
"""

import re

from .encoding import decode_bytes, read_file_bytes
from .reg_types import (
    OpKind,
    Operation,
    ParseResult,
    RegRoot,
    RegType,
    compute_mode,
    parse_subtype_text,
)

HEADER_PREFIX = "windows registry editor"
LEGACY_HEADER = "REGEDIT4"

_LINE_RE = re.compile(r"\r\n|\r|\n")
_HEX_TYPED_RE = re.compile(r"^hex\s*\(\s*([0-9A-Fa-f]+)\s*\)\s*:", re.IGNORECASE)
_HEX_PLAIN_RE = re.compile(r"^hex\s*:", re.IGNORECASE)
_DWORD_RE = re.compile(r"^dword\s*:", re.IGNORECASE)
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def split_assignment(text):
    """
    Split ``name=data`` at the first ``=`` that is outside a quoted string.

    Returns ``(name_part, value_part)`` or ``None`` when there is no ``=``.
    """
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "=":
            return text[:index], text[index + 1:]
        index += 1
    return None


def ends_inside_string(text):
    """True when the final character of ``text`` sits inside a quoted string."""
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        index += 1
    return in_string


def value_part_of(text):
    """Return the stripped data part of an assignment, or None."""
    parts = split_assignment(text)
    if parts is None:
        return None
    return parts[1].strip()


def is_hex_payload(text):
    """True when the data part of ``text`` starts a hex keyword."""
    value = value_part_of(text)
    if value is None:
        return False
    return bool(_HEX_TYPED_RE.match(value) or _HEX_PLAIN_RE.match(value))


class RegFileParser:
    """Converts .reg text into operations plus diagnostics."""

    def __init__(self, lenient=False):
        self.lenient = bool(lenient)
        self.result = ParseResult()
        self._seen_section = False

    # ------------------------------------------------------------------
    #  Driver
    # ------------------------------------------------------------------

    def parse(self, text):
        lines = _LINE_RE.split(text or "")
        current = None
        position = 0
        total = len(lines)

        while position < total:
            lineno = position + 1
            raw = lines[position]
            position += 1
            stripped = raw.strip()

            if not stripped:
                continue
            if stripped.startswith(";"):
                continue
            if self._is_header(stripped):
                if self._seen_section:
                    self.result.warning(
                        lineno,
                        "registry header after the first section is ignored")
                continue
            if stripped.startswith("["):
                current = self._parse_section(stripped, lineno)
                continue

            text_line, position = self._assemble(stripped, lines, position,
                                                 lineno)
            if current is None:
                self.result.error(lineno, "value outside of a section")
                continue
            self._parse_value_line(text_line, current, lineno)

        self.result.mode = int(compute_mode(self.result.operations))
        return self.result

    @staticmethod
    def _is_header(text):
        lowered = text.lower()
        return lowered.startswith(HEADER_PREFIX) or \
            text.upper() == LEGACY_HEADER

    # ------------------------------------------------------------------
    #  Continuations (§7.3.5-§7.3.8)
    # ------------------------------------------------------------------

    def _assemble(self, text, lines, position, lineno):
        """Join continuation lines; returns (text, next_position)."""
        while text.endswith("\\"):
            if ends_inside_string(text):
                break
            if not is_hex_payload(text):
                message = ("unexpected line continuation (only hex data may "
                           "be continued)")
                if self.lenient:
                    self.result.warning(lineno, message)
                else:
                    self.result.error(lineno, message)
                text = text[:-1].rstrip()
                break
            if position >= len(lines) or not lines[position].strip():
                self.result.error(
                    lineno, "unexpected end of file after a line continuation")
                text = text[:-1].rstrip()
                break
            text = text[:-1] + lines[position].strip()
            position += 1
        return text, position

    # ------------------------------------------------------------------
    #  Sections (§7.4)
    # ------------------------------------------------------------------

    def _parse_section(self, text, lineno):
        if not text.endswith("]"):
            self.result.error(lineno, "unterminated section header")
            return None

        inner = text[1:-1].strip()
        deleting = inner.startswith("-")
        if deleting:
            inner = inner[1:].strip()

        try:
            root, path = RegRoot.from_section(inner)
        except ValueError as exc:
            self.result.error(lineno, str(exc))
            return None

        self._seen_section = True
        if deleting:
            self.result.operations.append(
                Operation(OpKind.DELETE_KEY, root, path, line=lineno))
            return None

        self.result.operations.append(
            Operation(OpKind.CREATE_KEY, root, path, line=lineno))
        return root, path

    def _parse_value_line(self, text, current, lineno):
        root, path = current
        parts = split_assignment(text)
        if parts is None:
            self.result.error(
                lineno, 'expected a value assignment, e.g. "name"="data"')
            return

        name_part, value_part = parts[0].strip(), parts[1].strip()

        if name_part == "@":
            name = ""
        elif len(name_part) >= 2 and name_part.startswith('"') and \
                name_part.endswith('"'):
            name = self._unescape(name_part[1:-1], lineno, "value name")
            if name is None:
                return
        else:
            self.result.error(
                lineno, 'value name must be quoted ("name"=) or @=')
            return

        if "\\" in name:
            self.result.error(
                lineno, "value name must not contain a backslash")
            return

        if not value_part:
            self.result.error(lineno, "missing value data")
            return

        if value_part == "-":
            self.result.operations.append(
                Operation(OpKind.DELETE_VALUE, root, path, name=name,
                          line=lineno))
            return

        if value_part.startswith('"'):
            if len(value_part) < 2 or not value_part.endswith('"'):
                self.result.error(lineno, "unterminated quoted string")
                return
            data = self._unescape(value_part[1:-1], lineno, "value data")
            if data is None:
                return
            self._set(root, path, name, data, RegType.SZ, lineno)
            return

        if _DWORD_RE.match(value_part):
            self._parse_dword(root, path, name, value_part, lineno)
            return

        match = _HEX_TYPED_RE.match(value_part)
        if match:
            try:
                vtype = parse_subtype_text(match.group(1))
            except ValueError as exc:
                self.result.error(lineno, str(exc))
                return
            payload = self._hex_bytes(value_part[match.end():], lineno)
            if payload is None:
                return
            self._emit_payload(root, path, name, vtype, payload, lineno)
            return

        if value_part.lower().startswith("hex("):
            self.result.error(
                lineno, "malformed value type, expected hex(N):")
            return

        match = _HEX_PLAIN_RE.match(value_part)
        if match:
            payload = self._hex_bytes(value_part[match.end():], lineno)
            if payload is None:
                return
            self._set(root, path, name, payload, RegType.BINARY, lineno)
            return

        message = ('value data must be quoted or use dword:/hex: '
                   '(wrap the data in double quotes)')
        if self.lenient:
            self.result.warning(lineno, message + "; treated as REG_SZ")
            # Legacy behaviour: unquoted data was kept as-is, only doubled
            # backslashes were collapsed.  No escape diagnostics here, so the
            # downgrade stays a single, actionable warning.
            self._set(root, path, name, value_part.replace("\\\\", "\\"),
                      RegType.SZ, lineno)
            return
        self.result.error(lineno, message)

    def _parse_dword(self, root, path, name, value_text, lineno):
        token = value_text[value_text.index(":") + 1:].strip()
        if not token:
            self.result.error(lineno, "dword value is empty")
            return
        if not all(char in _HEX_DIGITS for char in token):
            self.result.error(
                lineno, "dword value {0!r} is not hexadecimal".format(token))
            return
        number = int(token, 16)
        if len(token) > 8 or number > 0xFFFFFFFF:
            self.result.error(
                lineno, "dword value {0} exceeds 0xFFFFFFFF".format(token))
            return
        self._set(root, path, name, number, RegType.DWORD, lineno)

    def _hex_bytes(self, payload_text, lineno):
        """Parse a hex payload (§7.6); returns bytes or None after an error."""
        text = payload_text.strip()
        if not text:
            return b""
        parts = text.split(",")
        last = len(parts) - 1
        out = bytearray()
        for index, part in enumerate(parts):
            token = part.strip()
            if not token:
                if index == last:
                    self.result.error(lineno, "trailing comma in hex data")
                else:
                    self.result.error(lineno, "empty byte in hex data")
                return None
            if not all(char in _HEX_DIGITS for char in token):
                self.result.error(
                    lineno, "invalid hex byte {0!r} (expected two "
                            "hexadecimal digits)".format(token))
                return None
            if len(token) != 2:
                self.result.error(
                    lineno, "hex byte {0!r} must have exactly two "
                            "digits".format(token))
                return None
            out.append(int(token, 16))
        return bytes(out)

    def _emit_payload(self, root, path, name, vtype, payload, lineno):
        expected = vtype.exact_byte_length
        if expected is not None and len(payload) != expected:
            self.result.error(
                lineno, "{0} payload must be exactly {1} bytes, got {2}"
                .format(vtype.reg_name, expected, len(payload)))
            return
        if vtype.requires_even_length and len(payload) % 2:
            self.result.error(
                lineno, "{0} payload must contain whole UTF-16LE code units "
                        "(even byte count), got {1} bytes"
                .format(vtype.reg_name, len(payload)))
            return

        if vtype in (RegType.DWORD, RegType.QWORD):
            self._set(root, path, name, int.from_bytes(payload, "little"),
                      vtype, lineno)
            return

        if vtype in (RegType.SZ, RegType.EXPAND_SZ):
            text = self._utf16_text(payload, vtype, lineno)
            if text is None:
                return
            self._set(root, path, name, text.rstrip("\x00"), vtype, lineno)
            return

        if vtype is RegType.MULTI_SZ:
            text = self._utf16_text(payload, vtype, lineno)
            if text is None:
                return
            self._set(root, path, name, split_multi_sz(text), vtype, lineno)
            return

        self._set(root, path, name, bytes(payload), vtype, lineno)

    def _utf16_text(self, payload, vtype, lineno):
        try:
            return payload.decode("utf-16-le", errors="strict")
        except UnicodeDecodeError as exc:
            self.result.error(
                lineno, "{0} payload is not valid UTF-16LE: {1}"
                .format(vtype.reg_name, exc.reason))
            return None

    def _unescape(self, text, lineno, what):
        """Decode ``\\\\`` and ``\\"`` (§7.7); returns None after an error."""
        out = []
        index = 0
        length = len(text)
        while index < length:
            char = text[index]
            if char != "\\":
                out.append(char)
                index += 1
                continue
            if index + 1 >= length:
                if self.lenient:
                    self.result.warning(
                        lineno, "trailing backslash in {0} kept".format(what))
                    out.append(char)
                    index += 1
                    continue
                self.result.error(
                    lineno, "invalid escape sequence at the end of {0}"
                    .format(what))
                return None
            following = text[index + 1]
            if following in ("\\", '"'):
                out.append(following)
                index += 2
                continue
            if self.lenient:
                self.result.warning(
                    lineno, "invalid escape sequence \\{0} in {1} kept "
                            "verbatim".format(following, what))
                out.append(char)
                index += 1
                continue
            self.result.error(
                lineno, "invalid escape sequence \\{0} in {1} (only "
                        "\\\\ and \\\" are defined)".format(following, what))
            return None
        return "".join(out)

    def _set(self, root, path, name, value, vtype, lineno):
        self.result.operations.append(
            Operation(OpKind.SET_VALUE, root, path, name=name, value=value,
                      vtype=vtype, line=lineno))


def split_multi_sz(text):
    """Split decoded REG_MULTI_SZ text into items (§7.5)."""
    if text.endswith("\x00\x00"):
        text = text[:-2]
    elif text.endswith("\x00"):
        text = text[:-1]
    if not text:
        return []
    return text.split("\x00")


# ----------------------------------------------------------------------
#  Public helpers
# ----------------------------------------------------------------------

def parse_reg_text(text, lenient=False):
    """Parse .reg text and return a ``ParseResult``."""
    return RegFileParser(lenient=lenient).parse(text)


def parse_reg_bytes(data, encoding=None, lenient=False):
    """Decode and parse .reg bytes."""
    return parse_reg_text(decode_bytes(data, encoding=encoding),
                          lenient=lenient)


def parse_reg_file(path, encoding=None, lenient=False):
    """Read, decode and parse a .reg file."""
    return parse_reg_bytes(read_file_bytes(path), encoding=encoding,
                           lenient=lenient)


def parse_value_line(line, root, path, lenient=True, line_number=0):
    """
    Legacy helper: parse a single value line.

    Returns ``(name, value, type_name)`` or ``None``; deletion lines yield
    ``(name, None, "DELETE")``.  Used only by the regfile.py shim.
    """
    parser = RegFileParser(lenient=lenient)
    parser._parse_value_line(line.strip(), (root, path), line_number)
    if parser.result.has_errors or not parser.result.operations:
        return None
    operation = parser.result.operations[0]
    if operation.kind is OpKind.DELETE_VALUE:
        return operation.name, None, "DELETE"
    return operation.name, operation.value, operation.vtype.reg_name


def parse_hex_payload(text, lenient=True):
    """Legacy helper: parse ``01,02`` into bytes, or None on error."""
    parser = RegFileParser(lenient=lenient)
    payload = parser._hex_bytes(text, 0)
    if payload is None or parser.result.has_errors:
        return None
    return payload




