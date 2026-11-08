"""
CO-37 - eine Rueckmeldung, die nicht ankam, geht nicht verloren.

DER ANLASS

Am 2026-10-21 lief auf einem Proxmox-Host ein Patchlauf durch, waehrend
sich das Backend selbst patchte. Die Schlussmeldung des Agenten lief in
einen Verbindungsfehler. api() wiederholt nichts, die Hauptschleife
schrieb "Verbindungsfehler" ins Journal und schlief weiter - Log,
Ergebnis und der Neustartbedarf waren damit weg, und der Auftrag stand
serverseitig weiter auf "laeuft".

WAS DIESE REIHE TUT

Zwei Haelften, die zusammengehoeren:

  AGENT    - die Meldung wird vor dem Senden abgelegt, nach einer
             Antwort geloescht und sonst nachgereicht. Mit
             vorgespulter Zeit statt echtem Warten, und mit einem
             vorgetaeuschten api() statt eines Servers.

  BACKEND  - der Endpunkt nimmt eine Nachreichung an, wenn der SERVER
             den Auftrag abgebrochen hat, laesst aber einen Zustand
             stehen, den der Agent selbst gemeldet hat. Und er loest
             bei einer Wiederholung derselben Meldung nichts zweimal
             aus - daran haengen Syslog, die Downtime und vor allem
             der Nachschlag-Zaehler, der sonst eine Patchkette von
             vorn beginnen liesse.

Braucht kein laufendes Backend und kein Netz.

    python3 tests/nachreichung-test.py
"""
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/nachreichung.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ======================================================================
# Haelfte 1: der Agent
# ======================================================================
# Eigene Kopie samt Konfiguration in einem temporaeren Verzeichnis, wie
# in agent-selfheal-test.py: PENDING_DIR liegt neben agent.py, und die
# Reihe soll weder das Arbeitsverzeichnis noch /etc anfassen.
ATMP = Path(tempfile.mkdtemp())
shutil.copy2(WURZEL / "agent" / "agent.py", ATMP / "agent.py")
(ATMP / "agent.conf").write_text(
    "server = http://127.0.0.1:1\ntoken = test\nverify_ssl = false\n")

spec = importlib.util.spec_from_file_location("co37_agent", ATMP / "agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class FakeResponse:
    def __init__(self, code):
        self.status_code = code


def http_fehler(code):
    exc = agent.requests.HTTPError(f"HTTP {code}")
    exc.response = FakeResponse(code)
    return exc


gesendet = []


def api_laeuft(method, path, payload=None, **kw):
    gesendet.append((path, payload))
    return {"ok": True}


def api_tot(method, path, payload=None, **kw):
    raise agent.requests.ConnectionError("Server nicht erreichbar")


def api_code(code):
    def f(method, path, payload=None, **kw):
        raise http_fehler(code)
    return f


def bericht(job_id=1, state="done"):
    return {"job_id": job_id, "report_id": agent.uuid.uuid4().hex,
            "state": state, "log": "x", "error": None, "result": {},
            "reboot_required": False}


print("--- Agent: ablegen und loeschen ---")

agent.api = api_laeuft
gesendet.clear()
check("eine angekommene Meldung wird gesendet",
      agent.bericht_abliefern(bericht()) is True)
check("und hinterlaesst keine Datei", agent.pending_dateien() == [],
      agent.pending_dateien())

agent.api = api_tot
check("eine nicht angekommene Meldung meldet das zurueck",
      agent.bericht_abliefern(bericht(job_id=2867)) is False)
check("und bleibt liegen", len(agent.pending_dateien()) == 1,
      agent.pending_dateien())

print("--- Agent: nachreichen ---")

agent.api = api_tot
check("solange der Server schweigt, wird nichts zugestellt",
      agent.pending_senden() == 0)
check("die Meldung liegt weiterhin", len(agent.pending_dateien()) == 1)

agent.api = api_laeuft
gesendet.clear()
check("sobald der Server redet, geht sie raus", agent.pending_senden() == 1)
check("danach ist der Puffer leer", agent.pending_dateien() == [])
check("und es war die richtige Meldung",
      gesendet and gesendet[0][1].get("job_id") == 2867, gesendet[:1])

# Genau der Fall vom 2026-10-21: Meldung weg, ein Durchlauf spaeter da.
print("--- Agent: der Server weist ab ---")

agent.api = api_tot
agent.bericht_abliefern(bericht(job_id=4711))
agent.api = api_code(404)
check("einen Auftrag, den es nicht mehr gibt, verwirft der Agent",
      agent.pending_senden() == 0)
check("und raeumt die Datei weg", agent.pending_dateien() == [],
      agent.pending_dateien())

agent.api = api_tot
agent.bericht_abliefern(bericht(job_id=4712))
agent.api = api_code(503)
check("bei einem Serverfehler wird nichts verworfen",
      agent.pending_senden() == 0)
check("die Meldung bleibt fuer den naechsten Versuch liegen",
      len(agent.pending_dateien()) == 1)

agent.api = api_laeuft
agent.pending_senden()

print("--- Agent: Grenzen des Puffers ---")

# Unlesbares bleibt unlesbar - liegenlassen hiesse, es bei jedem
# Durchlauf erneut zu versuchen.
(agent.PENDING_DIR / "kaputt.json").write_text("{kein json", encoding="utf-8")
agent.api = api_laeuft
agent.pending_senden()
check("eine unlesbare Datei wird verworfen", agent.pending_dateien() == [],
      agent.pending_dateien())

alt = agent.PENDING_DIR / "alt.json"
alt.write_text(json.dumps(bericht()), encoding="utf-8")
zu_alt = time.time() - agent.PENDING_MAX_AGE - 60
os.utime(alt, (zu_alt, zu_alt))
agent.pending_aufraeumen()
check("eine zu alte Meldung wird verworfen", not alt.exists())

for i in range(agent.PENDING_MAX_FILES + 5):
    pfad = agent.PENDING_DIR / f"m{i:03d}.json"
    pfad.write_text(json.dumps(bericht(job_id=i)), encoding="utf-8")
    # Eigene Zeitstempel, sonst entscheidet unter Windows die Aufloesung
    # der Uhr darueber, welche Datei als die aelteste gilt.
    os.utime(pfad, (time.time() - (1000 - i), time.time() - (1000 - i)))
agent.pending_aufraeumen()
uebrig = agent.pending_dateien()
check("ueber der Mengengrenze wird gekappt",
      len(uebrig) == agent.PENDING_MAX_FILES, len(uebrig))
check("und zwar das Aelteste zuerst",
      "m000.json" not in [p.name for p in uebrig]
      and "m054.json" in [p.name for p in uebrig],
      [p.name for p in uebrig[:3]])

for pfad in agent.pending_dateien():
    pfad.unlink()


# ======================================================================
# Haelfte 2: das Backend
# ======================================================================
sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from models import (ApprovalState, Host, Job, JobState,  # noqa: E402
                    JobType, OSType)

SQLModel.metadata.create_all(main.engine)

TOKEN = "agenttoken-fuer-die-reihe"
with Session(main.engine) as s:
    wirt = Host(hostname="TEST-NACH01", os_type=OSType.linux,
                approval_state=ApprovalState.approved,
                agent_token_hash=main.hash_token(TOKEN))
    s.add(wirt)
    s.commit()
    s.refresh(wirt)
    WIRT_ID = wirt.id


def neuer_auftrag(state=JobState.running, params=None):
    with Session(main.engine) as s:
        job = Job(host_id=WIRT_ID, job_type=JobType.patch,
                  params=params or {}, state=state,
                  started_at=main.utcnow())
        if state in (JobState.done, JobState.failed, JobState.cancelled):
            job.finished_at = main.utcnow()
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def melden(job_id, state=JobState.done, report_id=None, log="fertig"):
    payload = main.AgentJobReport(
        job_id=job_id, report_id=report_id, state=state, log=log,
        error=None, result={}, reboot_required=False)
    with Session(main.engine) as s:
        return asyncio.run(main.agent_report(
            payload=payload, x_agent_token=TOKEN, session=s))


def zustand(job_id):
    with Session(main.engine) as s:
        job = s.get(Job, job_id)
        return job.state, job.log, dict(job.params or {})


print("--- Backend: der Normalfall ---")

jid = neuer_auftrag()
antwort = melden(jid, report_id="bericht-1")
state, log, params = zustand(jid)
check("eine Meldung wird angenommen", state == JobState.done, state)
check("das Protokoll steht in der Datenbank", log == "fertig", log)
check("die Kennung der Meldung ist vermerkt",
      params.get("last_report_id") == "bericht-1", params)
check("die Antwort meldet keine Wiederholung", not antwort.get("duplicate"),
      antwort)

print("--- Backend: dieselbe Meldung zweimal ---")

antwort = melden(jid, report_id="bericht-1", log="zweiter Versuch")
check("die Wiederholung wird als solche erkannt",
      antwort.get("duplicate") is True, antwort)
state, log, _ = zustand(jid)
check("und aendert nichts am Auftrag", log == "fertig", log)

print("--- Backend: ein Zustand vom Agenten steht ---")

# Ohne diese Schranke koennte eine alte, lange gepufferte Meldung einen
# neueren Zustand ueberschreiben - sie weiss ja nichts von dem, was
# inzwischen passiert ist.
antwort = melden(jid, state=JobState.failed, report_id="bericht-2",
                 log="viel spaeter")
check("eine spaetere Meldung wird abgewiesen",
      antwort.get("ignored") is not None, antwort)
state, log, _ = zustand(jid)
check("der Zustand bleibt, wie der Agent ihn gemeldet hat",
      state == JobState.done and log == "fertig", (state, log))

print("--- Backend: einen Serverabbruch darf sie berichtigen ---")

# Genau der Fall vom 2026-10-21: der Server hat den Auftrag aufgegeben,
# der Agent meldet danach den erfolgreichen Lauf nach.
jid2 = neuer_auftrag()
with Session(main.engine) as s:
    job = s.get(Job, jid2)
    main.job_vom_server_abbrechen(job, main.utcnow(), "Abgebrochen: Testfall")
    s.add(job)
    s.commit()

state, _, params = zustand(jid2)
check("der Serverabbruch ist vermerkt",
      state == JobState.cancelled and params.get("server_cancelled") is True,
      (state, params))

melden(jid2, report_id="bericht-3", log="lief sauber durch")
state, log, params = zustand(jid2)
check("die nachgereichte Meldung setzt den Auftrag richtig",
      state == JobState.done, state)
check("mit dem Protokoll des Agenten", log == "lief sauber durch", log)
check("und der Vermerk ist weg", "server_cancelled" not in params, params)

# Danach gilt wieder die Schranke von oben: der Zustand kommt jetzt vom
# Agenten, eine weitere Meldung darf ihn nicht umwerfen.
antwort = melden(jid2, state=JobState.failed, report_id="bericht-4")
check("ein zweites Mal berichtigen geht nicht",
      antwort.get("ignored") is not None, antwort)

print("--- Backend: ein alter Agent meldet ohne Kennung ---")

# report_id ist bewusst optional. Ein Agent, der noch nicht
# aktualisiert ist, muss weiter melden koennen - er bekommt den Schutz
# vor Wiederholungen eben nicht.
jid3 = neuer_auftrag()
antwort = melden(jid3, report_id=None)
state, _, params = zustand(jid3)
check("eine Meldung ohne Kennung wird angenommen", state == JobState.done,
      state)
check("und hinterlaesst keine Kennung", "last_report_id" not in params,
      params)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
