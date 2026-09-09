#!/bin/bash
# Baut die Agent-Pakete. Sie enthalten kein Geheimnis - Server und
# Enrollment-Token werden erst beim Installieren uebergeben.
#
#   bash build_packages.sh            beide, Version aus backend/VERSION
#   bash build_packages.sh --deb-only ohne Windows (kein wixl noetig)
set -euo pipefail
cd "$(dirname "$0")"

# Die Paketversion kommt aus AGENT_VERSION in agent/agent.py, nicht aus
# backend/VERSION. Der Agent aendert sich nicht bei jedem Systemupdate, und
# das Backend vergleicht Pakete gegen genau diesen Wert.
# Python aus dem venv bevorzugen. Das System-Python hat auf einem Server
# oft kein pip, das der MSI-Bau fuer die Windows-Wheels braucht.
if [ -x "./venv/bin/python" ]; then
  PY="./venv/bin/python"
elif [ -x "/opt/co37/venv/bin/python" ]; then
  PY="/opt/co37/venv/bin/python"
else
  PY="python3"
fi

VERSION=$(grep -m1 '^AGENT_VERSION' agent/agent.py | cut -d'"' -f2)
if [ -z "$VERSION" ]; then
  echo "!!! AGENT_VERSION konnte nicht aus agent/agent.py gelesen werden."
  exit 1
fi
# Ausgang, nicht Eingang (F-29 der Pruefung vom 2026-08-31). Dieses
# Skript laeuft als root - der Watcher startet es. Lag das Ziel wie bis
# 0.37.9 unter data/, gehoerte es co37, und open(ziel,"wb") in
# build_deb.py folgte einer dort abgelegten Verknuepfung. Damit konnte
# co37 root dazu bringen, eine beliebige Datei zu ueberschreiben - ohne
# Paket, ohne Signatur, nur mit einer Datei im Eingang.
#
# CO37_STATE ueberschreibt das Ziel fuer Testlaeufe; ohne die Variable
# liegt es neben diesem Skript.
OUT="${CO37_STATE:-$(pwd)/state}/packages"

# Zuerst pruefen, dann anlegen. Ein Verzeichnis, das eine Verknuepfung
# IST, waere derselbe Hebel eine Ebene hoeher, und mkdir -p laeuft darauf
# ohne Fehler durch - bei einer ins Leere zeigenden Verknuepfung legt es
# sogar das Ziel an.
if [ -L "$OUT" ]; then
  echo "!!! $OUT ist eine Verknuepfung. Abbruch."
  exit 1
fi
mkdir -p "$OUT"

# Aeltere Agent-Pakete entfernen. Sie enthalten eine alte agent.py und
# wuerden auf dem Zielsystem gegen ein neueres Backend laufen.
OLD=$(find "$OUT" -maxdepth 1 -name 'co37-agent*' ! -name "*$VERSION*" 2>/dev/null)
if [ -n "$OLD" ]; then
  echo ">>> Entferne veraltete Pakete"
  echo "$OLD" | while read -r f; do echo "    $(basename "$f")"; rm -f "$f"; done
fi

echo ">>> Linux-Paket, Debian/Ubuntu (Agent-Version $VERSION)"
"$PY" packaging/build_deb.py --version "$VERSION" --out "$OUT"

# RPM fuer Red Hat, Oracle, Rocky, Alma und SUSE.
#
# Fehlt rpmbuild, entsteht kein RPM - aber der Bau bricht deswegen NICHT
# ab: das .deb und das MSI sind davon nicht betroffen, und ein Server,
# der keine RPM-Anlagen verwaltet, braucht das Paket nicht. Der Hinweis
# muss dafuer deutlich sein; ein stillschweigend fehlendes Paket waere
# genau das, was die MSI-Saga gelehrt hat.
echo ">>> Linux-Paket, RPM (Agent-Version $VERSION)"
if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "!!! rpmbuild fehlt. Installieren mit:  apt install rpm"
  echo "    Ueberspringe RPM. Debian- und Windows-Paket sind davon"
  echo "    nicht betroffen."
else
  "$PY" packaging/build_rpm.py --version "$VERSION" --out "$OUT" || \
    echo "!!! RPM-Bau fehlgeschlagen, die uebrigen Pakete sind trotzdem da."
fi

if [ "${1:-}" = "--deb-only" ]; then
  echo ">>> Windows uebersprungen"
else
  echo ">>> Windows-Paket"
  echo "    Python: $PY"
  if ! command -v wixl >/dev/null 2>&1; then
    echo "!!! wixl fehlt. Installieren mit:  apt install wixl msitools"
    echo "    (Das Paket heisst wixl, nicht msitools - msitools wird"
    echo "     zusaetzlich gebraucht, um das fertige MSI zu pruefen.)"
    echo "    Ueberspringe MSI."
  else
    "$PY" packaging/build_msi.py --version "$VERSION" --out "$OUT" || \
      echo "!!! MSI-Bau fehlgeschlagen, DEB ist trotzdem vorhanden."
  fi
fi

echo
echo "Pakete in $OUT:"
ls -lh "$OUT" 2>/dev/null | tail -n +2 | awk '{print "  " $9 "  " $5}'
