"""Konfiguration ausschließlich aus Umgebungsvariablen (systemd: EnvironmentFile).

Werte werden erst beim Aufruf von ``load()`` gelesen, nie beim Import.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def _str(env: dict[str, str], key: str, default: str) -> str:
    value = env.get(key, "").strip()
    return value if value else default


def _int(env: dict[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} muss eine ganze Zahl sein, ist aber {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} muss mindestens {minimum} sein, ist aber {value}")
    return value


def _float(env: dict[str, str], key: str, default: float, minimum: float = 0.0) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} muss eine Zahl sein, ist aber {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} muss mindestens {minimum} sein, ist aber {value}")
    return value


def _bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "ja", "on"):
        return True
    if raw in ("0", "false", "no", "nein", "off"):
        return False
    raise ConfigError(f"{key} muss true oder false sein, ist aber {raw!r}")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    image_size_mb: int
    label: str
    gadget: str  # "configfs" oder "fake"
    readonly: bool
    apply_delay: float  # Sekunden nach der letzten Änderung; 0 = nur per Knopf
    idle_seconds: float  # so lange darf der Drucker nichts gelesen haben, bevor getauscht wird
    replug_pause: float  # Pause zwischen Abziehen und Anstecken
    host: str
    port: int
    web_user: str
    web_password: str
    log_level: str
    usb_vendor_id: str
    usb_product_id: str
    usb_manufacturer: str
    usb_product: str
    usb_serial: str

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.web_user or self.web_password)


_LABEL_RE = re.compile(r"^[A-Z0-9_\- ]{1,11}$")
_HEX_ID_RE = re.compile(r"^0x[0-9a-fA-F]{4}$")


def load(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)

    label = _str(env, "USBSTICK_LABEL", "PHOTON").upper()
    if not _LABEL_RE.match(label):
        raise ConfigError("USBSTICK_LABEL: 1–11 Zeichen, nur A–Z, 0–9, Leerzeichen, _ und -")

    gadget = _str(env, "USBSTICK_GADGET", "configfs").lower()
    if gadget not in ("configfs", "fake"):
        raise ConfigError("USBSTICK_GADGET muss configfs oder fake sein")

    vendor = _str(env, "USBSTICK_USB_VENDOR_ID", "0x1d6b")
    product = _str(env, "USBSTICK_USB_PRODUCT_ID", "0x0104")
    for key, value in (("USBSTICK_USB_VENDOR_ID", vendor), ("USBSTICK_USB_PRODUCT_ID", product)):
        if not _HEX_ID_RE.match(value):
            raise ConfigError(f"{key} muss die Form 0x1234 haben")

    web_user = env.get("USBSTICK_WEB_USER", "")
    web_password = env.get("USBSTICK_WEB_PASSWORD", "")
    if bool(web_user) != bool(web_password):
        raise ConfigError("USBSTICK_WEB_USER und USBSTICK_WEB_PASSWORD nur gemeinsam setzen")

    return Config(
        data_dir=Path(_str(env, "USBSTICK_DATA_DIR", "/var/lib/usb-stick-simulator")),
        # FAT32 braucht mindestens 65525 Cluster; unter ~64 MB wird mkfs.vfat unzuverlässig.
        image_size_mb=_int(env, "USBSTICK_IMAGE_SIZE_MB", 4096, minimum=64),
        label=label,
        gadget=gadget,
        readonly=_bool(env, "USBSTICK_READONLY", True),
        apply_delay=_float(env, "USBSTICK_APPLY_DELAY", 10.0),
        idle_seconds=_float(env, "USBSTICK_IDLE_SECONDS", 60.0),
        replug_pause=_float(env, "USBSTICK_REPLUG_PAUSE", 1.5),
        host=_str(env, "USBSTICK_HOST", "0.0.0.0"),
        port=_int(env, "USBSTICK_PORT", 80, minimum=1),
        web_user=web_user,
        web_password=web_password,
        log_level=_str(env, "LOG_LEVEL", "info").upper(),
        usb_vendor_id=vendor,
        usb_product_id=product,
        usb_manufacturer=_str(env, "USBSTICK_USB_MANUFACTURER", "USB-Stick-Simulator"),
        usb_product=_str(env, "USBSTICK_USB_PRODUCT", "Virtual USB Stick"),
        usb_serial=_str(env, "USBSTICK_USB_SERIAL", "0123456789AB"),
    )
