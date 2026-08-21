"""
CO-37 - Wertegrenzen beim Bearbeiten eines Hosts.

Anlass: beim Bauen der Bereiche fiel auf, dass dieselbe Zeile fuer
patch_grace_hours auch hier steht - "if host.patch_grace_hours and
host.patch_grace_hours < 1" wertet bei genau 0 nicht aus, weil 0 in Python
selbst schon falsch ist. Eine Kulanzzeit von 0 Stunden blieb dadurch
stehen, statt auf die Vorgabe (4) zurueckgesetzt zu werden - ein Host, der
genau zum Patch-Termin aus war, haette sein Fenster dann ganz verpasst.

Keine der bestehenden Reihen hat PATCH /api/v1/hosts/{id} mit ungueltigen
Werten geprueft, weder fuer downtime_minutes noch fuer patch_grace_hours.

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/host-patch-test.py
"""
import json
import os
import urllib.error
import urllib.request

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
ADMIN_KEY = os.getenv("CO37_TEST_KEY", "t")
ADM = {"X-API-Key": ADMIN_KEY}

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
        body = urllib.request.urlopen(r, timeout=30).read()
        return 200, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


code, res = call("/api/v1/agent/enroll", {
    "hostname": "PATCH-GRENZ-01", "os_type": "linux",
    "os_version": "Debian 13", "agent_version": "0.35.4",
})
check("Testhost angemeldet", code == 200, code)
hosts = call("/api/v1/hosts", hdr=ADM)[1]
hid = next(h["id"] for h in hosts if h["hostname"] == "PATCH-GRENZ-01")
call(f"/api/v1/hosts/{hid}/approve", {}, hdr=ADM)

code, res = call(f"/api/v1/hosts/{hid}", {
    "downtime_minutes": 0, "patch_grace_hours": 0,
}, method="PATCH", hdr=ADM)
check("downtime_minutes = 0 wird auf 30 zurueckgesetzt",
      code == 200 and res.get("downtime_minutes") == 30, (code, res.get("downtime_minutes")))
check("patch_grace_hours = 0 wird auf 4 zurueckgesetzt",
      code == 200 and res.get("patch_grace_hours") == 4, (code, res.get("patch_grace_hours")))

# Ein gueltiger Wert darf nicht ueberschrieben werden - sonst waere die
# Grenze zu grob und naehme jedem eine bewusst kurze Kulanzzeit weg.
code, res = call(f"/api/v1/hosts/{hid}", {
    "downtime_minutes": 15, "patch_grace_hours": 1,
}, method="PATCH", hdr=ADM)
check("gueltiges downtime_minutes bleibt erhalten",
      code == 200 and res.get("downtime_minutes") == 15, (code, res.get("downtime_minutes")))
check("gueltiges patch_grace_hours bleibt erhalten",
      code == 200 and res.get("patch_grace_hours") == 1, (code, res.get("patch_grace_hours")))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
