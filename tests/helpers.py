from __future__ import annotations

import logging
import time
import unittest

from usbstick import image as img

# Erwartete Fehler (z. B. „zu groß“) nicht als Traceback in die Testausgabe schreiben
logging.getLogger("usbstick").setLevel(logging.CRITICAL)

needs_mtools = unittest.skipIf(img.required_tools(), "dosfstools/mtools fehlen (in der WSL bzw. auf dem Pi ausführen)")


def wait_for(predicate, timeout: float = 15.0, interval: float = 0.05, message: str = "Bedingung"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"Zeitüberschreitung: {message}")
