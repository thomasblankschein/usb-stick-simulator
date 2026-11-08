# CLAUDE.md

**usb-stick-simulator** — Raspberry Pi Zero W gibt sich per USB-Gadget (configfs, `dwc2` + `libcomposite`, Mass Storage) gegenüber dem Resin-Drucker Anycubic Photon M3 als USB-Stick aus; die Druckdateien (`.pm3`) kommen über eine Web-Oberfläche. Python 3 (nur Standardbibliothek, läuft ab 3.11 / Raspberry Pi OS Bookworm), dazu `dosfstools` + `mtools`. Doku auf Deutsch, siehe README.md.

- `usbstick/library.py` — Ablage `files/` = einzige Wahrheit über den Stick-Inhalt. Upload atomar über versteckte `.upload-*.part`, `sanitize_name` (ASCII, FAT-sicher, max. 100 Zeichen), Groß-/Kleinschreibungs-Duplikate werden ersetzt (FAT unterscheidet nicht), `snapshot` per Hardlinks für den Image-Bau
- `usbstick/image.py` — FAT32-Image: Sparse-Datei → `mkfs.vfat -F 32` → `mcopy -D o -m -Q` → Prüfung per `mdir` (`parse_mdir`) → atomar umbenennen; Manifest `<image>.json`. Nie mounten (braucht root, und mtools ist ohne Hardware testbar)
- `usbstick/gadget.py` — `ConfigfsGadget`: Image hängt über `losetup` am Gadget, `read_counter` = Feld 3 von `/sys/block/loopN/stat`; `swap` = UDC lösen → Loop tauschen → Pause → binden; `setup` übernimmt ein schon laufendes Gadget mit dem aktuellen Image ohne Abziehen. `FakeGadget` für Tests/Entwicklung
- `usbstick/manager.py` — ein Hintergrund-Thread: Generation zählt Änderungen; fällig nach `apply_delay` oder Knopf → Image `stick-a.img`/`stick-b.img` im Wechsel bauen → warten bis `idle_seconds` ohne Lesezugriff (außer `force` oder noch kein Stick) → tauschen → altes Image löschen. Zeiger auf das aktuelle Image in `images/current`
- `usbstick/server.py` — `http.server` (HTTP/1.1): `/`, `/health`, `/api/status`, `/api/files` (GET, PUT `<name>` mit Rohdaten, DELETE), `/api/apply[?force=1]`; optional HTTP Basic. Bei API-Änderungen README (API-Abschnitt) mitpflegen
- `usbstick/web/index.html` — Oberfläche (Upload per XHR wegen Fortschrittsanzeige, Polling alle 2 s)
- `deploy/` — `install.sh` (Pakete, `dtoverlay=dwc2,dr_mode=peripheral`, Module, `/opt/usb-stick-simulator`, `/etc/usb-stick-simulator.env`, systemd), Unit, Beispielkonfiguration
- Tests: `python3 -m unittest discover -s tests` in der WSL (mtools/dosfstools dort installiert). Kein Test-Framework. `test_gadget.py` prüft configfs nur gegen einen nachgebauten Baum

**Designregeln**
- **Nie Dateien in ein gestecktes Image schreiben.** Der Host cached das FAT; Änderungen unter ihm → Datenmüll/kaputtes Dateisystem. Immer neues Image bauen und per Abziehen/Anstecken tauschen.
- **Druckschutz nicht aufweichen:** Abziehen während des Drucks bricht den Druck ab. Getauscht wird nur nach `idle_seconds` ohne Lesezugriff oder ausdrücklich erzwungen. Nach jedem Anstecken gilt der Drucker konservativ als „gerade aktiv“. Ein Dienst-(Neu)start oder -stopp zieht den Stick nie ab.
- **Stick standardmäßig schreibgeschützt;** was der Host schreibt, geht beim nächsten Tausch verloren (Image wird immer aus der Ablage gebaut).
- Konfiguration nur aus Umgebungsvariablen, gelesen in `config.load()` zur Laufzeit, nie beim Import.
- Nur Standardbibliothek – auf dem Pi Zero W (ARMv6, 512 MB) kein pip/venv nötig; Uploads gestreamt (nie ganze Datei in den Speicher).
- **Patchen von Dateien:** Mit dem Edit/Write-Werkzeug, nicht per `node -e`/Heredoc. Achtung: ein Edit hat ein führendes Leerzeichen am Ende des Ersetzungstexts verschluckt (Regex `_MDIR_LINE` kaputt, alle Image-Prüfungen schlugen fehl) – deshalb steht dort jetzt `[ ]`. Wichtige Leerzeichen in Regexen explizit schreiben.
- `.gitattributes` erzwingt LF (Skripte laufen auf dem Pi).

**Nicht verifiziert:** alles auf echter Hardware – configfs/dwc2 auf dem Pi Zero W, Loop-Lesezähler als Druckerkennung, ob der Photon M3 während des Drucks laufend liest und ob er einen schreibgeschützten Stick akzeptiert, Image-Bau-Dauer mit großen Dateien auf dem Pi. Der WSL-Kernel hat kein USB-Gadget (`CONFIG_USB_GADGET` aus), ein Hardwaretest geht nur auf dem Pi.

- Nur auf ausdrücklichen Wunsch committen/pushen.
