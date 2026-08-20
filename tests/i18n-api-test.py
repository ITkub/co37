"""
CO-37 - Sprache am Konto und Vorgabe der Installation.

Die Rollentrennung dieser beiden Routen steht in roles-test.py, wo alle
Routen beieinander liegen. Hier geht es um das Verhalten.

Zwei Dinge sind wichtiger, als sie aussehen:

Eine unbekannte Sprachkennung muss abgewiesen werden. Was hier
durchrutscht, steht anschliessend in der Datenbank und fuehrt in der
Oberflaeche zu einem Woerterbuch, das es nicht gibt.

Und ein Konto, das nie etwas gewaehlt hat, muss NULL bleiben - nicht 'en'.
Sonst laesst sich "hat sich fuer Englisch entschieden" nicht mehr von "hat
sich nie entschieden" unterscheiden, und eine spaeter geaenderte Vorgabe
der Installation erreicht bestehende Konten nie.

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/i18n-api-test.py
"""
import json
import os
import urllib.error
import urllib.request

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
KEY = os.getenv("CO37_TEST_KEY", "t")

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def call(path, data=None, method=None, hdr=None):
    h = {"Content-Type": "application/json"}
    h.update(hdr or {})
    r = urllib.request.Request(
        B + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"body": e.read().decode()[:200]}


adm = {"X-API-Key": KEY}

# ----------------------------------------------------------------------
# Vorgabe der Installation
# ----------------------------------------------------------------------
code, res = call("/api/health")
check("health liefert eine Vorgabesprache", "default_language" in res, res)
check("Vorgabe ist ohne Zutun Englisch", res.get("default_language") == "en",
      res.get("default_language"))

code, res = call("/api/v1/settings/language", {"language": "de"}, hdr=adm)
check("Vorgabe laesst sich setzen", code == 200 and res.get("language") == "de",
      (code, res))

code, res = call("/api/health")
check("health meldet die neue Vorgabe", res.get("default_language") == "de",
      res.get("default_language"))

code, res = call("/api/v1/settings/language", {"language": "kl"}, hdr=adm)
check("unbekannte Sprache wird abgewiesen", code == 400, code)

code, res = call("/api/v1/settings/language", {"language": "de-DE"}, hdr=adm)
check("'de-DE' gilt nicht als 'de'", code == 400, code)

code, res = call("/api/health")
check("nach der Abweisung steht die alte Vorgabe noch",
      res.get("default_language") == "de", res.get("default_language"))

# Zuruecksetzen, damit nachfolgende Reihen eine unveraenderte Lage vorfinden.
call("/api/v1/settings/language", {"language": "en"}, hdr=adm)

# ----------------------------------------------------------------------
# Eigene Sprache
# ----------------------------------------------------------------------
code, res = call("/api/v1/me", hdr=adm)
check("me liefert das Feld language", "language" in res, res)
check("ohne Wahl steht dort nichts", res.get("language") is None,
      res.get("language"))

# Der API-Key hat kein Konto. Das ist kein Fehler, den jemand beheben
# muesste - die Oberflaeche merkt sich die Wahl dann nur im Cookie.
code, res = call("/api/v1/me/language", {"language": "de"}, hdr=adm)
check("API-Key: Wahl wird angenommen", code == 200, (code, res))
check("API-Key: aber nicht gespeichert", res.get("gespeichert") is False, res)

code, res = call("/api/v1/me/language", {"language": "kl"}, hdr=adm)
check("unbekannte Sprache am Konto wird abgewiesen", code == 400, code)

# ----------------------------------------------------------------------
# Am echten Konto
# ----------------------------------------------------------------------
call("/api/v1/users", {"username": "sprachtest",
                       "password": "sprachtest-passwort", "role": "user"},
     hdr=adm)
code, res = call("/api/v1/login", {"username": "sprachtest",
                                   "password": "sprachtest-passwort"})
usr = {"X-Session": res.get("session", "")} if code == 200 else {}
check("Testkonto kann sich anmelden", code == 200, code)

code, res = call("/api/v1/me", hdr=usr)
check("neues Konto hat keine Sprache gewaehlt", res.get("language") is None,
      res.get("language"))

code, res = call("/api/v1/me/language", {"language": "de"}, hdr=usr)
check("Konto speichert die Wahl",
      code == 200 and res.get("gespeichert") is True, (code, res))

code, res = call("/api/v1/me", hdr=usr)
check("und me liefert sie zurueck", res.get("language") == "de",
      res.get("language"))

code, res = call("/api/v1/me/language", {"language": "en"}, hdr=usr)
code, res = call("/api/v1/me", hdr=usr)
check("Wahl laesst sich wieder aendern", res.get("language") == "en",
      res.get("language"))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
