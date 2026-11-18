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
    _bm = (WURZEL / "packaging" / "build_msi.py").read_text(encoding="utf-8")
    PY_ERWARTET = _re.search(r'^PY_VERSION = "([^"]+)"', _bm, _re.M).group(1)
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

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
