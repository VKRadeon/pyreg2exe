# -*- coding: utf-8 -*-
"""
Build pipeline: validation, mode byte, atomic write, verification (§6.6).
"""

import hashlib
import os
import unittest

import support

from pyreg2core import builder, overlay
from pyreg2core.errors import BuildError, InputError
from pyreg2core.reg_types import Mode

GOOD_REG = support.reg_text("[HKCU\\Software\\Test]", '"a"="b"')


class _BuilderBase(unittest.TestCase):

    def setUp(self):
        self._directory = support.temp_dir()
        self.directory = self._directory.name
        self.template = os.path.join(self.directory, "regapply.exe")
        with open(self.template, "wb") as handle:
            handle.write(support.fake_pe())
        self.reg = os.path.join(self.directory, "patch.reg")
        support.write_reg_file(self.reg, GOOD_REG)
        self.log = support.quiet_log()

    def tearDown(self):
        self._directory.cleanup()

    def build(self, **kwargs):
        kwargs.setdefault("template_path", self.template)
        kwargs.setdefault("log", self.log)
        return builder.build_patch(self.reg, **kwargs)

class BuilderTests(_BuilderBase):

    def test_builds_a_patch_with_a_valid_overlay(self):
        plan = self.build()
        self.assertTrue(os.path.isfile(plan.output_path))
        self.assertEqual(plan.output_path,
                         os.path.join(self.directory, "patch.exe"))
        self.assertEqual(plan.mode, Mode.HKCU)
        self.assertEqual(plan.operation_count, 2)

        with open(self.reg, "rb") as handle:
            expected = handle.read()
        with open(plan.output_path, "rb") as handle:
            blob = handle.read()
        self.assertEqual(blob[:len(support.fake_pe())], support.fake_pe())
        recovered = overlay.parse_tail(blob)
        self.assertEqual(recovered.data, expected)
        self.assertEqual(recovered.mode, Mode.HKCU)
        self.assertEqual(plan.output_size, len(blob))

    def test_no_temporary_file_is_left_behind(self):
        self.build()
        leftovers = [name for name in os.listdir(self.directory)
                     if ".tmp-" in name]
        self.assertEqual(leftovers, [])

    def test_digest_matches_the_input(self):
        plan = self.build()
        with open(self.reg, "rb") as handle:
            expected = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(plan.digest, expected)

    def test_explicit_output_path(self):
        target = os.path.join(self.directory, "custom.exe")
        plan = self.build(output_path=target)
        self.assertEqual(plan.output_path, target)
        self.assertTrue(os.path.isfile(target))

    def test_mixed_mode(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\A]", "[HKLM\\B]"))
        plan = self.build()
        self.assertEqual(plan.mode, Mode.MIXED)
        recovered = overlay.read_file(plan.output_path)
        self.assertEqual(recovered.mode, Mode.MIXED)

    def test_dry_run_writes_nothing(self):
        plan = self.build(dry_run=True)
        self.assertTrue(plan.dry_run)
        self.assertFalse(os.path.exists(plan.output_path))
        self.assertEqual(plan.operation_count, 2)

    def test_existing_output_requires_force(self):
        self.build()
        with self.assertRaises(BuildError) as caught:
            self.build()
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertIn("already exists", str(caught.exception))
        self.build(force=True)

    def test_output_equal_to_input_is_rejected(self):
        with self.assertRaises(BuildError) as caught:
            self.build(output_path=self.reg, force=True)
        self.assertIn("input .reg", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 4)

    def test_output_equal_to_template_is_rejected(self):
        with self.assertRaises(BuildError) as caught:
            self.build(output_path=self.template, force=True)
        self.assertIn("template", str(caught.exception))

    def test_unwritable_output_directory(self):
        target = os.path.join(self.directory, "missing-dir", "out.exe")
        with self.assertRaises(BuildError) as caught:
            self.build(output_path=target)
        self.assertEqual(caught.exception.exit_code, 4)


class InputValidationTests(_BuilderBase):

    def test_missing_reg_file(self):
        self.reg = os.path.join(self.directory, "nope.reg")
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertIn("file not found", str(caught.exception))

    def test_wrong_extension(self):
        path = os.path.join(self.directory, "patch.txt")
        support.write_reg_file(path, GOOD_REG)
        self.reg = path
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertIn("not a .reg file", str(caught.exception))

    def test_missing_template(self):
        self.template = os.path.join(self.directory, "absent.exe")
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertIn("template not found", str(caught.exception))

    def test_template_without_pe_signature(self):
        with open(self.template, "wb") as handle:
            handle.write(b"MZ" + b"\x00" * 200)
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertIn("PE image", str(caught.exception))

    def test_template_that_is_already_patched(self):
        with open(self.template, "ab") as handle:
            handle.write(overlay.encode_blob(b"old", Mode.HKCU))
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertIn("already contains", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 3)

    def test_invalid_reg_reports_the_line(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"a"=hex(5):01'))
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(caught.exception.line, 4)
        self.assertIn("invalid .reg", str(caught.exception))

    def test_empty_reg(self):
        support.write_reg_file(self.reg, support.reg_text())
        with self.assertRaises(InputError) as caught:
            self.build()
        self.assertIn("no operations", str(caught.exception))

    def test_lenient_mode_builds_legacy_files(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"Path"=%CD%\\dir'))
        with self.assertRaises(InputError):
            self.build()
        plan = self.build(lenient=True)
        self.assertEqual(plan.operation_count, 2)
        self.assertEqual(len(plan.feedback), 1)

    def test_cp1251_requires_the_option(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"Имя"="Значение"'), encoding="cp1251", bom=False)
        with self.assertRaises(InputError):
            self.build()
        plan = self.build(encoding_name="cp1251")
        self.assertTrue(os.path.isfile(plan.output_path))

    def test_utf8_with_bom(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"a"="b"'), encoding="utf-8")
        plan = self.build()
        self.assertTrue(os.path.isfile(plan.output_path))


class HelperTests(unittest.TestCase):

    def test_default_output_path(self):
        self.assertEqual(builder.default_output_path(r"C:\a\patch.reg"),
                         r"C:\a\patch.exe")

    def test_default_output_path_keeps_dots(self):
        self.assertEqual(builder.default_output_path("my.patch.v2.reg"),
                         os.path.abspath("my.patch.v2.exe"))


if __name__ == "__main__":
    unittest.main()

