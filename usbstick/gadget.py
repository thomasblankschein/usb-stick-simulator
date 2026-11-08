"""USB-Gadget: der Pi gibt sich gegenüber dem Drucker als USB-Stick aus.

``ConfigfsGadget`` steuert den Linux-Kernel (libcomposite/configfs, Mass-Storage-Funktion).
Das Image hängt über ein Loop-Gerät am Gadget; dessen Lesezähler
(``/sys/block/loopN/stat``) zeigt, ob der Drucker gerade liest (Druck läuft).

``FakeGadget`` ersetzt die Hardware in Tests und im Entwicklungsbetrieb.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


class GadgetError(Exception):
    pass


class Gadget:
    """Schnittstelle; siehe ``ConfigfsGadget`` und ``FakeGadget``."""

    kind = "abstract"

    def setup(self, current_image: Path | None) -> bool:
        """Gadget vorbereiten. True, wenn ``current_image`` schon gesteckt ist und übernommen wurde."""
        raise NotImplementedError

    def attach(self, image: Path) -> None:
        """Image einlegen und Stick anstecken."""
        raise NotImplementedError

    def swap(self, image: Path, pause: float) -> None:
        """Abziehen, Image tauschen, nach ``pause`` Sekunden wieder anstecken."""
        raise NotImplementedError

    def read_counter(self) -> int | None:
        """Bisher vom Host gelesene Sektoren (steigt bei jedem Lesezugriff); None = unbekannt."""
        raise NotImplementedError

    def host_connected(self) -> bool | None:
        """Hat der Host (Drucker) den Stick eingebunden? None = unbekannt."""
        raise NotImplementedError

    def attached_image(self) -> Path | None:
        raise NotImplementedError


class FakeGadget(Gadget):
    kind = "fake"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._image: Path | None = None
        self._reads = 0
        self.events: list[tuple[str, str]] = []

    def setup(self, current_image: Path | None) -> bool:
        self.events.append(("setup", str(current_image)))
        return False

    def attach(self, image: Path) -> None:
        if not image.is_file():
            raise GadgetError(f"Image fehlt: {image}")
        with self._lock:
            self._image = image
            self.events.append(("attach", image.name))

    def swap(self, image: Path, pause: float) -> None:
        if not image.is_file():
            raise GadgetError(f"Image fehlt: {image}")
        with self._lock:
            self.events.append(("detach", self._image.name if self._image else ""))
            self._image = None
        time.sleep(pause)
        with self._lock:
            self._image = image
            self.events.append(("attach", image.name))

    def simulate_read(self, sectors: int = 8) -> None:
        """Für Tests: der Drucker liest."""
        with self._lock:
            self._reads += sectors

    def read_counter(self) -> int | None:
        with self._lock:
            return self._reads

    def host_connected(self) -> bool | None:
        return None

    def attached_image(self) -> Path | None:
        with self._lock:
            return self._image


CONFIGFS = Path("/sys/kernel/config/usb_gadget")
UDC_CLASS = Path("/sys/class/udc")
SYS_BLOCK = Path("/sys/block")


class ConfigfsGadget(Gadget):
    kind = "configfs"

    def __init__(
        self,
        name: str = "usbstick",
        readonly: bool = True,
        vendor_id: str = "0x1d6b",
        product_id: str = "0x0104",
        manufacturer: str = "USB-Stick-Simulator",
        product: str = "Virtual USB Stick",
        serial: str = "0123456789AB",
    ) -> None:
        self.root = CONFIGFS / name
        self.function = self.root / "functions" / "mass_storage.usb0"
        self.lun = self.function / "lun.0"
        self.config = self.root / "configs" / "c.1"
        self.readonly = readonly
        self.ids = (vendor_id, product_id)
        self.strings = (manufacturer, product, serial)
        self._loop: str | None = None
        self._image: Path | None = None
        self._lock = threading.Lock()

    # --- Hilfen -------------------------------------------------------------

    @staticmethod
    def _write(path: Path, value: str) -> None:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(value)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="ascii").strip()
        except OSError:
            return ""

    @staticmethod
    def _run(cmd: list[str]) -> str:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if proc.returncode != 0:
            raise GadgetError(f"{' '.join(cmd)}: {proc.stderr.strip() or proc.returncode}")
        return proc.stdout.strip()

    def _udc(self) -> str:
        try:
            names = sorted(os.listdir(UDC_CLASS))
        except OSError:
            names = []
        if not names:
            raise GadgetError(
                "Kein USB-Device-Controller gefunden – steht dtoverlay=dwc2,dr_mode=peripheral unter [all] "
                "in config.txt und wurde der Pi danach neu gestartet? (siehe README, Fehlersuche)"
            )
        return names[0]

    def _bound(self) -> bool:
        return bool(self._read(self.root / "UDC"))

    def _unbind(self) -> None:
        if self._bound():
            self._write(self.root / "UDC", "\n")

    def _bind(self) -> None:
        self._write(self.root / "UDC", self._udc())

    def _loop_attach(self, image: Path) -> str:
        cmd = ["losetup", "--find", "--show"]
        if self.readonly:
            cmd.append("--read-only")
        return self._run([*cmd, str(image)])

    def _loop_detach(self, loop: str | None) -> None:
        if loop:
            try:
                self._run(["losetup", "--detach", loop])
            except GadgetError as exc:
                log.warning("Loop-Gerät %s nicht gelöst: %s", loop, exc)

    def _set_lun_file(self, value: str) -> None:
        self._write(self.lun / "file", value + "\n" if value else "\n")

    # --- Schnittstelle --------------------------------------------------------

    def setup(self, current_image: Path | None) -> bool:
        if not CONFIGFS.is_dir():
            subprocess.run(["modprobe", "libcomposite"], check=False)
        if not CONFIGFS.is_dir():
            raise GadgetError(f"{CONFIGFS} fehlt – Modul libcomposite geladen? configfs gemountet?")

        if self.root.is_dir() and current_image is not None and self._adopt(current_image):
            return True

        if self.root.is_dir():
            # Reste eines früheren Laufs: abziehen und Image auswerfen, Struktur weiterverwenden
            self._unbind()
            old = self._read(self.lun / "file")
            self._set_lun_file("")
            if old.startswith("/dev/loop"):
                self._loop_detach(old)
        else:
            self._create()
        # ro lässt sich nur ohne eingelegtes Image ändern
        self._write(self.lun / "ro", "1" if self.readonly else "0")
        return False

    def _create(self) -> None:
        vendor, product_id = self.ids
        manufacturer, product, serial = self.strings
        self.root.mkdir()
        self._write(self.root / "idVendor", vendor)
        self._write(self.root / "idProduct", product_id)
        self._write(self.root / "bcdDevice", "0x0100")
        self._write(self.root / "bcdUSB", "0x0200")
        strings = self.root / "strings" / "0x409"
        strings.mkdir(parents=True)
        self._write(strings / "manufacturer", manufacturer)
        self._write(strings / "product", product)
        self._write(strings / "serialnumber", serial)
        self.config.mkdir(parents=True)
        self._write(self.config / "MaxPower", "250")
        (self.config / "strings" / "0x409").mkdir(parents=True)
        self._write(self.config / "strings" / "0x409" / "configuration", "Mass Storage")
        self.function.mkdir(parents=True)
        self.lun.mkdir(exist_ok=True)  # legt der Kernel selbst an; nur für Tests ohne configfs nötig
        self._write(self.lun / "removable", "1")
        self._write(self.lun / "cdrom", "0")
        os.symlink(self.function, self.config / self.function.name)

    def _adopt(self, current_image: Path) -> bool:
        """Läuft das Gadget schon mit unserem Image (Dienst-Neustart), nichts abziehen –
        sonst bräche ein laufender Druck ab."""
        loop = self._read(self.lun / "file")
        if not (self._bound() and loop.startswith("/dev/loop")):
            return False
        backing = self._read(SYS_BLOCK / Path(loop).name / "loop" / "backing_file")
        if backing != str(current_image.resolve()):
            return False
        with self._lock:
            self._loop, self._image = loop, current_image
        log.info("Gadget läuft bereits mit %s (%s) – übernommen ohne Abziehen", current_image.name, loop)
        return True

    def attach(self, image: Path) -> None:
        with self._lock:
            loop = self._loop_attach(image)
            try:
                self._set_lun_file(loop)
                self._bind()
            except Exception:
                self._loop_detach(loop)
                raise
            self._loop, self._image = loop, image
        log.info("Stick angesteckt: %s über %s", image.name, loop)

    def swap(self, image: Path, pause: float) -> None:
        with self._lock:
            new_loop = self._loop_attach(image)
            old_loop = self._loop
            try:
                self._unbind()
                self._set_lun_file("")
                self._loop_detach(old_loop)
                self._loop, self._image = None, None
                self._set_lun_file(new_loop)
                time.sleep(pause)  # der Host soll das Abziehen sicher bemerken
                self._bind()
            except Exception:
                self._loop_detach(new_loop)
                raise
            self._loop, self._image = new_loop, image
        log.info("Stick getauscht: %s über %s", image.name, new_loop)

    def read_counter(self) -> int | None:
        loop = self._loop
        if not loop:
            return None
        fields = self._read(SYS_BLOCK / Path(loop).name / "stat").split()
        # Feld 3 = gelesene Sektoren (Documentation/block/stat.rst)
        return int(fields[2]) if len(fields) >= 3 else None

    def host_connected(self) -> bool | None:
        try:
            return self._read(UDC_CLASS / self._udc() / "state") == "configured"
        except GadgetError:
            return None

    def attached_image(self) -> Path | None:
        return self._image
