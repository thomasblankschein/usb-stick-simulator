import io
import tempfile
import unittest
from pathlib import Path

from usbstick.library import InvalidName, Library, UploadIncomplete, sanitize_name


class SanitizeTest(unittest.TestCase):
    def test_examples(self):
        cases = {
            "Teil.pwmb": "Teil.pwmb",
            "Größe Ölwanne.pwmb": "Groesse Oelwanne.pwmb",
            "../../etc/passwd": "passwd",
            "C:\\Users\\x\\Halter v2.pwmb": "Halter v2.pwmb",
            "a:b*c?.pwmb": "a_b_c_.pwmb",
            "  .versteckt.pwmb  ": "versteckt.pwmb",
            "name.": "name",
            "CON.pwmb": "_CON.pwmb",
            "café.pwmb": "cafe.pwmb",
            "Mehrere   Leerzeichen.pwmb": "Mehrere Leerzeichen.pwmb",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_name(raw), expected)

    def test_long_name_keeps_extension(self):
        name = sanitize_name("x" * 300 + ".pwmb")
        self.assertTrue(name.endswith(".pwmb"))
        self.assertLessEqual(len(name), 100)

    def test_empty_rejected(self):
        for raw in ("", "...", "///", "😀"):
            with self.subTest(raw=raw), self.assertRaises(InvalidName):
                sanitize_name(raw)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Library(Path(self.tmp.name) / "files")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_list_delete(self):
        info = self.lib.save("Teil A.pwmb", io.BytesIO(b"x" * 10), 10)
        self.assertEqual((info.name, info.size), ("Teil A.pwmb", 10))
        self.assertEqual([f.name for f in self.lib.list()], ["Teil A.pwmb"])
        self.assertTrue(self.lib.delete("Teil A.pwmb"))
        self.assertFalse(self.lib.delete("Teil A.pwmb"))
        self.assertEqual(self.lib.list(), [])

    def test_casefold_replaces(self):
        self.lib.save("teil.pwmb", io.BytesIO(b"alt"), 3)
        self.lib.save("TEIL.pwmb", io.BytesIO(b"neuer"), 5)
        files = self.lib.list()
        self.assertEqual([(f.name, f.size) for f in files], [("TEIL.pwmb", 5)])

    def test_incomplete_upload_leaves_nothing(self):
        with self.assertRaises(UploadIncomplete):
            self.lib.save("a.pwmb", io.BytesIO(b"abc"), 10)
        self.assertEqual(self.lib.list(), [])
        self.assertEqual(list(self.lib.files_dir.iterdir()), [])

    def test_delete_rejects_paths(self):
        outside = Path(self.tmp.name) / "geheim.txt"
        outside.write_text("x")
        self.assertFalse(self.lib.delete("../geheim.txt"))
        self.assertTrue(outside.exists())

    def test_snapshot_is_stable(self):
        self.lib.save("a.pwmb", io.BytesIO(b"aaa"), 3)
        snap = Path(self.tmp.name) / "snap"
        files = self.lib.snapshot(snap)
        self.lib.delete("a.pwmb")
        self.assertEqual([f.name for f in files], ["a.pwmb"])
        self.assertEqual((snap / "a.pwmb").read_bytes(), b"aaa")


if __name__ == "__main__":
    unittest.main()
