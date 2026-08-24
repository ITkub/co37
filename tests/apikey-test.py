"""
CO-37 - Der Admin-Token gilt nur auf dem Rechner selbst.

Der Anlass (F-11, gefunden beim Durchsprechen der Sicherheitspruefung):
in authenticate() wird der API-Key VOR der Sitzung geprueft, auf jeder
Route. Die Drosselung greift aber nur an der Anmelderoute. Der
Admin-Token war damit die einzige Zugangsberechtigung, die man unbegrenzt
oft durchprobieren kann - dauerhaft gueltig, nie ablaufend, volle Rechte.
Vierzig Zeichen aus openssl rand machen Durchprobieren unrealistisch, aber
die Asymmetrie blieb: das Passwort ist nach fuenf Fehlversuchen fuenfzehn
Minuten dicht, der Token nie.

Gebraucht wird er nur noch dort, wo man ohnehin auf dem Server sitzt: vom
Testgeruest und als Weg zurueck, wenn man sich aus der Oberflaeche
aussperrt. Deshalb gilt er jetzt nur ueber Loopback.

Geprueft wird die Funktion direkt statt ueber das Netz: das Testgeruest
bindet uvicorn an 127.0.0.1, von aussen kaeme man gar nicht erst an. Ein
nachgebautes Request-Objekt sagt genau, welche Gegenstelle gemeldet wird -
und das ist der Punkt, um den es geht.

Braucht kein Backend und kein Netz.

    python3 tests/apikey-test.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/apikey.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_ADMIN_TOKEN"] = "geheimer-test-token"
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

SQLModel.metadata.create_all(main.engine)

KEY = "geheimer-test-token"
fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def anfrage(ip, kopfzeilen=None):
    """Ein Request, der als Gegenstelle genau ip meldet."""
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/", "raw_path": b"/", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "client": (ip, 40000) if ip else None,
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (kopfzeilen or {}).items()],
    })


def versuch(key, ip, kopfzeilen=None):
    """Gibt (Rolle, None) zurueck oder (None, HTTP-Code)."""
    with Session(main.engine) as s:
        try:
            wer = main.authenticate(key, "", s, anfrage(ip, kopfzeilen))
            return wer.role, None
        except HTTPException as exc:
            return None, exc.status_code


# ----------------------------------------------------------------------
print("--- Von innen gilt er ---")
rolle, code = versuch(KEY, "127.0.0.1")
check("richtiger Token ueber 127.0.0.1 wird angenommen",
      rolle == main.Role.admin, code or rolle)

rolle, code = versuch(KEY, "::1")
check("und ueber ::1 ebenso", rolle == main.Role.admin, code or rolle)

# ----------------------------------------------------------------------
print("--- Von aussen nicht ---")
rolle, code = versuch(KEY, "192.0.2.9")
check("richtiger Token aus dem Netz wird abgewiesen", code == 403, code or rolle)

# Der eigentliche Angriff: die Kopfzeile setzt sich jeder selbst. Geprueft
# wird die tatsaechliche Gegenstelle, nicht was die Anfrage behauptet.
rolle, code = versuch(KEY, "192.0.2.9", {"X-Forwarded-For": "127.0.0.1"})
check("X-Forwarded-For hilft nicht", code == 403, code or rolle)

rolle, code = versuch(KEY, "192.0.2.9", {"X-Real-IP": "127.0.0.1"})
check("X-Real-IP hilft ebenso wenig", code == 403, code or rolle)

# Und noch einmal schaerfer, MIT eingetragenem Proxy. Ohne ihn glaubt
# client_ip() der Kopfzeile ohnehin nicht - eine Pruefung ohne diesen
# Schritt kann nicht unterscheiden, ob hier die Gegenstelle oder
# client_ip() befragt wird, und bliebe auch dann gruen, wenn jemand auf
# client_ip() umstellt. Genau das waere aber die Luecke: der Proxy steht
# vor dem Netz, und dann waere der Token darueber wieder erreichbar.
main._PROXY_CFG["trusted"] = ["192.0.2.9"]
try:
    rolle, code = versuch(KEY, "192.0.2.9", {"X-Forwarded-For": "127.0.0.1"})
    check("auch ueber den eingetragenen Proxy nicht", code == 403, code or rolle)
finally:
    main._PROXY_CFG["trusted"] = []

# Ohne erkennbare Gegenstelle im Zweifel abweisen.
rolle, code = versuch(KEY, None)
check("ohne erkennbare Gegenstelle abgewiesen", code == 403, code or rolle)

# ----------------------------------------------------------------------
print("--- Ein unpassender Key sperrt niemanden aus ---")
# Wichtig fuer die Rueckwaertsvertraeglichkeit: eine versehentlich
# mitgeschickte Kopfzeile darf nicht 403 ergeben, sondern muss weiter auf
# die Sitzungspruefung durchfallen. Ohne Sitzung ist das 401, nicht 403.
rolle, code = versuch("falscher-token", "192.0.2.9")
check("falscher Token aus dem Netz faellt auf die Sitzung durch (401)",
      code == 401, code or rolle)

rolle, code = versuch("falscher-token", "127.0.0.1")
check("falscher Token ueber Loopback ebenso", code == 401, code or rolle)

rolle, code = versuch("", "192.0.2.9")
check("gar kein Token ergibt 401", code == 401, code or rolle)

# ----------------------------------------------------------------------
print("--- Gegenprobe zur Aussagekraft ---")
# Wenn der richtige Token ueber Loopback NICHT durchkaeme, wuerde oben
# alles Moegliche gruen bleiben, ohne dass die Regel greift. Deshalb hier
# ausdruecklich beides nebeneinander.
rolle_innen, _ = versuch(KEY, "127.0.0.1")
_, code_aussen = versuch(KEY, "192.0.2.9")
check("innen angenommen UND aussen abgewiesen - beides zusammen",
      rolle_innen == main.Role.admin and code_aussen == 403,
      f"innen={rolle_innen} aussen={code_aussen}")

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
