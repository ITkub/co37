"""
CO-37 - Eingabegrenzen der Schnittstelle (F-51 bis F-57).

Alle sieben Befunde stammen aus einem Fuzzing-Lauf am 2026-09-03:
schemathesis 4.25.2 gegen das eigene OpenAPI-Schema, ein Testbackend im
Container, rund 24000 erzeugte Faelle in mehreren Durchgaengen
(angemeldet, mit Agent-Token, und ganz ohne Anmeldung).

Es war der erste Fuzzing-Lauf ueberhaupt gegen CO-37 - im Pruefdokument
stand er seit Runde 4 als "angeboten, noch nicht gemacht". Er hat in
achtzig Sekunden gefunden, was fuenf Prueframden nicht gefunden haben,
weil sie alle den Code gelesen haben statt ihn zu beschiessen.

WAS DIESE REIHE PRUEFT

  F-51  Ein ausdrueckliches null in einem PATCH.
        PATCH /hosts/{id} {"patch_days": null} lieferte 500 - UND schrieb
        die null trotzdem, weil der Fehler erst beim Bauen der Antwort
        auftrat, nach dem commit(). Danach lieferte GET /hosts dauerhaft
        500, ueber diese eine Zeile. Die Oberflaeche, mit der man das
        haette heilen koennen, war damit selbst tot.

  F-52  Ganzzahlen ohne Obergrenze. Python kennt keine, SQLite schon:
        host_id=5446878413911239950336 gab 500 statt 404. Ueber acht
        Routen, 212 Vorfaelle im ersten Lauf - und ueber
        POST /agent/report {"job_id": <riesig>} auch fuer jeden
        verwalteten Host, nicht nur fuer Angemeldete.

  F-53  limit=-1 auf /api/v1/jobs. SQLite liest ein negatives LIMIT als
        "keine Grenze" - der Wert hob genau die Schranke auf, die F-38
        eingezogen hat. Ohne Fehler, ohne Meldung.

  F-54  Nicht-ASCII in Checkmk-Benutzer oder -Secret. httpx kodiert
        Kopfzeilen als ASCII; ein Umlaut gab 500 aus der Route heraus.

  F-55  Netzfehler von httpx. Ein nicht erreichbarer Checkmk-Server -
        der haeufigste Fall beim Einrichten ueberhaupt - kam als
        ConnectError heraus und wurde zu 500.

  F-56  Keine Obergrenze fuer den Anfragekoerper. 200 MB an die
        ABSICHTLICH offene Route /agent/enroll wurden vollstaendig
        gepuffert, bevor irgendeine Pruefung griff; der Prozess stand bei
        1,1 GB. Ohne Zugangsdaten ausloesbar.

  F-57  Keine Laengengrenze auf den Schreibwegen der Oberflaeche.
        display_name mit 400 MB ging durch und blieb in der Datenbank
        stehen - die war danach 500 MB gross.

WAS SIE NICHT PRUEFT: das Fuzzing selbst. schemathesis laeuft hier nicht
mit; die Reihe prueft die BEHOBENEN Faelle als feste Beispiele. Ein neuer
Fuzzing-Lauf gehoert von Hand angestossen, wenn sich die Schnittstelle
aendert - der Befehl steht unten im Kommentar.

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/eingaben-test.py

Fuzzing-Lauf von Hand (Backend auf 8123, Sitzung in $S, Agent-Token in $T):

    # openapi.json aus dem App-Objekt erzeugen - die HTTP-Route ist ab
    # Werk aus (B-01). Braucht die Backend-Abhaengigkeiten im Pfad.
    python3 -c "import json,sys; sys.path.insert(0,'backend'); import main; \\
      json.dump(main.app.openapi(), open('openapi.json','w'))"

    schemathesis run ./openapi.json --url http://127.0.0.1:8123 \\
      -H "X-Session: $S" -H "X-Agent-Token: $T" \\
      --checks not_a_server_error --continue-on-failure
"""
import ast
import re
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
ADM = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def call(path, data=None, method=None, hdr=None, roh=None):
    h = {"Content-Type": "application/json"}
    h.update(hdr or {})
    koerper = roh if roh is not None else (
        json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(
        B + path, data=koerper, headers=h,
        method=method or ("POST" if koerper is not None else "GET"))
    try:
        body = urllib.request.urlopen(r, timeout=120).read()
        return 200, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def ansage(laenge: int, gesendet: bytes,
           pfad: str = "/api/v1/agent/enroll", kopf=None):
    """
    Kuendigt einen Koerper der Laenge 'laenge' an und schickt nur
    'gesendet' davon.

    Ueber einen rohen Socket, nicht ueber urllib: das Backend antwortet
    auf die angekuendigte Laenge hin sofort mit 413 und liest den Koerper
    gar nicht erst - genau das ist der Sinn der Sache. urllib schreibt
    aber stur zu Ende und sieht dabei nur ein "Connection reset by peer",
    bevor es die Antwort liest. curl und jeder Browser lesen nebenher und
    bekommen die 413; hier wird derselbe Ablauf von Hand nachgestellt.
    """
    import socket
    from urllib.parse import urlparse
    ziel = urlparse(B)
    s = socket.create_connection((ziel.hostname, ziel.port or 80), timeout=30)
    zeilen = [f"POST {pfad} HTTP/1.1", f"Host: {ziel.hostname}",
              "Content-Type: application/json", f"Content-Length: {laenge}"]
    zeilen += [f"{a}: {b}" for a, b in (kopf or {}).items()]
    s.sendall(("\r\n".join(zeilen) + "\r\n\r\n").encode() + gesendet)
    antwort = b""
    s.settimeout(5)
    try:
        # Ueber die Kopfzeilen hinaus weiterlesen: der Grund steht im
        # Koerper, und die erste Fassung dieser Pruefung hoerte genau
        # davor auf und suchte den Wortlaut in den Kopfzeilen.
        while True:
            teil = s.recv(8192)
            if not teil:
                break
            antwort += teil
            if b"\r\n\r\n" in antwort and antwort.split(b"\r\n\r\n", 1)[1]:
                break
    except OSError:
        pass
    s.close()
    return antwort


# ======================================================================
# Testhost anlegen
# ======================================================================
code, _ = call("/api/v1/agent/enroll", {
    "hostname": "EINGABE-GRENZ-01", "os_type": "linux",
})
check("Testhost angemeldet", code == 200, code)
hosts = call("/api/v1/hosts", hdr=ADM)[1]
hid = next(h["id"] for h in hosts if h["hostname"] == "EINGABE-GRENZ-01")
call(f"/api/v1/hosts/{hid}/approve", {}, hdr=ADM)

code, bereich = call("/api/v1/areas", {"name": "Eingabegrenzen"}, hdr=ADM)
check("Testbereich angelegt", code == 200, code)
aid = bereich.get("id")


# ======================================================================
# F-51 - ein ausdrueckliches null in einem PATCH
# ======================================================================
print()
print("--- Ein ausdrueckliches null im PATCH (F-51) ---")

# Die Felder, die in der Antwortform KEIN None tragen: null muss 422
# geben. Die Auswahl steht hier ausgeschrieben und nicht abgeleitet - der
# Code leitet ab, die Pruefung zaehlt auf. Waeren beide abgeleitet,
# pruefte sich die Ableitung selbst.
for feld in ("patch_days", "tags", "checkmk_hosts", "downtime_minutes",
             "patch_enabled", "patch_auto_reboot", "checkmk_downtime_all",
             "patch_grace_hours"):
    code, res = call(f"/api/v1/hosts/{hid}", {feld: None}, method="PATCH", hdr=ADM)
    check(f"Host {feld}=null wird abgewiesen", code == 422, (code, res))

# Und die, bei denen null "loeschen" heisst: die muessen weiterhin gehen.
# Ohne diese Haelfte waere die Pruefung mit einem pauschalen "null immer
# 422" zufrieden - und die Oberflaeche koennte einen Anzeigenamen nicht
# mehr entfernen.
for feld in ("display_name", "maintenance_window", "patch_time"):
    code, res = call(f"/api/v1/hosts/{hid}", {feld: None}, method="PATCH", hdr=ADM)
    check(f"Host {feld}=null bleibt erlaubt (heisst loeschen)",
          code == 200 and res.get(feld) is None, (code, res.get(feld)))

for feld in ("name", "checkmk_downtime_all", "patch_days"):
    code, res = call(f"/api/v1/areas/{aid}", {feld: None}, method="PATCH", hdr=ADM)
    check(f"Bereich {feld}=null wird abgewiesen", code == 422, (code, res))

code, res = call(f"/api/v1/areas/{aid}", {"patch_time": None}, method="PATCH", hdr=ADM)
check("Bereich patch_time=null bleibt erlaubt", code == 200, (code, res))

# Der eigentliche Schaden war nicht der 500, sondern was danach kam.
check("die Hostliste antwortet danach noch",
      call("/api/v1/hosts", hdr=ADM)[0] == 200)
check("die Bereichsliste antwortet danach noch",
      call("/api/v1/areas", hdr=ADM)[0] == 200)

# Und das gewoehnliche Bearbeiten muss unveraendert laufen.
code, res = call(f"/api/v1/hosts/{hid}", {
    "display_name": "  Buchhaltung  ", "patch_days": ["MO", "DI"],
}, method="PATCH", hdr=ADM)
check("gewoehnliches PATCH laeuft weiter",
      code == 200 and res.get("patch_days") == ["MO", "DI"], (code, res))
check("und der Anzeigename wird weiterhin beschnitten",
      res.get("display_name") == "Buchhaltung", res.get("display_name"))

# Die Ableitung selbst: sie darf nicht versehentlich alles erlauben.
quelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
_baum = ast.parse(quelle)
_lesemodelle = {k.name: k for k in ast.walk(_baum)
                if isinstance(k, ast.ClassDef) and k.name in ("HostRead", "AreaRead")}
check("HostRead und AreaRead sind auffindbar", len(_lesemodelle) == 2)
_optional = {
    name: {ast.unparse(s.target) for s in kl.body
           if isinstance(s, ast.AnnAssign) and "Optional" in ast.unparse(s.annotation)}
    for name, kl in _lesemodelle.items()
}
check("HostRead hat nicht nur optionale Felder",
      0 < len(_optional["HostRead"]) < 15, sorted(_optional["HostRead"]))
check("patch_days ist in HostRead NICHT optional",
      "patch_days" not in _optional["HostRead"])
check("patch_time ist in HostRead optional",
      "patch_time" in _optional["HostRead"])


# ======================================================================
# F-52 - Ganzzahlen ohne Obergrenze
# ======================================================================
print()
print("--- Ganzzahlen ohne Obergrenze (F-52) ---")

RIESIG = 5446878413911239950336          # groesser als 2**63-1
for pfad in (f"/api/v1/hosts/{RIESIG}/updates",
             f"/api/v1/hosts/{RIESIG}/downtime",
             f"/api/v1/jobs/{RIESIG}/log"):
    code, _ = call(pfad, hdr=ADM)
    check(f"GET {pfad.split('/')[-2]}/… mit riesiger Kennung -> 422",
          code == 422, code)

for pfad in (f"/api/v1/hosts/{RIESIG}/approve",
             f"/api/v1/hosts/{RIESIG}/reject",
             f"/api/v1/hosts/{RIESIG}/reset-token"):
    code, _ = call(pfad, {}, hdr=ADM)
    check(f"POST …/{pfad.split('/')[-1]} mit riesiger Kennung -> 422",
          code == 422, code)

code, _ = call(f"/api/v1/areas/{RIESIG}", method="DELETE", hdr=ADM)
check("DELETE /areas/<riesig> -> 422", code == 422, code)

# Die Gegenprobe: eine Kennung, die es nicht gibt, muss weiterhin 404
# geben und nicht 422 - sonst haette die Grenze bloss einen Fehler durch
# einen anderen ersetzt.
code, _ = call("/api/v1/hosts/99999999/updates", hdr=ADM)
check("eine nur unbekannte Kennung gibt weiterhin 404 oder 200",
      code in (200, 404), code)
code, res = call(f"/api/v1/hosts/{hid}/updates", hdr=ADM)
check("und die echte Kennung liefert Daten", code == 200, code)

# Null und negativ gehoeren ebenfalls abgewiesen.
for kennung in (0, -1):
    code, _ = call(f"/api/v1/hosts/{kennung}/updates", hdr=ADM)
    check(f"Kennung {kennung} wird abgewiesen", code == 422, code)

# Ueber den Koerper, nicht nur ueber den Pfad - das war der Weg, den ein
# uebernommener Agent haette gehen koennen.
code, _ = call("/api/v1/hosts/order", {"ids": [RIESIG]}, hdr=ADM)
check("hosts/order mit riesiger Kennung -> 422", code == 422, code)
code, _ = call(f"/api/v1/hosts/{hid}/downtime", {"minutes": RIESIG}, hdr=ADM)
check("downtime mit riesiger Minutenzahl -> 422", code == 422, code)

_koerpermodelle = {k.name: k for k in ast.walk(_baum) if isinstance(k, ast.ClassDef)}
for klasse, feld in (("AgentJobReport", "job_id"), ("AgentJobLog", "job_id")):
    kl = _koerpermodelle.get(klasse)
    art = next((ast.unparse(s.annotation) for s in (kl.body if kl else [])
                if isinstance(s, ast.AnnAssign) and ast.unparse(s.target) == feld), "")
    check(f"{klasse}.{feld} ist begrenzt", "Kennung" in art, art)


# ======================================================================
# F-53 - ein negatives limit hob die Obergrenze auf
# ======================================================================
print()
print("--- Negatives limit (F-53) ---")

for _ in range(6):
    call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "scan"}, hdr=ADM)

code, drei = call("/api/v1/jobs?limit=3", hdr=ADM)
check("limit=3 liefert hoechstens 3", code == 200 and len(drei) <= 3,
      (code, len(drei) if isinstance(drei, list) else drei))
code, _ = call("/api/v1/jobs?limit=-1", hdr=ADM)
check("limit=-1 wird abgewiesen", code == 422, code)
code, _ = call("/api/v1/jobs?limit=0", hdr=ADM)
check("limit=0 wird abgewiesen", code == 422, code)
code, _ = call("/api/v1/jobs?limit=-116933759148049384115011584", hdr=ADM)
check("ein riesiges negatives limit ebenfalls", code == 422, code)
code, viele = call("/api/v1/jobs?limit=500", hdr=ADM)
check("limit=500 geht weiter", code == 200, code)

# Warum das kein reiner Schoenheitsfehler war: SQLite behandelt ein
# negatives LIMIT als "keine Grenze". Hier nachgestellt, damit die
# Begruendung im Kommentar nicht nur behauptet ist.
import sqlite3  # noqa: E402
_db = sqlite3.connect(":memory:")
_db.execute("create table t(x)")
_db.executemany("insert into t values (?)", [(i,) for i in range(50)])
check("SQLite: LIMIT -1 liefert wirklich alles",
      len(_db.execute("select * from t limit -1").fetchall()) == 50)
check("SQLite: LIMIT 5 liefert fuenf",
      len(_db.execute("select * from t limit 5").fetchall()) == 5)


# ======================================================================
# F-54 und F-55 - die Checkmk-Route
# ======================================================================
print()
print("--- Checkmk: Kopfzeile und Netzfehler (F-54, F-55) ---")

# Kein erreichbarer Checkmk-Server noetig: beide Faelle scheitern vor
# oder in der Verbindung, und genau das ist der geprueffte Punkt.
code, res = call("/api/v1/checkmk/config", {
    "url": "http://127.0.0.1:1", "site": "prod",
    "user": "pruefer", "secret": "geheim", "verify_ssl": True,
}, hdr=ADM)
check("ein nicht erreichbarer Server gibt 400, nicht 500", code == 400,
      (code, res))
check("und die Meldung nennt den Grund",
      "erreichbar" in str(res).lower() or "fehlgeschlagen" in str(res).lower(),
      res)

code, res = call("/api/v1/checkmk/config", {
    "url": "http://127.0.0.1:1", "site": "prod",
    "user": "prüfer", "secret": "geheim", "verify_ssl": True,
}, hdr=ADM)
check("ein Umlaut im Benutzer gibt 400, nicht 500", code == 400, (code, res))
check("und sagt, woran es liegt", "ASCII" in str(res), res)

code, res = call("/api/v1/checkmk/config", {
    "url": "http://127.0.0.1:1", "site": "prod",
    "user": "pruefer", "secret": "ge\r\nheim", "verify_ssl": True,
}, hdr=ADM)
check("ein Zeilenumbruch im Secret ebenfalls", code == 400, (code, res))

# Gegenprobe an der Quelle: der Aufbau des Clients muss IM try stehen.
# Stuende er davor, waere F-54 wieder ein 500 - und eine Pruefung, die nur
# die Meldung ansieht, bliebe trotzdem gruen, solange irgendwo ein 400
# entsteht.
_i = quelle.index("def set_checkmk_config")
_block = quelle[_i:_i + 4000]
_probe = _block.index("probe = CheckmkClient")
_try = _block.rindex("try:", 0, _probe)
check("der Client wird innerhalb des try aufgebaut",
      _block[_try:_probe].strip() == "try:", _block[_try:_probe][:120])

_cmk = (WURZEL / "backend" / "checkmk.py").read_text(encoding="utf-8")
check("checkmk.py faengt httpx.HTTPError ab", "except httpx.HTTPError" in _cmk)
check("und wandelt es in einen CheckmkError",
      "CheckmkError" in _cmk.split("except httpx.HTTPError")[1][:300])


# ======================================================================
# F-56 - Obergrenze fuer den Anfragekoerper
# ======================================================================
print()
print("--- Obergrenze fuer den Anfragekoerper (F-56) ---")

# Die Vorgabe seit F-62. /agent/enroll ist keine Ausnahme.
GRENZE = 1024 * 1024
zu_gross = b'{"hostname":"' + b"x" * (GRENZE + 4096) + b'","os_type":"linux"}'


# Ueber die offene Route, ohne jede Anmeldung - das war der Kern des
# Befunds. 200 MB kamen vorher vollstaendig in den Speicher.
_antwort = ansage(len(zu_gross), zu_gross[:2048])
check("eine zu gross angekuendigte Anfrage -> 413",
      b"413" in _antwort.split(b"\r\n")[0], _antwort[:120])
check("und die Meldung sagt, dass es die Groesse war",
      b"gross" in _antwort.lower(), _antwort[-200:])
check("der Koerper wird dafuer gar nicht erst gelesen",
      b"413" in _antwort.split(b"\r\n")[0], "nur 2 KB von 32 MB geschickt")

# Gegenprobe zur Ansage: eine ehrliche kleine Laenge muss durchkommen.
_klein = json.dumps({"hostname": "EINGABE-GRENZ-03",
                     "os_type": "linux"}).encode()
_antwort = ansage(len(_klein), _klein)
check("eine ehrlich angekuendigte kleine Anfrage geht durch",
      b"200" in _antwort.split(b"\r\n")[0], _antwort[:120])

# Ohne Content-Length, also chunked: die Grenze darf sich nicht durch das
# Weglassen einer Kopfzeile umgehen lassen. urllib schickt ohne
# Content-Length automatisch chunked.
class _Strom:
    def __init__(self, daten):
        self._daten = daten
        self._pos = 0

    def read(self, n=-1):
        if self._pos >= len(self._daten):
            return b""
        ende = len(self._daten) if n is None or n < 0 else self._pos + n
        stueck = self._daten[self._pos:ende]
        self._pos = ende
        return stueck


_r = urllib.request.Request(
    B + "/api/v1/agent/enroll", data=_Strom(zu_gross), method="POST",
    headers={"Content-Type": "application/json",
             "Transfer-Encoding": "chunked"})
try:
    urllib.request.urlopen(_r, timeout=120)
    _code = 200
except urllib.error.HTTPError as _e:
    _code = _e.code
except Exception as _e:  # noqa: BLE001
    _code = f"{type(_e).__name__}"
check("auch ohne Content-Length (chunked) -> 413", _code == 413, _code)

# Gegenprobe: knapp darunter muss durchgehen, sonst waere die Grenze
# einfach "alles abweisen".
# Deutlich unter der Vorgabe - seit F-62 liegt die bei 1 MiB, ein
# Megabyte waere also genau der Grenzfall und sagte nichts.
knapp = json.dumps({"hostname": "EINGABE-GRENZ-02", "os_type": "linux",
                    "os_version": "x" * (200 * 1024)}).encode()
code, _ = call("/api/v1/agent/enroll", roh=knapp)
check("eine gewoehnlich grosse Anmeldung geht weiterhin durch",
      code == 200, code)

check("die Grenze steht als Konstante im Quelltext", "KOERPER_MAX" in quelle)
check("und laesst sich fuer den Betrieb setzen", "CO37_MAX_BODY" in quelle)


# ======================================================================
# F-57 - Laengengrenzen auf den Schreibwegen der Oberflaeche
# ======================================================================
print()
print("--- Laengen aus der Oberflaeche (F-57) ---")

code, res = call(f"/api/v1/hosts/{hid}", {"display_name": "x" * 120},
                 method="PATCH", hdr=ADM)
check("120 Zeichen Anzeigename gehen", code == 200, code)
code, _ = call(f"/api/v1/hosts/{hid}", {"display_name": "x" * 121},
               method="PATCH", hdr=ADM)
check("121 Zeichen werden abgewiesen", code == 422, code)
code, _ = call(f"/api/v1/hosts/{hid}", {"display_name": "x" * 200000},
               method="PATCH", hdr=ADM)
check("200000 Zeichen ebenfalls", code == 422, code)
code, _ = call(f"/api/v1/hosts/{hid}", {"tags": ["x" * 121]},
               method="PATCH", hdr=ADM)
check("ein zu langer Eintrag in einer Liste wird abgewiesen", code == 422, code)
code, _ = call(f"/api/v1/hosts/{hid}", {"tags": ["a"] * 501},
               method="PATCH", hdr=ADM)
check("eine zu lange Liste wird abgewiesen", code == 422, code)
code, _ = call(f"/api/v1/hosts/{hid}", {"tags": ["prod", "linux"]},
               method="PATCH", hdr=ADM)
check("gewoehnliche Merkmale gehen weiter", code == 200, code)
code, _ = call(f"/api/v1/areas/{aid}", {"name": "x" * 121},
               method="PATCH", hdr=ADM)
check("ein zu langer Bereichsname wird abgewiesen", code == 422, code)

# ======================================================================
# Eine Grenze je Route (F-62)
# ======================================================================
print()
print("--- Eine Koerpergrenze je Route (F-62) ---")

# 32 MiB fuer alles war zu grosszuegig: der Koerper wird vollstaendig
# gelesen und ausgewertet, BEVOR eine Route etwas prueft - vor der
# Anmeldung, vor dem Agent-Token, sogar vor der Drosselung. Gemessen am
# 2026-09-04: eine bereits GESPERRTE Adresse kostete mit 32 MiB Koerper
# weiterhin 323 ms je Anfrage, mit kurzem Koerper 3 ms. Die Drosselung
# begrenzt Versuche, nicht Arbeit - sie kann es gar nicht, weil sie erst
# laeuft, wenn schon gelesen wurde.
VORGABE = 1024 * 1024


def gross(pfad, bytes_, kopf=None):
    """Schickt einen Koerper der angegebenen Groesse und meldet den Code."""
    return call(pfad, roh=b'{"x":"' + b"y" * bytes_ + b'"}', hdr=kopf)[0]


# Die Vorgabe gilt fuer eine gewoehnliche Route.
check("knapp unter der Vorgabe geht durch",
      gross("/api/v1/login", VORGABE // 2) in (200, 401, 422),
      gross("/api/v1/login", VORGABE // 2))
# Die beiden Faelle ueber der Grenze ueber einen rohen Socket, nicht ueber
# urllib. Sie standen bis zum 2026-09-04 als gross(...) == 413 hier und
# waren damit von der Zeit abhaengig: der Server antwortet mit 413 und
# hoert auf zu lesen, waehrend urllib den Koerper noch schreibt - dann
# sieht urllib nur den Verbindungsabbruch und meldet 0. Bei etwa jedem
# vierten Lauf. Genau derselbe Grund, aus dem ansage() ueberhaupt
# entstanden ist; zwei Zeilen weiter unten war er schon beruecksichtigt.
#
# Eine Pruefung, die manchmal grundlos rot ist, wird nach dem dritten Mal
# nicht mehr gelesen.
_a = ansage(VORGABE + 8192, b'{"x":"y"}', "/api/v1/login")
check("darueber wird mit 413 abgewiesen",
      b"413" in _a.split(b"\r\n")[0], _a[:100])
_a = ansage(VORGABE + 8192, b'{"x":"y"}', "/api/v1/agent/enroll")
check("auch die offene Anmelderoute des Agents",
      b"413" in _a.split(b"\r\n")[0], _a[:100])

# Und die Ausnahmen, die eine groessere brauchen. Ohne diese Haelfte
# waere die Reihe mit "alles abweisen" zufrieden - und ein Scan mit 5000
# Paketen oder ein Systemupdate kaeme nie wieder durch.
_agt = {"X-Agent-Token": "gibt-es-nicht"}
check("ein Scan darf mehr als die Vorgabe schicken",
      gross("/api/v1/agent/scan-result", 2 * 1024 * 1024, _agt) != 413,
      gross("/api/v1/agent/scan-result", 2 * 1024 * 1024, _agt))
# Ueber einen rohen Socket: bei mehreren MiB antwortet der Server, waehrend
# der Client noch schreibt, und urllib sieht nur den Verbindungsabbruch.
# Genau daran ist die erste Fassung dieser Zeile gescheitert.
_a = ansage(9 * 1024 * 1024, b'{"x":"y"}', "/api/v1/agent/scan-result")
check("aber auch dort gibt es eine Grenze",
      b"413" in _a.split(b"\r\n")[0], _a[:100])
_a = ansage(70 * 1024 * 1024, b'{"x":"y"}', "/api/v1/update/upload")
check("und selbst das Systemupdate ist nicht unbegrenzt",
      b"413" in _a.split(b"\r\n")[0], _a[:100])
check("und das Systemupdate darf am meisten",
      gross("/api/v1/update/upload", 8 * 1024 * 1024, ADM) != 413,
      gross("/api/v1/update/upload", 8 * 1024 * 1024, ADM))

# Die Ausnahmen muessen an PFADEN haengen, die es wirklich gibt - eine
# Ausnahme auf einen Tippfehler waere eine Grenze, die niemand bemerkt.
_pfade = set()
for _knoten in ast.walk(_baum):
    if isinstance(_knoten, ast.Call):
        for _d in getattr(_knoten.func, "attr", "") and [_knoten] or []:
            pass
for _z in quelle.splitlines():
    _tr = re.findall(r'@app\.\w+\("(/api/[^"]+)"', _z)
    _pfade.update(_tr)
_ausnahmen = re.findall(r'^\s*"(/api/v1/[^"]+)":\s*\d', quelle, re.M)
check("die Ausnahmen sind auffindbar", len(_ausnahmen) >= 2, _ausnahmen)
check("jede Ausnahme zeigt auf eine wirklich vorhandene Route",
      all(a in _pfade for a in _ausnahmen),
      [a for a in _ausnahmen if a not in _pfade])

# Keine Laengengrenze auf dem Passwortfeld - und das ist Absicht. Die
# Verstaerkung bei scrypt verschwindet mit der kleineren Koerpergrenze
# von selbst (gemessen 236 ms bei 16 Zeichen, 232 ms bei 1 MiB), und eine
# Laengengrenze koennte jemanden mit einem sehr langen Passwort
# aussperren. Die Pruefung haelt die Entscheidung fest, damit sie nicht
# unbemerkt in die eine oder andere Richtung kippt.
check("das Passwortfeld traegt bewusst keine Laengengrenze",
      "password: str" in quelle.split("class LoginIn")[1][:200],
      quelle.split("class LoginIn")[1][:200])


# ======================================================================
# Laengen in den Meldungen des Agents
# ======================================================================
print()
print("--- Laengen in /agent/report und /agent/notice ---")

# Anders als bei der Oberflaeche wird hier GEKUERZT, nicht abgewiesen: ein
# 422 auf eine Auftragsmeldung liesse den Auftrag fuer immer auf "laeuft"
# stehen. Dieselbe Abwaegung wie beim Heartbeat (kurz()/FELD_MAX) und beim
# Scan (F-34).
_tok = call("/api/v1/agent/enroll", {
    "hostname": "EINGABE-GRENZ-04", "os_type": "linux"})[1].get("agent_token")
_hosts = call("/api/v1/hosts", hdr=ADM)[1]
_h4 = next((h["id"] for h in _hosts if h["hostname"] == "EINGABE-GRENZ-04"), None)
check("zweiter Testhost angemeldet", bool(_tok) and _h4 is not None)
call(f"/api/v1/hosts/{_h4}/approve", {}, hdr=ADM)
AGT = {"X-Agent-Token": _tok or ""}

code, res = call(f"/api/v1/hosts/{_h4}/jobs", {"job_type": "scan"}, hdr=ADM)
_jid = res.get("id")
check("Auftrag fuer die Meldung angelegt", code == 200 and _jid, (code, res))

code, _ = call("/api/v1/agent/report", {
    "job_id": _jid, "state": "done",
    "log": "x" * (300 * 1024),
    "result": {"gross": "y" * (200 * 1024)},
}, hdr=AGT)
check("eine sehr lange Meldung wird angenommen", code == 200, code)

_job = next((j for j in call("/api/v1/jobs?limit=500", hdr=ADM)[1]
             if j.get("id") == _jid), {})
check("das Protokoll ist gekuerzt",
      0 < len(_job.get("log") or "") <= 256 * 1024 + 32,
      len(_job.get("log") or ""))
check("und sagt, dass gekuerzt wurde",
      str(_job.get("log", "")).endswith("[gekuerzt]"),
      str(_job.get("log", ""))[-30:])
check("das Ergebnis ist nicht halb uebernommen, sondern vermerkt",
      (_job.get("result") or {}).get("gekuerzt") is True, _job.get("result"))

# Gegenprobe: eine normale Meldung muss unveraendert ankommen. Ohne das
# bestuende die Reihe auch bei "kuerzt immer alles auf null".
code, res = call(f"/api/v1/hosts/{_h4}/jobs", {"job_type": "scan"}, hdr=ADM)
_jid2 = res.get("id")
call("/api/v1/agent/report", {
    "job_id": _jid2, "state": "done", "log": "Alles in Ordnung",
    "result": {"updates": 3},
}, hdr=AGT)
_job2 = next((j for j in call("/api/v1/jobs?limit=500", hdr=ADM)[1]
              if j.get("id") == _jid2), {})
check("eine gewoehnliche Meldung bleibt unveraendert",
      _job2.get("log") == "Alles in Ordnung", _job2.get("log"))
check("und ihr Ergebnis ebenfalls",
      (_job2.get("result") or {}).get("updates") == 3, _job2.get("result"))


# ======================================================================
# Das Sitzungscookie und HTTPS
# ======================================================================
print()
print("--- Secure am tatsaechlichen Schema (Kleinkram aus Runde 4) ---")
# Das Merkmal hing allein am Schalter "nur HTTPS". Wer einen TLS-Proxy
# betreibt, den Schalter aber (wie ab Werk) aus laesst, bekam ein Cookie
# ohne Secure - der Browser haette es auch ueber http:// mitgeschickt.
#
# Der Testlauf spricht das Backend unverschluesselt an; geprueft wird
# deshalb beides: dass ueber http KEIN Secure gesetzt wird (sonst koennte
# sich hier niemand mehr anmelden - genau der Grund, aus dem es frueher
# am Schalter hing), und dass die Bedingung im Quelltext das tatsaechliche
# Schema mitnimmt.
import urllib.request as _ur  # noqa: E402

_pw = os.getenv("CO37_TEST_ADMIN_PW", "")
_req = _ur.Request(B + "/api/v1/login",
                   data=json.dumps({"username": "admin",
                                    "password": _pw}).encode(),
                   headers={"Content-Type": "application/json"},
                   method="POST")
try:
    with _ur.urlopen(_req, timeout=30) as _r:
        _setcookie = _r.headers.get("set-cookie", "")
except Exception as _e:  # noqa: BLE001
    _setcookie = f"FEHLER {_e}"
check("die Anmeldung setzt ein Cookie", "co37_session=" in _setcookie,
      _setcookie[:80])
check("ueber http ohne Secure - sonst kaeme niemand mehr herein",
      "Secure" not in _setcookie, _setcookie)

_i = quelle.index("SESSION_COOKIE, token")
_secure = quelle[_i:_i + 1600].split("secure=")[1][:140]
check("secure haengt nicht mehr allein am Schalter",
      "request_is_https(request)" in _secure, _secure)
check("der Schalter zaehlt weiterhin mit",
      '_PROXY_CFG["https_only"]' in _secure, _secure)


# ======================================================================
# Das Pruefprotokoll (F-63, Pruefung vom 2026-09-04)
# ======================================================================
print()
print("--- Laengengrenzen im Pruefprotokoll (F-63) ---")
# Gefunden beim Abgleich gegen BSI APP.3.1.A5 (Protokollierung
# sicherheitsrelevanter Ereignisse).
#
# /api/v1/login schreibt den vom Aufrufer gelieferten Benutzernamen als
# 'actor' ins Protokoll, bevor feststeht, dass es ihn gibt - richtig so,
# ein fehlgeschlagener Versuch ist der aufschlussreichere Eintrag. Nur
# stand da keine Laengengrenze, und 'actor' hat einen Index.
#
# Gemessen am 2026-09-04: fuenf abgewiesene Anmeldungen mit einem Namen
# von knapp 1 MiB haben die Datenbank von 144 KiB auf 11,6 MiB wachsen
# lassen. Ohne Zugangsdaten. Und das Protokoll wird bewusst nur
# angehaengt - es gibt keine Route zum Loeschen, der Muell bleibt drin.
#
# Hier mit 200000 Zeichen statt einem MiB: die Grenze wirkt genauso, und
# die Reihe soll nicht sekundenlang Daten schaufeln.
_lang = "B" * 200000
_code, _ = call("/api/v1/login", {"username": _lang, "password": "x"})
check("ein ueberlanger Benutzername wird weiterhin mit 401 abgewiesen",
      _code == 401, _code)

# Den Fehlerzaehler dieser Adresse wieder loeschen. Ohne das nimmt die
# Reihe 'login-throttle' einen Versuch mit ins Rennen, den sie nicht
# eingeplant hat - fuenf sind erlaubt, und dieser hier waere der erste.
call("/api/v1/login", {"username": "admin", "password": _pw})

_eintraege = call("/api/v1/audit?limit=200", hdr=ADM)[1]
_meiner = [e for e in _eintraege
           if str(e.get("actor", "")).startswith("BBBB")]
check("der Versuch steht im Protokoll", bool(_meiner),
      [e.get("action") for e in _eintraege[:5]])
check("aber gekuerzt, nicht in voller Laenge",
      all(len(e["actor"]) <= 120 for e in _meiner),
      [len(e["actor"]) for e in _meiner])

# Die Grenze sitzt in audit() und nicht am Anmeldeschema - damit gilt sie
# fuer alle Aufrufstellen und fuer jede, die noch dazukommt. Eine
# max_length am Benutzernamen haette nur diesen einen Weg geschlossen.
_quelle_audit = quelle[quelle.index("def audit(session"):]
_quelle_audit = _quelle_audit[:_quelle_audit.index("class Principal")]
for _feld, _grenze in (("actor", "AUDIT_ACTOR_MAX"),
                       ("action", "AUDIT_ACTION_MAX"),
                       ("detail", "AUDIT_DETAIL_MAX")):
    check(f"audit() kuerzt {_feld}",
          f"kurz({_feld}, {_grenze})" in _quelle_audit
          or f"kurz({_feld}, {_grenze})" in _quelle_audit.replace("\n", " "),
          _quelle_audit.count("kurz("))

# Steuerzeichen muessen mit raus. Ein Protokoll, in das man mit einem
# Zeilenumbruch im Benutzernamen eine zweite Zeile schreiben kann, ist
# keins - kurz() erledigt das, deshalb kurz() und kein reines [:n].
check("audit() nimmt kurz() und nicht nur einen Schnitt",
      "[:AUDIT_" not in _quelle_audit, _quelle_audit.count("[:"))

_code, _ = call("/api/v1/login", {"username": "admin\nfake admin login.ok",
                                  "password": "x"})
call("/api/v1/login", {"username": "admin", "password": _pw})
_zeilen = [e for e in call("/api/v1/audit?limit=50", hdr=ADM)[1]
           if "fake" in str(e.get("actor", ""))]
check("ein Zeilenumbruch im Namen erzeugt keine zweite Protokollzeile",
      all("\n" not in e["actor"] for e in _zeilen),
      [repr(e["actor"])[:60] for e in _zeilen])


# ======================================================================
# Aufraeumen
# ======================================================================
# Diese Reihe legt Hosts an - einen freigegebenen und zwei, die bei den
# Groessenproben entstehen. Sie teilt sich das Backend mit allen anderen,
# und der Freibetrag der Lizenz sind zehn freigegebene Hosts: ohne
# Aufraeumen fiel 'roles' hier durch ("Freigabe bis zum Freibetrag
# moeglich"), und 'frontend' kam mit der Hostliste durcheinander.
#
# Beim ersten Lauf genau so passiert. Wer eine Reihe hinzufuegt, die
# schreibt, muss hinter sich aufraeumen.
print()
print("--- Aufraeumen ---")
_uebrig = []
for h in call("/api/v1/hosts", hdr=ADM)[1]:
    if str(h.get("hostname", "")).startswith("EINGABE-GRENZ-"):
        code, _ = call(f"/api/v1/hosts/{h['id']}", method="DELETE", hdr=ADM)
        if code != 200:
            _uebrig.append((h["hostname"], code))
if aid:
    call(f"/api/v1/areas/{aid}", method="DELETE", hdr=ADM)
check("die angelegten Hosts sind wieder weg", not _uebrig, _uebrig)
check("kein EINGABE-GRENZ-Host bleibt stehen",
      not [h for h in call("/api/v1/hosts", hdr=ADM)[1]
           if str(h.get("hostname", "")).startswith("EINGABE-GRENZ-")])
check("und der Testbereich ebenfalls nicht",
      not [a for a in call("/api/v1/areas", hdr=ADM)[1]
           if a.get("name") == "Eingabegrenzen"])

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
