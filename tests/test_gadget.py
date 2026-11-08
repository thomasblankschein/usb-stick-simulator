"""ConfigfsGadget gegen einen nachgebauten configfs-/sysfs-Baum (ohne echte USB-Hardware).

Prüft Aufbau, Schreibreihenfolge und Übernahme nach Neustart – NICHT, ob der Kernel das
genauso akzeptiert. Das zeigt erst der Pi.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from usbstick import gadget as gadget_mod
from usbstick.gadget import ConfigfsGadget


class FakeSys:
    def __init__(self, root: Path):
        self.configfs = root / "config" / "usb_gadget"
        self.udc = root / "class" / "udc"
        self.block = root / "block"
        self.configfs.mkdir(parents=True)
        (self.udc / "20980000.usb").mkdir(parents=True)
        (self.udc / "20980000.usb" / "state").write_text("configured\n")
        self.block.mkdir()
        self.loops: dict[str, str] = {}
        self.detached: list[str] = []
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str]) -> str:
        self.calls.append(cmd)
        if cmd[:3] == ["losetup", "--find", "--show"]:
            dev = f"/dev/loop{len(self.loops)}"
            self.loops[dev] = cmd[-1]
            d = self.block / Path(dev).name
            (d / "loop").mkdir(parents=True)
            (d / "loop" / "backing_file").write_text(str(Path(cmd[-1]).resolve()) + "\n")
            (d / "stat").write_text("   10 0 80 0 0 0 0 0 0 0 0\n")
            return dev
        if cmd[:2] == ["losetup", "--detach"]:
            self.detached.append(cmd[2])
            return ""
        raise AssertionError(f"unerwarteter Befehl {cmd}")

    def patch(self):
        return mock.patch.multiple(gadget_mod, CONFIGFS=self.configfs, UDC_CLASS=self.udc, SYS_BLOCK=self.block)


class ConfigfsGadgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sys = FakeSys(root / "sys")
        self.img_a = root / "stick-a.img"
        self.img_b = root / "stick-b.img"
        self.img_a.write_bytes(b"a")
        self.img_b.write_bytes(b"b")
        p = self.sys.patch()
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, **kw):
        g = ConfigfsGadget(**kw)
        g._run = self.sys.run
        return g

    def read(self, *parts):
        return (self.sys.configfs / "usbstick").joinpath(*parts).read_text().strip()

    def test_setup_attach_swap(self):
        g = self.make()
        self.assertFalse(g.setup(None))
        self.assertEqual(self.read("idVendor"), "0x1d6b")
        self.assertEqual(self.read("functions", "mass_storage.usb0", "lun.0", "ro"), "1")
        self.assertEqual(self.read("functions", "mass_storage.usb0", "lun.0", "removable"), "1")
        link = self.sys.configfs / "usbstick" / "configs" / "c.1" / "mass_storage.usb0"
        self.assertTrue(link.is_symlink())

        g.attach(self.img_a)
        self.assertEqual(self.read("UDC"), "20980000.usb")
        self.assertEqual(self.read("functions", "mass_storage.usb0", "lun.0", "file"), "/dev/loop0")
        self.assertIn("--read-only", self.sys.calls[0])
        self.assertEqual(g.read_counter(), 80)
        self.assertTrue(g.host_connected())

        g.swap(self.img_b, pause=0)
        self.assertEqual(self.read("functions", "mass_storage.usb0", "lun.0", "file"), "/dev/loop1")
        self.assertEqual(self.read("UDC"), "20980000.usb")
        self.assertEqual(self.sys.detached, ["/dev/loop0"])
        self.assertEqual(g.attached_image(), self.img_b)

    def test_writable_mode(self):
        g = self.make(readonly=False)
        g.setup(None)
        g.attach(self.img_a)
        self.assertEqual(self.read("functions", "mass_storage.usb0", "lun.0", "ro"), "0")
        self.assertNotIn("--read-only", self.sys.calls[0])

    def test_restart_adopts_running_gadget(self):
        g = self.make()
        g.setup(None)
        g.attach(self.img_a)
        # Dienst-Neustart: neues Objekt, Gadget läuft noch mit stick-a.img
        g2 = self.make()
        self.assertTrue(g2.setup(self.img_a))
        self.assertEqual(g2.attached_image(), self.img_a)
        self.assertEqual(self.sys.detached, [], "darf nicht abziehen")
        self.assertEqual(g2.read_counter(), 80)

    def test_restart_with_other_image_resets(self):
        g = self.make()
        g.setup(None)
        g.attach(self.img_a)
        g2 = self.make()
        self.assertFalse(g2.setup(self.img_b))
        self.assertEqual(self.read("UDC"), "")
        self.assertEqual(self.sys.detached, ["/dev/loop0"])

    def test_missing_udc_message(self):
        (self.sys.udc / "20980000.usb" / "state").unlink()
        (self.sys.udc / "20980000.usb").rmdir()
        g = self.make()
        g.setup(None)
        with self.assertRaisesRegex(gadget_mod.GadgetError, "dwc2"):
            g.attach(self.img_a)
        self.assertEqual(self.sys.detached, ["/dev/loop0"], "Loop-Gerät nach Fehler wieder lösen")


if __name__ == "__main__":
    unittest.main()
