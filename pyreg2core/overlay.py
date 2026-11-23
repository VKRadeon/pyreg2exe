# -*- coding: utf-8 -*-
"""
The overlay codec: the frozen patch format (PROJECT_CONTEXT.md §4.4).

    [PE image of regapply.exe]
    [.reg data as-is (bytes)]
    [length of .reg: 8 bytes, little-endian]
    [mode: 1 byte]
    [marker b"PYREGEND": 8 bytes]

The same tail (data + length + mode + marker) is also used as a standalone
``.regpatch`` file, which is how the loader hands the embedded data to its
elevated child without depending on where the running image lives.

This module uses neither ``winreg`` nor ``ctypes``; it is fully testable and
is mirrored by the C module of Stage 3.
"""

import dataclasses
import os

from .errors import InputError, OverlayError
from .reg_types import Mode

MARKER = b"PYREGEND"
SIZE_LEN = 8
MODE_LEN = 1
TAIL_LEN = SIZE_LEN + MODE_LEN + len(MARKER)     # 17 bytes

__all__ = [
    "MARKER", "SIZE_LEN", "MODE_LEN", "TAIL_LEN", "Mode", "Overlay",
    "encode_blob", "parse_tail", "read_file", "has_overlay",
    "has_overlay_data", "validate_pe_image",
]


@dataclasses.dataclass(frozen=True, slots=True)
class Overlay:
    """Registry payload recovered from a patch or a .regpatch file."""

    data: bytes
    mode: Mode
    offset: int = 0

    @property
    def size(self):
        return len(self.data)

    @property
    def tail_size(self):
        return self.size + TAIL_LEN

    def to_bytes(self):
        """Serialise as a standalone tail (used for the elevated child)."""
        return encode_blob(self.data, self.mode)

    def write_to(self, path):
        """Write the tail to ``path`` and return the path."""
        with open(path, "wb") as handle:
            handle.write(self.to_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        return path


def encode_blob(reg_data, mode):
    """Build ``data + length + mode + marker``."""
    if not isinstance(reg_data, (bytes, bytearray)):
        raise TypeError("reg_data must be bytes")
    payload = bytes(reg_data)
    if not payload:
        raise OverlayError("refusing to embed an empty .reg payload")
    try:
        mode_value = Mode(int(mode))
    except ValueError:
        raise OverlayError("invalid overlay mode {0!r}".format(mode))
    return (payload
            + len(payload).to_bytes(SIZE_LEN, "little")
            + bytes([int(mode_value)])
            + MARKER)


def parse_tail(blob):
    """
    Recover the overlay from the end of ``blob``.

    Returns ``None`` when ``blob`` carries no marker (a plain template).
    Raises ``OverlayError`` when the marker is present but the record is
    inconsistent — that is a corrupt patch, not an unpatched executable.
    """
    if blob is None:
        return None
    blob = bytes(blob)
    if len(blob) < TAIL_LEN:
        return None
    if blob[-len(MARKER):] != MARKER:
        return None

    mode_index = len(blob) - len(MARKER) - MODE_LEN
    mode_byte = blob[mode_index]
    try:
        mode = Mode(mode_byte)
    except ValueError:
        raise OverlayError(
            "unsupported overlay mode 0x{0:02x} (expected 0x01, 0x02 or "
            "0x03)".format(mode_byte))

    size_start = mode_index - SIZE_LEN
    declared = int.from_bytes(blob[size_start:mode_index], "little")
    available = len(blob) - TAIL_LEN
    if declared <= 0:
        raise OverlayError("overlay declares an empty payload")
    if declared > available:
        raise OverlayError(
            "overlay declares {0} bytes of data but only {1} are present "
            "(truncated patch)".format(declared, available))

    start = available - declared
    return Overlay(data=blob[start:available], mode=mode, offset=start)


def has_overlay_data(blob):
    """True when the byte string ends with the overlay marker."""
    if not blob:
        return False
    return bytes(blob)[-len(MARKER):] == MARKER


def read_file(path):
    """Parse a standalone tail / patched executable.  None if unreadable."""
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return None
    return parse_tail(blob)


def has_overlay(path):
    """Cheap check: read only the last ``TAIL_LEN`` bytes of a file."""
    try:
        size = os.path.getsize(path)
        if size < TAIL_LEN:
            return False
        with open(path, "rb") as handle:
            handle.seek(-TAIL_LEN, os.SEEK_END)
            tail = handle.read()
        return has_overlay_data(tail)
    except OSError:
        return False


def validate_pe_image(blob, path="<template>"):
    """Check the DOS and PE signatures of a template."""
    if len(blob) < 0x40:
        raise InputError("{0} is too small to be a Windows "
                         "executable".format(path))
    if blob[:2] != b"MZ":
        raise InputError("{0} is not a Windows executable (missing MZ "
                         "signature)".format(path))
    pe_offset = int.from_bytes(blob[0x3C:0x40], "little")
    if pe_offset + 4 > len(blob) or blob[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise InputError("{0} is not a valid PE image (missing PE "
                         "signature)".format(path))
