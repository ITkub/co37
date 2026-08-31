"""
CO-37 - Schema-Migration

Grund: SQLModel.metadata.create_all() legt nur fehlende Tabellen an und
aendert bestehende nicht. Nach einem Update mit geaendertem Datenmodell
laeuft jede Abfrage auf "no such column" und liefert 500.

Genau das ist beim Umstieg von 0.3.x auf 0.4.0 passiert. Dieses Modul
gleicht das Schema vor dem ersten Zugriff ab.

Vorgehen:
  1. Tatsaechliche Spalten per PRAGMA table_info auslesen
  2. Fehlende Spalten per ALTER TABLE ergaenzen
  3. Bekannte Umbenennungen mit Datenuebernahme behandeln
  4. Schema-Version in der Settings-Tabelle festhalten

Absichtlich ohne Alembic: eine einzelne SQLite-Datei, ueberschaubares
Modell, und der Watcher muss die Migration ohne Zusatzwerkzeug ausfuehren
koennen.
"""
import json
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 7: Benutzer, Anmeldesitzungen und Pruefprotokoll. Die drei Tabellen legt
#    create_all() an - hier steht nur die Nummer, damit die Aenderung im
#    Health-Endpunkt sichtbar wird.
# 8: host.reboot_reasons - warum ein Neustart angezeigt ist.
# 9: job.delivered / job.last_delivered_at - ein ausgelieferter Auftrag
#    gilt erst als laufend, wenn der Agent sich zurueckmeldet.
# 10: host.checkmk_downtime_all - Downtime auf alle Checkmk-Hosts statt
#    nur auf die verknuepften.
# 11: host.sort_order - selbst bestimmte Anzeigereihenfolge.
# 12: host.patch_followup_left - offene Nachschlaege ueber einen Neustart
#    hinweg.
# 13: host.updates_require_reboot - erfordern die gefundenen Updates einen
#    Neustart? Getrennt vom aktuellen Zustand reboot_required.
# 14: Tabelle installtoken - kurzlebiges Token statt Dauerschluessel fuer
#    das Abholen des Agent-Pakets. Legt create_all() an; hier steht nur
#    die Nummer, damit die Aenderung im Health-Endpunkt sichtbar wird.
# 15: host.last_seen_secure - kam der letzte Kontakt verschluesselt ueber
#    den Proxy? Grundlage fuer die Bereitschaftsanzeige vor dem
#    Umschalten auf HTTPS-Zwang.
# 16: user.language - Sprache der Oberflaeche je Konto. Ohne Vorgabewert:
#    NULL heisst "nie gewaehlt", dann gilt die Vorgabe der Installation.
# 17: Tabelle area - Bereiche zum Gruppieren von Hosts in der Uebersicht,
#    mit eigener Checkmk-Verknuepfung und eigenem Update-Zeitplan. Legt
#    create_all() an, hier steht nur die Nummer. host.area_id verweist auf
#    den Bereich (None = ohne Bereich, wie bisher). host.area_patch_last_run
#    haelt den Bereichs-Zeitplan pro Host fest, getrennt von last_patch_run -
#    sonst wuerde der zuerst meldende Host im Bereich den Termin fuer alle
#    anderen als erledigt markieren.
# 18: user.may_reboot - das Recht, Neustarts anzulegen und einzuplanen.
#    Vorgabe 0, also NEIN, und zwar auch fuer alle BESTEHENDEN Konten.
#    Das ist Absicht: die Einschraenkung soll beim Einspielen greifen und
#    nicht erst, wenn jemand daran denkt. Administratoren sind davon
#    unberuehrt, sie duerfen es ueber ihre Rolle. Wer einem vorhandenen
#    'user'-Konto das Recht geben will, hakt es danach in der
#    Benutzerverwaltung an.
# 19: eindeutiger Index ueber lower(hostname). Keine neue Spalte, deshalb
#    faellt er bei verify() nicht auf - die Zahl steht hier trotzdem, weil
#    ein Rueckschritt auf 18 den Index nicht kennt und ein Doppeleintrag
#    dann wieder entstehen kann. Siehe _hostname_index().
SCHEMA_VERSION = 19

# Spalten, die es in 0.4.0 gibt. Fehlen sie, werden sie ergaenzt.
EXPECTED_COLUMNS = {
    "host": {
        "checkmk_hosts": "JSON",
        "reboot_reasons": "JSON",
        "downtime_minutes": "INTEGER DEFAULT 30",
        "checkmk_downtime_all": "BOOLEAN DEFAULT 0",
        "sort_order": "INTEGER DEFAULT 0",
        "patch_followup_left": "INTEGER DEFAULT 0",
        "updates_require_reboot": "BOOLEAN DEFAULT 0",
        "last_seen_secure": "BOOLEAN DEFAULT 0",
        "approval_state": "VARCHAR DEFAULT 'approved'",
        "enrolled_at": "DATETIME",
        "enrolled_from_ip": "VARCHAR",
        "maintenance_window": "VARCHAR",
        "auto_reboot": "BOOLEAN DEFAULT 0",
        "tags": "JSON",
        "agent_token_hash": "VARCHAR",
        "patch_enabled": "BOOLEAN DEFAULT 0",
        "patch_days": "JSON",
        "patch_time": "VARCHAR",
        "patch_auto_reboot": "BOOLEAN DEFAULT 0",
        "patch_grace_hours": "INTEGER DEFAULT 4",
        "last_patch_run": "DATETIME",
        "area_id": "INTEGER",
        "area_patch_last_run": "DATETIME",
    },
    "job": {
        "delivered": "INTEGER DEFAULT 0",
        "last_delivered_at": "DATETIME",
        "downtime_ref": "VARCHAR",
        "scheduled_at": "DATETIME",
        "expires_at": "DATETIME",
    },
    "user": {
        # Ohne DEFAULT: NULL heisst "hat sich nie entschieden", und dann
        # gilt die Vorgabe der Installation. Ein DEFAULT 'en' machte aus
        # jedem bestehenden Konto eine bewusste Wahl fuer Englisch - eine
        # spaeter geaenderte Vorgabe erreichte diese Konten nie mehr.
        "language": "VARCHAR",
        # Siehe SCHEMA_VERSION 18 oben: bewusst DEFAULT 0, damit auch
        # bestehende Konten das Recht erst bekommen, wenn es jemand
        # ausdruecklich vergibt.
        "may_reboot": "BOOLEAN DEFAULT 0",
    },
}

# Felder, die entfallen sind. Sie werden NICHT geloescht - SQLite kann das
# nur ueber einen Tabellenneubau, und ein ungenutztes Feld stoert nicht.
OBSOLETE = {
    "host": ["customer", "checkmk_site", "agent_token", "checkmk_host"],
    "job": ["downtime_id", "created_by"],
}


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def migrate(engine: Engine) -> dict:
    """
    Gleicht das Schema ab. Gibt einen Bericht zurueck, damit das Ergebnis
    im Log und im Health-Endpunkt sichtbar ist.
    """
    report = {"added": [], "migrated": [], "notes": [], "version": SCHEMA_VERSION}
    tables = _tables(engine)

    if "host" not in tables:
        # Frische Datenbank - create_all() erledigt alles.
        report["notes"].append("Neue Datenbank, keine Migration notwendig")
        _set_version(engine, SCHEMA_VERSION)
        return report

    # Auch auf einer frischen Anlage noetig: create_all() legt die
    # Tabellen an, aber keinen Index ueber lower(hostname) - den kennt
    # SQLModel nicht. Deshalb hier und nicht bei den Spalten.
    _hostname_index(engine, report)

    with engine.begin() as conn:
        for table, columns in EXPECTED_COLUMNS.items():
            if table not in tables:
                continue
            have = _columns(conn, table)
            for name, sqltype in columns.items():
                if name in have:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"))
                report["added"].append(f"{table}.{name}")
                logger.warning("Schema: Spalte %s.%s ergaenzt", table, name)

        have_host = _columns(conn, "host")

        # 0.3.x: ein einzelner Checkmk-Host -> Liste
        if "checkmk_host" in have_host:
            rows = conn.execute(text(
                "SELECT id, checkmk_host, checkmk_hosts FROM host "
                "WHERE checkmk_host IS NOT NULL AND checkmk_host != ''"
            )).fetchall()
            for row in rows:
                existing = row[2]
                if existing not in (None, "", "[]", "null"):
                    continue      # bereits uebernommen
                conn.execute(
                    text("UPDATE host SET checkmk_hosts = :val WHERE id = :id"),
                    {"val": json.dumps([row[1]]), "id": row[0]},
                )
                report["migrated"].append(f"host {row[0]}: checkmk_host -> checkmk_hosts")

        # 0.3.x kannte kein agent_token_hash, sondern agent_token im Klartext.
        # Der Klartext laesst sich nicht in einen Hash rueckfuehren, ohne die
        # Agents zu erreichen - diese Hosts muessen sich neu anmelden.
        if "agent_token" in have_host and "agent_token_hash" in have_host:
            orphans = conn.execute(text(
                "SELECT COUNT(*) FROM host "
                "WHERE agent_token_hash IS NULL OR agent_token_hash = ''"
            )).scalar()
            if orphans:
                report["notes"].append(
                    f"{orphans} Host(s) ohne Token-Hash. Diese Agents muessen "
                    f"sich neu anmelden."
                )

        # Leere JSON-Felder auf gueltige Werte setzen, sonst scheitert das Lesen
        for col in ("checkmk_hosts", "tags", "patch_days"):
            if col in have_host:
                conn.execute(text(
                    f"UPDATE host SET {col} = '[]' "
                    f"WHERE {col} IS NULL OR {col} = '' OR {col} = 'null'"
                ))

        if "downtime_minutes" in have_host:
            conn.execute(text(
                "UPDATE host SET downtime_minutes = 30 "
                "WHERE downtime_minutes IS NULL OR downtime_minutes < 1"
            ))

        # NULL in Zahlen- und Wahrheitsfeldern: die alte Datenbank liess das
        # zu, das Ausgabeschema nicht. Ohne diesen Schritt liefert die
        # Hostliste einen Validierungsfehler statt Daten.
        defaults = {
            "host": {
                "updates_available": "0",
                "security_updates": "0",
                "reboot_required": "0",
                "auto_reboot": "0",
                "patch_enabled": "0",
                "patch_auto_reboot": "0",
                "patch_grace_hours": "4",
                "status": "'unknown'",
                "hostname": "'unbekannt'",
            },
            "job": {
                "params": "'{}'",
                "state": "'done'",
            },
        }
        for table, cols in defaults.items():
            if table not in tables:
                continue
            have = _columns(conn, table)
            for col, default in cols.items():
                if col not in have:
                    continue
                res = conn.execute(text(
                    f"UPDATE {table} SET {col} = {default} WHERE {col} IS NULL"
                ))
                if res.rowcount:
                    report["migrated"].append(
                        f"{table}.{col}: {res.rowcount} Leerwert(e) gesetzt"
                    )

        if "approval_state" in have_host:
            # Bestandshosts aus 0.3.x waren freigegeben - nicht nachtraeglich sperren
            conn.execute(text(
                "UPDATE host SET approval_state = 'approved' "
                "WHERE approval_state IS NULL OR approval_state = ''"
            ))

        for table, cols in OBSOLETE.items():
            if table not in tables:
                continue
            have = _columns(conn, table)
            leftover = [c for c in cols if c in have]
            if leftover:
                report["notes"].append(
                    f"{table}: ungenutzte Altspalten bleiben erhalten "
                    f"({', '.join(leftover)})"
                )

    _set_version(engine, SCHEMA_VERSION)
    return report


def _hostname_index(engine: Engine, report: dict):
    """
    Eindeutiger Index auf den kleingeschriebenen Hostnamen (F-33 der
    Pruefung vom 2026-08-31).

    Die Pruefung in main.py allein reicht nicht: enroll und heartbeat sind
    synchrone Routen und laufen nebenlaeufig im Threadpool. Zwischen dem
    'gibt es den Namen schon?' und dem Schreiben liegt kein Schloss - zwei
    gleichzeitige Anfragen auf denselben Namen kommen beide durch. Das
    faengt nur die Datenbank ab.

    Ueber lower(), nicht ueber die Spalte selbst: SQLite vergleicht TEXT
    mit '=' unter Beachtung der Gross- und Kleinschreibung, ein Index auf
    'hostname' liesse 'DC01' neben 'dc01' also weiter zu.

    Ein bereits vorhandenes Paar dieser Art laesst den Index scheitern.
    Dann bricht die Migration NICHT ab: die Anlage laeuft weiter, die
    Pruefung in main.py greift, und im Bericht steht, was zu tun ist.
    Ein Update, das wegen zweier aehnlicher Hostnamen nicht einspielt,
    waere die schlechtere Antwort - es liesse die Anlage auf einem Stand
    mit offenen Befunden stehen.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_host_hostname_nocase "
                "ON host (lower(hostname))"
            ))
    except Exception as exc:  # noqa: BLE001
        report["notes"].append(
            "Hostnamen sind nicht eindeutig (Gross-/Kleinschreibung): "
            f"{exc}. Doppelte Eintraege im Dashboard entfernen, danach "
            "greift der eindeutige Index beim naechsten Start."
        )


def _set_version(engine: Engine, version: int):
    with engine.begin() as conn:
        if "setting" not in _tables(engine):
            return
        conn.execute(text(
            "INSERT INTO setting (key, value) VALUES ('schema_version', :v) "
            "ON CONFLICT(key) DO UPDATE SET value = :v"
        ), {"v": str(version)})


def get_version(engine: Engine) -> int:
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM setting WHERE key = 'schema_version'"
            )).fetchone()
            return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def verify(engine: Engine) -> tuple[bool, list[str]]:
    """
    Prueft, ob alle erwarteten Spalten vorhanden sind.
    Wird vom Health-Endpunkt genutzt, damit ein Schema-Bruch zum
    Rueckrollen durch den Update-Watcher fuehrt.
    """
    missing = []
    tables = _tables(engine)
    try:
        with engine.connect() as conn:
            for table, columns in EXPECTED_COLUMNS.items():
                if table not in tables:
                    missing.append(f"Tabelle {table} fehlt")
                    continue
                have = _columns(conn, table)
                missing.extend(
                    f"{table}.{name}" for name in columns if name not in have
                )
    except Exception as exc:  # noqa: BLE001
        return False, [f"Schema nicht pruefbar: {exc}"]
    return not missing, missing
