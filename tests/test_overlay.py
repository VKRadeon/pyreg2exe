# -*- coding: utf-8 -*-
"""
The overlay codec: layout, detection and corruption handling (B2, B19, §4.4).
"""

import os
import unittest

import support

from pyreg2core import overlay
from pyreg2core.errors import InputError, OverlayError
from pyreg2core.reg_types import Mode


class EncodeTests(unittest.TestCase):

    def test_layout(self):
        blob = overlay.encode_blob(b"abc", Mode.HKCU)
        self.assertEqual(blob[:3], b"abc")
        self.assertEqual(blob[3:11], (3).to_bytes(8, "little"))
        self.assertEqual(blob[11], 0x02)
        self.assertEqual(blob[12:], b"PYREGEND")
        self.assertEqual(len(blob), 3 + overlay.TAIL_LEN)

    def test_empty_payload_is_rejected(self):
        with self.assertRaises(OverlayError):
            overlay.encode_blob(b"", Mode.HKCU)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(OverlayError):
            overlay.encode_blob(b"abc", 9)

    def test_mode_accepts_int(self):
        blob = overlay.encode_blob(b"abc", 3)
        self.assertEqual(overlay.parse_tail(blob).mode, Mode.MIXED)


class ParseTests(unittest.TestCase):

    def test_roundtrip(self):
        blob = support.fake_pe() + overlay.encode_blob(b"payload",
                                                       Mode.MIXED)
        recovered = overlay.parse_tail(blob)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.data, b"payload")
        self.assertEqual(recovered.mode, Mode.MIXED)
        self.assertEqual(recovered.size, 7)
        self.assertEqual(recovered.offset, len(support.fake_pe()))

    def test_plain_image_has_no_overlay(self):
        self.assertIsNone(overlay.parse_tail(support.fake_pe()))

    def test_empty_blob(self):
        self.assertIsNone(overlay.parse_tail(b""))
        self.assertIsNone(overlay.parse_tail(None))

    def test_blob_shorter_than_the_tail(self):
        self.assertIsNone(overlay.parse_tail(b"PYREG"))

    def test_unknown_mode_is_corruption(self):
        blob = bytearray(overlay.encode_blob(b"abc", Mode.HKCU))
        blob[-overlay.MODE_LEN - len(overlay.MARKER)] = 0x07
        with self.assertRaises(OverlayError) as caught:
            overlay.parse_tail(bytes(blob))
        self.assertIn("unsupported overlay mode", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, 3)

    def test_zero_size_is_corruption(self):
        broken = bytearray(overlay.encode_blob(b"abc", Mode.HKCU))
        start = len(broken) - overlay.TAIL_LEN
        broken[start:start + overlay.SIZE_LEN] = (0).to_bytes(8, "little")
        with self.assertRaises(OverlayError) as caught:
            overlay.parse_tail(bytes(broken))
        self.assertIn("empty payload", str(caught.exception))

    def test_truncated_patch_is_corruption(self):
        blob = overlay.encode_blob(b"0123456789", Mode.HKCU)
        truncated = blob[4:]
        with self.assertRaises(OverlayError) as caught:
            overlay.parse_tail(truncated)
        self.assertIn("truncated", str(caught.exception))

    def test_has_overlay_data(self):
        self.assertFalse(overlay.has_overlay_data(b""))
        self.assertFalse(overlay.has_overlay_data(support.fake_pe()))
        self.assertTrue(overlay.has_overlay_data(
            overlay.encode_blob(b"x", Mode.HKLM)))


class FileTests(unittest.TestCase):

    def test_has_overlay_and_read_file(self):
        with support.temp_dir() as directory:
            plain = os.path.join(directory, "plain.exe")
            with open(plain, "wb") as handle:
                handle.write(support.fake_pe())
            self.assertFalse(overlay.has_overlay(plain))
            self.assertIsNone(overlay.read_file(plain))

            patch = os.path.join(directory, "patch.exe")
            with open(patch, "wb") as handle:
                handle.write(support.fake_pe()
                             + overlay.encode_blob(b"data", Mode.HKCU))
            self.assertTrue(overlay.has_overlay(patch))
            recovered = overlay.read_file(patch)
            self.assertEqual(recovered.data, b"data")
            self.assertEqual(recovered.mode, Mode.HKCU)

    def test_missing_file_is_not_an_error(self):
        self.assertIsNone(overlay.read_file("no-such-file.exe"))
        self.assertFalse(overlay.has_overlay("no-such-file.exe"))

    def test_write_to_roundtrip(self):
        with support.temp_dir() as directory:
            path = os.path.join(directory, "p.regpatch")
            written = overlay.Overlay(data=b"abc", mode=Mode.HKLM)
            written.write_to(path)
            self.assertEqual(os.path.getsize(path),
                             written.size + overlay.TAIL_LEN)
            recovered = overlay.read_file(path)
            self.assertEqual(recovered.data, b"abc")
            self.assertEqual(recovered.mode, Mode.HKLM)


class TemplateValidationTests(unittest.TestCase):

    def test_accepts_a_pe_image(self):
        overlay.validate_pe_image(support.fake_pe())

    def test_rejects_missing_mz(self):
        blob = bytearray(support.fake_pe())
        blob[0:2] = b"ZM"
        with self.assertRaises(InputError) as caught:
            overlay.validate_pe_image(bytes(blob))
        self.assertIn("MZ", str(caught.exception))

    def test_rejects_missing_pe_signature(self):
        blob = bytearray(support.fake_pe())
        blob[0x80:0x84] = b"XX\0\0"
        with self.assertRaises(InputError) as caught:
            overlay.validate_pe_image(bytes(blob))
        self.assertIn("PE image", str(caught.exception))

    def test_rejects_short_file(self):
        with self.assertRaises(InputError):
            overlay.validate_pe_image(b"MZ")


if __name__ == "__main__":
    unittest.main()
