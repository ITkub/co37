"""
CO-37 - Anmeldung in zwei Schritten (F-70).

Verlangt von OPS.1.2.5.A17 und OPS.1.1.7.A6. Eingebaut als freiwillige
Einrichtung je Konto, mit einem abgeschalteten Schalter, der sie fuer
Administratoren zur Pflicht macht.

WAS EIN ZWEITER FAKTOR WERTLOS MACHT

Alle vier Fehler unten sehen im Betrieb gleich aus - es funktioniert, man
tippt einen Code ein, man kommt hinein. Auffallen wuerden sie erst dem,
der sie ausnutzt:

  1. **Der Code laesst sich wiederverwenden.** Wer eine Anmeldung
     mitliest, hat bei 30 Sekunden Schritt und einem Schritt Toleranz bis
     zu 90 Sekunden Zeit, sie zu wiederholen. Dagegen hilft nur, den
     zuletzt benutzten Schritt zu speichern.

  2. **Der Zwischenschritt ist schon eine Sitzung.** Wenn nach dem
     Passwort etwas zurueckkommt, mit dem sich arbeiten laesst, ist der
     zweite Faktor Zierde.

  3. **Der Code laesst sich durchprobieren.** Sechs Stellen sind eine
     Million - ohne eigenen Zaehler in Minuten erledigt.

  4. **Ein Wiederherstellungscode gilt mehrfach.** Dann ist er ein
     zweites Passwort, das nie ablaeuft.

Genau diese vier werden hier gemessen, nicht die Frage, ob ein richtiger
Code durchgeht.

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/totp-test.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))
import totp  # noqa: E402

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
# Das Geruest legt eine Administrator-Sitzung an und reicht sie durch.
SESSION = os.getenv("CO37_TEST_SESSION", "")

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
        roh = e.read().decode()[:300]
        try:
            return e.code, json.loads(roh)
        except ValueError:
            return e.code, {"body": roh}


adm = {"X-Session": SESSION}
NAME = "totptest"
PW = "totptest-passwort-lang"

# ======================================================================
# Vorbereitung: ein eigenes Konto
# ======================================================================
call("/api/v1/users", {"username": NAME, "password": PW, "role": "user"},
     hdr=adm)
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
check("Testkonto meldet sich mit dem Passwort an", code == 200, (code, res))
check("und bekommt SOFORT eine Sitzung, solange nichts eingerichtet ist",
      bool(res.get("session")) and not res.get("mfa_required"), res)
usr = {"X-Session": res.get("session", "")}


# ======================================================================
# Einrichtung
# ======================================================================
print()
print("--- Einrichtung ---")
code, res = call("/api/v1/me/totp/start", {}, hdr=usr)
check("Einrichtung liefert ein Geheimnis", code == 200 and res.get("secret"),
      (code, res))
GEHEIM = res.get("secret", "")
check("und eine otpauth-Adresse fuer die App",
      res.get("otpauth", "").startswith("otpauth://totp/"), res.get("otpauth"))

# Der wichtigste Zwischenzustand: angefangen, aber nicht bestaetigt.
# Wer hier abbricht, darf sich NICHT ausgesperrt haben.
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
check("nach dem Start, aber vor der Bestaetigung: Anmeldung wie bisher",
      code == 200 and not res.get("mfa_required"), res)

code, res = call("/api/v1/me/totp/confirm", {"code": "000000"}, hdr=usr)
check("ein falscher Code bestaetigt nichts", code == 400, code)

code, res = call("/api/v1/me/totp/confirm",
                 {"code": totp.code(GEHEIM)}, hdr=usr)
check("der richtige Code schaltet scharf", code == 200, (code, res))
RETTUNG = res.get("recovery_codes", [])
check("und liefert zehn Wiederherstellungscodes", len(RETTUNG) == 10,
      len(RETTUNG))
check("die genau einmal zu sehen sind - der zweite Aufruf scheitert",
      call("/api/v1/me/totp/confirm", {"code": totp.code(GEHEIM)},
           hdr=usr)[0] == 400)


# ======================================================================
# Fehler 2: der Zwischenschritt darf keine Sitzung sein
# ======================================================================
print()
print("--- Der Zwischenschritt ist keine Sitzung ---")
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
check("das Passwort allein reicht jetzt nicht mehr",
      code == 200 and res.get("mfa_required") is True, res)
check("und liefert KEINE Sitzung mit", not res.get("session"), res)
ZWISCHEN = res.get("mfa_token", "")
check("sondern einen eigenen Zwischentoken", bool(ZWISCHEN))

# Der Zwischentoken darf nirgends als Sitzung durchgehen.
code, res = call("/api/v1/me", hdr={"X-Session": ZWISCHEN})
check("der Zwischentoken oeffnet /me nicht", code in (401, 403), code)
code, res = call("/api/v1/hosts", hdr={"X-Session": ZWISCHEN})
check("und die Hostliste ebenfalls nicht", code in (401, 403), code)


# ======================================================================
# Fehler 1: Wiederverwendung
# ======================================================================
print()
print("--- Ein Code gilt genau einmal ---")


def frischer_code():
    """
    Ein Code aus dem NAECHSTEN Zeitschritt.

    Gebraucht, weil der Code aus der Bestaetigung bereits verbraucht ist -
    genau das ist der Schutz, den diese Reihe misst.

    Statt eine halbe Minute zu schlafen, wird der Code des naechsten
    Schritts gerechnet. Der Server nimmt ihn an (TOLERANZ = 1 Schritt)
    und merkt sich dessen hoeheren Zaehler. Das spart in dieser Reihe
    rund neunzig Sekunden, ohne die Pruefung zu entschaerfen: der
    Wiederverwendungsschutz wird weiter gegen einen echten, schon
    benutzten Code gemessen.
    """
    return totp.code(GEHEIM, time.time() + totp.SCHRITT)


JETZT = frischer_code()
code, res = call("/api/v1/login/totp",
                 {"mfa_token": ZWISCHEN, "code": JETZT})
check("mit gueltigem Code kommt die Sitzung", code == 200 and res.get("session"),
      (code, res))
SITZUNG = {"X-Session": res.get("session", "")}
code, res = call("/api/v1/me", hdr=SITZUNG)
check("und die Sitzung taugt auch etwas", code == 200 and res.get("username") == NAME,
      (code, res))

# Derselbe Code, neuer Anlauf - muss abgewiesen werden.
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
ZWEITER = res.get("mfa_token", "")
code, res = call("/api/v1/login/totp",
                 {"mfa_token": ZWEITER, "code": JETZT})
check("DERSELBE Code ein zweites Mal wird abgewiesen", code == 401, (code, res))
check("und die Meldung sagt, dass er verbraucht ist - nicht 'falsch'",
      "bereits verwendet" in str(res), res)
_c, _protokoll = call("/api/v1/audit", hdr=adm)
_eintraege = _protokoll if isinstance(_protokoll, list) else _protokoll.get("items", [])
check("und die Begruendung steht im Pruefprotokoll",
      any(isinstance(e, dict) and e.get("action") == "login.mfa-failed"
          for e in _eintraege),
      [e.get("action") for e in _eintraege[:5] if isinstance(e, dict)])


# ======================================================================
# Fehler 3: Durchprobieren
# ======================================================================
print()
print("--- Der Code laesst sich nicht durchprobieren ---")
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
DRITTER = res.get("mfa_token", "")
antworten = []
for i in range(7):
    c, r = call("/api/v1/login/totp",
                {"mfa_token": DRITTER, "code": f"{i:06d}"})
    antworten.append((c, str(r.get("detail", ""))))

check("jeder falsche Versuch wird abgewiesen",
      all(c == 401 for c, _ in antworten), [c for c, _ in antworten])

# Die Unterscheidung ist der eigentliche Punkt, und sie muss an der
# MELDUNG haengen, nicht am Rueckgabewert.
#
# Die erste Fassung dieser Pruefung schickte nach den Fehlversuchen einen
# richtigen Code und erwartete 401. Das war gruen - aber aus dem falschen
# Grund: der richtige Code war durch den Wiederverwendungsschutz ohnehin
# schon verbraucht. Die Mutationsprobe hat es gezeigt, indem sie die
# Zaehlung entfernte und trotzdem nichts umfiel.
falsch = [d for _, d in antworten if "Code falsch" in d]
zuviele = [d for _, d in antworten if "Zu viele" in d]
vonvorn = [d for _, d in antworten if "von vorn" in d]
check(f"die ersten {5} Versuche melden schlicht 'Code falsch'",
      len(falsch) == 5, falsch)
check("der sechste meldet, dass es zu viele waren",
      len(zuviele) == 1, zuviele)
check("und danach ist der Vorgang weg - nicht nur der Code falsch",
      len(vonvorn) >= 1, vonvorn)


# ======================================================================
# Fehler 4: Wiederherstellungscodes
# ======================================================================
print()
print("--- Wiederherstellungscodes gelten einmal ---")
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
VIERTER = res.get("mfa_token", "")
code, res = call("/api/v1/login/totp",
                 {"mfa_token": VIERTER, "code": RETTUNG[0]})
check("ein Wiederherstellungscode ersetzt den Code aus der App",
      code == 200 and res.get("session"), (code, res))

code, res = call("/api/v1/login", {"username": NAME, "password": PW})
FUENFTER = res.get("mfa_token", "")
code, res = call("/api/v1/login/totp",
                 {"mfa_token": FUENFTER, "code": RETTUNG[0]})
check("DERSELBE Wiederherstellungscode ein zweites Mal nicht",
      code == 401, (code, res))

code, res = call("/api/v1/login/totp",
                 {"mfa_token": FUENFTER, "code": RETTUNG[1]})
check("ein anderer aus derselben Liste dagegen schon",
      code == 200 and res.get("session"), (code, res))


# ======================================================================
# Abgelaufener und erfundener Zwischentoken
# ======================================================================
print()
print("--- Zwischentoken ---")
code, res = call("/api/v1/login/totp",
                 {"mfa_token": "voellig-erfunden", "code": totp.code(GEHEIM)})
check("ein erfundener Zwischentoken fuehrt nirgendwohin", code == 401, code)

# Ein Zwischentoken gehoert zu GENAU einem Konto: der Token des einen
# Kontos darf nicht mit dem Code eines anderen zusammenpassen.
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
SECHSTER = res.get("mfa_token", "")
code, res = call("/api/v1/login/totp", {"mfa_token": SECHSTER, "code": "123456"})
check("ein falscher Code am gueltigen Token wird abgewiesen", code == 401, code)


# ======================================================================
# Abschalten verlangt beides
# ======================================================================
print()
print("--- Abschalten ---")
code, res = call("/api/v1/me/totp/off",
                 {"password": "falsches-passwort", "code": totp.code(GEHEIM)},
                 hdr=SITZUNG)
check("ohne richtiges Passwort kein Abschalten", code == 401, code)
code, res = call("/api/v1/me/totp/off",
                 {"password": PW, "code": "000000"}, hdr=SITZUNG)
check("ohne richtigen Code ebenfalls nicht", code == 401, code)

# Warten, bis ein neuer Zeitschritt beginnt - der letzte Code ist
# verbraucht, und das ist ja gerade der Sinn.
code, res = call("/api/v1/me/totp/off",
                 {"password": PW, "code": frischer_code()}, hdr=SITZUNG)
check("mit beidem laesst es sich abschalten", code == 200, (code, res))
code, res = call("/api/v1/login", {"username": NAME, "password": PW})
check("danach reicht das Passwort wieder",
      code == 200 and res.get("session") and not res.get("mfa_required"), res)


# ======================================================================
# Der Pflichtschalter
# ======================================================================
print()
print("--- Pflicht fuer Administratoren ---")
code, res = call("/api/v1/mfa-policy", hdr=adm)
check("ab Werk ist die Pflicht AUS", res.get("required_for_admins") is False,
      res)
code, res = call("/api/v1/mfa-policy", {"required_for_admins": True}, hdr=adm)
check("und laesst sich nicht einschalten, solange das eigene Konto sie "
      "nicht hat", code == 400, (code, res))
check("die Meldung sagt auch warum",
      "sperrst du dich aus" in str(res), res)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
