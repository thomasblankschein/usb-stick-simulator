import io
import os
import tempfile
import unittest
from pathlib import Path

from usbstick import image as img
from usbstick.library import Library

from helpers import needs_mtools

MDIR_SAMPLE = """\
 Volume in drive : is PHOTON
 Volume Serial Number is 5692-1EAD
Directory for ::/

A        TXT         3 2026-11-08  16:29
TEILNR~1 PWM    123456 2026-11-08  16:29  Teil Nr 1 (final).pwmb
BIG_FI~1 PWM   5000000 2026-11-08  16:29  big_file_name_long.pwmb
NOEXT              7 2026-11-08  16:29
SUB          <DIR>     2026-11-08  16:29
        3 files           5 123 459 bytes
                         60 934 144 bytes free
"""

MDIR_EMPTY = """\
 Volume in drive : is PHOTON
 Volume Serial Number is 5692-1EAD
Directory for ::/

No files
                         66 970 112 bytes free
"""


class ParseTest(unittest.TestCase):
    def test_parse_mdir(self):
        self.assertEqual(
            img.parse_mdir(MDIR_SAMPLE),
            {"A.TXT": 3, "Teil Nr 1 (final).pwmb": 123456, "big_file_name_long.pwmb": 5000000, "NOEXT": 7},
        )
        self.assertEqual(img.parse_mdir(MDIR_EMPTY), {})


@needs_mtools
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.lib = Library(root / "files")
        self.images = root / "images"
        self.images.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_roundtrip(self):
        payload = os.urandom(300_000)
        self.lib.save("Halter v2 (final).pwmb", io.BytesIO(payload), len(payload))
        self.lib.save("a.pwmb", io.BytesIO(b"x"), 1)
        target = self.images / "stick-a.img"
        img.build_image(target, self.lib.files_dir, self.lib.list(), 64, "PHOTON")
        self.assertTrue(target.is_file())
        self.assertFalse(target.with_name(target.name + ".tmp").exists())
        self.assertEqual(img.list_image(target), {"Halter v2 (final).pwmb": 300_000, "a.pwmb": 1})
        # Inhalt byte-genau zurücklesen
        out = Path(self.tmp.name) / "back.bin"
        img._run(["mcopy", "-i", str(target), "::/Halter v2 (final).pwmb", str(out)])
        self.assertEqual(out.read_bytes(), payload)
        # Sparse: belegt deutlich weniger als die nominellen 64 MB (falls das Dateisystem das kann)
        if hasattr(os.stat_result, "st_blocks"):
            self.assertLess(target.stat().st_blocks * 512, 32 * img.MIB)

    def test_empty_image(self):
        target = self.images / "stick-a.img"
        img.build_image(target, self.lib.files_dir, [], 64, "PHOTON")
        self.assertEqual(img.list_image(target), {})

    def test_too_big(self):
        self.lib.save("a.pwmb", io.BytesIO(b"x"), 1)
        files = [img.FileInfo("a.pwmb", 100 * img.MIB, 0)]
        with self.assertRaises(img.ImageError):
            img.build_image(self.images / "stick-a.img", self.lib.files_dir, files, 64, "PHOTON")

    def test_manifest(self):
        files = [img.FileInfo("a.pwmb", 1, 123.0)]
        target = self.images / "stick-a.img"
        img.write_manifest(target, files)
        self.assertEqual(img.read_manifest(target), files)
        self.assertIsNone(img.read_manifest(self.images / "fehlt.img"))


if __name__ == "__main__":
    unittest.main()
