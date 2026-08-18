"""
CO-37 - Selbstheilung und Protokoll des Agents.

Grund fuer dieses Geruest: eine fehlerhafte Fassung ist unbemerkt liegen
geblieben. Der Agent lief durch, scheiterte bei jedem Heartbeat und meldete
das auch - nur schreibt er unter pythonw.exe ohne Konsole ins Leere. Der
Rollback zaehlte ausschliesslich beim Prozessstart hoch und griff deshalb
nie.

Braucht kein Backend. Legt sich eine eigene Kopie von agent.py samt
Konfiguration in einem temporaeren Verzeichnis an, damit weder das
Arbeitsverzeichnis noch /etc angefasst wird.

    python3 tests/agent-selfheal-test.py
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "agent" / "agent.py"

tmp = Path(tempfile.mkdtemp())
shutil.copy2(SRC, tmp / "agent.py")
(tmp / "agent.conf").write_text(
    "server = http://127.0.0.1:1\ntoken = test\nverify_ssl = false\n")

spec = importlib.util.spec_from_file_location("co37_agent", tmp / "agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ---------------------------------------------------------------- Vermerk
check("kein Vermerk -> None", agent.update_marker_info() is None)
agent.UPDATE_MARKER.write_text(json.dumps(
    {"old_version": "0.16.1", "at": time.time() - 500, "starts": 0}))
check("Vermerk gelesen", agent.update_marker_info()["old_version"] == "0.16.1")
agent.UPDATE_MARKER.write_text("kaputt")
check("unlesbarer Vermerk -> leeres dict", agent.update_marker_info() == {})
agent.clear_update_marker()


# ------------------------------------------------- Ausloesen des Rollbacks
def would_roll_back(code, n, seconds_since, marker=True):
    """Bildet die Bedingung aus der Hauptschleife nach."""
    info = {"at": time.time() - seconds_since} if (marker and code != 401) else None
    if info is None:
        return False
    since = time.time() - float(info.get("at") or 0)
    return (n >= agent.MAX_HTTP_FAILS_BEFORE_ROLLBACK
            and since >= agent.MIN_SECONDS_BEFORE_HTTP_ROLLBACK)


check("500, genug Fehlschlaege und Zeit -> Rollback",
      would_roll_back(500, agent.MAX_HTTP_FAILS_BEFORE_ROLLBACK, 300))
check("ein Fehlschlag zu wenig -> kein Rollback",
      not would_roll_back(500, agent.MAX_HTTP_FAILS_BEFORE_ROLLBACK - 1, 300))
check("zu frueh nach der Aktualisierung -> kein Rollback",
      not would_roll_back(500, agent.MAX_HTTP_FAILS_BEFORE_ROLLBACK, 10))
check("401 zaehlt nicht (Host entfernt, nicht Code kaputt)",
      not would_roll_back(401, 99, 9999))
check("ohne Vermerk kein Rollback",
      not would_roll_back(500, 99, 9999, marker=False))
check("422 zaehlt wie 500 (Server lehnt unsere Daten ab)",
      would_roll_back(422, agent.MAX_HTTP_FAILS_BEFORE_ROLLBACK, 300))

# Unerreichbarer Server ist keine HTTPError - er kommt gar nicht hier an.
# Die Trennung ist der Grund, warum ein Backend-Neustart keine Flotte
# zurueckrollt.
check("Verbindungsfehler ist keine HTTPError",
      not issubclass(agent.requests.ConnectionError, agent.requests.HTTPError))


# ------------------------------------------------------ Abgelehnte Version
agent.REJECTED_MARKER.unlink(missing_ok=True)
check("ohne Sperre keine abgelehnte Version", agent.rejected_version() is None)
agent.REJECTED_MARKER.write_text(json.dumps({"version": "0.18.0", "at": time.time()}))
check("abgelehnte Version gelesen", agent.rejected_version() == "0.18.0")
check("andere Fassung ist nicht gesperrt", agent.rejected_version() != "0.18.1")
agent.REJECTED_MARKER.write_text("kaputt")
check("unlesbare Sperre blockiert nichts", agent.rejected_version() is None)
agent.REJECTED_MARKER.unlink(missing_ok=True)


# ---------------------------------------------------------------- Protokoll
agent.log("Testzeile")
agent.log("Fehlerzeile", err=True)
txt = agent.LOG_FILE.read_text(encoding="utf-8")
check("Protokoll geschrieben",
      "Testzeile" in txt and "FEHLER Fehlerzeile" in txt)

agent.LOG_MAX_BYTES = 200
for i in range(60):
    agent.log(f"Fuellzeile {i} " + "-" * 30)
check("Umlauf angelegt", agent.LOG_FILE.with_suffix(".log.1").exists())
check("aktuelle Datei laeuft nicht voll",
      agent.LOG_FILE.stat().st_size <= 400, agent.LOG_FILE.stat().st_size)

agent.LOG_FILE = Path("/nicht/vorhanden/agent.log")
try:
    agent.log("darf nicht werfen")
    check("unschreibbares Protokoll wirft nicht", True)
except Exception as exc:  # noqa: BLE001
    check("unschreibbares Protokoll wirft nicht", False, exc)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
