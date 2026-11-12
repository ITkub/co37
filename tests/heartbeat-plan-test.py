"""
CO-37 - aus einem faelligen Termin wird im Heartbeat ein Auftrag.

DER ANLASS

Am 2026-11-10 wurde die Auftragsplanung aus der Heartbeat-Route in
backend/scheduling.py gezogen. Zur Gegenprobe, ob die Pruefreihen diesen
Umbau ueberhaupt gesichert haetten, wurde eine Bedingung umgedreht:

    if due and payload.reboot_pending:        # statt: and not

Ein faelliger Host ohne anstehenden Neustart bekommt damit KEINEN
Patch-Auftrag mehr - das Programm tut seine Hauptarbeit nicht. Der volle
Lauf meldete danach: 2231 Pruefungen, alle bestanden.

Der Grund, nachgesehen und nicht vermutet:

  - patchdue und area-schedule rufen patch_due() und area_patch_due()
    DIREKT auf, nie ueber die Route
  - nachreichung und scan-patch fassen den Heartbeat nicht an
  - agent-api ruft ihn auf, prueft aber nur, dass "jobs" in der Antwort
    steht - nicht, was darin steht

Geprueft war damit, ob patch_due() richtig ENTSCHEIDET. Ungeprueft war,
ob daraus ein Auftrag ENTSTEHT. Diese Reihe schliesst genau das.

WAS SIE TUT

Sie geht ueber HTTP gegen das gemeinsame Testbackend, nicht gegen die
Funktion - sonst misst sie wieder die Entscheidung statt deren Folge.

Jeder Fall bekommt einen EIGENEN Host. Der erste Lauf setzt
last_patch_run, der Termin ist danach erledigt, und ein zweiter Fall auf
demselben Host wuerde nur noch messen, dass nichts mehr passiert.

ZUR ZEITRECHNUNG

Der faellige Termin wird mit localnow() DES BACKENDS berechnet, nicht mit
datetime.now().astimezone(). Das ist bewusst eine Kopplung an den
Prueflingen, und zwar die kleinere Gefahr: rechnete die Reihe in einer
anderen Zone als der Server, laege der Termin um den Zonenversatz daneben.

  - nach vorn faellt das auf: der Termin liegt in der Zukunft,
    _schedule_due ueberspringt ihn, die Reihe wird rot
  - nach hinten faellt es NICHT auf: der Termin liegt dann noch im
    Nachlauf von vier Stunden, die Reihe wird gruen - aus dem falschen
    Grund

Genau diese Sorte Gruen hat dieses Projekt mehrfach getaeuscht (siehe den
Portcheck in run-tests.sh). Deshalb dieselbe Zeitquelle wie der Server.

Der Wochentag wird am TERMIN abgelesen, nicht an der Gegenwart - um 00:05
sind das zwei verschiedene Tage.

Braucht das gemeinsame Testbackend aus run-tests.sh.

    ./run-tests.sh heartbeat-plan
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8099")
adm = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from utctime import localnow  # noqa: E402

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
        return json.loads(urllib.request.urlopen(anfrage, timeout=30).read())
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "body": e.read().decode()[:300]}


# ----------------------------------------------------------- der Termin
# Die Kuerzel stehen hier ausgeschrieben und werden nicht aus
# scheduling.DAYS geholt: eine Pruefung, die ihre Erwartung aus dem
# Prueflingen bezieht, bestaetigt nur sich selbst.
KUERZEL = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]

# Zehn Minuten zurueck: sicher vorbei, und weit innerhalb des Nachlaufs
# von vier Stunden.
TERMIN = localnow() - timedelta(minutes=10)
TAG = KUERZEL[TERMIN.weekday()]
UHRZEIT = f"{TERMIN:%H:%M}"


def host_anlegen(name, einplanen=True):
    """
    Meldet einen Host an, gibt ihn frei und setzt ihn auf einen faelligen
    Termin. Gibt (id, Kopfzeile fuer den Agenten) zurueck.
    """
    angemeldet = call("/api/v1/agent/enroll", {
        "hostname": name, "os_type": "linux", "os_version": "Debian 12",
        "ip_address": "192.0.2.77", "agent_version": "0.49.0",
    })
    if "agent_token" not in angemeldet:
        raise SystemExit(f"Anmeldung von {name} fehlgeschlagen: {angemeldet}")

    # Die Id ueber den NAMEN suchen. agent-api nimmt an dieser Stelle den
    # ersten Host der Liste - das trifft nur, solange es genau einen gibt,
    # und diese Reihe laeuft danach.
    liste = call("/api/v1/hosts", hdr=adm)
    if not isinstance(liste, list):
        raise SystemExit(f"Hostliste nicht lesbar: {liste}")
    treffer = [h for h in liste if h["hostname"] == name]
    if len(treffer) != 1:
        raise SystemExit(
            f"{name} steht {len(treffer)} mal in der Hostliste, erwartet 1")
    hid = treffer[0]["id"]

    call(f"/api/v1/hosts/{hid}/approve", {}, hdr=adm)
    call(f"/api/v1/hosts/{hid}", {
        "patch_enabled": einplanen,
        "patch_days": [TAG],
        "patch_time": UHRZEIT,
        "patch_grace_hours": 4,
    }, method="PATCH", hdr=adm)
    return hid, {"X-Agent-Token": angemeldet["agent_token"]}


def heartbeat(name, kopf, **extra):
    nutzlast = {
        "hostname": name, "os_type": "linux", "os_version": "Debian 12",
        "ip_address": "192.0.2.77", "agent_version": "0.49.0",
        "reboot_required": False, "reboot_reasons": [],
    }
    nutzlast.update(extra)
    return call("/api/v1/agent/heartbeat", nutzlast, hdr=kopf)


def patch_auftraege(hid):
    """
    ALLE Patch-Auftraege dieses Hosts, gleich in welchem Zustand.

    Absichtlich ohne Filter auf den Zustand: DELETE /api/v1/jobs/{id}
    loescht nicht, es setzt cancelled. Zaehlte man nur die offenen, waere
    die Pruefung unten gruen, ohne last_patch_run ueberhaupt zu beruehren.
    """
    auftraege = call(f"/api/v1/jobs?host_id={hid}", hdr=adm)
    if not isinstance(auftraege, list):
        raise SystemExit(f"Auftragsliste nicht lesbar: {auftraege}")
    return [a for a in auftraege if a.get("job_type") == "patch"]


print(f"--- Termin {TAG} {UHRZEIT} (Ortszeit des Servers) ---")

# ------------------------------------- 1. faellig -> ein Auftrag entsteht
print("--- ein faelliger Termin legt einen Auftrag an ---")

A = "TEST-PLAN01"
a_id, a_kopf = host_anlegen(A)
antwort = heartbeat(A, a_kopf, reboot_pending=False)

geliefert = [j for j in antwort.get("jobs", []) if j.get("type") == "patch"]
check("der Heartbeat liefert den neuen Patch-Auftrag gleich mit",
      len(geliefert) == 1, antwort)
check("der Auftrag ist als geplant gekennzeichnet",
      bool(geliefert) and geliefert[0].get("params", {}).get("scheduled") is True,
      geliefert)
check("und in der Auftragsliste steht genau einer",
      len(patch_auftraege(a_id)) == 1, patch_auftraege(a_id))

# ------------------------- 2. derselbe Termin loest nicht ein zweites Mal
print("--- ein erledigter Termin loest nicht erneut aus ---")

# Den Auftrag erst aus dem Weg nehmen. Bliebe er offen, griffe
# patch_job_open() und es entstuende ohnehin kein zweiter - die Pruefung
# waere gruen, ohne dass last_patch_run daran beteiligt ist.
#
# Ohne die Verzweigung stuerbe die Reihe hier mit einem IndexError,
# sobald Pruefung 1 fehlschlaegt - also genau unter der Mutation, gegen
# die sie gebaut ist. Dann stuende am Ende ein Rueckverfolg statt
# "8 Pruefungen, 3 Fehler", und die Zahl der Pruefungen waere nicht mehr
# vergleichbar. Deshalb melden beide Pruefungen hier einen Fehler und
# sagen, dass sie nicht messbar waren.
vorhanden = patch_auftraege(a_id)
if not vorhanden:
    check("Vorbereitung: der erste Auftrag ist abgebrochen", False,
          "es gibt keinen - Pruefung 1 ist fehlgeschlagen")
    check("kein zweiter Auftrag - der Termin ist mit last_patch_run erledigt",
          False, "nicht messbar, weil kein erster Auftrag entstanden ist")
else:
    weg = call(f"/api/v1/jobs/{vorhanden[0]['id']}", method="DELETE", hdr=adm)
    check("Vorbereitung: der erste Auftrag ist abgebrochen",
          weg.get("ok") is True, weg)

    heartbeat(A, a_kopf, reboot_pending=False)
    check("kein zweiter Auftrag - der Termin ist mit last_patch_run erledigt",
          len(patch_auftraege(a_id)) == 1, patch_auftraege(a_id))

# ------------------------- 3. mit anstehendem Neustart wird nicht geplant
print("--- waehrend ein Neustart aussteht entsteht kein Auftrag ---")

C = "TEST-PLAN02"
c_id, c_kopf = host_anlegen(C)
antwort = heartbeat(C, c_kopf, reboot_pending=True)

# Die Route steigt in diesem Zustand frueh mit jobs: [] aus. Die Antwort
# sagt also nichts darueber, ob ANGELEGT wurde - nur die Liste sagt es.
check("die Antwort liefert nichts aus", antwort.get("jobs") == [], antwort)
check("und es ist auch keiner angelegt worden",
      len(patch_auftraege(c_id)) == 0, patch_auftraege(c_id))

# ---------------------------------- 4. nicht eingeplant heisst kein Auftrag
print("--- ein nicht eingeplanter Host bekommt nichts ---")

D = "TEST-PLAN03"
d_id, d_kopf = host_anlegen(D, einplanen=False)
heartbeat(D, d_kopf, reboot_pending=False)
check("kein Auftrag, obwohl Tag und Uhrzeit passen",
      len(patch_auftraege(d_id)) == 0, patch_auftraege(d_id))

# ------------------------------- 5. Nach einem Neustart wird gescannt
print("--- nach einem erkannten Neustart entsteht ein Scan ---")

# DER ANLASS (gemeldet am 2026-11-12)
#
# host.updates_available wird an genau EINER Stelle geschrieben: in
# /api/v1/agent/scan-result. Der Agent scannt nach einem Patchlauf zwar
# nach, aber VOR dem Neustart - und da zaehlt Windows das installierte,
# auf den Neustart wartende Update noch als offen. Danach scannt nichts
# mehr, also blieb die falsche Zahl stehen, bis jemand von Hand scannte.
#
# Der Server erkennt den Neustart jetzt an host.last_boot gegen die
# gemeldete boot_time und legt einen Scan an.
#
# Diese Hosts werden ABSICHTLICH NICHT eingeplant (einplanen=False).
# Waeren sie faellig, entstuende nebenher ein Patch-Auftrag, und die
# Zaehlung unten pruefte etwas anderes als sie zu pruefen glaubt.

STARTZEIT_ALT = (localnow() - timedelta(hours=3)).timestamp()
STARTZEIT_NEU = (localnow() - timedelta(minutes=10)).timestamp()


def scan_auftraege(hid):
    auftraege = call(f"/api/v1/jobs?host_id={hid}", hdr=adm)
    if not isinstance(auftraege, list):
        raise SystemExit(f"Auftragsliste nicht lesbar: {auftraege}")
    return [a for a in auftraege if a.get("job_type") == "scan"]


E = "TEST-BOOT01"
e_id, e_kopf = host_anlegen(E, einplanen=False)

# Erstes Sehen. Hier darf NICHTS passieren: beim Einspielen dieses
# Updates ist last_boot bei jedem Host leer, und ein Scan an dieser
# Stelle hiesse, dass die ganze Anlage gleichzeitig scannt.
erste_antwort = heartbeat(E, e_kopf, boot_time=STARTZEIT_ALT)
# "jobs" in der Antwort heisst: der Heartbeat ist durchgelaufen. Ohne das
# ist diese Pruefung wertlos - bei einem Serverfehler gibt es auch keinen
# Scan-Auftrag, und sie waere gruen, ohne etwas gemessen zu haben.
# Aufgefallen am 2026-11-12 bei der Mutationsprobe: ein TypeError in der
# Pflege liess den Heartbeat mit 500 antworten, und die Pruefung nickte.
check("erster Heartbeat laeuft durch und merkt sich nur den Startzeitpunkt",
      "jobs" in erste_antwort and len(scan_auftraege(e_id)) == 0,
      (erste_antwort, scan_auftraege(e_id)))

# Derselbe Start noch einmal. boot_time kann um Sekunden wandern, wenn
# NTP die Uhr stellt - daher die Toleranz. Ohne sie entstuende hier bei
# jedem Heartbeat ein Scan.
heartbeat(E, e_kopf, boot_time=STARTZEIT_ALT + 12)
check("derselbe Startzeitpunkt loest keinen Scan aus",
      len(scan_auftraege(e_id)) == 0, scan_auftraege(e_id))

# Jetzt ein echter Neustart.
heartbeat(E, e_kopf, boot_time=STARTZEIT_NEU)
nach_neustart = scan_auftraege(e_id)
check("ein neuer Startzeitpunkt legt genau einen Scan an",
      len(nach_neustart) == 1, nach_neustart)
# Ein reiner Scan. Nach einem Neustart ungefragt zu patchen waere eine
# Verhaltensaenderung, die niemand verlangt hat - und bei einem Server,
# der gerade hochgefahren ist, die falsche.
check("und es ist ein reiner Scan, ohne patch_after",
      bool(nach_neustart)
      and not (nach_neustart[0].get("params") or {}).get("patch_after"),
      nach_neustart)

# Ein Host, der mehrfach startet, soll keine Scans stapeln.
# Fuenf Minuten spaeter: mehr als die Toleranz, also ein neuer Start -
# und immer noch in der Vergangenheit. Hier stand zuerst +7200, das lag
# 110 Minuten in der ZUKUNFT und fiel aus dem Plausibilitaetsband; die
# Pruefung war damit gruen, ohne den Stapelschutz zu beruehren.
heartbeat(E, e_kopf, boot_time=STARTZEIT_NEU + 300)
check("solange der Scan offen ist, kommt kein zweiter dazu",
      len(scan_auftraege(e_id)) == 1, scan_auftraege(e_id))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
