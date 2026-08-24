"""
CO-37 - requirements.txt und das tatsaechlich Installierte stimmen ueberein.

Der Anlass, aus der Sicherheitspruefung vom 2026-08-22: die Abhaengigkeiten
lagen ein Jahr zurueck, und 23 bekannte Schwachstellen steckten darin. Der
einzige unauthentifiziert erreichbare Befund sass in starlette - und
starlette stand nicht einmal in requirements.txt, sie kam ueber fastapi.
Ein Rueckschritt dort waere niemandem aufgefallen.

Was diese Reihe leistet:

  1. Jede Zeile in requirements.txt ist auf eine genaue Fassung
     festgenagelt (==). Ein '>=' waere ein Update, das sich beim
     Einspielen selbst zusammensucht - bei jedem Kunden ein anderes.
  2. Was installiert ist, entspricht dem Pin. Faellt eine Fassung
     zurueck, meldet es sich hier statt erst in einer Pruefung, die
     niemand von Hand laufen laesst.
  3. starlette ist ausdruecklich gepinnt. Nicht wegen der Vollstaendigkeit,
     sondern weil dort der Range-Header-DoS sass.

Was sie NICHT leistet: sie kennt keine Schwachstellen. Dafuer gibt es
pip-audit, und das gehoert beim Anheben der Fassungen von Hand gelaufen.
Eine Pruefung, die dafuer ins Netz greift, waere im Testlauf am falschen
Platz - sie wuerde ohne Netz scheitern und damit den falschen Grund melden.

Braucht kein Backend und kein Netz.

    python3 tests/deps-test.py
"""

import re
import sys
from pathlib import Path

try:
    from importlib.metadata import version, PackageNotFoundError
except ImportError:  # pragma: no cover
    from importlib_metadata import version, PackageNotFoundError  # type: ignore

WURZEL = Path(__file__).resolve().parent.parent
REQ = WURZEL / "backend" / "requirements.txt"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ----------------------------------------------------------------------
# Zeilen einlesen. Kommentare und Leerzeilen fliegen raus, die Extras in
# eckigen Klammern (uvicorn[standard]) gehoeren nicht zum Paketnamen.
# ----------------------------------------------------------------------
ZEILE = re.compile(r"^([A-Za-z0-9._-]+)(\[[^\]]*\])?==([^\s#]+)\s*$")

check("requirements.txt ist lesbar", REQ.is_file(), REQ)

rohzeilen = [z.strip() for z in REQ.read_text(encoding="utf-8").splitlines()]
inhalt = [z for z in rohzeilen if z and not z.startswith("#")]

check("requirements.txt hat ueberhaupt Eintraege", len(inhalt) > 0, len(inhalt))

pins = {}
krumm = []
for zeile in inhalt:
    m = ZEILE.match(zeile)
    if not m:
        krumm.append(zeile)
        continue
    pins[m.group(1).lower().replace("_", "-")] = m.group(3)

check("jede Zeile ist auf == festgenagelt", not krumm, "; ".join(krumm))
check("alle Eintraege eingelesen", len(pins) == len(inhalt) - len(krumm),
      f"{len(pins)} von {len(inhalt)}")

# ----------------------------------------------------------------------
# starlette muss ausdruecklich dabei sein
# ----------------------------------------------------------------------
check("starlette ist ausdruecklich gepinnt", "starlette" in pins,
      sorted(pins))

# ----------------------------------------------------------------------
# Abgleich mit dem, was tatsaechlich installiert ist
# ----------------------------------------------------------------------
# Nur pruefen, was da ist: die Reihe soll auch auf einem Rechner laufen,
# auf dem das Backend nie installiert wurde - dort ist nichts zu
# vergleichen, und ein Fehlschlag deswegen waere eine Falschmeldung.
fehlt = []
abweichend = []
for name, gewuenscht in sorted(pins.items()):
    try:
        ist = version(name)
    except PackageNotFoundError:
        fehlt.append(name)
        continue
    if ist != gewuenscht:
        abweichend.append(f"{name}: installiert {ist}, gepinnt {gewuenscht}")

check("keine Fassung weicht vom Pin ab", not abweichend,
      "; ".join(abweichend))

if fehlt:
    print(f"       ({len(fehlt)} nicht installiert, nicht verglichen: "
          f"{', '.join(fehlt)})")
geprueft = len(pins) - len(fehlt)
check("mindestens ein Paket war zum Vergleichen da", geprueft > 0,
      f"{geprueft} von {len(pins)}")

# ----------------------------------------------------------------------
# Gegenprobe zur Aussagekraft: der Abgleich muss wirklich vergleichen.
# Ein Pin auf eine Fassung, die es nicht gibt, MUSS auffallen.
# ----------------------------------------------------------------------
erkannt = False
for name in sorted(pins):
    try:
        ist = version(name)
    except PackageNotFoundError:
        continue
    erkannt = ist != "0.0.0-gibtsnicht"
    break
check("der Abgleich vergleicht tatsaechlich", erkannt,
      "kein installiertes Paket gefunden" if not erkannt else "")

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
