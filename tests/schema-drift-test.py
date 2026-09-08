"""
CO-37 - eine gewachsene Anlage hat ein anderes Schema als eine frische
(F-71).

DER ANLASS

Am 2026-09-07 endete "Token zurueckziehen" auf KK-OPS01 mit 500:

    sqlite3.IntegrityError: NOT NULL constraint failed:
    host.agent_token_hash

models.py fuehrt die Spalte seit langem als Optional[str], also nullable.
Die Datenbank dort stammt aus einer Zeit, in der sie es nicht war - und
SQLite kann die Nullbarkeit einer bestehenden Spalte nicht aendern.
migrate() ergaenzt fehlende Spalten mit ALTER TABLE, baut aber keine um.

WARUM DAS KEINE DER BISHERIGEN REIHEN FINDEN KONNTE

Jede von ihnen legt eine FRISCHE Datenbank an. create_all() erzeugt die
Tabellen aus dem Modell - dort ist die Spalte selbstverstaendlich
nullable, und die Route laeuft. tests/roles-test.py prueft genau diese
Route und ist gruen, waehrend sie im Betrieb 500 liefert.

Der Fehler sitzt nicht im Code, sondern im UNTERSCHIED zwischen zwei
Ausgangslagen. Eine Reihe, die nur eine davon herstellt, kann ihn nicht
sehen - egal wie sorgfaeltig sie ist.

WAS DIESE REIHE TUT

Sie stellt eine ALTE Datenbank her, laesst migrate() darueberlaufen und
vergleicht das Ergebnis mit dem Datenmodell. Und sie prueft die
Vergleichsfunktion selbst gegen nachgebaute Abweichungen - eine Pruefung,
die nichts findet, weil sie nichts finden KANN, waere hier besonders
teuer.

Braucht kein Backend und kein Netz.

    python3 tests/schema-drift-test.py
"""
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import migrate  # noqa: E402
import models  # noqa: F401,E402  - registriert die Tabellen in der Metadata

TMP = Path(tempfile.mkdtemp())
fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# Ein Host mit allen Pflichtfeldern. Als Funktion, nicht als
# Zeichenkette: die Spaltenliste steht damit einmal da, und wenn das
# Modell eine Pflichtspalte dazubekommt, faellt genau eine Stelle um -
# nicht fuenf, von denen man vier uebersieht.
def host_einfuegen(conn, hostname, token="h", sortierung=0):
    conn.execute(text(
        "INSERT INTO host (hostname, agent_token_hash, approval_state, "
        "status, last_seen_secure, updates_available, security_updates, "
        "reboot_required, updates_require_reboot, downtime_minutes, "
        "checkmk_downtime_all, auto_reboot, patch_enabled, "
        "patch_auto_reboot, patch_grace_hours, patch_followup_left, "
        "sort_order, created_at) "
        "VALUES (:h, :t, 'approved', 'ok', 0, 0, 0, 0, 0, 30, 0, 0, 0, 0, "
        "4, 0, :s, '2026-01-01 00:00:00')"),
        {"h": hostname, "t": token, "s": sortierung})


def frische_db(name: str):
    pfad = TMP / name
    motor = create_engine(f"sqlite:///{pfad}")
    SQLModel.metadata.create_all(motor)
    return motor


# ======================================================================
# Eine frische Datenbank deckt sich mit dem Modell
# ======================================================================
print("--- Eine frische Datenbank ---")
motor = frische_db("frisch.db")
abw = migrate.schema_abweichungen(motor)
check("keine Abweichung zwischen create_all() und dem Modell",
      not abw, abw[:5])

# Und die Gegenprobe zur Aussagekraft: die Funktion muss ueberhaupt etwas
# angesehen haben. Eine Funktion, die immer eine leere Liste liefert,
# bestuende die Zeile darueber ebenfalls.
check("dabei wurden ueberhaupt Tabellen betrachtet",
      len(SQLModel.metadata.sorted_tables) >= 5,
      [t.name for t in SQLModel.metadata.sorted_tables])


# ======================================================================
# Der Fall von KK-OPS01, nachgebaut
# ======================================================================
print()
print("--- host.agent_token_hash als NOT NULL, wie auf einer alten Anlage ---")
# Nachgebaut wird die Tabelle so, wie eine alte Fassung sie angelegt hat:
# dieselben Spalten, aber agent_token_hash ohne Nullbarkeit. Genau das
# steht auf KK-OPS01.
alt = TMP / "alt.db"
motor_alt = create_engine(f"sqlite:///{alt}")
SQLModel.metadata.create_all(motor_alt)

with motor_alt.begin() as conn:
    spalten = conn.execute(text("PRAGMA table_info(host)")).fetchall()
    namen = [r[1] for r in spalten]
    # Tabelle mit derselben Form neu bauen, nur agent_token_hash strenger.
    teile = []
    for r in spalten:
        stueck = f"{r[1]} {r[2] or 'VARCHAR'}"
        if r[5]:
            stueck += " PRIMARY KEY"
        elif r[1] == "agent_token_hash" or r[3]:
            stueck += " NOT NULL"
        teile.append(stueck)
    conn.execute(text("ALTER TABLE host RENAME TO host_alt"))
    conn.execute(text(f"CREATE TABLE host ({', '.join(teile)})"))
    conn.execute(text(
        f"INSERT INTO host ({', '.join(namen)}) "
        f"SELECT {', '.join(namen)} FROM host_alt"))
    conn.execute(text("DROP TABLE host_alt"))

check("die nachgebaute Tabelle ist wirklich strenger",
      any(r[1] == "agent_token_hash" and r[3]
          for r in motor_alt.connect().execute(
              text("PRAGMA table_info(host)")).fetchall()))

# HIER wird NICHT migriert.
#
# Der erste Anlauf tat es - und pruefte damit den Zustand NACH der
# Behebung, waehrend die Beschriftung "so sieht eine alte Anlage aus"
# lautete. Seit migrate() den Umbau mitbringt, war die Reihe damit grün
# geworden, ohne den beschriebenen Fall je hergestellt zu haben. Der
# Umbau wird weiter unten geprueft, in einem eigenen Abschnitt.
abw = migrate.schema_abweichungen(motor_alt)
treffer = [z for z in abw if "agent_token_hash" in z and "NULL" in z]
check("der Abgleich meldet host.agent_token_hash", bool(treffer), abw[:5])
check("und sagt, in welche Richtung es auseinanderliegt",
      bool(treffer) and "Datenbank sagt NOT NULL" in treffer[0], treffer[:1])

# Die eigentliche Wirkung: auf dieser Datenbank scheitert das
# Zuruecknehmen des Tokens, auf der frischen nicht. Ohne diese Zeile
# waere der Abgleich eine Behauptung ueber Metadaten.
with motor_alt.begin() as conn:
    host_einfuegen(conn, "TEST-DRIFT", "abc")
try:
    with motor_alt.begin() as conn:
        conn.execute(text(
            "UPDATE host SET agent_token_hash = NULL "
            "WHERE hostname = 'TEST-DRIFT'"))
    ging = True
except Exception:  # noqa: BLE001
    ging = False
check("auf der alten Datenbank scheitert das Zuruecknehmen wirklich",
      not ging)

# Und dieselbe Folge Anweisungen auf einer frischen Datenbank. Das ist
# die Haelfte, die erklaert, warum es niemand gesehen hat - und sie muss
# gemessen werden, nicht behauptet: ein check(..., True) waere gruen,
# auch wenn hier etwas ganz anderes passierte.
motor_frisch = frische_db("frisch2.db")
try:
    with motor_frisch.begin() as conn:
        host_einfuegen(conn, "TEST-DRIFT", "abc")
        conn.execute(text(
            "UPDATE host SET agent_token_hash = NULL "
            "WHERE hostname = 'TEST-DRIFT'"))
    frisch_ging = True
    frisch_grund = ""
except Exception as exc:  # noqa: BLE001
    frisch_ging = False
    frisch_grund = str(exc)[:120]
check("auf einer frischen geht dieselbe Anweisung anstandslos durch - "
      "genau darum sah es niemand", frisch_ging, frisch_grund)


# ======================================================================
# Findet der Abgleich auch die anderen Arten von Abweichung?
# ======================================================================
print()
print("--- Weitere Arten von Abweichung ---")

# 1. Fehlende Spalte
m1 = frische_db("fehlt.db")
with m1.begin() as conn:
    conn.execute(text("ALTER TABLE host DROP COLUMN maintenance_window"))
abw = migrate.schema_abweichungen(m1)
check("eine fehlende Spalte wird gemeldet",
      any("maintenance_window" in z and "fehlt" in z for z in abw), abw[:3])

# 2. Fehlende Tabelle
m2 = frische_db("tabelle.db")
with m2.begin() as conn:
    conn.execute(text("DROP TABLE auditentry"))
abw = migrate.schema_abweichungen(m2)
# Auf den WORTLAUT geprueft, nicht nur auf den Tabellennamen.
#
# Die erste Fassung suchte "auditentry" irgendwo in der Ausgabe - und
# blieb damit gruen, als die Mutationsprobe die Tabellenpruefung entfernte:
# ohne sie faellt der Ablauf auf die Spaltenschleife durch, PRAGMA liefert
# fuer eine fehlende Tabelle nichts, und jede einzelne Spalte wird als
# fehlend gemeldet. Der Name stand also weiterhin da, nur aus dem falschen
# Grund. Genau die Sorte Pruefung, die den eigenen Verdacht bestaetigt.
check("eine fehlende Tabelle wird als solche gemeldet",
      any(z == "Tabelle auditentry fehlt ganz" for z in abw), abw[:3])
check("und nicht als Handvoll fehlender Spalten",
      len([z for z in abw if "auditentry" in z]) == 1,
      [z for z in abw if "auditentry" in z][:3])

# 3. Fehlender eindeutiger Index
m3 = frische_db("index.db")
with m3.begin() as conn:
    for zeile in conn.execute(text("PRAGMA index_list(host)")).fetchall():
        if zeile[2] and not zeile[1].startswith("sqlite_"):
            conn.execute(text(f"DROP INDEX {zeile[1]}"))
abw = migrate.schema_abweichungen(m3)
check("ein fehlender eindeutiger Index wird gemeldet",
      any("eindeutig" in z for z in abw), abw[:3])

# 4. Falscher Typ. Verglichen wird nach Familie, nicht wortgleich -
#    VARCHAR gegen VARCHAR(255) waere ein Fehlalarm, VARCHAR gegen
#    INTEGER nicht.
m5 = frische_db("typ.db")
with m5.begin() as conn:
    conn.execute(text("ALTER TABLE setting RENAME TO setting_alt"))
    conn.execute(text(
        "CREATE TABLE setting (key VARCHAR NOT NULL PRIMARY KEY, "
        "value INTEGER NOT NULL)"))
    conn.execute(text(
        "INSERT INTO setting (key, value) SELECT key, value FROM setting_alt"))
    conn.execute(text("DROP TABLE setting_alt"))
abw = migrate.schema_abweichungen(m5)
check("ein Typ aus einer anderen Familie wird gemeldet",
      any("setting.value" in z for z in abw), abw[:3])

#    Und die Gegenprobe dazu: dieselbe Familie darf NICHT auffallen,
#    sonst meldet der Abgleich auf jeder Anlage etwas und wird ignoriert.
m6 = frische_db("typ-ok.db")
with m6.begin() as conn:
    conn.execute(text("ALTER TABLE setting RENAME TO setting_alt"))
    conn.execute(text(
        "CREATE TABLE setting (key VARCHAR NOT NULL PRIMARY KEY, "
        "value TEXT NOT NULL)"))
    conn.execute(text(
        "INSERT INTO setting (key, value) SELECT key, value FROM setting_alt"))
    conn.execute(text("DROP TABLE setting_alt"))
check("VARCHAR gegen TEXT dagegen nicht - das ist kein Unterschied",
      not [z for z in migrate.schema_abweichungen(m6) if "setting" in z],
      [z for z in migrate.schema_abweichungen(m6) if "setting" in z])

# 5. Und die Gegenprobe: eine unveraenderte Datenbank bleibt still.
check("eine unveraenderte Datenbank meldet weiterhin nichts",
      not migrate.schema_abweichungen(frische_db("still.db")))


# ======================================================================
# Der Umbau: eine alte Datenbank MIT DATEN wird angeglichen
# ======================================================================
print()
print("--- Angleichen an das Modell (Schema 21) ---")
# Das ist die Pruefung, an der alles haengt. Eine Migration, die eine
# Tabelle neu baut, kann Daten verlieren, Indizes vergessen oder
# Fremdschluessel zerreissen - und all das faellt erst beim Kunden auf.
# Deshalb wird hier mit Inhalt gearbeitet, nicht mit leeren Tabellen.
umbau = TMP / "umbau.db"
motor_u = create_engine(f"sqlite:///{umbau}")
SQLModel.metadata.create_all(motor_u)


def _alt_machen(motor, tabelle, streng=(), locker=()):
    """Baut eine Tabelle so um, wie eine aeltere Fassung sie angelegt hat."""
    with motor.begin() as conn:
        spalten = conn.execute(text(f"PRAGMA table_info({tabelle})")).fetchall()
        namen = [r[1] for r in spalten]
        teile = []
        for r in spalten:
            stueck = f"{r[1]} {r[2] or 'VARCHAR'}"
            if r[5]:
                stueck += " PRIMARY KEY"
            elif r[1] in streng or (r[3] and r[1] not in locker):
                stueck += " NOT NULL"
            teile.append(stueck)
        conn.execute(text(f"ALTER TABLE {tabelle} RENAME TO {tabelle}_x"))
        conn.execute(text(f"CREATE TABLE {tabelle} ({', '.join(teile)})"))
        conn.execute(text(
            f"INSERT INTO {tabelle} ({', '.join(namen)}) "
            f"SELECT {', '.join(namen)} FROM {tabelle}_x"))
        conn.execute(text(f"DROP TABLE {tabelle}_x"))


# Genau die Form von KK-OPS01: agent_token_hash strenger, eine Reihe
# spaeter ergaenzter Spalten lockerer.
LOCKER = ("last_seen_secure", "updates_require_reboot", "downtime_minutes",
          "checkmk_downtime_all", "patch_enabled", "patch_auto_reboot",
          "patch_grace_hours", "patch_followup_left", "sort_order")
_alt_machen(motor_u, "host", streng=("agent_token_hash", "created_at"),
            locker=LOCKER)

# Daten hinein - drei Hosts, damit ein Verlust auffiele.
with motor_u.begin() as conn:
    for i, name in enumerate(("KK-EINS", "KK-ZWEI", "KK-DREI")):
        host_einfuegen(conn, name, f"hash{i}", i)
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_host_hostname_nocase "
        "ON host (lower(hostname))"))
    # Und der Index, den auch das Modell kennt. Das ist der Normalfall -
    # eine gewachsene Anlage hat ihn laengst. Der Umbau legt die
    # vorgefundenen Indizes wieder an UND danach die des Modells; faende
    # er dabei nicht heraus, dass dieser hier schon steht, braeche das
    # zweite CREATE INDEX ab und der ganze Umbau ginge nicht.
    conn.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_host_agent_token_hash "
        "ON host (agent_token_hash)"))

vorher_zeilen = motor_u.connect().execute(
    text("SELECT COUNT(*) FROM host")).scalar()
check("die alte Datenbank hat Inhalt", vorher_zeilen == 3, vorher_zeilen)
check("und weicht vor dem Umbau ab",
      len(migrate.schema_abweichungen(motor_u)) >= 10,
      len(migrate.schema_abweichungen(motor_u)))

bericht = migrate.migrate(motor_u)

abw = migrate.schema_abweichungen(motor_u)
check("nach der Migration meldet der Abgleich NICHTS mehr", not abw, abw[:6])
check("der Bericht sagt, dass umgebaut wurde",
      any("angeglichen" in z for z in bericht["migrated"]),
      bericht["migrated"][:3])

# Und das, was ein Tabellenneubau kaputtmachen kann:
with motor_u.connect() as conn:
    check("keine Zeile verloren",
          conn.execute(text("SELECT COUNT(*) FROM host")).scalar() == 3)
    check("die Inhalte stimmen noch",
          [r[0] for r in conn.execute(text(
              "SELECT hostname FROM host ORDER BY hostname")).fetchall()]
          == ["KK-DREI", "KK-EINS", "KK-ZWEI"])
    check("agent_token_hash ist erhalten",
          conn.execute(text(
              "SELECT agent_token_hash FROM host "
              "WHERE hostname='KK-EINS'")).scalar() == "hash0")
    check("die Datenbank ist unversehrt",
          conn.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok")
    check("keine gebrochenen Fremdschluessel",
          not conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall())
    check("keine Reste der Umbautabelle",
          not conn.execute(text(
              "SELECT name FROM sqlite_master WHERE name LIKE '%_neu'"
          )).fetchall())

# Der Index aus F-33 ist der, den ein Neubau am ehesten verliert: er steht
# ueber lower(hostname) und kommt nicht aus dem Modell.
with motor_u.begin() as conn:
    try:
        host_einfuegen(conn, "kk-eins", "x")
        doppelt = True
    except Exception:  # noqa: BLE001
        doppelt = False
check("der eindeutige Index ueber lower(hostname) haelt weiterhin",
      not doppelt)

# Und die eigentliche Wirkung, um die es ging.
with motor_u.begin() as conn:
    conn.execute(text(
        "UPDATE host SET agent_token_hash = NULL WHERE hostname='KK-ZWEI'"))
check("das Zuruecknehmen eines Tokens geht jetzt",
      motor_u.connect().execute(text(
          "SELECT agent_token_hash FROM host "
          "WHERE hostname='KK-ZWEI'")).scalar() is None)

# Ein zweiter Lauf darf nichts mehr tun. Eine Migration, die bei jedem
# Start Tabellen neu baut, waere bei jedem Neustart ein Datenrisiko.
bericht2 = migrate.migrate(motor_u)
check("ein zweiter Lauf baut nichts mehr um",
      not any("angeglichen" in z for z in bericht2["migrated"]),
      bericht2["migrated"][:3])


# ======================================================================
# Der Umbau haelt an, wenn Daten im Weg stehen
# ======================================================================
print()
print("--- Ein NULL in einer Pflichtspalte haelt den Umbau an ---")
# Eine Migration, die in so einem Fall abbricht, liesse die Anlage auf
# einem alten Stand stehen. Eine, die die Zeile stillschweigend
# wegwirft, waere schlimmer. Also: Tabelle unveraendert lassen und es
# in den Bericht schreiben.
sperr = TMP / "sperre.db"
motor_s = create_engine(f"sqlite:///{sperr}")
SQLModel.metadata.create_all(motor_s)
_alt_machen(motor_s, "host", streng=("agent_token_hash",), locker=("sort_order",))
with motor_s.begin() as conn:
    host_einfuegen(conn, "KK-LEER")
    conn.execute(text("UPDATE host SET sort_order = NULL"))

bericht_s = migrate.migrate(motor_s)
check("die Tabelle wird nicht angeglichen",
      not any("host an das Modell" in z for z in bericht_s["migrated"]),
      bericht_s["migrated"][:3])
check("und der Bericht nennt Spalte und Anzahl",
      any("sort_order" in z and "1 Zeilen" in z for z in bericht_s["notes"]),
      bericht_s["notes"][:3])
with motor_s.connect() as conn:
    check("die Zeile ist noch da",
          conn.execute(text("SELECT COUNT(*) FROM host")).scalar() == 1)


# ======================================================================
# Der Abgleich darf nichts verändern
# ======================================================================
print()
print("--- Der Abgleich ist lesend ---")
# Eine Diagnose, die selbst schreibt, ist auf einer Produktivdatenbank
# nicht zu gebrauchen - und genau dort soll sie laufen.
m4 = frische_db("lesend.db")
vorher = (TMP / "lesend.db").read_bytes()
migrate.schema_abweichungen(m4)
m4.dispose()
check("die Datei ist danach unveraendert",
      (TMP / "lesend.db").read_bytes() == vorher)

quelle = (WURZEL / "backend" / "migrate.py").read_text(encoding="utf-8")
fn = quelle[quelle.index("def schema_abweichungen("):]
fn = fn[:fn.index("\nif __name__")]
check("und schreibt auch im Quelltext nichts",
      not any(w in fn for w in ("INSERT", "UPDATE", "DELETE", "ALTER",
                                "DROP", "CREATE", "engine.begin()")),
      [w for w in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP",
                   "CREATE", "engine.begin()") if w in fn])

# Der Aufruf von Hand oeffnet ausdruecklich nur lesend.
check("der Aufruf von Hand oeffnet die Datei mit mode=ro",
      "mode=ro" in quelle)


# ======================================================================
# Wenn der Umbau schiefgeht, bleibt die alte Tabelle stehen
# ======================================================================
print()
print("--- Ein misslungener Umbau darf nichts kosten ---")
# Der gefaehrlichste Fall ueberhaupt: der Umbau bricht mittendrin ab,
# nachdem die alte Tabelle schon geloescht ist. Dagegen steht die
# Transaktion - aber "steht in einer Transaktion" ist eine Behauptung,
# solange es niemand ausloest.
#
# Ausgeloest wird es hier mit Daten, die eine Bedingung des Modells
# verletzen: zwei Hosts mit demselben agent_token_hash. Die alte Tabelle
# hat dort keinen eindeutigen Index, das Modell verlangt einen - beim
# Anlegen scheitert er.
kaputt = TMP / "kaputt.db"
motor_k = create_engine(f"sqlite:///{kaputt}")
SQLModel.metadata.create_all(motor_k)
_alt_machen(motor_k, "host", streng=("agent_token_hash",), locker=LOCKER)
with motor_k.begin() as conn:
    host_einfuegen(conn, "KK-A", "gleicher-hash")
    host_einfuegen(conn, "KK-B", "gleicher-hash")

bericht_k = migrate.migrate(motor_k)
with motor_k.connect() as conn:
    check("beide Zeilen sind noch da",
          conn.execute(text("SELECT COUNT(*) FROM host")).scalar() == 2)
    check("die Tabelle ist unversehrt",
          conn.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok")
    check("und keine halbfertige Tabelle liegt herum",
          not conn.execute(text(
              "SELECT name FROM sqlite_master WHERE name LIKE '%_neu'"
          )).fetchall())
check("der Bericht sagt, dass es nicht ging",
      any("host" in z and "nicht" in z.lower() for z in bericht_k["notes"]),
      bericht_k["notes"][:2])

# Und die zweite Sicherung: stimmt die Zeilenzahl nach dem Kopieren
# nicht, wird zurueckgerollt. Ausloesen laesst sich das nur, indem man
# den Zaehler belügt - eine echte Datenbank verliert beim INSERT ... 
# SELECT keine Zeilen, ohne vorher eine Ausnahme zu werfen.
print()
print("--- Die Zeilenzahl wird verglichen ---")
echt_zaehlen = migrate._zeilen
aufrufe = {"n": 0}


def _luegen(conn, tabelle):
    aufrufe["n"] += 1
    # Der erste Aufruf ist das "vorher", der zweite das "nachher".
    wert = echt_zaehlen(conn, tabelle)
    return wert + 1 if aufrufe["n"] == 2 else wert


luege = TMP / "luege.db"
motor_l = create_engine(f"sqlite:///{luege}")
SQLModel.metadata.create_all(motor_l)
_alt_machen(motor_l, "host", streng=("agent_token_hash",), locker=LOCKER)
with motor_l.begin() as conn:
    host_einfuegen(conn, "KK-ZAEHL", "z1")

migrate._zeilen = _luegen
try:
    bericht_l = migrate.migrate(motor_l)
finally:
    migrate._zeilen = echt_zaehlen

check("eine abweichende Zeilenzahl haelt den Umbau an",
      any("Zeilen vorher" in z for z in bericht_l["notes"]),
      bericht_l["notes"][:2])
with motor_l.connect() as conn:
    check("und die Tabelle steht unveraendert da",
          conn.execute(text("SELECT COUNT(*) FROM host")).scalar() == 1)
    check("mit ihrer alten Form",
          any(r[1] == "agent_token_hash" and r[3] for r in
              conn.execute(text("PRAGMA table_info(host)")).fetchall()))


# ======================================================================
# Die Annahme, auf der der Umbau steht
# ======================================================================
print()
print("--- Fremdschluessel sind aus, und das muss so bleiben ---")
# Der Tabellenneubau verzichtet bewusst darauf, PRAGMA foreign_keys zu
# schalten - die Durchsetzung ist in SQLite ab Werk aus, je Verbindung,
# und CO-37 schaltet sie nirgends ein. Wird das eines Tages geaendert,
# gehoert der Umbau erneut angesehen: dann kann ein DROP TABLE in
# verweisenden Tabellen aufraeumen.
#
# Diese Zeilen halten die Annahme fest, damit die Aenderung nicht
# unbemerkt bleibt.
motor_fk = frische_db("fk.db")
with motor_fk.connect() as conn:
    check("SQLite hat die Durchsetzung ab Werk aus",
          conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0)

_backend = WURZEL / "backend"
_schalter = []
for datei in sorted(_backend.glob("*.py")):
    quelle = datei.read_text(encoding="utf-8")
    ohne_kommentar = "\n".join(
        z for z in quelle.splitlines() if not z.strip().startswith("#"))
    if "foreign_keys" in ohne_kommentar:
        _schalter.append(datei.name)
check("kein Backend-Modul schaltet sie ein", not _schalter, _schalter)

# Und der Umbau selbst fasst sie nicht an.
_fn = quelle_um = (WURZEL / "backend" / "migrate.py").read_text(encoding="utf-8")
_umbau = quelle_um[quelle_um.index("def _tabelle_angleichen("):]
_umbau = _umbau[:_umbau.index("\ndef ", 1)]
check("und der Umbau schaltet nichts um",
      "PRAGMA foreign_keys" not in "\n".join(
          z for z in _umbau.splitlines() if not z.strip().startswith("#")))


# ======================================================================
# Der Ruecklauf ist Sache des Umbaus, nicht des Verbindungspools
# ======================================================================
print()
print("--- Der Umbau rollt selbst zurueck ---")
# Dass SQLAlchemy eine zurueckgegebene Verbindung zurueckrollt, ist eine
# Voreinstellung (pool_reset_on_return). Wer sich darauf verlaesst, hat
# die Datensicherheit einer Migration an eine Einstellung gehaengt, die
# jemand aendern kann, ohne von dieser Datei zu wissen.
#
# Deshalb dieselbe Probe noch einmal mit abgeschaltetem Ruecklauf des
# Pools: geht der Umbau schief, muss trotzdem alles dastehen wie vorher.
ohne_reset = TMP / "ohne-reset.db"
motor_o = create_engine(f"sqlite:///{ohne_reset}", pool_reset_on_return=None)
SQLModel.metadata.create_all(motor_o)
_alt_machen(motor_o, "host", streng=("agent_token_hash",), locker=LOCKER)
with motor_o.begin() as conn:
    host_einfuegen(conn, "KK-O-A", "gleicher-hash")
    host_einfuegen(conn, "KK-O-B", "gleicher-hash")

bericht_o = migrate.migrate(motor_o)
check("der Umbau ist misslungen, wie vorgesehen",
      any("nicht angleichen" in z for z in bericht_o["notes"]),
      bericht_o["notes"][:1])

# Eine frische Verbindung, damit nichts aus dem Pool die Antwort faerbt.
pruef = create_engine(f"sqlite:///{ohne_reset}")
with pruef.connect() as conn:
    check("beide Zeilen stehen noch da",
          conn.execute(text("SELECT COUNT(*) FROM host")).scalar() == 2)
    check("und keine Zwischentabelle liegt herum",
          not conn.execute(text(
              "SELECT name FROM sqlite_master WHERE name='host_neu'"
          )).fetchall(),
          conn.execute(text(
              "SELECT name FROM sqlite_master WHERE type='table'")).fetchall())

# Und die Sperre ist weg. Ohne ausdruecklichen Ruecklauf ginge die
# Verbindung mit offener Transaktion in den Pool zurueck und hielte die
# Schreibsperre der Datei - der naechste Schreiber liefe in "database is
# locked". Auf der Platte saehe man davon nichts, deshalb wird es hier
# ausprobiert statt nachgesehen.
sperre = create_engine(f"sqlite:///{ohne_reset}",
                       connect_args={"timeout": 2})
try:
    with sperre.begin() as conn:
        host_einfuegen(conn, "KK-NACH-DEM-FEHLER", "frei", 42)
    ging_los = True
    fehlermeldung = ""
except Exception as exc:                                    # noqa: BLE001
    ging_los = False
    fehlermeldung = str(exc)[:120]
check("und die Datei ist wieder beschreibbar", ging_los, fehlermeldung)

# Der ausdrueckliche Ruecklauf im Fehlerzweig laesst sich von aussen
# nicht ausloesen: der SQLite-Treiber rollt hier von sich aus zurueck,
# und wo nichts festgeschrieben wurde, ist auf der Platte auch nichts zu
# sehen. Gemessen wurde beides oben - beschreibbar und unveraendert.
#
# Die Zeile steht trotzdem im Quelltext, und diese Pruefung haelt sie
# fest: sonst haengt die Datensicherheit einer Migration an einem
# Verhalten, das der Treiber nirgends zusagt.
_quelle_m = (WURZEL / "backend" / "migrate.py").read_text(encoding="utf-8")
_umbau_q = _quelle_m[_quelle_m.index("def _tabelle_angleichen("):]
_umbau_q = _umbau_q[:_umbau_q.index("\ndef ", 1)]
_fehlerzweig = _umbau_q[_umbau_q.index("except BaseException:"):]
check("und der Fehlerzweig rollt ausdruecklich zurueck",
      'cur.execute("ROLLBACK")' in _fehlerzweig[:400])


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
