import base64
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from usbstick import config as config_mod
from usbstick import image as img
from usbstick.gadget import FakeGadget
from usbstick.library import Library
from usbstick.manager import Manager
from usbstick.server import make_server

from helpers import needs_mtools, wait_for


@needs_mtools
class ServerTest(unittest.TestCase):
    env_extra: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {
            "USBSTICK_DATA_DIR": self.tmp.name,
            "USBSTICK_GADGET": "fake",
            "USBSTICK_HOST": "127.0.0.1",
            "USBSTICK_PORT": "8080",
            "USBSTICK_IMAGE_SIZE_MB": "64",
            "USBSTICK_APPLY_DELAY": "0.2",
            "USBSTICK_IDLE_SECONDS": "0.3",
            "USBSTICK_REPLUG_PAUSE": "0",
            **self.env_extra,
        }
        self.cfg = config_mod.load(env)
        self.gadget = FakeGadget()
        self.lib = Library(self.cfg.files_dir)
        self.manager = Manager(self.lib, self.gadget, self.cfg.images_dir, self.cfg.image_size_mb, self.cfg.label,
                               self.cfg.apply_delay, self.cfg.idle_seconds, self.cfg.replug_pause, poll_interval=0.02)
        self.manager.start()
        # Port 0: freien Port vom System nehmen
        self.cfg = self.cfg.__class__(**{**self.cfg.__dict__, "port": 0})
        self.server = make_server(self.cfg, self.lib, self.manager)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.manager.stop()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            res = conn.getresponse()
            data = res.read()
            return res.status, res.getheader("Content-Type", ""), data
        finally:
            conn.close()

    def json(self, method, path, **kw):
        status, _, data = self.request(method, path, **kw)
        return status, (json.loads(data) if data else None)


class ApiTest(ServerTest):
    def test_index_and_health(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"USB-Stick-Simulator", body)
        self.assertEqual(self.request("GET", "/health")[2], b"ok")

    def test_upload_list_apply_delete(self):
        payload = b"\x00\x01PWMB" * 1000
        status, info = self.json("PUT", "/api/files/" + "Gr%C3%B6%C3%9Fe%20Teil.pwmb", body=payload)
        self.assertEqual(status, 201)
        self.assertEqual(info["name"], "Groesse Teil.pwmb")
        self.assertEqual(info["size"], len(payload))

        status, data = self.json("GET", "/api/files")
        self.assertEqual([f["name"] for f in data["files"]], ["Groesse Teil.pwmb"])

        wait_for(lambda: self.gadget.attached_image()
                 and set(img.list_image(self.gadget.attached_image())) == {"Groesse Teil.pwmb"},
                 message="Datei auf dem Stick")
        st = wait_for(lambda: (s := self.json("GET", "/api/status")[1])["phase"] == "idle" and s)
        self.assertFalse(st["pending"])
        self.assertEqual([f["name"] for f in st["stickFiles"]], ["Groesse Teil.pwmb"])

        status, _ = self.json("DELETE", "/api/files/Groesse%20Teil.pwmb")
        self.assertEqual(status, 204)
        self.assertEqual(self.json("DELETE", "/api/files/Groesse%20Teil.pwmb")[0], 404)
        wait_for(lambda: img.list_image(self.gadget.attached_image()) == {}, message="Stick leer")

    def test_apply_endpoint(self):
        status, data = self.json("POST", "/api/apply?force=1")
        self.assertEqual(status, 202)
        self.assertTrue(data["force"])

    def test_upload_requires_length(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("PUT", "/api/files/a.pwmb")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.endheaders()
        conn.send(b"0\r\n\r\n")
        res = conn.getresponse()
        self.assertEqual(res.status, 411)
        conn.close()

    def test_upload_too_big(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("PUT", "/api/files/gross.pwmb")
        conn.putheader("Content-Length", str(100 * 1024 * 1024))
        conn.endheaders()  # Body wird gar nicht erst geschickt
        res = conn.getresponse()
        self.assertEqual(res.status, 413)
        conn.close()
        self.assertEqual(self.lib.list(), [])

    def test_invalid_name(self):
        self.assertEqual(self.json("PUT", "/api/files/...", body=b"x")[0], 400)

    def test_unknown_path(self):
        self.assertEqual(self.json("GET", "/api/nope")[0], 404)


class AuthTest(ServerTest):
    env_extra = {"USBSTICK_WEB_USER": "thomas", "USBSTICK_WEB_PASSWORD": "geheim"}

    def test_requires_auth(self):
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertEqual(self.request("GET", "/health")[0], 200, "Health ohne Anmeldung")
        bad = base64.b64encode(b"thomas:falsch").decode()
        self.assertEqual(self.request("GET", "/", headers={"Authorization": f"Basic {bad}"})[0], 401)
        good = base64.b64encode(b"thomas:geheim").decode()
        self.assertEqual(self.request("GET", "/api/status", headers={"Authorization": f"Basic {good}"})[0], 200)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config_mod.load({})
        self.assertEqual(cfg.gadget, "configfs")
        self.assertTrue(cfg.readonly)
        self.assertEqual(cfg.label, "PHOTON")
        self.assertEqual(cfg.files_dir, Path("/var/lib/usb-stick-simulator/files"))

    def test_invalid(self):
        for env in ({"USBSTICK_LABEL": "VIEL_ZU_LANGER_NAME"}, {"USBSTICK_GADGET": "usb"},
                    {"USBSTICK_IMAGE_SIZE_MB": "10"}, {"USBSTICK_WEB_USER": "nur-name"},
                    {"USBSTICK_READONLY": "vielleicht"}):
            with self.subTest(env=env), self.assertRaises(config_mod.ConfigError):
                config_mod.load(env)


del ServerTest  # Basisklasse nicht selbst ausführen

if __name__ == "__main__":
    unittest.main()
