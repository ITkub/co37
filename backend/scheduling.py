"""
CO-37 - wann ein Auftrag faellig ist, und wann einer tot ist.

Herausgeloest aus main.py am 2026-11-10. Die Route agent_heartbeat war
auf 343 Zeilen gewachsen und enthielt das gesamte Scheduling: Nachschlag,
eigener Zeitplan, Bereichsplan, Verfall, Stilleerkennung,
Hoechstlaufzeit. Verhalten ist dabei unveraendert geblieben - was hier
steht, stand vorher woertlich dort.

WARUM DIESER SCHNITT

Zwei Fragen gehoeren zusammen und sonst nirgendwohin: "ist jetzt etwas
faellig?" und "laeuft dieser Auftrag noch?". Beide haengen an Zeit und
Zustand, keine an HTTP.

Was NICHT hier steht: die Uebernahme der gemeldeten Stammdaten und das
Ausliefern der Auftraege. Beides baut die Antwort der Route und gehoert
deshalb dorthin.

KEIN IMPORT VON main

Das ist die Bedingung, unter der der Schnitt ueberhaupt moeglich ist.
Dieses Modul kennt models, utctime und sqlmodel - mehr nicht.
tests/scheduling-export-test.py misst das in einem eigenen Python: wird
main beim Laden mitgezogen, faellt die Reihe um.

main.py holt alles bis auf BACKEND_START per Re-Export zurueck, damit
sechs vorhandene Pruefreihen unveraendert weiterlaufen. BACKEND_START
ausdruecklich nicht: tests/jobstille-test.py SETZT den Wert, um die Zeit
vorzuspulen, und ein Wert an zwei Stellen, von denen nur eine wirkt, ist
der Fehler aus F-12/F-14.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from models import Area, Host, Job, JobState, JobType
from utctime import UTC, from_timestamp, localnow, utcnow

DAYS = {"MO": 0, "DI": 1, "MI": 2, "DO": 3, "FR": 4, "SA": 5, "SO": 6,
        "TU": 1, "WE": 2, "TH": 3, "SU": 6}

# Hoechstzahl der Laeufe je Termin. Mehr als drei heisst: etwas laesst
# sich nicht installieren, wird aber weiter als verfuegbar gemeldet.
PATCH_MAX_RUNS_PER_SLOT = 3

# Wie oft ein Auftrag ausgeliefert werden darf, ohne dass der Agent ihn
# begonnen hat. Zwei Versuche: einer fuer den Normalfall, einer fuer einen
# Neustart dazwischen. Danach ist von einem Auftrag auszugehen, der den
# Agent zuverlaessig umbringt.
MAX_JOB_DELIVERIES = 2

# Mindestabstand vor einer erneuten Auslieferung. Der Agent arbeitet
# Auftraege einzeln ab und meldet sich waehrenddessen nicht, doppelte
# Ausfuehrung ist also ohnehin unwahrscheinlich - dieser Abstand ist die
# Absicherung fuer den Fall, dass doch zwei Instanzen laufen.
REDELIVER_AFTER = timedelta(seconds=60)

# Hoechstlaufzeit je Auftragstyp, bevor ein haengender Auftrag verworfen
# wird. Grosszuegig angesetzt: der Agent laesst PowerShell und apt jeweils
# bis zu zwei Stunden laufen, und ein Neustart-Auftrag ueberdauert den
# Neustart selbst. Zu knapp waere schlimmer als zu weit - ein Abbruch
# mitten im Patchen kostet mehr als ein spaeter aufgeraeumter Auftrag.
JOB_MAX_RUNTIME = {
    JobType.scan: timedelta(minutes=30),
    JobType.patch: timedelta(hours=4),
    JobType.reboot: timedelta(hours=1),
    JobType.selfupdate: timedelta(minutes=30),
}

# Wie lange ein laufender Auftrag schweigen darf, waehrend sein Host sich
# weiter meldet. Grosszuegig gewaehlt: bei apt fliesst die Ausgabe
# durchgehend, ein einzelner Windows-Installationsschritt kann dagegen
# laenger stumm bleiben. Gilt nicht fuer Auftraege mit gekuerztem
# Protokoll - siehe job_verstummt().
JOB_LOG_SILENCE = timedelta(minutes=30)

# Start dieses Prozesses. Die Stilleregel zaehlt erst ab hier: war das
# BACKEND weg - Neustart, Update, Wartung -, konnte kein Agent Zeilen
# liefern, und seine Fehler dabei schluckt er stillschweigend
# (LogSink.flush faengt alles ab). Ohne diese Schranke wuerde der erste
# Heartbeat nach einem Backend-Neustart jeden laufenden Auftrag
# abraeumen, obwohl die Hosts durchgehend gearbeitet haben.
#
# Liegt NUR hier. Siehe den Kopf dieser Datei.
BACKEND_START = utcnow()

# Wie lange ein wartender Scan- oder Patch-Auftrag auf seinen Host
# wartet, bevor er verfaellt. Neustarts haben ihren eigenen, viel
# kuerzeren Nachlauf (grace_minutes).
#
# Vorher hatten beide gar kein Verfallsdatum - "ein Scan oder Patch
# schadet auch spaeter nicht". Bei Hosts, die bewusst laengere Zeit aus
# sind, sammeln sich die Auftraege dadurch an und laufen beim
# Einschalten alle nacheinander los. Und ein Patch-Auftrag von vor drei
# Wochen installiert Updates, die inzwischen von neueren ueberholt
# sind, auf einem Host, dessen Zustand niemand mehr kennt.
#
# Sieben Tage: ein Wochenende oder ein Urlaub darf dazwischenliegen.
# Wer danach noch etwas will, klickt neu - und bekommt eine Pruefung
# auf dem Stand von heute statt einer von vorgestern.
JOB_MAX_WAIT = timedelta(days=7)


# ======================================================================
# Faelligkeit
# ======================================================================
def _followup_due(
    host: Host, slot_utc: datetime, session: Optional[Session]
) -> tuple[bool, str]:
    """
    Entscheidet, ob nach einem bereits gelaufenen Termin noch einmal
    gepatcht wird.

    Hintergrund: Windows legt nach einem Patch-Lauf mit Neustart haeufig
    weitere Updates nach - der kumulative Teil bringt Nachfolger mit, die
    vorher gar nicht sichtbar waren. Ohne das hier bleiben die bis zum
    naechsten Termin liegen, obwohl das Wartungsfenster noch laeuft.

    Drei Bremsen, damit daraus keine Endlosschleife wird:
      - nur solange der Nachlauf des Termins laeuft (Pruefung beim Aufrufer)
      - nur wenn der vorige Lauf sauber beendet wurde. Ein fehlgeschlagenes
        Update wuerde sonst alle 15 Sekunden neu versucht.
      - hoechstens PATCH_MAX_RUNS_PER_SLOT Laeufe je Termin.
    """
    if host.updates_available <= 0:
        return False, "fuer diesen Termin bereits gelaufen"
    # Steht ein Neustart aus, wird nicht nachgelegt. Windows nimmt in
    # diesem Zustand keine weiteren Updates an, und der Lauf wuerde ohnehin
    # vom Herunterfahren abgeschnitten. Genau das ist passiert: der Agent
    # meldet den Patch-Auftrag als fertig und startet erst 30 Sekunden
    # spaeter neu - in dieser Luecke wurde der Nachschlag angelegt,
    # angenommen und mitten in der Installation gekappt.
    # Der Nachschlag laeuft stattdessen nach dem Neustart, gesteuert ueber
    # host.patch_followup_left.
    if host.reboot_required:
        return False, "Neustart steht aus - Nachschlag erst danach"
    if session is None:
        return False, "fuer diesen Termin bereits gelaufen"

    runs = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.job_type == JobType.patch,
            Job.created_at >= slot_utc,
        ).order_by(Job.created_at)
    ).all()

    if len(runs) >= PATCH_MAX_RUNS_PER_SLOT:
        return False, "Hoechstzahl der Laeufe fuer diesen Termin erreicht"
    if runs and runs[-1].state is not JobState.done:
        return False, f"voriger Lauf endete mit {runs[-1].state.value}"

    # Woher stammen die gemeldeten Updates?
    #
    # Der Nachlauf-Scan eines Patch-Laufs erzeugt keinen eigenen Auftrag -
    # er laeuft im Patch-Auftrag mit. Ein von Hand ausgeloester Scan ist
    # dagegen ein eigener Auftrag vom Typ scan. Steht ein solcher nach dem
    # letzten Patch-Lauf, kommen die Zahlen von dort.
    #
    # Ohne diese Pruefung genuegte ein Druck auf "Pruefen" im Nachlauf
    # eines bereits gelaufenen Termins, um sofort einen Patch-Lauf
    # auszuloesen. Der Nachschlag ist dafuer da, dass Windows nach einem
    # Patch-Lauf kumulativ nachlegt - nicht dafuer, auf einen Blick ins
    # System hin zu patchen. Wer prueft, will erst sehen.
    if runs:
        manual_scan = session.exec(
            select(Job).where(
                Job.host_id == host.id,
                Job.job_type == JobType.scan,
                Job.created_at > runs[-1].created_at,
            ).limit(1)
        ).first()
        if manual_scan:
            return False, "Zahlen stammen aus einem eigenen Scan - kein Nachschlag"

    return True, f"Nachschlag: {host.updates_available} weitere Updates im Nachlauf"


def _schedule_due(
    enabled: bool,
    days: list[str],
    time_str: Optional[str],
    grace_hours: int,
    last_run: Optional[datetime],
    host: Host,
    session: Optional[Session],
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """
    Kern von patch_due() und area_patch_due(): prueft, ob ein durch Tage,
    Uhrzeit und Kulanz beschriebener Termin jetzt faellig ist - unabhaengig
    davon, ob der Zeitplan vom Host selbst oder von seinem Bereich stammt.

    Wird beim Heartbeat aufgerufen, es gibt also keinen Hintergrunddienst.
    War der Host zum Termin ausgeschaltet, greift der Nachlauf
    (grace_hours) - danach wird der Termin uebersprungen, damit nicht
    Montagmorgen im Betrieb gepatcht wird.

    last_run ist die Bezugsgroesse fuer genau diesen Zeitplan:
    host.last_patch_run fuer den eigenen, host.area_patch_last_run fuer den
    des Bereichs. Getrennte Felder, weil beide Zeitplaene unabhaengig
    nebeneinander laufen koennen. host bleibt in jedem Fall der Host, fuer
    den geprueft wird - der Nachschlag (_followup_due) ist grundsaetzlich
    host-eigen, unabhaengig davon, welcher Zeitplan den urspruenglichen
    Lauf ausgeloest hat.
    """
    if not enabled or not days or not time_str:
        return False, "nicht eingeplant"
    try:
        hh, mm = (int(x) for x in time_str.split(":"))
    except ValueError:
        return False, f"Uhrzeit nicht lesbar: {time_str}"

    wanted = {DAYS[d] for d in days if d in DAYS}
    if not wanted:
        return False, "kein gueltiger Wochentag"

    now = now or localnow()
    grace = timedelta(hours=max(1, grace_hours or 4))

    # Den zuletzt vergangenen passenden Termin suchen
    for offset in range(8):
        day = now - timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        slot = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot > now:
            continue
        if now - slot > grace:
            return False, "Termin liegt zu lange zurueck (Nachlauf abgelaufen)"

        # last_run steht in UTC, der Termin in Ortszeit. Ohne Umrechnung
        # waere der Vergleich um den Zeitzonenversatz daneben - der Lauf
        # koennte doppelt ausloesen oder ganz ausfallen.
        slot_utc = slot.astimezone(UTC)
        if last_run and last_run >= slot_utc:
            return _followup_due(host, slot_utc, session)
        return True, f"Termin {slot:%d.%m. %H:%M} faellig"

    return False, "kein vergangener Termin gefunden"


def patch_due(
    host: Host,
    now: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> tuple[bool, str]:
    """Prueft, ob jetzt der eigene Patch-Zeitplan des Hosts faellig ist."""
    return _schedule_due(
        host.patch_enabled, host.patch_days, host.patch_time,
        host.patch_grace_hours, host.last_patch_run, host, session, now,
    )


def area_patch_due(
    host: Host,
    area: Area,
    now: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> tuple[bool, str]:
    """
    Prueft, ob jetzt der Patch-Zeitplan des Bereichs faellig ist, fuer
    genau diesen Host.

    Absichtlich pro Host statt einmal pro Bereich: Hosts melden sich
    zeitversetzt per Heartbeat. Ein einzelnes last_run am Bereich wuerde
    beim ersten meldenden Host schon auf "erledigt" stehen, und jeder
    andere Host im selben Bereich bekaeme seinen Auftrag fuer diesen
    Termin nie - er wurde ja nie gefragt.
    """
    return _schedule_due(
        area.patch_enabled, area.patch_days, area.patch_time,
        area.patch_grace_hours, host.area_patch_last_run, host, session, now,
    )


# ======================================================================
# Ende eines Auftrags
# ======================================================================
def verfallsgrund(job: Job) -> str:
    """
    Warum ein wartender Auftrag verworfen wurde.

    Zwei Faelle, und sie haben nichts miteinander zu tun. Beim Neustart
    ist es der Nachlauf von Minuten, den jemand bewusst gewaehlt hat.
    Bei Scan und Patch ist es die weite Frist aus JOB_MAX_WAIT, von der
    niemand etwas weiss, bis sie zuschlaegt.

    Bis die Frist auch fuer Scan und Patch galt, gab es nur den ersten
    Fall. Der Text stand deshalb fest auf "Nachlauf" - und haette bei
    einem verfallenen Scan "Nachlauf None Minuten" gemeldet.
    """
    when = job.scheduled_at or job.created_at
    grace = (job.params or {}).get("grace_minutes")
    if job.job_type == JobType.reboot and grace is not None:
        return (
            f"Nicht ausgefuehrt: Nachlauf abgelaufen. Geplant war "
            f"{when:%d.%m.%Y %H:%M} UTC, Nachlauf {grace} Minuten. "
            f"Der Host hat sich in diesem Zeitraum nicht gemeldet."
        )
    return (
        f"Nicht ausgefuehrt: der Auftrag wartete seit "
        f"{when:%d.%m.%Y %H:%M} UTC auf den Host, laenger als die "
        f"Frist von {JOB_MAX_WAIT.days} Tagen. In dieser Zeit hat sich "
        f"der Host nicht gemeldet."
    )


def job_verstummt(job: Job, now: datetime) -> bool:
    """
    Ist zu einem laufenden Auftrag die Ausgabe versiegt?

    Aufgerufen im Heartbeat des Hosts - der Host MELDET sich also
    gerade. Waehrend eines Laufs schickt der Agent alle ein bis zwei
    Sekunden Zeilen an /agent/job-log; bleibt das ueber
    JOB_LOG_SILENCE aus, obwohl der Host ansprechbar ist, fuehrt er den
    Auftrag nicht mehr aus.

    Eigene Funktion, nicht in der Schleife: nur so laesst sich die Regel
    mit vorgespulter Zeit pruefen, ohne eine halbe Stunde zu warten
    (tests/jobstille-test.py).

    Zwei Ausnahmen, beide mit Anlass:

    Gekuerztes Protokoll. Ist die Obergrenze erreicht, stellt der Agent
    das Senden fuer diesen Auftrag GANZ ein (LogSink.stopped) - auch den
    Fortschritt. Die Stille sagt dann nichts ueber den Lauf, und ein
    langer Patchlauf waere genau der Fall, der sie ausloest. Fuer solche
    Auftraege bleibt die Hoechstlaufzeit die einzige Schranke.

    Neustart des Backends. War das Backend weg, konnte kein Agent Zeilen
    liefern - seine Fehler dabei schluckt er stillschweigend
    (LogSink.flush faengt alles ab). Ohne BACKEND_START raeumte der
    erste Heartbeat nach jedem Backend-Update alle laufenden Auftraege
    ab, obwohl die Hosts durchgehend gearbeitet haben.
    """
    if (job.params or {}).get("log_truncated"):
        return False
    regung = job.last_progress_at or job.started_at
    if regung is None:
        return False
    return now - max(regung, BACKEND_START) > JOB_LOG_SILENCE


def job_vom_server_abbrechen(job: Job, now: datetime, grund: str) -> None:
    """
    Bricht einen laufenden Auftrag ab, weil der SERVER ihn aufgibt - der
    Agent hat dazu nichts gesagt.

    Der Vermerk in params ist der Unterschied, auf den es ankommt: eine
    Meldung, die danach doch noch eintrifft, darf einen so abgebrochenen
    Auftrag berichtigen (siehe agent_report). Einen Zustand, den der
    Agent selbst gemeldet hat, darf sie nicht ueberschreiben.
    """
    job.state = JobState.cancelled
    job.finished_at = now
    job.error = grund
    params = dict(job.params or {})
    params["server_cancelled"] = True
    job.params = params


# ======================================================================
# Was der Heartbeat an Scheduling tut
# ======================================================================
# Die beiden folgenden Funktionen standen bis 0.49.0 inline in
# agent_heartbeat(). Sie sind woertlich uebernommen - wer hier nach einer
# Verhaltensaenderung sucht, sucht vergeblich.
#
# 'payload' ist bewusst OHNE Typangabe: die Klasse AgentHeartbeat steht in
# main.py, und ein Import von dort waere genau der Kreis, den dieses Modul
# vermeidet. Gebraucht werden daraus nur reboot_pending, reboot_required
# und boot_time.


def patch_auftraege_planen(session: Session, host: Host, payload) -> None:
    """
    Legt faellige Patch-Auftraege an: Nachschlag, eigener Zeitplan,
    Zeitplan des Bereichs.

    Bewusst beim Heartbeat statt in einem Hintergrunddienst - der Agent
    meldet sich ohnehin regelmaessig, und so kann nichts auseinanderlaufen.
    """
    def patch_job_open() -> bool:
        """Laeuft oder wartet bereits ein Patch-Auftrag fuer diesen Host?"""
        return session.exec(
            select(Job).where(
                Job.host_id == host.id,
                Job.job_type == JobType.patch,
                Job.state.in_([JobState.pending, JobState.running]),
            )
        ).first() is not None

    # Vorgemerkter Nachschlag nach einem Neustart. Hat Vorrang vor dem
    # Zeitplan und laeuft bewusst ohne Ruecksicht auf den Nachlauf
    # (patch_grace_hours): ein Windows-Server kann laenger neu starten, als
    # der Nachlauf reicht, und die nachgelegten Updates blieben sonst bis
    # zum naechsten Termin liegen.
    if host.patch_followup_left > 0 and not payload.reboot_pending:
        if payload.reboot_required:
            # Neustart steht weiter aus - der Nachschlag wartet.
            pass
        elif host.updates_available <= 0:
            # Nichts mehr offen, Kette beenden.
            host.patch_followup_left = 0
            session.add(host)
        elif not patch_job_open():
            host.patch_followup_left -= 1
            session.add(Job(
                host_id=host.id,
                job_type=JobType.patch,
                params={
                    "scheduled": True,
                    "followup": True,
                    "reboot_if_needed": host.patch_auto_reboot,
                    "downtime_minutes": host.downtime_minutes,
                },
                expires_at=utcnow() + JOB_MAX_WAIT,
            ))
            host.last_patch_run = utcnow()
            session.add(host)

    # Zeitplan pruefen und bei Faelligkeit einen Patch-Auftrag anlegen.
    due, reason = patch_due(host, session=session)
    if due and not payload.reboot_pending:
        if not patch_job_open():
            session.add(Job(
                host_id=host.id,
                job_type=JobType.patch,
                params={
                    "scheduled": True,
                    "reboot_if_needed": host.patch_auto_reboot,
                    "downtime_minutes": host.downtime_minutes,
                },
                expires_at=utcnow() + JOB_MAX_WAIT,
            ))
            host.last_patch_run = utcnow()
            session.add(host)

    # Zeitplan des Bereichs pruefen, unabhaengig vom eigenen Zeitplan oben -
    # beide koennen nebeneinander laufen, das wird beim Speichern des
    # Bereichs nur angezeigt, nicht verhindert. patch_job_open() sorgt
    # dafuer, dass nie zwei Patch-Auftraege gleichzeitig fuer denselben
    # Host entstehen: trifft der Bereichs-Termin auf einen schon laufenden
    # Lauf (gleich welcher Herkunft), wird er uebersprungen und beim
    # naechsten Termin neu geprueft - kein doppeltes Patchen, aber auch
    # keine Sonderbehandlung noetig.
    if host.area_id and not payload.reboot_pending:
        area = session.get(Area, host.area_id)
        if area:
            area_due, area_reason = area_patch_due(host, area, session=session)
            if area_due and not patch_job_open():
                session.add(Job(
                    host_id=host.id,
                    job_type=JobType.patch,
                    params={
                        "scheduled": True,
                        "source_area_id": area.id,
                        "reboot_if_needed": area.patch_auto_reboot,
                        "downtime_minutes": area.downtime_minutes,
                    },
                    expires_at=utcnow() + JOB_MAX_WAIT,
                ))
                host.area_patch_last_run = utcnow()
                session.add(host)


def boot_zeit(payload, now: datetime) -> Optional[datetime]:
    """
    Der Startzeitpunkt des Hosts aus der gemeldeten boot_time - oder
    None, wenn der Wert nicht plausibel ist.

    Plausibilitaetsband (F-41 der Pruefung vom 2026-08-31):
    from_timestamp() ist datetime.fromtimestamp() und wirft bei grossen
    Werten - 1e18 gibt OSError, 1e30 OverflowError. Das fing niemand ab:
    ein Agent, der einmal Unsinn meldet, bekam bei jedem Heartbeat einen
    500 und fiel damit aus dem Betrieb. Ein Wert weit in der Zukunft
    bricht ausserdem ohne Fehler alle laufenden Auftraege des Hosts ab.

    Ausserhalb des Bandes wird der WERT verworfen, nicht der Heartbeat:
    boot_time ist eine Hilfsangabe, kein Grund, einen Host stillzulegen.

    Der Rueckgabewert traegt die Zeitzone UTC und wird nicht davon
    befreit. Vorher stand an der Aufrufstelle ein .replace(tzinfo=None):
    der Vergleich mit job.started_at warf dann den TypeError, fuer den
    utctime.py gerade gebaut ist - beim Agent kam er als 500 an, und der
    Heartbeat schlug dauerhaft fehl.

    Steht als eigene Funktion da, weil zwei Stellen sie brauchen -
    auftraege_aufraeumen() und scan_nach_neustart(). Eine zweite Fassung
    desselben Bandes waere derselbe Fehler, der am 2026-11-10 in
    list_jobs() steckte: ein Wert an zwei Stellen, von denen nur eine
    eine Korrektur bekommt.
    """
    if not payload.boot_time or payload.boot_time <= 0:
        return None
    # Nach unten das Jahr 2000, nach oben eine Stunde Vorlauf fuer Uhren,
    # die nicht genau gehen.
    if not (946684800 < payload.boot_time < now.timestamp() + 3600):
        return None
    return from_timestamp(payload.boot_time)


# Wie weit die gemeldete boot_time wandern darf, ohne als neuer Start zu
# gelten.
#
# Der Wert kommt als Gleitkommazahl vom Host und verschiebt sich um
# Sekunden, wenn NTP die Uhr stellt. Auf Gleichheit zu pruefen hiesse,
# bei solchen Korrekturen einen Scan anzulegen, ohne dass etwas gestartet
# ist. Ein echter Neustart verschiebt boot_time um die ganze vorige
# Laufzeit, also um Minuten bis Monate - zwei Minuten trennen das
# zuverlaessig, und es ist dieselbe Spanne, die der Abbruch laufender
# Auftraege weiter unten benutzt.
BOOT_TOLERANZ = timedelta(minutes=2)


def scan_nach_neustart(session, host, booted_at) -> None:
    """
    Hat der Host neu gestartet, einen Scan anlegen.

    DER ANLASS (gemeldet am 2026-11-12)

    host.updates_available wird an genau EINER Stelle geschrieben, in
    /agent/scan-result. Der Agent scannt nach einem Patchlauf zwar nach,
    aber VOR dem Neustart - und da zaehlt Windows das installierte, auf
    den Neustart wartende Update noch als offen. Danach scannte nichts
    mehr: der Neustart-Auftrag scannt gar nicht, und die Oberflaeche
    zeigte die Zahl von vor dem Neustart, bis jemand von Hand scannte.

    Aufgefallen ist es nur manchmal, und das hatte einen Grund: startet
    der Agent SELBST neu (patch_auto_reboot), merkt der Server einen
    Nachschlag vor, und dessen Patchlauf scannt nach dem Neustart - es
    heilte von selbst. Startet ein Mensch neu, und das ist die Vorgabe,
    wurde kein Nachschlag vorgemerkt und nichts heilte.

    DREI BREMSEN

    - Beim ERSTEN Sehen wird nur gespeichert. Nach dem Einspielen ist
      last_boot bei jedem Host leer; ein Scan an dieser Stelle hiesse,
      dass die ganze Anlage gleichzeitig scannt.
    - Innerhalb von BOOT_TOLERANZ gilt der Start als derselbe.
    - Kein zweiter Scan, solange einer wartet oder laeuft. Ein Host, der
      mehrfach startet, soll keine Auftraege stapeln.

    Der Auftrag ist ein REINER Scan, ohne patch_after. Nach einem
    Neustart ungefragt zu patchen waere eine Verhaltensaenderung, die
    niemand verlangt hat - und bei einem Server, der gerade hochgefahren
    ist, die falsche.
    """
    if booted_at is None:
        return

    if host.last_boot is None:
        host.last_boot = booted_at
        session.add(host)
        return

    if abs(booted_at - host.last_boot) <= BOOT_TOLERANZ:
        return

    host.last_boot = booted_at
    session.add(host)

    offen = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.job_type == JobType.scan,
            Job.state.in_([JobState.pending, JobState.running]),
        )
    ).first()
    if offen is not None:
        return

    session.add(Job(
        host_id=host.id,
        job_type=JobType.scan,
        # Der Vermerk steht nur da, damit in der Auftragsliste erkennbar
        # ist, warum dieser Scan von selbst auftauchte.
        params={"nach_neustart": True},
        expires_at=utcnow() + JOB_MAX_WAIT,
    ))


def auftraege_aufraeumen(
    session: Session, host: Host, payload, now: datetime
) -> None:
    """
    Raeumt Auftraege auf, bevor irgendetwas ausgeliefert wird: verfallene
    wartende und laufende ohne Abschluss.
    """
    # Abgelaufene Auftraege verwerfen, bevor irgendetwas ausgeliefert wird.
    # Sonst wuerde ein fuer Samstag nachts geplanter Neustart Montag frueh
    # ausgefuehrt, wenn der Host ueber das Wochenende aus war.
    expired = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.state == JobState.pending,
            Job.expires_at != None,          # noqa: E711
            Job.expires_at <= now,
        )
    ).all()
    for job in expired:
        job.state = JobState.cancelled
        job.finished_at = now
        job.error = verfallsgrund(job)
        session.add(job)

    # Laufende Auftraege ohne Abschluss einsammeln. Ein Auftrag, dessen
    # Agent mitten im Lauf verschwindet - Absturz, Neustart, misslungene
    # Selbstaktualisierung - bleibt sonst dauerhaft auf running stehen und
    # blockiert jeden weiteren Auftrag desselben Typs fuer diesen Host,
    # ohne dass irgendwo ein Grund sichtbar waere.
    #
    # Zwei Wege. Der erste ist die Boot-Zeit: hat der Host seit dem Beginn
    # des Auftrags neu gestartet, kann der Agent ihn nicht mehr fortsetzen -
    # er hat kein Gedaechtnis ueber einen Neustart hinweg. Das steht sofort
    # fest, statt vier Stunden auf die Hoechstlaufzeit zu warten, waehrend
    # der Auftrag jeden weiteren desselben Typs blockiert.
    #
    # Der zweite ist die Hoechstlaufzeit als Auffangnetz - fuer Abstuerze
    # ohne Neustart und fuer Agents, die keine Boot-Zeit liefern.

    booted_at = boot_zeit(payload, now)

    for job in session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.state == JobState.running,
            Job.started_at != None,          # noqa: E711
        )
    ).all():
        limit = JOB_MAX_RUNTIME.get(job.job_type, timedelta(hours=1))

        # Kleiner Vorlauf, weil die Uhren von Host und Server nicht exakt
        # gleich gehen. Ohne ihn koennte ein Auftrag, der Sekunden nach dem
        # Start begonnen hat, faelschlich als abgeschnitten gelten.
        if booted_at and job.started_at < booted_at - timedelta(minutes=2):
            job_vom_server_abbrechen(job, now, (
                "Abgebrochen: der Host hat neu gestartet, waehrend der Auftrag "
                "lief. Der Agent kann ihn nach einem Neustart nicht "
                "fortsetzen."
            ))
            session.add(job)
            continue

        # Dritter Weg: der Host meldet sich - wir stehen in seinem
        # Heartbeat -, aber zu seinem laufenden Auftrag kommt nichts
        # mehr. Etwa, weil die Schlussmeldung des Agenten in einen
        # Verbindungsfehler lief und nirgends gepuffert wird (am
        # 2026-10-21 auf einem verwalteten Host genau so passiert). Die
        # Regel samt ihren beiden Ausnahmen steht in job_verstummt().
        if job_verstummt(job, now):
            still_seit = job.last_progress_at or job.started_at
            job_vom_server_abbrechen(job, now, (
                f"Abgebrochen: seit "
                f"{int((now - still_seit).total_seconds() // 60)} Minuten "
                f"keine Ausgabe mehr, obwohl der Host sich meldet. Der "
                f"Agent fuehrt den Auftrag nicht mehr aus."
            ))
            session.add(job)
            continue

        if now - job.started_at <= limit:
            continue
        job_vom_server_abbrechen(job, now, (
            f"Abgebrochen: seit {int((now - job.started_at).total_seconds() // 60)} "
            f"Minuten ohne Rueckmeldung (Grenze "
            f"{int(limit.total_seconds() // 60)} Minuten). Der Agent hat den "
            f"Auftrag nie abgeschlossen."
        ))
        session.add(job)
