"""
CO-37 - WAL-Modus und Wartezeit der Datenbank.

Grund fuer dieses Geruest: SQLite laeuft ab Werk im Rollback-Journal, und
dort sperrt ein Schreiber die ganze Datei. Das Backend bedient die
Heartbeats im Threadpool - zwei Hosts, die sich im selben Augenblick
melden, waehrend nebenher ein Auftrag geschrieben wird, bekommen dann
nicht etwa eine Wartezeit, sondern sofort "database is locked" und damit
eine 500. Bei zehn Hosts faellt das nicht auf, bei fuenfzig schon.

Zwei PRAGMAs beheben das, und beide werden hier an der Datenbank
gemessen - nicht im Quelltext gesucht:

    journal_mode=WAL    Leser blockieren Schreiber nicht mehr
    busy_timeout=5000   ein wartender Schreiber wartet, statt zu scheitern

Die dritte Pruefung ist die eigentliche Zusage: haelt eine Verbindung die
Schreibsperre, muss die zweite WARTEN und danach durchkommen. Ohne
busy_timeout scheitert sie im selben Augenblick.

Die vierte sichert eine Nebenwirkung ab. 'migrate.py --pruefen' oeffnet
die Datenbank mit mode=ro. Eine nur lesende Verbindung kann die
Begleitdatei -shm nicht anlegen; gehoert sie einem anderen Benutzer,
scheitert der Aufruf mit "unable to open database file". Am 2026-11-08
nachgemessen: als Eigentuemer geht es, bei laufendem wie bei gestopptem
Backend. Diese Pruefung haelt das fest, damit es nicht unbemerkt kippt.

Laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/sqlite-pragma-test.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/pragma.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def skalar(motor, sql):
    with motor.connect() as c:
        return c.exec_driver_sql(sql).scalar()


# ----------------------------------------------------------------------
# 1. Die Funktion setzt beide PRAGMAs
# ----------------------------------------------------------------------
# Gemessen wird an einem FRISCHEN Motor, damit die Pruefung nicht davon
# lebt, dass main beim Import zufaellig schon etwas gesetzt hat.
frisch = create_engine(f"sqlite:///{TMP}/frisch.db",
                       connect_args={"check_same_thread": False})
main.sqlite_pragmas_anhaengen(frisch)

check("journal_mode ist wal",
      str(skalar(frisch, "PRAGMA journal_mode")).lower() == "wal",
      skalar(frisch, "PRAGMA journal_mode"))

check("busy_timeout ist 5000 ms",
      int(skalar(frisch, "PRAGMA busy_timeout")) == 5000,
      skalar(frisch, "PRAGMA busy_timeout"))

# ----------------------------------------------------------------------
# 2. Der Motor des Backends hat sie auch wirklich
# ----------------------------------------------------------------------
# Getrennt von Pruefung 1, und das ist der Punkt: eine Funktion, die
# richtig aussieht und nie aufgerufen wird, ist in diesem Projekt schon
# zweimal vorgekommen (F-46, F-60).
check("main.engine laeuft im WAL-Modus",
      str(skalar(main.engine, "PRAGMA journal_mode")).lower() == "wal",
      skalar(main.engine, "PRAGMA journal_mode"))

check("main.engine hat die Wartezeit",
      int(skalar(main.engine, "PRAGMA busy_timeout")) == 5000,
      skalar(main.engine, "PRAGMA busy_timeout"))

# ----------------------------------------------------------------------
# 3. Ein wartender Schreiber wartet, statt zu scheitern
# ----------------------------------------------------------------------
# Die eigentliche Zusage. Verbindung A nimmt die Schreibsperre und haelt
# sie eine Sekunde. B will im selben Augenblick schreiben.
#
# Mit busy_timeout kommt B nach rund einer Sekunde durch. Ohne scheitert
# B sofort mit "database is locked" - der Unterschied, um den es geht.
# Deshalb wird BEIDES geprueft: dass B durchkam UND dass es gewartet hat.
# Ohne die zweite Haelfte waere die Pruefung auch dann gruen, wenn die
# Sperre nie zustande kam.
sperrdb = f"sqlite:///{TMP}/sperre.db"
motor_a = create_engine(sperrdb, connect_args={"check_same_thread": False})
motor_b = create_engine(sperrdb, connect_args={"check_same_thread": False})
main.sqlite_pragmas_anhaengen(motor_a)
main.sqlite_pragmas_anhaengen(motor_b)

with motor_a.connect() as c:
    c.exec_driver_sql("CREATE TABLE t (x INTEGER)")
    c.commit()

HALTEDAUER = 1.0
sperre_steht = threading.Event()


def haelt_die_sperre():
    with motor_a.connect() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        c.exec_driver_sql("INSERT INTO t VALUES (1)")
        sperre_steht.set()
        time.sleep(HALTEDAUER)
        c.commit()


t = threading.Thread(target=haelt_die_sperre)
t.start()
sperre_steht.wait(timeout=5)

start = time.monotonic()
b_fehler = ""
try:
    with motor_b.connect() as c:
        c.exec_driver_sql("BEGIN IMMEDIATE")
        c.exec_driver_sql("INSERT INTO t VALUES (2)")
        c.commit()
except Exception as exc:  # noqa: BLE001
    b_fehler = str(exc)
gewartet = time.monotonic() - start
t.join()

check("der zweite Schreiber kommt durch, statt an der Sperre zu scheitern",
      b_fehler == "", b_fehler or "ohne Fehler")

check("und er hat dabei tatsaechlich gewartet",
      gewartet >= HALTEDAUER * 0.5, f"{gewartet:.2f}s gewartet")

# ----------------------------------------------------------------------
# 4. 'migrate.py --pruefen' kommt an eine WAL-Datenbank heran
# ----------------------------------------------------------------------
# Nebenwirkung des WAL-Modus, gegen die hier abgesichert wird: der Aufruf
# oeffnet mit mode=ro und darf dabei nicht an der Begleitdatei scheitern.
# Geprueft wird genau das - nicht, zu welchem Urteil migrate kommt.
pruef_db = TMP / "pruef.db"
pruef_motor = create_engine(f"sqlite:///{pruef_db}",
                            connect_args={"check_same_thread": False})
main.sqlite_pragmas_anhaengen(pruef_motor)
SQLModel.metadata.create_all(pruef_motor)
pruef_motor.dispose()

lauf = subprocess.run(
    [sys.executable, str(WURZEL / "backend" / "migrate.py"),
     "--pruefen", str(pruef_db)],
    capture_output=True, text=True, timeout=120,
)
ausgabe = (lauf.stdout or "") + (lauf.stderr or "")

check("migrate.py --pruefen oeffnet die WAL-Datenbank",
      "unable to open database file" not in ausgabe, ausgabe.strip()[-200:])

# Rueckgabe 2 heisst Bedienfehler - dann hat der Aufruf die Datenbank gar
# nicht erst angesehen, und die Pruefung darueber waere wertlos gruen.
check("migrate.py --pruefen hat die Datenbank wirklich gelesen",
      lauf.returncode in (0, 1), f"Rueckgabe {lauf.returncode}")

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
