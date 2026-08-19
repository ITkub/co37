#!/bin/bash
#
# CO-37 - alle Tests
#
# Startet ein eigenes Backend auf einem freien Port mit eigener Datenbank,
# laesst alle Reihen durchlaufen und raeumt danach auf. Die Produktivdaten
# werden nie angefasst.
#
#   ./run-tests.sh              alles
#   ./run-tests.sh roles        nur diese Reihe
#   ./run-tests.sh -v           volle Ausgabe statt nur der Zusammenfassung
#
set -u

PORT="${CO37_TEST_PORT:-8099}"
KEY="testkey"
TMP=$(mktemp -d)
AUSFUEHRLICH=0
NUR=""

for arg in "$@"; do
  case "$arg" in
    -v|--verbose) AUSFUEHRLICH=1 ;;
    -*) echo "Unbekannte Option: $arg"; exit 2 ;;
    *)  NUR="$arg" ;;
  esac
done

HIER=$(cd "$(dirname "$0")" && pwd)
cd "$HIER" || exit 1

# ---------------------------------------------------------------------
# Reihenfolge
# ---------------------------------------------------------------------
# roles MUSS zuletzt laufen. Am Ende loest die Reihe die Drosselung der
# Anmeldung aus; die gilt fuer die gesamte Absender-Adresse und bleibt
# eine Viertelstunde bestehen. Jede danach gestartete Reihe, die sich
# anmeldet, liefe in die Sperre und meldete Fehler, die nichts mit ihr zu
# tun haben - genau so ist der Frontend-Test schon einmal stumm
# ausgestiegen.
REIHEN=(
  "proxy-fallback  python3 tests/proxy-fallback-test.py"
  "license        python3 tests/license-test.py"
  "release-sig    python3 tests/release-sig-test.py"
  "patchdue        python3 tests/patchdue-test.py"
  "login-throttle  python3 tests/login-throttle-test.py"
  "agent-selfheal  python3 tests/agent-selfheal-test.py"
  "proxy-https     python3 tests/proxy-https-test.py"
  "agent-api       python3 tests/agent-api-test.py"
  "frontend        node tests/frontend-test.js PORT KEY frontend/index.html"
  "roles           python3 tests/roles-test.py"
)

aufraeumen() {
  [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null
  wait "${BACKEND_PID:-}" 2>/dev/null
  rm -rf "$TMP"
}
trap aufraeumen EXIT INT TERM

# ---------------------------------------------------------------------
# Backend starten
# ---------------------------------------------------------------------
if ! python3 tests/port-frei.py "$PORT"; then
  echo "Port $PORT ist belegt. Anderen waehlen: CO37_TEST_PORT=8100 $0"
  exit 1
fi

echo "Backend auf Port $PORT, Daten in $TMP"
(
  cd backend || exit 1
  CO37_ADMIN_TOKEN="$KEY" \
  CO37_SECRET_KEY="testsecret" \
  CO37_DB="sqlite:///$TMP/co37.db" \
  CO37_DATA="$TMP" \
  python3 -m uvicorn main:app --port "$PORT" > "$TMP/backend.log" 2>&1
) &
BACKEND_PID=$!

bereit=0
for _ in $(seq 1 40); do
  sleep 0.5
  if curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    bereit=1; break
  fi
  # Frueh abbrechen, wenn der Prozess gar nicht mehr laeuft - sonst wartet
  # man zwanzig Sekunden auf etwas, das schon gestorben ist.
  kill -0 "$BACKEND_PID" 2>/dev/null || break
done

if [ "$bereit" -ne 1 ]; then
  echo "Backend nicht erreichbar. Letzte Zeilen:"
  tail -20 "$TMP/backend.log"
  exit 1
fi

# ---------------------------------------------------------------------
# Reihen durchlaufen
# ---------------------------------------------------------------------
export CO37_TEST_URL="http://127.0.0.1:$PORT"
export CO37_TEST_KEY="$KEY"

gesamt=0; fehlerhaft=0; uebersprungen=0
echo

for eintrag in "${REIHEN[@]}"; do
  name="${eintrag%% *}"
  befehl="${eintrag#* }"
  befehl="${befehl#"${befehl%%[![:space:]]*}"}"
  befehl="${befehl/PORT/$PORT}"
  befehl="${befehl/KEY/$KEY}"

  if [ -n "$NUR" ] && [ "$NUR" != "$name" ]; then
    uebersprungen=$((uebersprungen + 1))
    continue
  fi

  ausgabe=$($befehl 2>&1)
  code=$?
  n=$(printf '%s\n' "$ausgabe" | grep -cE '^(ok|FEHLER)')
  gesamt=$((gesamt + n))

  if [ "$code" -eq 0 ]; then
    printf '  \033[32m✓\033[0m %-16s %3d Prüfungen\n' "$name" "$n"
  else
    fehlerhaft=$((fehlerhaft + 1))
    printf '  \033[31m✗\033[0m %-16s %3d Prüfungen\n' "$name" "$n"
    printf '%s\n' "$ausgabe" | grep -E '^FEHLER' | sed 's/^/      /'
    # Bricht eine Reihe ab, statt Fehler zu melden, steht der Grund nur am
    # Ende der Ausgabe.
    if [ "$n" -eq 0 ]; then
      printf '%s\n' "$ausgabe" | tail -5 | sed 's/^/      /'
    fi
  fi

  [ "$AUSFUEHRLICH" -eq 1 ] && printf '%s\n' "$ausgabe" | sed 's/^/      /'
done

# ---------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------
echo
# grep -c gibt bei null Treffern '0' aus UND meldet Fehlschlag. Ein
# '|| echo 0' haengt dann eine zweite Null an, und der Vergleich unten
# scheitert an '0\n0'.
tracebacks=$(grep -c "Traceback" "$TMP/backend.log" 2>/dev/null)
tracebacks=${tracebacks:-0}
if [ "$tracebacks" -gt 0 ]; then
  echo "  Achtung: $tracebacks Traceback(s) im Backend-Log"
  grep -A 12 "Traceback" "$TMP/backend.log" | tail -20 | sed 's/^/      /'
  fehlerhaft=$((fehlerhaft + 1))
fi

[ "$uebersprungen" -gt 0 ] && echo "  $uebersprungen Reihe(n) übersprungen"

if [ "$fehlerhaft" -eq 0 ]; then
  echo "  $gesamt Prüfungen, alle bestanden"
  exit 0
fi
echo "  $gesamt Prüfungen, $fehlerhaft Reihe(n) mit Fehlern"
exit 1
