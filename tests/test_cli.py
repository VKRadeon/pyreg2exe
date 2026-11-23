# -*- coding: utf-8 -*-
"""
Command-line behaviour and exit codes (§6.5, §6.9).
"""

import contextlib
import io
import os
import unittest
from unittest import mock

import support

from pyreg2core import applier, cli


def run(main, argv):
    """Call an entry point, capturing its console output and exit code."""
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), \
            contextlib.redirect_stderr(stream):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stream.getvalue()


class BuilderCliTests(unittest.TestCase):

    def setUp(self):
        self._directory = support.temp_dir()
        self.directory = self._directory.name
        self.addCleanup(self._directory.cleanup)
        self.template = os.path.join(self.directory, "regapply.exe")
        with open(self.template, "wb") as handle:
            handle.write(support.fake_pe())
        self.reg = os.path.join(self.directory, "patch.reg")
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"a"="b"'))

    def test_help(self):
        code, output = run(cli.main_builder, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("--lenient", output)
        self.assertIn("Exit codes", output)

    def test_version(self):
        code, output = run(cli.main_builder, ["--version"])
        self.assertEqual(code, 0)
        self.assertIn("pyreg2exe 1.1.0", output)

    def test_no_arguments(self):
        code, _ = run(cli.main_builder, [])
        self.assertEqual(code, 2)

    def test_unknown_option(self):
        code, _ = run(cli.main_builder, ["--nonsense"])
        self.assertEqual(code, 2)

    def test_successful_build(self):
        code, output = run(cli.main_builder,
                           [self.reg, "-t", self.template])
        self.assertEqual(code, 0)
        self.assertIn("wrote", output)
        self.assertTrue(os.path.isfile(
            os.path.join(self.directory, "patch.exe")))

    def test_dry_run(self):
        code, output = run(cli.main_builder,
                           [self.reg, "-t", self.template, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("dry run", output)

    def test_invalid_reg_exit_three(self):
        support.write_reg_file(self.reg, support.reg_text(
            "[HKCU\\X]", '"a"=hex(5):01'))
        code, output = run(cli.main_builder,
                           [self.reg, "-t", self.template])
        self.assertEqual(code, 3)
        self.assertIn("invalid .reg", output)

    def test_existing_output_exit_four(self):
        output_path = os.path.join(self.directory, "out.exe")
        with open(output_path, "wb") as handle:
            handle.write(b"old")
        code, output = run(cli.main_builder,
                           [self.reg, "-t", self.template, "-o", output_path])
        self.assertEqual(code, 4)
        self.assertIn("--force", output)

    def test_missing_template_exit_three(self):
        code, _ = run(cli.main_builder,
                      [self.reg, "-t", os.path.join(self.directory, "no.exe")])
        self.assertEqual(code, 3)

    def test_quiet_success(self):
        code, output = run(cli.main_builder,
                           [self.reg, "-t", self.template, "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(output, "")


class ApplierCliTests(unittest.TestCase):

    def test_help(self):
        code, output = run(cli.main_applier, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("--view", output)

    def test_version(self):
        code, output = run(cli.main_applier, ["--version"])
        self.assertEqual(code, 0)
        self.assertIn("regapply 1.1.0", output)

    def test_invalid_view(self):
        code, _ = run(cli.main_applier, ["--view", "128"])
        self.assertEqual(code, 2)

    def test_plain_template_returns_zero(self):
        with mock.patch.object(applier.selfpath, "existing_candidates",
                               return_value=[]):
            code, _ = run(cli.main_applier, ["--quiet"])
        self.assertEqual(code, 0)

    def test_missing_patch_data(self):
        code, _ = run(cli.main_applier,
                      ["--patch-data", "no-such-file.regpatch", "--quiet"])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
