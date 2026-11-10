"""
CO-37 - was der Heartbeat an Auftraegen aufraeumt.

DER ANLASS

Am 2026-11-10 wurde gemessen, ob die Pruefreihen den Umbau von
auftraege_aufraeumen() ueberhaupt gesichert haetten. Vier Mutationen, je
gegen den vollen Satz von 2264 Pruefungen:

  V1 Verfall abgeschaltet        -> 2264 gruen, NICHTS faellt darauf
  V2 Boot-Zeit abgeschaltet      -> agent-api rot
  V3 Stille abgeschaltet         -> 2264 gruen, NICHTS faellt darauf
  V4 Hoechstlaufzeit abgeschaltet-> 2264 gruen, NICHTS faellt darauf

Drei von vier Mechanismen waren ungeprueft. Dass jobstille existiert,
deckt V3 nicht ab: die Reihe prueft job_verstummt() als Funktion, nicht
die Schleife, die sie benutzt und die Auftraege tatsaechlich abbricht.

Dass die ROUTE die Pflege aufruft, ist bereits belegt - agent-api
spricht nur HTTP und wurde unter V2 rot. Diese Reihe braucht deshalb
keine HTTP-Fixtur, nur die Mechanismen.

WAS SIE TUT

Direkt gegen scheduling.auftraege_aufraeumen(), mit eigener Datenbank und
vorgespulter Zeit. Kein Backend, kein Netz, keine Wartezeit - eine Reihe,
die eine halbe Stunde auf Stille wartet, laeuft niemand.

JE FALL EIN EIGENER HOST. Die Funktion arbeitet auf ALLEN Auftraegen des
Hosts, den sie bekommt; zwei Faelle auf demselben Host wuerden sich
gegenseitig abraeumen.

ZWEI DINGE, DIE SONST STILL GRUEN WERDEN

1. BACKEND_START wird vorgespult. job_verstummt() nimmt laufende
   Auftraege aus, solange das Backend selbst gerade erst gestartet ist -
   sonst raeumte der erste Heartbeat nach einem Backend-Neustart alles
   ab. Ohne das Vorspulen greift diese Ausnahme hier, der Abbruch bleibt
   aus, und die Stille-Pruefung waere gruen, ohne die Stille zu messen.

   Gesetzt wird auf scheduling, NICHT auf main: main re-exportiert den
   Wert absichtlich nicht (F-12/F-14, siehe
   tests/scheduling-export-test.py).

2. Zu jedem Abbruch gehoert der Gegenfall. Ohne ihn waere eine Mutation,
   die einfach ALLES abbricht, gruen - und das waere schlimmer als gar
   keine Pruefung: ein Patchlauf, der laeuft, waere dann weg.

Braucht kein Backend und kein Netz.

    python3 tests/auftragspflege-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/auftragspflege.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main        # noqa: E402
import scheduling  # noqa: E402
from models import ApprovalState, Host, Job, JobState, JobType  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from utctime import utcnow  # noqa: E402

# Die Tabellen legt sonst der Start des Dienstes an. Der laeuft hier nicht.
SQLModel.metadata.create_all(main.engine)

JETZT = utcnow().replace(microsecond=0)

# Siehe Punkt 1 im Kopf dieser Datei.
scheduling.BACKEND_START = JETZT - timedelta(days=1)

STILLE = scheduling.JOB_LOG_SILENCE
GRENZE_PATCH = scheduling.JOB_MAX_RUNTIME[JobType.patch]

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}"
          f"{'  -> ' + str(extra) if extra else ''}")


class Meldung:
    """
    Der Platzhalter fuer die Nutzlast des Heartbeats.

    auftraege_aufraeumen() liest daraus NUR boot_time - nachgesehen, nicht
    vermutet. Die echte Klasse AgentHeartbeat steht in main.py, und sie
    hier zu benutzen hiesse, die Reihe an etwas zu binden, das die
    Funktion gar nicht braucht.
    """

    def __init__(self, boot_time=None):
        self.boot_time = boot_time


def host_anlegen(name):
    h = Host(hostname=name, os_type="linux",
             approval_state=ApprovalState.approved,
             agent_token_hash=os.urandom(8).hex())
    with Session(main.engine) as s:
        s.add(h)
        s.commit()
        s.refresh(h)
        return h.id


def auftrag_anlegen(host_id, job_type=JobType.patch, state=JobState.pending,
                    gestartet_vor=None, regung_vor=None, verfaellt_in=None,
                    erstellt_vor=None, params=None):
    with Session(main.engine) as s:
        j = Job(
            host_id=host_id,
            job_type=job_type,
            state=state,
            params=params or {},
            created_at=JETZT - (erstellt_vor or timedelta(minutes=5)),
            started_at=(None if gestartet_vor is None
                        else JETZT - gestartet_vor),
            last_progress_at=(None if regung_vor is None
                              else JETZT - regung_vor),
            expires_at=(None if verfaellt_in is None
                        else JETZT + verfaellt_in),
        )
        s.add(j)
        s.commit()
        s.refresh(j)
        return j.id


def aufraeumen(host_id, boot_time=None):
    """Ruft die Pflege genau so auf, wie die Route es tut."""
    with Session(main.engine) as s:
        host = s.get(Host, host_id)
        scheduling.auftraege_aufraeumen(s, host, Meldung(boot_time), JETZT)
        s.commit()


def auftrag(job_id):
    with Session(main.engine) as s:
        return s.get(Job, job_id)


# ------------------------------------------------- 1. Verfall
print("--- ein wartender Auftrag verfaellt ---")

h1 = host_anlegen("PFLEGE-01")
# Seit acht Tagen wartend, Frist war sieben Tage.
j_patch = auftrag_anlegen(h1, JobType.patch, JobState.pending,
                          erstellt_vor=timedelta(days=8),
                          verfaellt_in=-timedelta(hours=1))
aufraeumen(h1)
jp = auftrag(j_patch)

check("der verfallene Patch-Auftrag ist abgebrochen",
      jp.state == JobState.cancelled, jp.state)
check("und traegt einen Endzeitpunkt", jp.finished_at is not None)
# Der Grund MUSS der der Frist sein, nicht der des Nachlaufs. Genau
# dafuer hat verfallsgrund() zwei Zweige - ein Patch-Auftrag hat kein
# grace_minutes, und der Nachlauf-Text meldete sonst "None Minuten".
check("der Grund nennt die Frist, nicht den Nachlauf",
      "Frist von" in (jp.error or ""), jp.error)

h2 = host_anlegen("PFLEGE-02")
j_boot = auftrag_anlegen(h2, JobType.reboot, JobState.pending,
                         erstellt_vor=timedelta(hours=2),
                         verfaellt_in=-timedelta(hours=1),
                         params={"grace_minutes": 30})
aufraeumen(h2)
jb = auftrag(j_boot)
check("ein verfallener Neustart nennt dagegen den Nachlauf",
      "Nachlauf 30 Minuten" in (jb.error or ""), jb.error)

# ------------------------------------- 2. Was NICHT verfallen darf
print("--- was nicht verfallen darf, bleibt wartend ---")

h3 = host_anlegen("PFLEGE-03")
j_spaeter = auftrag_anlegen(h3, JobType.patch, JobState.pending,
                            verfaellt_in=timedelta(days=3))
j_ohne = auftrag_anlegen(h3, JobType.patch, JobState.pending,
                         verfaellt_in=None)
aufraeumen(h3)
check("ein Auftrag mit Frist in der Zukunft bleibt wartend",
      auftrag(j_spaeter).state == JobState.pending,
      auftrag(j_spaeter).state)
check("ein Auftrag ohne Frist bleibt wartend",
      auftrag(j_ohne).state == JobState.pending, auftrag(j_ohne).state)

# ------------------------------------------------- 3. Stille
print("--- ein laufender Auftrag, zu dem nichts mehr kommt ---")

h4 = host_anlegen("PFLEGE-04")
j_still = auftrag_anlegen(h4, JobType.patch, JobState.running,
                          gestartet_vor=timedelta(hours=1),
                          regung_vor=STILLE + timedelta(minutes=1))
aufraeumen(h4)
js = auftrag(j_still)
check("der verstummte Auftrag ist abgebrochen",
      js.state == JobState.cancelled, js.state)
check("der Grund nennt die fehlende Ausgabe",
      "keine Ausgabe mehr" in (js.error or ""), js.error)
# Der Vermerk ist der Unterschied, auf den es ankommt: eine Meldung, die
# danach doch noch eintrifft, darf einen so abgebrochenen Auftrag
# berichtigen (agent_report). Ohne den Vermerk ist der Abbruch endgueltig.
check("und params vermerkt, dass der SERVER abgebrochen hat",
      (js.params or {}).get("server_cancelled") is True, js.params)

h5 = host_anlegen("PFLEGE-05")
j_gekuerzt = auftrag_anlegen(h5, JobType.patch, JobState.running,
                             gestartet_vor=timedelta(hours=1),
                             regung_vor=STILLE + timedelta(minutes=1),
                             params={"log_truncated": True})
aufraeumen(h5)
check("mit gekuerztem Protokoll laeuft er weiter",
      auftrag(j_gekuerzt).state == JobState.running,
      auftrag(j_gekuerzt).state)

# ------------------------------------------ 4. Hoechstlaufzeit
print("--- ein Auftrag laeuft ueber seine Hoechstlaufzeit ---")

h6 = host_anlegen("PFLEGE-06")
# Ausgabe frisch, damit NICHT die Stilleregel greift, sondern die Grenze.
j_lang = auftrag_anlegen(h6, JobType.patch, JobState.running,
                         gestartet_vor=GRENZE_PATCH + timedelta(hours=1),
                         regung_vor=timedelta(minutes=1))
aufraeumen(h6)
jl = auftrag(j_lang)
check("der zu lange laufende Auftrag ist abgebrochen",
      jl.state == JobState.cancelled, jl.state)
check("der Grund nennt die fehlende Rueckmeldung und die Grenze",
      "ohne Rueckmeldung" in (jl.error or ""), jl.error)

h7 = host_anlegen("PFLEGE-07")
j_gesund = auftrag_anlegen(h7, JobType.patch, JobState.running,
                           gestartet_vor=timedelta(hours=1),
                           regung_vor=timedelta(minutes=1))
aufraeumen(h7)
check("ein gesunder laufender Auftrag bleibt unberuehrt",
      auftrag(j_gesund).state == JobState.running,
      auftrag(j_gesund).state)

# --------------------------------------------------- 5. Boot-Zeit
print("--- der Host hat neu gestartet, waehrend der Auftrag lief ---")

h8 = host_anlegen("PFLEGE-08")
j_ueberrollt = auftrag_anlegen(h8, JobType.patch, JobState.running,
                               gestartet_vor=timedelta(hours=1),
                               regung_vor=timedelta(minutes=1))
aufraeumen(h8, boot_time=(JETZT - timedelta(minutes=30)).timestamp())
ju = auftrag(j_ueberrollt)
check("ein Auftrag von vor dem Neustart ist abgebrochen",
      ju.state == JobState.cancelled, ju.state)
check("der Grund nennt den Neustart",
      "neu gestartet" in (ju.error or ""), ju.error)

# Plausibilitaetsband um boot_time (F-41). Ein Agent, der einmal Unsinn
# meldet, darf nicht jeden laufenden Auftrag des Hosts abraeumen - und
# from_timestamp() wuerde bei 1e18 ausserdem einen OSError werfen.
h9 = host_anlegen("PFLEGE-09")
j_unsinn = auftrag_anlegen(h9, JobType.patch, JobState.running,
                           gestartet_vor=timedelta(hours=1),
                           regung_vor=timedelta(minutes=1))
aufraeumen(h9, boot_time=1e18)
check("eine unsinnige Boot-Zeit wird verworfen, nicht angewandt",
      auftrag(j_unsinn).state == JobState.running,
      auftrag(j_unsinn).state)

# ------------------------------------------ 6. Nur der eigene Host
print("--- die Pflege fasst nur den eigenen Host an ---")

h10 = host_anlegen("PFLEGE-10")
h11 = host_anlegen("PFLEGE-11")
j_fremd = auftrag_anlegen(h11, JobType.patch, JobState.running,
                          gestartet_vor=timedelta(hours=1),
                          regung_vor=STILLE + timedelta(minutes=1))
# Aufgeraeumt wird fuer h10 - der Auftrag von h11 waere faellig, gehoert
# aber nicht dazu.
aufraeumen(h10)
check("der verstummte Auftrag eines ANDEREN Hosts bleibt laufend",
      auftrag(j_fremd).state == JobState.running, auftrag(j_fremd).state)

# ------------------------- 7. list_jobs() schreibt denselben Grund
print("--- beide Wege nennen denselben Grund ---")

# Verfallene Auftraege raeumt NICHT nur der Heartbeat ab: main.list_jobs()
# tut es ebenfalls, damit sie nicht ewig als "wartet" in der Liste
# stehen, wenn sich der Host gar nicht mehr meldet.
#
# Welcher der beiden zuerst dran ist, entscheidet der Zufall - also muss
# derselbe Grund herauskommen. verfallsgrund() hat seine zwei Zweige
# genau dafuer; eine zweite, selbst gebaute Fassung des Textes hat sie
# nicht und meldet bei einem Patch-Auftrag "Nachlauf None Minuten".
h12 = host_anlegen("PFLEGE-12")
j_liste = auftrag_anlegen(h12, JobType.patch, JobState.pending,
                          erstellt_vor=timedelta(days=8),
                          verfaellt_in=-timedelta(hours=1))

with Session(main.engine) as s:
    erwartet = scheduling.verfallsgrund(s.get(Job, j_liste))
    main.list_jobs(host_id=h12, limit=100, session=s)
    s.commit()

jl2 = auftrag(j_liste)
check("list_jobs() bricht den verfallenen Auftrag ebenfalls ab",
      jl2.state == JobState.cancelled, jl2.state)
check("und schreibt denselben Grund wie der Heartbeat",
      jl2.error == erwartet, f"{jl2.error!r} statt {erwartet!r}")
check("insbesondere steht dort keine Nachlaufzeit 'None'",
      "None" not in (jl2.error or ""), jl2.error)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
