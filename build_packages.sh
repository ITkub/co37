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
OUT="${CO37_DATA:-$(pwd)/data}/packages"
mkdir -p "$OUT"

# Aeltere Agent-Pakete entfernen. Sie enthalten eine alte agent.py und
# wuerden auf dem Zielsystem gegen ein neueres Backend laufen.
OLD=$(find "$OUT" -maxdepth 1 -name 'co37-agent*' ! -name "*$VERSION*" 2>/dev/null)
if [ -n "$OLD" ]; then
  echo ">>> Entferne veraltete Pakete"
  echo "$OLD" | while read -r f; do echo "    $(basename "$f")"; rm -f "$f"; done
fi

echo ">>> Linux-Paket (Agent-Version $VERSION)"
"$PY" packaging/build_deb.py --version "$VERSION" --out "$OUT"

if [ "${1:-}" = "--deb-only" ]; then
  echo ">>> Windows uebersprungen"
else
  echo ">>> Windows-Paket"
  echo "    Python: $PY"
  if ! command -v wixl >/dev/null 2>&1; then
    echo "!!! wixl fehlt. Installieren mit:  apt install wixl"
    echo "    (Das Paket heisst wixl, nicht msitools.)"
    echo "    Ueberspringe MSI."
  else
    "$PY" packaging/build_msi.py --version "$VERSION" --out "$OUT" || \
      echo "!!! MSI-Bau fehlgeschlagen, DEB ist trotzdem vorhanden."
  fi
fi

echo
echo "Pakete in $OUT:"
ls -lh "$OUT" 2>/dev/null | tail -n +2 | awk '{print "  " $9 "  " $5}'
