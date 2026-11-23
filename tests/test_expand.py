# -*- coding: utf-8 -*-
"""
Variable expansion: %CD%, %VAR% and <reg2exe...> markers (B15, §7.9).
"""

import unittest

from pyreg2core.expand import MARKERS, VariableExpander, make_expander
from pyreg2core.reg_types import RegType

BASE = r"C:\Program Files\Patch"
ENVIRON = {
    "WINDIR": r"C:\Windows",
    "SYSTEMROOT": r"C:\Windows",
    "TEMP": r"C:\Temp",
    "PROGRAMFILES": r"C:\Program Files",
    "APPDATA": r"C:\Users\u\AppData\Roaming",
    "LOCALAPPDATA": r"C:\Users\u\AppData\Local",
    "USERNAME": "u",
    "COMPUTERNAME": "PC",
    "USERPROFILE": r"C:\Users\u",
    "CD": r"C:\wrong",
    "MYVAR": "value",
}


class PercentTests(unittest.TestCase):

    def test_cd_uses_the_patch_directory(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("%CD%\\data"),
                         BASE + "\\data")

    def test_cd_lowercase(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("%cd%\\x"), BASE + "\\x")

    def test_environment_variable(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("%MYVAR%!"), "value!")

    def test_unknown_token_is_kept(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("%NOPE%\\x"), "%NOPE%\\x")

    def test_environment_expansion_can_be_disabled(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON,
                                    expand_env=False)
        self.assertEqual(expander.expand("%MYVAR%"), "%MYVAR%")
        self.assertEqual(expander.expand("%CD%\\x"), BASE + "\\x")

    def test_empty_text(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand(""), "")


class MarkerTests(unittest.TestCase):

    def test_reg2exepath(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("<reg2exepath>\\a"), BASE + "\\a")

    def test_every_marker(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        for marker, variable in MARKERS.items():
            with self.subTest(marker=marker):
                expected = BASE if variable is None else ENVIRON[variable]
                self.assertEqual(expander.expand(marker), expected)

    def test_markers_are_case_insensitive(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("<REG2EXEWINPATH>"), r"C:\Windows")

    def test_unknown_marker_is_kept(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON)
        self.assertEqual(expander.expand("<reg2exeother>"),
                         "<reg2exeother>")

    def test_missing_environment_variable_keeps_the_marker(self):
        expander = VariableExpander(base_dir=BASE, environ={})
        self.assertEqual(expander.expand("<reg2exetemppath>"),
                         "<reg2exetemppath>")

    def test_marker_expansion_can_be_disabled(self):
        expander = VariableExpander(base_dir=BASE, environ=ENVIRON,
                                    expand_env=False)
        self.assertEqual(expander.expand("<reg2exewinpath>"),
                         "<reg2exewinpath>")
        self.assertEqual(expander.expand("<reg2exepath>"), BASE)


class ValueTypeTests(unittest.TestCase):

    def setUp(self):
        self.expander = VariableExpander(base_dir=BASE, environ=ENVIRON)

    def test_sz_expands_environment(self):
        self.assertEqual(
            self.expander.expand_value("%MYVAR%", RegType.SZ), "value")

    def test_expand_sz_keeps_environment_literal(self):
        self.assertEqual(
            self.expander.expand_value("%MYVAR%\\x", RegType.EXPAND_SZ),
            "%MYVAR%\\x")

    def test_expand_sz_still_expands_the_patch_directory(self):
        self.assertEqual(
            self.expander.expand_value("%CD%\\x", RegType.EXPAND_SZ),
            BASE + "\\x")

    def test_multi_sz_expands_each_item(self):
        self.assertEqual(
            self.expander.expand_value(["%MYVAR%", "%CD%"],
                                       RegType.MULTI_SZ),
            ["value", BASE])

    def test_binary_is_untouched(self):
        payload = b"%CD%"
        self.assertIs(self.expander.expand_value(payload, RegType.BINARY),
                      payload)

    def test_dword_is_untouched(self):
        self.assertEqual(self.expander.expand_value(7, RegType.DWORD), 7)

    def test_make_expander_accepts_extra_variables(self):
        expander = make_expander(environ={}, extra={"%myvar%": "x"})
        self.assertEqual(expander.expand("%MyVar%"), "x")

    def test_extra_variables_win_over_the_environment(self):
        expander = VariableExpander(environ=ENVIRON,
                                    extra={"%myvar%": "override"})
        self.assertEqual(expander.expand("%MYVAR%"), "override")


if __name__ == "__main__":
    unittest.main()
