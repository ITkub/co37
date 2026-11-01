"""
CO-37 - "Alle pruefen und patchen".

WAS DER KNOPF TUT

Er legt nur Scan-Auftraege an. Ob daraus ein Patch-Auftrag wird,
entscheidet der Server, sobald das Pruefergebnis vorliegt - vorher
weiss niemand, ob es etwas zu patchen gibt. Die Verkettung haengt an
einem Vermerk 'patch_after' am Scan-Auftrag und greift in
agent_report(), wenn dieser Auftrag als erledigt gemeldet wird.

WAS HIER GEPRUEFT WIRD

Vor allem die Stelle, an der es wehtut: der entstehende Patch-Auftrag
startet den Host NICHT neu. 'reboot_if_needed' wird fest auf False
gesetzt und kommt nicht vom Aufrufer - auch bei einem Host, an dem
auto_reboot und patch_auto_reboot gesetzt sind. Ginge das verloren,
wuerde ein Knopf, der "ohne Neustart" verspricht, reihenweise
Produktivsysteme neu starten.

Dazu die Grenzen: keine Updates - kein Auftrag; laeuft schon ein
Patch-Auftrag, kommt kein zweiter; ohne den Vermerk passiert nichts;
und der Aufrufer darf 'patch_after' nicht ueber params einschleusen,
denn params wird gegen eine weisse Liste gefiltert (F-22).

Braucht kein laufendes Backend und kein Netz.

    python3 tests/scan-patch-test.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/scanpatch.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402
from models import (ApprovalState, Host, Job, JobState,  # noqa: E402
                    JobType, OSType)

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def wirt_anlegen(auto_reboot=False, patch_auto_reboot=False, updates=3):
    """
    Ein freigegebener Host mit EIGENEM Token.

    Nicht ein gemeinsames Token fuer alle: host_by_token() sucht den
    Host zum Token, und bei gleichem Hash traefe die Meldung
    irgendeinen - die Reihe pruefte dann etwas anderes als sie behauptet.
    """
    token = "agenttoken-" + os.urandom(8).hex()
    with Session(main.engine) as s:
        host = Host(hostname=f"TEST-SP{os.urandom(3).hex()}",
                    os_type=OSType.linux,
                    approval_state=ApprovalState.approved,
                    agent_token_hash=main.hash_token(token),
                    auto_reboot=auto_reboot,
                    patch_auto_reboot=patch_auto_reboot,
                    updates_available=updates)
        s.add(host)
        s.commit()
        s.refresh(host)
        return host.id, token


def scan_auftrag(host_id, patch_after=True, state=JobState.running):
    with Session(main.engine) as s:
        job = Job(host_id=host_id, job_type=JobType.scan,
                  params={"patch_after": True} if patch_after else {},
                  state=state, started_at=main.utcnow())
        s.add(job)
        s.commit()
        s.refresh(job)
        return job.id


def melden(job_id, token, state=JobState.done):
    payload = main.AgentJobReport(
        job_id=job_id, report_id=os.urandom(8).hex(), state=state,
        log="Pruefung fertig", error=None, result={}, reboot_required=None)
    with Session(main.engine) as s:
        return asyncio.run(main.agent_report(
            payload=payload, x_agent_token=token, session=s))


def patch_auftraege(host_id):
    with Session(main.engine) as s:
        return s.exec(
            select(Job).where(Job.host_id == host_id,
                              Job.job_type == JobType.patch)
        ).all()


print("--- aus einer Pruefung mit Fund wird ein Patch-Auftrag ---")

h1, tok1 = wirt_anlegen(updates=3)
melden(scan_auftrag(h1), tok1)
auftraege = patch_auftraege(h1)
check("ein Patch-Auftrag entsteht", len(auftraege) == 1, len(auftraege))
if auftraege:
    p = auftraege[0]
    check("er wartet auf den Agenten", p.state == JobState.pending, p.state)
    check("und weiss, woher er kommt",
          p.params.get("from_scan_job_id") is not None, p.params)

print("--- und er startet den Host nicht neu ---")

# Der eigentliche Punkt. Ohne reboot_if_needed=False entscheidet
# agent_pre_reboot() nach host.auto_reboot - und der steht hier auf an.
h2, tok2 = wirt_anlegen(auto_reboot=True, patch_auto_reboot=True, updates=5)
melden(scan_auftrag(h2), tok2)
p = patch_auftraege(h2)[0]
check("reboot_if_needed steht auf False",
      p.params.get("reboot_if_needed") is False, p.params)
check("auch wenn am Host auto_reboot gesetzt ist",
      p.params.get("reboot_if_needed") is False, p.params)
check("kein 'manual', das Fenster und Richtlinie uebergehen wuerde",
      "manual" not in p.params, p.params)
check("und kein Neustart-Auftrag nebenher",
      not [j for j in patch_auftraege(h2) if j.job_type == JobType.reboot])

print("--- ohne Fund kein Auftrag ---")

h3, tok3 = wirt_anlegen(updates=0)
melden(scan_auftrag(h3), tok3)
check("eine Pruefung ohne Updates loest nichts aus",
      patch_auftraege(h3) == [], patch_auftraege(h3))

print("--- ohne Vermerk kein Auftrag ---")

h4, tok4 = wirt_anlegen(updates=4)
melden(scan_auftrag(h4, patch_after=False), tok4)
check("eine gewoehnliche Pruefung bleibt eine Pruefung",
      patch_auftraege(h4) == [], patch_auftraege(h4))

print("--- eine gescheiterte Pruefung patcht nicht ---")

h5, tok5 = wirt_anlegen(updates=4)
melden(scan_auftrag(h5), tok5, state=JobState.failed)
check("nach einem Fehlschlag wird nicht gepatcht",
      patch_auftraege(h5) == [], patch_auftraege(h5))

print("--- kein zweiter Patch-Auftrag daneben ---")

# Zwei Patch-Auftraege gleichzeitig auf einem Host sind nirgends
# vorgesehen; der Heartbeat haelt das mit patch_job_open() genauso.
h6, tok6 = wirt_anlegen(updates=2)
with Session(main.engine) as s:
    s.add(Job(host_id=h6, job_type=JobType.patch, params={},
              state=JobState.pending))
    s.commit()
melden(scan_auftrag(h6), tok6)
check("laeuft schon einer, bleibt es bei dem",
      len(patch_auftraege(h6)) == 1, len(patch_auftraege(h6)))

print("--- wartende Auftraege verfallen ---")

# Ohne Frist sammeln sich Auftraege bei Hosts, die bewusst laengere Zeit
# aus sind, und laufen beim Einschalten alle nacheinander los.
h7, tok7 = wirt_anlegen(updates=2)
melden(scan_auftrag(h7), tok7)
p7 = patch_auftraege(h7)[0]
check("der verkettete Patch-Auftrag hat eine Frist",
      p7.expires_at is not None, p7.expires_at)
if p7.expires_at:
    rest = main.ensure_utc(p7.expires_at) - main.utcnow()
    check("und sie betraegt sieben Tage",
          abs(rest - main.JOB_MAX_WAIT) < timedelta(minutes=5), rest)

# Der Text eines verfallenen Auftrags. Beim Neustart ist es der
# Nachlauf, sonst die weite Frist - frueher stand dort in jedem Fall
# "Nachlauf", bei einem Scan also "Nachlauf None Minuten".
mit_nachlauf = Job(host_id=h7, job_type=JobType.reboot,
                   params={"grace_minutes": 10}, created_at=main.utcnow())
ohne_nachlauf = Job(host_id=h7, job_type=JobType.scan, params={},
                    created_at=main.utcnow())
check("ein verfallener Neustart nennt den Nachlauf",
      "Nachlauf 10 Minuten" in main.verfallsgrund(mit_nachlauf),
      main.verfallsgrund(mit_nachlauf))
check("ein verfallener Scan nennt die Frist",
      "Frist von 7 Tagen" in main.verfallsgrund(ohne_nachlauf),
      main.verfallsgrund(ohne_nachlauf))
check("und schreibt kein 'None' in den Grund",
      "None" not in main.verfallsgrund(ohne_nachlauf),
      main.verfallsgrund(ohne_nachlauf))

print("--- der Aufrufer kann den Vermerk nicht einschleusen ---")

# params wird in create_job() gegen eine weisse Liste gefiltert. Steht
# patch_after eines Tages darin, faellt das hier auf - und damit auch
# jeder andere Steuerparameter, den jemand dort nachtraegt.
quelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
check("ERLAUBTE_PARAMS steht unveraendert auf source_area_id",
      'ERLAUBTE_PARAMS = {"source_area_id"}' in quelle)
check("patch_after ist ein eigenes Feld in JobCreate",
      "patch_after: bool = False" in quelle)
check("und wird nur fuer Scan-Auftraege uebernommen",
      "payload.job_type == JobType.scan and payload.patch_after" in quelle)
check("der entstehende Patch-Auftrag verdrahtet reboot_if_needed fest",
      '"reboot_if_needed": False,' in quelle)
check("auch Scan und Patch bekommen in create_job eine Frist",
      "elif payload.job_type in (JobType.scan, JobType.patch):" in quelle)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
