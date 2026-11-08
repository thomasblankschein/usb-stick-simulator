#!/bin/sh
# Installation auf einem Raspberry Pi Zero 2 W (oder Zero W) mit Raspberry Pi OS Lite.
# Aufruf im entpackten/geklonten Projektordner:  sudo sh deploy/install.sh
# Danach einmal neu starten (USB-Gadget-Modus wird erst nach dem Reboot aktiv).
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi

SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/usb-stick-simulator
ENV_FILE=/etc/usb-stick-simulator.env
UNIT=/etc/systemd/system/usb-stick-simulator.service

echo "==> Pakete (python3, dosfstools, mtools)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 dosfstools mtools

echo "==> USB-Gadget-Modus (dwc2) einschalten"
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_TXT=/boot/firmware/config.txt
else
    CONFIG_TXT=/boot/config.txt
fi
sh "$SRC/deploy/enable-dwc2.sh" "$CONFIG_TXT"
for mod in dwc2 libcomposite; do
    grep -qx "$mod" /etc/modules || echo "$mod" >> /etc/modules
done

echo "==> Programm nach $APP"
mkdir -p "$APP"
rm -rf "$APP/usbstick"
cp -r "$SRC/usbstick" "$APP/"
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} +

if [ ! -f "$ENV_FILE" ]; then
    cp "$SRC/deploy/usb-stick-simulator.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "    Konfiguration angelegt: $ENV_FILE"
else
    echo "    Konfiguration vorhanden, unverändert: $ENV_FILE"
fi

echo "==> systemd-Dienst"
cp "$SRC/deploy/usb-stick-simulator.service" "$UNIT"
systemctl daemon-reload
systemctl enable usb-stick-simulator.service

echo
echo "Fertig. Jetzt neu starten:  sudo reboot"
echo "Danach: Web-Oberfläche unter http://$(hostname).local/ – Log: journalctl -u usb-stick-simulator -f"
