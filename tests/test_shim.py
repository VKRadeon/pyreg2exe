# -*- coding: utf-8 -*-
"""
The deprecated tuple API of regfile.py (B17, B23, §6.8).
"""

import os
import unittest
import warnings

import support

import regfile


class DeprecationTests(unittest.TestCase):

    def test_flag_is_set(self):
        self.assertTrue(regfile.__deprecated__)

    def test_import_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            import importlib
            importlib.reload(regfile)


class ReadingTests(unittest.TestCase):

    def test_read_content_from_bytes(self):
        data = support.reg_text("[HKCU\\X]").encode("utf-16-le")
        self.assertIn("[HKCU\\X]", regfile._read_reg_content_from_bytes(data))

    def test_read_reg_file(self):
        with support.temp_dir() as directory:
            path = support.write_reg_file(
                os.path.join(directory, "x.reg"),
                support.reg_text("[HKCU\\X]", '"a"="b"'))
            self.assertIn('"a"="b"', regfile._read_reg_file(path))


class ParsingTests(unittest.TestCase):

    def test_legacy_tuples(self):
        operations = regfile._parse_reg_file(support.reg_text(
            "[HKCU\\Software\\X]",
            '"Name"="value"',
            '"Number"=dword:00000002',
            '"Old"=-',
            "[-HKCU\\Software\\Gone]",
        ))
        self.assertEqual(operations, [
            ("create_key", "HKEY_CURRENT_USER", "Software\\X"),
            ("set_value", "HKEY_CURRENT_USER", "Software\\X", "Name",
             "value", "REG_SZ"),
            ("set_value", "HKEY_CURRENT_USER", "Software\\X", "Number",
             2, "REG_DWORD"),
            ("delete_value", "HKEY_CURRENT_USER", "Software\\X", "Old"),
            ("delete_key", "HKEY_CURRENT_USER", "Software\\Gone"),
        ])

    def test_parse_value_line(self):
        self.assertEqual(regfile._parse_value_line('"a"="b"'),
                         ("a", "b", "REG_SZ"))
        self.assertEqual(regfile._parse_value_line('"a"=hex:01,02'),
                         ("a", b"\x01\x02", "REG_BINARY"))
        self.assertEqual(regfile._parse_value_line('"a"=-'),
                         ("a", None, "DELETE"))
        self.assertEqual(regfile._parse_value_line('@="x"'),
                         ("", "x", "REG_SZ"))
        self.assertIsNone(regfile._parse_value_line("garbage"))

    def test_parse_hex_bytes(self):
        self.assertEqual(regfile._parse_hex_bytes("01,0a,  02"),
                         b"\x01\x0a\x02")
        self.assertEqual(regfile._parse_hex_bytes(""), b"")
        self.assertIsNone(regfile._parse_hex_bytes("0x01"))
        self.assertIsNone(regfile._parse_hex_bytes("01,"))

    def test_utf16_helpers(self):
        payload = "abc\x00".encode("utf-16-le")
        self.assertEqual(regfile._decode_utf16_le(payload), "abc")
        self.assertIsNone(regfile._decode_utf16_le(b"\x01"))

        multi = "a\x00b\x00\x00".encode("utf-16-le")
        self.assertEqual(regfile._decode_utf16_le_multi(multi), ["a", "b"])
        self.assertIsNone(regfile._decode_utf16_le_multi(b"\x01"))

    def test_split_reg_path(self):
        self.assertEqual(regfile._split_reg_path("HKCU\\Software\\X"),
                         ("HKEY_CURRENT_USER", "Software\\X"))
        self.assertEqual(regfile._split_reg_path("HKLM\\A"),
                         ("HKEY_LOCAL_MACHINE", "A"))
        with self.assertRaises(ValueError):
            regfile._split_reg_path("HKDD\\X")
        with self.assertRaises(ValueError):
            regfile._split_reg_path("HKLM")

    def test_split_operations_by_root(self):
        operations = [
            ("create_key", "HKCU", "A"),
            ("create_key", "HKEY_LOCAL_MACHINE", "B"),
            ("create_key", "HKCR", "C"),
            ("broken",),
        ]
        hkcu, hklm, other = regfile.split_operations_by_root(operations)
        self.assertEqual(hkcu, [operations[0]])
        self.assertEqual(hklm, [operations[1]])
        self.assertEqual(other, [operations[2], operations[3]])

    def test_expand_variables(self):
        variables = {"%CD%": r"C:\patch"}
        self.assertEqual(regfile._expand_variables("%CD%\\x", variables),
                         r"C:\patch\x")
        self.assertEqual(
            regfile._expand_variables("<reg2exepath>\\x", variables),
            r"C:\patch\x")


class TypeMapTests(unittest.TestCase):

    def test_type_map(self):
        support.require_windows(self)
        mapping = regfile._get_type_map()
        self.assertEqual(mapping["REG_SZ"], 1)
        self.assertEqual(mapping["REG_QWORD"], 11)

    def test_normalize_root(self):
        support.require_windows(self)
        self.assertIsNotNone(regfile._normalize_root("HKCU"))
        self.assertIsNotNone(regfile._normalize_root("hkey_local_machine"))
        self.assertIsNone(regfile._normalize_root("HKDD"))


class ApplyTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()

    def test_apply_legacy_operations(self):
        errors = regfile.apply_operations([
            ("create_key", "HKEY_CURRENT_USER", self.path),
            ("set_value", "HKEY_CURRENT_USER", self.path, "Name", "value",
             "REG_SZ"),
            ("set_value", "HKEY_CURRENT_USER", self.path, "Path",
             "%CD%\\data", "REG_SZ"),
            ("set_value", "HKEY_CURRENT_USER", self.path, "Number", 7,
             "REG_DWORD"),
        ], variables={"%CD%": r"C:\patch"})
        self.assertEqual(errors, [])
        self.assertEqual(support.read_value(self.path, "Name")[0], "value")
        self.assertEqual(support.read_value(self.path, "Path")[0],
                         r"C:\patch\data")
        self.assertEqual(support.read_value(self.path, "Number"),
                         (7, 4))

    def test_apply_reports_errors(self):
        errors = regfile.apply_operations([
            ("set_value", "HKEY_CURRENT_USER", self.path, "x", "1",
             "REG_UNKNOWN"),
        ])
        self.assertEqual(len(errors), 1)
        self.assertIn("Unknown value type", errors[0])

    def test_delete_operations(self):
        regfile.apply_operations([
            ("set_value", "HKEY_CURRENT_USER", self.path, "x", "1",
             "REG_SZ"),
            ("delete_value", "HKEY_CURRENT_USER", self.path, "x"),
            ("delete_key", "HKEY_CURRENT_USER", self.path),
        ])
        self.assertFalse(support.key_exists(self.path))

    def test_winreg_helpers(self):
        regfile._winreg_create_key("HKEY_CURRENT_USER", self.path)
        regfile._winreg_set_value("HKEY_CURRENT_USER", self.path, "x", "1",
                                  "REG_SZ")
        self.assertEqual(support.read_value(self.path, "x")[0], "1")
        regfile._winreg_delete_value("HKEY_CURRENT_USER", self.path, "x")
        self.assertEqual(support.value_names(self.path), [])
        regfile._winreg_delete_key("HKEY_CURRENT_USER", self.path,
                                   recursive=False)
        self.assertFalse(support.key_exists(self.path))


if __name__ == "__main__":
    unittest.main()

