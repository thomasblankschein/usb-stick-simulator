import io
import tempfile
import time
import unittest
from pathlib import Path

from usbstick import image as img
from usbstick.gadget import FakeGadget
from usbstick.library import Library
from usbstick.manager import Manager

from helpers import needs_mtools, wait_for


@needs_mtools
class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = Library(self.root / "files")
        self.gadget = FakeGadget()
        self.managers = []

    def tearDown(self):
        for m in self.managers:
            m.stop()
        self.tmp.cleanup()

    def make(self, **kw):
        args = dict(
            library=self.lib, gadget=self.gadget, images_dir=self.root / "images", image_size_mb=64,
            label="PHOTON", apply_delay=0.2, idle_seconds=0.5, replug_pause=0.01, poll_interval=0.02,
        )
        args.update(kw)
        m = Manager(**args)
        self.managers.append(m)
        return m

    def stick_names(self):
        image = self.gadget.attached_image()
        return set(img.list_image(image)) if image else None

    def upload(self, m, name, data=b"data"):
        self.lib.save(name, io.BytesIO(data), len(data))
        m.notify_change()

    def test_first_start_attaches_empty_stick(self):
        m = self.make()
        m.start()
        wait_for(lambda: self.gadget.attached_image() is not None, message="Stick angesteckt")
        self.assertEqual(self.stick_names(), set())
        wait_for(lambda: m.status()["phase"] == "idle", message="idle")

    def test_upload_is_applied_automatically(self):
        m = self.make()
        m.start()
        wait_for(lambda: m.status()["phase"] == "idle")
        self.upload(m, "a.pwmb")
        self.assertIn(m.status()["phase"], ("pending", "building", "swapping", "idle"))
        wait_for(lambda: self.stick_names() == {"a.pwmb"}, message="Datei auf dem Stick")
        st = wait_for(lambda: (s := m.status())["phase"] == "idle" and s, message="idle")
        self.assertFalse(st["pending"])
        self.assertIn(("detach", "stick-a.img"), self.gadget.events)
        self.assertEqual(self.gadget.attached_image().name, "stick-b.img")
        self.assertFalse((self.root / "images" / "stick-a.img").exists(), "altes Image wird gelöscht")

    def test_waits_while_printer_reads_then_force(self):
        m = self.make(idle_seconds=1.0)
        m.start()
        wait_for(lambda: m.status()["phase"] == "idle")
        self.upload(m, "a.pwmb")
        # Drucker liest dauernd → Übernahme muss warten
        deadline = time.monotonic() + 1.5
        saw_waiting = False
        while time.monotonic() < deadline:
            self.gadget.simulate_read()
            saw_waiting = saw_waiting or m.status()["phase"] == "waiting"
            time.sleep(0.05)
        self.assertTrue(saw_waiting, "Phase waiting erwartet")
        self.assertEqual(self.stick_names(), set(), "darf während des Lesens nicht tauschen")
        self.assertTrue(m.status()["printerBusy"])
        m.request_apply(force=True)
        wait_for(lambda: self.stick_names() == {"a.pwmb"}, message="erzwungene Übernahme")

    def test_waits_then_applies_when_printer_idle(self):
        m = self.make(idle_seconds=0.4)
        m.start()
        wait_for(lambda: m.status()["phase"] == "idle")
        self.upload(m, "a.pwmb")
        for _ in range(10):
            self.gadget.simulate_read()
            time.sleep(0.05)
        self.assertEqual(self.stick_names(), set())
        wait_for(lambda: self.stick_names() == {"a.pwmb"}, message="Übernahme nach Leerlauf")

    def test_manual_mode(self):
        m = self.make(apply_delay=0)
        m.start()
        wait_for(lambda: m.status()["phase"] == "idle")
        self.upload(m, "a.pwmb")
        time.sleep(0.3)
        self.assertEqual(m.status()["phase"], "manual")
        self.assertEqual(self.stick_names(), set())
        m.request_apply()
        wait_for(lambda: self.stick_names() == {"a.pwmb"}, message="manuelle Übernahme")

    def test_restart_reuses_image_and_detects_changes(self):
        m = self.make()
        m.start()
        self.upload(m, "a.pwmb")
        wait_for(lambda: self.stick_names() == {"a.pwmb"} and m.status()["phase"] == "idle")
        m.stop()

        # Neustart ohne Änderung: altes Image sofort wieder anstecken, nichts neu bauen
        self.gadget = FakeGadget()
        m2 = self.make(gadget=self.gadget)
        m2.start()
        self.assertEqual(self.stick_names(), {"a.pwmb"})
        time.sleep(0.4)
        self.assertEqual([e[0] for e in self.gadget.events], ["setup", "attach"])
        m2.stop()

        # Änderung, während der Dienst aus war → nach Start übernommen
        self.lib.save("b.pwmb", io.BytesIO(b"b"), 1)
        self.gadget = FakeGadget()
        m3 = self.make(gadget=self.gadget)
        m3.start()
        wait_for(lambda: self.stick_names() == {"a.pwmb", "b.pwmb"}, message="Änderung nach Neustart")

    def test_error_is_reported_and_retry_works(self):
        m = self.make(image_size_mb=64)
        m.start()
        wait_for(lambda: m.status()["phase"] == "idle")
        big = b"x" * (70 * 1024 * 1024)  # passt nicht in 64 MB
        self.lib.save("zu_gross.pwmb", io.BytesIO(big), len(big))
        m.notify_change()
        st = wait_for(lambda: (s := m.status())["phase"] == "error" and s, message="Fehlerstatus")
        self.assertIn("zu groß", st["lastError"])
        self.assertTrue(st["pending"])
        self.lib.delete("zu_gross.pwmb")
        m.request_apply()
        wait_for(lambda: m.status()["phase"] == "idle" and m.status()["lastError"] is None, message="Erholung")


if __name__ == "__main__":
    unittest.main()
