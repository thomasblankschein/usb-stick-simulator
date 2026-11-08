#!/bin/sh
# Schaltet in config.txt den USB-Gadget-Modus ein (dtoverlay=dwc2,dr_mode=peripheral).
# Aufruf: enable-dwc2.sh <pfad/zu/config.txt>   (von install.sh benutzt, einzeln testbar)
#
# Wichtig: Aktuelle Raspberry-Pi-OS-Images enthalten schon eine Zeile
#   [cm5]
#   dtoverlay=dwc2,dr_mode=host
# Die gilt nur für das Compute Module 5 und schaltet den Host-Modus ein. Ein einfaches
# "grep dtoverlay=dwc2" hält das fälschlich für erledigt – genau daran scheiterte die erste
# Version (Pi meldete danach "Kein USB-Device-Controller gefunden").
# Deshalb wird nur unsere eigene Zeile (peripheral) als erledigt gewertet, und zwar nur in
# einem Abschnitt, der für alle Boards gilt ([all] oder vor dem ersten Filter).
set -eu

CONFIG_TXT=${1:?Pfad zu config.txt fehlt}
LINE='dtoverlay=dwc2,dr_mode=peripheral'

already=$(awk -v line="$LINE" '
    /^[[:space:]]*\[/ { section = $0; gsub(/[[:space:]]/, "", section); next }
    {
        current = $0; gsub(/[[:space:]]/, "", current)
        if (current == line && (section == "" || section == "[all]")) found = 1
    }
    END { print found ? "ja" : "nein" }
' "$CONFIG_TXT")

if [ "$already" = "ja" ]; then
    echo "    $CONFIG_TXT enthält bereits $LINE"
    exit 0
fi

cp "$CONFIG_TXT" "$CONFIG_TXT.bak-usbstick"
printf '\n[all]\n# USB-Stick-Simulator: Micro-USB-Datenport als Gerät (Gadget)\n%s\n' "$LINE" >> "$CONFIG_TXT"
echo "    $LINE in $CONFIG_TXT ergänzt (Sicherung: $CONFIG_TXT.bak-usbstick) – Neustart nötig"
