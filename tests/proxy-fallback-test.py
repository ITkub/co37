"""
CO-37 - Rueckfall des HTTPS-Zwangs.

Der wichtigste Teil der Umschaltung, und der einzige, der sich ueber HTTP
nicht pruefen laesst: ist der Zwang an und der Proxy falsch eingetragen,
kommt niemand mehr hinein - auch nicht, um ihn wieder abzuschalten. Dann
muss er sich nach Ablauf der Frist von selbst zurueckstellen.

Wird direkt gegen die Funktionen geprueft, damit sich die Zeit vorspulen
laesst statt eine Viertelstunde zu warten.

    python3 tests/proxy-fallback-test.py
"""
import os
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/fallback.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from starlette.requests import Request  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def fake_request(path="/api/v1/hosts", peer="192.0.2.99", headers=None):
    """Baut eine Anfrage, wie sie bei der Middleware ankommt."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": raw,
        "client": (peer, 12345), "server": ("127.0.0.1", 8080),
    })


PROXY = "192.168.1.20"
HTTPS = {"x-forwarded-proto": "https"}
HTTP = {"x-forwarded-proto": "http"}


def configure(trusted="", https_only=False, deadline=None):
    with Session(main.engine) as s:
        main.set_setting(s, main.SET_TRUSTED_PROXY, trusted)
        main.set_setting(s, main.SET_HTTPS_ONLY, "true" if https_only else "false")
        main.set_setting(s, main.SET_HTTPS_DEADLINE,
                         deadline.isoformat() if deadline else "")
        s.commit()
        main.load_proxy_config(s)


def blocked(req):
    return main.https_only_check(req) is not None


# ---------------------------------- Regel 1: HTTPS nur ueber den Proxy
# Gilt unabhaengig vom Schalter. Eine Anfrage, die 'https' behauptet, aber
# nicht vom eingetragenen Proxy kommt, ist eine unbelegte Behauptung -
# CO-37 nimmt selbst kein TLS entgegen.
configure(trusted="", https_only=False)
check("ohne Proxy: HTTPS wird abgewiesen",
      blocked(fake_request(peer="192.0.2.77", headers=HTTPS)))
check("ohne Proxy: HTTP kommt durch",
      not blocked(fake_request(peer="192.0.2.77")))
check("ohne Proxy: HTTP mit Kopfzeile kommt durch",
      not blocked(fake_request(peer="192.0.2.77", headers=HTTP)))

configure(trusted=PROXY, https_only=False)
check("mit Proxy: HTTPS von dort kommt durch",
      not blocked(fake_request(peer=PROXY, headers=HTTPS)))
check("mit Proxy: HTTPS von woanders wird abgewiesen",
      blocked(fake_request(peer="192.0.2.77", headers=HTTPS)))
check("mit Proxy: HTTP von woanders kommt durch",
      not blocked(fake_request(peer="192.0.2.77")))

# ------------------------------------------------------------ Grundlagen
configure(trusted=PROXY, https_only=False)
check("Zwang aus - alles kommt durch", not blocked(fake_request(peer=PROXY)))

configure(trusted=PROXY, https_only=True,
          deadline=main.utcnow() + timedelta(minutes=15))
check("ueber den Proxy mit HTTPS erlaubt",
      not blocked(fake_request(peer=PROXY, headers=HTTPS)))
check("ueber den Proxy ohne HTTPS abgewiesen",
      blocked(fake_request(peer=PROXY, headers=HTTP)))
check("direkt am Backend abgewiesen",
      blocked(fake_request(peer="192.0.2.77")))
check("gefaelschte Kopfzeile von fremder Adresse nuetzt nichts",
      blocked(fake_request(peer="192.0.2.77", headers=HTTPS)))

# ------------------------------------------------------ Gesundheitspruefung
check("Gesundheitspruefung ueber Loopback frei",
      not blocked(fake_request(path="/api/health", peer="127.0.0.1")))
check("Gesundheitspruefung von aussen NICHT frei",
      blocked(fake_request(path="/api/health", peer="192.0.2.77")))
check("Gesundheitspruefung ueber Loopback auch ohne Proxy frei",
      not blocked(fake_request(path="/api/health", peer="127.0.0.1",
                               headers=HTTPS)))
check("andere Route ueber Loopback nicht frei",
      blocked(fake_request(path="/api/v1/hosts", peer="127.0.0.1")))

# ------------------------------------------------------------ Rueckfall
# Falsch eingetragener Proxy: niemand kommt mehr durch, auch nicht zum
# Abschalten.
configure(trusted="10.9.9.9", https_only=True,
          deadline=main.utcnow() + timedelta(minutes=15))
check("falscher Proxy sperrt aus", blocked(fake_request(peer=PROXY, headers=HTTPS)))

# Frist abgelaufen -> der naechste Zugriff stellt zurueck.
#
# Wichtig: ueber den (falsch eingetragenen) Proxy bleibt es trotzdem
# gesperrt - Regel 1 gilt unabhaengig vom Schalter. Der Weg zurueck ist
# der unverschluesselte Zugang direkt am Backend. Genau darauf muss man
# sich verlassen koennen, wenn die Adresse des Proxy nicht stimmt.
configure(trusted="10.9.9.9", https_only=True,
          deadline=main.utcnow() - timedelta(seconds=1))
check("nach Ablauf der Frist unverschluesselt wieder erreichbar",
      not blocked(fake_request(peer=PROXY)))
check("Zwang hat sich selbst abgeschaltet", main._PROXY_CFG["https_only"] is False)
check("ueber den falschen Proxy bleibt HTTPS gesperrt",
      blocked(fake_request(peer=PROXY, headers=HTTPS)))

with Session(main.engine) as s:
    check("Abschaltung ist auch gespeichert",
          main._setting(s, main.SET_HTTPS_ONLY) == "false",
          main._setting(s, main.SET_HTTPS_ONLY))
    from models import AuditEntry  # noqa: E402
    from sqlmodel import select  # noqa: E402
    entries = s.exec(select(AuditEntry)
                     .where(AuditEntry.action == "https-only.auto-off")).all()
    check("Rueckfall steht im Pruefprotokoll", len(entries) >= 1, len(entries))

# ------------------------------------------- bestaetigt: kein Rueckfall mehr
configure(trusted=PROXY, https_only=True, deadline=None)
check("ohne Frist bleibt der Zwang bestehen",
      blocked(fake_request(peer=PROXY, headers=HTTP)))
check("und schaltet sich nicht ab", main._PROXY_CFG["https_only"] is True)

# ------------------------------------------------------- Adressliste
configure(trusted="192.168.1.20, 10.0.0.5", https_only=False)
check("mehrere Proxys werden erkannt",
      main.via_trusted_proxy(fake_request(peer="10.0.0.5")))
check("Leerzeichen in der Liste stoeren nicht",
      main.via_trusted_proxy(fake_request(peer="192.168.1.20")))
check("fremde Adresse gilt nicht",
      not main.via_trusted_proxy(fake_request(peer="10.0.0.6")))

# ------------------------------------------------- echte Absenderadresse
#
# F-21, 2026-08-31: gelesen wurde der ERSTE Eintrag aus X-Forwarded-For.
# Das ist nur richtig, wenn der Proxy die Kopfzeile ersetzt. Die
# verbreitete nginx-Vorgabe $proxy_add_x_forwarded_for haengt dagegen an,
# Traefik ebenso - dann steht vorne, was der Aufrufer selbst geschickt
# hat. Damit liess sich die Anmeldedrosselung vollstaendig umgehen: je
# Versuch eine neue Fantasieadresse, und der Zaehler fing bei null an.
# Im Pruefprotokoll stand dieselbe Erfindung.
#
# Seit 0.37.7 wird von RECHTS gelesen, unter Ueberspringen der
# eingetragenen Proxys.
configure(trusted=PROXY, https_only=False)

req = fake_request(peer=PROXY, headers={"x-forwarded-for": "203.0.113.9"})
check("einzelner Eintrag wird uebernommen",
      main.client_ip(req) == "203.0.113.9", main.client_ip(req))

# Der Angriff: der Aufrufer setzt selbst eine Adresse, der Proxy haengt
# seine Sicht hinten an. Massgeblich ist, was der Proxy gesehen hat.
req = fake_request(peer=PROXY,
                   headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9"})
check("eine vorangestellte Fantasieadresse zaehlt nicht",
      main.client_ip(req) == "203.0.113.9", main.client_ip(req))

# Kette aus zwei eingetragenen Proxys: die eigenen werden von rechts
# uebersprungen, uebrig bleibt der Aufrufer.
configure(trusted=f"{PROXY}, 10.0.0.1", https_only=False)
req = fake_request(peer=PROXY,
                   headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9, 10.0.0.1"})
check("eingetragene Proxys werden von rechts uebersprungen",
      main.client_ip(req) == "203.0.113.9", main.client_ip(req))

# Stehen nur eigene Proxys darin, ist die Gegenstelle die beste Auskunft.
req = fake_request(peer=PROXY, headers={"x-forwarded-for": "10.0.0.1"})
check("nur eigene Proxys: Gegenstelle gilt",
      main.client_ip(req) == PROXY, main.client_ip(req))

configure(trusted=PROXY, https_only=False)
req = fake_request(peer=PROXY, headers={"x-forwarded-for": "   "})
check("leere Kopfzeile faellt auf die Gegenstelle zurueck",
      main.client_ip(req) == PROXY, main.client_ip(req))

req = fake_request(peer="192.0.2.77", headers={"x-forwarded-for": "203.0.113.9"})
check("von fremder Adresse wird die Kopfzeile ignoriert",
      main.client_ip(req) == "192.0.2.77", main.client_ip(req))

# ------------------------------------------- X-Forwarded-Proto, dieselbe
#                                             Leserichtung (F-32)
#
# Die Korrektur aus F-21 wurde in der Schwesterfunktion nicht mitgezogen:
# request_is_https() nahm bis 0.37.9 weiter proto.split(",")[0], den
# ERSTEN Eintrag. Haengt der Proxy an, gewinnt damit der vom Aufrufer
# geschickte Wert - und die Funktion meldet "verschluesselt" fuer eine
# unverschluesselte Anfrage. Daran haengen der HTTPS-Zwang, das Loeschen
# der Bestaetigungsfrist beim Anmelden und last_seen_secure; die
# Fehlrichtung ist also die falsche.
print("--- X-Forwarded-Proto wird von rechts gelesen (F-32) ---")
configure(trusted=PROXY, https_only=False)

req = fake_request(peer=PROXY, headers={"x-forwarded-proto": "https"})
check("einzelnes https zaehlt", main.request_is_https(req) is True)

req = fake_request(peer=PROXY, headers={"x-forwarded-proto": "http"})
check("einzelnes http zaehlt", main.request_is_https(req) is False)

# Der Angriff: der Aufrufer behauptet https, der Proxy haengt seine
# Sicht ('http') hinten an.
req = fake_request(peer=PROXY,
                   headers={"x-forwarded-proto": "https, http"})
check("vorangestelltes https zaehlt nicht",
      main.request_is_https(req) is False, "https, http")

# Und die Gegenrichtung, damit die Pruefung nicht einfach 'immer False'
# belohnt: echtes https hinter einem angehaengten Eintrag.
req = fake_request(peer=PROXY,
                   headers={"x-forwarded-proto": "http, https"})
check("angehaengtes https zaehlt", main.request_is_https(req) is True,
      "http, https")

req = fake_request(peer=PROXY, headers={"x-forwarded-proto": "  "})
check("leere Kopfzeile gilt als unverschluesselt",
      main.request_is_https(req) is False)

req = fake_request(peer=PROXY, headers={"x-forwarded-proto": "HTTPS"})
check("Grossschreibung zaehlt genauso",
      main.request_is_https(req) is True)

req = fake_request(peer="192.0.2.77", headers={"x-forwarded-proto": "https"})
check("von fremder Adresse wird die Kopfzeile ignoriert",
      main.request_is_https(req) is False)


# ======================================================================
# uvicorn glaubt die Kopfzeile ab Werk selbst (F-58)
# ======================================================================
# Alles oben prueft die FUNKTIONEN. Die haben recht: client_ip() glaubt
# X-Forwarded-For nur, wenn peer_ip() ein eingetragener Proxy ist.
#
# Nur kommt peer_ip() nicht aus dem Netz, sondern aus scope["client"] -
# und uvicorn schaltet ProxyHeadersMiddleware ab Werk EIN. Die schreibt
# genau diesen Wert aus X-Forwarded-For um, fuer jeden Aufrufer aus
# 127.0.0.1 (forwarded_allow_ips, Vorgabe "127.0.0.1"). Eine Ebene unter
# der sorgfaeltigen Pruefung war die Kopfzeile also schon geglaubt.
#
# Am 2026-09-03 gemessen, sieben Anmeldeversuche mit je einem anderen
# erfundenen X-Forwarded-For:
#
#   ohne  --no-proxy-headers   401 401 401 401 401 401 401   (nie gesperrt)
#   mit   --no-proxy-headers   401 401 401 401 401 429 429
#
# Und im Pruefprotokoll stand danach die erfundene Adresse.
#
# Diese Reihe startet dafuer ein EIGENES Backend - zweimal, mit und ohne
# den Schalter. Gegen die Funktionen laesst sich das nicht pruefen: der
# Fehler sitzt in der Schicht darunter, und genau deshalb ist er fuenf
# Pruefrunden lang durchgerutscht.
print()
print("--- uvicorn glaubt X-Forwarded-For nicht mehr von selbst (F-58) ---")

import json as _json  # noqa: E402
import socket as _socket  # noqa: E402
import subprocess as _sub  # noqa: E402
import time as _time  # noqa: E402
import urllib.error as _uerr  # noqa: E402
import urllib.request as _ureq  # noqa: E402


def _freier_port():
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _versuche(extra_args):
    """Startet ein Backend, macht sieben Anmeldeversuche, gibt die Codes."""
    port = _freier_port()
    daten = Path(tempfile.mkdtemp())
    umgebung = dict(os.environ,
                    CO37_SECRET_KEY="test",
                    CO37_DB=f"sqlite:///{daten}/x.db",
                    CO37_DATA=str(daten))
    proc = _sub.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port)]
        + extra_args,
        cwd=str(Path(__file__).resolve().parent.parent / "backend"),
        env=umgebung, stdout=_sub.DEVNULL, stderr=_sub.DEVNULL)
    codes, protokoll = [], []
    try:
        for _ in range(60):
            _time.sleep(0.5)
            try:
                _ureq.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    return ["Backend gestorben"], []
        for i in range(1, 8):
            req = _ureq.Request(
                f"http://127.0.0.1:{port}/api/v1/login",
                data=_json.dumps({"username": "a", "password": "b"}).encode(),
                method="POST",
                headers={"Content-Type": "application/json",
                         "X-Forwarded-For": f"203.0.113.{i}"})
            try:
                with _ureq.urlopen(req, timeout=30) as r:
                    codes.append(r.status)
            except _uerr.HTTPError as e:
                codes.append(e.code)
        import sqlite3 as _sq
        with _sq.connect(f"{daten}/x.db") as c:
            protokoll = [r[0] for r in c.execute(
                "select from_ip from auditentry order by id desc limit 3")]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(daten, ignore_errors=True)
    return codes, protokoll


_mit, _prot_mit = _versuche(["--no-proxy-headers"])
check("mit --no-proxy-headers greift die Drosselung", 429 in _mit, _mit)
check("und im Protokoll steht die echte Adresse",
      all(p == "127.0.0.1" for p in _prot_mit) and bool(_prot_mit), _prot_mit)

# Gegenprobe: ohne den Schalter darf sie NICHT greifen. Ohne diese Haelfte
# sagte die Reihe nichts - eine Drosselung, die immer greift, bestuende
# die obere Pruefung auch dann, wenn der Schalter gar nichts tut.
_ohne, _prot_ohne = _versuche([])
check("ohne ihn greift sie nicht - der Schalter ist also der Grund",
      429 not in _ohne, _ohne)
check("und im Protokoll steht dann die erfundene Adresse",
      any(p.startswith("203.0.113.") for p in _prot_ohne), _prot_ohne)

# Und der Betrieb muss ihn setzen, nicht nur der Testlauf.
_setup = (Path(__file__).resolve().parent.parent / "setup.sh").read_text(
    encoding="utf-8")
_zeile = next((z for z in _setup.splitlines() if z.startswith("ExecStart=")), "")
check("die systemd-Unit startet uvicorn mit --no-proxy-headers",
      "--no-proxy-headers" in _zeile, _zeile)
_rt = (Path(__file__).resolve().parent.parent / "run-tests.sh").read_text(
    encoding="utf-8")
check("und das Testgeruest ebenso - sonst misst es etwas anderes",
      "--no-proxy-headers" in _rt)


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
