"""
CO-37 - keine Sofort-Auftraege fuer Hosts, die offline sind.

Grund: ein von Hand angelegter Scan- oder Patch-Auftrag blieb bis zu sieben
Tage auf "pending" und lief beim Wiederkommen des Hosts nachtraeglich.
Jetzt wird er gar nicht erst angelegt (HTTP 409). Eingeplante Auftraege
und Neustart bleiben unberuehrt.

Braucht ein laufendes Backend mit eigener Testdatenbank:

    ./run-tests.sh offline-auftraege
"""
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8099")
DB = os.getenv("CO37_TEST_DB", "")
adm = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}
fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}"
          f"{'  -> ' + str(extra) if extra else ''}")


def call(pfad, daten=None, method=None, hdr=None):
    kopf = {"Content-Type": "application/json"}
    kopf.update(hdr or {})
    anfrage = urllib.request.Request(
        B + pfad,
        data=json.dumps(daten).encode() if daten is not None else None,
        headers=kopf,
        method=method or ("POST" if daten is not None else "GET"),
    )
    try:
        return 200, json.loads(urllib.request.urlopen(anfrage, timeout=30).read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def host_anlegen(name):
    _, a = call("/api/v1/agent/enroll", {
        "hostname": name, "os_type": "linux", "os_version": "Debian 12",
        "ip_address": "192.0.2.88", "agent_version": "0.51.5"})
    _, liste = call("/api/v1/hosts", hdr=adm)
    hid = [h["id"] for h in liste if h["hostname"] == name][0]
    call(f"/api/v1/hosts/{hid}/approve", {}, hdr=adm)
    return hid, {"X-Agent-Token": a["agent_token"]}


def heartbeat(name, kopf):
    return call("/api/v1/agent/heartbeat", {
        "hostname": name, "os_type": "linux", "os_version": "Debian 12",
        "ip_address": "192.0.2.88", "agent_version": "0.51.5",
        "reboot_required": False, "reboot_reasons": []}, hdr=kopf)


def auftrag(hid, **kw):
    return call(f"/api/v1/hosts/{hid}/jobs", kw, hdr=adm)


def anzahl_auftraege(hid):
    _, js = call(f"/api/v1/jobs?host_id={hid}&limit=500", hdr=adm)
    return len(js)


def last_seen_setzen(hid, vor_sekunden):
    t = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=vor_sekunden)
    con = sqlite3.connect(DB)
    n = con.execute("UPDATE host SET last_seen=? WHERE id=?",
                    (t.strftime("%Y-%m-%d %H:%M:%S.%f"), hid)).rowcount
    con.commit()
    con.close()
    return n


ZUKUNFT = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

check("CO37_TEST_DB gesetzt", bool(DB) and os.path.exists(DB), DB)

# --- nie gemeldet
A = "offline-test-a"
hid_a, kopf_a = host_anlegen(A)
for art in ("scan", "patch"):
    code, text = auftrag(hid_a, job_type=art, params={})
    check(f"nie gemeldet: {art} ohne Termin -> 409", code == 409, (code, text))
    check(f"nie gemeldet: {art}-Meldung nennt 'offline'",
          "offline" in str(text).lower(), text)
code, text = auftrag(hid_a, job_type="scan", patch_after=True, params={})
check("nie gemeldet: scan mit patch_after -> 409", code == 409, (code, text))
check("nie gemeldet: kein Auftrag entstanden", anzahl_auftraege(hid_a) == 0)

# --- eingeplant bleibt erlaubt
for art in ("scan", "patch"):
    code, text = auftrag(hid_a, job_type=art, params={}, scheduled_at=ZUKUNFT)
    check(f"offline: {art} mit Termin bleibt erlaubt", code == 200, (code, text))

# --- Neustart unveraendert (kein 409 wegen offline)
code, text = auftrag(hid_a, job_type="reboot")
check("offline: Neustart wird angelegt (Administrator)", code == 200, (code, text))

# --- die Rechtepruefung kommt vor der Offline-Sperre
# Ein Konto ohne Neustart-Recht bekommt 403, auch wenn der Host offline ist;
# sonst haengt es vom Online-Zustand ab, ob die Rechtepruefung greift.
call("/api/v1/users", {"username": "offline-tester",
                       "password": "offline-passwort-1", "role": "user"}, hdr=adm)
code, res = call("/api/v1/login", {"username": "offline-tester",
                                   "password": "offline-passwort-1"})
usr = {"X-Session": res.get("session", "")} if code == 200 else {}
check("Testkonto ohne Neustart-Recht angemeldet", code == 200 and bool(usr), code)
# Derselbe Host wie oben, noch ohne Heartbeat: jede weitere Freigabe zehrt am
# Freibetrag der Lizenz, und spaetere Reihen brauchen ihn.
hid_b = hid_a
code, text = call(f"/api/v1/hosts/{hid_b}/jobs",
                  {"job_type": "patch", "reboot_after": True}, hdr=usr)
check("offline, ohne Recht: Patch MIT Neustart -> 403, nicht 409",
      code == 403, (code, text))
code, text = call(f"/api/v1/hosts/{hid_b}/jobs", {"job_type": "reboot"}, hdr=usr)
check("offline, ohne Recht: Neustart -> 403, nicht 409", code == 403, (code, text))

# --- ein Termin, der schon vorbei ist, ist kein Termin
VOR_30S = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
vorher = anzahl_auftraege(hid_b)
for art in ("scan", "patch"):
    code, text = auftrag(hid_b, job_type=art, params={}, scheduled_at=VOR_30S)
    check(f"offline: {art} mit Termin vor 30 s zaehlt als Sofort-Auftrag -> 409",
          code == 409, (code, text))
check("offline: dafuer kein Auftrag entstanden",
      anzahl_auftraege(hid_b) == vorher, (vorher, anzahl_auftraege(hid_b)))

# --- online nach Heartbeat
heartbeat(A, kopf_a)
for art in ("scan", "patch"):
    code, text = auftrag(hid_a, job_type=art, params={})
    check(f"nach Heartbeat: {art} ohne Termin -> 200", code == 200, (code, text))

# --- wieder offline durch alte Meldung
check("last_seen verschoben", last_seen_setzen(hid_a, 600) == 1)
code, text = auftrag(hid_a, job_type="scan", params={})
check("letzte Meldung vor 10 Min: scan -> 409", code == 409, (code, text))
last_seen_setzen(hid_a, 60)
code, text = auftrag(hid_a, job_type="scan", params={})
check("letzte Meldung vor 1 Min: scan -> 200", code == 200, (code, text))

print(f"\n{fails} Fehler" if fails else "\nalle bestanden")
raise SystemExit(1 if fails else 0)
