# -*- coding: utf-8 -*-
"""
Value objects shared by the parser, the applier, the builder and the overlay.

This module is deliberately dependency-free (stdlib only): it is the part of
the implementation that the Stage 3 C module mirrors one-to-one
(PROJECT_CONTEXT.md §6.4, §9.7).
"""

import dataclasses
import enum
import typing


class Severity(str, enum.Enum):
    """Diagnostic severity.  A ``str`` subclass, so == "error" works."""

    ERROR = "error"
    WARNING = "warning"


class Mode(enum.IntEnum):
    """Overlay mode byte (frozen format, §4.4)."""

    HKLM = 0x01
    HKCU = 0x02
    MIXED = 0x03

    @property
    def needs_admin(self):
        return self in (Mode.HKLM, Mode.MIXED)

    @property
    def needs_user(self):
        return self in (Mode.HKCU, Mode.MIXED)

    def __str__(self):
        return {
            Mode.HKLM: "HKLM only",
            Mode.HKCU: "HKCU only",
            Mode.MIXED: "mixed (HKLM + HKCU)",
        }[self]


class RegRoot(enum.Enum):
    """Supported registry roots and whether they need administrator rights."""

    HKCR = ("HKEY_CLASSES_ROOT", False)
    HKCU = ("HKEY_CURRENT_USER", False)
    HKLM = ("HKEY_LOCAL_MACHINE", True)
    HKU = ("HKEY_USERS", True)
    HKCC = ("HKEY_CURRENT_CONFIG", True)

    def __init__(self, long_name, requires_admin):
        self.long_name = long_name
        self.requires_admin = requires_admin

    @property
    def short_name(self):
        return self.name

    @classmethod
    def parse(cls, token):
        """Exact, case-insensitive token match.  Raises ``ValueError``."""
        root = _ROOT_BY_TOKEN.get(token.strip().upper())
        if root is None:
            raise ValueError(
                "unknown registry root {0!r} (supported: HKEY_CLASSES_ROOT, "
                "HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, HKEY_USERS, "
                "HKEY_CURRENT_CONFIG and their short forms)".format(token))
        return root

    @classmethod
    def from_section(cls, section):
        """Split ``ROOT\\sub\\key`` into ``(RegRoot, path)``."""
        text = section.strip()
        token, _, rest = text.partition("\\")
        root = cls.parse(token)
        path = rest.strip().lstrip("\\").rstrip("\\").strip()
        if not path:
            raise ValueError("empty key path (a section must name a subkey)")
        return root, path

    def __str__(self):
        return self.long_name


_ROOT_TOKENS = {
    RegRoot.HKCR: ("HKEY_CLASSES_ROOT", "HKCR"),
    RegRoot.HKCU: ("HKEY_CURRENT_USER", "HKCU"),
    RegRoot.HKLM: ("HKEY_LOCAL_MACHINE", "HKLM"),
    RegRoot.HKU: ("HKEY_USERS", "HKU"),
    RegRoot.HKCC: ("HKEY_CURRENT_CONFIG", "HKCC"),
}

_ROOT_BY_TOKEN = {}
for _root, _tokens in _ROOT_TOKENS.items():
    for _token in _tokens:
        _ROOT_BY_TOKEN[_token] = _root
del _root, _tokens, _token


class RegType(enum.Enum):
    """Registry value types.  The value is the numeric type code."""

    NONE = 0
    SZ = 1
    EXPAND_SZ = 2
    BINARY = 3
    DWORD = 4
    MULTI_SZ = 7
    QWORD = 11

    @property
    def subtype(self):
        """The number used in ``hex(N)`` (``hex(b)`` for QWORD)."""
        return self.value

    @property
    def reg_name(self):
        return "REG_" + self.name

    @property
    def is_text(self):
        return self in (RegType.SZ, RegType.EXPAND_SZ, RegType.MULTI_SZ)

    @property
    def exact_byte_length(self):
        """Required payload length in bytes, or None when it varies."""
        if self is RegType.DWORD:
            return 4
        if self is RegType.QWORD:
            return 8
        return None

    @property
    def requires_even_length(self):
        """UTF-16LE payloads must consist of whole code units."""
        return self.is_text

    @classmethod
    def from_subtype(cls, number):
        try:
            return cls(number)
        except ValueError:
            raise ValueError(
                "unsupported value type hex({0})".format(
                    format(number, "x")))


_HEX_SUBTYPE_ALIASES = {"b": RegType.QWORD}


def parse_subtype_text(text):
    """Parse the ``N`` of ``hex(N)``: ``0``..``4``, ``7``, ``b``/``B``."""
    token = text.strip()
    lowered = token.lower()
    if lowered in _HEX_SUBTYPE_ALIASES:
        return _HEX_SUBTYPE_ALIASES[lowered]
    if not token:
        raise ValueError("empty value type in hex()")
    if not all(ch in "0123456789abcdefABCDEF" for ch in token):
        raise ValueError(
            "invalid value type hex({0})".format(token))
    return RegType.from_subtype(int(token, 16))


class OpKind(enum.Enum):
    """What an operation does to the registry."""

    CREATE_KEY = "create_key"
    SET_VALUE = "set_value"
    DELETE_VALUE = "delete_value"
    DELETE_KEY = "delete_key"

    def __str__(self):
        return self.value


@dataclasses.dataclass(frozen=True, slots=True)
class Operation:
    """One registry operation, in file order."""

    kind: OpKind
    root: RegRoot
    path: str
    name: typing.Optional[str] = None
    value: typing.Any = None
    vtype: typing.Optional[RegType] = None
    line: int = 0

    def describe(self):
        """Short human-readable form used by logs and dry runs."""
        if self.kind is OpKind.CREATE_KEY:
            return "create {0}\\{1}".format(self.root, self.path)
        if self.kind is OpKind.DELETE_KEY:
            return "delete {0}\\{1}".format(self.root, self.path)
        if self.kind is OpKind.DELETE_VALUE:
            return "delete value {0}\\{1} :: {2!r}".format(
                self.root, self.path, self.name)
        return "set {0}\\{1} :: {2!r} = {3} ({4})".format(
            self.root, self.path, self.name,
            _short_value(self.value), self.vtype.reg_name if self.vtype else "?")

    @property
    def is_admin(self):
        return self.root.requires_admin


def _short_value(value, limit=40):
    if isinstance(value, (bytes, bytearray)):
        text = "hex:" + ",".join("{0:02x}".format(b) for b in value)
    else:
        text = repr(value)
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    return text


@dataclasses.dataclass(frozen=True, slots=True)
class Diagnostic:
    """A located message: an error blocks the build, a warning does not."""

    severity: Severity
    line: int
    message: str

    @property
    def is_error(self):
        return self.severity is Severity.ERROR

    def __str__(self):
        if self.line:
            return "{0} on line {1}: {2}".format(
                self.severity.value, self.line, self.message)
        return "{0}: {1}".format(self.severity.value, self.message)


@dataclasses.dataclass(slots=True)
class ParseResult:
    """Everything the parser learned about one .reg document."""

    operations: typing.List[Operation] = dataclasses.field(default_factory=list)
    diagnostics: typing.List[Diagnostic] = dataclasses.field(
        default_factory=list)
    mode: int = 0

    @property
    def errors(self):
        return [d for d in self.diagnostics if d.is_error]

    @property
    def warnings(self):
        return [d for d in self.diagnostics if not d.is_error]

    @property
    def has_errors(self):
        return any(d.is_error for d in self.diagnostics)

    def add(self, severity, line, message):
        self.diagnostics.append(Diagnostic(severity, line, message))

    def error(self, line, message):
        self.add(Severity.ERROR, line, message)

    def warning(self, line, message):
        self.add(Severity.WARNING, line, message)


@dataclasses.dataclass(slots=True)
class ApplyReport:
    """Outcome of applying a list of operations."""

    keys_created: int = 0
    keys_deleted: int = 0
    values_written: int = 0
    values_deleted: int = 0
    diagnostics: typing.List[Diagnostic] = dataclasses.field(
        default_factory=list)

    @property
    def failed(self):
        return any(d.is_error for d in self.diagnostics)

    @property
    def errors(self):
        return [d for d in self.diagnostics if d.is_error]

    def error(self, line, message):
        self.diagnostics.append(
            Diagnostic(Severity.ERROR, line, message))

    def warning(self, line, message):
        self.diagnostics.append(
            Diagnostic(Severity.WARNING, line, message))

    def merge(self, other):
        self.keys_created += other.keys_created
        self.keys_deleted += other.keys_deleted
        self.values_written += other.values_written
        self.values_deleted += other.values_deleted
        self.diagnostics.extend(other.diagnostics)
        return self

    def summary(self):
        return ("{0} key(s) created, {1} deleted, {2} value(s) written, "
                "{3} deleted, {4} error(s)".format(
                    self.keys_created, self.keys_deleted,
                    self.values_written, self.values_deleted,
                    len(self.errors)))


def compute_mode(operations):
    """Derive the overlay mode byte from the operations (0 when empty)."""
    has_admin = False
    has_user = False
    for op in operations:
        if op.root.requires_admin:
            has_admin = True
        else:
            has_user = True
    if has_admin and has_user:
        return Mode.MIXED
    if has_admin:
        return Mode.HKLM
    if has_user:
        return Mode.HKCU
    return 0


