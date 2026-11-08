"""Ablaufsteuerung: Änderungen sammeln → neues Image bauen → auf Leerlauf warten → tauschen.

Ein einziger Hintergrund-Thread erledigt alles nacheinander; Web-Anfragen melden nur
Änderungen (``notify_change``) oder fordern die Übernahme an (``request_apply``).

Phasen (``status()["phase"]``):
  idle        Stick aktuell, nichts zu tun
  pending     Änderungen da, automatische Übernahme läuft nach Ablauf der Wartezeit
  manual      Änderungen da, automatische Übernahme abgeschaltet (Knopf drücken)
  building    neues Image wird gebaut (alter Stick bleibt solange gesteckt)
  waiting     Image fertig, Drucker liest noch (Druck läuft?) – Tausch wartet
  swapping    Stick wird kurz abgezogen und mit neuem Image wieder angesteckt
  error       letzter Versuch fehlgeschlagen (``lastError``); nächste Änderung versucht es erneut
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import image as img
from .gadget import Gadget
from .library import FileInfo, Library

log = logging.getLogger(__name__)

IMAGE_NAMES = ("stick-a.img", "stick-b.img")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _same(a: list[FileInfo] | None, b: list[FileInfo]) -> bool:
    if a is None:
        return False
    return sorted((f.name, f.size, int(f.mtime)) for f in a) == sorted((f.name, f.size, int(f.mtime)) for f in b)


class Manager:
    def __init__(
        self,
        library: Library,
        gadget: Gadget,
        images_dir: Path,
        image_size_mb: int,
        label: str,
        apply_delay: float,
        idle_seconds: float,
        replug_pause: float,
        poll_interval: float = 1.0,
        clock=time.monotonic,
    ) -> None:
        self.library = library
        self.gadget = gadget
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.image_size_mb = image_size_mb
        self.label = label
        self.apply_delay = apply_delay
        self.idle_seconds = idle_seconds
        self.replug_pause = replug_pause
        self.poll_interval = poll_interval
        self.clock = clock

        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None

        self._generation = 0  # steigt bei jeder Änderung an der Ablage
        self._applied_generation = 0
        self._last_change = 0.0
        self._apply_requested = False
        self._force = False
        self._phase = "idle"
        self._last_error: str | None = None
        self._last_apply: str | None = None
        self._stick_files: list[FileInfo] = []
        self._current: Path | None = None

        self._last_counter: int | None = None
        self._last_read_at: float | None = None

    # --- Zustand --------------------------------------------------------------

    def _current_pointer(self) -> Path:
        return self.images_dir / "current"

    def _load_current(self) -> Path | None:
        try:
            name = self._current_pointer().read_text(encoding="utf-8").strip()
        except OSError:
            return None
        path = self.images_dir / name
        return path if name in IMAGE_NAMES and path.is_file() else None

    def _save_current(self, image: Path) -> None:
        tmp = self._current_pointer().with_suffix(".tmp")
        tmp.write_text(image.name, encoding="utf-8")
        tmp.replace(self._current_pointer())

    def _next_image(self) -> Path:
        current = self._current.name if self._current else None
        return self.images_dir / next(n for n in IMAGE_NAMES if n != current)

    # --- Start/Stopp ------------------------------------------------------------

    def start(self) -> None:
        current = self._load_current()
        adopted = self.gadget.setup(current)
        if current is not None:
            manifest = img.read_manifest(current) or []
            if not adopted:
                self.gadget.attach(current)
            self._current = current
            self._stick_files = manifest
            self._mark_read()  # konservativ: direkt nach dem Anstecken liest der Host ohnehin
            if not _same(manifest, self.library.list()):
                self._generation = 1  # Ablage weicht vom Stick ab → übernehmen
        else:
            # Erster Start: sofort ein Image bauen, damit überhaupt ein Stick erscheint
            self._generation = 1
            self._apply_requested = True
            self._force = True
        self._last_change = self.clock() - self.apply_delay
        self._thread = threading.Thread(target=self._run, name="manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Beendet den Thread. Der Stick bleibt bewusst gesteckt (laufender Druck!)."""
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=10)

    # --- Anstöße von außen --------------------------------------------------------

    def notify_change(self) -> None:
        with self._cond:
            self._generation += 1
            self._last_change = self.clock()
            self._cond.notify_all()

    def request_apply(self, force: bool = False) -> None:
        """Sofort übernehmen (Knopf). Baut auch ohne Änderung neu – dient so als „Erneut versuchen“."""
        with self._cond:
            if self._generation == self._applied_generation:
                self._generation += 1
            self._apply_requested = True
            self._force = self._force or force
            self._cond.notify_all()

    # --- Hintergrund-Thread -----------------------------------------------------------

    def _mark_read(self) -> None:
        self._last_read_at = self.clock()

    def _poll_activity(self) -> None:
        counter = self.gadget.read_counter()
        if counter is not None and self._last_counter is not None and counter != self._last_counter:
            if self._last_read_at is not None:
                log.debug("Lesezugriff: %d Sektoren, %.1f s nach dem letzten",
                          counter - self._last_counter, self.clock() - self._last_read_at)
            self._mark_read()
        self._last_counter = counter

    def _idle_for(self) -> float | None:
        if self._last_read_at is None:
            return None
        return self.clock() - self._last_read_at

    def _printer_idle(self) -> bool:
        idle = self._idle_for()
        return idle is None or idle >= self.idle_seconds

    def _due(self) -> bool:
        if self._generation == self._applied_generation:
            return False
        if self._apply_requested:
            return True
        return self.apply_delay > 0 and self.clock() - self._last_change >= self.apply_delay

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
                self._poll_activity()
                if not self._due():
                    if self._generation == self._applied_generation:
                        self._apply_requested = False
                        self._force = False
                        if self._phase != "error":
                            self._phase = "idle"
                    elif self._phase != "error":
                        self._phase = "pending" if self.apply_delay > 0 else "manual"
                    self._cond.wait(self.poll_interval)
                    continue
                generation = self._generation
                self._phase = "building"
            try:
                self._apply(generation)
            except Exception as exc:  # noqa: BLE001 – Fehler landen im Status, Thread läuft weiter
                log.exception("Übernahme fehlgeschlagen")
                with self._cond:
                    self._phase = "error"
                    self._last_error = str(exc)
                    self._apply_requested = False
                    self._force = False
                    # erst die nächste Änderung (oder der Knopf) versucht es erneut
                    self._applied_generation = generation

    def _apply(self, generation: int) -> None:
        snapshot_dir = self.images_dir / ".snapshot"
        files = self.library.snapshot(snapshot_dir)
        target = self._next_image()
        try:
            log.info("Baue %s mit %d Datei(en)", target.name, len(files))
            started = time.monotonic()
            img.build_image(target, snapshot_dir, files, self.image_size_mb, self.label)
            img.write_manifest(target, files)
            log.info("Image fertig nach %.1f s", time.monotonic() - started)
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)

        # Auf Leerlauf warten – außer bei erzwungener Übernahme oder wenn noch gar kein Stick steckt
        while True:
            with self._cond:
                if self._stop:
                    return
                self._poll_activity()
                if self._generation != generation:
                    # Während Bau/Warten neue Änderungen: Image veraltet, später neu bauen
                    log.info("Ablage hat sich geändert – Image wird neu gebaut")
                    target.unlink(missing_ok=True)
                    target.with_name(target.name + ".json").unlink(missing_ok=True)
                    self._phase = "pending"
                    return
                if self._force or self._current is None or self._printer_idle():
                    self._phase = "swapping"
                    break
                self._phase = "waiting"
                self._cond.wait(self.poll_interval)

        old = self._current
        if old is None:
            self.gadget.attach(target)
        else:
            self.gadget.swap(target, self.replug_pause)
        self._save_current(target)
        with self._cond:
            self._current = target
            self._stick_files = files
            self._applied_generation = generation
            self._apply_requested = False
            self._force = False
            self._last_error = None
            self._last_apply = _now_iso()
            self._last_counter = None  # neues Loop-Gerät, neuer Zähler
            self._mark_read()
            self._phase = "idle"
        if old is not None and old != target:
            old.unlink(missing_ok=True)
            old.with_name(old.name + ".json").unlink(missing_ok=True)
        log.info("Stick aktualisiert: %s", target.name)

    # --- Status für die Web-Oberfläche ---------------------------------------------------

    def status(self) -> dict:
        with self._cond:
            idle = self._idle_for()
            apply_in = None
            if self._phase == "pending" and self.apply_delay > 0:
                apply_in = max(0.0, self.apply_delay - (self.clock() - self._last_change))
            files = self.library.list()
            return {
                "phase": self._phase,
                "pending": self._generation != self._applied_generation or not _same(self._stick_files, files),
                "applyIn": None if apply_in is None else round(apply_in, 1),
                "autoApply": self.apply_delay > 0,
                "gadget": self.gadget.kind,
                "attached": self.gadget.attached_image() is not None,
                "hostConnected": self.gadget.host_connected(),
                "lastReadAgo": None if idle is None else round(idle, 1),
                "idleSeconds": self.idle_seconds,
                "printerBusy": not self._printer_idle(),
                "stickFiles": [f.as_dict() for f in self._stick_files],
                "lastApply": self._last_apply,
                "lastError": self._last_error,
                "capacity": img.capacity_bytes(self.image_size_mb),
                "used": img.needed_bytes(files),
            }
