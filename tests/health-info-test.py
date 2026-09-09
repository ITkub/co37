"""
CO-37 - /api/health gibt Versionsnummern nicht an jeden heraus.

Aus der Sicherheitspruefung vom 2026-08-22 (F-09). Die Route lieferte
unangemeldet und aus jedem Netz die Fassung des Systems, die des Agents
und den Stand des Datenbankschemas. Fuer den Betrieb bringt das niemandem
etwas, der nicht ohnehin angemeldet ist; fuer die Auswahl eines passenden
Angriffs ist es der erste Schritt.

Offen bleiben MUSS die Route. Zwei Aufrufer haengen daran:

  Der Watcher fragt sie nach jedem Update ab und vergleicht die Version.
  Er laeuft auf demselben Rechner - Loopback.

  Die Anmeldeseite braucht die Vorgabesprache, bevor ein Benutzer bekannt
  ist. Sie bekommt weiterhin status und default_language.

Das ist kein starker Schutz und soll keiner sein: wer sich anmelden kann,
sieht die Version ohnehin. Es nimmt die Auskunft nur aus der offenen
Antwort heraus.

Geprueft wird im selben Prozess statt ueber das Netz: das Testgeruest
bindet uvicorn an 127.0.0.1, und von dort AUS ist jeder Aufruf Loopback -
der Fall, um den es hier geht, liesse sich so gar nicht herstellen.

Braucht kein laufendes Backend und kein Netz.

    python3 tests/health-info-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/health.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from starlette.requests import Request  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from models import LoginSession, Role, User  # noqa: E402

SQLModel.metadata.create_all(main.engine)

VERSIONSFELDER = ("version", "agent_version", "schema_version")
# Seit 0.37.32 haengt eine vierte Auskunft an derselben Schranke: ob
# gerade ein Systemupdate laeuft. Die Oberflaeche unterscheidet damit
# einen erwarteten Ausfall von einem echten - ein Unangemeldeter braucht
# sie nicht.
GESCHUETZT = VERSIONSFELDER + ("update_running",)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def anfrage(ip, kopfzeilen=None):
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/api/health", "raw_path": b"/api/health", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "client": (ip, 40000) if ip else None,
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (kopfzeilen or {}).items()],
    })


def hole(ip, sitzung="", kopfzeilen=None):
    with Session(main.engine) as s:
        return main.health(anfrage(ip, kopfzeilen), sitzung, s)


# Ein Konto mit gueltiger Sitzung - fuer die Gegenprobe weiter unten.
with Session(main.engine) as s:
    u = User(username="healthpruefer", role=Role.admin,
             password_hash=main.hash_password("egal"))
    s.add(u)
    s.commit()
    s.refresh(u)
    BENUTZER_ID = u.id
    TOKEN = "sitzung-fuer-die-health-pruefung"
    s.add(LoginSession(user_id=BENUTZER_ID, token_hash=main.hash_token(TOKEN),
                       expires_at=main.utcnow() + timedelta(hours=1)))
    s.commit()


# ======================================================================
print("--- Was jeder sehen darf ---")
offen = hole("192.0.2.50")
check("die Route antwortet auch unangemeldet aus dem Netz",
      offen.get("status") == "ok", offen)
check("die Vorgabesprache bleibt drin", "default_language" in offen, offen)
for feld in GESCHUETZT:
    check(f"{feld} steht NICHT in der offenen Antwort", feld not in offen,
          offen.get(feld))


# ======================================================================
print("--- Was der Watcher sehen muss ---")
# Ohne diesen Abschnitt sagte die Reihe nichts: eine Antwort, die die
# Felder immer weglaesst, bestuende alles oben - und der Watcher koennte
# ein misslungenes Update nicht mehr erkennen.
lokal = hole("127.0.0.1")
for feld in VERSIONSFELDER:
    check(f"ueber Loopback ist {feld} da", feld in lokal, lokal.get(feld))
check("und die Version ist keine leere Angabe",
      bool(lokal.get("version")), lokal.get("version"))

check("auch ueber ::1", "version" in hole("::1"))
check("und update_running ebenfalls", "update_running" in lokal, lokal)


# ======================================================================
print("--- Was ein angemeldeter Benutzer sieht ---")
mit_kopf = hole("192.0.2.50", sitzung=TOKEN)
for feld in GESCHUETZT:
    check(f"mit Sitzung (Kopfzeile) ist {feld} da", feld in mit_kopf,
          mit_kopf.get(feld))

mit_cookie = hole("192.0.2.50",
                  kopfzeilen={"Cookie": f"{main.SESSION_COOKIE}={TOKEN}"})
check("und ebenso ueber das Sitzungscookie", "version" in mit_cookie,
      mit_cookie)

unbekannt = hole("192.0.2.50", sitzung="gibt-es-nicht")
check("eine unbekannte Sitzung oeffnet nichts",
      "version" not in unbekannt, unbekannt)
check("und auch update_running nicht",
      "update_running" not in unbekannt, unbekannt)

# Der Wert selbst, nicht nur seine Anwesenheit: ohne laufendes Update
# muss er falsch sein, sonst hielte die Oberflaeche jeden Ausfall drei
# Minuten lang fuer erwartet.
check("ohne laufendes Update ist update_running falsch",
      mit_kopf.get("update_running") is False, mit_kopf.get("update_running"))

import json as _json  # noqa: E402
_statusdatei = main.update_manager.UPDATE_DIR / "status.json"
_statusdatei.parent.mkdir(parents=True, exist_ok=True)
_gemerkt = _statusdatei.read_text() if _statusdatei.exists() else None
try:
    _statusdatei.write_text(_json.dumps({"state": "running", "log": []}))
    _laeuft = hole("192.0.2.50", sitzung=TOKEN)
    check("waehrend eines Updates ist update_running wahr",
          _laeuft.get("update_running") is True, _laeuft.get("update_running"))
    _statusdatei.write_text(_json.dumps({"state": "success", "log": []}))
    _fertig = hole("192.0.2.50", sitzung=TOKEN)
    check("nach dem Update wieder falsch",
          _fertig.get("update_running") is False, _fertig.get("update_running"))
finally:
    if _gemerkt is None:
        _statusdatei.unlink(missing_ok=True)
    else:
        _statusdatei.write_text(_gemerkt)

# Eine abgelaufene Sitzung ebenfalls nicht.
with Session(main.engine) as s:
    alt = "abgelaufene-sitzung"
    s.add(LoginSession(user_id=BENUTZER_ID, token_hash=main.hash_token(alt),
                       expires_at=main.utcnow() - timedelta(minutes=1)))
    s.commit()
check("eine abgelaufene Sitzung ebenfalls nicht",
      "version" not in hole("192.0.2.50", sitzung=alt))


# ======================================================================
print("--- Die Oberflaeche kommt weiterhin an die Version ---")
# Sie holt sie mit fetch. Ohne mitgeschicktes Cookie stuende in der
# Fusszeile ab jetzt keine Version mehr - und der Hinweis auf eine
# veraltete Seite erschiene nie. fetch schickt das Cookie von selbst mit,
# solange niemand credentials auf 'omit' setzt.
app_js = (WURZEL / "frontend" / "app.js").read_text(encoding="utf-8")
treffer = [z for z in app_js.splitlines()
           if "/api/health" in z and "omit" in z]
check("keine health-Abfrage schickt das Cookie ausdruecklich nicht mit",
      not treffer, treffer)
check("die Oberflaeche fragt die Route ueberhaupt ab",
      app_js.count('"/api/health"') >= 2, app_js.count('"/api/health"'))



# ======================================================================
# Der Fehlerpfad verraet das Datenmodell nicht (F-42)
# ======================================================================
# /api/health muss offen bleiben, und _darf_versionen_sehen() haelt die
# Versionsnummern korrekt zurueck (F-09). Der 503-Zweig lief bis 0.37.9
# daran vorbei und lieferte jedem Unangemeldeten die Namen der fehlenden
# Tabellen und Spalten.
mquelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
_i = mquelle.find("schema_mismatch")
_block = mquelle[max(0, _i - 800):_i + 800]
check("der 503-Zweig fragt, wer zusehen darf",
      "_darf_versionen_sehen" in _block)
# Nur der 503-Zweig, nicht die ganze Datei: /api/v1/schema liefert
# dieselbe Liste voellig zu Recht - die Route haengt an require_admin.
# Die erste Fassung dieser Pruefung suchte im ganzen Quelltext und
# schlug genau daran an.
check("missing steht im 503-Zweig nicht mehr bedingungslos drin",
      '"missing": missing' not in _block, _block[-300:])

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
