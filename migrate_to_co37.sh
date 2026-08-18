#!/bin/bash
# =====================================================================
# Einmalige Migration einer bestehenden PatchPilot-Installation nach
# CO-37. Danach wird dieses Skript nicht mehr gebraucht.
#
#   bash migrate_to_co37.sh
#
# Was passiert:
#   /opt/patchpilot            -> /opt/co37
#   Benutzer patchpilot        -> co37
#   Dienste patchpilot-*       -> co37-*
#   data/patchpilot.db         -> data/co37.db
#   Umgebungsvariablen         -> CO37_*
#
# Das Virtualenv wird neu angelegt, weil darin absolute Pfade stecken.
# Datenbank, Schluessel und Agent-Pakete bleiben erhalten.
#
# Die Agents auf den Zielsystemen sind NICHT Teil dieser Migration.
# Sie laufen zunaechst weiter - das Auftragsprotokoll aendert sich nicht.
# =====================================================================
set -euo pipefail

OLD="/opt/patchpilot"
NEW="/opt/co37"
PORT="${CO37_PORT:-8080}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren."
  exit 1
fi

if [ ! -d "$OLD" ]; then
  echo "Unter $OLD liegt nichts. Nichts zu migrieren."
  exit 1
fi
if [ -e "$NEW" ]; then
  echo "!!! $NEW existiert bereits. Abbruch, damit nichts ueberschrieben wird."
  exit 1
fi

# ---------------------------------------------------------------------
# Sicherung, bevor irgendetwas angefasst wird
# ---------------------------------------------------------------------
STAMP=$(date +%Y%m%d-%H%M%S)
SAFE="/root/co37-migration-$STAMP"
mkdir -p "$SAFE"
echo ">>> Sicherung nach $SAFE"
cp -a "$OLD/data" "$SAFE/data"
cp -a /etc/systemd/system/patchpilot-*.service "$SAFE/" 2>/dev/null || true
echo "    Datenbank und Schluessel gesichert."

# ---------------------------------------------------------------------
# Dienste anhalten
# ---------------------------------------------------------------------
echo ">>> Alte Dienste anhalten"
for unit in patchpilot-watcher patchpilot-backend; do
  systemctl stop "$unit" 2>/dev/null || true
  systemctl disable "$unit" 2>/dev/null || true
  rm -f "/etc/systemd/system/$unit.service"
done
rm -f /etc/cron.daily/patchpilot-backup
systemctl daemon-reload

# ---------------------------------------------------------------------
# Verzeichnis und Datenbank umziehen
# ---------------------------------------------------------------------
echo ">>> Verzeichnis verschieben"
mv "$OLD" "$NEW"

if [ -f "$NEW/data/patchpilot.db" ]; then
  mv "$NEW/data/patchpilot.db" "$NEW/data/co37.db"
  echo "    Datenbank umbenannt."
fi
# Begleitdateien von SQLite, falls die Datenbank unsauber geschlossen wurde
for ext in -wal -shm; do
  [ -f "$NEW/data/patchpilot.db$ext" ] && \
    mv "$NEW/data/patchpilot.db$ext" "$NEW/data/co37.db$ext"
done
rm -rf "$NEW/data/backup"

# Das Virtualenv enthaelt absolute Pfade auf $OLD und ist nach dem
# Verschieben unbrauchbar. Neu anlegen statt reparieren.
echo ">>> Virtualenv neu anlegen"
rm -rf "$NEW/venv"

# ---------------------------------------------------------------------
# Benutzer umbenennen
# ---------------------------------------------------------------------
echo ">>> Benutzer"
if id -u patchpilot >/dev/null 2>&1; then
  usermod -l co37 -d "$NEW" patchpilot
  groupmod -n co37 patchpilot 2>/dev/null || true
  echo "    patchpilot -> co37"
elif ! id -u co37 >/dev/null 2>&1; then
  useradd -r -s /usr/sbin/nologin -d "$NEW" co37
fi
chown -R co37:co37 "$NEW"

# ---------------------------------------------------------------------
# Neue Fassung einspielen
# ---------------------------------------------------------------------
echo
echo "======================================================================"
echo " Vorbereitung abgeschlossen."
echo
echo " Jetzt das CO-37-Paket entpacken und einrichten:"
echo
echo "     unzip -o co37_v0_13_0.zip -d $NEW"
echo "     bash $NEW/setup.sh"
echo
echo " setup.sh legt Dienste, Virtualenv und Zeitplan neu an und"
echo " uebernimmt den vorhandenen Admin-Token und Schluessel aus"
echo " $NEW/data."
echo
echo " Sicherung liegt unter: $SAFE"
echo "======================================================================"
