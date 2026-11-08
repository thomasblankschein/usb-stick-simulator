"""FAT32-Image für den Stick bauen (dosfstools + mtools, ohne mount und ohne root)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .library import FileInfo

log = logging.getLogger(__name__)

MIB = 1024 * 1024
# Reserve für FAT-Tabellen, Verzeichnisse und Cluster-Verschnitt
OVERHEAD_FRACTION = 0.03
CLUSTER_SLACK = 32 * 1024


class ImageError(Exception):
    pass


def required_tools() -> list[str]:
    return [tool for tool in ("mkfs.vfat", "mcopy", "mdir") if _which(tool) is None]


def _which(tool: str) -> str | None:
    # mkfs.vfat liegt in /usr/sbin, das für normale Nutzer oft nicht im PATH ist
    return shutil.which(tool) or shutil.which(tool, path="/usr/sbin:/sbin")


def _resolve(cmd: list[str]) -> list[str]:
    return [_which(cmd[0]) or cmd[0], *cmd[1:]]


def capacity_bytes(size_mb: int) -> int:
    """Grobe Nutzkapazität eines Images (konservativ geschätzt)."""
    return int(size_mb * MIB * (1 - OVERHEAD_FRACTION))


def needed_bytes(files: list[FileInfo]) -> int:
    return sum(f.size + CLUSTER_SLACK for f in files)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Image ohne Partitionstabelle: mtools soll nicht über die Geometrie meckern
    env["MTOOLS_SKIP_CHECK"] = "1"
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def _run(cmd: list[str]) -> str:
    log.debug("run %s", cmd[:4])
    proc = subprocess.run(_resolve(cmd), stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise ImageError(f"{cmd[0]} fehlgeschlagen: {detail[-1] if detail else proc.returncode}")
    return proc.stdout


def build_image(target: Path, source_dir: Path, files: list[FileInfo], size_mb: int, label: str) -> None:
    """Baut ``target`` neu aus ``files`` in ``source_dir``.

    Erst in ``<target>.tmp``, geprüft, dann atomar umbenannt. Das Image ist eine
    Sparse-Datei: belegt wird nur, was wirklich darin steht.
    """
    missing = required_tools()
    if missing:
        raise ImageError(f"Werkzeuge fehlen: {', '.join(missing)} (Pakete dosfstools und mtools)")
    need = needed_bytes(files)
    cap = capacity_bytes(size_mb)
    if need > cap:
        raise ImageError(f"Dateien zu groß für den Stick: {need // MIB} MB nötig, {cap // MIB} MB frei")

    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    free = shutil.disk_usage(target.parent).free
    if free < need + 64 * MIB:
        raise ImageError(f"Zu wenig Platz auf der SD-Karte: {need // MIB} MB nötig, {free // MIB} MB frei")
    try:
        with open(tmp, "wb") as fh:
            fh.truncate(size_mb * MIB)
        _run(["mkfs.vfat", "-F", "32", "-n", label, str(tmp)])
        if files:
            # -D o: bei Namenskonflikt überschreiben statt interaktiv nachzufragen
            # -m: Änderungszeit übernehmen; -Q: beim ersten Fehler abbrechen
            _run(["mcopy", "-i", str(tmp), "-D", "o", "-m", "-Q", *[str(source_dir / f.name) for f in files], "::/"])
        listed = list_image(tmp)
        expected = {f.name: f.size for f in files}
        if listed != expected:
            raise ImageError(f"Image-Prüfung fehlgeschlagen: erwartet {sorted(expected)}, gefunden {sorted(listed)}")
        with open(tmp, "rb+") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


# Zeile aus `mdir -a`, z. B.
#   "A        TXT         3 2026-11-08  16:29 "
#   "TEILNR~1 PWM    123456 2026-11-08  16:29  Teil Nr 1 (final).pwmb"
# Kurzname (8) + Endung (3), Größe, Datum, Uhrzeit, optional der lange Name.
# Kurznamen beginnen nie mit Leerzeichen – so fallen Summenzeilen wie "  60 934 144 bytes free" heraus.
_MDIR_LINE = re.compile(r"^(?P<short>\S.{7})[ ](?P<ext>.{3}) +(?P<size>\d+) +\S+ +\S+ ?(?: (?P<long>.+))?$")


def parse_mdir(output: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in output.splitlines():
        m = _MDIR_LINE.match(line)
        if not m:
            continue  # Kopfzeilen, Summen, <DIR>-Einträge
        name = (m.group("long") or "").rstrip()
        if not name:
            short, ext = m.group("short").strip(), m.group("ext").strip()
            name = f"{short}.{ext}" if ext else short
        result[name] = int(m.group("size"))
    return result


def list_image(image: Path) -> dict[str, int]:
    """Dateien im Wurzelverzeichnis des Images: Name → Größe."""
    return parse_mdir(_run(["mdir", "-i", str(image), "-a", "::/"]))


def write_manifest(image: Path, files: list[FileInfo]) -> None:
    manifest = image.with_name(image.name + ".json")
    tmp = manifest.with_name(manifest.name + ".tmp")
    tmp.write_text(json.dumps([f.as_dict() for f in files], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, manifest)


def read_manifest(image: Path) -> list[FileInfo] | None:
    manifest = image.with_name(image.name + ".json")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return [FileInfo(d["name"], int(d["size"]), float(d["mtime"])) for d in data]
    except (OSError, ValueError, KeyError, TypeError):
        return None
