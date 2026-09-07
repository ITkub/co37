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

# Das Bild kommt fertig aus dem Backend und steht im Antwortkoerper, nicht
# hinter einer eigenen Adresse: eine Route /totp/qr?secret=... haette das
# Geheimnis in jede Zugriffsliste zwischen Browser und Server geschrieben.
#
# Geprueft wird, dass darin auch WIRKLICH die ausgelieferte Adresse steckt.
# Verglichen wird gegen den Encoder selbst - ob der stimmt, entscheidet
# tests/qr-test.py gegen zwei fremde Umsetzungen. Hier geht es nur um die
# Frage, ob die Route das Richtige hineingesteckt hat: ein Bild vom
# falschen Text saehe genauso aus.
import qrsvg  # noqa: E402
check("ein QR-Bild liegt bei", (res.get("qr_svg") or "").startswith("<svg "),
      (res.get("qr_svg") or "")[:40])
check("und es zeigt genau die ausgelieferte Adresse",
      res.get("qr_svg") == qrsvg.svg(res.get("otpauth", ""), modul=5, rand=4))
check("das Geheimnis steht in keiner Adresse, nur im Antwortkoerper",
      "secret=" not in str(res.get("qr_svg", "")))

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

# Und die Meldung ist hier ABSICHTLICH eine andere als beim App-Code.
#
# Beim App-Code kann der Server "bereits verwendet" sagen: er merkt sich
# den zuletzt benutzten Zeitschritt. Ein Wiederherstellungscode dagegen
# wird beim Einloesen aus der Liste geloescht, und gespeichert sind
# ohnehin nur Hashes - danach ist er nicht von einem erfundenen Code zu
# unterscheiden.
#
# Das liesse sich "glattziehen", indem man die Hashes verbrauchter Codes
# aufhebt. Genau das soll nicht passieren: es wuerde jemandem, der einen
# alten Code in die Finger bekommen hat, bestaetigen, dass er echt war.
# "Code falsch" ist die karge, aber richtige Auskunft.
check("und die Meldung verraet nicht, dass es mal ein echter war",
      "bereits verwendet" not in str(res), res)

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
# 403, nicht 401. Der Unterschied ist im Betrieb der zwischen "die
# Meldung steht am Formular" und "der Benutzer ist abgemeldet": die
# Oberflaeche wertet 401 global als abgelaufene Sitzung. Gefunden am
# 2026-09-07 im Handtest - Passwort und Code waren richtig eingetippt,
# ein Feld hatte einen Tippfehler, und CO-37 warf den Benutzer hinaus
# mit der Begruendung "Sitzung abgelaufen". Die stimmte nicht.
code, res = call("/api/v1/me/totp/off",
                 {"password": "falsches-passwort", "code": totp.code(GEHEIM)},
                 hdr=SITZUNG)
check("ohne richtiges Passwort kein Abschalten", code == 403, code)
check("und zwar mit 403 - 401 wuerde die Sitzung wegwerfen",
      code != 401, code)
code, res = call("/api/v1/me/totp/off",
                 {"password": PW, "code": "000000"}, hdr=SITZUNG)
check("ohne richtigen Code ebenfalls nicht", code == 403, code)

# Und die Sitzung muss das ueberlebt haben - sonst waere der Statuscode
# zwar richtig und der Schaden derselbe.
_c, _r = call("/api/v1/me", hdr=SITZUNG)
check("die Sitzung gilt nach den Fehlversuchen weiter",
      _c == 200 and _r.get("username") == NAME, (_c, _r))

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

# ----------------------------------------------------------------------
# Und jetzt der Teil, an dem der Schalter haette Zierde bleiben koennen
# ----------------------------------------------------------------------
# Die erste Fassung hat die Pflicht nur an einer Stelle geprueft: beim
# ABSCHALTEN des eigenen zweiten Faktors. Wirkung war damit, dass niemand
# seinen loswerden konnte, der einen hatte - und ein Administrator, der
# nie einen eingerichtet hatte, war von der "Pflicht" ueberhaupt nicht
# betroffen. Genau der aber ist gemeint.
#
# Gemessen wird deshalb an einem ZWEITEN Administrator ohne zweiten
# Faktor: er muss an allem scheitern, ausser an dem, was er zum
# Einrichten braucht.
print()
print("--- Die Pflicht trifft, wer sie noch nicht erfuellt ---")
ANAME, APW = "totpadm", "totpadm-passwort-lang"
call("/api/v1/users", {"username": ANAME, "password": APW, "role": "admin"},
     hdr=adm)
_c, _r = call("/api/v1/login", {"username": ANAME, "password": APW})
a_ses = {"X-Session": _r.get("session", "")}
_c, _r = call("/api/v1/me/totp/start", {}, hdr=a_ses)
A_GEHEIM = _r.get("secret", "")
check("der zweite Administrator richtet sie ein",
      call("/api/v1/me/totp/confirm", {"code": totp.code(A_GEHEIM)},
           hdr=a_ses)[0] == 200)

code, res = call("/api/v1/mfa-policy", {"required_for_admins": True},
                 hdr=a_ses)
check("und kann sie damit zur Pflicht machen",
      code == 200 and res.get("required_for_admins") is True, (code, res))

try:
    # Das Konto des Geruests ist Administrator OHNE zweiten Faktor - genau
    # der Fall, um den es geht.
    code, res = call("/api/v1/hosts", hdr=adm)
    check("ein Administrator ohne zweiten Faktor kommt an keine Route mehr",
          code == 403, (code, res))
    code, res = call("/api/v1/users", hdr=adm)
    check("auch nicht an die Benutzerverwaltung", code == 403, code)
    code, res = call("/api/v1/packages/co37-agent.msi", hdr=adm)
    check("und nicht an die Paketroute, die die Anmeldung selbst feststellt",
          code == 403, code)

    code, res = call("/api/v1/me", hdr=adm)
    check("aber /me bleibt erreichbar - sonst wuesste die Oberflaeche "
          "nichts", code == 200, code)
    check("und sagt, dass die Einrichtung ansteht",
          res.get("mfa_setup_required") is True, res)
    check("das Konto mit zweitem Faktor merkt nichts davon",
          call("/api/v1/hosts", hdr=a_ses)[0] == 200)
    code, res = call("/api/v1/me", hdr=a_ses)
    check("und meldet ihn als eingerichtet",
          res.get("mfa_enabled") is True and
          res.get("mfa_setup_required") is False, res)

    check("die Einrichtung selbst bleibt offen - sonst kaeme er nie heraus",
          call("/api/v1/me/totp/start", {}, hdr=adm)[0] == 200)

    # Abschalten waere der Weg an der Pflicht vorbei.
    code, res = call("/api/v1/me/totp/off",
                     {"password": APW, "code": totp.code(A_GEHEIM,
                                                         time.time() + totp.SCHRITT)},
                     hdr=a_ses)
    check("solange die Pflicht steht, laesst sie sich nicht abschalten",
          code == 400, (code, res))
finally:
    # Zurueckdrehen, komme was wolle: die Reihen danach laufen gegen
    # dasselbe Backend und mit demselben Administratorkonto. Bliebe die
    # Pflicht stehen, faellt alles Nachfolgende mit 403 um - und der
    # Grund staende dann hier, nicht dort.
    code, res = call("/api/v1/mfa-policy", {"required_for_admins": False},
                     hdr=a_ses)
    check("und laesst sich wieder abschalten",
          code == 200 and res.get("required_for_admins") is False, (code, res))
    check("danach arbeitet das Konto ohne zweiten Faktor wieder normal",
          call("/api/v1/hosts", hdr=adm)[0] == 200)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
