"""
CO-37 - Sagt das Testgeruest den richtigen Grund, wenn es nicht laufen kann?

Grund fuer diese Reihe: run-tests.sh rief fest 'python3' auf. Unter Windows
gibt es das nicht, dort faengt ein Platzhalter des Microsoft Store den
Aufruf ab und scheitert. Der Aufruf steckte im Portcheck, und jeder
Fehlschlag dort wurde zu "Port belegt" gemeldet - obwohl der Port frei war.
Daraufhin wurde einmal ungetestet gebaut, eingecheckt und ausgeliefert.

Ein Geruest, das den falschen Grund nennt, ist schlimmer als keins: es
sieht aus wie ein Befund und schickt einen in die falsche Richtung.

Geprueft werden beide Richtungen. Der falsche Grund darf nicht mehr
kommen, der richtige muss weiterhin kommen - sonst waere nur ein Fehler
durch den anderen ersetzt.

Laeuft ohne Backend: run-tests.sh bricht in beiden Faellen ab, bevor es
etwas startet.

Aufruf:

    python3 tests/harness-test.py
"""
import os
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
SKRIPT = HIER.parent / "run-tests.sh"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def lauf(umgebung, args=()):
    """Startet run-tests.sh und gibt Rueckgabewert samt Ausgabe zurueck."""
    proc = subprocess.run(
        ["bash", str(SKRIPT), *args],
        capture_output=True, text=True, timeout=120,
        cwd=str(HIER.parent), env=umgebung,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def store_attrappen(ordner: Path):
    """
    Legt Attrappen fuer python3, python und py an, die sich wie der
    Platzhalter des Microsoft Store verhalten: Hinweis ausgeben, scheitern.

    Alle drei Namen, damit die Suche in run-tests.sh nicht doch noch ein
    echtes Python weiter hinten im Pfad findet. Sonst haenge der Ausgang
    des Tests davon ab, was auf dem Rechner zufaellig installiert ist.
    """
    for name in ("python3", "python", "py"):
        p = ordner / name
        p.write_text(
            "#!/bin/sh\n"
            "echo 'Python wurde nicht gefunden; ohne Argumente ausfuehren,"
            " um aus dem Microsoft Store zu installieren.' >&2\n"
            "exit 9009\n",
            encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ----------------------------------------------------------------------
# Kein Python zu finden
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-harness-") as tmp:
    bin_ordner = Path(tmp)
    store_attrappen(bin_ordner)

    umgebung = dict(os.environ)
    umgebung["PATH"] = f"{bin_ordner}{os.pathsep}{umgebung.get('PATH', '')}"
    umgebung.pop("CO37_PYTHON", None)

    code, ausgabe = lauf(umgebung)

    check("ohne Python 3 bricht das Geruest ab", code != 0, code)
    check("und nennt den wahren Grund",
          "Kein Python 3 gefunden" in ausgabe, ausgabe.strip()[:120])
    check("und behauptet nicht, der Port sei belegt",
          "belegt" not in ausgabe, ausgabe.strip()[:120])
    check("und nennt, was versucht wurde",
          "python3, python, py" in ausgabe, ausgabe.strip()[:120])

# ----------------------------------------------------------------------
# Eigener Pfad ueber CO37_PYTHON
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-harness-") as tmp:
    bin_ordner = Path(tmp)
    store_attrappen(bin_ordner)

    umgebung = dict(os.environ)
    umgebung["PATH"] = f"{bin_ordner}{os.pathsep}{umgebung.get('PATH', '')}"
    # Der laufende Python ist per Definition ein brauchbarer Python 3.
    umgebung["CO37_PYTHON"] = sys.executable
    # Port bewusst belegen, damit der Lauf gleich danach abbricht und
    # kein Backend startet. Geprueft wird nur, dass er ueber die
    # Python-Suche hinauskommt.
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    umgebung["CO37_TEST_PORT"] = str(s.getsockname()[1])

    code, ausgabe = lauf(umgebung)
    s.close()

    check("CO37_PYTHON wird angenommen, obwohl python3 im Pfad kaputt ist",
          "Kein Python 3 gefunden" not in ausgabe, ausgabe.strip()[:120])

# ----------------------------------------------------------------------
# Der richtige Grund muss weiterhin kommen
# ----------------------------------------------------------------------
# Sonst waere nur ein irrefuehrender Abbruch durch einen anderen ersetzt.
# Kein SO_REUSEADDR unter Windows. Dort heisst die Option "ein anderer
# darf mir den Port wegnehmen", und genau das tat der Portcheck dann
# auch - er band den belegten Port erfolgreich und meldete ihn als frei.
# Die Begruendung steht ausfuehrlich in tests/port-frei.py.
s = socket.socket()
if not hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 0))
s.listen(1)
belegt = s.getsockname()[1]

umgebung = dict(os.environ)
umgebung["CO37_TEST_PORT"] = str(belegt)
code, ausgabe = lauf(umgebung)
s.close()

check("belegter Port bricht ab", code != 0, code)
check("belegter Port wird auch so genannt",
      f"Port {belegt} ist belegt" in ausgabe, ausgabe.strip()[:120])
check("und wird nicht als fehlendes Python ausgegeben",
      "Kein Python 3 gefunden" not in ausgabe, ausgabe.strip()[:120])

# ----------------------------------------------------------------------
# Kein fest verdrahtetes python3 mehr in den Aufrufen
# ----------------------------------------------------------------------
# Bewusst nur die ausgefuehrten Zeilen, nicht die Kommentare: dort steht
# 'python3' zu Recht, weil die Begruendung davon handelt.
quelltext = SKRIPT.read_text(encoding="utf-8").splitlines()
zeilen = [z for z in quelltext if z.strip() and not z.strip().startswith("#")]
fest = [z.strip() for z in zeilen
        if "python3" in z and "for kandidat in" not in z and "echo" not in z]
check("kein fest verdrahtetes python3 mehr im Ablauf", not fest, fest[:2])

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
