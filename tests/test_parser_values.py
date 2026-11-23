# -*- coding: utf-8 -*-
"""
Value lines, hex payloads and escapes (B5, B11-B14, B18, §7.5-§7.7).
"""

import unittest

import support

from pyreg2core.parser import parse_reg_text
from pyreg2core.reg_types import OpKind, RegType


def parse_value(line, lenient=False):
    """Parse one value line inside a section."""
    return parse_reg_text(support.reg_text("[HKCU\\X]", line),
                          lenient=lenient)


def single_result(line, lenient=False):
    result = parse_value(line, lenient=lenient)
    assert not result.has_errors, [str(d) for d in result.errors]
    assert len(result.operations) == 2, result.operations
    return result.operations[1]


class StringValueTests(unittest.TestCase):

    def test_quoted_string(self):
        operation = single_result('"Name"="VKRadeon"')
        self.assertIs(operation.kind, OpKind.SET_VALUE)
        self.assertEqual(operation.name, "Name")
        self.assertEqual(operation.value, "VKRadeon")
        self.assertIs(operation.vtype, RegType.SZ)

    def test_default_value(self):
        operation = single_result('@="Default"')
        self.assertEqual(operation.name, "")
        self.assertEqual(operation.value, "Default")

    def test_backslash_escape_is_decoded(self):
        operation = single_result('"Path"="C:\\\\dir\\\\file"')
        self.assertEqual(operation.value, "C:\\dir\\file")

    def test_quote_escape_is_decoded(self):
        operation = single_result('"Quote"="say \\"hi\\""')
        self.assertEqual(operation.value, 'say "hi"')

    def test_unknown_escape_is_an_error(self):
        result = parse_value('"Path"="C:\\new"')
        self.assertTrue(result.has_errors)
        self.assertIn("invalid escape sequence", result.errors[0].message)

    def test_unknown_escape_is_kept_in_lenient_mode(self):
        result = parse_value('"Path"="C:\\new"', lenient=True)
        self.assertFalse(result.has_errors)
        self.assertEqual(result.operations[1].value, "C:\\new")
        self.assertTrue(result.warnings)

    def test_unterminated_string(self):
        result = parse_value('"Path"="C:\\dir')
        self.assertTrue(result.has_errors)
        self.assertIn("unterminated quoted string",
                      result.errors[0].message)

    def test_empty_string(self):
        operation = single_result('"Empty"=""')
        self.assertEqual(operation.value, "")

    def test_unquoted_name_is_rejected(self):
        result = parse_value('Name="value"')
        self.assertTrue(result.has_errors)
        self.assertIn("value name must be quoted",
                      result.errors[0].message)

    def test_name_with_backslash_is_rejected(self):
        result = parse_value('"a\\\\b"="value"')
        self.assertTrue(result.has_errors)
        self.assertIn("must not contain a backslash",
                      result.errors[0].message)

    def test_missing_assignment(self):
        result = parse_value('"lonely"')
        self.assertTrue(result.has_errors)
        self.assertIn("expected a value assignment",
                      result.errors[0].message)

    def test_unquoted_data_is_an_error(self):
        result = parse_value('"Path"=%CD%\\dir')
        self.assertTrue(result.has_errors)
        self.assertIn("must be quoted", result.errors[0].message)

    def test_unquoted_data_is_a_warning_in_lenient_mode(self):
        result = parse_value('"Path"=%CD%\\dir', lenient=True)
        self.assertFalse(result.has_errors)
        self.assertEqual(result.operations[1].value, "%CD%\\dir")
        self.assertEqual(result.operations[1].vtype, RegType.SZ)
        self.assertEqual(len(result.warnings), 1)

    def test_line_numbers_are_reported(self):
        result = parse_value('"a"="b"', lenient=False)
        self.assertEqual(result.operations[1].line, 4)


class DeletionTests(unittest.TestCase):

    def test_delete_value(self):
        operation = single_result('"Old"=-')
        self.assertIs(operation.kind, OpKind.DELETE_VALUE)
        self.assertEqual(operation.name, "Old")

    def test_delete_default_value(self):
        operation = single_result("@=-")
        self.assertIs(operation.kind, OpKind.DELETE_VALUE)
        self.assertEqual(operation.name, "")


class DwordTests(unittest.TestCase):

    def test_dword(self):
        operation = single_result('"Number"=dword:00000001')
        self.assertEqual(operation.value, 1)
        self.assertIs(operation.vtype, RegType.DWORD)

    def test_dword_short_form(self):
        operation = single_result('"Number"=dword:ff')
        self.assertEqual(operation.value, 255)

    def test_dword_maximum(self):
        operation = single_result('"Number"=dword:ffffffff')
        self.assertEqual(operation.value, 0xFFFFFFFF)

    def test_dword_overflow_is_an_error(self):
        result = parse_value('"Number"=dword:100000000')
        self.assertTrue(result.has_errors)
        self.assertIn("exceeds 0xFFFFFFFF", result.errors[0].message)

    def test_dword_garbage_is_an_error(self):
        for value in ("dword:", "dword:xyz", "dword:-1"):
            with self.subTest(value=value):
                result = parse_value('"Number"={0}'.format(value))
                self.assertTrue(result.has_errors)


class HexPayloadTests(unittest.TestCase):

    def test_binary(self):
        operation = single_result('"Data"=hex:01,02,03')
        self.assertEqual(operation.value, b"\x01\x02\x03")
        self.assertIs(operation.vtype, RegType.BINARY)

    def test_binary_with_spaces_around_separators(self):
        operation = single_result('"Data"=hex:01, 02 ,03')
        self.assertEqual(operation.value, b"\x01\x02\x03")

    def test_empty_payload(self):
        operation = single_result('"Data"=hex:')
        self.assertEqual(operation.value, b"")
        self.assertIs(operation.vtype, RegType.BINARY)

    def test_hex_none(self):
        operation = single_result('"Data"=hex(0):01,02')
        self.assertEqual(operation.value, b"\x01\x02")
        self.assertIs(operation.vtype, RegType.NONE)

    def test_hex_sz(self):
        payload = "abc\x00".encode("utf-16-le")
        text = ",".join("{0:02x}".format(byte) for byte in payload)
        operation = single_result('"Text"=hex(1):{0}'.format(text))
        self.assertEqual(operation.value, "abc")
        self.assertIs(operation.vtype, RegType.SZ)

    def test_hex_expand_sz(self):
        payload = "%TEMP%\\x\x00".encode("utf-16-le")
        text = ",".join("{0:02x}".format(byte) for byte in payload)
        operation = single_result('"Text"=hex(2):{0}'.format(text))
        self.assertEqual(operation.value, "%TEMP%\\x")
        self.assertIs(operation.vtype, RegType.EXPAND_SZ)

    def test_hex_binary_subtype(self):
        operation = single_result('"Data"=hex(3):0a,0b')
        self.assertEqual(operation.value, b"\x0a\x0b")
        self.assertIs(operation.vtype, RegType.BINARY)

    def test_hex_dword(self):
        operation = single_result('"Number"=hex(4):01,00,00,00')
        self.assertEqual(operation.value, 1)
        self.assertIs(operation.vtype, RegType.DWORD)

    def test_hex_dword_wrong_length(self):
        for payload in ("01,00,00", "01,00,00,00,00"):
            with self.subTest(payload=payload):
                result = parse_value('"Number"=hex(4):{0}'.format(payload))
                self.assertTrue(result.has_errors)
                self.assertIn("exactly 4 bytes", result.errors[0].message)

    def test_hex_qword(self):
        operation = single_result('"Number"=hex(b):01,00,00,00,00,00,00,00')
        self.assertEqual(operation.value, 1)
        self.assertIs(operation.vtype, RegType.QWORD)

    def test_hex_qword_uppercase_b(self):
        operation = single_result('"Number"=hex(B):02,00,00,00,00,00,00,00')
        self.assertEqual(operation.value, 2)
        self.assertIs(operation.vtype, RegType.QWORD)

    def test_hex_qword_wrong_length(self):
        result = parse_value('"Number"=hex(b):01,00,00,00')
        self.assertTrue(result.has_errors)
        self.assertIn("exactly 8 bytes", result.errors[0].message)

    def test_multi_sz(self):
        payload = "a\x00b\x00\x00".encode("utf-16-le")
        text = ",".join("{0:02x}".format(byte) for byte in payload)
        operation = single_result('"Multi"=hex(7):{0}'.format(text))
        self.assertEqual(operation.value, ["a", "b"])
        self.assertIs(operation.vtype, RegType.MULTI_SZ)

    def test_multi_sz_single_item(self):
        payload = "a\x00\x00".encode("utf-16-le")
        text = ",".join("{0:02x}".format(byte) for byte in payload)
        operation = single_result('"Multi"=hex(7):{0}'.format(text))
        self.assertEqual(operation.value, ["a"])

    def test_multi_sz_empty(self):
        operation = single_result('"Multi"=hex(7):')
        self.assertEqual(operation.value, [])
        self.assertIs(operation.vtype, RegType.MULTI_SZ)

    def test_unsupported_subtypes_are_rejected(self):
        for subtype in ("5", "6", "8", "a", "9"):
            with self.subTest(subtype=subtype):
                result = parse_value('"Data"=hex({0}):01'.format(subtype))
                self.assertTrue(result.has_errors)
                self.assertIn("unsupported value type",
                              result.errors[0].message)

    def test_malformed_subtype_payload(self):
        result = parse_value('"Data"=hex(4:01,00,00,00')
        self.assertTrue(result.has_errors)
        self.assertIn("malformed value type", result.errors[0].message)

    def test_odd_length_text_payload(self):
        result = parse_value('"Text"=hex(1):61,00,62')
        self.assertTrue(result.has_errors)
        self.assertIn("even byte count", result.errors[0].message)

    def test_invalid_byte_tokens(self):
        for token in ("0x0A", "1", "+1", "-1", "123", "0 A", "GG"):
            with self.subTest(token=token):
                result = parse_value('"Data"=hex:{0}'.format(token))
                self.assertTrue(result.has_errors)
                self.assertIn("hex byte", result.errors[0].message)

    def test_trailing_comma(self):
        result = parse_value('"Data"=hex:01,02,')
        self.assertTrue(result.has_errors)
        self.assertIn("trailing comma", result.errors[0].message)

    def test_empty_byte_in_the_middle(self):
        result = parse_value('"Data"=hex:01,,02')
        self.assertTrue(result.has_errors)
        self.assertIn("empty byte", result.errors[0].message)


class ContinuationTests(unittest.TestCase):

    def test_hex_continuation_is_joined(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            '"Data"=hex:01,02,\\',
            '  03,04',
            '"Next"="value"',
        ))
        self.assertFalse(result.has_errors, [str(d) for d in result.errors])
        self.assertEqual(result.operations[1].value,
                         b"\x01\x02\x03\x04")
        self.assertEqual(result.operations[1].line, 4)
        self.assertEqual(result.operations[2].name, "Next")

    def test_comment_never_continues(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            "; a comment ending with a backslash \\",
            '"a"="b"',
        ))
        self.assertFalse(result.has_errors, [str(d) for d in result.errors])
        self.assertEqual(len(result.operations), 2)

    def test_quoted_escape_is_not_a_continuation(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            '"a"="ends with a quote \\""',
            '"b"="second"',
        ))
        self.assertFalse(result.has_errors, [str(d) for d in result.errors])
        self.assertEqual(result.operations[1].value,
                         'ends with a quote "')
        self.assertEqual(result.operations[2].name, "b")

    def test_continuation_outside_hex_is_an_error(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            '"a"="value"\\',
            '"b"="second"',
        ))
        self.assertTrue(result.has_errors)
        self.assertIn("unexpected line continuation",
                      result.errors[0].message)

    def test_continuation_outside_hex_is_a_warning_in_lenient_mode(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            '"a"="value"\\',
            '"b"="second"',
        ), lenient=True)
        self.assertFalse(result.has_errors)
        self.assertEqual(result.operations[1].value, "value")
        self.assertTrue(result.warnings)

    def test_continuation_at_end_of_file(self):
        result = parse_reg_text(support.reg_text(
            "[HKCU\\X]",
            '"Data"=hex:01,\\',
        ))
        self.assertTrue(result.has_errors)
        self.assertIn("unexpected end of file",
                      result.errors[0].message)


if __name__ == "__main__":
    unittest.main()


