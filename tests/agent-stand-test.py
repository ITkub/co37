"""
CO-37 - der Agent meldet seinen mitgelieferten Stand, der Server vergleicht.

Anlass, am 2026-11-17 gemessen: auf einem Windows-Host lief die
Selbstaktualisierung auf 0.50.1 durch, und der eingebettete Interpreter
meldete weiter 3.14.7. self_update() ersetzt nur agent.py - Laufzeit und
die acht gepinnten Bibliotheken kommen ausschliesslich ueber eine
Neuinstallation des Pakets auf den Host. Sichtbar war das nirgends: der
Agent meldete seine Laufzeitfassung nicht, und der Server hielt den Host
fuer aktuell. Dasselbe galt bei 0.48.0, als certifi 2026.7.22 angreifbar
war.

Diese Reihe prueft die Sichtbarkeit, nicht die Verteilung. Verteilt wird
bewusst nichts.

Teil 1 laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/agent-stand-test.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
DB_PATH = TMP / "aufstieg.db"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


print("\n--- Teil 1: Schemaschritt fuer host.bundled ---")

# Eine Datenbank nachbauen, wie sie vor der Spalte aussah. Nur eine
# schlanke host-Tabelle noetig: migrate.py fragt jede Spalte einzeln per
# PRAGMA table_info ab, weitere fehlende Spalten stoeren hier nicht.
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
conn.execute("INSERT INTO setting (key, value) VALUES ('schema_version', '25')")
conn.commit()
conn.close()

os.environ["CO37_DB"] = f"sqlite:///{DB_PATH}"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import migrate  # noqa: E402
import models  # noqa: E402,F401  registriert alle Tabellen bei SQLModel.metadata
from sqlmodel import SQLModel, create_engine  # noqa: E402

engine = create_engine(os.environ["CO37_DB"])

# Genau der Ablauf aus main.py beim Start.
SQLModel.metadata.create_all(engine)
migrate.migrate(engine)

conn = sqlite3.connect(DB_PATH)
cols = {r[1] for r in conn.execute("PRAGMA table_info(host)").fetchall()}
check("host.bundled ergaenzt", "bundled" in cols, sorted(cols))

# Der Bestandshost bleibt stehen, und die neue Spalte ist bei ihm leer -
# NICHT NULL: eine JSON-Spalte, die NULL zulaesst, zwingt jeden Leser zu
# einer Sonderbehandlung (derselbe Grund wie bei reboot_reasons).
try:
    zeile = conn.execute("SELECT hostname, bundled FROM host WHERE id = 1").fetchone()
except sqlite3.OperationalError as exc:
    # Fehlt die Spalte, soll die Reihe das als Befund melden und
    # weiterlaufen - nicht mit einem Rueckverfolgungsprotokoll abbrechen
    # und die restlichen Pruefungen verschlucken.
    zeile = None
    print(f"       (Abfrage nicht moeglich: {exc})")
check("Bestandshost ueberlebt den Aufstieg", bool(zeile) and zeile[0] == "bestandshost", zeile)
conn.close()

# Gegen die Konstante, nicht gegen eine abgetippte Zahl: sonst gehoerte
# diese Zeile bei jeder weiteren Schemaerweiterung mitgepflegt, und wer
# das vergisst, bekommt einen roten Test ohne echten Befund.
check(f"Version nach dem Aufstieg ist {migrate.SCHEMA_VERSION}",
      migrate.get_version(engine) == migrate.SCHEMA_VERSION,
      migrate.get_version(engine))

# Das Modell kennt die Spalte auch - sonst ergaenzt der Aufstieg eine
# Spalte, die niemand liest.
check("Host.bundled steht im Modell",
      "bundled" in models.Host.model_fields,
      sorted(models.Host.model_fields)[:5])

print("\n--- Teil 2: der Agent sammelt seinen Stand ein ---")

import importlib.util  # noqa: E402
import platform  # noqa: E402
import shutil  # noqa: E402


def lade_agent():
    """
    Laedt agent.py als Modul aus einem eigenen Verzeichnis.

    Eigenes Verzeichnis samt agent.conf, weil sich der Agent ohne
    Konfiguration schon beim Laden beendet - dieselbe Hilfe wie in
    agent-haertung-test.py.
    """
    ordner = TMP / "agentmodul"
    ordner.mkdir(parents=True, exist_ok=True)
    shutil.copy(WURZEL / "agent" / "agent.py", ordner / "agent.py")
    (ordner / "agent.conf").write_text("server = http://127.0.0.1:1\n",
                                       encoding="utf-8")
    spec = importlib.util.spec_from_file_location("co37agent_stand",
                                                  ordner / "agent.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules["co37agent_stand"] = modul
    spec.loader.exec_module(modul)
    return modul


def dist_info(ordner: Path, verzeichnis: str, name: str = "", fassung: str = ""):
    """
    Ein .dist-info nachbauen, wie pip --target es hinterlaesst.

    Der Verzeichnisname ist das Entscheidende: genau daraus liest
    lies_installiert() in packaging/build_msi.py Name und Fassung, und der
    Agent muss dasselbe tun - sonst vergleicht der Server zwei verschieden
    gewonnene Angaben.
    """
    d = ordner / f"{verzeichnis}.dist-info"
    d.mkdir(parents=True, exist_ok=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {fassung}\n",
        encoding="utf-8")
    return d


agent = lade_agent()
check("bundled_stand() gibt es", hasattr(agent, "bundled_stand"))

if hasattr(agent, "bundled_stand"):
    # Ein site-packages nachbauen: zwei vollstaendige Pakete und eines
    # ohne lesbare METADATA.
    site = TMP / "site-packages"
    dist_info(site, "certifi-2026.10.23", "certifi", "2026.10.23")
    dist_info(site, "urllib3-2.8.0", "urllib3", "2.8.0")
    # Der Name im Verzeichnis ist unterstrichen, in requirements.txt steht
    # er mit Bindestrich. Ohne Normalisierung nach PEP 503 meldete der
    # Server fuer jeden gesunden Host eine Abweichung - der Fall, der
    # diese Pruefung wert macht.
    dist_info(site, "charset_normalizer-3.5.1", "charset-normalizer", "3.5.1")
    # Kein Fassungsteil im Verzeichnisnamen: nicht aufloesbar.
    dist_info(site, "kaputt", "kaputt", "9.9.9")

    stand = agent.bundled_stand(site)
    pakete = (stand or {}).get("packages", {})

    check("certifi erscheint mit seiner Fassung",
          pakete.get("certifi") == "2026.10.23", pakete.get("certifi"))
    check("urllib3 erscheint mit seiner Fassung",
          pakete.get("urllib3") == "2.8.0", pakete.get("urllib3"))
    check("der Name ist nach PEP 503 normalisiert",
          pakete.get("charset-normalizer") == "3.5.1", sorted(pakete))
    # Ausgelassen statt geraten: ein Verzeichnisname ohne Fassungsteil
    # laesst sich nicht aufloesen, und eine Vermutung erzeugte im
    # Vergleich gegen das Paket einen Hinweis, den niemand aufloesen kann.
    check("ein nicht aufloesbares .dist-info wird ausgelassen",
          "kaputt" not in pakete, sorted(pakete))
    check("die Python-Fassung steht dabei",
          (stand or {}).get("python") == platform.python_version(),
          (stand or {}).get("python"))

    leer = TMP / "site-leer"
    leer.mkdir(parents=True, exist_ok=True)
    stand_leer = agent.bundled_stand(leer)
    check("ein leeres site-packages ergibt keine Pakete",
          (stand_leer or {}).get("packages") == {}, stand_leer)

    # Ohne Parameter: unter Linux gibt es nichts zu melden, dort kommt
    # Python aus der Distribution. Die Reihe laeuft im Container, also
    # ist IS_WINDOWS hier falsch.
    if not agent.IS_WINDOWS:
        check("ohne Windows meldet der Agent nichts",
              agent.bundled_stand() is None, agent.bundled_stand())

# Der Stand muss auch im Heartbeat landen - sonst sammelt der Agent
# etwas ein, das nie ankommt. Geprueft wird am Quelltext, weil der
# Heartbeat selbst ein Backend und eine Anmeldung braucht; die Wirkung
# ueber HTTP prueft Teil 3.
agent_quelle = (WURZEL / "agent" / "agent.py").read_text(encoding="utf-8")
check("der Heartbeat traegt das Feld bundled",
      '"bundled"' in agent_quelle, None)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
