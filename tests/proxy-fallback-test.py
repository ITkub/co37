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
configure(trusted=PROXY, https_only=False)
req = fake_request(peer=PROXY, headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
check("echter Aufrufer wird uebernommen",
      main.client_ip(req) == "203.0.113.9", main.client_ip(req))
req = fake_request(peer="192.0.2.77", headers={"x-forwarded-for": "203.0.113.9"})
check("von fremder Adresse wird die Kopfzeile ignoriert",
      main.client_ip(req) == "192.0.2.77", main.client_ip(req))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
