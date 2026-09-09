"""
CO-37 - Staffelung beim Ausrollen der Agents (F-43).

DER ANLASS

Die Staffelung soll dafuer sorgen, dass ein fehlerhaftes Rollout
zunaechst nur ein System trifft: erst der Pilot, dann der Rest. Bis
0.37.9 genuegte dafuer die vom Agenten gemeldete Fassung - ein Host,
dessen Aktualisierung schiefging und der danach Unsinn meldete, gab
damit die ganze Flotte frei. Seit 0.37.10 muss zusaetzlich ein in
diesem Durchlauf abgeschlossener selfupdate-Auftrag vorliegen.

Beim Nachsehen am 2026-09-09 fiel auf, dass das immer noch zu wenig
ist, und zwar nicht wegen eines Angreifers: die Freigabe erfolgte beim
ERSTEN Heartbeat, bei dem beide Bedingungen zutrafen. Ein Pilot, der
die neue Fassung meldet und zwei Minuten spaeter stirbt, hatte die
ganze Flotte schon losgeschickt. Bewiesen war damit, dass der neue
Agent einmal gestartet ist - nicht, dass er laeuft.

Seit 0.37.32 muss die Meldung PILOT_BESTAETIGUNG_MINUTEN lang stehen
bleiben, also ueber mehrere Heartbeats hinweg.

WAS DIESE REIHE TUT

Sie ruft advance_agent_rollout() wirklich auf, gegen eine eigene
Datenbank, und spult die Zeit vor, statt eine Viertelstunde zu warten.
Geprueft wird das Verhalten, nicht der Quelltext.

Was sie NICHT kann: eine Aussage ueber einen uebernommenen Piloten. Der
bestimmt beide Angaben und kann sie beliebig lange wiederholen - siehe
F-43 im Pruefdokument. Ausgerollt wird ohnehin nur signierter
Servercode, es geht um Verfuegbarkeit.

    python3 tests/rollout-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/rollout.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402
from models import (ApprovalState, Host, Job, JobState,  # noqa: E402
                    JobType, Setting)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}"
          f"{'  -> ' + str(extra) if extra else ''}")


SQLModel.metadata.create_all(main.engine)
VERSION = main.agent_source_version()
ALT = "0.0.1"


def frisch(anzahl_weitere: int = 2):
    """Eine Anlage mit einem Piloten und N weiteren veralteten Hosts."""
    with Session(main.engine) as s:
        for tabelle in (Job, Host, Setting):
            for zeile in s.exec(select(tabelle)).all():
                s.delete(zeile)
        s.commit()
        pilot = Host(hostname="PILOT", approval_state=ApprovalState.approved,
                     agent_version=ALT)
        s.add(pilot)
        for i in range(anzahl_weitere):
            s.add(Host(hostname=f"REST{i}",
                       approval_state=ApprovalState.approved,
                       agent_version=ALT))
        s.commit()
        s.refresh(pilot)
        main._set(s, "agent_autoroll", "true")
        main._set(s, "agent_roll_mode", "staged")
        main._set(s, "agent_pilot_host", str(pilot.id))
        s.commit()
        return pilot.id


def zustand():
    with Session(main.engine) as s:
        return (main._setting(s, "agent_roll_state"),
                main._setting(s, "agent_roll_note"),
                main._setting(s, "agent_roll_pilot_ok"))


def auftraege(host_id=None):
    with Session(main.engine) as s:
        q = select(Job).where(Job.job_type == JobType.selfupdate)
        if host_id is not None:
            q = q.where(Job.host_id == host_id)
        return s.exec(q).all()


def pilot_meldet_erfolg(pilot_id):
    """Der Pilot meldet die neue Fassung und schliesst seinen Auftrag ab."""
    with Session(main.engine) as s:
        host = s.get(Host, pilot_id)
        host.agent_version = VERSION
        s.add(host)
        for job in s.exec(select(Job).where(
                Job.host_id == pilot_id,
                Job.job_type == JobType.selfupdate)).all():
            job.state = JobState.done
            s.add(job)
        s.commit()


def zeit_vorspulen(minuten):
    """Den Merker in die Vergangenheit schieben, statt zu warten."""
    with Session(main.engine) as s:
        seit = main._setting(s, "agent_roll_pilot_ok")
        if not seit:
            return False
        neu = main.ensure_utc(main.datetime.fromisoformat(seit)) \
            - timedelta(minutes=minuten)
        main._set(s, "agent_roll_pilot_ok", neu.isoformat())
        s.commit()
        return True


def heartbeat():
    with Session(main.engine) as s:
        main.advance_agent_rollout(s)
        s.commit()


# ======================================================================
# Die Vorgabe
# ======================================================================
print("--- Die Vorgabe ---")
check("es gibt eine Bestaetigungszeit",
      isinstance(main.PILOT_BESTAETIGUNG_MINUTEN, int)
      and main.PILOT_BESTAETIGUNG_MINUTEN > 0,
      main.PILOT_BESTAETIGUNG_MINUTEN)
# Sie muss deutlich unter dem Zeitablauf liegen, sonst laeuft jedes
# Ausrollen in den Timeout, bevor es freigeben darf.
check("und sie liegt unter dem Zeitablauf des Piloten",
      main.PILOT_BESTAETIGUNG_MINUTEN < main.PILOT_TIMEOUT_MINUTES,
      f"{main.PILOT_BESTAETIGUNG_MINUTEN} < {main.PILOT_TIMEOUT_MINUTES}")


# ======================================================================
# Der gute Weg
# ======================================================================
print("\n--- Pilot laeuft durch ---")
pid = frisch(2)
with Session(main.engine) as s:
    main.start_agent_rollout(s, manual=True)
    s.commit()

state, note, merker = zustand()
check("nach dem Start ist der Pilot dran", state == "pilot", state)
check("nur der Pilot hat einen Auftrag",
      len(auftraege()) == 1 and auftraege()[0].host_id == pid,
      [(j.host_id, j.state) for j in auftraege()])
check("und es gibt noch keinen Bestaetigungsmerker", merker == "", merker)

# Erster Heartbeat mit Erfolgsmeldung: NOCH nicht freigeben.
pilot_meldet_erfolg(pid)
heartbeat()
state, note, merker = zustand()
check("die Erfolgsmeldung gibt die Flotte nicht sofort frei",
      state == "pilot", state)
check("der Merker steht jetzt", merker != "", merker)
check("und die Oberflaeche sagt, dass bestaetigt wird",
      "estaetigung" in note, note)
check("die uebrigen Hosts haben weiterhin keinen Auftrag",
      len(auftraege()) == 1, [(j.host_id, j.state) for j in auftraege()])

# Zweiter Heartbeat, immer noch innerhalb der Frist: weiterhin nichts.
heartbeat()
check("auch ein zweiter Heartbeat innerhalb der Frist gibt nicht frei",
      zustand()[0] == "pilot" and len(auftraege()) == 1,
      [(j.host_id, j.state) for j in auftraege()])

# Frist abgelaufen: jetzt folgen die uebrigen.
zeit_vorspulen(main.PILOT_BESTAETIGUNG_MINUTEN + 1)
heartbeat()
state, note, merker = zustand()
check("nach der Bestaetigungszeit folgen die uebrigen", state == "rest", state)
check("und zwar alle zwei", len(auftraege()) == 3,
      [(j.host_id, j.state) for j in auftraege()])
check("der Merker ist wieder leer", merker == "", merker)
check("die Notiz nennt den Piloten", "PILOT" in note, note)


# ======================================================================
# Der Fall, um den es geht
# ======================================================================
print("\n--- Pilot faellt nach der Erfolgsmeldung wieder aus ---")
# Genau das Muster, das die Bestaetigungszeit finden soll: der Agent
# startet, meldet die neue Fassung und stirbt danach. Vorher gab er die
# ganze Flotte frei.
pid = frisch(2)
with Session(main.engine) as s:
    main.start_agent_rollout(s, manual=True)
    s.commit()
pilot_meldet_erfolg(pid)
heartbeat()
check("Merker gesetzt", zustand()[2] != "", zustand())

with Session(main.engine) as s:
    host = s.get(Host, pid)
    host.agent_version = ALT          # faellt auf die alte Fassung zurueck
    s.add(host)
    s.commit()
heartbeat()
state, note, merker = zustand()
check("der Merker faellt zurueck", merker == "", merker)
check("die Flotte bleibt stehen",
      state == "pilot" and len(auftraege()) == 1,
      [(j.host_id, j.state) for j in auftraege()])
check("und die Notiz sagt warum", "von vorn" in note, note)

# Und die Gegenprobe: meldet er danach wieder Erfolg, faengt die Frist
# von vorn an - eine alte Bestaetigung darf nicht weitergelten.
pilot_meldet_erfolg(pid)
heartbeat()
check("nach der Rueckkehr beginnt die Frist neu",
      zustand()[0] == "pilot" and len(auftraege()) == 1,
      zustand())


# ======================================================================
# Ein Merker aus einem frueheren Durchlauf
# ======================================================================
print("\n--- Alter Merker ---")
# Ohne Zuruecksetzen beim Start gaebe ein Durchlauf, dessen Pilot
# frueher einmal bestaetigt hatte, die Flotte beim ersten Heartbeat
# frei - die Bestaetigung waere dann laengst abgelaufen.
pid = frisch(2)
with Session(main.engine) as s:
    main._set(s, "agent_roll_pilot_ok",
              (main.utcnow() - timedelta(days=30)).isoformat())
    s.commit()
with Session(main.engine) as s:
    main.start_agent_rollout(s, manual=True)
    s.commit()
check("der Start loescht einen alten Merker", zustand()[2] == "", zustand())
pilot_meldet_erfolg(pid)
heartbeat()
check("und die Bestaetigung faengt bei null an",
      zustand()[0] == "pilot" and len(auftraege()) == 1,
      [(j.host_id, j.state) for j in auftraege()])


# ======================================================================
# Ohne Staffelung
# ======================================================================
print("\n--- Modus 'alle auf einmal' ---")
# Die Bestaetigungszeit darf den ungestaffelten Weg nicht anfassen - wer
# 'alle auf einmal' waehlt, hat sich gegen die Staffelung entschieden.
pid = frisch(2)
with Session(main.engine) as s:
    main._set(s, "agent_roll_mode", "all")
    s.commit()
with Session(main.engine) as s:
    main.start_agent_rollout(s, manual=True)
    s.commit()
check("alle drei bekommen sofort einen Auftrag", len(auftraege()) == 3,
      [(j.host_id, j.state) for j in auftraege()])
check("und der Zustand ist 'rest'", zustand()[0] == "rest", zustand()[0])


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
