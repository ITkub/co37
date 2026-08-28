"""
CO-37 - Zeitplan eines Bereichs: area_patch_due().

Kernfrage, die dieser Test absichert: bekommt wirklich JEDER Host im
Bereich seinen Auftrag fuer einen faelligen Termin - nicht nur der erste,
der sich per Heartbeat meldet?

Ein einzelnes last_run am Bereich wuerde das nicht leisten: der zuerst
meldende Host setzte es auf "erledigt", und jeder andere Host im selben
Bereich bekaeme seinen Auftrag fuer diesen Termin nie, weil er nie gefragt
wurde. Deshalb traegt jeder Host sein eigenes area_patch_last_run - der
Bereich liefert nur noch Tage, Uhrzeit und Kulanz.

Der Nachschlag (_followup_due) wird unveraendert wiederverwendet, egal ob
der urspruengliche Lauf vom eigenen Zeitplan des Hosts oder vom Bereich
ausgeloest wurde - getestet auch hier, weil area_patch_due() ihn ueber
denselben Weg wie patch_due() aufruft.

Laeuft ohne Backend-Prozess und ohne Netz - wie tests/patchdue-test.py,
demselben Aufbau folgend.

    python3 tests/area-schedule-test.py
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/area-schedule.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402
from models import Area, Host, Job, JobState, JobType  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

SQLModel.metadata.create_all(main.engine)

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


def make_area(session, **kw):
    a = Area(
        name=kw.pop("name", "Testbereich"),
        patch_enabled=True,
        patch_days=kw.pop("patch_days", [DAY_KEYS[slot_local.weekday()]]),
        patch_time=kw.pop(
            "patch_time", f"{slot_local.hour:02d}:{slot_local.minute:02d}"),
        patch_grace_hours=4,
        **kw,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def make_host(session, area, **kw):
    h = Host(
        hostname=kw.pop("hostname", "TEST-HOST"),
        os_type="windows",
        approval_state="approved",
        agent_token_hash=kw.pop("token_hash", None) or os.urandom(8).hex(),
        area_id=area.id if area else None,
        **kw,
    )
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


with Session(main.engine) as s:
    area = make_area(s)

    # ----------------------------------------- Grundfall: faellig, niemand
    #                                            hat den Termin schon gehabt
    a_host = make_host(s, area, hostname="AREA-A", area_patch_last_run=None)
    due, why = main.area_patch_due(a_host, area, session=s)
    check("Bereichs-Termin faellig fuer ersten Host", due, why)

    b_host = make_host(s, area, hostname="AREA-B", area_patch_last_run=None)
    due, why = main.area_patch_due(b_host, area, session=s)
    check("und unabhaengig davon auch fuer den zweiten", due, why)

    # ---------------------------- Der Kernfall: A wurde schon bedient (mit
    # aufgebrauchten Updates, kein Nachschlag faellig) - B darf davon nicht
    # betroffen sein. Ein gemeinsames last_run am Bereich wuerde hier
    # durchfallen.
    a_host.area_patch_last_run = slot_utc + timedelta(minutes=5)
    a_host.updates_available = 0
    s.add(a_host)
    s.commit()
    due, why = main.area_patch_due(a_host, area, session=s)
    check("A gilt jetzt als erledigt (kein Nachschlag ohne Updates)", not due, why)

    due, why = main.area_patch_due(b_host, area, session=s)
    check("B bekommt seinen Auftrag trotzdem noch - nicht vom Bereich "
          "als erledigt markiert", due, why)

    # -------- host.last_patch_run (eigener Zeitplan) darf area_patch_due
    # nicht beeinflussen - getrennte Felder, unabhaengige Zeitplaene.
    c_host = make_host(s, area, hostname="AREA-C", area_patch_last_run=None,
                        last_patch_run=slot_utc + timedelta(minutes=5))
    due, why = main.area_patch_due(c_host, area, session=s)
    check("eigener last_patch_run stoert den Bereichs-Zeitplan nicht", due, why)

    # ------------------------------------------- Nachschlag wiederverwendet
    # Wie beim eigenen Zeitplan: aufgebrauchte Updates + erledigter Termin
    # -> kein Nachschlag; offene Updates -> doch.
    d_host = make_host(s, area, hostname="AREA-D",
                        area_patch_last_run=slot_utc + timedelta(minutes=5),
                        updates_available=3)
    s.add(Job(host_id=d_host.id, job_type=JobType.patch,
              state=JobState.done, created_at=slot_utc + timedelta(minutes=5),
              params={}))
    s.commit()
    due, why = main.area_patch_due(d_host, area, session=s)
    check("Nachschlag ueber den Bereichs-Zeitplan funktioniert wie beim "
          "eigenen", due, why)

    # ------------------------------------------------ Bereich nicht aktiv
    area.patch_enabled = False
    s.add(area)
    s.commit()
    e_host = make_host(s, area, hostname="AREA-E", area_patch_last_run=None)
    due, why = main.area_patch_due(e_host, area, session=s)
    check("abgeschalteter Bereichs-Zeitplan -> nichts faellig", not due, why)
    area.patch_enabled = True
    s.add(area)
    s.commit()

    # -------------------------------------------------- Nachlauf abgelaufen
    old_local = now_local - timedelta(hours=9)
    area2 = make_area(s, name="Altbereich",
                      patch_days=[DAY_KEYS[old_local.weekday()]],
                      patch_time=f"{old_local.hour:02d}:{old_local.minute:02d}")
    f_host = make_host(s, area2, hostname="AREA-F", area_patch_last_run=None)
    due, why = main.area_patch_due(f_host, area2, session=s)
    check("Nachlauf des Bereichs abgelaufen -> nichts faellig", not due, why)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
