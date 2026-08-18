"""
CO-37 - Wann ein Patch-Lauf faellig ist.

Grund fuer dieses Geruest: ein von Hand ausgeloester Scan hat auf einem
Windows-Server sofort einen Patch-Lauf nach sich gezogen. Der Nachschlag
prueft nur, ob Updates gemeldet sind - woher die Zahl stammt, hat er nicht
unterschieden. Auf einem zweiten Host trat es nicht auf, weil dort der
Nachlauf des Termins schon abgelaufen war; es sah nach Zufall aus, war
aber dieselbe Regel mit anderem Ausgangszustand.

Laeuft ohne Backend-Prozess und ohne Netz: die Logik wird direkt gegen
eine SQLite-Datei im temporaeren Verzeichnis geprueft.

    python3 tests/patchdue-test.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/patchdue.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_ADMIN_TOKEN"] = "test"
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402
from models import ApprovalState, Host, Job, JobState, JobType  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

# Die Tabellen legt sonst der Start des Dienstes an. Der laeuft hier nicht.
SQLModel.metadata.create_all(main.engine)

# Schluessel wie in main.DAYS - dort in Grossbuchstaben.
DAY_KEYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


UTC = timezone.utc
now_local = main.localnow().replace(microsecond=0)
# Termin vor zwei Stunden - liegt im Nachlauf (Vorgabe vier Stunden)
slot_local = now_local - timedelta(hours=2)
slot_utc = slot_local.astimezone(UTC)


def make_host(session, **kw):
    h = Host(
        hostname=kw.pop("hostname", "TEST-HOST"),
        os_type="windows",
        approval_state=ApprovalState.approved,
        agent_token_hash=kw.pop("token_hash", None) or os.urandom(8).hex(),
        patch_enabled=True,
        patch_days=[DAY_KEYS[slot_local.weekday()]],
        patch_time=f"{slot_local.hour:02d}:{slot_local.minute:02d}",
        patch_grace_hours=4,
        **kw,
    )
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


def add_job(session, host, job_type, created_at, state=JobState.done):
    j = Job(host_id=host.id, job_type=job_type, state=state,
            created_at=created_at, params={})
    session.add(j)
    session.commit()
    return j


with Session(main.engine) as s:
    # ---------------------------------------------------------- Grundfall
    h = make_host(s, hostname="TEST-A", updates_available=0,
                  last_patch_run=slot_utc + timedelta(minutes=5))
    add_job(s, h, JobType.patch, slot_utc + timedelta(minutes=5))
    due, why = main.patch_due(h, session=s)
    check("keine Updates -> kein Nachschlag", not due, why)

    # ------------------------------- Nachschlag nach dem Patch-Lauf (soll)
    h.updates_available = 2
    s.add(h); s.commit()
    due, why = main.patch_due(h, session=s)
    check("Updates aus dem Patch-Lauf -> Nachschlag", due, why)

    # ------------------------------ eigener Scan danach (soll NICHT)
    add_job(s, h, JobType.scan, slot_utc + timedelta(minutes=30))
    due, why = main.patch_due(h, session=s)
    check("nach eigenem Scan -> kein Nachschlag", not due, why)

    # ---------------------- neuer Patch-Lauf hebt die Sperre wieder auf
    add_job(s, h, JobType.patch, slot_utc + timedelta(minutes=40))
    due, why = main.patch_due(h, session=s)
    check("nach erneutem Patch-Lauf wieder Nachschlag", due, why)

    # ------------------------------------------- Scan VOR dem Patch-Lauf
    h2 = make_host(s, hostname="TEST-B", updates_available=3,
                   last_patch_run=slot_utc + timedelta(minutes=20))
    add_job(s, h2, JobType.scan, slot_utc + timedelta(minutes=5))
    add_job(s, h2, JobType.patch, slot_utc + timedelta(minutes=20))
    due, why = main.patch_due(h2, session=s)
    check("Scan vor dem Patch-Lauf stoert nicht", due, why)

    # ----------------------------------------------- Neustart steht aus
    h3 = make_host(s, hostname="TEST-C", updates_available=2,
                   reboot_required=True,
                   last_patch_run=slot_utc + timedelta(minutes=5))
    add_job(s, h3, JobType.patch, slot_utc + timedelta(minutes=5))
    due, why = main.patch_due(h3, session=s)
    check("ausstehender Neustart -> kein Nachschlag", not due, why)

    # ------------------------------------------ Nachlauf abgelaufen
    old_local = now_local - timedelta(hours=9)
    h4 = make_host(s, hostname="TEST-D", updates_available=5,
                   last_patch_run=old_local.astimezone(UTC) + timedelta(minutes=5))
    h4.patch_days = [DAY_KEYS[old_local.weekday()]]
    h4.patch_time = f"{old_local.hour:02d}:{old_local.minute:02d}"
    s.add(h4); s.commit()
    due, why = main.patch_due(h4, session=s)
    check("Nachlauf abgelaufen -> nichts", not due, why)

    # --------------------------------- Termin noch nicht gelaufen
    h5 = make_host(s, hostname="TEST-E", updates_available=4,
                   last_patch_run=None)
    due, why = main.patch_due(h5, session=s)
    check("Termin faellig, noch nicht gelaufen -> Patch", due, why)

    # --------------------------------- Hoechstzahl je Termin
    h6 = make_host(s, hostname="TEST-F", updates_available=1,
                   last_patch_run=slot_utc + timedelta(minutes=5))
    for i in range(main.PATCH_MAX_RUNS_PER_SLOT):
        add_job(s, h6, JobType.patch, slot_utc + timedelta(minutes=5 + i))
    due, why = main.patch_due(h6, session=s)
    check("Hoechstzahl erreicht -> kein Nachschlag", not due, why)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
