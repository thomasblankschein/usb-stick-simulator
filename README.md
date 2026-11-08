# USB-Stick-Simulator

Ein Raspberry Pi Zero 2 W steckt am Resin-Drucker **Anycubic Photon M3** und gibt sich dort als
USB-Stick aus. Druckdateien (`.pm3` aus dem Slicer) lädst du über eine Web-Oberfläche im WLAN
hoch – kurz darauf liegen sie auf dem „Stick“. Kein Stick-Umstecken mehr.

```
 PC/Handy ──WLAN──▶ Pi Zero 2 W ──USB (Micro-USB-Datenport)──▶ Photon M3
   Web-Oberfläche     Ablage → FAT32-Image            sieht einen USB-Stick
```

## So funktioniert es

1. **Ablage:** Hochgeladene Dateien landen in `files/` auf der SD-Karte des Pi. Diese Ablage ist
   die einzige Wahrheit über den Stick-Inhalt.
2. **Image bauen:** Nach einer Änderung baut der Pi im Hintergrund ein komplett **neues**
   FAT32-Image (`mkfs.vfat` + `mcopy`, ohne mount). Der alte Stick bleibt währenddessen gesteckt.
3. **Tauschen:** Der Stick wird kurz virtuell abgezogen, das Image getauscht und wieder angesteckt
   (ca. 1–2 s). Für den Drucker sieht das aus wie Aus- und wieder Einstecken.
4. **Druckschutz:** Der Drucker liest die Schichten während des Drucks vom Stick – Abziehen
   bräche den Druck ab. Deshalb wartet der Tausch, bis der Drucker
   `USBSTICK_IDLE_SECONDS` (Standard 60 s) lang nichts gelesen hat. Gemessen wird das am
   Lesezähler des Loop-Geräts, über das das Image am USB-Gadget hängt. In der Web-Oberfläche
   lässt sich der Tausch mit „Trotzdem übernehmen“ erzwingen.

Warum nicht einfach Dateien ins gesteckte Image schreiben? Der Drucker cached das Dateisystem.
Ändert der Pi es unter ihm, sieht der Drucker Datenmüll oder zerstört das Dateisystem. Nur
Abziehen + Anstecken ist sicher.

Die Übernahme startet automatisch `USBSTICK_APPLY_DELAY` Sekunden (Standard 10) nach der
letzten Änderung – mehrere Uploads hintereinander ergeben also nur einen Tausch. Mit
`USBSTICK_APPLY_DELAY=0` gibt es nur den Knopf „Jetzt übernehmen“.

Der Stick ist standardmäßig **schreibgeschützt**. Der Drucker braucht nichts zu schreiben, und
alles, was er schreiben würde, ginge beim nächsten Tausch ohnehin verloren.

## Hardware

- Raspberry Pi **Zero 2 W** mit Raspberry Pi OS Lite (64 bit), eingerichtet wie unter
  [Pi einrichten](#pi-einrichten-raspberry-pi-imager) beschrieben. Der ältere Zero W geht auch,
  braucht aber die 32-bit-Version (ARMv6) und baut Images langsamer.
- Micro-USB-Kabel vom **Datenport** des Pi (mittlere Buchse, beschriftet „USB“, nicht „PWR“) zum
  USB-Port des Druckers. Der Pi wird darüber auch mit Strom versorgt.
- SD-Karte: Platz für die Dateien ×2 (beim Tausch existieren altes und neues Image kurz gleichzeitig)

Hinweis: Der Pi braucht nach dem Einschalten des Druckers ca. 30–40 s, bis der Stick erscheint.

### WLAN-Empfang prüfen

Der Zero hat nur eine Platinenantenne und keinen Anschluss für eine externe. Ein USB-WLAN-Stick
scheidet aus, weil der einzige Daten-USB-Port als Stick für den Drucker gebraucht wird. Am
Aufstellort deshalb den Pegel prüfen:

```bash
iw dev wlan0 link
```

Maßgeblich ist die Zeile `signal: -NN dBm` (nur sichtbar, wenn der Pi verbunden ist):

| Pegel | Bewertung |
|---|---|
| besser als −67 dBm | gut, auch für große Uploads |
| −67 bis −75 dBm | brauchbar, Uploads werden langsamer |
| −75 bis −80 dBm | wackelig, Abbrüche möglich |
| schlechter als −80 dBm | unzuverlässig |

Laufend beobachten (z. B. beim Verschieben des Pi): `watch -n 1 iw dev wlan0 link`.
Bei schwachem Empfang den Pi nicht in oder unter das Druckergehäuse legen (Metall schirmt ab),
mit einem längeren USB-Datenkabel außen anbringen oder einen 2,4-GHz-Repeater in die Nähe stellen.

## Pi einrichten (Raspberry Pi Imager)

Einmalig vor der Installation: SD-Karte (ab 8 GB) mit dem
[Raspberry Pi Imager](https://www.raspberrypi.com/software/) am PC beschreiben.

1. **Gerät:** *Raspberry Pi Zero 2 W*
2. **Betriebssystem:** *Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)* (ohne Desktop)
3. **Speicher:** die SD-Karte
4. **Einstellungen anpassen** (Frage „OS-Anpassungen anwenden?“ mit *Einstellungen bearbeiten*
   beantworten; je nach Imager-Version heißen die Schritte etwas anders):
   - **Hostname:** `usb-stick-photon-m3`
   - **Benutzer:** `pi` mit eigenem Passwort (ein Standardpasswort gibt es nicht mehr)
   - **WLAN:** Name (SSID) und Passwort; **WLAN-Land `DE`** – ohne Land bleibt das WLAN aus.
     Der Zero 2 W kann nur 2,4 GHz.
   - **Gebietsschema:** Zeitzone `Europe/Berlin`, Tastatur `de`
   - **Dienste:** **SSH aktivieren** (Passwort oder öffentlicher Schlüssel)
5. Schreiben, SD-Karte in den Pi stecken, Pi mit Strom versorgen (zum Einrichten reicht ein
   Netzteil am Anschluss „PWR IN“).

Der erste Start dauert einige Minuten (inkl. eines automatischen Neustarts). Danach vom PC
anmelden – klappt `.local` nicht, die IP aus der FRITZ!Box (*Heimnetz → Netzwerk*) nehmen:

```bash
ssh pi@usb-stick-photon-m3.local
```

System auf den neuesten Stand bringen und neu starten:

```bash
sudo apt update && sudo apt upgrade -y
```

```bash
sudo reboot
```

Klappt die Anmeldung nicht, siehe [WLAN-Empfang prüfen](#wlan-empfang-prüfen); WLAN-Daten lassen
sich am Pi mit `sudo raspi-config` (*System Options → Wireless LAN*) nachtragen.

## Installation auf dem Pi

```bash
sudo apt install -y git                        # falls git fehlt
git clone https://github.com/thomasblankschein/usb-stick-simulator.git
cd usb-stick-simulator
sudo sh deploy/install.sh
sudo reboot
```

Das Skript installiert `python3`, `dosfstools`, `mtools`, schaltet den USB-Gadget-Modus ein
(`dtoverlay=dwc2,dr_mode=peripheral` in `config.txt`, Module `dwc2` und `libcomposite`),
kopiert das Programm nach `/opt/usb-stick-simulator`, legt `/etc/usb-stick-simulator.env` an
(falls nicht vorhanden) und aktiviert den systemd-Dienst `usb-stick-simulator`.

Danach: Web-Oberfläche unter `http://<pi-name>.local/`, Log mit
`journalctl -u usb-stick-simulator -f`.

Update: im Ordner `git pull`, `sudo sh deploy/install.sh` erneut ausführen,
`sudo systemctl restart usb-stick-simulator`. Ein Neustart des Dienstes zieht den Stick **nicht**
ab (läuft das Gadget schon mit dem aktuellen Image, wird es übernommen).

### Fehlersuche: „Kein USB-Device-Controller gefunden“

Der USB-Port läuft noch im Host-Modus. Prüfen:

```bash
ls /sys/class/udc
```

Leer → der Gadget-Modus ist nicht aktiv. In `/boot/firmware/config.txt` muss am Ende stehen:

```
[all]
dtoverlay=dwc2,dr_mode=peripheral
```

Achtung: Aktuelle Images enthalten bereits `dtoverlay=dwc2,dr_mode=host` unter `[cm5]`. Die
Zeile gilt nur für das Compute Module 5 und hilft dem Zero nicht. (Die erste Version von
`install.sh` hielt sie fälschlich für ausreichend und ergänzte nichts – mit dem aktuellen Stand
`git pull` und `sudo sh deploy/install.sh` erneut ausführen.) Danach `sudo reboot`; jetzt sollte
`ls /sys/class/udc` einen Eintrag wie `3f980000.usb` zeigen.

## Feste IP-Adresse

Damit die Web-Oberfläche immer unter derselben Adresse erreichbar ist (auch wenn `.local`
nicht auflöst), sollte der Pi eine feste IP bekommen. Die Beispiele gehen von einem
FRITZ!Box-Netz `192.168.178.0/24` aus. Passe Adresse, Gateway und DNS an dein Netz an. Die
gewählte Adresse darf nicht im DHCP-Bereich des Routers liegen, sonst kann sie doppelt vergeben
werden.

**Variante A – im Router (am einfachsten):** In der FRITZ!Box unter *Heimnetz → Netzwerk →* Pi
bearbeiten → „Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“. Am Pi ändert sich
nichts, Gateway und DNS kommen weiter per DHCP.

**Variante B – am Pi, Raspberry Pi OS Bookworm und neuer (NetworkManager):**

```bash
# Name der WLAN-Verbindung herausfinden (mit dem Raspberry Pi Imager eingerichtet: meist "preconfigured")
nmcli -t -f NAME,DEVICE connection show --active

sudo nmcli connection modify "preconfigured" \
    ipv4.method manual \
    ipv4.addresses 192.168.178.50/24 \
    ipv4.gateway 192.168.178.1 \
    ipv4.dns "192.168.178.1 1.1.1.1"
sudo nmcli connection up "preconfigured"
```

- `ipv4.addresses`: feste Adresse des Pi mit Netzmaske (`/24` = 255.255.255.0)
- `ipv4.gateway`: Router (Standard-Gateway)
- `ipv4.dns`: DNS-Server, mehrere durch Leerzeichen getrennt (hier Router, dann Cloudflare als Reserve)

Wer per SSH über WLAN verbunden ist, verliert mit `connection up` die Verbindung und meldet sich
danach unter der neuen Adresse an. Prüfen: `ip -4 addr show wlan0`, `ip route` (Zeile
`default via 192.168.178.1`) und `resolvectl status` bzw. `cat /etc/resolv.conf`. Zurück auf
DHCP: `sudo nmcli connection modify "preconfigured" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns ""`.

**Variante C – ältere Versionen (Bullseye, dhcpcd):** am Ende von `/etc/dhcpcd.conf` ergänzen und
neu starten:

```
interface wlan0
static ip_address=192.168.178.50/24
static routers=192.168.178.1
static domain_name_servers=192.168.178.1 1.1.1.1
```

Welche Variante gilt, zeigt `systemctl is-active NetworkManager` (`active` → Variante B).

## Konfiguration

Umgebungsvariablen in `/etc/usb-stick-simulator.env`, alle optional. Vollständige Liste mit
Erklärungen: [`deploy/usb-stick-simulator.env.example`](deploy/usb-stick-simulator.env.example).

| Variable | Standard | Bedeutung |
|---|---|---|
| `USBSTICK_DATA_DIR` | `/var/lib/usb-stick-simulator` | Ablage (`files/`) und Images (`images/`) |
| `USBSTICK_IMAGE_SIZE_MB` | `4096` | nominelle Stick-Größe (Sparse-Datei, belegt nur den Inhalt) |
| `USBSTICK_LABEL` | `PHOTON` | Datenträgername |
| `USBSTICK_GADGET` | `configfs` | `fake` für Entwicklung ohne USB-Hardware |
| `USBSTICK_READONLY` | `true` | Stick schreibgeschützt anbieten |
| `USBSTICK_APPLY_DELAY` | `10` | Sekunden bis zur automatischen Übernahme, `0` = nur per Knopf |
| `USBSTICK_IDLE_SECONDS` | `60` | so lange darf der Drucker nichts gelesen haben, bevor getauscht wird |
| `USBSTICK_REPLUG_PAUSE` | `1.5` | Pause zwischen Abziehen und Anstecken |
| `USBSTICK_HOST` / `USBSTICK_PORT` | `0.0.0.0` / `80` | Web-Oberfläche |
| `USBSTICK_WEB_USER` / `USBSTICK_WEB_PASSWORD` | – | optionaler Passwortschutz (HTTP Basic) |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |

`USBSTICK_IDLE_SECONDS` muss länger sein als die längste Pause zwischen zwei Lesezugriffen
während eines Drucks (Belichtung + Hub einer Schicht; am echten Druck im Log mit
`LOG_LEVEL=debug` bzw. an „Letzter Lesezugriff“ in der Oberfläche beobachten). Zu kurz → ein laufender Druck kann abbrechen; zu lang → Uploads erscheinen später.

## Web-Oberfläche und API

Die Seite zeigt Stick-Status, letzten Lesezugriff, Dateiliste (mit Markierung „noch nicht auf
dem Stick“), Belegung, Upload per Drag & Drop und die Knöpfe „Jetzt übernehmen“ bzw.
„Trotzdem übernehmen“.

| Methode | Pfad | |
|---|---|---|
| `GET` | `/api/status` | Zustand (Phase, Druckeraktivität, Dateien auf dem Stick, Belegung, letzter Fehler) |
| `GET` | `/api/files` | Dateien in der Ablage |
| `PUT` | `/api/files/<name>` | Datei hochladen, Rohdaten im Body, `Content-Length` Pflicht |
| `DELETE` | `/api/files/<name>` | Datei löschen |
| `POST` | `/api/apply` | sofort übernehmen; `?force=1` auch, wenn der Drucker gerade liest |
| `GET` | `/health` | `ok` (ohne Anmeldung) |

Beispiel:

```bash
curl -T "Halter v2.pm3" "http://usbstick.local/api/files/Halter%20v2.pm3"
```

Dateinamen werden für FAT bereinigt: nur ASCII (Umlaute → ae/oe/ue/ss), keine Pfade, keine
Sonderzeichen außer ` ._-()+`, höchstens 100 Zeichen. Die Antwort enthält den tatsächlich
gespeicherten Namen. Eine gleichnamige Datei (auch in anderer Groß-/Kleinschreibung) wird ersetzt.

Phasen in `/api/status`: `idle`, `pending` (Übernahme folgt), `manual` (Knopf drücken),
`building`, `waiting` (Drucker liest), `swapping`, `error`.

## Entwicklung (Windows + WSL)

Ohne Pi läuft alles mit dem Fake-Gadget in der WSL (braucht `mtools` und `dosfstools`:
`sudo apt install mtools dosfstools`):

```bash
USBSTICK_GADGET=fake USBSTICK_DATA_DIR=/tmp/usbstick-dev USBSTICK_PORT=8080 python3 -m usbstick
```

Dann `http://localhost:8080/` im Windows-Browser öffnen.

Tests (nur Standardbibliothek, kein Test-Framework):

```bash
python3 -m unittest discover -s tests
```

Die Image-, Ablauf- und API-Tests brauchen `mtools`/`dosfstools` und werden sonst übersprungen.

## Stand

- Getestet (WSL): Image-Bau mit echtem mtools (Inhalt byte-genau zurückgelesen), gesamter Ablauf
  mit Fake-Gadget (automatische und manuelle Übernahme, Warten bei Druckeraktivität, Erzwingen,
  Neustart, Fehler und Erholung), Web-API und Oberfläche im Browser, configfs-Steuerung gegen
  einen nachgebauten sysfs-Baum.
- **Nicht getestet:** echter Pi Zero 2 W (configfs/dwc2, Loop-Gerät, Lesezähler), der Photon M3 am
  simulierten Stick, Dauer des Image-Baus mit großen Dateien auf dem Pi. Offen ist insbesondere,
  ob der Photon M3 während des Drucks tatsächlich laufend vom Stick liest (dann greift der
  Druckschutz) und ob er einen schreibgeschützten Stick akzeptiert (sonst
  `USBSTICK_READONLY=false`).

## Aufbau

```
usbstick/
  __main__.py   Start, Signale
  config.py     Umgebungsvariablen
  library.py    Ablage: Upload (atomar über .part), Löschen, Namensbereinigung, Snapshot per Hardlinks
  image.py      FAT32-Image bauen und prüfen (mkfs.vfat, mcopy, mdir), Manifest
  gadget.py     ConfigfsGadget (Kernel, Loop-Gerät, Lesezähler), FakeGadget
  manager.py    Ablauf: Änderungen → Image bauen → auf Leerlauf warten → tauschen
  server.py     HTTP-Server und API (http.server)
  web/index.html  Oberfläche
deploy/         install.sh, systemd-Unit, Beispielkonfiguration
tests/          unittest
```
