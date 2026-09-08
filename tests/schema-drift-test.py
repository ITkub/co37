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


# Alle Spalten ohne NULL-Erlaubnis muessen beim Einfuegen mitkommen -
# sonst scheitert das Beispiel an einer ganz anderen Spalte als der, um
# die es geht, und die Meldung zeigt in die falsche Richtung.
SPALTEN = ("INSERT INTO host (hostname, agent_token_hash, approval_state, "
           "status, last_seen_secure, updates_available, security_updates, "
           "reboot_required, updates_require_reboot, downtime_minutes, "
           "checkmk_downtime_all, auto_reboot, patch_enabled, "
           "patch_auto_reboot, patch_grace_hours, patch_followup_left, "
           "sort_order)")


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

# migrate() darueberlaufen lassen - so, wie es beim Update passiert.
bericht = migrate.migrate(motor_alt)
check("migrate() laeuft ohne Ausnahme durch", isinstance(bericht, dict),
      type(bericht).__name__)

# Und jetzt die Frage, um die es geht.
abw = migrate.schema_abweichungen(motor_alt)
treffer = [z for z in abw if "agent_token_hash" in z]
check("der Abgleich meldet host.agent_token_hash", bool(treffer), abw[:5])
check("und sagt, in welche Richtung es auseinanderliegt",
      bool(treffer) and "NOT NULL" in treffer[0], treffer[:1])

# Die eigentliche Wirkung: auf dieser Datenbank scheitert das
# Zuruecknehmen des Tokens, auf der frischen nicht. Ohne diese Zeile
# waere der Abgleich eine Behauptung ueber Metadaten.
with motor_alt.begin() as conn:
    conn.execute(text(
        SPALTEN + " VALUES ('TEST-DRIFT', 'abc', 'approved', 'ok', 0, 0, 0, 0, "
        "0, 30, 0, 0, 0, 0, 4, 0, 0)"))
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
        conn.execute(text(
            SPALTEN + " VALUES ('TEST-DRIFT', 'abc', 'approved', 'ok', 0, 0, "
            "0, 0, 0, 30, 0, 0, 0, 0, 4, 0, 0)"))
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


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
