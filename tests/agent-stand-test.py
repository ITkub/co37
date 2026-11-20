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
import hashlib
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

# Der Bestandshost bleibt stehen. Die Spalte steht bei ihm auf NULL, und
# das ist die bekannte Drift: ALTER TABLE ergaenzt sie ohne NOT NULL,
# waehrend create_all() sie auf einer frischen Datenbank mit NOT NULL
# anlegt - genau wie bei tags und reboot_reasons. Harmlos, weil jeder
# Leser des Feldes NULL aushaelt; geprueft wird das unten in Teil 4
# (isinstance) und hier nur, dass der Host den Aufstieg ueberlebt.
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

print("\n--- Teil 1b: die Spalte bleibt nicht als Abweichung stehen ---")

# Gemessen am 2026-11-17 auf der Anlage: nach dem Aufstieg meldete
# migrate.py --pruefen dauerhaft "host.bundled: Datenbank sagt NULL
# erlaubt, Modell sagt NOT NULL".
#
# Der Grund: ALTER TABLE ADD COLUMN ergaenzt die Spalte ohne NOT NULL.
# angleichen() baut die Tabelle bei jedem Start auf die Form des Modells
# um - aber _tabelle_angleichen() weigert sich zu Recht, solange eine
# Pflichtspalte leer ist. Vorbelegt werden sie an genau EINER Stelle in
# migrate.py, und die zaehlte die Spalten fest auf; bundled fehlte dort.
#
# Die Vorrichtung von Teil 1 taugt dafuer nicht: sie ist absichtlich
# duenn, und der Umbau scheitert dort an ganz anderen Spalten. Deshalb
# hier die Lage der echten Anlage nachgebaut - vollstaendige Tabelle,
# nur bundled nullbar, wie nach einem ALTER TABLE.
DB2 = TMP / "angleichen.db"
os.environ["CO37_DB"] = f"sqlite:///{DB2}"
motor2 = create_engine(os.environ["CO37_DB"])
SQLModel.metadata.create_all(motor2)

SPALTEN = ("hostname, status, agent_token_hash, created_at, tags, "
           "reboot_reasons, checkmk_hosts, patch_days, bundled, "
           "updates_available, security_updates, reboot_required, "
           "updates_require_reboot, downtime_minutes, checkmk_downtime_all, "
           "auto_reboot, patch_enabled, patch_auto_reboot, "
           "patch_grace_hours, patch_followup_left, sort_order, "
           "approval_state, last_seen_secure")
WERTE = ("'bestand','ok','h','2026-01-01 00:00:00','[]','[]','[]','[]',NULL,"
         "0,0,0,0,30,0,0,0,0,4,0,1,'approved',0")

conn = sqlite3.connect(DB2)
ddl = [r[0] for r in conn.execute(
    "SELECT sql FROM sqlite_master WHERE name='host'")][0]
check("das Modell legt bundled als NOT NULL an",
      "bundled JSON NOT NULL" in ddl, ddl[:80])
conn.executescript(ddl.replace("CREATE TABLE host", "CREATE TABLE host_alt")
                      .replace("bundled JSON NOT NULL", "bundled JSON") + ";")
conn.executescript("DROP TABLE host;")
conn.executescript("ALTER TABLE host_alt RENAME TO host;")
conn.execute(f"INSERT INTO host ({SPALTEN}) VALUES ({WERTE})")
conn.execute("UPDATE setting SET value='25' WHERE key='schema_version'")
conn.commit()
conn.close()

vorher = [z for z in migrate.schema_abweichungen(motor2) if "bundled" in z]
check("die Lage ist nachgebaut: bundled weicht vorher ab", bool(vorher), vorher)

bericht = migrate.migrate(motor2)
check("der Umbau meldet keine Probleme",
      not bericht.get("problems"), bericht.get("problems"))
check("host.bundled steht danach nicht mehr als Abweichung da",
      not [z for z in migrate.schema_abweichungen(motor2) if "bundled" in z],
      [z for z in migrate.schema_abweichungen(motor2) if "bundled" in z])

conn = sqlite3.connect(DB2)
check("und der Bestandshost traegt ein leeres Objekt, keine leere Liste",
      conn.execute("SELECT bundled FROM host").fetchone()[0] == "{}",
      conn.execute("SELECT bundled FROM host").fetchone())
conn.close()


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

# Waechter: _normname() im Agenten und normname() im Paketbau muessen
# dasselbe tun. Sie liegen doppelt, weil der Agent packaging/ nicht
# mitgeliefert bekommt - und laufen sie auseinander, meldet der Server
# fuer JEDEN gesunden Host eine Abweichung. Dieselbe Sorte Waechter wie
# schema-drift, i18n-guard und ki-hinweise.
_bm_spec = importlib.util.spec_from_file_location(
    "co37_build_msi_test", WURZEL / "packaging" / "build_msi.py")
_bm = importlib.util.module_from_spec(_bm_spec)
_bm_spec.loader.exec_module(_bm)

if hasattr(agent, "_normname"):
    PROBEN = ["charset_normalizer", "charset-normalizer", "Charset.Normalizer",
              "certifi", "urllib3", "pycparser", "A__B--C..D", "CFFI"]
    abweichend = [n for n in PROBEN
                  if agent._normname(n) != _bm.normname(n)]
    check("Agent und Paketbau normalisieren Namen gleich",
          not abweichend,
          [(n, agent._normname(n), _bm.normname(n)) for n in abweichend])

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
        # Nicht nur ein falsches Unterfeld, sondern die falschen TYPEN -
        # und zwar ueber HTTP. Die Reihe hatte diese Faelle nur direkt an
        # bundled_sauber() gegeben; dort kommen sie an, ueber die Route
        # dagegen entscheidet vorher die Typpruefung des Schemas. Genau
        # in dieser Luecke sass die 422, die die Vorgabe "keine 4xx"
        # verletzte - und ein Agent, der an seinem eigenen Heartbeat
        # scheitert, meldet auch keine Updates mehr.
        for was, wert in (("packages als Zeichenkette",
                           {"python": "3.14.10", "packages": "viele"}),
                          ("ganzes Feld als Liste", ["certifi"]),
                          ("ganzes Feld als Zeichenkette", "certifi==1"),
                          ("ganzes Feld als Zahl", 7),
                          ("ganzes Feld null", None)):
            r = heartbeat(wert)
            check(f"Heartbeat bleibt 200: {was}", r.status_code == 200,
                  (r.status_code, r.text[:120]))
            check(f"und gespeichert wird nichts: {was}",
                  gespeichert() == {}, gespeichert())

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

        # Der Laufzeitbefund reist im selben Feld mit. Missgebildet darf
        # er NUR sich selbst verwerfen: ein Host mit krummem Befund soll
        # nicht auch seine Paketliste verlieren. Und keine 422 - ein
        # Agent, der an seinem eigenen Heartbeat scheitert, meldet auch
        # nichts anderes mehr (Befund C-1 vom 2026-11-17).
        for was, wert in (("Liste", ["a"]),
                          ("Zeichenkette", "kaputt"),
                          ("Zahl", 7),
                          ("null", None),
                          ("unbekannter Zustand", {"zustand": "kaputt"}),
                          ("fehlend als Zeichenkette",
                           {"zustand": "ok", "fehlend": "a"}),
                          ("zu viele Eintraege",
                           {"zustand": "unvollstaendig",
                            "fehlend": [f"d{i}" for i in range(99)]})):
            r = heartbeat({**gut, "laufzeit": wert})
            check(f"Heartbeat bleibt 200 mit krummer Laufzeit: {was}",
                  r.status_code == 200, (r.status_code, r.text[:120]))
            check(f"die Paketliste bleibt erhalten: {was}",
                  (gespeichert() or {}).get("packages") == gut["packages"],
                  gespeichert())
            check(f"der krumme Befund wird verworfen: {was}",
                  "laufzeit" not in (gespeichert() or {}), gespeichert())

        befund = {"zustand": "unvollstaendig",
                  "fehlend": ["lib/site-packages/certifi/cacert.pem"],
                  "abweichend": [], "mehr": 0, "liste": "0.51.2",
                  "rueckfall": True}
        r = heartbeat({**gut, "laufzeit": befund})
        check("ein gueltiger Laufzeitbefund landet am Host",
              (gespeichert() or {}).get("laufzeit") == befund, gespeichert())

print("\n--- Teil 4: Vergleich gegen das aktuelle Paket ---")

for name in ("paket_soll", "paket_gebaut_aktuell", "bundled_abweichungen",
             "bundled_vergleich"):
    check(f"{name}() gibt es", hasattr(main, name))

if all(hasattr(main, n) for n in
       ("paket_soll", "paket_gebaut_aktuell", "bundled_abweichungen",
        "bundled_vergleich")):

    # --- die Vergleichsgrundlage ---------------------------------------
    soll = main.paket_soll(WURZEL)
    # Gegen die Dateien, nicht gegen abgetippte Zahlen: sonst macht die
    # naechste Anhebung von PY_VERSION diese Reihe rot ohne Befund -
    # dieselbe Objektion wie oben bei SCHEMA_VERSION.
    import re as _re  # noqa: E402
    # Eigener Name: _bm ist oben das geladene Modul, und eine Zuweisung
    # hier ueberschrieb es fuer alles, was danach kommt - ein spaeterer
    # Teil fand dann eine Zeichenkette statt des Moduls vor.
    _bm_quelle = (WURZEL / "packaging" / "build_msi.py").read_text(encoding="utf-8")
    PY_ERWARTET = _re.search(r'^PY_VERSION = "([^"]+)"', _bm_quelle, _re.M).group(1)
    PINS_ERWARTET = len([z for z in
                         (WURZEL / "agent" / "requirements.txt")
                         .read_text(encoding="utf-8").splitlines()
                         if _re.match(r"^[A-Za-z].*==", z)])
    check("paket_soll() liest die Python-Fassung aus build_msi.py",
          (soll or {}).get("python") == PY_ERWARTET,
          ((soll or {}).get("python"), PY_ERWARTET))
    # Acht Pins, und die Namen normalisiert: in der Datei steht
    # charset-normalizer, im .dist-info charset_normalizer. Ohne
    # Normalisierung auf BEIDEN Seiten meldete der Server fuer jeden
    # gesunden Host eine Abweichung.
    check("paket_soll() liest alle Pins der Datei",
          len((soll or {}).get("packages", {})) == PINS_ERWARTET,
          (len((soll or {}).get("packages", {})), PINS_ERWARTET))
    check("die Namen sind normalisiert",
          "charset-normalizer" in (soll or {}).get("packages", {}),
          sorted((soll or {}).get("packages", {})))

    leer_wurzel = TMP / "ohne-packaging"
    (leer_wurzel / "agent").mkdir(parents=True, exist_ok=True)
    check("fehlt build_msi.py, ist die Grundlage unbekannt",
          main.paket_soll(leer_wurzel) is None, main.paket_soll(leer_wurzel))

    def wurzel_mit(req_inhalt: str, bm_inhalt: str = "", name: str = "w"):
        """Eine Wurzel nachbauen: echtes build_msi.py, eigene requirements."""
        w = TMP / name
        (w / "packaging").mkdir(parents=True, exist_ok=True)
        (w / "agent").mkdir(parents=True, exist_ok=True)
        if bm_inhalt:
            (w / "packaging" / "build_msi.py").write_text(bm_inhalt,
                                                          encoding="utf-8")
        else:
            shutil.copy(WURZEL / "packaging" / "build_msi.py",
                        w / "packaging" / "build_msi.py")
        (w / "agent" / "requirements.txt").write_text(req_inhalt,
                                                      encoding="utf-8")
        return w

    # lies_pins() wirft bei einer Zeile ohne feste Fassung ein SystemExit -
    # es ist ein Kommandozeilenskript, kein Bibliotheksmodul. SystemExit
    # ist kein Exception, kaeme also an jedem "except Exception" vorbei
    # und riss in Starlette die ganze Hostliste mit: der Hauptbildschirm,
    # im 15-Sekunden-Takt, wegen einer schief geschriebenen Zeile in
    # agent/requirements.txt.
    kaputte_pins = wurzel_mit("requests>=2.0\n", name="pins-kaputt")
    try:
        ergebnis = main.paket_soll(kaputte_pins)
        ausnahme = None
    except BaseException as exc:  # noqa: BLE001
        ergebnis, ausnahme = "AUSNAHME", exc
    check("eine Zeile ohne feste Fassung ergibt None, keine Ausnahme",
          ergebnis is None and ausnahme is None, ausnahme or ergebnis)

    # Und die zweite Haelfte derselben Forderung: PY_VERSION nicht lesbar.
    ohne_py = wurzel_mit("certifi==2026.10.23\n",
                         bm_inhalt="def lies_pins(t):\n    return {}\n",
                         name="ohne-pyversion")
    check("ohne PY_VERSION ist die Grundlage unbekannt",
          main.paket_soll(ohne_py) is None, main.paket_soll(ohne_py))

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

    # Strukturiert, nicht als fertiger Satz: die Oberflaeche ist
    # vollstaendig zweisprachig, und Prosa aus dem Backend stuende im
    # englischen Dialog auf Deutsch. Dasselbe Muster wie bei
    # reboot_reasons, wo der Agent 'kernel' schickt und rebootNote()
    # uebersetzt.
    andere = main.bundled_abweichungen({"certifi": "2026.7.22"},
                                       {"certifi": "2026.10.23"})
    check("eine andere Fassung wird strukturiert gemeldet",
          andere == [{"art": "anders", "name": "certifi",
                      "ist": "2026.7.22", "soll": "2026.10.23"}], andere)
    fehlt = main.bundled_abweichungen({}, {"certifi": "2026.10.23"})
    check("ein fehlendes Paket wird strukturiert gemeldet",
          fehlt == [{"art": "fehlt", "name": "certifi",
                     "ist": None, "soll": "2026.10.23"}], fehlt)
    extra = main.bundled_abweichungen({"neunte": "1.0"}, {})
    check("ein zusaetzliches Paket wird strukturiert gemeldet",
          extra == [{"art": "zusaetzlich", "name": "neunte",
                     "ist": "1.0", "soll": None}], extra)
    check("keine Abweichung traegt fertige Prosa",
          all(set(e) == {"art", "name", "ist", "soll"}
              for e in andere + fehlt + extra), andere + fehlt + extra)

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
              zustand == "abweichend" and diff
              and diff[0] == {"art": "python", "name": "python",
                              "ist": "3.14.7", "soll": "3.14.10"},
              (zustand, diff))

        # Der ganze Weg fuer eine zusaetzliche Bibliothek: Host meldet
        # eine neunte, Zustand abweichend, und sie steht als solche drin.
        neun = dict(ist)
        neun["neunte"] = "1.0"
        zustand, diff = main.bundled_vergleich(
            FakeHost("windows", {"python": "3.14.10", "packages": neun}))
        check("eine neunte Bibliothek macht den Host abweichend",
              zustand == "abweichend"
              and any(e["art"] == "zusaetzlich" and e["name"] == "neunte"
                      for e in diff), (zustand, diff))
    finally:
        main.paket_soll, main.paket_gebaut_aktuell = echt_soll, echt_gebaut

    # --- die Hostliste darf nicht je Host die Dateien lesen ------------
    # paket_gebaut_aktuell() ruft agent_source_version(), und das liest
    # agent/agent.py GANZ (rund 97 KB) und geht sie zeilenweise durch.
    # Die Hostliste wird alle 15 Sekunden geholt, bei laufenden Auftraegen
    # alle 3, je offenem Browser - je Host einmal waere bei 300 Hosts
    # rund 29 MB Dateilesen pro Abruf.
    echt_asv = main.agent_source_version
    zaehler = {"n": 0}

    def gezaehlt():
        zaehler["n"] += 1
        return echt_asv()

    try:
        main.agent_source_version = gezaehlt
        # Mit gefuelltem bundled, sonst steigt bundled_vergleich frueher
        # aus und erreicht die Paketpruefung gar nicht - dann zaehlte die
        # Reihe eine Ersparnis, die nirgends anfaellt. (Genau so hat eine
        # Mutation ueberlebt, bevor diese Zeile stand.)
        with main.Session(main.engine) as s:
            for i in range(5):
                s.add(models.Host(hostname=f"ZAEHL-{i}",
                                  os_type=models.OSType.windows,
                                  agent_token_hash=f"z{i}",
                                  bundled={"python": "3.14.10",
                                           "packages": {"certifi": "1.0"}}))
            s.commit()
        # Kein TestClient hier: dessen Start ruft selbst einmal
        # agent_source_version() und verfaelschte die Zaehlung. Gezaehlt
        # wird genau das, was list_hosts() tut - bundled_vergleich je
        # Host.
        with main.Session(main.engine) as s2:
            for h in s2.exec(main.select(models.Host)).all():
                main.bundled_vergleich(h)
        check("agent_source_version wird nicht je Host gelesen",
              zaehler["n"] <= 1, zaehler["n"])
    finally:
        main.agent_source_version = echt_asv

    # --- der Betreiber muss sehen, wenn gar nicht verglichen wird ------
    # Sind alle Hosts unbekannt, weil die Vergleichsgrundlage fehlt, steht
    # an der Hostzeile planmaessig NICHTS - und "alle aktuell" waere von
    # "Vergleich nicht moeglich" nicht zu unterscheiden. Die
    # Selbstpruefung muss es sagen.
    namen = [c["name"] for c in main.diagnostics()["checks"]]
    check("die Selbstpruefung nennt die Vergleichsgrundlage",
          any("ergleich" in n for n in namen), namen)

    echt_soll2 = main.paket_soll
    try:
        main.paket_soll = lambda *a, **k: None
        zeile = next((c for c in main.diagnostics()["checks"]
                      if "ergleich" in c["name"]), None)
        check("fehlt sie, ist die Zeile rot",
              zeile is not None and zeile["ok"] is False, zeile)
    finally:
        main.paket_soll = echt_soll2

    # --- und es kommt bis in die Ausgabe -------------------------------
    check("HostRead traegt bundled_state",
          "bundled_state" in main.HostRead.model_fields)
    check("HostRead traegt bundled_diff",
          "bundled_diff" in main.HostRead.model_fields)

print("\n--- Teil 5: der Bau schreibt die Laufzeitliste ---")

# Die Liste ist die Vergleichsgrundlage, gegen die der Agent seine eigene
# Laufzeit prueft. Sie entsteht beim Packen unter Linux, verglichen wird
# unter Windows - Pfadtrenner und Schreibweise muessen deshalb schon hier
# vereinheitlicht sein. Ohne das meldet jeder gesunde Host jede Datei als
# fehlend, und das ist der Fehlalarm, der Waechter abschaltet.
BAUM = TMP / "paketbaum" / "python"
CERTIFI = BAUM / "Lib" / "site-packages" / "certifi"
(CERTIFI / "__pycache__").mkdir(parents=True, exist_ok=True)
(BAUM / "python314.dll").write_bytes(b"abc")
(CERTIFI / "cacert.pem").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
(CERTIFI / "__pycache__" / "core.cpython-314.pyc").write_bytes(b"\x00\x01")

if not hasattr(_bm, "laufzeit_liste"):
    # Bewusst kein Zugriff ins Leere: ein AttributeError wuerde die Reihe
    # abbrechen und die Teile 1 bis 4 mit verschlucken.
    check("laufzeit_liste() gibt es im Paketbau", False, "Funktion fehlt")
    liste = {"version": None, "dateien": {}}
else:
    check("laufzeit_liste() gibt es im Paketbau", True)
    liste = _bm.laufzeit_liste(BAUM, "0.52.0")

check("Liste nennt die Paketfassung", liste["version"] == "0.52.0", liste["version"])
check("Pfade mit / und klein geschrieben",
      "python314.dll" in liste["dateien"]
      and "lib/site-packages/certifi/cacert.pem" in liste["dateien"],
      sorted(liste["dateien"]))
check("Groesse und sha256 je Datei",
      liste["dateien"].get("python314.dll", {}).get("groesse") == 3
      and liste["dateien"].get("python314.dll", {}).get("sha256")
          == hashlib.sha256(b"abc").hexdigest(),
      liste["dateien"].get("python314.dll"))
# Beide Pruefungen verlangen ausdruecklich eine nicht leere Liste: sonst
# waeren sie gruen, weil nichts erfasst wurde - gruen aus dem falschen
# Grund, genau die Sorte Pruefung aus dem Befund vom 2026-11-12.
check("__pycache__ bleibt draussen",
      bool(liste["dateien"])
      and not any("__pycache__" in pfad for pfad in liste["dateien"]),
      sorted(liste["dateien"]))
check("die Liste enthaelt sich selbst nicht",
      bool(liste["dateien"])
      and not any("laufzeit.json" in pfad for pfad in liste["dateien"]),
      sorted(liste["dateien"]))

# Dass die Datei auch ins Paket kommt, prueft die Reihe am Quelltext: ein
# vollstaendiger build()-Lauf braucht wixl und Netz. Die eigentliche
# Gegenprobe ist der Bau auf dem Baurechner samt seiner Protokollzeile.
bm_quelle = (WURZEL / "packaging" / "build_msi.py").read_text(encoding="utf-8")
check("der Bau schreibt laufzeit.json", "laufzeit.json" in bm_quelle)
# wixl laesst Eintraege stillschweigend weg - deshalb gibt es
# MUSS_ENTHALTEN. Fehlte laufzeit.json im Paket, meldete JEDER Host
# dauerhaft "unpruefbar", und die Funktion waere lautlos aus.
check("laufzeit.json steht in MUSS_ENTHALTEN",
      "laufzeit.json" in getattr(_bm, "MUSS_ENTHALTEN", ()),
      getattr(_bm, "MUSS_ENTHALTEN", None))
check("und zwar vor add_tree",
      "laufzeit.json" in bm_quelle and "tree = add_tree(work)" in bm_quelle
      and bm_quelle.index("laufzeit.json") < bm_quelle.index("tree = add_tree(work)"))

print("\n--- Teil 6: der Agent prueft seine Laufzeit ---")

import itertools  # noqa: E402

_inst_zaehler = itertools.count(1)
FUELLER = 14  # mehr als die Kappungsgrenze von 10, fuer den Massenfall


def bau(loeschen=None, kuerzen=None, tauschen=None, anhaengen=None, leeren=None,
        ohne_liste=False, liste_text=None, liste_version=None,
        trenner="/", gross=False) -> Path:
    """
    Ein Installationsverzeichnis nachbauen, wie die MSI es hinterlaesst:
    python/ mit Dateien, laufzeit.json daneben.

    Die Liste entsteht ueber laufzeit_liste() aus dem Paketbau und nicht
    von Hand - zwei verschieden gewonnene Listen zu vergleichen waere die
    Sorte Unterschied, die erst beim Kunden auffaellt.
    """
    wurzel = TMP / f"inst{next(_inst_zaehler)}"
    py = wurzel / "python"
    certifi = py / "Lib" / "site-packages" / "certifi"
    certifi.mkdir(parents=True)
    (py / "python314.dll").write_bytes(b"abcdef")
    (certifi / "cacert.pem").write_text(
        "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
        encoding="utf-8")
    for nr in range(FUELLER):
        (py / f"modul{nr}.pyd").write_bytes(b"x" * (nr + 1))

    verzeichnis = _bm.laufzeit_liste(py, liste_version or "0.52.0")

    # Erst die Liste, dann der Schaden - sonst beschreibt die Liste
    # bereits den beschaedigten Stand und es gibt nichts zu finden.
    if loeschen:
        ziel = wurzel / loeschen
        shutil.rmtree(ziel) if ziel.is_dir() else ziel.unlink()
    if kuerzen:
        (wurzel / kuerzen).write_bytes(b"a")
    if tauschen:
        alt = (wurzel / tauschen).read_bytes()
        (wurzel / tauschen).write_bytes(b"z" * len(alt))
    if anhaengen:
        with (wurzel / anhaengen).open("a", encoding="utf-8") as fh:
            fh.write("-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n")
    if leeren:
        (wurzel / leeren).write_text("", encoding="utf-8")

    if trenner != "/":
        verzeichnis["dateien"] = {k.replace("/", trenner): v
                                  for k, v in verzeichnis["dateien"].items()}
    if gross:
        verzeichnis["dateien"] = {k.upper(): v
                                  for k, v in verzeichnis["dateien"].items()}

    if not ohne_liste:
        (wurzel / "laufzeit.json").write_text(
            liste_text if liste_text is not None else json.dumps(verzeichnis),
            encoding="utf-8")
    return wurzel


def pruef(wurzel):
    if not hasattr(agent, "laufzeit_pruefen"):
        return {"zustand": "(fehlt)", "fehlend": [], "abweichend": [], "mehr": 0}
    return agent.laufzeit_pruefen(wurzel)


import json  # noqa: E402

check("laufzeit_pruefen() gibt es im Agenten", hasattr(agent, "laufzeit_pruefen"))
check("vollstaendig ist ok", pruef(bau())["zustand"] == "ok", pruef(bau()))

_fehlt = pruef(bau(loeschen="python/python314.dll"))
check("fehlende Datei ist ein Befund", _fehlt["zustand"] == "unvollstaendig", _fehlt)
check("die fehlende Datei wird benannt",
      "python314.dll" in " ".join(_fehlt["fehlend"]), _fehlt["fehlend"])

check("abgeschnittene Datei ist ein Befund",
      pruef(bau(kuerzen="python/python314.dll"))["zustand"] == "unvollstaendig")
# Gleiche Groesse, anderer Inhalt: das ist der Fall, den ein Vergleich
# ohne Pruefsumme nicht sieht.
check("veraenderte Datei bei gleicher Groesse ist ein Befund",
      pruef(bau(tauschen="python/python314.dll"))["zustand"] == "unvollstaendig")

check("fehlende Liste ist unpruefbar",
      pruef(bau(ohne_liste=True))["zustand"] == "unpruefbar")
check("kaputte Liste ist unpruefbar",
      pruef(bau(liste_text="{kein json"))["zustand"] == "unpruefbar")
check("Liste ohne dateien-Schluessel ist unpruefbar",
      pruef(bau(liste_text='{"version": "0.52.0"}'))["zustand"] == "unpruefbar")

# Der Normalfall nach einer Selbstaktualisierung: neuer Agent, alte
# Liste. Das ist KEIN Befund - dafuer gibt es den Hinweis auf die
# Paketfassung seit 0.51.0.
check("aeltere Liste nach Selbstaktualisierung bleibt ok",
      pruef(bau(liste_version="0.40.0"))["zustand"] == "ok")

# Eine interne CA an das Buendel zu haengen ist ein dokumentierter
# Eingriff (REVERSE-PROXY.md). Ein Dauerbefund dafuer waere der
# Fehlalarm, der Waechter abschaltet.
check("cacert.pem mit angehaengter CA bleibt ok",
      pruef(bau(anhaengen="python/Lib/site-packages/certifi/cacert.pem"))["zustand"]
      == "ok")
check("fehlendes cacert.pem ist ein Befund",
      pruef(bau(loeschen="python/Lib/site-packages/certifi/cacert.pem"))["zustand"]
      == "unvollstaendig")
check("leeres cacert.pem ist ein Befund",
      pruef(bau(leeren="python/Lib/site-packages/certifi/cacert.pem"))["zustand"]
      == "unvollstaendig")

# Die Liste entsteht unter Linux, verglichen wird unter Windows: ohne
# Vereinheitlichung meldete jeder gesunde Host jede Datei als fehlend.
check("Pfadtrenner der Liste spielen keine Rolle",
      pruef(bau(trenner="\\"))["zustand"] == "ok",
      pruef(bau(trenner="\\")))
check("Grossschreibung in der Liste spielt keine Rolle",
      pruef(bau(gross=True))["zustand"] == "ok", pruef(bau(gross=True)))

_viele = pruef(bau(loeschen="python"))
check("viele Treffer werden gekappt",
      len(_viele["fehlend"]) <= 10 and _viele["mehr"] > 0,
      (len(_viele["fehlend"]), _viele["mehr"]))

# Unter Linux liefert die Pruefung ohne Parameter None, und der Heartbeat
# sendet dann nichts: sonst stuende bei jedem Linux-Host "unpruefbar".
check("ohne Parameter meldet ein Nicht-Windows-Agent nichts",
      not hasattr(agent, "laufzeit_pruefen") and False
      or (agent.laufzeit_pruefen() is None if (hasattr(agent, "laufzeit_pruefen")
                                               and not agent.IS_WINDOWS) else
          hasattr(agent, "laufzeit_pruefen")))

# Frueher stand hier ein Zugriff auf die Modulkonstante BUNDLED. Die
# gibt es seit 0.51.4 nicht mehr: unter Windows waere die ganze Reihe an
# dieser Zeile abgebrochen, unter Linux war sie durch das vorangestellte
# "not IS_WINDOWS or" ohnehin inhaltsleer. Pruefungen dieser Form
# verrotten still - den Zusammenhang prueft jetzt Teil 9.
check("der gemeldete Stand traegt den Laufzeitbefund",
      not agent.IS_WINDOWS
      or "laufzeit" in (agent.stand_melden() or {}))
check("der Heartbeat traegt das Feld laufzeit nur mit Befund",
      '"laufzeit"' in agent_quelle)

print("\n--- Teil 7: Rueckfall auf den Zertifikatsspeicher des Systems ---")

# Der belegte Fall vom 2026-11-18: cacert.pem fehlt, der TLS-Aufbau
# scheitert, und der Agent kommt mit seinem Befund nicht zum Server. Eine
# Selbstpruefung, deren Meldung genau dann nicht ankommt, ist wirkungslos.
#
# Geprueft ohne Netz: requests im Agenten wird durch eine Attrappe
# ersetzt, die beim ersten Aufruf SSLError wirft.
_echte_requests = agent.requests


class _Antwort:
    status_code = 200
    content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class _SitzungAttrappe:
    def __init__(self, ziel):
        self.ziel = ziel
        self.gemountet = []

    def mount(self, prefix, adapter):
        self.gemountet.append((prefix, adapter))

    def request(self, method, url, **kw):
        return self.ziel.request(method, url, **kw)


class _ReqAttrappe:
    exceptions = _echte_requests.exceptions
    adapters = _echte_requests.adapters

    def __init__(self, fehler_bei=(1,), art=None):
        self.aufrufe = 0
        self.alle = []
        self.sitzungen = []
        self.fehler_bei = set(fehler_bei)
        # Welche Ausnahme geworfen wird, ist der Kern dieser Pruefung:
        # requests wirft NICHT immer SSLError. Siehe unten.
        self.art = art or (lambda: _echte_requests.exceptions.SSLError(
            "Buendel unbrauchbar"))

    def request(self, method, url, **kw):
        self.aufrufe += 1
        self.alle.append({"method": method, "url": url, "kwargs": kw})
        if self.aufrufe in self.fehler_bei:
            raise self.art()
        return _Antwort()

    def Session(self):
        s = _SitzungAttrappe(self)
        self.sitzungen.append(s)
        return s


def mit_attrappe(fehler_bei, art=None):
    """Einen api()-Aufruf gegen die Attrappe fahren. Gibt (Attrappe, Fehler) zurueck."""
    attrappe = _ReqAttrappe(fehler_bei, art)
    agent.requests = attrappe
    agent.RUECKFALL_AKTIV = False
    fehler = None
    try:
        agent.api("GET", "/api/v1/ping", auth=False)
    except Exception as exc:  # noqa: BLE001
        fehler = exc
    finally:
        agent.requests = _echte_requests
    return attrappe, fehler


if not hasattr(agent, "RUECKFALL_AKTIV"):
    check("der Agent kennt einen Rueckfall", False, "RUECKFALL_AKTIV fehlt")
else:
    check("der Agent kennt einen Rueckfall", True)

    a1, f1 = mit_attrappe((1,))
    check("nach SSLError wird genau einmal wiederholt", a1.aufrufe == 2, a1.aufrufe)
    check("die Wiederholung laeuft ueber eine eigene Sitzung",
          len(a1.sitzungen) == 1 and any(p.startswith("https")
                                         for p, _ in a1.sitzungen[0].gemountet),
          [s.gemountet for s in a1.sitzungen])
    # Die Kernbedingung: der Rueckfall darf die Pruefung nicht abschwaechen.
    check("die Wiederholung prueft weiterhin",
          all(auf["kwargs"].get("verify") is not False for auf in a1.alle),
          [auf["kwargs"].get("verify") for auf in a1.alle])
    check("der Rueckfall wird vermerkt", agent.RUECKFALL_AKTIV is True)
    check("der erste Aufruf war erfolgreich beantwortet", f1 is None, f1)

    a2, f2 = mit_attrappe(())
    check("ohne SSLError kein Rueckfall",
          agent.RUECKFALL_AKTIV is False and a2.aufrufe == 1,
          (agent.RUECKFALL_AKTIV, a2.aufrufe))

    a3, f3 = mit_attrappe((1, 2))
    check("ein zweiter SSLError wird durchgelassen",
          isinstance(f3, _echte_requests.exceptions.SSLError), f3)
    check("und nicht ein drittes Mal versucht", a3.aufrufe == 2, a3.aufrufe)

# ----------------------------------------------------------------------
# Der Fall, den die Attrappe oben NICHT abdeckt - und der eigentliche
# Schadensfall vom 2026-11-18.
#
# requests prueft die Existenz des Zertifikatsbuendels in
# HTTPAdapter.cert_verify(), VOR jedem Verbindungsaufbau und ausserhalb
# seiner Ausnahmeumwandlung. FEHLT die Datei, wirft es ein nacktes
# OSError - keine SSLError. Eine Pruefung, die nur SSLError wirft, setzt
# damit voraus, was zu zeigen war.
#
# Deshalb hier zuerst eine Festschreibung der Bibliothek selbst: ohne
# Netz, ohne Server, nur ueber einen Pfad, der nicht existiert.
try:
    _echte_requests.request("GET", "https://127.0.0.1:1/x",
                            verify="/nicht/vorhanden.pem", timeout=1)
    _bibliothek = None
except Exception as exc:  # noqa: BLE001
    _bibliothek = exc
check("fehlendes Buendel ergibt OSError, nicht SSLError",
      isinstance(_bibliothek, OSError)
      and not isinstance(_bibliothek, _echte_requests.exceptions.SSLError),
      type(_bibliothek).__name__)
check("und auch keine RequestException",
      not isinstance(_bibliothek, _echte_requests.exceptions.RequestException),
      type(_bibliothek).__name__)

if hasattr(agent, "RUECKFALL_AKTIV"):
    _fehlt_buendel = lambda: OSError(  # noqa: E731
        "Could not find a suitable TLS CA certificate bundle, invalid path: "
        "C:\\Program Files\\CO37\\python\\Lib\\site-packages\\certifi\\cacert.pem")
    a4, f4 = mit_attrappe((1,), _fehlt_buendel)
    check("auch bei fehlendem Buendel wird wiederholt", a4.aufrufe == 2, a4.aufrufe)
    check("und der Rueckfall wird vermerkt", agent.RUECKFALL_AKTIV is True)
    check("der Aufruf kommt durch", f4 is None, f4)

    # Gegenprobe, damit der Rueckfall nicht bei JEDEM Fehler greift:
    # requests.exceptions.RequestException erbt selbst von OSError, ein
    # Verbindungsfehler ist also auch ein OSError. Wiederholt wuerde er
    # nur eine zweite, genauso aussichtslose Verbindung kosten - und
    # jeden Host, der offline ist, als "ueber den Systemspeicher
    # gemeldet" ausweisen.
    a5, f5 = mit_attrappe((1, 2), lambda: _echte_requests.exceptions.ConnectionError(
        "Verbindung abgelehnt"))
    check("ein Verbindungsfehler loest keinen Rueckfall aus",
          a5.aufrufe == 1 and agent.RUECKFALL_AKTIV is False,
          (a5.aufrufe, agent.RUECKFALL_AKTIV))
    check("und fliegt unveraendert weiter",
          isinstance(f5, _echte_requests.exceptions.ConnectionError), f5)

# Der Agent muss sich ohne Warnung uebersetzen lassen. Ungueltige
# Escape-Sequenzen sind seit Python 3.12 eine Warnung und sollen
# SyntaxError werden - dann laedt agent.py nicht mehr. Unter Windows
# laeuft er als pythonw.exe ohne Konsole; die Warnung waere dort
# unsichtbar und die spaetere Fehlermeldung ebenso.
import warnings  # noqa: E402

with warnings.catch_warnings(record=True) as _warnungen:
    warnings.simplefilter("always")
    compile(agent_quelle, "agent.py", "exec")
_syntax = [f"Zeile {w.lineno}" for w in _warnungen
           if issubclass(w.category, SyntaxWarning)]
check("agent.py uebersetzt ohne SyntaxWarning", not _syntax, _syntax)

check("verify=False steht nirgends im Agenten",
      "verify=False" not in agent_quelle and "verify = False" not in agent_quelle)
# Der Rueckfall greift erst beim Senden, also traegt ihn der naechste
# Heartbeat - geprueft am Quelltext, weil der Heartbeat ein Backend und
# eine Anmeldung braucht. Die Wirkung ueber HTTP prueft Teil 3.
check("der Heartbeat vermerkt einen Rueckfall",
      '"rueckfall"' in agent_quelle)

print("\n--- Teil 8: das Backend urteilt nicht selbst, es prueft Grenzen ---")


def host_mit(laufzeit, windows=True):
    """Ein Host-Doppel. Mehr braucht laufzeit_zustand() nicht."""
    bundled = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}
    if laufzeit is not None:
        bundled["laufzeit"] = laufzeit
    return type("HostDoppel", (), {
        "os_type": main.OSType.windows if windows else main.OSType.linux,
        "bundled": bundled})()


GUT = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}

check("laufzeit_zustand() gibt es", hasattr(main, "laufzeit_zustand"))

if hasattr(main, "laufzeit_zustand"):
    befund = {"zustand": "unvollstaendig",
              "fehlend": ["lib/site-packages/certifi/cacert.pem"],
              "abweichend": ["python314.dll"], "mehr": 3, "liste": "0.51.2"}
    zustand, eintraege = main.laufzeit_zustand(host_mit(befund))
    check("unvollstaendig kommt durch", zustand == "unvollstaendig", zustand)
    check("fehlende und abweichende Dateien werden benannt",
          any(e["art"] == "fehlt" and "cacert.pem" in e["name"] for e in eintraege)
          and any(e["art"] == "anders" and e["name"] == "python314.dll"
                  for e in eintraege), eintraege)
    check("die gekappten Treffer tauchen als eigener Eintrag auf",
          any(e["art"] == "mehr" for e in eintraege), eintraege)
    check("ok ergibt keinen Eintrag",
          main.laufzeit_zustand(host_mit({"zustand": "ok", "fehlend": [],
                                          "abweichend": [], "mehr": 0,
                                          "liste": "0.51.2"})) == ("ok", []))
    check("unpruefbar bleibt unpruefbar",
          main.laufzeit_zustand(host_mit({"zustand": "unpruefbar", "fehlend": [],
                                          "abweichend": [], "mehr": 0,
                                          "liste": ""}))[0] == "unpruefbar")
    check("ohne Feld ist der Zustand unbekannt",
          main.laufzeit_zustand(host_mit(None))[0] == "unbekannt")
    # Der Rueckfall auf den Zertifikatsspeicher des Systems gehoert in die
    # Einzelheiten, aber er ist KEIN Befund: der Zustand bleibt, was er
    # ist, und die Hostzeile bekommt davon keinen Hinweis. Sonst stuende
    # ein Dauerhinweis bei jeder Anlage mit eigener CA.
    _ok_rueckfall = {"zustand": "ok", "fehlend": [], "abweichend": [],
                     "mehr": 0, "liste": "0.51.2", "rueckfall": True}
    check("der Rueckfall erscheint als Eintrag",
          main.laufzeit_zustand(host_mit(_ok_rueckfall))
          == ("ok", [{"art": "rueckfall", "name": ""}]),
          main.laufzeit_zustand(host_mit(_ok_rueckfall)))
    check("ein Linux-Host bleibt unbekannt",
          main.laufzeit_zustand(host_mit(befund, windows=False))[0] == "unbekannt")

    # Der Befund ist host-lokale Wahrheit: er braucht KEINE
    # Vergleichsgrundlage auf dem Server. Sonst waere er auf jedem Server
    # ohne frisch gebaute MSI unsichtbar - und das sind genau die Server,
    # auf denen etwas im Argen ist.
    echt_soll = main.paket_soll
    try:
        main.paket_soll = lambda *a, **k: None
        check("der Zustand haengt nicht an paket_soll()",
              main.laufzeit_zustand(host_mit(befund))[0] == "unvollstaendig")
    finally:
        main.paket_soll = echt_soll

if hasattr(main, "bundled_sauber"):
    laufzeit_gut = {"zustand": "unvollstaendig", "fehlend": ["a"],
                    "abweichend": [], "mehr": 0, "liste": "0.51.2",
                    "rueckfall": False}
    check("ein gueltiger Befund kommt durch bundled_sauber()",
          main.bundled_sauber({**GUT, "laufzeit": laufzeit_gut})
          .get("laufzeit") == laufzeit_gut,
          main.bundled_sauber({**GUT, "laufzeit": laufzeit_gut}))
    check("ohne Befund bleibt das Feld weg",
          main.bundled_sauber(GUT) == GUT, main.bundled_sauber(GUT))
    krumm = {
        "unbekannter Zustand": {"zustand": "halbgut"},
        "fehlend keine Liste": {"zustand": "ok", "fehlend": "a"},
        "Eintrag keine Zeichenkette": {"zustand": "ok", "fehlend": [7]},
        "Eintrag zu lang": {"zustand": "ok", "fehlend": ["x" * 300]},
        "zu viele Eintraege": {"zustand": "ok",
                               "fehlend": [f"d{i}" for i in range(11)]},
        "mehr als Zeichenkette": {"zustand": "ok", "mehr": "viele"},
        "rueckfall als Zeichenkette": {"zustand": "ok", "rueckfall": "ja"},
        "Liste statt Verzeichnis": ["ok"],
        "Zeichenkette": "ok",
    }
    for was, wert in krumm.items():
        ergebnis = main.bundled_sauber({**GUT, "laufzeit": wert})
        check(f"verworfen, aber nur die Laufzeit: {was}",
              "laufzeit" not in ergebnis
              and ergebnis.get("packages") == GUT["packages"], ergebnis)
    # Die Grenze selbst, nicht nur ein Stueck weit daneben.
    check("genau zehn Eintraege sind erlaubt",
          "laufzeit" in main.bundled_sauber(
              {**GUT, "laufzeit": {"zustand": "unvollstaendig",
                                   "fehlend": [f"d{i}" for i in range(10)]}}))

check("HostRead traegt laufzeit_state", "laufzeit_state" in main.HostRead.model_fields)
check("HostRead traegt laufzeit_diff", "laufzeit_diff" in main.HostRead.model_fields)
check("die Hostliste fuellt die beiden Felder",
      "laufzeit_state" in (WURZEL / "backend" / "main.py").read_text(encoding="utf-8"))
check("die Diagnoseseite nennt die Laufzeitpruefung",
      any("aufzeit" in c["name"] and "Vergleichsgrundlage" not in c["name"]
          for c in main.diagnostics()["checks"]),
      [c["name"] for c in main.diagnostics()["checks"]])

print("\n--- Teil 9: der gemeldete Stand altert nicht mit dem Prozess ---")

# Anlass, am 2026-11-19 im Feld gemessen: ein Agent, der sich selbst
# aktualisiert hat, haengt nicht mehr am Aufgabenplaner, und die
# Einzelinstanz-Sperre haelt jeden geplanten Start fern. Der Prozess
# laeuft damit bis zum naechsten Systemneustart durch - und meldete den
# Paketstand, den er beim Start gelesen hatte. Nach einer
# MSI-Installation blieb der Hinweis "Agent muss neu installiert werden"
# deshalb dauerhaft stehen, obwohl auf der Platte alles stimmte.


class Zaehler:
    """Eine Attrappe, die mitzaehlt und einen vorgebbaren Wert liefert."""

    def __init__(self, wert):
        self.n = 0
        self.wert = wert

    def __call__(self, *a, **k):
        self.n += 1
        return self.wert() if callable(self.wert) else self.wert


def mit_stand(pakete_wert, lauf_wert):
    """bundled_stand und laufzeit_pruefen durch Zaehler ersetzen."""
    pakete = Zaehler(pakete_wert)
    lauf = Zaehler(lauf_wert)
    agent.bundled_stand = pakete
    agent.laufzeit_pruefen = lauf
    agent._STAND_PAKETE = None
    agent._STAND_LAUFZEIT = None
    agent._STAND_GEPRUEFT = None
    agent._STAND_ZUSTAND = None
    return pakete, lauf


_echt_bundled, _echt_lauf = agent.bundled_stand, agent.laufzeit_pruefen
ALT = {"python": "3.14.7", "packages": {"certifi": "2026.7.22"}}
NEU = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}
OK = {"zustand": "ok", "fehlend": [], "abweichend": [], "mehr": 0,
      "liste": "0.51.3"}

check("stand_melden() gibt es", hasattr(agent, "stand_melden"))

if hasattr(agent, "stand_melden"):
    try:
        # --- Takt: teure Pruefung nicht bei jedem Heartbeat ------------
        pakete, lauf = mit_stand(lambda: dict(ALT), lambda *a, **k: dict(OK))
        erst = agent.stand_melden(jetzt=1000.0)
        check("erster Aufruf liest beides genau einmal",
              (pakete.n, lauf.n) == (1, 1), (pakete.n, lauf.n))
        check("und meldet den gelesenen Stand",
              erst["python"] == "3.14.7" and "laufzeit" in erst, erst)

        agent.stand_melden(jetzt=1060.0)
        check("zweiter Aufruf liest den Paketstand neu", pakete.n == 2, pakete.n)
        check("aber nicht die Dateien", lauf.n == 1, lauf.n)

        # --- Aenderung schlaegt den Takt ------------------------------
        pakete.wert = lambda: dict(NEU)
        zweit = agent.stand_melden(jetzt=1120.0)
        check("ein geaenderter Paketstand erzwingt die Dateipruefung",
              lauf.n == 2, lauf.n)
        check("und der gemeldete Stand ist der neue",
              zweit["python"] == "3.14.10", zweit)

        # --- nach dem Takt wieder --------------------------------------
        agent.stand_melden(jetzt=1120.0 + agent.LAUFZEIT_TAKT)
        check("nach dem Takt wird wieder geprueft", lauf.n == 3, lauf.n)

        # --- Lesefehler darf den Heartbeat nicht beenden ---------------
        def kaputt(*a, **k):
            raise OSError("site-packages weg")

        agent.bundled_stand = kaputt
        agent._STAND_PAKETE = None
        check("ein Lesefehler beendet den Heartbeat nicht",
              agent.stand_melden(jetzt=2000.0) is None)

        # --- Protokollzeile nur beim Wechsel ---------------------------
        kaputt_lauf = {"zustand": "unvollstaendig",
                       "fehlend": ["lib/site-packages/certifi/cacert.pem"],
                       "abweichend": [], "mehr": 0, "liste": "0.51.3"}
        pakete, lauf = mit_stand(lambda: dict(NEU), lambda *a, **k: dict(kaputt_lauf))
        gesammelt = []
        echt_log = agent.log
        agent.log = lambda nachricht, err=False: gesammelt.append(nachricht)
        try:
            agent.stand_melden(jetzt=3000.0)
            agent.stand_melden(jetzt=3060.0)
            agent.stand_melden(jetzt=3120.0)
        finally:
            agent.log = echt_log
        zeilen = [z for z in gesammelt if "unvollstaendig" in z]
        check("die Klartextzeile erscheint nur beim Wechsel",
              len(zeilen) == 1, len(zeilen))
    finally:
        agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf

# Der Takt haengt an einer monotonen Uhr: mit time.time() verschoebe eine
# NTP-Korrektur die naechste Pruefung beliebig weit, in beide Richtungen.
if hasattr(agent, "stand_melden"):
    try:
        # --- Important 2 und 3: nach einem Befund kuerzer takten -------
        #
        # Eine Reparatur mit msiexec /fa schreibt DIESELBEN Fassungen
        # zurueck - der Paketstand aendert sich also nicht, und der lange
        # Takt liesse den Hinweis bis zu eine Stunde stehen, obwohl auf
        # der Platte alles stimmt. Genau die Beschwerde, die diese
        # Aenderung beheben soll. Dasselbe gilt fuer einen Fehlalarm, der
        # mitten in einer laufenden Installation entsteht.
        kaputt = {"zustand": "unvollstaendig", "fehlend": ["x"],
                  "abweichend": [], "mehr": 0, "liste": "0.51.4"}
        pakete, lauf = mit_stand(lambda: dict(NEU), lambda *a, **k: dict(kaputt))
        agent.stand_melden(jetzt=5000.0)
        check("nach einem Befund wird frueh wieder geprueft",
              agent.stand_melden(jetzt=5000.0 + agent.LAUFZEIT_TAKT_KURZ)
              is not None and lauf.n == 2, lauf.n)
        check("und der kurze Takt ist deutlich kuerzer als der lange",
              agent.LAUFZEIT_TAKT_KURZ < agent.LAUFZEIT_TAKT / 10,
              (agent.LAUFZEIT_TAKT_KURZ, agent.LAUFZEIT_TAKT))

        # Repariert, ohne dass sich der Paketstand aendert: der Befund
        # muss trotzdem von selbst auf ok gehen.
        lauf.wert = lambda *a, **k: dict(OK)
        geheilt = agent.stand_melden(jetzt=5000.0 + 2 * agent.LAUFZEIT_TAKT_KURZ)
        check("eine Reparatur ohne Paketwechsel wird bemerkt",
              geheilt["laufzeit"]["zustand"] == "ok", geheilt)
        check("danach gilt wieder der lange Takt",
              agent.stand_melden(jetzt=5000.0 + 3 * agent.LAUFZEIT_TAKT_KURZ)
              is not None and lauf.n == 3, lauf.n)

        # --- Important 5: Ausnahme in der Dateipruefung ---------------
        def lauf_kaputt(*a, **k):
            raise OSError("python-Verzeichnis weg")

        pakete, lauf = mit_stand(lambda: dict(NEU), None)
        agent.laufzeit_pruefen = lauf_kaputt
        ergebnis = agent.stand_melden(jetzt=6000.0)
        check("eine Ausnahme in der Dateipruefung kostet nur das Feld",
              ergebnis is not None and "laufzeit" not in ergebnis, ergebnis)
        gezaehlt = Zaehler(dict(OK))
        agent.laufzeit_pruefen = gezaehlt
        agent.stand_melden(jetzt=6000.0 + agent.LAUFZEIT_TAKT_KURZ)
        check("und wird frueh erneut versucht", gezaehlt.n == 1, gezaehlt.n)

        # --- Important 4: Heilung, dann erneuter Schaden --------------
        pakete, lauf = mit_stand(lambda: dict(NEU), lambda *a, **k: dict(kaputt))
        gesammelt2 = []
        echt_log2 = agent.log
        agent.log = lambda nachricht, err=False: gesammelt2.append(nachricht)
        try:
            agent.stand_melden(jetzt=7000.0)
            lauf.wert = lambda *a, **k: dict(OK)
            agent.stand_melden(jetzt=7000.0 + agent.LAUFZEIT_TAKT_KURZ)
            lauf.wert = lambda *a, **k: dict(kaputt)
            # Nach der Heilung gilt wieder der LANGE Takt - der erneute
            # Schaden faellt also erst dann auf. Genau so ist es gemeint:
            # haeufig nachsehen, solange etwas nicht stimmt, sparsam,
            # wenn alles in Ordnung ist.
            agent.stand_melden(jetzt=7000.0 + agent.LAUFZEIT_TAKT_KURZ
                               + agent.LAUFZEIT_TAKT)
        finally:
            agent.log = echt_log2
        check("nach Heilung und erneutem Schaden kommt die Zeile wieder",
              len([z for z in gesammelt2 if "unvollstaendig" in z]) == 2,
              len([z for z in gesammelt2 if "unvollstaendig" in z]))

        # --- Minor 6 hochgestuft: Linux meldet nichts -----------------
        # Mit den ECHTEN Funktionen, sonst prueft man die Attrappe.
        agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf
        agent._STAND_PAKETE = None
        agent._STAND_GEPRUEFT = None
        check("ohne Windows liefert stand_melden() None",
              agent.stand_melden() is None if not agent.IS_WINDOWS else True,
              agent.stand_melden())
    finally:
        agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf

# Gegen den Bytecode der FUNKTION, nicht gegen den Dateiinhalt:
# time.monotonic steht in agent.py schon an anderer Stelle, und eine
# Pruefung am Text waere hier gruen, ohne etwas gesehen zu haben. Wird
# monotonic durch time.time ersetzt, verschwindet der Name hier.
check("der Takt haengt an einer monotonen Uhr",
      hasattr(agent, "stand_melden")
      and "monotonic" in agent.stand_melden.__code__.co_names,
      getattr(agent, "stand_melden", None)
      and agent.stand_melden.__code__.co_names)

# Und der Heartbeat holt den Stand bei jedem Durchlauf, statt eine
# Modulkonstante zu senden.
check("der Heartbeat ruft stand_melden()", "stand_melden(" in agent_quelle)
check("es gibt keine Modulkonstante BUNDLED mehr",
      "\nBUNDLED = " not in agent_quelle)

_echt_lauft = getattr(agent, "LAUFT", None)
_echt_inst_py = getattr(agent, "installierte_python", None)

print("\n--- Teil 10: was laeuft gegen was installiert ist ---")

# Anlass, am 2026-11-19 im Feld gemessen: nach einer MSI-Installation
# meldete ein Host weiter "Paketupdate - Agent muss neu installiert
# werden", obwohl Platte, Paketfassung und Agent-Code stimmten. In der
# Detailansicht stand der Grund: "Python: 3.14.7, package has 3.14.10".
#
# bundled_stand() liest zwei Dinge aus zwei Quellen - die Bibliotheken
# von der PLATTE, die Python-Fassung aus dem LAUFENDEN PROZESS. Die
# zweite laesst sich durch erneutes Lesen nicht aktualisieren: ein
# Prozess kann seine eigene Interpreter-DLL nicht tauschen.

check("LAUFT ist die Momentaufnahme vom Prozessstart",
      hasattr(agent, "LAUFT")
      and (agent.LAUFT == agent.bundled_stand() if agent.IS_WINDOWS
           else agent.LAUFT is None),
      getattr(agent, "LAUFT", "fehlt"))

check("installierte_python() gibt es", hasattr(agent, "installierte_python"))

if hasattr(agent, "installierte_python"):
    # Ohne Windows und ohne python.exe im Pfad: nicht ermittelbar, und
    # dann wird das Feld weggelassen statt geraten.
    check("ohne python.exe liefert sie None",
          agent.installierte_python(TMP / "gibtsnicht") is None,
          agent.installierte_python(TMP / "gibtsnicht"))

    # Eine Attrappe, die sich wie python.exe -V verhaelt.
    PYDIR = TMP / "pyattrappe" / "python"
    PYDIR.mkdir(parents=True, exist_ok=True)
    attrappe = PYDIR / ("python.exe" if agent.IS_WINDOWS else "python")
    attrappe.write_text("#!/bin/sh\necho 'Python 3.14.10'\n", encoding="utf-8")
    attrappe.chmod(0o755)
    check("sie liest die Fassung aus der Ausgabe",
          agent.installierte_python(TMP / "pyattrappe") == "3.14.10",
          agent.installierte_python(TMP / "pyattrappe"))

if hasattr(agent, "stand_melden") and hasattr(agent, "LAUFT"):
    try:
        # Platte sagt 3.14.10, der Prozess faehrt 3.14.7.
        # Die Platte meldet ueber bundled_stand() die Zahl des LAUFENDEN
        # Interpreters (3.14.7) - genau das war der Fehler. Die
        # installierte Fassung muss sie ueberschreiben, und das ist nur
        # unterscheidbar, wenn beide Zahlen verschieden sind.
        pakete, lauf = mit_stand(lambda: dict(ALT), lambda *a, **k: dict(OK))
        agent.LAUFT = {"python": "3.14.7",
                       "packages": {"certifi": "2026.7.22"}}
        agent.installierte_python = lambda *a, **k: "3.14.10"
        stand = agent.stand_melden(jetzt=100.0)
        check("der gemeldete Stand traegt laeuft", "laeuft" in stand, stand)
        check("laeuft bleibt der Stand des Prozesses",
              stand["laeuft"]["python"] == "3.14.7", stand["laeuft"])
        check("python ist die INSTALLIERTE Fassung",
              stand["python"] == "3.14.10", stand["python"])

        # Ist die installierte Fassung nicht zu ermitteln, bleibt es bei
        # der laufenden - lieber die alte Wahrheit als eine geratene.
        # Mit ALT auf der Platte, damit die beiden Zahlen sich
        # unterscheiden - sonst waere die Pruefung gruen, ohne etwas zu
        # unterscheiden.
        pakete.wert = lambda: dict(ALT)
        agent.installierte_python = lambda *a, **k: None
        agent._STAND_GEPRUEFT = None
        agent._STAND_PY_PLATTE = None
        stand2 = agent.stand_melden(jetzt=200.0)
        check("ohne installierte Fassung gilt die laufende",
              stand2["python"] == "3.14.7" and ALT["python"] != "3.14.10",
              stand2["python"])

        agent.LAUFT = None
        agent._STAND_GEPRUEFT = None
        check("ohne Momentaufnahme kein Feld",
              "laeuft" not in agent.stand_melden(jetzt=300.0))
    finally:
        agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf
        agent.LAUFT = _echt_lauft
        agent.installierte_python = _echt_inst_py

print("\n--- Teil 11: die sechs Sicherungen vor dem Selbstneustart ---")

# Ein Agent, der sich beendet und nicht wiederkommt, ist schlimmer als
# ein falscher Hinweis: der Host geht dunkel, und sichtbar ist nur "seit
# X Minuten nicht gesehen". Jede der sechs Sicherungen hat hier ihre
# eigene Pruefung, und jede ist einzeln abschaltbar gemacht.

GLEICH = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"},
          "laeuft": {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}},
          "laufzeit": dict(OK)}
ABW = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"},
       "laeuft": {"python": "3.14.7", "packages": {"certifi": "2026.7.22"}},
       "laufzeit": dict(OK)}


ERLAUBT = {"agent_laufzeit_neustart": True}


def grund(stand, antwort=None, versucht=None, rueckweg=True, zweimal=True):
    """
    Einmal durch neustart_faellig(). zweimal=True heisst: die Abweichung
    gilt als beim vorigen Heartbeat schon gesehen (Sicherung 6).

    Die Erlaubnis muss ausdruecklich in der Antwort stehen - ein
    Schweigen des Servers ist KEINE Zustimmung.
    """
    if not hasattr(agent, "neustart_faellig"):
        return "(fehlt)"
    echt = agent.rueckweg_bestaetigt
    agent.rueckweg_bestaetigt = lambda: rueckweg
    agent._NEUSTART_GESEHEN = None
    antwort = dict(ERLAUBT, **(antwort or {}))
    try:
        if zweimal:
            agent.neustart_faellig(stand, antwort, versucht)
        return agent.neustart_faellig(stand, antwort, versucht)[1]
    finally:
        agent.rueckweg_bestaetigt = echt


check("neustart_faellig() gibt es", hasattr(agent, "neustart_faellig"))
check("rueckweg_bestaetigt() gibt es", hasattr(agent, "rueckweg_bestaetigt"))

if hasattr(agent, "neustart_faellig"):
    check("gleicher Stand: kein Neustart", grund(GLEICH) == "gleich",
          grund(GLEICH))
    # Sicherung 5
    check("Schalter aus: kein Neustart",
          grund(ABW, {"agent_laufzeit_neustart": False}) == "aus",
          grund(ABW, {"agent_laufzeit_neustart": False}))
    # Sicherung 1
    check("Auftrag in der Antwort: kein Neustart",
          grund(ABW, {"jobs": [{"id": 1}]}) == "auftrag",
          grund(ABW, {"jobs": [{"id": 1}]}))
    # Sicherung 6, erste Haelfte
    for zustand in ("unvollstaendig", "unpruefbar"):
        st = {**ABW, "laufzeit": {**OK, "zustand": zustand}}
        check(f"Laufzeit {zustand}: kein Neustart", grund(st) == "laufzeit",
              grund(st))
    # Sicherung 6, zweite Haelfte: erst beim zweiten Sehen
    check("beim ersten Sehen wird gewartet",
          grund(ABW, zweimal=False) == "warten", grund(ABW, zweimal=False))
    # Sicherung 2
    ziel = agent._neustart_ziel(ABW)
    check("Ziel schon erfolglos versucht: kein zweiter Neustart",
          grund(ABW, versucht=ziel) == "zweiter", grund(ABW, versucht=ziel))
    check("ein ANDERES Ziel ist kein verbrauchter Versuch",
          grund(ABW, versucht="deadbeefdeadbeef") == "ok",
          grund(ABW, versucht="deadbeefdeadbeef"))
    # Sicherung 3
    check("Rueckweg nicht bestaetigt: kein Neustart",
          grund(ABW, rueckweg=False) == "rueckweg", grund(ABW, rueckweg=False))
    # alles erfuellt
    check("alles erfuellt: Neustart", grund(ABW) == "ok", grund(ABW))

    # I2: ein Schweigen des Servers ist keine Zustimmung. Ein aelteres
    # Backend kennt den Schluessel nicht und schickt ihn nicht - dort
    # darf sich kein Agent von selbst beenden, und es gibt dort auch
    # keinen Schalter, mit dem man es verhindern koennte.
    check("ohne ausdrueckliche Erlaubnis kein Neustart",
          grund(ABW, {"agent_laufzeit_neustart": None}) == "aus",
          grund(ABW, {"agent_laufzeit_neustart": None}))

    # I1: der zweite Blick muss zweimal MESSEN, nicht zweimal dieselbe
    # Messung auswerten. Dafuer wird der Takt zurueckgesetzt.
    agent._NEUSTART_GESEHEN = None
    agent._STAND_GEPRUEFT = 12345.0
    agent.neustart_faellig(ABW, dict(ERLAUBT), None)
    check("nach 'warten' wird neu gemessen", agent._STAND_GEPRUEFT is None,
          agent._STAND_GEPRUEFT)

    # I2: ist das HANDELN schon einmal misslungen, muss das einen
    # eigenen Grund haben. Vorher blieb "ok" stehen - und genau "ok"
    # blendet das Backend aus, der Operator sah "prozess_alt" ohne Grund,
    # unbegrenzt lange, und der Agent rief dabei in jedem Heartbeat
    # erneut PowerShell fuer den Rueckweg.
    agent._NEUSTART_FEHLER = agent._neustart_ziel(ABW)
    try:
        check("nach einem gescheiterten Handeln: eigener Grund",
              grund(ABW) == "gescheitert", grund(ABW))
        check("ein anderes Ziel ist davon nicht betroffen",
              grund({**ABW, "python": "3.14.11"}) == "ok",
              grund({**ABW, "python": "3.14.11"}))
    finally:
        agent._NEUSTART_FEHLER = None
    check("der Grund steht in der gemeinsamen Menge",
          "gescheitert" in agent.NEUSTART_GRUENDE)

    # I3b: der Merker wird NUR auf belastbaren Befund geloescht. Ist die
    # Laufzeit nicht als "ok" vermessen, kann "gleich" auch von einer
    # ausgefallenen Messung kommen - dann waere das Loeschen ein
    # Freibrief fuer den naechsten Neustart derselben Fassung.
    for zustand, erwartet in (("ok", ["weg"]), ("unpruefbar", [])):
        geloescht = []
        _echt_vg = getattr(agent, "ziel_vergessen", None)
        if _echt_vg:
            agent.ziel_vergessen = lambda *a, **k: geloescht.append("weg")
            try:
                agent.neustart_faellig(
                    {**GLEICH, "laufzeit": {**OK, "zustand": zustand}},
                    dict(ERLAUBT), None)
            finally:
                agent.ziel_vergessen = _echt_vg
            check(f"Laufzeit {zustand}: Merker loeschen {erwartet != []}",
                  geloescht == erwartet, geloescht)

    # Kleinigkeit 5: ein gleicher Stand loescht den Merker - der Neustart
    # hat gewirkt, und dieselbe Fassung soll spaeter wieder duerfen.
    gemerkt = []
    echt_vergessen = getattr(agent, "ziel_vergessen", None)
    if echt_vergessen:
        agent.ziel_vergessen = lambda: gemerkt.append("weg")
        try:
            agent.neustart_faellig(GLEICH, dict(ERLAUBT), None)
        finally:
            agent.ziel_vergessen = echt_vergessen
        check("ein gleicher Stand loescht den Merker", gemerkt == ["weg"],
              gemerkt)
    else:
        check("ziel_vergessen() gibt es", False, "fehlt")

    # Der Grund reist beim naechsten Heartbeat mit.
    try:
        pakete, lauf = mit_stand(lambda: dict(NEU), lambda *a, **k: dict(OK))
        agent.LAUFT = {"python": "3.14.7", "packages": {"certifi": "2026.7.22"}}
        agent.installierte_python = lambda *a, **k: "3.14.10"
        echt_rw = agent.rueckweg_bestaetigt
        agent.rueckweg_bestaetigt = lambda: False
        agent._NEUSTART_GESEHEN = None
        agent._NEUSTART_GRUND = None
        try:
            s1 = agent.stand_melden(jetzt=400.0)
            agent.neustart_faellig(s1, dict(ERLAUBT), None)
            agent.neustart_faellig(s1, dict(ERLAUBT), None)
            s2 = agent.stand_melden(jetzt=460.0)
            check("der Grund reist im Stand mit",
                  s2.get("neustart") == "rueckweg", s2.get("neustart"))
        finally:
            agent.rueckweg_bestaetigt = echt_rw
    finally:
        agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf
        agent.LAUFT = _echt_lauft
        agent.installierte_python = _echt_inst_py

# I3a: faellt die Messung aus, gilt weiter die LETZTE gemessene Fassung.
# Vorher wurde sie mit None ueberschrieben, und stand["python"] fiel auf
# den Wert aus bundled_stand() zurueck - platform.python_version(), also
# die des LAUFENDEN Prozesses. Damit sah eine echte Abweichung bis zu
# einer Stunde lang wie "alles gleich" aus, ausgerechnet wenn python.exe
# gesperrt ist oder in den Zeitablauf laeuft, und ziel_vergessen() nahm
# das als "der Neustart hat gewirkt".
#
# Die beiden Seiten muessen AUSEINANDERLIEGEN: bundled_stand liefert hier
# 3.14.7, die Messung 3.14.10. Mit demselben Wert auf beiden Seiten war
# diese Pruefung gruen, egal was der Code tut - die Mutation hat sie
# ueberlebt.
try:
    mit_stand(lambda: dict(ALT), lambda *a, **k: dict(OK))
    agent.LAUFT = dict(ALT)
    agent.installierte_python = lambda *a, **k: "3.14.10"
    agent._NEUSTART_GRUND = None
    s1 = agent.stand_melden(jetzt=1000.0)
    check("die gemessene Fassung steht im Stand",
          s1.get("python") == "3.14.10", s1.get("python"))
    agent.installierte_python = lambda *a, **k: None
    agent._STAND_GEPRUEFT = None
    s2 = agent.stand_melden(jetzt=1060.0)
    check("eine misslungene Messung behaelt die letzte gute",
          s2.get("python") == "3.14.10", s2.get("python"))
    check("und damit bleibt die Abweichung sichtbar",
          s2.get("python") != (s2.get("laeuft") or {}).get("python"),
          (s2.get("python"), (s2.get("laeuft") or {}).get("python")))
finally:
    agent.bundled_stand, agent.laufzeit_pruefen = _echt_bundled, _echt_lauf
    agent.LAUFT = _echt_lauft
    agent.installierte_python = _echt_inst_py
    agent._STAND_PY_PLATTE = None
    agent._STAND_GEPRUEFT = None

# rueckweg_bestaetigt() prueft die Aufgabe im Detail: vorhanden,
# eingeschaltet, und ihr Aufruf zeigt auf Dateien, die es gibt. Eine
# Aufgabe kann vorhanden und aktiv sein und nach einer halb
# durchgelaufenen Installation ins Leere zeigen - dann darf nicht
# beendet werden. Geprueft wird am Quelltext, weil die Abfrage Windows
# braucht.
import inspect  # noqa: E402

# C1, I3 und I4: die HANDLUNG, nicht nur die Entscheidung.
check("neustart_ausfuehren() gibt es", hasattr(agent, "neustart_ausfuehren"))

if hasattr(agent, "neustart_ausfuehren"):
    def ausfuehren(merker_ok=True, neustart_ok=True):
        ruf = []
        e1, e2 = agent.ziel_vermerken, agent.restart_self_detached
        agent.ziel_vermerken = lambda z: (ruf.append("merker"), merker_ok)[1]
        agent.restart_self_detached = lambda: (ruf.append("neustart"),
                                               neustart_ok)[1]
        try:
            return agent.neustart_ausfuehren(ABW), ruf
        finally:
            agent.ziel_vermerken, agent.restart_self_detached = e1, e2

    ok, ruf = ausfuehren()
    check("im guten Fall wird vermerkt und dann neu gestartet",
          ok is True and ruf == ["merker", "neustart"], (ok, ruf))
    # C1: ohne Merker kein Neustart. Der Merker ist die EINZIGE Sicherung
    # gegen eine Schleife; er ueberlebt den Neustart, der Speicher nicht.
    ok, ruf = ausfuehren(merker_ok=False)
    check("ohne vermerktes Ziel wird NICHT neu gestartet",
          ok is False and ruf == ["merker"], (ok, ruf))
    # I3: scheitert der Neustart selbst, darf sich der Agent nicht
    # beenden - sonst ist er weg und niemand holt ihn.
    ok, ruf = ausfuehren(neustart_ok=False)
    check("scheitert der Neustart, wird nicht beendet", ok is False, (ok, ruf))

# Der Merker im echten Rundlauf, nicht am Quelltext. Dafuer nehmen die
# drei Funktionen das Verzeichnis als Parameter - wie laufzeit_pruefen()
# auch. Vorher stand hier eine Suche nach ".replace(" im Quelltext; die
# blieb gruen, als eine Mutation direkt in die Zieldatei schrieb.
_mk = TMP / "merkerprobe"
_mk.mkdir(parents=True, exist_ok=True)
check("ohne Merker gibt es kein versuchtes Ziel",
      agent.versuchtes_ziel(_mk) is None, agent.versuchtes_ziel(_mk))
check("vermerken gelingt und sagt das",
      agent.ziel_vermerken("640d8602c61140ec", _mk) is True)
check("das vermerkte Ziel kommt zurueck",
      agent.versuchtes_ziel(_mk) == "640d8602c61140ec",
      agent.versuchtes_ziel(_mk))
check("vergessen loescht es",
      (agent.ziel_vergessen(_mk), agent.versuchtes_ziel(_mk))[1] is None)
check("vergessen ohne Merker wirft nicht",
      (agent.ziel_vergessen(_mk), True)[1])
# Laesst sich der Merker nicht schreiben, muss ziel_vermerken() False
# sagen und nicht nur protokollieren - sonst beendet sich der Agent ohne
# Schleifensicherung. Erzwungen ueber ein Verzeichnis, das es nicht gibt,
# nicht ueber Dateirechte: die Pruefreihe laeuft als root, und dort
# greifen Rechte nicht - eine Pruefung, die davon abhaengt, ist hier
# gruen, ohne etwas zu messen.
check("ein unbeschreibbares Verzeichnis ergibt False",
      agent.ziel_vermerken("deadbeefdeadbeef",
                           TMP / "gibtsnicht" / "tiefer") is False)
# Ein bestehender Merker darf bei einem Fehlschlag NICHT verloren gehen.
# Hier steht ein VERZEICHNIS an der Stelle der Nebendatei, in die
# geschrieben wird - das scheitert fuer jeden Benutzer.
agent.ziel_vermerken("aaaabbbbccccdddd", _mk)
(_mk / (agent.NEUSTART_MERKER + ".neu")).mkdir(exist_ok=True)
try:
    check("ein Fehlschlag meldet sich",
          agent.ziel_vermerken("1111222233334444", _mk) is False)
    check("nach einem Fehlschlag steht der alte Merker noch",
          agent.versuchtes_ziel(_mk) == "aaaabbbbccccdddd",
          agent.versuchtes_ziel(_mk))
finally:
    (_mk / (agent.NEUSTART_MERKER + ".neu")).rmdir()
# M2: ungueltiges UTF-8 - nicht Nullbytes - laesst read_text() platzen.
(_mk / agent.NEUSTART_MERKER).write_bytes(b"\xff\xfe nicht utf-8")
check("ein unlesbarer Merker ergibt kein Ziel",
      agent.versuchtes_ziel(_mk) is None, agent.versuchtes_ziel(_mk))
# M2: wer ins Installationsverzeichnis schreiben kann, darf den Agenten
# nicht mit einer riesigen Datei zum Absturz bringen.
(_mk / agent.NEUSTART_MERKER).write_bytes(b"a" * 5_000_000)
check("ein riesiger Merker wirft nicht",
      agent.versuchtes_ziel(_mk) != "a" * 5_000_000)
agent.ziel_vergessen(_mk)

# Die Abfrage steht als Konstante neben der Funktion - dort wird
# geprueft, und zusaetzlich, dass die Funktion sie wirklich benutzt.
rw_quelle = getattr(agent, "PS_AUFGABE_PRUEFEN", "")
check("die Funktion benutzt die Abfrage",
      hasattr(agent, "rueckweg_bestaetigt")
      and "PS_AUFGABE_PRUEFEN" in inspect.getsource(agent.rueckweg_bestaetigt))
# Am 2026-11-20 auf einem Windows-Testhost gemessen, nicht angenommen:
#
#   Execute   : "C:\Program Files\CO37\python\pythonw.exe"
#   Arguments : "C:\Program Files\CO37\agent.py"
#   Interval  : PT5M   Duration : (leer)   StopAtDurationEnd : False
#
# Zwei Dinge daran sind tragend. Erstens zerlegt schtasks das /TR
# wirklich in Execute und Arguments - sonst stuende die ganze
# Befehlszeile in Execute und Test-Path scheiterte auf jedem gesunden
# Host. Zweitens stehen die ANFUEHRUNGSZEICHEN im Wert: ohne Trim('"')
# zeigt der Pfad auf "C:\...pythonw.exe" samt Zeichen, Test-Path sagt
# nein, und rueckweg_bestaetigt() ergibt ueberall False - das Merkmal
# waere lautlos wirkungslos, sichtbar nur als Grund "rueckweg".
#
# Geprueft wird am Quelltext, weil die Abfrage Windows braucht. Das ist
# die schwaechere Sorte Pruefung; sie steht hier mit der Messung daneben,
# damit niemand das Trim fuer Zierat haelt und herausnimmt.
for teil, was in (("CO37Agent", "die Aufgabe beim Namen"),
                  ("Disabled", "den ausgeschalteten Zustand"),
                  ("$exe = $exe.Trim('\"')",
                   "die Anfuehrungszeichen um den Aufruf"),
                  ("$arg = $arg.Trim('\"')",
                   "die Anfuehrungszeichen um das Argument"),
                  ("Test-Path -LiteralPath $exe", "die Datei des Aufrufs"),
                  ("Test-Path -LiteralPath $arg", "die Datei des Arguments"),
                  ("pythonw.exe", "dass der Aufruf pythonw.exe ist"),
                  ("agent.py", "dass das Argument agent.py ist"),
                  # Die Wiederholung IST der Rueckweg. Der Installer sagt
                  # selbst, dass sie fehlen kann ("Warnung: 5-Minuten-
                  # Wiederholung nicht gesetzt", build_msi.py), und bricht
                  # dort nicht ab. Ohne sie ist der Once-Ausloeser nach der
                  # Installation verbraucht und holt nie wieder.
                  ("Repetition", "die Wiederholung des Ausloesers"),
                  ("PT5M", "deren Abstand"),
                  ("StopAtDurationEnd", "dass sie nicht auslaeuft")):
    check(f"rueckweg_bestaetigt() prueft {was}", teil in rw_quelle,
          None if teil in rw_quelle else rw_quelle[:0])
# Die Schleife muss die Entscheidung treffen und handeln. Geprueft am
# Quelltext der jeweiligen Funktion - nicht an der ganzen Datei: eine
# Suche in agent_quelle blieb gruen, nachdem genau diese zwei Aufrufe aus
# der Schleife herausgezogen worden waren.
_main_quelle = inspect.getsource(agent.main)
for teil in ("neustart_faellig(", "neustart_ausfuehren("):
    check(f"die Heartbeat-Schleife ruft {teil}", teil in _main_quelle)
_tun_quelle = inspect.getsource(agent.neustart_ausfuehren)
for teil in ("ziel_vermerken(", "restart_self_detached()"):
    check(f"neustart_ausfuehren() ruft {teil}", teil in _tun_quelle)

# Und sie muss danach AUFHOEREN. Ohne das return laeuft der alte Prozess
# weiter, haelt die Einzelinstanz-Sperre, und der angestossene Neustart
# beendet sich sofort wieder ("Es laeuft bereits ein Agent") - der Host
# bliebe auf der alten Laufzeit stehen, und der Merker haette den
# zweiten Versuch schon verbraucht. Genau der Fehlschlag, wegen dem es
# 0.51.4 und 0.51.5 gab. Geprueft wird am Baum, nicht an Zeichenketten:
# ein "return" irgendwo in der Naehe beweist nichts.
import ast as _ast  # noqa: E402

_baum = _ast.parse(agent_quelle)
_main = next((k for k in _ast.walk(_baum)
              if isinstance(k, _ast.FunctionDef) and k.name == "main"), None)
_zweige = [k for k in _ast.walk(_main)] if _main else []
_treffer = [k for k in _zweige if isinstance(k, _ast.If)
            and "neustart_ausfuehren" in _ast.dump(k.test)]
check("main() entscheidet ueber neustart_ausfuehren()", len(_treffer) == 1,
      len(_treffer))
if len(_treffer) == 1:
    _rumpf = _treffer[0].body
    check("nach dem angestossenen Neustart kehrt die Schleife zurueck",
          len(_rumpf) == 1 and isinstance(_rumpf[0], _ast.Return),
          [type(k).__name__ for k in _rumpf])
    # Und die REIHENFOLGE. "neustart_ausfuehren(stand) and beenden" haette
    # alle sechs Sicherungen umgangen: der Aufruf liefe in jedem
    # Heartbeat, schriebe den Merker und stiesse einen Neustart an, ohne
    # dass beenden auch nur gelesen wird. Diese Mutation hat die Pruefung
    # am Baum zunaechst ueberlebt.
    _test = _treffer[0].test
    check("die Entscheidung steht VOR dem Handeln",
          isinstance(_test, _ast.BoolOp) and isinstance(_test.op, _ast.And)
          and isinstance(_test.values[0], _ast.Name)
          and _test.values[0].id == "beenden",
          _ast.dump(_test)[:80])

# I1: der Neustart wird NUR mit BREAKAWAY angestossen. Die Flagge
# verweigert CreateProcess genau dann, wenn dieser Prozess in einem
# Job-Objekt steckt, das Ausbrechen verbietet - und dann raeumt der
# Aufgabenplaner den schlafenden Helfer mit ab. Ein zweiter Versuch ohne
# die Flagge meldete "gelungen" und bewirkte sicher nichts. Geprueft am
# Quelltext, weil der Fall Windows und ein Job-Objekt braucht.
_rsd = inspect.getsource(agent.restart_self_detached)
check("der Neustart laeuft nur mit BREAKAWAY",
      _rsd.count("creationflags=") == 1
      and "BREAKAWAY" in _rsd.split("creationflags=")[1].split(")")[0],
      _rsd.count("creationflags="))
check("keine Schleife ueber Flaggenvarianten mehr",
      "for flags in" not in _rsd)

check("ohne Windows ist der Rueckweg bestaetigt",
      agent.rueckweg_bestaetigt() is True if not agent.IS_WINDOWS else True)

print("\n--- Teil 12: das Backend nimmt den Prozessstand auf ---")

LAEUFT_ALT = {"python": "3.14.7", "packages": {"certifi": "2026.7.22"}}

if hasattr(main, "bundled_sauber"):
    check("laeuft kommt durch",
          main.bundled_sauber({**GUT, "laeuft": LAEUFT_ALT}).get("laeuft")
          == LAEUFT_ALT,
          main.bundled_sauber({**GUT, "laeuft": LAEUFT_ALT}))
    check("ein Grund kommt durch",
          main.bundled_sauber({**GUT, "neustart": "rueckweg"}).get("neustart")
          == "rueckweg")
    krumm_lauft = {
        "Liste": ["3.14.7"],
        "Zeichenkette": "3.14.7",
        "Zahl": 7,
        "packages als Zeichenkette": {"python": "3.14.7", "packages": "viele"},
        "Name mit Leerzeichen": {"python": "3.14.7",
                                 "packages": {"cer tifi": "1.0"}},
        "zu viele Eintraege": {"python": "3.14.7",
                               "packages": {f"p{i}": "1.0" for i in range(21)}},
    }
    for was, wert in krumm_lauft.items():
        ergebnis = main.bundled_sauber({**GUT, "laeuft": wert})
        check(f"verworfen, aber nur laeuft: {was}",
              "laeuft" not in ergebnis
              and ergebnis.get("packages") == GUT["packages"], ergebnis)
    check("ein unbekannter Grund wird verworfen",
          "neustart" not in main.bundled_sauber({**GUT, "neustart": "erfunden"}))
    check("ein Grund als Zahl wird verworfen",
          "neustart" not in main.bundled_sauber({**GUT, "neustart": 7}))

if hasattr(main, "laufzeit_zustand"):
    def host_lauft(laeuft, laufzeit=None, neustart=None):
        bundled = {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}
        if laeuft is not None:
            bundled["laeuft"] = laeuft
        bundled["laufzeit"] = laufzeit or dict(
            {"zustand": "ok", "fehlend": [], "abweichend": [], "mehr": 0,
             "liste": "0.51.4"})
        if neustart:
            bundled["neustart"] = neustart
        return type("H", (), {"os_type": main.OSType.windows,
                              "bundled": bundled})()

    check("abweichender Prozessstand ergibt prozess_alt",
          main.laufzeit_zustand(host_lauft(LAEUFT_ALT))[0] == "prozess_alt",
          main.laufzeit_zustand(host_lauft(LAEUFT_ALT)))
    # Der Name traegt den Wert, die Art sagt, worum es geht - dasselbe
    # Muster wie bei bundled_diff.
    _eintr = main.laufzeit_zustand(host_lauft(LAEUFT_ALT))[1]
    check("und nennt, was abweicht",
          any(e.get("art") == "prozess_python" and e.get("name") == "3.14.7"
              for e in _eintr)
          and any(e.get("art") == "prozess_paket" and e.get("name") == "certifi"
                  for e in _eintr), _eintr)
    check("gleicher Prozessstand bleibt ok",
          main.laufzeit_zustand(host_lauft(
              {"python": "3.14.10", "packages": {"certifi": "2026.10.23"}}))[0]
          == "ok")
    # Eine unvollstaendige Laufzeit ist der dringendere Befund.
    check("unvollstaendig hat Vorrang vor prozess_alt",
          main.laufzeit_zustand(host_lauft(
              LAEUFT_ALT, {"zustand": "unvollstaendig", "fehlend": ["x"],
                           "abweichend": [], "mehr": 0, "liste": "0.51.4"}
          ))[0] == "unvollstaendig")
    check("ein gescheitertes Handeln reist nach aussen",
          {"art": "neustart", "name": "gescheitert"} in main.laufzeit_zustand(
              type("H", (), {"os_type": main.OSType.windows,
                             "bundled": {**GUT, "laeuft": LAEUFT_ALT,
                                         "laufzeit": dict(OK),
                                         "neustart": "gescheitert"}})())[1],
          main.laufzeit_zustand(
              type("H", (), {"os_type": main.OSType.windows,
                             "bundled": {**GUT, "laeuft": LAEUFT_ALT,
                                         "laufzeit": dict(OK),
                                         "neustart": "gescheitert"}})())[1])
    check("beide Seiten kennen dieselben Gruende",
          set(agent.NEUSTART_GRUENDE) == set(main.NEUSTART_GRUENDE),
          set(agent.NEUSTART_GRUENDE) ^ set(main.NEUSTART_GRUENDE))
    check("der verbrauchte Versuch reist nach aussen",
          any(e.get("art") == "neustart" for e in main.laufzeit_zustand(
              host_lauft(LAEUFT_ALT, neustart="zweiter"))[1]),
          main.laufzeit_zustand(host_lauft(LAEUFT_ALT, neustart="zweiter"))[1])

check("der Schalter steht in den Vorgaben mit true",
      main.ROLL_DEFAULTS.get("agent_laufzeit_neustart") == "true",
      main.ROLL_DEFAULTS.get("agent_laufzeit_neustart"))
check("RollConfig kennt ihn",
      "laufzeit_neustart" in main.RollConfig.model_fields)
check("die Heartbeat-Antwort traegt ihn",
      "agent_laufzeit_neustart" in (main.__dict__ and
      (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")))

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
