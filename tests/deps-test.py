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
    from importlib.metadata import distribution, version, PackageNotFoundError
except ImportError:  # pragma: no cover
    from importlib_metadata import (  # type: ignore
        distribution, version, PackageNotFoundError)

# Zum Auswerten der Abhaengigkeitsangaben in den Metadaten. Die Marker
# ("extra == 'standard'", "python_version < '3.11'") von Hand zu
# zerlegen waere die Sorte Eigenbau, die genau dann falsch liegt, wenn es
# darauf ankommt.
#
# 'packaging' ist auf vielen Rechnern da, aber nicht auf allen - pip
# bringt es als eigene Kopie mit. Beide Wege versuchen, und wenn keiner
# geht, das SAGEN statt die Pruefung stillschweigend auszulassen.
try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError:
        Requirement = None

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
# Die Huelle ist vollstaendig: was gebraucht wird, steht auch drin
# ----------------------------------------------------------------------
# Bis 0.37.16 nannte die Datei acht Pakete, installiert waren dreissig.
# Die uebrigen zog pip beim Aufloesen dazu, in der Fassung, die PyPI im
# Augenblick des Updates gerade auslieferte - und der Watcher ruft
# "pip install -r" bei JEDEM Update auf. Vier davon waren am 2026-09-04
# nachweislich schon auseinandergelaufen (anyio, SQLAlchemy, greenlet,
# websockets).
#
# Geprueft wird ueber die Metadaten des Installierten, nicht gegen eine
# hier abgeschriebene Liste: eine zweite Aufzaehlung waere genau der
# Fehler, den F-14 eingebracht hat. Kommt mit einer neuen Fassung von
# fastapi ein weiterer mittelbarer Abhaengiger dazu, faellt er hier auf.
AUSGESUCHT = ["fastapi", "uvicorn", "sqlmodel", "httpx", "pydantic",
              "cryptography", "python-multipart", "starlette"]
EXTRAS = {"uvicorn": {"standard"}}


def huelle():
    """
    Alle Pakete, die von den ausgesuchten aus erreichbar sind.

    Gibt (namen, nicht_lesbar) zurueck. Was nicht installiert ist, kann
    nicht befragt werden - das wird gemeldet statt uebergangen, sonst
    bestuende die Pruefung auf einem Rechner ohne Backend vollstaendig
    und haette nichts angesehen.
    """
    gesehen, unlesbar = set(), set()

    def besuche(name, extras):
        norm = name.lower().replace("_", "-").replace(".", "-")
        if norm in gesehen:
            return
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            unlesbar.add(norm)
            return
        gesehen.add(norm)
        for roh in (dist.requires or []):
            req = Requirement(roh)
            if req.marker is not None and not any(
                    req.marker.evaluate({"extra": e}) for e in (extras or {""})):
                continue
            besuche(req.name, set())

    for paket in AUSGESUCHT:
        besuche(paket, EXTRAS.get(paket) or {""})
    return gesehen, unlesbar


if Requirement is None:
    gebraucht, unlesbar = set(), set()
    print("       HINWEIS: weder 'packaging' noch die Kopie in pip gefunden - "
          "die Vollstaendigkeit der Huelle wurde NICHT geprueft "
          "(pip install packaging).")
else:
    gebraucht, unlesbar = huelle()

check("die Huelle liess sich ueberhaupt bilden",
      Requirement is None or len(gebraucht) >= 8,
      f"{len(gebraucht)} Pakete, {len(unlesbar)} nicht lesbar")
if unlesbar:
    print(f"       (nicht installiert, deshalb nicht befragt: "
          f"{', '.join(sorted(unlesbar))})")

ungepinnt = sorted(gebraucht - set(pins))
check("jedes gebrauchte Paket steht in requirements.txt", not ungepinnt,
      ungepinnt)
check("und es wurde wirklich etwas nachgesehen",
      Requirement is None or len(gebraucht) > len(AUSGESUCHT),
      f"{len(gebraucht)} gegen {len(AUSGESUCHT)} ausgesuchte")

# Die Gegenrichtung: eine Zeile, die niemand mehr braucht, gehoert raus.
# Nur melden, nicht durchfallen lassen - sie kann auch fuer eine andere
# Plattform dastehen (die Datei wird auf Python 3.13 erzeugt, gelesen
# wird sie hier unter Umstaenden mit 3.11).
ueberfluessig = sorted(set(pins) - gebraucht - unlesbar)
if ueberfluessig:
    print(f"       (steht in der Datei, wird hier aber nicht gebraucht: "
          f"{', '.join(ueberfluessig)})")

# ----------------------------------------------------------------------
# Abgleich mit dem, was tatsaechlich installiert ist
# ----------------------------------------------------------------------
# Nur pruefen, was da ist: die Reihe soll auch auf einem Rechner laufen,
# auf dem das Backend nie installiert wurde - dort ist nichts zu
# vergleichen, und ein Fehlschlag deswegen waere eine Falschmeldung.
#
# STRENG nur fuer die ausgesuchten Pakete. Die mittelbaren werden zwar
# gepinnt, aber die Datei wird auf der Zielplattform erzeugt (KK-OPS01,
# Python 3.13); wer die Reihe woanders laufen laesst, hat dort eine
# andere Aufloesung im System stehen. Eine Reihe, die deswegen rot wird,
# meldet den falschen Grund - und genau davor warnt der Kommentar hier
# schon seit der ersten Fassung.
fehlt = []
abweichend = []
nebenbei = []
for name, gewuenscht in sorted(pins.items()):
    try:
        ist = version(name)
    except PackageNotFoundError:
        fehlt.append(name)
        continue
    if ist == gewuenscht:
        continue
    hinweis = f"{name}: installiert {ist}, gepinnt {gewuenscht}"
    (abweichend if name in AUSGESUCHT else nebenbei).append(hinweis)

check("keine ausgesuchte Fassung weicht vom Pin ab", not abweichend,
      "; ".join(abweichend))

if nebenbei:
    print(f"       ({len(nebenbei)} mittelbare Fassung(en) weichen ab - "
          f"auf der Zielplattform gilt die Datei: {'; '.join(nebenbei)})")
if fehlt:
    print(f"       ({len(fehlt)} nicht installiert, nicht verglichen: "
          f"{', '.join(fehlt)})")
geprueft = len(pins) - len(fehlt)
check("mindestens ein Paket war zum Vergleichen da", geprueft > 0,
      f"{geprueft} von {len(pins)}")

# Und die Aufteilung selbst muss stimmen: jedes ausgesuchte Paket gehoert
# in die Datei. Ohne das koennte AUSGESUCHT auf Namen zeigen, die es gar
# nicht mehr gibt, und der strenge Teil oben liefe ins Leere.
check("jedes ausgesuchte Paket steht in der Datei",
      not [p for p in AUSGESUCHT if p not in pins],
      [p for p in AUSGESUCHT if p not in pins])
check("die Datei nennt deutlich mehr als nur die ausgesuchten",
      len(pins) > len(AUSGESUCHT) + 10, len(pins))

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

# ======================================================================
# Gleichstand mit dem Agenten (F-61)
# ======================================================================
# Fuenf Pakete stehen in beiden requirements.txt. Bis 0.37.19 wichen drei
# davon voneinander ab - cryptography sogar so, dass Backend und Agent
# verschiedene eingebaute OpenSSL-Fassungen ausgeliefert haben (4.0.1
# gegen 4.0.2). Niemand hat je verglichen, weil niemand hingesehen hat.
#
# Zwei Staende derselben Bibliothek in einem Produkt sind fuer sich kein
# Befund. Aber es heisst, dass die Frage "was liefern wir eigentlich aus"
# zwei verschiedene Antworten hat, je nachdem wen man fragt.
print()
print("--- Backend und Agent pinnen dieselben Pakete gleich ---")

AGENT_REQ = WURZEL / "agent" / "requirements.txt"
check("agent/requirements.txt ist lesbar", AGENT_REQ.is_file(), AGENT_REQ)

_agent = {}
_roh = re.sub(r"\\\s*\n\s*--hash=\S+", "", AGENT_REQ.read_text(encoding="utf-8"))
for _z in _roh.splitlines():
    _m = ZEILE.match(_z.split("#", 1)[0].strip())
    if _m:
        _agent[_m.group(1).lower().replace("_", "-")] = _m.group(3)

check("die Agent-Datei liess sich einlesen", len(_agent) >= 8, len(_agent))
_gemeinsam = sorted(set(_agent) & set(pins))
check("es gibt ueberhaupt gemeinsame Pakete", len(_gemeinsam) >= 4, _gemeinsam)

_ungleich = [f"{n}: Backend {pins[n]}, Agent {_agent[n]}"
             for n in _gemeinsam if pins[n] != _agent[n]]
check("gemeinsame Pakete stehen in derselben Fassung", not _ungleich,
      "; ".join(_ungleich))

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
