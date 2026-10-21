"""
CO-37 - ein laufender Auftrag, zu dem nichts mehr kommt.

DER ANLASS

Am 2026-10-21 lief auf KK-PVE02 ein Patchlauf sauber durch: dpkg meldete
um 06:58:20 alle Pakete als installiert. In derselben Minute war das
Backend fuer rund fuenf Minuten nicht erreichbar - es patchte sich
selbst. Die Schlussmeldung des Agenten lief in einen Verbindungsfehler,
und der Agent puffert sie nicht: api() hat keinen Wiederholungsversuch,
die Hauptschleife schreibt "Verbindungsfehler" ins Journal und schlaeft
weiter. Das Ergebnis war damit weg.

Serverseitig blieb der Auftrag auf running stehen - sichtbar als
laufender Patchlauf in der Oberflaeche, obwohl auf dem Host nichts mehr
lief. Aufgeraeumt haette ihn erst die Hoechstlaufzeit, also vier Stunden
nach dem Start, und bis dahin blockiert er jeden weiteren Auftrag
desselben Typs fuer diesen Host.

WAS DIESE REIHE TUT

Sie prueft job_verstummt() - die Regel, die so einen Auftrag erkennt -
mit vorgespulter Zeit. Direkt gegen die Funktion, nicht ueber HTTP: ueber
die Schnittstelle liesse sich eine halbe Stunde Stille nur durch
Warten herstellen, und eine Reihe, die eine halbe Stunde wartet, laeuft
niemand.

Geprueft werden beide Ausnahmen mit, denn beide wuerden sonst das
Gegenteil anrichten - den Abbruch eines Auftrags, der noch laeuft:

  - gekuerztes Protokoll: der Agent stellt das Senden dann GANZ ein
    (LogSink.stopped), die Stille sagt nichts mehr ueber den Lauf
  - Neustart des Backends: war es selbst weg, konnte niemand liefern

Braucht kein Backend und kein Netz.

    python3 tests/jobstille-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/jobstille.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402
from models import Job, JobType  # noqa: E402
from utctime import utcnow  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


JETZT = utcnow()

# Das Backend laeuft seit gestern - die Schranke aus BACKEND_START ist in
# den ersten Faellen also nicht im Weg. Sie bekommt ihren eigenen Block
# weiter unten.
main.BACKEND_START = JETZT - timedelta(days=1)

STILLE = main.JOB_LOG_SILENCE


def auftrag(job_type=JobType.patch, gestartet_vor=None, regung_vor=None,
            params=None):
    """
    Ein laufender Auftrag, wie ihn der Heartbeat vorfindet.

    Ohne Datenbank: job_verstummt() liest nur Felder, und eine Instanz
    ohne Sitzung verhaelt sich dabei wie eine geladene Zeile.
    """
    return Job(
        host_id=1,
        job_type=job_type,
        params=params or {},
        started_at=JETZT - (gestartet_vor if gestartet_vor is not None
                            else timedelta(hours=1)),
        last_progress_at=(None if regung_vor is None
                          else JETZT - regung_vor),
    )


# -------------------------------------------------- die Regel selbst
print("--- Stille waehrend der Host sich meldet ---")

check("frische Ausgabe -> laeuft",
      main.job_verstummt(auftrag(regung_vor=timedelta(seconds=30)),
                         JETZT) is False)

check("Ausgabe kurz vor der Grenze -> laeuft",
      main.job_verstummt(auftrag(gestartet_vor=STILLE * 2,
                                 regung_vor=STILLE - timedelta(minutes=1)),
                         JETZT) is False)

check("Ausgabe genau auf der Grenze -> laeuft noch",
      main.job_verstummt(auftrag(gestartet_vor=STILLE * 2,
                                 regung_vor=STILLE),
                         JETZT) is False)

check("Ausgabe jenseits der Grenze -> verstummt",
      main.job_verstummt(auftrag(gestartet_vor=STILLE * 2,
                                 regung_vor=STILLE + timedelta(minutes=1)),
                         JETZT) is True)

# Genau der Fall vom 2026-10-21: der Agent hat gemeldet, bis die
# Verbindung wegbrach, und danach nie wieder.
check("der Fall KK-PVE02 wird erkannt",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(hours=3),
                                 regung_vor=timedelta(hours=3)),
                         JETZT) is True)

# ------------------------------------------- Altbestand ohne Lebenszeichen
# Auftraege aus der Zeit vor Schema 24 haben die Spalte nicht gefuellt.
# Dann gilt der Start - sonst waeren sie von der Regel gar nicht erfasst
# und blieben wieder bis zur Hoechstlaufzeit stehen.
print("--- ohne Lebenszeichen zaehlt der Start ---")

check("kein Lebenszeichen, gerade erst gestartet -> laeuft",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(minutes=2)),
                         JETZT) is False)

check("kein Lebenszeichen, lange gestartet -> verstummt",
      main.job_verstummt(auftrag(gestartet_vor=STILLE + timedelta(minutes=1)),
                         JETZT) is True)

# Ohne beides kann die Regel nichts sagen. Im Heartbeat kommt das nicht
# vor - die Abfrage dort verlangt started_at -, hier schon.
check("weder Start noch Lebenszeichen -> keine Aussage",
      main.job_verstummt(Job(host_id=1, job_type=JobType.patch, params={}),
                         JETZT) is False)

# ------------------------------------------------- Ausnahme 1: gekuerzt
print("--- gekuerztes Protokoll ist von der Regel ausgenommen ---")

check("gekuerzt und still -> laeuft weiter",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(hours=3),
                                 regung_vor=timedelta(hours=3),
                                 params={"log_truncated": True}),
                         JETZT) is False)

# Der Vermerk muss ein echtes Ja sein. Stuende dort etwas Beliebiges,
# koennte ein Agent sich mit einem Fortschrittstext aus der Regel
# nehmen - params schreibt er ueber /agent/job-log mit.
check("ein anderer Eintrag in params nimmt nicht aus",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(hours=3),
                                 regung_vor=timedelta(hours=3),
                                 params={"progress": "Installiere"}),
                         JETZT) is True)

# ----------------------------------------- Ausnahme 2: Backend-Neustart
print("--- nach einem Neustart des Backends zaehlt dessen Start ---")

main.BACKEND_START = JETZT - timedelta(minutes=1)
check("Backend gerade gestartet, Auftrag lange still -> laeuft",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(hours=3),
                                 regung_vor=timedelta(hours=3)),
                         JETZT) is False)

main.BACKEND_START = JETZT - (STILLE + timedelta(minutes=1))
check("Backend laeuft laenger als die Grenze -> Stille zaehlt wieder",
      main.job_verstummt(auftrag(gestartet_vor=timedelta(hours=3),
                                 regung_vor=timedelta(hours=3)),
                         JETZT) is True)

# --------------------------------------------------- Zusammenspiel
# Die Regel steht NEBEN der Hoechstlaufzeit, sie ersetzt sie nicht: ein
# gekuerzter Auftrag faellt weiter unter JOB_MAX_RUNTIME, und das ist
# die einzige Schranke, die ihm dann bleibt.
print("--- die Hoechstlaufzeit bleibt bestehen ---")

check("fuer Patchlaeufe gilt weiterhin eine Hoechstlaufzeit",
      main.JOB_MAX_RUNTIME.get(JobType.patch) is not None,
      main.JOB_MAX_RUNTIME.get(JobType.patch))
check("und sie ist groesser als die Stillegrenze",
      main.JOB_MAX_RUNTIME[JobType.patch] > STILLE,
      (main.JOB_MAX_RUNTIME[JobType.patch], STILLE))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
