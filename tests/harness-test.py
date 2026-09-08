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


# Der eigene Python-Pfad, wie ihn eine Shell versteht.
#
# Unter Git Bash steht in sys.executable ein Pfad mit Backslashes
# (C:\Projekte\...). In ein sh-Skript geschrieben sind das
# Fluchtzeichen, die Attrappe startet nicht - und ein Test, der die
# Attrappe nicht starten kann, prueft etwas anderes als gedacht. Am
# 2026-09-08 auf KK-LENOVO genau so passiert.
PY_SH = Path(sys.executable).as_posix()


def lauf(umgebung, args=(), skript=None):
    """Startet run-tests.sh und gibt Rueckgabewert samt Ausgabe zurueck."""
    proc = subprocess.run(
        ["bash", str(skript or SKRIPT), *args],
        capture_output=True, text=True, timeout=120,
        cwd=str(HIER.parent), env=umgebung,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def abseits(ordner: Path) -> Path:
    """
    Eine Kopie von run-tests.sh in einem eigenen Ordner.

    Das Skript sucht die Arbeitsumgebung des Projekts neben sich. Wer
    pruefen will, was ohne sie passiert, muss das Skript woanders
    hinlegen - sonst rettet auf einem Entwicklerrechner die vorhandene
    .venv jeden Fall, den der Test herstellen wollte.
    """
    kopie = ordner / "run-tests.sh"
    kopie.write_text(SKRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    return kopie


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

    code, ausgabe = lauf(umgebung, skript=abseits(bin_ordner))

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
# Python 3 da, aber ohne die Pakete des Backends
# ----------------------------------------------------------------------
# Am 2026-09-08 auf KK-LENOVO passiert: ein frisch installiertes
# Python 3.14 lag im Pfad vor der Arbeitsumgebung des Projekts. Es ist
# unzweifelhaft Python 3 - und hat kein fastapi. Das Geruest startete
# damit das Backend und meldete "Backend nicht erreichbar", mit einem
# Stapelauszug als einzigem Hinweis. Wieder der falsche Grund.
def ohne_pakete(ordner: Path):
    """
    Attrappen, die sich wie ein Python ohne die Pakete verhalten: die
    Frage nach der Version beantworten sie, die nach fastapi nicht.
    """
    for name in ("python3", "python", "py"):
        p = ordner / name
        p.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in *fastapi*) exit 1;; esac\n"
            f"exec '{PY_SH}' \"$@\"\n",
            encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


with tempfile.TemporaryDirectory(prefix="co37-harness-") as tmp:
    bin_ordner = Path(tmp)
    ohne_pakete(bin_ordner)

    umgebung = dict(os.environ)
    umgebung["PATH"] = f"{bin_ordner}{os.pathsep}{umgebung.get('PATH', '')}"
    umgebung.pop("CO37_PYTHON", None)
    # Damit die Arbeitsumgebung des Projekts, falls es sie auf diesem
    # Rechner gibt, den Fall nicht rettet: das Skript sucht sie neben
    # sich, und hier liegt es woanders.
    code, ausgabe = lauf(umgebung, skript=abseits(bin_ordner))

    check("Python ohne die Pakete bricht ab", code != 0, code)
    check("und nennt die fehlenden Pakete",
          "ohne die Pakete des Backends" in ausgabe, ausgabe.strip()[:200])
    check("und sagt, wie man die Arbeitsumgebung anlegt",
          "-m venv .venv" in ausgabe, ausgabe.strip()[:200])
    check("und behauptet nicht, es sei gar kein Python da",
          "Kein Python 3 gefunden" not in ausgabe, ausgabe.strip()[:200])
    check("und startet kein Backend, das dann nicht erreichbar ist",
          "Backend nicht erreichbar" not in ausgabe, ausgabe.strip()[:200])

# ----------------------------------------------------------------------
# Die Arbeitsumgebung des Projekts geht dem Pfad vor
# ----------------------------------------------------------------------
# Sonst entscheidet die Reihenfolge im PATH darueber, womit geprueft
# wird - und die aendert sich, ohne dass jemand etwas an CO-37 tut.
#
# Geprueft an einer Kopie des Skripts in einem eigenen Ordner: das Skript
# sucht die Arbeitsumgebung neben sich, die echte im Projekt bleibt damit
# aussen vor.
with tempfile.TemporaryDirectory(prefix="co37-harness-") as tmp:
    ordner = Path(tmp)
    kopie = abseits(ordner)

    venv_bin = ordner / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    marke = ordner / "genommen.txt"
    fake = venv_bin / "python"
    fake.write_text(f"#!/bin/sh\ntouch '{marke}'\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    umgebung = dict(os.environ)
    umgebung.pop("CO37_PYTHON", None)
    subprocess.run(["bash", str(kopie)], capture_output=True, text=True,
                   timeout=120, cwd=str(HIER.parent), env=umgebung)

    check("die Arbeitsumgebung des Projekts wird dem PATH vorgezogen",
          marke.exists())

# ----------------------------------------------------------------------
# Ein gesetztes CO37_PYTHON wird nicht stillschweigend uebergangen
# ----------------------------------------------------------------------
# Wer die Reihe eigens auf ein bestimmtes Python richtet, will wissen,
# wenn es nicht taugt - und nicht, dass sie mit einem anderen durchlaeuft
# und gruen meldet.
with tempfile.TemporaryDirectory(prefix="co37-harness-") as tmp:
    ordner = Path(tmp)
    untauglich = ordner / "python-ohne-pakete"
    untauglich.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *fastapi*) exit 1;; esac\n"
        f"exec '{PY_SH}' \"$@\"\n", encoding="utf-8")
    untauglich.chmod(untauglich.stat().st_mode | stat.S_IEXEC | stat.S_IXOTH)

    umgebung = dict(os.environ)
    umgebung["CO37_PYTHON"] = str(untauglich)
    code, ausgabe = lauf(umgebung)

    check("untaugliches CO37_PYTHON bricht ab", code != 0, code)
    check("und wird beim Namen genannt",
          "CO37_PYTHON zeigt auf" in ausgabe and str(untauglich) in ausgabe,
          ausgabe.strip()[:200])

# ----------------------------------------------------------------------
# Welcher Stapelauszug im Protokoll zaehlt
# ----------------------------------------------------------------------
# Unter Windows steht in JEDEM Lauf ein Auszug aus der Ereignisschleife
# von Python im Protokoll: der Client schliesst die Verbindung, bevor der
# Server sie zurueckbaut. Er sagt nichts ueber CO-37. Bliebe er drin,
# meldete die Reihe bei jedem Lauf eine Warnung - und eine Warnung, die
# immer da ist, bringt einem das Wegsehen bei.
#
# Die Gegenprobe steht gleich daneben: derselbe Fehler, aber aus CO-37,
# muss weiterhin gemeldet werden. Sonst waere aus dem Ausblenden des
# Rauschens ein Ausblenden von Befunden geworden.
ZAEHLER = HIER / "log-tracebacks.py"

WINDOWS_RAUSCHEN = """INFO:     127.0.0.1:54360 - "GET /api/health HTTP/1.1" 200 OK
Traceback (most recent call last):
  File "C:\\\\Python314\\\\Lib\\\\asyncio\\\\events.py", line 94, in _run
    self._context.run(self._callback, *self._args)
  File "C:\\\\Python314\\\\Lib\\\\asyncio\\\\proactor_events.py", line 165, in _call_connection_lost
    self._sock.shutdown(socket.SHUT_RDWR)
ConnectionResetError: [WinError 10054] Eine vorhandene Verbindung wurde vom Remotehost geschlossen
INFO:     127.0.0.1:54361 - "GET /api/v1/hosts HTTP/1.1" 200 OK
"""

ECHTER_FEHLER = """Traceback (most recent call last):
  File "/opt/co37/backend/main.py", line 1234, in host_liste
    return baue_antwort(hosts)
KeyError: 'agent_token_hash'
"""

# Derselbe Fehlertyp, aber aus CO-37 statt aus der Ereignisschleife.
ECHTER_RESET = """Traceback (most recent call last):
  File "/opt/co37/backend/checkmk.py", line 88, in hole_hosts
    antwort = sitzung.get(url)
ConnectionResetError: [Errno 104] Connection reset by peer
"""


def zaehle(text: str):
    with tempfile.TemporaryDirectory(prefix="co37-tb-") as tmp:
        pfad = Path(tmp) / "backend.log"
        pfad.write_text(text, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ZAEHLER), str(pfad)],
                              capture_output=True, text=True, timeout=60)
        zeilen = (proc.stdout or "").splitlines()
        return int(zeilen[0]) if zeilen else -1, "\n".join(zeilen[1:])


anzahl, _ = zaehle(WINDOWS_RAUSCHEN)
check("der Auszug der Ereignisschleife zaehlt nicht", anzahl == 0, anzahl)

anzahl, block = zaehle(ECHTER_FEHLER)
check("ein echter Auszug zaehlt", anzahl == 1, anzahl)
check("und wird auch ausgegeben", "KeyError" in block, block[:120])

anzahl, block = zaehle(ECHTER_RESET)
check("derselbe Fehlertyp aus CO-37 zaehlt weiterhin", anzahl == 1, anzahl)
check("und der Filter greift nicht schon beim Fehlernamen",
      "checkmk.py" in block, block[:120])

anzahl, block = zaehle(WINDOWS_RAUSCHEN + ECHTER_FEHLER)
check("aus Rauschen und Befund bleibt der Befund", anzahl == 1, anzahl)
check("und zwar der richtige",
      "KeyError" in block and "10054" not in block, block[:160])

# Ein Auszug endet an seiner Ausnahmezeile. Endete er nicht dort, zoege
# er die folgenden Protokollzeilen mit hinein - und stuende darin
# zufaellig das Muster des Rauschens, verschwaende ein echter Befund.
anzahl, block = zaehle(
    ECHTER_FEHLER
    + 'INFO:     127.0.0.1:1 - "GET /api/health HTTP/1.1" 200 OK\n'
    + "INFO:     proactor_events meldete WinError 10054\n")
check("ein Auszug endet an seiner Ausnahmezeile", anzahl == 1, anzahl)
check("und zieht die folgenden Protokollzeilen nicht mit hinein",
      "INFO:" not in block, block[:200])

anzahl, _ = zaehle("nichts besonderes\nINFO: alles gut\n")
check("ein sauberes Protokoll ergibt null", anzahl == 0, anzahl)

# Die Verdrahtung selbst: run-tests.sh muss den Zaehler wirklich
# aufrufen und muss merken, wenn dabei etwas schiefgeht. Ein Zaehler, der
# im Verborgenen scheitert und dabei "null" meldet, waere schlimmer als
# gar keiner.
# Die Kopie liegt diesmal IM Projekt, nicht daneben: dieser Lauf soll
# durchkommen, und run-tests.sh sucht tests/ neben sich.
kaputt_pfad = SKRIPT.parent / "run-tests-kaputter-zaehler.sh"
try:
    kaputt_pfad.write_text(
        SKRIPT.read_text(encoding="utf-8").replace(
            "tests/log-tracebacks.py", "tests/gibt-es-nicht.py"),
        encoding="utf-8")

    # Eigener Port: der Lauf muss bis zum Ende durchkommen, sonst kaeme
    # er an der Auszugszaehlung gar nicht an. 8099 ist belegt, wenn
    # diese Reihe aus der Gesamtreihe heraus laeuft.
    frei = socket.socket()
    frei.bind(("127.0.0.1", 0))
    port = frei.getsockname()[1]
    frei.close()

    umgebung = dict(os.environ)
    umgebung["CO37_TEST_PORT"] = str(port)
    code, ausgabe = lauf(umgebung, args=("deps",), skript=kaputt_pfad)
    check("ein kaputter Auszugszaehler faellt auf",
          "Auszugszaehlung lief nicht" in ausgabe, ausgabe.strip()[-200:])
    check("und der Lauf gilt dann nicht als bestanden", code != 0, code)
finally:
    kaputt_pfad.unlink(missing_ok=True)

leer_code = subprocess.run(
    [sys.executable, str(ZAEHLER), str(HIER / "gibt-es-nicht.log")],
    capture_output=True, text=True, timeout=60)
check("eine fehlende Protokolldatei ergibt null, nicht einen Absturz",
      leer_code.returncode == 0 and leer_code.stdout.strip() == "0",
      (leer_code.returncode, leer_code.stdout.strip()[:60]))

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
