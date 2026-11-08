"""Ablage der hochgeladenen Dateien (Quelle für jedes neue Stick-Image).

Die Ablage ist die einzige Wahrheit über den gewünschten Stick-Inhalt. Das Image wird
immer komplett daraus neu gebaut; was das Zielgerät auf den Stick schreiben würde,
geht dabei verloren (deshalb ist der Stick standardmäßig schreibgeschützt).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024
MAX_NAME_LEN = 100  # FAT erlaubt 255; Druckeranzeigen schneiden ohnehin früh ab
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_ALLOWED = re.compile(r"[^A-Za-z0-9 ._\-()+]")
# Unter Windows/DOS reservierte Namen – auch auf FAT problematisch
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class LibraryError(Exception):
    pass


class InvalidName(LibraryError):
    pass


class UploadIncomplete(LibraryError):
    pass


@dataclass(frozen=True)
class FileInfo:
    name: str
    size: int
    mtime: float

    def as_dict(self) -> dict:
        return {"name": self.name, "size": self.size, "mtime": self.mtime}


def sanitize_name(name: str) -> str:
    """Macht aus einem beliebigen Dateinamen einen sicheren, reinen ASCII-FAT-Namen.

    Pfadanteile werden verworfen, Umlaute umschrieben, alles andere Unerlaubte wird zu ``_``.
    """
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.translate(_UMLAUTS)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = _ALLOWED.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")  # FAT verträgt keine Punkte/Leerzeichen am Ende
    if not name or set(name) <= {"_", "."}:
        raise InvalidName("Dateiname ist nach der Bereinigung leer")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    if len(ext) > 10:  # keine echte Endung
        stem, ext = name, ""
    ext_part = f".{ext}" if ext else ""
    stem = stem[: MAX_NAME_LEN - len(ext_part)].rstrip(" .") or "_"
    return stem + ext_part


class Library:
    def __init__(self, files_dir: Path):
        self.files_dir = files_dir
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cleanup_partials()

    def _cleanup_partials(self) -> None:
        for path in self.files_dir.glob(".upload-*.part"):
            path.unlink(missing_ok=True)

    def list(self) -> list[FileInfo]:
        result = []
        for entry in os.scandir(self.files_dir):
            if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
            result.append(FileInfo(entry.name, st.st_size, st.st_mtime))
        result.sort(key=lambda f: f.name.lower())
        return result

    def total_size(self) -> int:
        return sum(f.size for f in self.list())

    def _find_casefold(self, name: str) -> list[Path]:
        folded = name.casefold()
        return [self.files_dir / f.name for f in self.list() if f.name.casefold() == folded]

    def save(self, name: str, stream: BinaryIO, length: int) -> FileInfo:
        """Schreibt genau ``length`` Bytes aus ``stream`` unter dem bereinigten Namen.

        Erst in eine versteckte ``.part``-Datei, dann atomar umbenennen. Eine gleichnamige
        Datei (auch in anderer Groß-/Kleinschreibung – FAT unterscheidet das nicht) wird ersetzt.
        """
        safe = sanitize_name(name)
        part = self.files_dir / f".upload-{secrets.token_hex(8)}.part"
        try:
            remaining = length
            with open(part, "wb") as out:
                while remaining > 0:
                    chunk = stream.read(min(CHUNK, remaining))
                    if not chunk:
                        raise UploadIncomplete(f"Upload abgebrochen: {length - remaining} von {length} Bytes empfangen")
                    out.write(chunk)
                    remaining -= len(chunk)
                out.flush()
                os.fsync(out.fileno())
            with self._lock:
                for old in self._find_casefold(safe):
                    if old.name != safe:
                        old.unlink(missing_ok=True)
                os.replace(part, self.files_dir / safe)
        finally:
            part.unlink(missing_ok=True)
        st = (self.files_dir / safe).stat()
        return FileInfo(safe, st.st_size, st.st_mtime)

    def delete(self, name: str) -> bool:
        if "/" in name or "\\" in name or name.startswith(".") or not name:
            return False
        with self._lock:
            path = self.files_dir / name
            if not path.is_file():
                return False
            path.unlink()
            return True

    def snapshot(self, target: Path) -> list[FileInfo]:
        """Friert den aktuellen Stand per Hardlinks in ``target`` ein (kostet keinen Platz).

        So kann ein Image-Bau minutenlang laufen, während weiter hochgeladen/gelöscht wird.
        Fällt auf Kopieren zurück, wenn das Dateisystem keine Hardlinks kann.
        """
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with self._lock:
            files = self.list()
            for f in files:
                src = self.files_dir / f.name
                try:
                    os.link(src, target / f.name)
                except OSError:
                    shutil.copy2(src, target / f.name)
        return files
