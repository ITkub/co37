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

print("\n--- Teil 3: das Backend nimmt an und prueft ---")

# Der Heartbeat ist angreifbare Eingabe: F-23 und F-33 haben gezeigt, dass
# ein Host dort hostname, os_type, os_version, ip_address und
# agent_version frei setzen kann. Fuer bundled gilt dasselbe. Geprueft
# wird deshalb nicht nur der gute Fall, sondern vor allem, dass Unfug das
# Feld verwirft statt den Heartbeat scheitern zu lassen - ein Agent, der
# an seinem eigenen Heartbeat stirbt, meldet auch nichts anderes mehr.
#
# EIGENE Datenbank, nicht die aus Teil 1: die ist absichtlich
# unvollstaendig nachgebaut (nur die Spalten, die der Aufstieg braucht),
# und das Backend liest beim Lesen eines Hosts alle. Der Pfad muss
# stehen, BEVOR main importiert wird - dort entsteht der Motor.
os.environ["CO37_DB"] = f"sqlite:///{TMP / 'backend.db'}"
import main  # noqa: E402

check("bundled_sauber() gibt es", hasattr(main, "bundled_sauber"))

if hasattr(main, "bundled_sauber"):
    gut = {"python": "3.14.10",
           "packages": {"certifi": "2026.10.23", "urllib3": "2.8.0"}}
    check("ein gueltiger Stand kommt durch",
          main.bundled_sauber(gut) == gut, main.bundled_sauber(gut))

    schlecht = {
        "packages als Zeichenkette": {"python": "3.14.10", "packages": "viele"},
        "ganzes Feld als Liste": ["certifi", "2026.10.23"],
        "None": None,
        "Zeichenkette": "certifi==2026.10.23",
        "Name mit Leerzeichen": {"python": "3.14.10",
                                 "packages": {"cer tifi": "2026.10.23"}},
        "Fassung zu lang": {"python": "3.14.10",
                            "packages": {"certifi": "9" * 200}},
        "zu viele Eintraege": {"python": "3.14.10",
                               "packages": {f"p{i}": "1.0" for i in range(21)}},
        "Python-Fassung als Zahl": {"python": 3.14, "packages": {}},
    }
    for was, wert in schlecht.items():
        try:
            ergebnis = main.bundled_sauber(wert)
            ausnahme = None
        except Exception as exc:  # noqa: BLE001
            ergebnis, ausnahme = "AUSNAHME", exc
        check(f"verworfen statt uebernommen: {was}",
              ergebnis == {} and ausnahme is None, ausnahme or ergebnis)

    # Genau 20 Eintraege sind erlaubt, 21 nicht - die Grenze selbst
    # pruefen, nicht nur ein Stueck weit daneben.
    zwanzig = {"python": "3.14.10",
               "packages": {f"p{i}": "1.0" for i in range(20)}}
    check("genau 20 Eintraege gehen durch",
          main.bundled_sauber(zwanzig) == zwanzig,
          len(main.bundled_sauber(zwanzig).get("packages", {})))

# Die Wirkung ueber HTTP, im eigenen Prozess: ein Heartbeat mit Unfug im
# Feld darf nicht scheitern, und gespeichert werden darf er auch nicht.
from starlette.testclient import TestClient  # noqa: E402

with TestClient(main.app) as klient:
    anmeldung = klient.post("/api/v1/agent/enroll", json={
        "hostname": "TEST-STAND01", "os_type": "windows",
        "os_version": "Windows 11", "agent_version": "0.50.1"})
    token = (anmeldung.json() or {}).get("agent_token", "")
    check("Testhost angemeldet", bool(token), anmeldung.status_code)

    def heartbeat(bundled):
        nutzlast = {"hostname": "TEST-STAND01", "os_type": "windows",
                    "os_version": "Windows 11", "agent_version": "0.50.1"}
        if bundled is not ...:
            nutzlast["bundled"] = bundled
        return klient.post("/api/v1/agent/heartbeat", json=nutzlast,
                           headers={"X-Agent-Token": token})

    def gespeichert():
        with main.Session(main.engine) as s:
            h = s.exec(main.select(models.Host).where(
                models.Host.hostname == "TEST-STAND01")).first()
            return None if h is None else h.bundled

    if token:
        r = heartbeat({"python": "3.14.10", "packages": "viele"})
        check("Heartbeat mit kaputtem bundled bleibt 200",
              r.status_code == 200, r.status_code)
        check("und gespeichert wird nichts", gespeichert() == {}, gespeichert())

        gut = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}
        r = heartbeat(gut)
        check("ein gueltiger Stand landet am Host",
              gespeichert() == gut, gespeichert())

        # Alter Agent: Feld fehlt ganz. Das darf den schon gemeldeten
        # Stand NICHT loeschen - sonst wechselte ein Host bei jedem
        # Wechsel der Agent-Fassung zwischen bekannt und unbekannt.
        r = heartbeat(...)
        check("ein Heartbeat ohne das Feld laesst den Stand stehen",
              gespeichert() == gut, gespeichert())

print("\n--- Teil 4: Vergleich gegen das aktuelle Paket ---")

for name in ("paket_soll", "paket_gebaut_aktuell", "bundled_abweichungen",
             "bundled_vergleich"):
    check(f"{name}() gibt es", hasattr(main, name))

if all(hasattr(main, n) for n in
       ("paket_soll", "paket_gebaut_aktuell", "bundled_abweichungen",
        "bundled_vergleich")):

    # --- die Vergleichsgrundlage ---------------------------------------
    soll = main.paket_soll(WURZEL)
    check("paket_soll() liest die Python-Fassung aus build_msi.py",
          (soll or {}).get("python") == "3.14.10", (soll or {}).get("python"))
    # Acht Pins, und die Namen normalisiert: in der Datei steht
    # charset-normalizer, im .dist-info charset_normalizer. Ohne
    # Normalisierung auf BEIDEN Seiten meldete der Server fuer jeden
    # gesunden Host eine Abweichung.
    check("paket_soll() liest die acht Pins",
          len((soll or {}).get("packages", {})) == 8,
          sorted((soll or {}).get("packages", {})))
    check("die Namen sind normalisiert",
          "charset-normalizer" in (soll or {}).get("packages", {}),
          sorted((soll or {}).get("packages", {})))

    leer_wurzel = TMP / "ohne-packaging"
    (leer_wurzel / "agent").mkdir(parents=True, exist_ok=True)
    check("fehlt build_msi.py, ist die Grundlage unbekannt",
          main.paket_soll(leer_wurzel) is None, main.paket_soll(leer_wurzel))

    # --- gehoert das gebaute Paket zum Stand? --------------------------
    pkg = TMP / "packages"
    pkg.mkdir(parents=True, exist_ok=True)
    check("ohne Paket gilt der Bau als nicht aktuell",
          main.paket_gebaut_aktuell(pkg, "0.50.1") is False)
    (pkg / "co37-agent-0.50.0.msi").write_bytes(b"x")
    check("ein Paket der Vorgaengerfassung zaehlt nicht",
          main.paket_gebaut_aktuell(pkg, "0.50.1") is False)
    (pkg / "co37-agent-0.50.1.msi").write_bytes(b"x")
    check("ein Paket der laufenden Fassung zaehlt",
          main.paket_gebaut_aktuell(pkg, "0.50.1") is True)

    # --- die drei Arten von Abweichung ---------------------------------
    ist = {"certifi": "2026.10.23", "urllib3": "2.8.0"}
    check("gleich ergibt keine Abweichung",
          main.bundled_abweichungen(ist, dict(ist)) == [],
          main.bundled_abweichungen(ist, dict(ist)))
    andere = main.bundled_abweichungen({"certifi": "2026.7.22"},
                                       {"certifi": "2026.10.23"})
    check("eine andere Fassung wird gemeldet",
          len(andere) == 1 and "certifi" in andere[0], andere)
    fehlt = main.bundled_abweichungen({}, {"certifi": "2026.10.23"})
    check("ein fehlendes Paket wird gemeldet",
          len(fehlt) == 1 and "certifi" in fehlt[0], fehlt)
    extra = main.bundled_abweichungen({"neunte": "1.0"}, {})
    check("ein zusaetzliches Paket wird gemeldet",
          len(extra) == 1 and "neunte" in extra[0], extra)

    # --- die Zustandstabelle -------------------------------------------
    class FakeHost:
        def __init__(self, os_type, bundled):
            self.os_type = os_type
            self.bundled = bundled

    win_gut = {"python": "3.14.10", "packages": dict(soll["packages"])} \
        if soll else {"python": "3.14.10", "packages": {}}

    check("ein Linux-Host bleibt unbekannt",
          main.bundled_vergleich(FakeHost("linux", win_gut))[0] == "unbekannt",
          main.bundled_vergleich(FakeHost("linux", win_gut))[0])
    check("ein Windows-Host ohne Meldung bleibt unbekannt",
          main.bundled_vergleich(FakeHost("windows", {}))[0] == "unbekannt")
    check("eine Meldung ohne Pakete bleibt unbekannt",
          main.bundled_vergleich(
              FakeHost("windows", {"python": "3.14.10", "packages": {}}))[0]
          == "unbekannt")

    # Die beiden Zeilen der Tabelle, die an den Dateien auf dem Server
    # haengen: dafuer werden die zwei Funktionen am Modul ersetzt. Keine
    # Attrappe des Verhaltens - der Vergleich selbst laeuft echt.
    echt_soll, echt_gebaut = main.paket_soll, main.paket_gebaut_aktuell
    try:
        main.paket_soll = lambda *a, **k: None
        main.paket_gebaut_aktuell = lambda *a, **k: True
        check("ohne Vergleichsgrundlage bleibt es unbekannt",
              main.bundled_vergleich(FakeHost("windows", win_gut))[0]
              == "unbekannt")

        main.paket_soll = lambda *a, **k: {"python": "3.14.10",
                                           "packages": dict(ist)}
        main.paket_gebaut_aktuell = lambda *a, **k: False
        check("gehoert das gebaute Paket nicht zum Stand, bleibt es unbekannt",
              main.bundled_vergleich(
                  FakeHost("windows",
                           {"python": "3.14.10", "packages": dict(ist)}))[0]
              == "unbekannt")

        main.paket_gebaut_aktuell = lambda *a, **k: True
        zustand, diff = main.bundled_vergleich(
            FakeHost("windows", {"python": "3.14.10", "packages": dict(ist)}))
        check("stimmt alles, ist der Zustand gleich", zustand == "gleich",
              (zustand, diff))

        zustand, diff = main.bundled_vergleich(
            FakeHost("windows", {"python": "3.14.7", "packages": dict(ist)}))
        check("eine aeltere Python-Fassung ist eine Abweichung",
              zustand == "abweichend" and any("3.14.7" in z for z in diff),
              (zustand, diff))
    finally:
        main.paket_soll, main.paket_gebaut_aktuell = echt_soll, echt_gebaut

    # --- und es kommt bis in die Ausgabe -------------------------------
    check("HostRead traegt bundled_state",
          "bundled_state" in main.HostRead.model_fields)
    check("HostRead traegt bundled_diff",
          "bundled_diff" in main.HostRead.model_fields)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
