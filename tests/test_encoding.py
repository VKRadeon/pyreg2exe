# -*- coding: utf-8 -*-
"""
Byte-level decoding: BOM table, ASCII heuristic, explicit encodings (B9, §7.2).
"""

import codecs
import os
import unittest

import support

from pyreg2core import encoding
from pyreg2core.errors import InputError


class DecodingTests(unittest.TestCase):

    def test_utf16le_with_bom(self):
        data = codecs.BOM_UTF16_LE + "HKEY_CURRENT_USER\\Тест".encode(
            "utf-16-le")
        self.assertEqual(encoding.decode_bytes(data),
                         "HKEY_CURRENT_USER\\Тест")

    def test_utf16be_with_bom(self):
        data = codecs.BOM_UTF16_BE + "REGEDIT4".encode("utf-16-be")
        self.assertEqual(encoding.decode_bytes(data), "REGEDIT4")

    def test_utf8_with_bom(self):
        data = codecs.BOM_UTF8 + "café".encode("utf-8")
        self.assertEqual(encoding.decode_bytes(data), "café")

    def test_utf16le_without_bom_ascii(self):
        data = "REGEDIT4\r\n".encode("utf-16-le")
        self.assertEqual(encoding.decode_bytes(data), "REGEDIT4\r\n")

    def test_bom_is_not_part_of_the_text(self):
        data = codecs.BOM_UTF16_LE + "x".encode("utf-16-le")
        self.assertEqual(encoding.decode_bytes(data), "x")

    def test_empty_input(self):
        self.assertEqual(encoding.decode_bytes(b""), "")

    def test_odd_length_without_bom_is_an_error(self):
        with self.assertRaises(InputError) as caught:
            encoding.decode_bytes(b"REGEDIT")
        self.assertIn("odd byte length", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 3)

    def test_invalid_utf8_is_an_error_not_replacement(self):
        data = "Привет".encode("cp1251")
        with self.assertRaises(InputError) as caught:
            encoding.decode_bytes(data)
        self.assertIn("UTF-8", str(caught.exception))
        self.assertNotIn("\ufffd", str(caught.exception))

    def test_cp1251_only_when_requested(self):
        data = "Привет".encode("cp1251")
        self.assertEqual(encoding.decode_bytes(data, encoding="cp1251"),
                         "Привет")

    def test_explicit_utf8_rejects_cp1251_bytes(self):
        with self.assertRaises(InputError):
            encoding.decode_bytes("Привет".encode("cp1251"),
                                  encoding="utf-8")

    def test_explicit_encoding_strips_matching_bom(self):
        data = codecs.BOM_UTF8 + b"abc"
        self.assertEqual(encoding.decode_bytes(data, encoding="utf-8"), "abc")

    def test_unknown_encoding_is_rejected(self):
        with self.assertRaises(InputError) as caught:
            encoding.decode_bytes(b"x", encoding="koi8-r")
        self.assertIn("unsupported encoding", str(caught.exception))

    def test_aliases(self):
        for given, expected in (("utf8", "utf-8"), ("UTF-16", "utf-16-le"),
                                ("windows-1251", "cp1251"), (None, "auto"),
                                ("Auto", "auto")):
            self.assertEqual(encoding.normalize_encoding(given), expected)

    def test_decode_file_missing(self):
        with self.assertRaises(InputError) as caught:
            encoding.decode_file("does-not-exist.reg")
        self.assertIn("cannot read", str(caught.exception))

    def test_decode_file_roundtrip(self):
        with support.temp_dir() as directory:
            path = support.write_reg_file(
                os.path.join(directory, "a.reg"),
                support.reg_text('[HKEY_CURRENT_USER\\X]'))
            self.assertIn("HKEY_CURRENT_USER", encoding.decode_file(path))


if __name__ == "__main__":
    unittest.main()
