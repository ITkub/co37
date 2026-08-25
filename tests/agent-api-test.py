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

adm = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}
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

# ----------------------------------------------------------------------
# Auskunftsfelder des Agents werden gekuerzt
# ----------------------------------------------------------------------
# Der Agent bestimmt os_version, agent_version, ip_address und
# reboot_reasons frei - bei der Anmeldung sogar ohne Anmeldung, denn
# /agent/enroll ist bewusst offen. Angezeigt werden sie escaped, das ist
# die Sicherheitsschranke. Hier geht es um die Groesse: ohne Grenze laesst
# sich die Datenbank und damit jede Host-Abfrage mit Megabyte volllaufen.
#
# Gekuerzt, nicht abgewiesen: ein Heartbeat, der mit 422 scheitert, nimmt
# einen Host dauerhaft aus dem Betrieb.
print()
lang = "A" * 5000
hb(os_version=lang, agent_version=lang, ip_address=lang,
   reboot_required=True, reboot_reasons=[lang] * 200)
h = [x for x in call("/api/v1/hosts", hdr=adm) if x["id"] == hid][0]

check("os_version gekuerzt", 0 < len(h["os_version"]) <= 120,
      len(h["os_version"]))
check("agent_version gekuerzt", 0 < len(h["agent_version"]) <= 40,
      len(h["agent_version"]))
check("ip_address gekuerzt", 0 < len(h["ip_address"]) <= 45,
      len(h["ip_address"]))
check("Anzahl der Neustartgruende begrenzt",
      len(h["reboot_reasons"]) <= 20, len(h["reboot_reasons"]))
check("jeder Neustartgrund einzeln begrenzt",
      all(len(g) <= 80 for g in h["reboot_reasons"]),
      max((len(g) for g in h["reboot_reasons"]), default=0))

# Steuerzeichen wuerden die Anzeige und das Protokoll zerschiessen, ohne
# je etwas zu bedeuten.
hb(os_version="Linux\x00\x07 6.1\ndanach")
h = [x for x in call("/api/v1/hosts", hdr=adm) if x["id"] == hid][0]
check("Steuerzeichen entfernt",
      "\x00" not in h["os_version"] and "\n" not in h["os_version"],
      repr(h["os_version"]))

# Kein Typ erzwungen: reboot_reasons darf auch Unsinn enthalten, ohne dass
# der Heartbeat scheitert - der Host bliebe sonst draussen.
r = hb(reboot_required=True, reboot_reasons=[{"a": 1}, 5, None])
check("Heartbeat ueberlebt Unsinn in reboot_reasons", "jobs" in r, r)
h = [x for x in call("/api/v1/hosts", hdr=adm) if x["id"] == hid][0]
check("und daraus werden Zeichenketten",
      all(isinstance(g, str) for g in h["reboot_reasons"]), h["reboot_reasons"])

# Ein normaler Wert bleibt unangetastet - sonst prueft das oben nichts.
hb(os_version="Windows-10-10.0.19045-SP0", agent_version="0.36.7",
   ip_address="192.0.2.50", reboot_reasons=[])
h = [x for x in call("/api/v1/hosts", hdr=adm) if x["id"] == hid][0]
check("normale Werte bleiben unveraendert",
      h["os_version"] == "Windows-10-10.0.19045-SP0"
      and h["agent_version"] == "0.36.7", h)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
