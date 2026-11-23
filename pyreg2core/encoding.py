# -*- coding: utf-8 -*-
"""
Byte-level decoding of .reg documents (PROJECT_CONTEXT.md §7.2).

Detection order, first match wins:

    FF FE ...            UTF-16LE, BOM removed
    FE FF ...            UTF-16BE, BOM removed
    EF BB BF ...         UTF-8, BOM removed
    (empty)              empty text
    odd length, no BOM   error: unable to determine encoding
    no BOM, data[1::2]   all zero -> UTF-16LE
    otherwise            UTF-8, strict; failure is an error

Lossy decoding is forbidden: no ``errors="replace"`` anywhere.  Windows-1251
is available only when the caller asks for it explicitly.
"""

from .errors import InputError

AUTO = "auto"
SUPPORTED_ENCODINGS = (AUTO, "utf-8", "utf-16-le", "utf-16-be", "cp1251")

_ALIASES = {
    "": AUTO,
    "auto": AUTO,
    "detect": AUTO,
    "utf8": "utf-8",
    "utf-8-sig": "utf-8",
    "utf-8-bom": "utf-8",
    "utf16": "utf-16-le",
    "utf-16": "utf-16-le",
    "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
    "ucs-2": "utf-16-le",
    "windows-1251": "cp1251",
    "win-1251": "cp1251",
    "1251": "cp1251",
    "ansi": "cp1251",
}

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

_CODEC_NAMES = {
    "utf-8": "UTF-8",
    "utf-16-le": "UTF-16LE",
    "utf-16-be": "UTF-16BE",
    "cp1251": "Windows-1251",
}


def normalize_encoding(name):
    """Map a user-supplied encoding name onto a supported one."""
    if name is None:
        return AUTO
    key = str(name).strip().lower()
    if key in _ALIASES:
        return _ALIASES[key]
    if key in SUPPORTED_ENCODINGS:
        return key
    raise InputError(
        "unsupported encoding {0!r} (supported: {1})".format(
            name, ", ".join(SUPPORTED_ENCODINGS)))


def _decode(data, codec):
    """Strict decode with a located error message."""
    try:
        return data.decode(codec, errors="strict")
    except UnicodeDecodeError as exc:
        raise InputError(
            "{0} decoding failed at byte {1} of {2}: {3}".format(
                _CODEC_NAMES.get(codec, codec), exc.start, len(data),
                exc.reason))
    except LookupError:
        raise InputError("unknown codec {0!r}".format(codec))


def guess_encoding(data):
    """Return the codec implied by the first bytes, or None when unknown."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return codec
    if not data:
        return "utf-8"
    if len(data) >= 4 and not any(data[1::2]):
        return "utf-16-le"
    return None


def decode_bytes(data, encoding=None):
    """
    Decode .reg bytes into text.

    ``encoding`` may be None / "auto" (detect), or one of
    ``utf-8``, ``utf-16-le``, ``utf-16-be``, ``cp1251``.
    """
    if data is None:
        return ""
    data = bytes(data)
    encoding = normalize_encoding(encoding)

    if encoding is not AUTO:
        for bom, codec in _BOMS:
            if codec == encoding and data.startswith(bom):
                data = data[len(bom):]
                break
        return _decode(data, encoding)

    if not data:
        return ""

    for bom, codec in _BOMS:
        if data.startswith(bom):
            return _decode(data[len(bom):], codec)

    if len(data) % 2:
        raise InputError(
            "unable to determine encoding (odd byte length {0}); "
            "save the file as UTF-16LE with BOM or as UTF-8".format(
                len(data)))

    if not any(data[1::2]):
        return _decode(data, "utf-16-le")

    return _decode(data, "utf-8")


def read_file_bytes(path):
    """Read a file completely; any OS error becomes an ``InputError``."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise InputError("cannot read {0}: {1}".format(path, exc))


def decode_file(path, encoding=None):
    """Read and decode a .reg file in one step."""
    return decode_bytes(read_file_bytes(path), encoding=encoding)
