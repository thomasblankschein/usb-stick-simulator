"""Einstieg: ``python3 -m usbstick`` (Konfiguration über Umgebungsvariablen, siehe README)."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from . import __version__
from . import config as config_mod
from . import image as img
from .gadget import ConfigfsGadget, FakeGadget, Gadget
from .library import Library
from .manager import Manager
from .server import make_server


def main() -> int:
    try:
        cfg = config_mod.load()
    except config_mod.ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("usbstick")
    log.info("USB-Stick-Simulator %s startet (Gadget: %s, Daten: %s)", __version__, cfg.gadget, cfg.data_dir)

    missing = img.required_tools()
    if missing:
        log.error("Werkzeuge fehlen: %s – bitte dosfstools und mtools installieren", ", ".join(missing))
        return 1

    gadget: Gadget
    if cfg.gadget == "fake":
        gadget = FakeGadget()
    else:
        gadget = ConfigfsGadget(
            readonly=cfg.readonly,
            vendor_id=cfg.usb_vendor_id,
            product_id=cfg.usb_product_id,
            manufacturer=cfg.usb_manufacturer,
            product=cfg.usb_product,
            serial=cfg.usb_serial,
        )

    library = Library(cfg.files_dir)
    manager = Manager(
        library=library,
        gadget=gadget,
        images_dir=cfg.images_dir,
        image_size_mb=cfg.image_size_mb,
        label=cfg.label,
        apply_delay=cfg.apply_delay,
        idle_seconds=cfg.idle_seconds,
        replug_pause=cfg.replug_pause,
    )
    manager.start()

    server = make_server(cfg, library, manager)
    log.info("Web-Oberfläche auf http://%s:%d/%s", cfg.host, cfg.port, " (mit Anmeldung)" if cfg.auth_enabled else "")

    def shutdown(signum, _frame) -> None:
        log.info("Signal %s – beende (Stick bleibt gesteckt)", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        manager.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
