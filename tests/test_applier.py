# -*- coding: utf-8 -*-
"""
Apply pipeline: routing, mode cross-check, elevation, exit codes (§6.7).
"""

import os
import unittest
from unittest import mock

import support

from pyreg2core import applier, cli
from pyreg2core.errors import ElevationError, EmbeddedRegError, OverlayError
from pyreg2core.reg_types import Mode


def patch_file(directory, text, mode=Mode.HKCU, name="patch.regpatch"):
    return support.write_patch_file(os.path.join(directory, name), text,
                                    mode=mode)


class NoOverlayTests(unittest.TestCase):

    def test_plain_template_returns_zero(self):
        log = support.LogCapture()
        with mock.patch.object(applier.selfpath, "existing_candidates",
                               return_value=[]):
            self.assertEqual(applier.apply_patch(log=log), 0)
        self.assertIn("plain template", log.text)

    def test_find_patch_returns_none(self):
        with mock.patch.object(applier.selfpath, "existing_candidates",
                               return_value=[]):
            self.assertIsNone(applier.find_patch())

    def test_missing_patch_data_file_raises(self):
        with self.assertRaises(OverlayError):
            applier.find_patch(patch_data="no-such-file.regpatch")


class ApplyTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()
        self._directory = support.temp_dir()
        self.directory = self._directory.name
        self.addCleanup(self._directory.cleanup)

    def test_applies_user_operations(self):
        text = support.reg_text(
            "[HKEY_CURRENT_USER\\{0}]".format(self.path),
            '"Name"="VKRadeon"',
            '"Path"="%CD%\\\\data"',
        )
        patch = patch_file(self.directory, text)
        log = support.LogCapture()
        code = applier.apply_patch(patch_data=patch, patch_dir=r"C:\patch",
                                   log=log)
        self.assertEqual(code, 0, log.text)
        self.assertEqual(support.read_value(self.path, "Name")[0], "VKRadeon")
        self.assertEqual(support.read_value(self.path, "Path")[0],
                         r"C:\patch\data")

    def test_dry_run_changes_nothing(self):
        text = support.reg_text(
            "[HKEY_CURRENT_USER\\{0}]".format(self.path), '"a"="b"')
        patch = patch_file(self.directory, text)
        log = support.LogCapture()
        code = applier.apply_patch(patch_data=patch, dry_run=True, log=log)
        self.assertEqual(code, 0)
        self.assertFalse(support.key_exists(self.path))
        self.assertIn("would apply", log.text)

    def test_empty_patch_is_a_no_op(self):
        patch = patch_file(self.directory, support.reg_text())
        log = support.LogCapture()
        self.assertEqual(applier.apply_patch(patch_data=patch, log=log), 0)
        self.assertIn("no operations", log.text)

    def test_mode_mismatch_is_only_a_warning(self):
        text = support.reg_text(
            "[HKEY_CURRENT_USER\\{0}]".format(self.path), '"a"="b"')
        patch = patch_file(self.directory, text, mode=Mode.HKLM)
        log = support.LogCapture()
        self.assertEqual(applier.apply_patch(patch_data=patch, log=log), 0)
        self.assertIn("does not match", log.text)
        self.assertEqual(support.read_value(self.path, "a")[0], "b")

    def test_invalid_embedded_reg_exits_with_five(self):
        text = support.reg_text(
            "[HKEY_CURRENT_USER\\{0}]".format(self.path), '"a"=hex(5):01')
        patch = patch_file(self.directory, text)
        with self.assertRaises(EmbeddedRegError) as caught:
            applier.apply_patch(patch_data=patch, log=support.LogCapture())
        self.assertEqual(caught.exception.exit_code, 5)
        self.assertEqual(cli.main_applier(["--patch-data", patch, "--quiet"]),
                         5)

    def test_damaged_overlay_exits_with_three(self):
        path = os.path.join(self.directory, "broken.regpatch")
        blob = bytearray(support.patch_bytes(
            support.reg_text("[HKEY_CURRENT_USER\\X]")))
        blob[-9] = 0x05
        with open(path, "wb") as handle:
            handle.write(blob)
        self.assertEqual(cli.main_applier(["--patch-data", path, "--quiet"]),
                         3)


class FakeApplier:
    """Records operations instead of touching the registry."""

    instances = []

    def __init__(self, expander=None, view="default", stop_on_error=False):
        self.applied = []
        self.view = view
        FakeApplier.instances.append(self)

    def apply(self, operations):
        from pyreg2core.reg_types import ApplyReport
        report = ApplyReport()
        for operation in operations:
            self.applied.append(operation)
            report.values_written += 1
        return report


class ElevationTests(support.SandboxMixin, unittest.TestCase):

    def setUp(self):
        self.path = self.make_sandbox()
        self._directory = support.temp_dir()
        self.directory = self._directory.name
        self.addCleanup(self._directory.cleanup)
        FakeApplier.instances = []
        self.text = support.reg_text(
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\__pyreg2exe_tests__\\x]",
            '"a"="b"')

    def test_uac_cancelled_raises_elevation_error(self):
        patch = patch_file(self.directory, self.text, mode=Mode.HKLM)
        with mock.patch.object(applier, "is_admin", return_value=False), \
                mock.patch.object(applier, "run_elevated_and_wait",
                                  side_effect=ElevationError("declined")):
            with self.assertRaises(ElevationError) as caught:
                applier.apply_patch(patch_data=patch,
                                    log=support.LogCapture())
        self.assertEqual(caught.exception.exit_code, 6)

    def test_cli_maps_elevation_error_to_six(self):
        with mock.patch("pyreg2core.cli.apply_patch",
                        side_effect=ElevationError("declined")):
            self.assertEqual(cli.main_applier(["--quiet"]), 6)

    def test_elevation_hands_over_the_patch_data(self):
        patch = patch_file(self.directory, self.text, mode=Mode.HKLM)
        captured = {}

        def fake(arguments, exe_path=None, **kwargs):
            captured["arguments"] = list(arguments)
            captured["exe_path"] = exe_path
            return 0

        with mock.patch.object(applier, "is_admin", return_value=False), \
                mock.patch.object(applier, "run_elevated_and_wait",
                                  side_effect=fake):
            code = applier.apply_patch(patch_data=patch,
                                       patch_dir=r"C:\patch", view="64",
                                       log=support.LogCapture())

        self.assertEqual(code, 0)
        arguments = captured["arguments"]
        self.assertIn("--hklm-only", arguments)
        self.assertIn("--view=64", arguments)
        patch_dir_args = [a for a in arguments if a.startswith("--patch-dir=")]
        self.assertEqual(patch_dir_args, ["--patch-dir=C:\\patch"])
        data_args = [a for a in arguments if a.startswith("--patch-data=")]
        self.assertEqual(len(data_args), 1)
        temporary = data_args[0].split("=", 1)[1]
        self.assertFalse(os.path.exists(temporary),
                         "the temporary patch file must be removed")

    def test_child_exit_code_is_relayed(self):
        patch = patch_file(self.directory, self.text, mode=Mode.HKLM)
        with mock.patch.object(applier, "is_admin", return_value=False), \
                mock.patch.object(applier, "run_elevated_and_wait",
                                  return_value=2):
            code = applier.apply_patch(patch_data=patch,
                                       log=support.LogCapture())
        self.assertEqual(code, 2)

    def test_unexpected_child_code_becomes_two(self):
        patch = patch_file(self.directory, self.text, mode=Mode.HKLM)
        with mock.patch.object(applier, "is_admin", return_value=False), \
                mock.patch.object(applier, "run_elevated_and_wait",
                                  return_value=99):
            code = applier.apply_patch(patch_data=patch,
                                       log=support.LogCapture())
        self.assertEqual(code, 2)

    def test_admin_runs_in_process(self):
        text = support.reg_text("[HKEY_LOCAL_MACHINE\\SOFTWARE\\X]")
        patch = patch_file(self.directory, text, mode=Mode.HKLM)
        with mock.patch.object(applier, "RegistryApplier", FakeApplier), \
                mock.patch.object(applier, "is_admin", return_value=True), \
                mock.patch.object(applier, "run_elevated_and_wait") as elevated:
            code = applier.apply_patch(patch_data=patch,
                                       log=support.LogCapture())
        self.assertEqual(code, 0)
        elevated.assert_not_called()
        self.assertEqual(len(FakeApplier.instances[0].applied), 1)

    def test_hklm_only_applies_admin_operations(self):
        text = support.reg_text(
            "[HKEY_CURRENT_USER\\Software\\X]", '"user"="1"',
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\X]", '"admin"="1"')
        patch = patch_file(self.directory, text, mode=Mode.MIXED)
        with mock.patch.object(applier, "RegistryApplier", FakeApplier), \
                mock.patch.object(applier, "is_admin", return_value=True):
            code = applier.apply_patch(patch_data=patch, hklm_only=True,
                                       log=support.LogCapture())
        self.assertEqual(code, 0)
        applied = FakeApplier.instances[0].applied
        self.assertTrue(all(op.root.requires_admin for op in applied))
        self.assertEqual(len(applied), 2)


if __name__ == "__main__":
    unittest.main()

