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
import re

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
# 20: vier Spalten an 'user' fuer die Anmeldung in zwei Schritten. Alle
#    ohne DEFAULT - NULL heisst "nicht eingerichtet". Ein Rueckschritt
#    auf 19 ist unkritisch: die aeltere Fassung kennt die Spalten nicht
#    und fragt nie nach einem Code. Wer die Anmeldung eingerichtet hat,
#    kommt danach also wieder mit dem Passwort allein hinein - das ist
#    kein Fehler, sondern die Kehrseite davon, dass ein Rueckschritt
#    ueberhaupt moeglich bleiben soll.
# 21: Tabellen an das Modell angeglichen (F-71). Kein neues Feld - die
#    NULLBARKEIT bestehender Spalten wird nachgezogen. SQLite kann das
#    nur ueber einen Tabellenneubau, und deshalb ist es bis hierher nie
#    passiert: eine gewachsene Anlage trug ein anderes Schema als eine
#    frisch installierte. Aufgefallen am 2026-09-07, als "Token
#    zurueckziehen" mit "NOT NULL constraint failed:
#    host.agent_token_hash" endete - waehrend dieselbe Route in jeder
#    Pruefreihe gruen war, weil die immer eine frische Datenbank anlegt.
#    Auf OPS01 betraf es 16 Spalten in fuenf Tabellen.
#    Ein Rueckschritt auf 20 ist unkritisch: die aeltere Fassung schreibt
#    in dieselben Spalten, sie sind danach nur strenger als sie es
#    erwartet - und Werte stehen ueberall.
# 22: die JSON-Spalten sind jetzt NOT NULL - sieben von acht. Kein
#    neues Feld und keine neue Idee, sondern dieselbe Abweichung wie 21,
#    eine Ebene hoeher: models.py fuehrt sie als list bzw. dict, nicht
#    als Optional - das Modell sagt also "immer eine Liste". Nur
#    Column(JSON) hatte kein nullable=False, und damit sagte die
#    Datenbank etwas anderes. Der Abgleich konnte das nicht sehen, weil
#    er gegen spalte.nullable vergleicht, also gegen die
#    Spaltendefinition und nicht gegen den Typ darueber.
#    job.result bleibt nullable - dort heisst NULL "noch kein Ergebnis",
#    und der Typ sagt es auch (Optional[dict]).
#    Vor dem Umbau werden vorhandene NULL-Werte auf '[]' bzw. '{}'
#    gesetzt; auf OPS01 waren es null Zeilen, bei einer aelteren
#    Anlage kann das anders sein.
#    Ein Rueckschritt auf 21 ist unkritisch: die aeltere Fassung schreibt
#    in dieselben Spalten immer Listen, sie sind danach nur strenger als
#    sie es erwartet.
SCHEMA_VERSION = 22

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
        # Anmeldung in zwei Schritten, ab Schema 20. Alle vier bewusst
        # ohne DEFAULT: NULL heisst "nicht eingerichtet", und genau das
        # trifft auf jedes bestehende Konto zu. Ein bestehender Benutzer
        # meldet sich nach dem Update also unveraendert an - er kann die
        # zweite Stufe einschalten, er muss nicht.
        "totp_secret": "VARCHAR",
        "totp_confirmed_at": "DATETIME",
        "totp_last_step": "INTEGER",
        "totp_recovery": "TEXT",
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
    report = {"added": [], "migrated": [], "notes": [], "problems": [],
              "version": SCHEMA_VERSION}
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

        # Leere JSON-Felder auf gueltige Werte setzen, sonst scheitert das
        # Lesen - und seit Schema 22 auch der Tabellenumbau: die Spalten
        # sind dort NOT NULL, und _tabelle_angleichen() weigert sich zu
        # Recht, eine Tabelle umzubauen, in der Pflichtspalten leer sind.
        #
        # DIE EINZIGE STELLE, an der das passiert. Beim Bauen von Schema
        # 22 stand hier zuerst eine zweite Liste weiter oben bei
        # 'defaults' - die Mutationsprobe hat sie entlarvt: das Entfernen
        # der neuen Zeile fuer 'tags' aenderte nichts, weil diese Schleife
        # es ohnehin tat. Ein Wert an zwei Stellen, von denen nur eine
        # wirkt - dieselbe Fehlerart wie F-12/F-14 und der feste
        # Dateiname in i18n.js.
        #
        # 'area' kam mit 0.36.1 dazu und fehlt in aelteren Datenbanken,
        # deshalb die Abfrage auf die Tabelle.
        for tabelle, spalten in (
                ("host", ("checkmk_hosts", "tags", "patch_days",
                          "reboot_reasons")),
                ("area", ("checkmk_hosts", "patch_days"))):
            if tabelle not in tables:
                continue
            vorhanden = _columns(conn, tabelle)
            for col in spalten:
                if col not in vorhanden:
                    continue
                conn.execute(text(
                    f"UPDATE {tabelle} SET {col} = '[]' "
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

    # Zuletzt: Tabellen an das Modell angleichen (F-71). Steht ganz am
    # Ende, weil davor Spalten ergaenzt und Werte gesetzt werden - erst
    # danach steht fest, was wirklich noch abweicht.
    angleichen(engine, report)

    _set_version(engine, SCHEMA_VERSION)
    return report



def _problem(report: dict, text: str):
    """
    Eine Notiz, die niemand uebersehen darf.

    Steht in notes wie bisher UND in problems. Der Unterschied: main.py
    gibt problems bei jedem Start aus, notes nur, wenn ausserdem etwas
    ergaenzt oder umgebaut wurde. Ein misslungener Umbau erzeugt aber
    genau nichts davon - er stand deshalb bis 0.37.24 nur im Bericht,
    den niemand abruft, und die Anlage schwieg dazu.
    """
    report["notes"].append(text)
    report["problems"].append(text)

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
        _problem(report,
                 "Hostnamen sind nicht eindeutig (Gross-/Kleinschreibung): "
                 f"{exc}. Doppelte Eintraege im Dashboard entfernen, danach "
                 "greift der eindeutige Index beim naechsten Start.")


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


# ======================================================================
# Schema-Abgleich gegen das Datenmodell (F-71)
# ======================================================================
# Anlass, 2026-09-07 im Betrieb gefunden: "Token zurueckziehen" endete mit
# 500, und im Protokoll stand
#
#     sqlite3.IntegrityError: NOT NULL constraint failed:
#     host.agent_token_hash
#
# models.py fuehrt die Spalte seit langem als Optional[str], also
# nullable. Die Datenbank auf OPS01 stammt aus einer Zeit, in der sie
# es nicht war - und SQLite kann die Nullbarkeit einer bestehenden Spalte
# nicht aendern. migrate() ergaenzt fehlende Spalten mit ALTER TABLE, baut
# aber keine um.
#
# Die Folge ist groesser als dieser eine Fall: eine frisch installierte
# Anlage und eine gewachsene haben nicht dasselbe Schema. Und keine
# Pruefreihe kann das sehen, weil jede von ihnen eine frische Datenbank
# anlegt.
#
# WARUM DIESE FUNKTION GEGEN models.py VERGLEICHT UND NICHT GEGEN
# EXPECTED_COLUMNS
#
# EXPECTED_COLUMNS ist eine von Hand gepflegte zweite Aufzaehlung. Genau
# so eine war F-14 (die Pins des Agents, die kein Bauschritt las) und
# F-59 (acht genannte Pakete, dreissig installierte). Eine zweite Liste
# ist genau dann falsch, wenn es darauf ankommt - naemlich wenn jemand
# das Modell aendert und die Liste vergisst. SQLModel.metadata IST das
# Modell; daran gemessen kann nichts vergessen werden.
#
# Die Funktion aendert NICHTS. Sie liest und berichtet.

# Typen werden nach Familie verglichen, nicht wortgleich. SQLite kennt
# ohnehin nur Affinitaeten, und VARCHAR gegen VARCHAR(255) oder JSON
# gegen TEXT waere ein Fehlalarm - beides landet in derselben Affinitaet.
# Gemeldet wird nur, was wirklich auseinanderliegt, etwa INTEGER gegen
# VARCHAR.
_TYP_FAMILIE = {
    "TEXT": "text", "VARCHAR": "text", "CHAR": "text", "NVARCHAR": "text",
    "JSON": "text", "CLOB": "text",
    "INTEGER": "zahl", "INT": "zahl", "BIGINT": "zahl", "SMALLINT": "zahl",
    "BOOLEAN": "zahl", "TINYINT": "zahl",
    "DATETIME": "zeit", "TIMESTAMP": "zeit", "DATE": "zeit",
    "FLOAT": "komma", "REAL": "komma", "NUMERIC": "komma", "DECIMAL": "komma",
    "BLOB": "blob",
}


def _familie(typ: str) -> str:
    kopf = (typ or "").split("(")[0].strip().upper()
    return _TYP_FAMILIE.get(kopf, kopf.lower() or "unbekannt")


def schema_abweichungen(engine: Engine) -> list[str]:
    """
    Vergleicht das tatsaechliche Schema mit dem Datenmodell.

    Gibt eine Liste lesbarer Zeilen zurueck; leer heisst "deckt sich".
    Geprueft wird je Tabelle und Spalte: Vorhandensein, Nullbarkeit und
    Typfamilie, dazu die eindeutigen Indizes.

    Aendert nichts.
    """
    try:
        from models import SQLModel        # noqa: PLC0415
    except ImportError as exc:             # pragma: no cover
        return [f"Datenmodell nicht ladbar, kein Abgleich moeglich: {exc}"]

    abweichungen: list[str] = []
    vorhandene = _tables(engine)

    try:
        with engine.connect() as conn:
            for tabelle in SQLModel.metadata.sorted_tables:
                name = tabelle.name
                if name not in vorhandene:
                    abweichungen.append(f"Tabelle {name} fehlt ganz")
                    continue

                rows = conn.execute(
                    text(f"PRAGMA table_info({name})")).fetchall()
                # PRAGMA liefert (cid, name, type, notnull, dflt_value, pk)
                ist = {r[1]: {"typ": r[2], "notnull": bool(r[3]),
                              "pk": bool(r[5])} for r in rows}

                for spalte in tabelle.columns:
                    da = ist.get(spalte.name)
                    if da is None:
                        abweichungen.append(
                            f"{name}.{spalte.name} fehlt "
                            f"(im Modell vorhanden)")
                        continue

                    # Der Primaerschluessel ist in SQLite immer NOT NULL,
                    # auch wenn das Modell ihn als optional fuehrt. Das
                    # ist kein Unterschied, sondern SQLite.
                    if not da["pk"]:
                        soll_null = bool(spalte.nullable)
                        ist_null = not da["notnull"]
                        if soll_null != ist_null:
                            abweichungen.append(
                                f"{name}.{spalte.name}: Datenbank sagt "
                                f"{'NOT NULL' if da['notnull'] else 'NULL erlaubt'}, "
                                f"Modell sagt "
                                f"{'NULL erlaubt' if soll_null else 'NOT NULL'}"
                            )

                    soll_typ = _familie(
                        spalte.type.compile(engine.dialect))
                    ist_typ = _familie(da["typ"])
                    if soll_typ != ist_typ:
                        abweichungen.append(
                            f"{name}.{spalte.name}: Datenbank hat "
                            f"{da['typ'] or '(ohne Typ)'}, Modell erwartet "
                            f"{spalte.type.compile(engine.dialect)}")

                # Eindeutige Indizes. Der Index ux_host_hostname_nocase
                # steht ueber lower(hostname) und gehoert keiner Spalte -
                # er wird deshalb nur gezaehlt, nicht zugeordnet.
                idx = conn.execute(
                    text(f"PRAGMA index_list({name})")).fetchall()
                eindeutig = set()
                for zeile in idx:
                    if not zeile[2]:                 # unique-Flag
                        continue
                    for teil in conn.execute(
                            text(f"PRAGMA index_info({zeile[1]})")).fetchall():
                        if teil[2]:
                            eindeutig.add(teil[2])
                for spalte in tabelle.columns:
                    if spalte.unique and not spalte.primary_key \
                            and spalte.name not in eindeutig:
                        abweichungen.append(
                            f"{name}.{spalte.name}: im Modell eindeutig, "
                            f"in der Datenbank ohne eindeutigen Index")
    except Exception as exc:  # noqa: BLE001
        return [f"Schema nicht vergleichbar: {exc}"]

    return abweichungen


if __name__ == "__main__":
    # Lesender Aufruf von Hand, fuer eine bestehende Anlage:
    #
    #     python3 backend/migrate.py --pruefen /opt/co37/data/co37.db
    #
    # Oeffnet die Datei ausdruecklich NUR LESEND (mode=ro). Wer eine
    # Produktivdatenbank untersucht, soll das ohne die Frage tun muessen,
    # ob das Werkzeug etwas veraendert.
    import sys
    from pathlib import Path
    from sqlalchemy import create_engine

    if len(sys.argv) != 3 or sys.argv[1] != "--pruefen":
        print(__doc__.strip())
        print()
        print("Schema gegen das Datenmodell pruefen (nur lesend):")
        print("    python3 backend/migrate.py --pruefen /pfad/zur/co37.db")
        raise SystemExit(2)

    pfad = Path(sys.argv[2]).resolve()
    if not pfad.is_file():
        raise SystemExit(f"Keine Datei: {pfad}")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    motor = create_engine(f"sqlite:///file:{pfad}?mode=ro&uri=true")
    zeilen = schema_abweichungen(motor)
    print(f"Schema-Version laut Datenbank: {get_version(motor)} "
          f"(Code erwartet {SCHEMA_VERSION})")
    if not zeilen:
        print("Keine Abweichung zum Datenmodell gefunden.")
        raise SystemExit(0)
    print(f"{len(zeilen)} Abweichung(en) zum Datenmodell:")
    for z in zeilen:
        print(f"  {z}")
    raise SystemExit(1)


# ======================================================================
# Tabellen an das Modell angleichen (F-71, Schema 21)
# ======================================================================
# SQLite kann die Nullbarkeit einer bestehenden Spalte nicht aendern. Der
# einzige Weg ist der, den die SQLite-Anleitung unter "Making Other Kinds
# Of Table Schema Changes" beschreibt: neue Tabelle anlegen, Daten
# kopieren, alte loeschen, umbenennen, Indizes neu anlegen.
#
# WELCHE TABELLEN UMGEBAUT WERDEN, ENTSCHEIDET NICHT EINE LISTE
#
# Sondern schema_abweichungen(). Eine hier hineingeschriebene Aufzaehlung
# waere zum dritten Mal derselbe Fehler (F-14, F-59): sie stimmt, bis
# jemand das Modell aendert und sie vergisst. So wird umgebaut, was
# tatsaechlich abweicht - und nach dem Umbau wird nachgesehen, ob es
# gewirkt hat.
#
# WAS VORHER GEPRUEFT WIRD
#
# Eine Spalte, die im Modell NOT NULL ist und in der Datenbank NULL-Werte
# enthaelt, laesst sich nicht umbauen - das INSERT in die neue Tabelle
# scheitert. Das wird VORHER festgestellt und die Tabelle uebersprungen,
# mit einer Zeile im Bericht. Ein Update, das an so etwas abbricht, waere
# die schlechtere Antwort: die Anlage stuende auf einem alten Stand.
#
# Auf OPS01 am 2026-09-08 nachgemessen: null NULL-Werte in allen elf
# betroffenen Spalten. Der Umbau ist dort reine Vorsorge.


def _nullbarkeit_weicht_ab(zeile: str) -> bool:
    return "Datenbank sagt" in zeile and "Modell sagt" in zeile


def _tabellen_mit_drift(engine: Engine) -> list[str]:
    """Tabellennamen, deren Nullbarkeit vom Modell abweicht."""
    namen = []
    for zeile in schema_abweichungen(engine):
        if not _nullbarkeit_weicht_ab(zeile):
            continue
        tabelle = zeile.split(".", 1)[0].strip()
        if tabelle and tabelle not in namen:
            namen.append(tabelle)
    return namen


def _zeilen(cur, tabelle: str) -> int:
    """
    Zeilen zaehlen, auf einem DBAPI-Cursor.

    Eigene Funktion, damit die Pruefreihe sich hier einhaengen und eine
    Abweichung erzwingen kann - die Sicherung "gleich viele Zeilen wie
    vorher" laesst sich sonst nicht ausloesen, ohne die Datenbank
    absichtlich zu beschaedigen.
    """
    cur.execute(f"SELECT COUNT(*) FROM {tabelle}")
    return cur.fetchone()[0]


def _indizes_von(conn, tabelle: str) -> list[tuple[str, str]]:
    """
    Die CREATE-INDEX-Anweisungen dieser Tabelle, wie sie dastehen, je mit
    ihrem Namen.

    Aus sqlite_master, nicht aus dem Modell: hier steht auch der Index
    ueber lower(hostname) aus F-33, den das Modell gar nicht kennt. Ginge
    er beim Umbau verloren, waeren zwei Hosts mit unterschiedlicher
    Gross-/Kleinschreibung wieder moeglich - und niemandem fiele es auf.
    """
    rows = conn.execute(text(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name=:t AND sql IS NOT NULL"), {"t": tabelle}).fetchall()
    return [(r[0], r[1]) for r in rows]


def _index_braucht(sql: str, spalten: set[str]) -> list[str]:
    """
    Welche der genannten Spalten dieser Index braucht.

    Ueber den Anweisungstext und nicht ueber PRAGMA index_info: das
    Pragma nennt die Spalten eines gewoehnlichen Index, laesst aber bei
    einem Ausdruck wie lower(hostname) NULL stehen - und gerade solche
    Indizes gibt es hier (F-33). Ein zweiter Weg, der nur die Haelfte der
    Faelle abdeckt, waere schwerer zu pruefen als er wert ist.

    Als ganzes Wort, sonst faenge 'customer' auch einen Index auf
    'customer_id'. Ohne Beachtung der Gross- und Kleinschreibung, weil
    SQLite Spaltennamen so vergleicht: wer den Index einmal auf
    CHECKMK_HOST angelegt hat, hat denselben Index.
    """
    return sorted(s for s in spalten
                  if re.search(rf"\b{re.escape(s)}\b", sql or "",
                               re.IGNORECASE))


def _tabelle_angleichen(engine: Engine, tabelle: str, report: dict) -> bool:
    """
    Baut eine Tabelle so neu, wie das Modell sie beschreibt.

    Gibt True zurueck, wenn umgebaut wurde. Die Daten der gemeinsamen
    Spalten werden uebernommen; Spalten, die es nur in der alten Tabelle
    gibt (OBSOLETE), fallen dabei weg - das ist der einzige Ort, an dem
    sie ueberhaupt verschwinden koennen.
    """
    from models import SQLModel                      # noqa: PLC0415
    from sqlalchemy.schema import CreateIndex, CreateTable   # noqa: PLC0415

    ziel = SQLModel.metadata.tables.get(tabelle)
    if ziel is None:
        return False

    with engine.connect() as conn:
        alt = {r[1] for r in conn.execute(
            text(f"PRAGMA table_info({tabelle})")).fetchall()}
        gemeinsam = [c.name for c in ziel.columns if c.name in alt]

        # Wuerde der Umbau an einem NULL scheitern? Lieber vorher wissen.
        leer = []
        for spalte in ziel.columns:
            if spalte.nullable or spalte.name not in alt:
                continue
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {tabelle} "
                f"WHERE {spalte.name} IS NULL")).scalar()
            if n:
                leer.append(f"{tabelle}.{spalte.name} ({n} Zeilen)")
        if leer:
            _problem(report,
                     f"Tabelle {tabelle} nicht angeglichen: dort stehen "
                     f"NULL-Werte in Spalten, die das Modell als Pflicht "
                     f"fuehrt - {', '.join(leer)}. Die Anlage laeuft "
                     f"unveraendert weiter; die Werte muessen erst gesetzt "
                     f"werden.")
            return False

        # Und die zweite Bedingung, an der ein Umbau scheitern kann:
        # doppelte Werte dort, wo das Modell einen eindeutigen Index
        # verlangt. Die alte Tabelle hat ihn nicht, also durfte es sie
        # geben; beim Anlegen des Index scheitert es.
        #
        # Ohne diese Vorpruefung stuende im Bericht eine rohe
        # IntegrityError. Damit weiss niemand, WELCHE Zeilen es sind und
        # wie viele - und genau das ist die Frage, die als Naechstes
        # gestellt wird.
        #
        # NULL zaehlt nicht als Dopplung: SQLite haelt NULLs in einem
        # eindeutigen Index auseinander, mehrere Zeilen ohne Wert sind
        # also erlaubt. Der WERT selbst steht bewusst nicht im Bericht -
        # in einer solchen Spalte kann ein Geheimnis stehen.
        doppelt = []
        for index in ziel.indexes:
            if not index.unique:
                continue
            spalten = [c.name for c in index.columns]
            if any(s not in alt for s in spalten):
                continue
            liste = ", ".join(spalten)
            bedingung = " AND ".join(f"{s} IS NOT NULL" for s in spalten)
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM (SELECT {liste} FROM {tabelle} "
                f"WHERE {bedingung} GROUP BY {liste} "
                f"HAVING COUNT(*) > 1)")).scalar()
            if n:
                doppelt.append(f"{tabelle}.{liste} ({n} Wert(e) mehrfach)")
        if doppelt:
            _problem(report,
                     f"Tabelle {tabelle} nicht angeglichen: das Modell "
                     f"verlangt Eindeutigkeit, wo die Datenbank Dopplungen "
                     f"enthaelt - {', '.join(doppelt)}. Die Anlage laeuft "
                     f"unveraendert weiter; die doppelten Zeilen muessen "
                     f"erst bereinigt werden.")
            return False

        # Indizes, die auf entfallene Spalten zeigen, koennen nicht
        # mitgenommen werden - die Spalte gibt es in der neuen Tabelle
        # nicht mehr.
        #
        # Genau daran ist der Umbau auf OPS01 am 2026-09-08
        # gescheitert: dort steht ein Index auf checkmk_host, einer der
        # Altspalten aus OBSOLETE. Die Meldung war "no such column:
        # checkmk_host" - richtig, aber ohne den Hinweis, dass es um
        # einen INDEX geht und dass er entfaellt.
        entfallen = alt - {c.name for c in ziel.columns}
        indizes = []
        verworfen = []
        for name, sql in _indizes_von(conn, tabelle):
            betroffen = _index_braucht(sql, entfallen)
            if betroffen:
                verworfen.append(f"{name} (auf {', '.join(betroffen)})")
                continue
            indizes.append(sql)
        if verworfen:
            _problem(report,
                     f"Tabelle {tabelle}: Index/Indizes entfallen mit ihren "
                     f"Spalten - {', '.join(verworfen)}. Diese Spalten fuehrt "
                     f"das Modell nicht mehr; der Index kann deshalb nicht "
                     f"uebernommen werden.")

    ddl = str(CreateTable(ziel).compile(engine)).strip()
    ddl_neu = ddl.replace(f"CREATE TABLE {tabelle}",
                          f"CREATE TABLE {tabelle}_neu", 1)
    spaltenliste = ", ".join(gemeinsam)

    # KEIN Herumschalten an PRAGMA foreign_keys - und das ist gemessen,
    # nicht angenommen.
    #
    # Die SQLite-Anleitung empfiehlt fuer diesen Umbau, die Durchsetzung
    # von Fremdschluesseln vorher abzuschalten. Sie ist in SQLite aber ab
    # Werk AUS, je Verbindung, und CO-37 schaltet sie nirgends ein
    # (nachgesehen: kein einziges "PRAGMA foreign_keys" im Backend, kein
    # entsprechender Ereignishorcher). Es gibt hier also nichts
    # abzuschalten.
    #
    # Der erste Anlauf tat es trotzdem - und hatte damit zwei Fehler auf
    # einmal: SQLAlchemy beginnt die Transaktion bereits bei der ersten
    # Anweisung, das PRAGMA waere also wirkungslos in einer Transaktion
    # gelandet; und das PRAGMA ... = ON am Ende haette die Durchsetzung
    # auf einer Verbindung EINgeschaltet, die aus dem Pool kommt und
    # danach weiterverwendet wird. Eine Migration, die nebenbei das
    # Verhalten der Datenbank aendert, ist keine.
    #
    # Sollte die Durchsetzung eines Tages eingeschaltet werden, gehoert
    # dieser Umbau erneut angesehen. tests/schema-drift-test.py haelt
    # genau diese Annahme fest, damit die Aenderung nicht unbemerkt
    # bleibt.
    # Warum hier von Hand BEGIN geschrieben wird und nicht engine.begin()
    # genommen wird - gemessen, nicht angenommen:
    #
    # Der SQLite-Treiber von Python faengt eine Transaktion von sich aus
    # erst bei der ersten SCHREIBENDEN Anweisung an, also bei INSERT,
    # UPDATE oder DELETE. CREATE TABLE und DROP TABLE zaehlen nicht dazu.
    # In engine.begin() liefe das CREATE der Zwischentabelle deshalb
    # ausserhalb jeder Transaktion und waere sofort dauerhaft. Bricht der
    # Umbau danach ab, sind die Daten zwar heil - das INSERT hatte die
    # Transaktion inzwischen aufgemacht, der Ruecklauf holt DROP und
    # RENAME zurueck -, aber {tabelle}_neu bleibt als Geistertabelle
    # liegen. "Ein misslungener Umbau kostet nichts" waere dann nur fast
    # wahr.
    #
    # Ein ausdrueckliches BEGIN vor der ersten Anweisung zieht auch die
    # DDL mit hinein. Behauptet wird das hier nicht: tests/
    # schema-drift-test.py laesst einen Umbau scheitern und sieht nach,
    # ob eine Zwischentabelle herumliegt - auf jeder Anlage, auf der die
    # Pruefreihe laeuft.
    #
    # An der Verbindung selbst wird nichts umgestellt. Sie kommt aus dem
    # Pool und wird danach weiterverwendet; eine Migration, die nebenbei
    # das Verhalten der Datenbank aendert, ist keine.
    roh = engine.raw_connection()
    try:
        cur = roh.cursor()
        try:
            cur.execute("BEGIN")
            vorher = _zeilen(cur, tabelle)
            cur.execute(f"DROP TABLE IF EXISTS {tabelle}_neu")
            cur.execute(ddl_neu)
            cur.execute(f"INSERT INTO {tabelle}_neu ({spaltenliste}) "
                        f"SELECT {spaltenliste} FROM {tabelle}")
            cur.execute(f"DROP TABLE {tabelle}")
            cur.execute(f"ALTER TABLE {tabelle}_neu RENAME TO {tabelle}")
            for anweisung in indizes:
                cur.execute(anweisung)
            # Und die Indizes, die das MODELL kennt, aber in der alten
            # Tabelle fehlten. Sonst hiesse "an das Modell angeglichen"
            # nur "die Spalten stimmen jetzt" - und der Abgleich meldete
            # danach weiter etwas, womit er als Warnung wertlos waere.
            #
            # Erst nachsehen, was die Zeile darueber schon angelegt hat:
            # ein zweites CREATE INDEX auf denselben Namen bricht ab, und
            # das traefe jede Anlage, deren alte Tabelle den Index des
            # Modells bereits hatte - also den Normalfall.
            cur.execute("SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name=?", (tabelle,))
            vorhandene = {r[0] for r in cur.fetchall()}
            for index in ziel.indexes:
                if index.name in vorhandene:
                    continue
                cur.execute(str(CreateIndex(index).compile(engine)))
            nachher = _zeilen(cur, tabelle)
            if nachher != vorher:
                # Loest den Ruecklauf der Transaktion aus - die alte
                # Tabelle steht danach unveraendert da.
                raise RuntimeError(
                    f"{vorher} Zeilen vorher, {nachher} nachher")
            cur.execute("COMMIT")
        except BaseException:
            # Ausdruecklich, nicht dem Pool ueberlassen: dass eine
            # zurueckgegebene Verbindung zurueckgerollt wird, ist eine
            # Einstellung von SQLAlchemy, keine Zusage dieser Funktion.
            cur.execute("ROLLBACK")
            raise
        finally:
            cur.close()
    finally:
        roh.close()

    report["migrated"].append(
        f"Tabelle {tabelle} an das Modell angeglichen "
        f"({vorher} Zeilen, {len(indizes)} Index(e) erhalten)")
    return True


def angleichen(engine: Engine, report: dict):
    """
    Bringt abweichende Tabellen auf die Form des Modells (F-71).

    Laeuft bei jedem Start. Gibt es nichts zu tun - der Normalfall,
    einschliesslich jeder frisch installierten Anlage - kostet das eine
    Abfrage je Tabelle und sonst nichts.
    """
    try:
        offen = _tabellen_mit_drift(engine)
    except Exception as exc:  # noqa: BLE001
        _problem(report, f"Schema nicht vergleichbar: {exc}")
        return
    if not offen:
        return

    for tabelle in offen:
        try:
            _tabelle_angleichen(engine, tabelle, report)
        except Exception as exc:  # noqa: BLE001
            # Ein misslungener Umbau darf die Anlage nicht anhalten. Die
            # Transaktion ist zurueckgerollt, die alte Tabelle steht
            # unveraendert da - es laeuft weiter wie bisher, und im
            # Bericht steht, was nicht ging.
            _problem(report,
                     f"Tabelle {tabelle} liess sich nicht angleichen: {exc}. "
                     f"Die Tabelle ist unveraendert.")

    # Und nachsehen, ob es gewirkt hat. Ein Umbau, der sich selbst nicht
    # nachprueft, ist eine Behauptung.
    rest = [z for z in schema_abweichungen(engine) if _nullbarkeit_weicht_ab(z)]
    if rest:
        _problem(report,
                 f"Nach dem Angleichen bleiben {len(rest)} Abweichung(en): "
                 + "; ".join(rest[:5]))
