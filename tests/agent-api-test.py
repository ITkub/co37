"""
CO-37 - Agent-API gegen ein laufendes Backend.

Grund fuer dieses Geruest: der Frontend-Test spricht das Backend nur als
Bedienoberflaeche an. Die Agent-Schnittstelle blieb ungeprueft - und genau
dort ist es schiefgegangen: eine Boot-Zeit ohne Zeitzone liess jeden
Heartbeat eines aktuellen Agents mit 500 scheitern. Sichtbar war davon
nichts, weil der Agent unter pythonw ohne Konsole laeuft und die Meldung
ins Leere schrieb.

Aufruf (eigenes Backend auf einem freien Port, eigene Datenbank):

    python3 tests/agent-api-test.py
"""
import json, time, urllib.request
import os
B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")

def call(path, data=None, method=None, hdr=None):
    h = {"Content-Type": "application/json"}; h.update(hdr or {})
    r = urllib.request.Request(B+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        return json.loads(urllib.request.urlopen(r, timeout=30).read())
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "body": e.read().decode()[:300]}

adm = {"X-API-Key": os.getenv("CO37_TEST_KEY", "t")}
en = call("/api/v1/agent/enroll", {"hostname":"TEST-WIN01","os_type":"windows",
     "os_version":"Windows 11","ip_address":"192.0.2.50","agent_version":"0.18.0"})
tok = en["agent_token"]; ah = {"X-Agent-Token": tok}
hid = call("/api/v1/hosts", hdr=adm)[0]["id"]
call(f"/api/v1/hosts/{hid}/approve", {}, hdr=adm)

def hb(**extra):
    p = {"hostname":"TEST-WIN01","os_type":"windows","os_version":"Windows 11",
         "ip_address":"192.0.2.50","agent_version":"0.18.0",
         "reboot_required":False,"reboot_reasons":[]}
    p.update(extra)
    return call("/api/v1/agent/heartbeat", p, hdr=ah)

fails = 0
def check(label, ok, extra=""):
    global fails
    if not ok: fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> '+str(extra) if extra else ''}")

boot = time.time() - 600          # vor 10 Minuten gestartet

r = hb()
check("Heartbeat ohne Boot-Zeit (alter Agent)", "jobs" in r, r)
r = hb(boot_time=boot, reboot_pending=False)
check("Heartbeat mit Boot-Zeit", "jobs" in r, r)
r = hb(boot_time=boot, reboot_pending=True)
check("Heartbeat mit anstehendem Neustart", r.get("jobs") == [], r)

# Auftrag anlegen, auf running setzen, dann Boot-Zeit danach melden
job = call(f"/api/v1/hosts/{hid}/jobs", {"job_type":"patch","params":{}}, hdr=adm)
jid = job.get("id")
check("Patch-Auftrag angelegt", bool(jid), job)
r = hb(boot_time=boot)             # Auslieferung -> started_at wird gesetzt
call("/api/v1/agent/job-log", {"job_id":jid,"text":"Startet","progress":"Startet"}, hdr=ah)
st = [j for j in call("/api/v1/jobs?limit=50", hdr=adm) if j["id"]==jid][0]
check("Auftrag laeuft", st["state"]=="running", st["state"])

# Neustart nach dem Start des Auftrags
r = hb(boot_time=time.time()+600)
check("Heartbeat nach Neustart ohne Fehler", "jobs" in r, r)
st = [j for j in call("/api/v1/jobs?limit=50", hdr=adm) if j["id"]==jid][0]
check("Auftrag nach Neustart abgebrochen", st["state"]=="cancelled", st["state"])
check("Grund vermerkt", "neu gestartet" in (st.get("error") or ""), st.get("error"))

# Geplanter Auftrag mit Zeitzone im Zeitstempel
from datetime import datetime, timedelta, timezone
when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
r = call(f"/api/v1/hosts/{hid}/jobs",
         {"job_type":"reboot","params":{},"scheduled_at":when,"set_downtime":False}, hdr=adm)
check("geplanter Neustart mit Zeitzone", bool(r.get("id")), r)

naive = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None).isoformat()
r = call(f"/api/v1/hosts/{hid}/jobs",
         {"job_type":"reboot","params":{},"scheduled_at":naive,"set_downtime":False}, hdr=adm)
check("geplanter Neustart ohne Zeitzone", bool(r.get("id")), r)

past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
r = call(f"/api/v1/hosts/{hid}/jobs",
         {"job_type":"reboot","params":{},"scheduled_at":past,"set_downtime":False}, hdr=adm)
check("Zeitpunkt in der Vergangenheit wird abgelehnt", r.get("HTTP") == 400, r)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
