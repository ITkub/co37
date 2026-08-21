"""
CO-37 - Bereiche (Areas): Anlegen, Zuordnen, Loeschen, Reihenfolge.

Bereiche gruppieren Hosts in der Uebersicht, ganz freiwillig - ohne sie
verhaelt sich CO-37 wie bisher. Dieser Test deckt die Backend-Routen ab,
nicht die Oberflaeche:

  - Anlegen, Umbenennen, eigene Reihenfolge
  - Ein Host gehoert hoechstens einem Bereich an, host_count zaehlt korrekt
  - Loeschen nur, wenn der Bereich leer ist (Mikes Entscheidung, nicht die
    weichere Variante "Hosts fallen zurueck auf ohne Bereich")
  - Warnung, wenn ein Bereichs-Zeitplan gespeichert wird, waehrend ein
    enthaltener Host schon einen eigenen hat - nicht blockierend, nur ein
    Hinweis, dass sich beide in die Quere kommen koennen

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/area-test.py
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


def enroll_host(hostname):
    code, res = call("/api/v1/agent/enroll", {
        "hostname": hostname, "os_type": "linux",
        "os_version": "Debian 13", "agent_version": "0.35.4",
    })
    check(f"Testhost {hostname} angemeldet", code == 200, code)
    hosts = call("/api/v1/hosts", hdr=ADM)[1]
    hid = next(h["id"] for h in hosts if h["hostname"] == hostname)
    call(f"/api/v1/hosts/{hid}/approve", {}, hdr=ADM)
    return hid


# --------------------------------------------------------- Anlegen, Lesen
code, res = call("/api/v1/areas", {"name": "  Buero  "}, hdr=ADM)
check("Bereich anlegbar", code == 200, (code, res))
area_a = res.get("id")
check("Name wird getrimmt", res.get("name") == "Buero", res.get("name"))
check("neuer Bereich hat keine Hosts", res.get("host_count") == 0, res.get("host_count"))

code, res = call("/api/v1/areas", {"name": ""}, hdr=ADM)
check("leerer Name abgewiesen", code == 400, code)
code, res = call("/api/v1/areas", {"name": "   "}, hdr=ADM)
check("nur Leerraum abgewiesen", code == 400, code)

code, res = call("/api/v1/areas", {"name": "Lager"}, hdr=ADM)
check("zweiter Bereich anlegbar", code == 200, (code, res))
area_b = res.get("id")

code, areas = call("/api/v1/areas", hdr=ADM)
check("beide Bereiche in der Liste", code == 200
      and {a["id"] for a in areas} >= {area_a, area_b}, areas)

# ---------------------------------------------------------- Eigene Reihenfolge
code, res = call("/api/v1/areas/order", {"ids": [area_b, area_a]}, hdr=ADM)
check("Reihenfolge speicherbar", code == 200, (code, res))
code, areas = call("/api/v1/areas", hdr=ADM)
order = [a["id"] for a in areas if a["id"] in (area_a, area_b)]
check("Lager steht jetzt vor Buero", order == [area_b, area_a], order)

# ------------------------------------------------------- Hosts zuordnen
h1 = enroll_host("AREA-TEST-01")
h2 = enroll_host("AREA-TEST-02")

code, res = call(f"/api/v1/hosts/{h1}/area", {"area_id": area_a}, hdr=ADM)
check("Host in Bereich verschiebbar", code == 200 and res.get("area_id") == area_a,
      (code, res))

code, areas = call("/api/v1/areas", hdr=ADM)
buero = next(a for a in areas if a["id"] == area_a)
check("host_count zaehlt den verschobenen Host", buero["host_count"] == 1,
      buero["host_count"])

code, res = call(f"/api/v1/hosts/{h1}/area", {"area_id": 999999}, hdr=ADM)
check("unbekannter Bereich abgewiesen", code == 404, code)

code, res = call(f"/api/v1/hosts/{h1}/area", {"area_id": None}, hdr=ADM)
check("Host wieder ohne Bereich", code == 200 and res.get("area_id") is None, res)
code, areas = call("/api/v1/areas", hdr=ADM)
buero = next(a for a in areas if a["id"] == area_a)
check("host_count sinkt wieder auf 0", buero["host_count"] == 0, buero["host_count"])

# ---------------------------------------------------- Loeschen nur wenn leer
call(f"/api/v1/hosts/{h1}/area", {"area_id": area_a}, hdr=ADM)
code, res = call(f"/api/v1/areas/{area_a}", method="DELETE", hdr=ADM)
check("Bereich mit Host nicht loeschbar", code == 409, (code, res))

call(f"/api/v1/hosts/{h1}/area", {"area_id": None}, hdr=ADM)
code, res = call(f"/api/v1/areas/{area_a}", method="DELETE", hdr=ADM)
check("leerer Bereich loeschbar", code == 200, (code, res))

code, res = call(f"/api/v1/areas/{area_a}", method="DELETE", hdr=ADM)
check("zweites Loeschen meldet 404", code == 404, code)

# --------------------------------------------------- Warnung bei Kollision
code, res = call(f"/api/v1/hosts/{h2}/area", {"area_id": area_b}, hdr=ADM)
check("zweiter Host in Lager verschoben", code == 200, (code, res))

# Host bekommt einen eigenen Zeitplan, ueber die schon bestehende Route.
code, res = call(f"/api/v1/hosts/{h2}", {
    "patch_enabled": True, "patch_days": ["MO"], "patch_time": "03:00",
}, method="PATCH", hdr=ADM)
check("Host hat jetzt einen eigenen Zeitplan", code == 200
      and res.get("patch_enabled") is True, (code, res))

# Bereich bekommt (noch) keinen eigenen Zeitplan - keine Warnung erwartet.
code, res = call(f"/api/v1/areas/{area_b}", {"downtime_minutes": 45},
                 method="PATCH", hdr=ADM)
check("keine Warnung ohne Bereichs-Zeitplan", code == 200
      and res.get("patch_conflicts") == [], (code, res.get("patch_conflicts")))

# Jetzt bekommt der Bereich selbst einen Zeitplan - Kollision erwartet.
code, res = call(f"/api/v1/areas/{area_b}", {
    "patch_enabled": True, "patch_days": ["DI"], "patch_time": "04:00",
}, method="PATCH", hdr=ADM)
check("Warnung bei Kollision", code == 200
      and "AREA-TEST-02" in res.get("patch_conflicts", []),
      (code, res.get("patch_conflicts")))

# --------------------------------------------------------- Werte-Grenzen
code, res = call(f"/api/v1/areas/{area_b}", {
    "downtime_minutes": 0, "patch_grace_hours": 0,
}, method="PATCH", hdr=ADM)
check("downtime_minutes < 1 wird auf 30 zurueckgesetzt",
      res.get("downtime_minutes") == 30, res.get("downtime_minutes"))
check("patch_grace_hours < 1 wird auf 4 zurueckgesetzt",
      res.get("patch_grace_hours") == 4, res.get("patch_grace_hours"))

code, res = call(f"/api/v1/areas/{area_b}", {"name": "  "},
                 method="PATCH", hdr=ADM)
check("Umbenennen auf Leerraum abgewiesen", code == 400, code)

code, res = call("/api/v1/areas/999999", {"name": "x"}, method="PATCH", hdr=ADM)
check("Bearbeiten eines unbekannten Bereichs meldet 404", code == 404, code)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
