# -*- coding: utf-8 -*-
"""
Locating our own executable (B3, §6.2).

The interesting failure modes cannot be reproduced in a source run, but the
invariants can: every candidate is absolute and unique, the running image is
an existing file, and a source run reports no ``__compiled__`` metadata.
"""

import os
import unittest

import support

from pyreg2core import selfpath


class SelfPathTests(unittest.TestCase):

    def test_running_image_exists(self):
        path = selfpath.running_image_path()
        self.assertTrue(os.path.isabs(path), path)
        self.assertTrue(os.path.isfile(path), path)

    def test_candidates_are_absolute_and_unique(self):
        paths = selfpath.candidate_paths()
        self.assertTrue(paths)
        self.assertTrue(all(os.path.isabs(path) for path in paths), paths)
        keys = [os.path.normcase(path) for path in paths]
        self.assertEqual(len(keys), len(set(keys)), paths)

    def test_existing_candidates_are_files(self):
        for path in selfpath.existing_candidates():
            self.assertTrue(os.path.isfile(path), path)

    def test_own_directory_is_a_directory(self):
        self.assertTrue(os.path.isdir(selfpath.own_directory()))

    def test_compiled_metadata_is_a_string(self):
        self.assertIsInstance(selfpath.compiled_argv0(), str)

    def test_is_compiled_is_boolean(self):
        self.assertIsInstance(selfpath.is_compiled(), bool)


class TemplateDiscoveryTests(unittest.TestCase):

    def test_candidates_start_with_the_explicit_path(self):
        explicit = os.path.join("C:\\", "somewhere", "regapply.exe")
        candidates = selfpath_candidates(explicit)
        self.assertEqual(candidates[0], os.path.abspath(explicit))

    def test_candidates_include_the_repository_root(self):
        expected = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(selfpath.__file__))),
            "regapply.exe")
        self.assertIn(expected, selfpath_candidates())

    def test_find_template_rejects_a_missing_explicit_path(self):
        from pyreg2core import builder
        from pyreg2core.errors import InputError

        with support.temp_dir() as directory:
            missing = os.path.join(directory, "absent.exe")
            with self.assertRaises(InputError) as caught:
                builder.find_template(missing)
        self.assertIn("template not found", str(caught.exception))


def selfpath_candidates(explicit=None):
    from pyreg2core import builder
    return builder.template_candidates(explicit)


if __name__ == "__main__":
    unittest.main()
