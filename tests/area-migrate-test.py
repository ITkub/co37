"""
CO-37 - Schema-Aufstieg auf Bereiche (Area).

Grund fuer diesen Test: der Aufstieg auf 0.4.0 endete mehrfach in "no such
column", weil SQLModel.metadata.create_all() bestehende Tabellen nicht
aendert, nur fehlende anlegt. Dieser Test baut eine Datenbank nach, wie sie
vor der Bereichs-Tabelle aussah - ohne host.area_id, ohne
host.area_patch_last_run, ohne die Tabelle 'area' - und prueft, dass der
echte Aufstiegsweg (erst create_all(), dann migrate(), genau wie main.py
beim Start) sie nachtraeglich ergaenzt, ohne den vorhandenen Bestandshost
zu verlieren oder ihm versehentlich einen Bereich zuzuweisen.

Laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/area-migrate-test.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
DB_PATH = TMP / "upgrade.db"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ------------------------------------------------- Alte Datenbank nachbauen
# Nur eine schlanke host-Tabelle noetig, ohne die beiden neuen Spalten -
# migrate.py fragt jede Spalte einzeln per PRAGMA table_info ab, weitere
# fehlende Spalten aus EXPECTED_COLUMNS stoeren den Test hier nicht.
conn = sqlite3.connect(DB_PATH)
conn.execute("""
    CREATE TABLE host (
        id INTEGER PRIMARY KEY,
        hostname VARCHAR,
        sort_order INTEGER DEFAULT 0
    )
""")
conn.execute("INSERT INTO host (id, hostname, sort_order) VALUES (1, 'bestandshost', 1)")
conn.execute("CREATE TABLE setting (key VARCHAR PRIMARY KEY, value VARCHAR)")
conn.execute("INSERT INTO setting (key, value) VALUES ('schema_version', '16')")
conn.commit()
conn.close()

os.environ["CO37_DB"] = f"sqlite:///{DB_PATH}"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import migrate  # noqa: E402
import models  # noqa: E402,F401  registriert alle Tabellen bei SQLModel.metadata
from sqlmodel import SQLModel, create_engine  # noqa: E402

engine = create_engine(os.environ["CO37_DB"])

# Genau der Ablauf aus main.py beim Start: erst neue Tabellen anlegen,
# dann bestehende nachruesten.
SQLModel.metadata.create_all(engine)
migrate.migrate(engine)

# Gegen die Konstante, nicht gegen eine abgetippte Zahl: sonst gehoert
# diese Zeile bei jeder Schemaerweiterung mitgepflegt, und wer das
# vergisst, bekommt einen roten Test ohne echten Befund. Geprueft wird,
# worauf es hier ankommt - dass der Aufstieg die Version fortschreibt.
check(f"Version nach dem Aufstieg ist {migrate.SCHEMA_VERSION}",
      migrate.get_version(engine) == migrate.SCHEMA_VERSION,
      migrate.get_version(engine))

conn = sqlite3.connect(DB_PATH)
cols = {r[1] for r in conn.execute("PRAGMA table_info(host)").fetchall()}
check("host.area_id ergaenzt", "area_id" in cols)
check("host.area_patch_last_run ergaenzt", "area_patch_last_run" in cols)

tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
check("Tabelle area angelegt", "area" in tables)

row = conn.execute(
    "SELECT hostname, sort_order, area_id FROM host WHERE id=1"
).fetchone()
check("Bestandshost bleibt erhalten", row is not None and row[0] == "bestandshost", row)
check("Bestandshost bekommt keinen Bereich zugewiesen",
      row is not None and row[2] is None, row)

conn.close()

ok, missing = migrate.verify(engine)
check("verify() meldet vollstaendiges Schema", ok, missing)


# ------------------------------------------- Bestehende area-Tabelle ohne
# parent_id (Anlage, die Bereiche schon vor 0.38.3 kannte). create_all()
# aendert die vorhandene Tabelle NICHT - erst migrate() ergaenzt die Spalte.
# Genau der Fall, der den Aufstieg auf 0.4.0 mit host.area_id gebrochen hat,
# nur eine Ebene tiefer.
DB2 = TMP / "sub.db"
conn = sqlite3.connect(DB2)
conn.execute("CREATE TABLE host (id INTEGER PRIMARY KEY, hostname VARCHAR, sort_order INTEGER DEFAULT 0)")
# area-Tabelle wie vor 0.38.3: mit name und sort_order, aber ohne parent_id.
conn.execute("CREATE TABLE area (id INTEGER PRIMARY KEY, name VARCHAR, sort_order INTEGER DEFAULT 0)")
conn.execute("INSERT INTO area (id, name, sort_order) VALUES (1, 'Bestandsbereich', 1)")
conn.execute("CREATE TABLE setting (key VARCHAR PRIMARY KEY, value VARCHAR)")
conn.execute("INSERT INTO setting (key, value) VALUES ('schema_version', '22')")
conn.commit()
conn.close()

engine2 = create_engine(f"sqlite:///{DB2}")
SQLModel.metadata.create_all(engine2)   # aendert die bestehende area-Tabelle nicht
migrate.migrate(engine2)

conn = sqlite3.connect(DB2)
acols = {r[1] for r in conn.execute("PRAGMA table_info(area)").fetchall()}
check("area.parent_id in bestehender Tabelle ergaenzt", "parent_id" in acols, acols)
row = conn.execute("SELECT name, parent_id FROM area WHERE id=1").fetchone()
check("Bestandsbereich bleibt erhalten und wird kein Unterbereich",
      row is not None and row[0] == "Bestandsbereich" and row[1] is None, row)
conn.close()
ok2, missing2 = migrate.verify(engine2)
check("verify() nach dem Aufstieg vollstaendig", ok2, missing2)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
