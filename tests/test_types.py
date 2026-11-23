# -*- coding: utf-8 -*-
"""
Value objects of the core: roots, modes, value types and operation rendering
(B6, B7, B11, B12, B16, §6.4).

The parser and the applier reach these objects indirectly; this module pins the
contracts they rely on, so a regression fails here first and with a precise
message instead of surfacing as a mis-routed operation or a wrong overlay mode.
"""

import unittest

import support  # noqa: F401  (puts the repository root on sys.path)

from pyreg2core.reg_types import (Mode, OpKind, Operation, RegRoot, RegType,
                                 compute_mode, parse_subtype_text)


class ModeTests(unittest.TestCase):
    """The mode byte is frozen by §4.4; the helpers drive routing (B16)."""

    def test_numeric_values_are_frozen(self):
        self.assertEqual(int(Mode.HKLM), 0x01)
        self.assertEqual(int(Mode.HKCU), 0x02)
        self.assertEqual(int(Mode.MIXED), 0x03)

    def test_mode_is_an_int(self):
        self.assertEqual(Mode.MIXED, 3)
        self.assertEqual(Mode.HKLM + Mode.HKCU, Mode.MIXED)

    def test_admin_and_user_flags(self):
        expected = {
            Mode.HKLM: (True, False),
            Mode.HKCU: (False, True),
            Mode.MIXED: (True, True),
        }
        for mode, (admin, user) in expected.items():
            with self.subTest(mode=mode):
                self.assertIs(mode.needs_admin, admin)
                self.assertIs(mode.needs_user, user)

    def test_str_is_human_readable(self):
        self.assertEqual(str(Mode.HKLM), "HKLM only")
        self.assertEqual(str(Mode.HKCU), "HKCU only")
        self.assertEqual(str(Mode.MIXED), "mixed (HKLM + HKCU)")


class RegRootParseTests(unittest.TestCase):
    """``RegRoot.parse`` must match whole tokens only (B6, B7)."""

    LONG_FORMS = {
        "HKEY_CLASSES_ROOT": RegRoot.HKCR,
        "HKEY_CURRENT_USER": RegRoot.HKCU,
        "HKEY_LOCAL_MACHINE": RegRoot.HKLM,
        "HKEY_USERS": RegRoot.HKU,
        "HKEY_CURRENT_CONFIG": RegRoot.HKCC,
    }
    SHORT_FORMS = {
        "HKCR": RegRoot.HKCR,
        "HKCU": RegRoot.HKCU,
        "HKLM": RegRoot.HKLM,
        "HKU": RegRoot.HKU,
        "HKCC": RegRoot.HKCC,
    }

    def test_long_forms(self):
        for token, expected in self.LONG_FORMS.items():
            with self.subTest(token=token):
                self.assertIs(RegRoot.parse(token), expected)

    def test_short_forms(self):
        for token, expected in self.SHORT_FORMS.items():
            with self.subTest(token=token):
                self.assertIs(RegRoot.parse(token), expected)

    def test_parse_is_case_insensitive(self):
        self.assertIs(RegRoot.parse("hkey_local_machine"), RegRoot.HKLM)
        self.assertIs(RegRoot.parse("HkCu"), RegRoot.HKCU)

    def test_surrounding_whitespace_is_stripped(self):
        self.assertIs(RegRoot.parse("  HKLM\t"), RegRoot.HKLM)

    def test_prefix_matches_are_rejected(self):
        # B6: "HKEY_LOCAL_MACHINEX" must not resolve to HKEY_LOCAL_MACHINE.
        for token in ("HKEY_LOCAL_MACHINEX", "HKEY_CURRENT_USERS", "HKLMX",
                      "HKCR2", "HKCU_", "HKL", "HKEY_LOCAL_MACHINE\\SOFTWARE"):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    RegRoot.parse(token)

    def test_unknown_roots_are_rejected(self):
        for token in ("HKEY_DYN_DATA", "HKEY_PERFORMANCE_DATA", "HKDD",
                      "HKPD", "SOFTWARE", ""):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    RegRoot.parse(token)

    def test_error_names_the_offending_token_and_lists_supported_roots(self):
        with self.assertRaises(ValueError) as caught:
            RegRoot.parse("HKEY_DYN_DATA")
        message = str(caught.exception)
        self.assertIn("unknown registry root 'HKEY_DYN_DATA'", message)
        self.assertIn("HKEY_LOCAL_MACHINE", message)
        self.assertIn("short forms", message)


class RegRootSectionTests(unittest.TestCase):
    """``from_section`` splits ``ROOT\\sub\\key`` and rejects an empty path (B8)."""

    def test_splits_root_and_path(self):
        root, path = RegRoot.from_section("HKEY_CURRENT_USER\\Software\\A")
        self.assertIs(root, RegRoot.HKCU)
        self.assertEqual(path, "Software\\A")

    def test_short_form_is_accepted(self):
        root, path = RegRoot.from_section("HKLM\\SOFTWARE\\Test")
        self.assertIs(root, RegRoot.HKLM)
        self.assertEqual(path, "SOFTWARE\\Test")

    def test_outer_whitespace_and_backslashes_are_trimmed(self):
        root, path = RegRoot.from_section("  HKCU\\A\\B\\\\  ")
        self.assertIs(root, RegRoot.HKCU)
        self.assertEqual(path, "A\\B")

    def test_empty_path_is_rejected(self):
        # B8: a section must name a subkey.
        for section in ("HKLM", "HKLM\\", "HKCU\\\\", " HKEY_USERS "):
            with self.subTest(section=section):
                with self.assertRaises(ValueError) as caught:
                    RegRoot.from_section(section)
                self.assertIn("empty key path", str(caught.exception))

    def test_unknown_root_raises_value_error(self):
        with self.assertRaises(ValueError):
            RegRoot.from_section("HKEY_DYN_DATA\\Legacy")


class RegRootMetaTests(unittest.TestCase):

    def test_long_and_short_names(self):
        self.assertEqual(RegRoot.HKLM.long_name, "HKEY_LOCAL_MACHINE")
        self.assertEqual(RegRoot.HKLM.short_name, "HKLM")
        self.assertEqual(str(RegRoot.HKCU), "HKEY_CURRENT_USER")

    def test_requires_admin(self):
        # Only the per-user and class-registration roots are user-writable.
        expected = {
            RegRoot.HKCR: False,
            RegRoot.HKCU: False,
            RegRoot.HKLM: True,
            RegRoot.HKU: True,
            RegRoot.HKCC: True,
        }
        for root, admin in expected.items():
            with self.subTest(root=root):
                self.assertIs(root.requires_admin, admin)


class RegTypeTests(unittest.TestCase):

    def test_numeric_codes(self):
        expected = {
            RegType.NONE: 0,
            RegType.SZ: 1,
            RegType.EXPAND_SZ: 2,
            RegType.BINARY: 3,
            RegType.DWORD: 4,
            RegType.MULTI_SZ: 7,
            RegType.QWORD: 11,
        }
        for vtype, code in expected.items():
            with self.subTest(vtype=vtype):
                self.assertEqual(vtype.value, code)
                self.assertEqual(vtype.subtype, code)

    def test_reg_name(self):
        self.assertEqual(RegType.SZ.reg_name, "REG_SZ")
        self.assertEqual(RegType.QWORD.reg_name, "REG_QWORD")

    def test_is_text(self):
        for vtype in (RegType.SZ, RegType.EXPAND_SZ, RegType.MULTI_SZ):
            with self.subTest(vtype=vtype):
                self.assertTrue(vtype.is_text)
                self.assertTrue(vtype.requires_even_length)
        for vtype in (RegType.NONE, RegType.BINARY, RegType.DWORD,
                      RegType.QWORD):
            with self.subTest(vtype=vtype):
                self.assertFalse(vtype.is_text)
                self.assertFalse(vtype.requires_even_length)

    def test_exact_byte_length(self):
        self.assertEqual(RegType.DWORD.exact_byte_length, 4)
        self.assertEqual(RegType.QWORD.exact_byte_length, 8)
        for vtype in (RegType.NONE, RegType.SZ, RegType.EXPAND_SZ,
                      RegType.BINARY, RegType.MULTI_SZ):
            with self.subTest(vtype=vtype):
                self.assertIsNone(vtype.exact_byte_length)

    def test_from_subtype(self):
        for number, expected in ((0, RegType.NONE), (1, RegType.SZ),
                                 (2, RegType.EXPAND_SZ), (3, RegType.BINARY),
                                 (4, RegType.DWORD), (7, RegType.MULTI_SZ),
                                 (0x0B, RegType.QWORD)):
            with self.subTest(number=number):
                self.assertIs(RegType.from_subtype(number), expected)

    def test_from_subtype_rejects_unsupported_numbers(self):
        for number in (5, 6, 8, 9, 10, 0x0C, 0x100):
            with self.subTest(number=number):
                with self.assertRaises(ValueError) as caught:
                    RegType.from_subtype(number)
                self.assertIn("unsupported value type hex({0})".format(
                    format(number, "x")), str(caught.exception))


class SubtypeParsingTests(unittest.TestCase):
    """``hex(N)`` must map exactly, including the ``b`` alias (B11, B12, B13)."""

    def test_decimal_subtypes(self):
        for token, expected in (("0", RegType.NONE), ("1", RegType.SZ),
                                ("2", RegType.EXPAND_SZ),
                                ("3", RegType.BINARY), ("4", RegType.DWORD),
                                ("7", RegType.MULTI_SZ)):
            with self.subTest(token=token):
                self.assertIs(parse_subtype_text(token), expected)

    def test_qword_alias(self):
        # B11/B13: "hex(b)" is REG_QWORD, not a decimal 11 or a rejected token.
        for token in ("b", "B"):
            with self.subTest(token=token):
                self.assertIs(parse_subtype_text(token), RegType.QWORD)

    def test_surrounding_whitespace_is_stripped(self):
        self.assertIs(parse_subtype_text(" 4 "), RegType.DWORD)

    def test_empty_subtype_is_rejected(self):
        for token in ("", "   "):
            with self.subTest(token=token):
                with self.assertRaises(ValueError) as caught:
                    parse_subtype_text(token)
                self.assertIn("empty value type in hex()",
                              str(caught.exception))

    def test_non_hex_tokens_are_rejected(self):
        for token in ("0x4", "g", "-1", "4.0", "1 2"):
            with self.subTest(token=token):
                with self.assertRaises(ValueError) as caught:
                    parse_subtype_text(token)
                self.assertIn("invalid value type hex(", str(caught.exception))

    def test_unsupported_subtype_message_uses_hex(self):
        with self.assertRaises(ValueError) as caught:
            parse_subtype_text("5")
        self.assertEqual(str(caught.exception),
                         "unsupported value type hex(5)")


class ComputeModeTests(unittest.TestCase):
    """The builder derives the mode byte from the operations (B16)."""

    def op(self, root):
        return Operation(OpKind.CREATE_KEY, root, "A")

    def test_no_operations_yield_zero(self):
        self.assertEqual(compute_mode([]), 0)

    def test_user_only(self):
        self.assertIs(compute_mode([self.op(RegRoot.HKCU)]), Mode.HKCU)

    def test_hkcr_counts_as_user(self):
        self.assertIs(compute_mode([self.op(RegRoot.HKCR)]), Mode.HKCU)

    def test_admin_only(self):
        for root in (RegRoot.HKLM, RegRoot.HKU, RegRoot.HKCC):
            with self.subTest(root=root):
                self.assertIs(compute_mode([self.op(root)]), Mode.HKLM)

    def test_mixed(self):
        mode = compute_mode([self.op(RegRoot.HKCU), self.op(RegRoot.HKLM)])
        self.assertIs(mode, Mode.MIXED)
        self.assertEqual(mode, 0x03)

    def test_ignores_duplicates(self):
        operations = [self.op(RegRoot.HKLM), self.op(RegRoot.HKLM)]
        self.assertIs(compute_mode(operations), Mode.HKLM)


class OperationTests(unittest.TestCase):
    """``describe`` feeds the dry run and the log, so its shape is a contract."""

    def test_create_key(self):
        operation = Operation(OpKind.CREATE_KEY, RegRoot.HKCU, "A\\B")
        self.assertEqual(operation.describe(),
                         "create HKEY_CURRENT_USER\\A\\B")

    def test_delete_key(self):
        operation = Operation(OpKind.DELETE_KEY, RegRoot.HKLM, "A")
        self.assertEqual(operation.describe(),
                         "delete HKEY_LOCAL_MACHINE\\A")

    def test_delete_value(self):
        operation = Operation(OpKind.DELETE_VALUE, RegRoot.HKCU, "A",
                              name="Name")
        self.assertEqual(operation.describe(),
                         "delete value HKEY_CURRENT_USER\\A :: 'Name'")

    def test_set_value(self):
        operation = Operation(OpKind.SET_VALUE, RegRoot.HKCU, "A",
                              name="Name", value="VKRadeon",
                              vtype=RegType.SZ)
        self.assertEqual(
            operation.describe(),
            "set HKEY_CURRENT_USER\\A :: 'Name' = 'VKRadeon' (REG_SZ)")

    def test_set_binary_value_is_rendered_as_hex(self):
        operation = Operation(OpKind.SET_VALUE, RegRoot.HKCU, "A",
                              name="Data", value=b"\x00\x01\xff",
                              vtype=RegType.BINARY)
        self.assertIn("= hex:00,01,ff", operation.describe())

    def test_long_values_are_truncated(self):
        operation = Operation(OpKind.SET_VALUE, RegRoot.HKCU, "A",
                              name="Long", value="a" * 60,
                              vtype=RegType.SZ)
        self.assertIn("= '" + "a" * 36 + "...", operation.describe())

    def test_is_admin(self):
        self.assertFalse(Operation(OpKind.CREATE_KEY, RegRoot.HKCU,
                                   "A").is_admin)
        self.assertTrue(Operation(OpKind.CREATE_KEY, RegRoot.HKLM,
                                  "A").is_admin)


if __name__ == "__main__":
    unittest.main()
