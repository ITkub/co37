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
# Werkzeuge suchen
# ---------------------------------------------------------------------
# Nicht fest 'python3' aufrufen. Unter Windows gibt es das nicht - dort
# faengt ein Platzhalter des Microsoft Store den Aufruf ab, schreibt einen
# Hinweis und scheitert. Frueher lief das in den Portcheck weiter unten
# und wurde dort zu "Port belegt" - eine Meldung, die mit dem wahren Grund
# nichts zu tun hat. Der Port war frei. Daraufhin wurde einmal ungetestet
# gebaut, eingecheckt und ausgeliefert.
#
# Darum die Kandidaten wirklich ausfuehren statt nur im Pfad zu suchen:
# 'command -v' sagt, dass etwas dort liegt, nicht dass es taugt. Nur was
# sich als Python 3 meldet, zaehlt - der Store-Platzhalter faellt damit
# von selbst durch, ein altes Python 2 ebenfalls.
werkzeug_fehlt() {
  rm -rf "$TMP"
  exit 1
}

PY=""
for kandidat in "${CO37_PYTHON:-}" python3 python py; do
  [ -z "$kandidat" ] && continue
  if "$kandidat" -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" \
       >/dev/null 2>&1; then
    PY="$kandidat"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Kein Python 3 gefunden. Versucht wurden: python3, python, py."
  echo "Unter Windows meldet sich hier oft nur der Platzhalter des"
  echo "Microsoft Store. Eigener Pfad: CO37_PYTHON=/pfad/zu/python $0"
  werkzeug_fehlt
fi

# node braucht nur die Frontend-Reihe. Ohne diese Pruefung faellt sie
# spaeter mit null Pruefungen durch, und der Grund steht klein am Ende
# ihrer Ausgabe. Lieber gleich hier und deutlich.
#
# Nur verlangen, wenn diese Reihe ueberhaupt drankommt: wer gezielt
# 'run-tests.sh agent-api' aufruft, braucht kein node und soll deswegen
# nicht abgewiesen werden.
if [ -z "$NUR" ] || [ "$NUR" = "frontend" ]; then
  if ! node --version >/dev/null 2>&1; then
    echo "node nicht gefunden. Die Frontend-Reihe braucht es."
    echo "Entweder node installieren oder eine einzelne Reihe waehlen,"
    echo "etwa: $0 agent-api"
    werkzeug_fehlt
  fi
fi

# ---------------------------------------------------------------------
# Reihenfolge
# ---------------------------------------------------------------------
# roles MUSS zuletzt laufen. Am Ende loest die Reihe die Drosselung der
# Anmeldung aus; die gilt fuer die gesamte Absender-Adresse und bleibt
# eine Viertelstunde bestehen. Jede danach gestartete Reihe, die sich
# anmeldet, liefe in die Sperre und meldete Fehler, die nichts mit ihr zu
# tun haben - genau so ist der Frontend-Test schon einmal stumm
# ausgestiegen.
#
# $PY steht hier direkt drin, nicht als Platzhalter wie PORT und SESSION.
# Eine Ersetzung waere wieder ein Treffer auf eine Teilzeichenkette -
# das Muster, an dem hier schon mehrfach etwas gescheitert ist.
#
# Die Reihen enroll, health-info und agent-haertung starten ein eigenes
# Backend im eigenen Prozess mit eigener Datenbank. Das ist Absicht und
# kein Versehen: sie muessen Grenzen tatsaechlich erreichen (fuenfzig
# Anmeldungen) oder von einer Adresse ausserhalb von Loopback aufrufen -
# beides ginge gegen das gemeinsame Backend nicht, ohne die nachfolgenden
# Reihen zu beschaedigen. Die Begruendung steht ausfuehrlich im Kopf der
# jeweiligen Datei.
REIHEN=(
  "deps            $PY tests/deps-test.py"
  "setup           $PY tests/setup-test.py"
  "apikey          $PY tests/apikey-test.py"
  "enroll          $PY tests/enroll-test.py"
  "health-info     $PY tests/health-info-test.py"
  "agent-haertung  $PY tests/agent-haertung-test.py"
  "proxy-fallback  $PY tests/proxy-fallback-test.py"
  "license        $PY tests/license-test.py"
  "release-sig    $PY tests/release-sig-test.py"
  "watcher-sig    $PY tests/watcher-sig-test.py"
  "keyguard       $PY tests/keyguard-test.py"
  "patchdue        $PY tests/patchdue-test.py"
  "area-migrate    $PY tests/area-migrate-test.py"
  "area-schedule   $PY tests/area-schedule-test.py"
  "login-throttle  $PY tests/login-throttle-test.py"
  "agent-selfheal  $PY tests/agent-selfheal-test.py"
  "reboot-report   $PY tests/reboot-report-test.py"
  "harness         $PY tests/harness-test.py"
  "archiv          $PY tests/archiv-test.py"
  "i18n            node tests/i18n-test.js frontend/index.html"
  "i18n-api        $PY tests/i18n-api-test.py"
  "i18n-guard      node tests/i18n-guard-test.js frontend/app.js"
  "proxy-https     $PY tests/proxy-https-test.py"
  "agent-api       $PY tests/agent-api-test.py"
  "area            $PY tests/area-test.py"
  "host-patch      $PY tests/host-patch-test.py"
  "frontend        node tests/frontend-test.js PORT SESSION frontend/index.html"
  "roles           $PY tests/roles-test.py"
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
# Ab hier ist die Meldung ehrlich: dass Python laeuft, steht oben schon
# fest. Frueher landete jeder Grund, aus dem dieser Aufruf scheiterte,
# hier als "Port belegt" - auch ein fehlendes Python.
if ! "$PY" tests/port-frei.py "$PORT"; then
  echo "Port $PORT ist belegt. Anderen waehlen: CO37_TEST_PORT=8100 $0"
  exit 1
fi

echo "Backend auf Port $PORT, Daten in $TMP"
(
  cd backend || exit 1
  CO37_SECRET_KEY="testsecret" \
  CO37_DB="sqlite:///$TMP/co37.db" \
  CO37_DATA="$TMP" \
  "$PY" -m uvicorn main:app --port "$PORT" > "$TMP/backend.log" 2>&1
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

# ---------------------------------------------------------------------
# Eine Sitzung fuer alle Reihen
# ---------------------------------------------------------------------
# Frueher sprachen die Reihen das Backend mit dem globalen Admin-Token an
# (X-API-Key). Den gibt es nicht mehr - er lief nie ab, galt auf jeder
# Route und war die einzige Berechtigung, die keine Drosselung bremste.
#
# Stattdessen EINE Anmeldung hier, das Sitzungstoken geht als
# CO37_TEST_SESSION an alle Reihen. Bewusst nur einmal und bewusst hier:
# roles-test.py loest am Ende absichtlich die Anmeldedrosselung aus. Wuerde
# sich jede Reihe selbst anmelden, liefe alles danach in die Sperre. Eine
# bereits bestehende Sitzung beruehrt das nicht - gedrosselt wird nur die
# Anmelderoute.
SESSION=$(curl -s --max-time 10 -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' \
  "http://127.0.0.1:$PORT/api/v1/login" \
  | "$PY" -c 'import sys,json; print(json.load(sys.stdin).get("session",""))' 2>/dev/null)

if [ -z "$SESSION" ]; then
  echo "Anmeldung am Testbackend fehlgeschlagen - ohne Sitzung laeuft keine Reihe."
  echo "Letzte Zeilen des Backends:"
  tail -20 "$TMP/backend.log"
  exit 1
fi

# Das Anfangspasswort muss geaendert werden, bevor irgendetwas anderes
# geht (F-16, ab 0.37.8). Genau das macht die frische Installation auch,
# und deshalb macht das Geruest es hier mit statt den Zwang fuer Tests
# abzuschalten: eine Absicherung, die im Testbetrieb nicht gilt, ist im
# Testbetrieb auch nicht geprueft.
#
# Die Sitzung wird beim Wechsel verworfen - danach also neu anmelden.
TESTPW="testlauf-passwort-2026"
curl -s --max-time 10 -X POST \
  -H "Content-Type: application/json" -H "X-Session: $SESSION" \
  -d "{\"old_password\":\"admin\",\"new_password\":\"$TESTPW\"}" \
  "http://127.0.0.1:$PORT/api/v1/me/password" > /dev/null

SESSION=$(curl -s --max-time 10 -X POST \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"admin\",\"password\":\"$TESTPW\"}" \
  "http://127.0.0.1:$PORT/api/v1/login" \
  | "$PY" -c 'import sys,json; print(json.load(sys.stdin).get("session",""))' 2>/dev/null)

if [ -z "$SESSION" ]; then
  echo "Anmeldung nach dem erzwungenen Passwortwechsel fehlgeschlagen."
  tail -20 "$TMP/backend.log"
  exit 1
fi
export CO37_TEST_SESSION="$SESSION"
export CO37_TEST_ADMIN_PW="$TESTPW"

gesamt=0; fehlerhaft=0; uebersprungen=0
echo

for eintrag in "${REIHEN[@]}"; do
  name="${eintrag%% *}"
  befehl="${eintrag#* }"
  befehl="${befehl#"${befehl%%[![:space:]]*}"}"
  befehl="${befehl/PORT/$PORT}"
  befehl="${befehl/SESSION/$SESSION}"

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
