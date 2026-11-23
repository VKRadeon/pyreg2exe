# -*- coding: utf-8 -*-
"""
Sections, roots and routing (B4, B6, B7, B10, §7.4, §7.8).
"""

import unittest

import support

from pyreg2core.parser import parse_reg_bytes, parse_reg_text
from pyreg2core.reg_types import Mode, OpKind, RegRoot


class RootTests(unittest.TestCase):

    def test_long_and_short_forms(self):
        cases = [
            ("[HKEY_CLASSES_ROOT\\A]", RegRoot.HKCR),
            ("[HKCR\\A]", RegRoot.HKCR),
            ("[HKEY_CURRENT_USER\\A]", RegRoot.HKCU),
            ("[HKCU\\A]", RegRoot.HKCU),
            ("[HKEY_LOCAL_MACHINE\\A]", RegRoot.HKLM),
            ("[HKLM\\A]", RegRoot.HKLM),
            ("[HKEY_USERS\\A]", RegRoot.HKU),
            ("[HKU\\A]", RegRoot.HKU),
            ("[HKEY_CURRENT_CONFIG\\A]", RegRoot.HKCC),
            ("[HKCC\\A]", RegRoot.HKCC),
        ]
        for section, expected in cases:
            with self.subTest(section=section):
                result = parse_reg_text(support.reg_text(section))
                self.assertFalse(result.has_errors, result.diagnostics)
                self.assertIs(result.operations[0].root, expected)
                self.assertEqual(result.operations[0].path, "A")

    def test_roots_are_case_insensitive(self):
        result = parse_reg_text(support.reg_text("[hkey_current_user\\a\\b]"))
        self.assertFalse(result.has_errors)
        self.assertIs(result.operations[0].root, RegRoot.HKCU)
        self.assertEqual(result.operations[0].path, "a\\b")

    def test_surrounding_spaces_are_ignored(self):
        result = parse_reg_text(support.reg_text("   [ HKCU\\X ]   "))
        self.assertFalse(result.has_errors, result.diagnostics)
        self.assertEqual(result.operations[0].path, "X")

    def test_empty_path_is_rejected(self):
        for section in ("[HKLM]", "[HKLM\\]", "[HKEY_CURRENT_USER]"):
            with self.subTest(section=section):
                result = parse_reg_text(support.reg_text(section))
                self.assertTrue(result.has_errors)
                self.assertIn("empty key path", result.errors[0].message)

    def test_unsupported_roots_are_rejected(self):
        for section in ("[HKEY_DYN_DATA\\Legacy]",
                        "[HKEY_PERFORMANCE_DATA\\X]",
                        "[HKDD\\X]", "[HKPD\\X]",
                        "[HKEY_CURRENT_USERX\\Y]",
                        "[SOFTWARE\\X]"):
            with self.subTest(section=section):
                result = parse_reg_text(support.reg_text(section))
                self.assertTrue(result.has_errors)
                self.assertIn("unknown registry root",
                              result.errors[0].message)
                self.assertEqual(result.operations, [])

    def test_lenient_mode_still_rejects_unknown_roots(self):
        result = parse_reg_text(support.reg_text("[HKDD\\X]"), lenient=True)
        self.assertTrue(result.has_errors)


class SectionStructureTests(unittest.TestCase):

    def test_unterminated_section(self):
        result = parse_reg_text(support.reg_text("[HKCU\\X"))
        self.assertTrue(result.has_errors)
        self.assertIn("unterminated section header",
                      result.errors[0].message)

    def test_value_outside_section(self):
        result = parse_reg_text(support.reg_text('"a"="b"'))
        self.assertTrue(result.has_errors)
        self.assertIn("value outside of a section",
                      result.errors[0].message)

    def test_delete_key_does_not_become_current(self):
        result = parse_reg_text(support.reg_text(
            "[-HKCU\\Gone]",
            '"a"="b"',
        ))
        self.assertTrue(result.has_errors)
        self.assertEqual([op.kind for op in result.operations],
                         [OpKind.DELETE_KEY])
        self.assertIn("value outside of a section",
                      result.errors[0].message)

    def test_delete_key_is_a_single_operation(self):
        result = parse_reg_text(support.reg_text("[-HKCU\\Software\\Gone]"))
        self.assertFalse(result.has_errors, result.diagnostics)
        operation = result.operations[0]
        self.assertIs(operation.kind, OpKind.DELETE_KEY)
        self.assertEqual(operation.path, "Software\\Gone")

    def test_header_after_first_section_is_a_warning(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            support.HEADER,
            '"a"="b"',
        ))
        self.assertFalse(result.has_errors)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("after the first section", result.warnings[0].message)

    def test_regedit4_header_is_accepted(self):
        result = parse_reg_text(support.reg_text(
            "REGEDIT4", "[HKCU\\X]", '"a"="b"'))
        self.assertFalse(result.has_errors, result.diagnostics)
        self.assertEqual(len(result.operations), 2)

    def test_create_key_precedes_the_values_of_its_section(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\A]", '"x"="1"', "[HKCU\\B]", '"y"="2"'))
        self.assertEqual([op.kind for op in result.operations],
                         [OpKind.CREATE_KEY, OpKind.SET_VALUE,
                          OpKind.CREATE_KEY, OpKind.SET_VALUE])
        self.assertEqual([op.path for op in result.operations],
                         ["A", "A", "B", "B"])

    def test_bomless_utf16_document(self):
        text = support.reg_text("[HKCU\\X]", '"a"="b"')
        result = parse_reg_bytes(text.encode("utf-16-le"))
        self.assertFalse(result.has_errors, result.diagnostics)
        self.assertEqual(len(result.operations), 2)


class ModeTests(unittest.TestCase):

    def test_user_only(self):
        result = parse_reg_text(support.reg_text("[HKCU\\X]"))
        self.assertEqual(result.mode, Mode.HKCU)

    def test_machine_only(self):
        result = parse_reg_text(support.reg_text("[HKLM\\X]"))
        self.assertEqual(result.mode, Mode.HKLM)

    def test_mixed(self):
        result = parse_reg_text(support.reg_text("[HKCU\\X]", "[HKLM\\Y]"))
        self.assertEqual(result.mode, Mode.MIXED)

    def test_hkcr_needs_no_elevation(self):
        result = parse_reg_text(support.reg_text("[HKCR\\X]"))
        self.assertEqual(result.mode, Mode.HKCU)

    def test_hku_and_hkcc_need_elevation(self):
        for section in ("[HKU\\X]", "[HKCC\\X]"):
            with self.subTest(section=section):
                result = parse_reg_text(support.reg_text(section))
                self.assertEqual(result.mode, Mode.HKLM)

    def test_no_operations_yields_zero(self):
        result = parse_reg_text(support.reg_text())
        self.assertEqual(result.mode, 0)


if __name__ == "__main__":
    unittest.main()

