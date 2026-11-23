# -*- coding: utf-8 -*-
"""
Applying operations to the live registry (B1, B20, B21, §7.10).

Every test works inside a unique
``HKCU\\Software\\__pyreg2exe_tests__\\<uuid>`` key and removes it afterwards.
"""

import unittest

import support

from pyreg2core import elevate
from pyreg2core.errors import RegistryError
from pyreg2core.expand import VariableExpander
from pyreg2core.reg_types import OpKind, Operation, RegRoot, RegType
from pyreg2core.registry import RegistryApplier, normalize_view


def set_value(path, name, value, vtype, root=RegRoot.HKCU):
    return Operation(OpKind.SET_VALUE, root, path, name=name, value=value,
                     vtype=vtype)


def create_key(path):
    return Operation(OpKind.CREATE_KEY, RegRoot.HKCU, path)


def delete_value(path, name):
    return Operation(OpKind.DELETE_VALUE, RegRoot.HKCU, path, name=name)


def delete_key(path):
    return Operation(OpKind.DELETE_KEY, RegRoot.HKCU, path)


class ValueTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()
        self.applier = RegistryApplier(
            expander=VariableExpander(environ={}))

    def test_create_key(self):
        report = self.applier.apply([create_key(self.path)])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.keys_created, 1)
        self.assertTrue(support.key_exists(self.path))

    def test_string_value(self):
        report = self.applier.apply([
            set_value(self.path, "Name", "VKRadeon", RegType.SZ)])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.values_written, 1)
        self.assertEqual(support.read_value(self.path, "Name"),
                         ("VKRadeon", 1))

    def test_default_value(self):
        self.applier.apply([set_value(self.path, "", "def", RegType.SZ)])
        self.assertEqual(support.read_value(self.path, "")[0], "def")

    def test_dword_value(self):
        self.applier.apply([
            set_value(self.path, "Number", 0xFFFFFFFF, RegType.DWORD)])
        self.assertEqual(support.read_value(self.path, "Number"),
                         (0xFFFFFFFF, 4))

    def test_qword_value(self):
        self.applier.apply([
            set_value(self.path, "Big", 0x1122334455667788, RegType.QWORD)])
        self.assertEqual(support.read_value(self.path, "Big"),
                         (0x1122334455667788, 11))

    def test_binary_value(self):
        self.applier.apply([
            set_value(self.path, "Data", b"\x00\x01\xff", RegType.BINARY)])
        self.assertEqual(support.read_value(self.path, "Data"),
                         (b"\x00\x01\xff", 3))

    def test_none_value(self):
        self.applier.apply([
            set_value(self.path, "Raw", b"\x00\x00", RegType.NONE)])
        self.assertEqual(support.read_value(self.path, "Raw")[1], 0)

    def test_expand_sz_value(self):
        self.applier.apply([
            set_value(self.path, "Path", "%TEMP%\\x", RegType.EXPAND_SZ)])
        self.assertEqual(support.read_value(self.path, "Path"),
                         ("%TEMP%\\x", 2))

    def test_multi_sz_value(self):
        self.applier.apply([
            set_value(self.path, "List", ["a", "b"], RegType.MULTI_SZ)])
        data, vtype = support.read_value(self.path, "List")
        self.assertEqual(vtype, 7)
        self.assertEqual([item for item in data if item != ""], ["a", "b"])

    def test_empty_multi_sz_value(self):
        self.applier.apply([
            set_value(self.path, "List", [], RegType.MULTI_SZ)])
        data, vtype = support.read_value(self.path, "List")
        self.assertEqual(vtype, 7)
        self.assertEqual([item for item in data if item != ""], [])

    def test_setting_a_value_creates_the_key_chain(self):
        nested = self.path + "\\deeper\\still"
        self.applier.apply([set_value(nested, "x", "1", RegType.SZ)])
        self.assertEqual(support.read_value(nested, "x")[0], "1")

    def test_duplicate_assignment_last_wins(self):
        self.applier.apply([
            set_value(self.path, "x", "first", RegType.SZ),
            set_value(self.path, "x", "second", RegType.SZ)])
        self.assertEqual(support.read_value(self.path, "x")[0], "second")


class DeletionTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()
        self.applier = RegistryApplier(
            expander=VariableExpander(environ={}))

    def test_delete_value(self):
        self.applier.apply([set_value(self.path, "x", "1", RegType.SZ)])
        report = self.applier.apply([delete_value(self.path, "x")])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.values_deleted, 1)
        self.assertNotIn("x", support.value_names(self.path))

    def test_delete_missing_value_is_success(self):
        self.applier.apply([create_key(self.path)])
        report = self.applier.apply([delete_value(self.path, "absent")])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.values_deleted, 0)

    def test_delete_missing_key_is_success(self):
        report = self.applier.apply([delete_key(self.path + "\\absent")])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.keys_deleted, 0)

    def test_delete_key_is_recursive(self):
        nested = self.path + "\\a\\b"
        self.applier.apply([set_value(nested, "x", "1", RegType.SZ)])
        report = self.applier.apply([delete_key(self.path)])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(report.keys_deleted, 1)
        self.assertFalse(support.key_exists(self.path))

    def test_recreating_a_deleted_key(self):
        self.applier.apply([
            set_value(self.path + "\\sub", "x", "1", RegType.SZ),
            delete_key(self.path),
            set_value(self.path + "\\sub", "x", "2", RegType.SZ),
        ])
        self.assertEqual(support.read_value(self.path + "\\sub", "x")[0],
                         "2")


class FailureTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()

    def test_a_failing_operation_does_not_abort_the_run(self):
        if elevate.is_admin():
            self.skipTest("needs a non-elevated process")
        denied = Operation(OpKind.SET_VALUE, RegRoot.HKLM,
                           "SOFTWARE\\__pyreg2exe_tests__\\denied",
                           name="x", value="1", vtype=RegType.SZ)
        report = RegistryApplier().apply([
            denied,
            set_value(self.path, "after", "ok", RegType.SZ),
        ])
        self.assertTrue(report.failed)
        self.assertIn("access denied", str(report.errors[0]))
        self.assertEqual(report.values_written, 1)
        self.assertEqual(support.read_value(self.path, "after")[0], "ok")

    def test_stop_on_error_aborts(self):
        if elevate.is_admin():
            self.skipTest("needs a non-elevated process")
        denied = Operation(OpKind.SET_VALUE, RegRoot.HKLM,
                           "SOFTWARE\\__pyreg2exe_tests__\\denied",
                           name="x", value="1", vtype=RegType.SZ)
        report = RegistryApplier(stop_on_error=True).apply([
            denied,
            set_value(self.path, "after", "ok", RegType.SZ),
        ])
        self.assertTrue(report.failed)
        self.assertEqual(report.values_written, 0)
        self.assertFalse(support.key_exists(self.path))

    def test_out_of_range_dword_is_reported(self):
        report = RegistryApplier().apply([
            set_value(self.path, "big", 0x1FFFFFFFF, RegType.DWORD)])
        self.assertTrue(report.failed)
        self.assertIn("out of range", str(report.errors[0]))


class ViewTests(unittest.TestCase):

    def test_view_aliases(self):
        self.assertEqual(normalize_view(None), "default")
        self.assertEqual(normalize_view("32"), "32")
        self.assertEqual(normalize_view("x86"), "32")
        self.assertEqual(normalize_view("64-bit"), "64")

    def test_unknown_view(self):
        with self.assertRaises(RegistryError):
            normalize_view("128")

    def test_view_flag_is_applied(self):
        applier = RegistryApplier(view="64")
        self.assertEqual(applier.view_flag, 0x0100)


class ExpansionIntegrationTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()

    def test_value_data_is_expanded_but_names_are_not(self):
        applier = RegistryApplier(expander=VariableExpander(
            base_dir=r"C:\patch", environ={"MYVAR": "expanded"}))
        report = applier.apply([
            set_value(self.path, "%MYVAR%", "%MYVAR%\\x", RegType.SZ)])
        self.assertFalse(report.failed, report.diagnostics)
        self.assertEqual(support.value_names(self.path), ["%MYVAR%"])
        self.assertEqual(support.read_value(self.path, "%MYVAR%")[0],
                         "expanded\\x")

    def test_expand_sz_keeps_environment_tokens(self):
        applier = RegistryApplier(expander=VariableExpander(
            base_dir=r"C:\patch", environ={"MYVAR": "expanded"}))
        applier.apply([
            set_value(self.path, "p", "%MYVAR%\\%CD%", RegType.EXPAND_SZ)])
        self.assertEqual(support.read_value(self.path, "p")[0],
                         "%MYVAR%\\C:\\patch")


if __name__ == "__main__":
    unittest.main()

