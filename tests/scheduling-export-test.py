"""
CO-37 - main und scheduling meinen denselben Gegenstand.

DER ANLASS

Die Auftragsplanung ist aus main.py in backend/scheduling.py gezogen
worden. main.py holt sie per Re-Export zurueck, damit sechs vorhandene
Pruefreihen (patchdue, area-schedule, area, jobstille, nachreichung,
scan-patch) unveraendert weiterlaufen - sie sind das Sicherungsnetz des
Umbaus.

Ein Re-Export ist aber nicht umsonst zu haben: schriebe jemand spaeter
eine zweite Fassung derselben Funktion nach main.py, waeren beide da,
nur eine wuerde benutzt, und alle Reihen blieben gruen. Das ist
woertlich F-12/F-14 - ein Wert an zwei Stellen, von denen nur eine
wirkt. Dieselbe Fehlerart hat dieses Projekt mehrfach getroffen.

Deshalb wird hier nicht geprueft, dass es die Namen GIBT, sondern dass
sie auf DASSELBE OBJEKT zeigen.

ZWEI SONDERFAELLE

BACKEND_START wird ABSICHTLICH nicht re-exportiert. jobstille-test.py
setzt den Wert, um die Zeit vorzuspulen; laege er an zwei Stellen,
traefe die Zuweisung die eine und die Abfrage die andere. Die Pruefung
unten haelt fest, dass er in main nicht auftaucht.

patch_auftraege_planen und auftraege_aufraeumen werden ebenfalls nicht
re-exportiert - die Route ruft sie ueber das Modul auf. Sie sind durch
agent-api, patchdue und scan-patch abgedeckt.

Laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/scheduling-export-test.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/export.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))

import main        # noqa: E402
import scheduling  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ----------------------------------------------------------------------
# 1. Was re-exportiert sein MUSS - und zwar als dasselbe Objekt
# ----------------------------------------------------------------------
# Die Liste ist bewusst ausgeschrieben und nicht aus main abgeleitet:
# eine Pruefung, die ihre Erwartung aus dem Prueflingen holt, bestaetigt
# nur sich selbst.
RE_EXPORTIERT = [
    "DAYS",
    "JOB_LOG_SILENCE",
    "JOB_MAX_RUNTIME",
    "JOB_MAX_WAIT",
    "MAX_JOB_DELIVERIES",
    "PATCH_MAX_RUNS_PER_SLOT",
    "REDELIVER_AFTER",
    "_followup_due",
    "_schedule_due",
    "area_patch_due",
    "job_verstummt",
    "job_vom_server_abbrechen",
    "patch_due",
    "verfallsgrund",
]

for name in RE_EXPORTIERT:
    im_modul = getattr(scheduling, name, None)
    in_main = getattr(main, name, None)
    check(f"scheduling kennt {name}", im_modul is not None)

    gleich = in_main is not None and in_main is im_modul
    # Der Grund NUR im Fehlerfall. Steht er auch hinter einem 'ok', liest
    # sich die Ausgabe, als waere etwas kaputt - und eine Pruefreihe, die
    # den falschen Grund nennt, ist schlimmer als keine.
    check(f"main.{name} ist dasselbe Objekt wie scheduling.{name}", gleich,
          "" if gleich else
          ("fehlt in main" if in_main is None else "zwei verschiedene Objekte"))

# ----------------------------------------------------------------------
# 2. BACKEND_START liegt an GENAU EINER Stelle
# ----------------------------------------------------------------------
# jobstille-test.py schreibt diesen Wert. Gaebe es ihn in main auch,
# ginge die Zuweisung ins Leere und die Reihe pruefte etwas anderes, als
# sie zu pruefen glaubt.
check("scheduling kennt BACKEND_START",
      getattr(scheduling, "BACKEND_START", None) is not None)
_nur_einmal = not hasattr(main, "BACKEND_START")
check("main kennt BACKEND_START NICHT", _nur_einmal,
      "" if _nur_einmal else
      "steht in main - die Zuweisung in jobstille-test wirkt dann nur dort")

# ----------------------------------------------------------------------
# 3. Die neuen Funktionen gibt es
# ----------------------------------------------------------------------
for name in ("patch_auftraege_planen", "auftraege_aufraeumen"):
    check(f"scheduling kennt {name}",
          callable(getattr(scheduling, name, None)))

# ----------------------------------------------------------------------
# 4. scheduling zieht main NICHT nach sich
# ----------------------------------------------------------------------
# Der Zirkelimport ist der Fehler, der diesen Schnitt unmoeglich machen
# wuerde. Geprueft in einem EIGENEN Python: in diesem hier ist main
# laengst geladen, die Frage liesse sich hier gar nicht stellen.
# Der Pfad geht als ARGUMENT, nicht in den Quelltext.
#
# Vorher stand er per %s in der an -c uebergebenen Zeichenkette. Das
# laeuft unter C:\Projekte\co37 einwandfrei und bricht bei einem Klon
# unter einem Pfad mit Apostroph - und bei einem, der auf \ endet, ist
# der Raw-String sogar ein Syntaxfehler. Der Fehlschlag saehe dann aus
# wie der Zirkelimport, den diese Pruefung sucht: wieder eine Meldung
# mit dem falschen Grund.
roh = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, sys.argv[1]); import scheduling; "
     "print('main' in sys.modules)",
     str(WURZEL / "backend")],
    capture_output=True, text=True, timeout=120,
)
check("scheduling laesst sich ohne main laden",
      roh.returncode == 0, (roh.stderr or "").strip()[-200:])
check("und zieht main dabei nicht mit herein",
      roh.stdout.strip() == "False", roh.stdout.strip() or "(keine Ausgabe)")

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
