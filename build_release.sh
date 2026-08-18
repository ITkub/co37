#!/bin/bash
# Erzeugt ein Update-Paket aus dem aktuellen Stand.
#   bash build_release.sh            -> Version aus backend/VERSION
#   bash build_release.sh 0.3.0      -> setzt Version vorher
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -ge 1 ]; then echo "$1" > backend/VERSION; fi
VERSION=$(cat backend/VERSION)

# Der Agent traegt dieselbe Nummer wie das System. Zwei getrennte Versionen
# haben nur verwirrt - und beim Wechsel des Schemas ging die Paketversion
# rueckwaerts, worauf apt die Installation als Downgrade verweigert hat.
sed -i "s/^AGENT_VERSION = .*/AGENT_VERSION = \"$VERSION\"/" agent/agent.py
sed -i "s/^WATCHER_VERSION = .*/WATCHER_VERSION = \"$VERSION\"/" update_watcher.py
# Kennung der Oberflaeche. Der Selbstdiagnose-Check im Backend sucht
# genau diese Zeile; ohne sie meldet er dauerhaft eine Oberflaeche
# ohne Kennung.
sed -i "s/CO37_FRONTEND_VERSION: .*/CO37_FRONTEND_VERSION: $VERSION -->/" frontend/index.html
echo "Agent-, Watcher- und Frontend-Version auf $VERSION gesetzt"

# Gegenpruefung: eine vergessene Nummer faellt sonst erst im Betrieb auf,
# und zwar als scheinbar nicht erneuerter Watcher.
for f in "agent/agent.py:AGENT_VERSION" "update_watcher.py:WATCHER_VERSION"; do
  file="${f%%:*}"; var="${f##*:}"
  got=$(grep -m1 "^$var" "$file" | cut -d'"' -f2)
  if [ "$got" != "$VERSION" ]; then
    echo "!!! $var in $file steht auf '$got', erwartet '$VERSION'."
    exit 1
  fi
done
FE=$(grep -m1 -o "CO37_FRONTEND_VERSION: [0-9.]*" frontend/index.html | awk '{print $2}')
if [ "$FE" != "$VERSION" ]; then
  echo "!!! CO37_FRONTEND_VERSION steht auf '$FE', erwartet '$VERSION'."
  exit 1
fi

OUT="co37_v${VERSION//./_}.zip"

rm -f "$OUT"
zip -rq "$OUT" \
  backend frontend agent packaging tests \
  update_watcher.py \
  setup.sh build_packages.sh build_release.sh migrate_to_co37.sh README.md \
  REVERSE-PROXY.md GITHUB.md run-tests.sh .gitignore .gitattributes \
  -x '*__pycache__*' '*.pyc' '*.db' '*.db-*' 'backend/test*' '.DS_Store' 'packaging/cache/*' 'packaging/_msi_build/*'

# ---------------------------------------------------------------------
# Gegenpruefung: alles, was der Watcher austauschen will, muss auch im
# Paket liegen. Fehlt eine Datei, ueberspringt er sie stillschweigend -
# ihre Korrekturen erreichen den Betrieb dann nie.
# ---------------------------------------------------------------------
MISSING=""
# Paketinhalt einmal einlesen. Nicht in 'unzip -l | grep -q' leiten:
# grep -q endet beim ersten Treffer, unzip bekommt SIGPIPE, und mit
# 'set -o pipefail' sieht ein Treffer dann wie ein Fehlschlag aus.
LISTING=$(unzip -Z1 "$OUT")

for f in $(python3 - <<'PY'
import re, pathlib
src = pathlib.Path("update_watcher.py").read_text()
m = re.search(r"MANAGED_FILES = \[(.*?)\]", src, re.S)
print(" ".join(re.findall(r'"([^"]+)"', m.group(1))) if m else "")
PY
); do
  # Zeilengenau vergleichen, nicht als Teilstring: sonst wuerde ein
  # fehlendes README.md durch tests/README.md gedeckt - genau der Fall,
  # den diese Pruefung verhindern soll.
  if ! grep -Fxq "$f" <<< "$LISTING"; then
    MISSING="$MISSING $f"
  fi
done

# Der Watcher tauscht sich selbst aus, muss also ebenfalls enthalten sein
if ! grep -Fxq "update_watcher.py" <<< "$LISTING"; then
  MISSING="$MISSING update_watcher.py"
fi

if [ -n "$MISSING" ]; then
  echo "!!! Diese Dateien stehen in MANAGED_FILES, fehlen aber im Paket:"
  echo "   $MISSING"
  echo "    Der Watcher wuerde sie stillschweigend ueberspringen."
  rm -f "$OUT"
  exit 1
fi

echo "$OUT  ($(du -h "$OUT" | cut -f1))"
unzip -l "$OUT" | tail -3
