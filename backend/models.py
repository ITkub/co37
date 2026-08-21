"""
CO-37 - Datenmodelle

Betriebsart ab v0.4: ausschliesslich lokales Netz. Es gibt keine
Enrollment-Tokens mehr. Agents melden sich frei an und landen im Zustand
'pending'; Schutz ist allein die Freigabe im Dashboard.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Column, Field, JSON, SQLModel

from utctime import UTCDateTime, utcnow


class OSType(str, Enum):
    windows = "windows"
    linux = "linux"


class HostStatus(str, Enum):
    online = "online"
    offline = "offline"
    unknown = "unknown"


class ApprovalState(str, Enum):
    """
    Ein neu angemeldeter Host bekommt erst Auftraege, wenn er im Dashboard
    freigegeben wurde. Das ist der einzige Schutz gegen fremde Anmeldungen,
    da im LAN-Betrieb kein Enrollment-Token verlangt wird.
    """
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class JobType(str, Enum):
    scan = "scan"
    patch = "patch"
    reboot = "reboot"
    selfupdate = "selfupdate"     # Agent aktualisiert sich selbst


class JobState(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Host(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    # Identitaet
    hostname: str = Field(index=True)
    display_name: Optional[str] = None
    # Darf leer sein: dann wurde das Token zurueckgezogen und der Host
    # wartet auf eine erneute Anmeldung. SQLite laesst mehrere NULL-Werte
    # in einem eindeutigen Index zu, mehrere Hosts koennen also
    # gleichzeitig in diesem Zustand stehen.
    agent_token_hash: Optional[str] = Field(default=None, index=True, unique=True)

    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))

    # Freigabe - Vorgabe pending, da alle Hosts durch Anmeldung entstehen
    approval_state: ApprovalState = Field(default=ApprovalState.pending, index=True)
    enrolled_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    enrolled_from_ip: Optional[str] = None

    # System
    os_type: Optional[OSType] = None
    os_version: Optional[str] = None
    ip_address: Optional[str] = None
    agent_version: Optional[str] = None

    # Zustand
    status: HostStatus = Field(default=HostStatus.unknown)
    last_seen: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    # Kam der letzte Kontakt verschluesselt ueber den Proxy? Nur zur
    # Anzeige: bevor der HTTPS-Zwang eingeschaltet wird, soll sichtbar
    # sein, welche Agents noch unverschluesselt sprechen. Die wuerden
    # sonst mit dem Umschalten still ausfallen.
    last_seen_secure: bool = Field(default=False)
    last_scan: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    updates_available: int = Field(default=0)
    security_updates: int = Field(default=0)
    reboot_required: bool = Field(default=False)
    # Warum ein Neustart angezeigt ist. Ohne den Grund laesst sich nicht
    # unterscheiden, ob Windows selbst einen braucht oder ob nur OneDrive
    # eine DLL zum Aufraeumen vorgemerkt hat.
    reboot_reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    # Vorschau statt Zustand: erfordern die *gefundenen* Updates einen
    # Neustart? Bewusst getrennt von reboot_required, das den aktuellen
    # Zustand meldet ("steht jetzt einer aus"). Beides in ein Feld zu
    # legen waere falsch - ein Kernel-Update im Scan wuerde den Host als
    # neustartbeduerftig markieren, obwohl noch nichts installiert ist,
    # und damit unter anderem den Nachschlag nach dem Patchen blockieren.
    updates_require_reboot: bool = Field(default=False)

    # Checkmk: mehrere Hosts moeglich. Faellt der Host aus, sind oft weitere
    # Objekte mit betroffen - Cluster-Ressourcen, Dienste, Anwendungen.
    checkmk_hosts: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    downtime_minutes: int = Field(default=30)
    # Downtime auf alle in Checkmk konfigurierten Hosts statt nur auf die
    # verknuepften. Noetig, wenn dieser Host das Monitoring selbst traegt -
    # etwa der Virtualisierer, auf dem die Checkmk-Maschine laeuft. Faellt
    # er aus, meldet jeder ueberwachte Host aus, ohne selbst betroffen zu
    # sein. Bewusst pro Host und nicht global: es trifft nur wenige.
    checkmk_downtime_all: bool = Field(default=False)

    # Patch-Richtlinie
    auto_reboot: bool = Field(default=False)
    maintenance_window: Optional[str] = None   # z.B. "SA,SO 02:00-05:00"

    # Zeitplan fuer Updates. Wird beim Heartbeat ausgewertet - kein
    # Hintergrunddienst. Genauigkeit entspricht dem Abfrageintervall, was
    # fuer ein naechtliches Fenster ausreicht.
    patch_enabled: bool = Field(default=False)
    patch_days: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    patch_time: Optional[str] = None            # "03:00" in Serverzeit
    patch_auto_reboot: bool = Field(default=False)
    patch_grace_hours: int = Field(default=4)   # Nachlauf, falls Host aus war
    last_patch_run: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))

    # Offene Nachschlaege nach einem Patch-Lauf mit Neustart. Windows legt
    # kumulativ nach: erst nach dem Neustart werden die Nachfolger sichtbar
    # und installierbar. Der Zaehler laeuft ueber den Neustart hinweg und
    # begrenzt zugleich die Kette - sonst koennte sich Patchen, Neustarten,
    # wieder Patchen endlos fortsetzen.
    patch_followup_left: int = Field(default=0)

    # Anzeigereihenfolge in der Uebersicht, per Drag and Drop gesetzt.
    # Reine Darstellung: sie beeinflusst weder Zeitplaene noch die
    # Reihenfolge, in der Auftraege ausgefuehrt werden - es gibt keine.
    # Neue Hosts werden hinten angehaengt.
    sort_order: int = Field(default=0, index=True)

    # Bereich, in dem der Host in der Uebersicht steht. None heisst "ohne
    # Bereich" - genau wie bisher, ganz oben in der flachen Liste.
    area_id: Optional[int] = Field(default=None, foreign_key="area.id", index=True)

    # Eigene Merkstelle fuer den Zeitplan des Bereichs, getrennt von
    # last_patch_run. Ein einzelnes Feld am Bereich wuerde nicht reichen:
    # Hosts melden sich zeitversetzt per Heartbeat, und der zuerst meldende
    # Host wuerde den Termin fuer alle anderen im selben Bereich als
    # erledigt markieren, bevor sie ueberhaupt gefragt wurden.
    area_patch_last_run: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))

    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))


class Area(SQLModel, table=True):
    """
    Gruppiert Hosts in der Uebersicht. Rein optional - ohne Bereiche
    verhaelt sich CO-37 genau wie bisher.

    Traegt dieselben Felder wie ein Host fuer Checkmk-Verknuepfung und
    Update-Zeitplan, mit derselben Bedeutung. Der Zeitplan hier laeuft
    unabhaengig neben einem etwaigen eigenen Zeitplan der enthaltenen
    Hosts her - beide koennen sich ueberschneiden, das wird beim Speichern
    nur angezeigt, nicht verhindert.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str

    # Eigene Reihenfolge der Bereiche, getrennt von Host.sort_order.
    sort_order: int = Field(default=0, index=True)

    checkmk_hosts: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    checkmk_downtime_all: bool = Field(default=False)
    downtime_minutes: int = Field(default=30)

    patch_enabled: bool = Field(default=False)
    patch_days: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    patch_time: Optional[str] = None
    patch_auto_reboot: bool = Field(default=False)
    patch_grace_hours: int = Field(default=4)

    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key="host.id", index=True)

    job_type: JobType
    state: JobState = Field(default=JobState.pending)

    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Geplante Ausfuehrung. Ist gesetzt, gibt der Heartbeat den Auftrag erst
    # heraus, wenn der Zeitpunkt erreicht ist. Bewusst ohne Hintergrunddienst:
    # die Pruefung passiert beim Abholen, laeuft also nicht auseinander und
    # uebersteht einen Neustart des Backends.
    scheduled_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime, index=True))

    # Verfallszeitpunkt. Meldet sich der Host erst danach, wird der Auftrag
    # verworfen statt ausgefuehrt. Ohne das wuerde ein fuer Samstag nachts
    # geplanter Neustart Montag frueh im Betrieb erfolgen, wenn der Host
    # ueber das Wochenende aus war.
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime, index=True))
    # Wie oft der Auftrag schon an den Agent ausgeliefert wurde.
    #
    # Ein ausgelieferter Auftrag wird nicht mehr sofort auf running
    # gesetzt - sonst geht er verloren, wenn der Agent zwischen Abholen
    # und Ausfuehren neu startet. Das passiert regelmaessig beim
    # Selfupdate. Er bleibt pending und wird erneut ausgeliefert; running
    # setzt erst die erste Rueckmeldung des Agenten.
    #
    # Der Zaehler begrenzt das: ein Auftrag, der den Agent reproduzierbar
    # zum Absturz bringt, wuerde sonst endlos wiederkommen.
    delivered: int = Field(default=0)
    last_delivered_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))

    log: Optional[str] = None
    error: Optional[str] = None

    # Kennzeichnung der fuer diesen Job gesetzten Downtime
    downtime_ref: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))
    started_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))


class UpdatePackage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int = Field(foreign_key="host.id", index=True)

    package_id: str
    title: str
    current_version: Optional[str] = None
    new_version: Optional[str] = None
    is_security: bool = Field(default=False)
    requires_reboot: bool = Field(default=False)
    size_bytes: Optional[int] = None

    detected_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class JobLogChunk(SQLModel, table=True):
    """
    Ein Abschnitt der Live-Ausgabe eines Auftrags.

    Angehaengt statt ueberschrieben: wuerde der Agent alle zwei Sekunden ein
    wachsendes Textfeld aktualisieren, muesste die gesamte bisherige Ausgabe
    jedes Mal neu geschrieben werden. Nach Abschluss werden die Abschnitte in
    Job.log zusammengefasst und geloescht.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    seq: int = Field(index=True)
    content: str
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))


# ======================================================================
# Benutzer, Sitzungen, Pruefprotokoll
# ======================================================================
class Role(str, Enum):
    admin = "admin"      # darf zusaetzlich Benutzer verwalten
    user = "user"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    role: Role = Field(default=Role.user)

    # scrypt aus hashlib - keine zusaetzliche Abhaengigkeit noetig.
    # Format: scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
    password_hash: str

    # Solange gesetzt, fuehrt jede Anmeldung direkt auf die Passwortmaske.
    # Verhindert, dass die Erstinstallation dauerhaft auf admin/admin steht.
    must_change_password: bool = Field(default=False)

    disabled: bool = Field(default=False)

    # Sprache der Oberflaeche. None heisst ausdruecklich "nicht gewaehlt",
    # dann gilt die Vorgabe der Installation. Ein Vorgabewert 'en' waere
    # hier falsch: er liesse sich nicht mehr von einer bewussten Wahl
    # unterscheiden, und eine spaeter geaenderte Vorgabe erreichte
    # bestehende Benutzer nie.
    language: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))
    last_login: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))


class LoginSession(SQLModel, table=True):
    """
    Anmeldesitzung. Der Token steht nur gehasht in der Datenbank - wer sie
    liest, kann sich damit nicht anmelden.

    Bewusst nicht 'Session' genannt: main.py importiert Session aus
    sqlmodel, der Name waere dort doppelt belegt.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))
    expires_at: datetime = Field(sa_column=Column(UTCDateTime, index=True))
    last_seen: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    from_ip: Optional[str] = None


class InstallToken(SQLModel, table=True):
    """
    Kurzlebiges Token zum Abholen des Agent-Pakets.

    Ersetzt den frueheren Dauerschluessel. Der stand im
    Installationsbefehl, wurde auf jedem neuen Host in eine Shell
    eingefuegt und blieb dort in der Verlaufsdatei stehen - dauerhaft
    gueltig. Aufraeumen half wenig: Rueckblaetterpuffer und Mitschnitte
    des SSH-Programms erreicht man ohnehin nicht.

    Hier bleibt zurueck, was von vornherein wertlos ist: nach Ablauf der
    Frist oder nach wenigen Abrufen ist das Token tot. Wie die
    Anmeldesitzungen nur gehasht abgelegt.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime))
    expires_at: datetime = Field(sa_column=Column(UTCDateTime, index=True))
    uses: int = Field(default=0)
    max_uses: int = Field(default=3)
    created_by: str = ""
    last_used_at: Optional[datetime] = Field(default=None, sa_column=Column(UTCDateTime))
    last_used_ip: Optional[str] = None


class AuditEntry(SQLModel, table=True):
    """
    Pruefprotokoll. Wird nur angehaengt - es gibt bewusst keine Route zum
    Aendern oder Loeschen. Wer sich selbst herausschreiben kann, macht das
    Protokoll wertlos.

    Festgehalten wird, was tatsaechlich geschah, auch abgelehnte Versuche.
    Ein gescheiterter Anmeldeversuch ist oft aufschlussreicher als ein
    erfolgreicher.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, sa_column=Column(UTCDateTime, index=True))
    actor: str = Field(index=True)       # Benutzername, "api-key" oder "?"
    action: str = Field(index=True)      # z.B. "login.failed", "host.approve"
    detail: Optional[str] = None
    from_ip: Optional[str] = None
