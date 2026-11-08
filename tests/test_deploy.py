"""deploy/enable-dwc2.sh gegen typische config.txt-Varianten."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "enable-dwc2.sh"
LINE = "dtoverlay=dwc2,dr_mode=peripheral"

# Ende einer aktuellen Raspberry-Pi-OS-config.txt (Bookworm/Trixie)
CURRENT_IMAGE = """\
# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on
camera_auto_detect=1
display_auto_detect=1
auto_initramfs=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1

[cm4]
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""


@unittest.skipIf(shutil.which("sh") is None or shutil.which("awk") is None, "sh/awk fehlen")
class EnableDwc2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self) -> str:
        proc = subprocess.run(["sh", str(SCRIPT), str(self.config)], capture_output=True, text=True, check=True)
        return proc.stdout

    def active_lines(self) -> list[tuple[str, str]]:
        """(Abschnitt, Zeile) für alle dwc2-Zeilen."""
        section, result = "", []
        for line in self.config.read_text().splitlines():
            if line.startswith("["):
                section = line.strip()
            elif "dwc2" in line and not line.lstrip().startswith("#"):
                result.append((section, line.strip()))
        return result

    def test_cm5_host_line_is_not_mistaken(self):
        self.config.write_text(CURRENT_IMAGE)
        out = self.run_script()
        self.assertIn("ergänzt", out)
        self.assertIn(("[all]", LINE), self.active_lines())
        self.assertTrue((self.config.parent / "config.txt.bak-usbstick").exists())

    def test_idempotent(self):
        self.config.write_text(CURRENT_IMAGE)
        self.run_script()
        before = self.config.read_text()
        self.assertIn("bereits", self.run_script())
        self.assertEqual(self.config.read_text(), before)

    def test_line_under_board_filter_does_not_count(self):
        self.config.write_text(f"[pi4]\n{LINE}\n")
        self.assertIn("ergänzt", self.run_script())

    def test_line_without_any_section_counts(self):
        self.config.write_text(f"{LINE}\ndtparam=audio=on\n")
        self.assertIn("bereits", self.run_script())

    def test_commented_line_does_not_count(self):
        self.config.write_text(f"[all]\n#{LINE}\n")
        self.assertIn("ergänzt", self.run_script())


if __name__ == "__main__":
    unittest.main()
