"""
CO-37 - Der globale Admin-Token ist entfallen und kommt nicht zurueck.

Die Geschichte in drei Schritten:

  Der Token stammte aus der Zeit vor den Benutzerkonten, als er der
  einzige Weg hinein war. Danach blieb er: dauerhaft gueltig, nie
  ablaufend, volle Rechte auf jeder Route - und als einzige
  Zugangsberechtigung ohne Drosselung, denn die sitzt nur an der
  Anmelderoute (F-11 der Sicherheitspruefung).

  In 0.36.10 galt er nur noch ueber Loopback. Damit war er auf einer
  Installation mit HTTPS-Zwang unbenutzbar: der Zwang laesst ueber
  Loopback nur /api/health durch. Uebrig blieb eine Berechtigung ohne
  Nutzen, aber mit Angriffsflaeche.

  In 0.36.12 ist er entfallen. Der Weg zurueck nach einem Aussperren
  fuehrt ueber die Datenbank - siehe README, "Wenn du dich aussperrst".

Diese Reihe haelt den Zustand fest. Eine Kopfzeile X-API-Key darf nichts
mehr oeffnen, und im Code darf der Weg nicht wieder auftauchen - auch
nicht versehentlich, etwa weil jemand eine alte Fassung einer Datei
zurueckspielt.

Geprueft wird die Funktion direkt statt ueber das Netz: das Testgeruest
bindet uvicorn an 127.0.0.1, und es geht hier ohnehin um Code, der nicht
mehr da sein soll.

Braucht kein Backend und kein Netz.

    python3 tests/apikey-test.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/apikey.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"
# Bewusst gesetzt: haette das Backend die Variable noch, wuerde sie hier
# greifen - und die Pruefungen unten wuerden es merken.
os.environ["CO37_ADMIN_TOKEN"] = "sollte-nichts-mehr-oeffnen"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def anfrage(ip, kopfzeilen=None):
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/", "raw_path": b"/", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "client": (ip, 40000) if ip else None,
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (kopfzeilen or {}).items()],
    })


# ----------------------------------------------------------------------
print("--- Der Weg ist im Code nicht mehr da ---")
quelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")

check("keine Konstante ADMIN_TOKEN mehr",
      re.search(r"^ADMIN_TOKEN\s*=", quelle, re.M) is None)
check("CO37_ADMIN_TOKEN wird nicht mehr gelesen",
      'getenv("CO37_ADMIN_TOKEN"' not in quelle)
check("kein Parameter x_api_key mehr",
      re.search(r"\bx_api_key\b", quelle) is None)
check("X-API-Key steht nicht mehr in den erlaubten CORS-Kopfzeilen",
      re.search(r'allow_headers=\[[^\]]*X-API-Key', quelle, re.S) is None)

# authenticate() nimmt den Key nicht mehr entgegen.
import inspect  # noqa: E402
argumente = list(inspect.signature(main.authenticate).parameters)
check("authenticate() kennt kein x_api_key", "x_api_key" not in argumente,
      argumente)

# ----------------------------------------------------------------------
print("--- Und er oeffnet nichts mehr ---")


def versuch(kopfzeilen=None, sitzung="", ip="127.0.0.1"):
    """
    Ruft authenticate() so auf, wie FastAPI es tut.

    Das Sitzungstoken geht als Argument hinein, nicht als Kopfzeile - die
    zieht sonst die Routenschicht heraus, und die laeuft hier nicht mit.
    Die Kopfzeilen dienen dem Gegenteil: sie sollen belegen, dass eine
    mitgeschickte X-API-Key nichts mehr bewirkt.
    """
    with Session(main.engine) as s:
        try:
            wer = main.authenticate(sitzung, s, anfrage(ip, kopfzeilen))
            return wer.role, None
        except HTTPException as exc:
            return None, exc.status_code


# Genau der Wert aus der Umgebungsvariable - haette das Backend ihn noch,
# waere das hier ein Treffer.
rolle, code = versuch({"X-API-Key": "sollte-nichts-mehr-oeffnen"})
check("X-API-Key mit dem alten Wert oeffnet nichts", code == 401,
      code or rolle)

rolle, code = versuch({"X-API-Key": "sollte-nichts-mehr-oeffnen"}, ip="192.0.2.9")
check("auch nicht aus dem Netz", code == 401, code or rolle)

rolle, code = versuch({})
check("ohne Kopfzeile ebenfalls 401", code == 401, code or rolle)

# ----------------------------------------------------------------------
print("--- Die Sitzung ist der Weg ---")
# Gegenprobe zur Aussagekraft: wenn hier gar nichts mehr durchkaeme,
# blieben die Pruefungen oben auch dann gruen, wenn die Anmeldung kaputt
# waere. Also einmal zeigen, dass eine gueltige Sitzung angenommen wird.
from datetime import timedelta  # noqa: E402

from models import LoginSession, Role, User  # noqa: E402

with Session(main.engine) as s:
    u = User(username="pruefer", role=Role.admin,
             password_hash=main.hash_password("egal"))
    s.add(u)
    s.commit()
    s.refresh(u)
    token = "sitzungstoken-fuer-die-pruefung"
    s.add(LoginSession(user_id=u.id, token_hash=main.hash_token(token),
                       expires_at=main.utcnow() + timedelta(hours=1)))
    s.commit()

rolle, code = versuch(sitzung=token)
check("eine gueltige Sitzung wird angenommen", rolle == Role.admin,
      code or rolle)

rolle, code = versuch(sitzung="unbekannt")
check("eine unbekannte Sitzung nicht", code == 401, code or rolle)

# ----------------------------------------------------------------------
print("--- Auch die Einrichtung erzeugt ihn nicht mehr ---")
setup = (WURZEL / "setup.sh").read_text(encoding="utf-8")
check("setup.sh erzeugt keinen Admin-Token mehr",
      "CO37_ADMIN_TOKEN" not in setup)
check("und raeumt eine alte Datei weg",
      'rm -f "$BASE/data/admin.token"' in setup)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
