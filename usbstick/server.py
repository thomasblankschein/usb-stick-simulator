"""HTTP-Server (nur Standardbibliothek): Web-Oberfläche und JSON-API.

  GET    /                 Web-Oberfläche
  GET    /health           "ok"
  GET    /api/status       Zustand (siehe Manager.status)
  GET    /api/files        Dateien in der Ablage
  PUT    /api/files/<name> Datei hochladen (Rohdaten im Body, Content-Length Pflicht)
  DELETE /api/files/<name> Datei löschen
  POST   /api/apply        sofort übernehmen; ?force=1 auch wenn der Drucker gerade liest
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import shutil
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from . import image as img
from .config import Config
from .library import InvalidName, Library, UploadIncomplete, sanitize_name
from .manager import Manager

log = logging.getLogger(__name__)


def _index_html() -> bytes:
    return resources.files("usbstick").joinpath("web/index.html").read_bytes()


def make_handler(cfg: Config, library: Library, manager: Manager) -> type[BaseHTTPRequestHandler]:
    expected_auth = None
    if cfg.auth_enabled:
        token = base64.b64encode(f"{cfg.web_user}:{cfg.web_password}".encode()).decode()
        expected_auth = f"Basic {token}"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"usb-stick-simulator/{__version__}"

        def log_message(self, fmt: str, *args) -> None:  # noqa: D401 – stdlib-Signatur
            log.debug("%s %s", self.address_string(), fmt % args)

        # --- Antworten --------------------------------------------------------

        def _send(self, status: int, body: bytes = b"", content_type: str = "application/json; charset=utf-8",
                  headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            if body or status not in (HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED):
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _authorized(self) -> bool:
            if expected_auth is None:
                return True
            got = self.headers.get("Authorization", "")
            if hmac.compare_digest(got.encode(), expected_auth.encode()):
                return True
            self.close_connection = True
            self._send(HTTPStatus.UNAUTHORIZED, b'{"error":"Anmeldung erforderlich"}',
                       headers={"WWW-Authenticate": 'Basic realm="USB-Stick-Simulator", charset="UTF-8"'})
            return False

        def _file_name(self, path: str) -> str | None:
            prefix = "/api/files/"
            if not path.startswith(prefix):
                return None
            return unquote(path[len(prefix):])

        # --- Methoden ---------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(HTTPStatus.OK, b"ok", "text/plain; charset=utf-8")
                return
            if not self._authorized():
                return
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, _index_html(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self._json(HTTPStatus.OK, {"version": __version__, **manager.status()})
            elif path == "/api/files":
                self._json(HTTPStatus.OK, {"files": [f.as_dict() for f in library.list()]})
            else:
                self._error(HTTPStatus.NOT_FOUND, "Nicht gefunden")

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_PUT(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            name = self._file_name(urlsplit(self.path).path)
            if not name:
                self.close_connection = True
                self._error(HTTPStatus.NOT_FOUND, "Nicht gefunden")
                return
            raw_length = self.headers.get("Content-Length")
            if raw_length is None or self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length fehlt")
                return
            try:
                length = int(raw_length)
                if length < 0:
                    raise ValueError
            except ValueError:
                self.close_connection = True
                self._error(HTTPStatus.BAD_REQUEST, "Content-Length ungültig")
                return

            # Passt die Datei auf den Stick (eine gleichnamige wird ersetzt)?
            files = library.list()
            try:
                safe_name = sanitize_name(name)
            except InvalidName as exc:
                self.close_connection = True
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            others = [f for f in files if f.name.casefold() != safe_name.casefold()]
            need = img.needed_bytes(others) + length + img.CLUSTER_SLACK
            if need > img.capacity_bytes(cfg.image_size_mb):
                self.close_connection = True
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Datei passt nicht mehr auf den Stick")
                return
            # Ablage + altes und neues Image müssen auf die SD-Karte passen
            if shutil.disk_usage(library.files_dir).free < length + img.needed_bytes(others) + 128 * img.MIB:
                self.close_connection = True
                self._error(HTTPStatus.INSUFFICIENT_STORAGE, "Zu wenig Platz auf der SD-Karte")
                return

            try:
                info = library.save(name, self.rfile, length)
            except UploadIncomplete as exc:
                self.close_connection = True
                log.warning("%s", exc)
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            log.info("Hochgeladen: %s (%d Bytes)", info.name, info.size)
            manager.notify_change()
            self._json(HTTPStatus.CREATED, info.as_dict())

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            name = self._file_name(urlsplit(self.path).path)
            if not name or not library.delete(name):
                self._error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden")
                return
            log.info("Gelöscht: %s", name)
            manager.notify_change()
            self._send(HTTPStatus.NO_CONTENT)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parts = urlsplit(self.path)
            # Body (falls vorhanden) verwerfen, damit die Verbindung sauber bleibt
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 65536))
            if parts.path != "/api/apply":
                self._error(HTTPStatus.NOT_FOUND, "Nicht gefunden")
                return
            force = parse_qs(parts.query).get("force", ["0"])[0] in ("1", "true")
            log.info("Übernahme angefordert%s", " (erzwungen)" if force else "")
            manager.request_apply(force=force)
            self._json(HTTPStatus.ACCEPTED, {"ok": True, "force": force})

    return Handler


def make_server(cfg: Config, library: Library, manager: Manager) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg, library, manager))
    server.daemon_threads = True
    return server
