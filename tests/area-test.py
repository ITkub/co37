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

Am Ende, ueber den echten Heartbeat statt nur direkt gegen area_patch_due():
  - Jeder Host in einem faelligen Bereich bekommt seinen eigenen Auftrag -
    nicht nur der zuerst meldende
  - Faellt der eigene Zeitplan eines Hosts UND der seines Bereichs auf
    denselben Heartbeat, entsteht trotzdem nur ein Auftrag
    (patch_job_open() als gemeinsame Bremse, keine Sonderbehandlung)

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/area-test.py
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
ADM = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}

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


def enroll_token(hostname):
    """Wie enroll_host(), liefert zusaetzlich den agent_token fuer den
    Heartbeat - enroll_host() verwirft ihn, den brauchen die Tests unten."""
    code, res = call("/api/v1/agent/enroll", {
        "hostname": hostname, "os_type": "linux",
        "os_version": "Debian 13", "agent_version": "0.35.4",
    })
    check(f"Testhost {hostname} angemeldet", code == 200, code)
    token = res.get("agent_token")
    hosts = call("/api/v1/hosts", hdr=ADM)[1]
    hid = next(h["id"] for h in hosts if h["hostname"] == hostname)
    call(f"/api/v1/hosts/{hid}/approve", {}, hdr=ADM)
    return hid, token


def heartbeat(token, hostname="TEST"):
    return call("/api/v1/agent/heartbeat", {
        "hostname": hostname, "os_type": "linux", "os_version": "Debian 13",
        "agent_version": "0.35.4",
    }, hdr={"X-Agent-Token": token})


def jobs_for(hid):
    return call(f"/api/v1/jobs?host_id={hid}", hdr=ADM)[1]


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

# --------------------------------------------------- Unterbereiche (0.38.3)
# Genau eine Ebene: ein Bereich unter einem obersten Bereich. Ein
# Unterbereich darf selbst keine Kinder haben, und ein Elter mit Kindern
# ist nicht loeschbar, solange die Kinder da sind.
code, res = call("/api/v1/areas", {"name": "PVE01"}, hdr=ADM)
check("oberster Bereich fuer Unterbereiche angelegt", code == 200, (code, res))
pve01 = res["id"]
check("oberster Bereich hat keinen Elter", res.get("parent_id") is None, res)

code, res = call("/api/v1/areas", {"name": "Windows", "parent_id": pve01}, hdr=ADM)
check("Unterbereich anlegbar", code == 200, (code, res))
win = res["id"]
check("Unterbereich traegt den Elter", res.get("parent_id") == pve01, res)

code, res = call("/api/v1/areas", {"name": "Linux", "parent_id": pve01}, hdr=ADM)
check("zweiter Unterbereich anlegbar", code == 200, (code, res))
lin = res["id"]

code, areas = call("/api/v1/areas", hdr=ADM)
byid = {a["id"]: a for a in areas}
check("Unterbereich steht mit parent_id in der Liste",
      byid.get(win, {}).get("parent_id") == pve01, byid.get(win))

# Eine zweite Ebene ist nicht erlaubt: ein Unterbereich kann nicht Elter sein.
code, res = call("/api/v1/areas", {"name": "zu tief", "parent_id": win}, hdr=ADM)
check("Unterbereich unter einem Unterbereich abgewiesen (nur eine Ebene)",
      code == 409, (code, res))

# Unbekannter Elter.
code, res = call("/api/v1/areas", {"name": "waise", "parent_id": 999999}, hdr=ADM)
check("unbekannter Elter abgewiesen", code == 404, code)

# Ein Host kommt in den Unterbereich (die VMs sollen in Windows/Linux).
# Bewusst der schon angemeldete h1 (gerade ohne Bereich), kein neuer Host:
# das Backend teilt sich alle Testreihen, und ein zusaetzlich genehmigter
# Host wuerde den Freibetrag von 10 fuer spaetere Reihen aufzehren.
hvm = h1
code, res = call(f"/api/v1/hosts/{hvm}/area", {"area_id": win}, hdr=ADM)
check("Host in einen Unterbereich verschiebbar",
      code == 200 and res.get("area_id") == win, (code, res))

# Elter mit Unterbereichen ist nicht loeschbar - erst die Kinder.
code, res = call(f"/api/v1/areas/{pve01}", method="DELETE", hdr=ADM)
check("Elter mit Unterbereichen nicht loeschbar", code == 409, (code, res))

# Unterbereich mit Host ist nicht loeschbar (bestehende Regel gilt weiter).
code, res = call(f"/api/v1/areas/{win}", method="DELETE", hdr=ADM)
check("Unterbereich mit Host nicht loeschbar", code == 409, (code, res))

# Host heraus, dann Unterbereich weg, dann der andere - erst jetzt der Elter.
call(f"/api/v1/hosts/{hvm}/area", {"area_id": None}, hdr=ADM)
code, res = call(f"/api/v1/areas/{win}", method="DELETE", hdr=ADM)
check("leerer Unterbereich loeschbar", code == 200, (code, res))
call(f"/api/v1/areas/{lin}", method="DELETE", hdr=ADM)
code, res = call(f"/api/v1/areas/{pve01}", method="DELETE", hdr=ADM)
check("Elter loeschbar, sobald keine Unterbereiche mehr da sind",
      code == 200, (code, res))


# --------------------------------------- Zeitplan des Bereichs im Heartbeat
# Ab hier ueber die echte Route /api/v1/agent/heartbeat statt nur gegen
# area_patch_due() direkt (das deckt schon tests/area-schedule-test.py ab).
# Hier geht es um das Zusammenspiel im Heartbeat selbst: entsteht wirklich
# fuer jeden Host im Bereich ein eigener Auftrag, und blockiert
# patch_job_open() zuverlaessig den doppelten Auftrag, wenn eigener und
# Bereichs-Zeitplan gleichzeitig faellig sind.
DAY_KEYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
slot = datetime.now() - timedelta(minutes=5)
faelliger_tag = [DAY_KEYS[slot.weekday()]]
faellige_zeit = f"{slot.hour:02d}:{slot.minute:02d}"

code, res = call("/api/v1/areas", {"name": "Heartbeat-Bereich"}, hdr=ADM)
check("Bereich fuer den Heartbeat-Test angelegt", code == 200, (code, res))
area_c = res["id"]
code, res = call(f"/api/v1/areas/{area_c}", {
    "patch_enabled": True, "patch_days": faelliger_tag, "patch_time": faellige_zeit,
}, method="PATCH", hdr=ADM)
check("Bereichs-Zeitplan gesetzt, faellig seit 5 Minuten", code == 200, (code, res))

h3, tok3 = enroll_token("AREA-HB-01")
h4, tok4 = enroll_token("AREA-HB-02")
call(f"/api/v1/hosts/{h3}/area", {"area_id": area_c}, hdr=ADM)
call(f"/api/v1/hosts/{h4}/area", {"area_id": area_c}, hdr=ADM)

code, res = heartbeat(tok3, "AREA-HB-01")
check("Heartbeat des ersten Hosts im faelligen Bereich ok", code == 200, (code, res))
j3 = jobs_for(h3)
check("erster Host bekommt genau einen Auftrag", len(j3) == 1, j3)
check("Auftrag traegt die Herkunft des Bereichs",
      len(j3) == 1 and j3[0]["params"].get("source_area_id") == area_c,
      j3[0]["params"] if j3 else None)

code, res = heartbeat(tok4, "AREA-HB-02")
check("Heartbeat des zweiten Hosts im selben Bereich ok", code == 200, (code, res))
j4 = jobs_for(h4)
check("zweiter Host bekommt seinen EIGENEN Auftrag - nicht vom ersten "
      "'verbraucht'", len(j4) == 1, j4)
check("auch dessen Auftrag traegt die Herkunft des Bereichs",
      len(j4) == 1 and j4[0]["params"].get("source_area_id") == area_c,
      j4[0]["params"] if j4 else None)

# --------- Kollision: eigener Zeitplan UND Bereichs-Zeitplan gleichzeitig
# faellig - patch_job_open() muss den zweiten verhindern, ohne dass hier
# irgendeine Sonderbehandlung noetig war.
h5, tok5 = enroll_token("AREA-HB-03")
call(f"/api/v1/hosts/{h5}/area", {"area_id": area_c}, hdr=ADM)
code, res = call(f"/api/v1/hosts/{h5}", {
    "patch_enabled": True, "patch_days": faelliger_tag, "patch_time": faellige_zeit,
}, method="PATCH", hdr=ADM)
check("dritter Host hat zusaetzlich einen eigenen, ebenfalls faelligen "
      "Zeitplan", code == 200, (code, res))

code, res = heartbeat(tok5, "AREA-HB-03")
check("Heartbeat mit doppelt faelligem Zeitplan ok", code == 200, (code, res))
j5 = jobs_for(h5)
check("trotz zweier gleichzeitig faelliger Zeitplaene nur EIN Auftrag",
      len(j5) == 1, j5)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
