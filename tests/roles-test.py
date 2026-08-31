"""
CO-37 - Rollentrennung und Paket-Schluessel.

Grund fuer dieses Geruest: die Pruefung hiess require_admin, liess aber
jeden angemeldeten Benutzer durch. Ein Konto mit der Rolle 'user' konnte
Hosts freigeben, die Checkmk-Zugangsdaten aendern und - ueber das
Hochladen eines Systemupdates, das der Watcher als root auspackt - Code
als root ausfuehren. Aufgefallen ist das nur beim Durchlesen, weil kein
Test die Rollen geprueft hat.

Braucht ein laufendes Backend mit eigener Testdatenbank.

    python3 tests/roles-test.py

ACHTUNG - als LETZTER Test gegen ein Backend laufen lassen. Am Ende wird
die Drosselung der Anmeldung ausgeloest; die gilt fuer die gesamte
Absender-Adresse und bleibt eine Viertelstunde bestehen. Jeder danach
gestartete Test, der sich anmeldet, liefe in die Sperre und meldete
Fehler, die nichts mit ihm zu tun haben. Der Zaehler liegt bewusst nur im
Arbeitsspeicher - ein Neustart des Backends leert ihn.
"""
import json
import os
import urllib.error
import urllib.request

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
ADMIN_SESSION = os.getenv("CO37_TEST_SESSION", "")

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def headers_of(path):
    """Antwortkopfzeilen einer Route, oder None bei einem Fehler."""
    try:
        return urllib.request.urlopen(B + path, timeout=30).headers
    except urllib.error.HTTPError as e:
        return e.headers
    except Exception:  # noqa: BLE001
        return None


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
        return e.code, e.read().decode()[:200]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


adm = {"X-Session": ADMIN_SESSION}

# Einen Benutzer mit der Rolle 'user' anlegen und anmelden
call("/api/v1/users", {"username": "tester", "password": "tester-passwort",
                       "role": "user"}, hdr=adm)
code, res = call("/api/v1/login", {"username": "tester",
                                   "password": "tester-passwort"})
check("Benutzer kann sich anmelden", code == 200, code)
usr = {"X-Session": res.get("session", "")} if code == 200 else {}
check("Rolle ist nicht Administrator", res.get("role") == "user", res.get("role"))

# --------------------------------------------------- verboten fuer 'user'
VERBOTEN = [
    ("POST",   "/api/v1/hosts/1/approve", {}),
    ("POST",   "/api/v1/hosts/1/reject", {}),
    ("DELETE", "/api/v1/hosts/1", None),
    ("PATCH",  "/api/v1/hosts/1", {"notes": "x"}),
    ("GET",    "/api/v1/checkmk/status", None),
    ("GET",    "/api/v1/checkmk/hosts", None),
    ("POST",   "/api/v1/checkmk/config", {"url": "http://x", "site": "s",
                                          "user": "u", "secret": "p"}),
    ("GET",    "/api/v1/update/status", None),
    ("POST",   "/api/v1/update/trigger", {}),
    ("POST",   "/api/v1/update/cancel", {}),
    ("GET",    "/api/v1/users", None),
    ("POST",   "/api/v1/users", {"username": "x", "password": "yyyyyyyy"}),
    ("GET",    "/api/v1/audit", None),
    # Die Vorgabesprache gilt fuer alle und fuer die Anmeldeseite - das ist
    # eine Einstellung der Installation, keine Anzeigevorliebe.
    ("POST",   "/api/v1/settings/language", {"language": "de"}),
    ("POST",   "/api/v1/install-token", {}),
    ("GET",    "/api/v1/install-token", None),
    ("GET",    "/api/v1/packages", None),
    ("POST",   "/api/v1/packages/build", {}),
    ("GET",    "/api/v1/install-script", None),
    ("GET",    "/api/v1/agent-rollout", None),
    ("POST",   "/api/v1/agent-rollout/start", {}),
    ("GET",    "/api/v1/diagnostics", None),
    ("GET",    "/api/v1/watcher", None),
    ("GET",    "/api/v1/schema", None),
    # Bereiche tragen eine Checkmk-Verknuepfung und einen Update-Zeitplan -
    # genauso schuetzenswert wie die entsprechenden Host-Felder oben.
    ("POST",   "/api/v1/areas", {"name": "x"}),
    ("PATCH",  "/api/v1/areas/1", {"name": "y"}),
    ("DELETE", "/api/v1/areas/1", None),
    ("POST",   "/api/v1/areas/order", {"ids": [1]}),
    ("POST",   "/api/v1/hosts/1/area", {"area_id": None}),
]
for method, path, body in VERBOTEN:
    code, res = call(path, body, method=method, hdr=usr)
    check(f"verboten: {method} {path}", code == 403, code)

# --------------------------------------------------- erlaubt fuer 'user'
ERLAUBT = [
    ("GET", "/api/v1/hosts", None),
    ("GET", "/api/v1/jobs", None),
    ("GET", "/api/v1/jobs/active", None),
    ("GET", "/api/v1/me", None),
    # Nur lesen darf jeder - die gruppierte Ansicht ist reine Anzeige,
    # bearbeiten bleibt oben in VERBOTEN dem Administrator vorbehalten.
    ("GET", "/api/v1/areas", None),
    # Die eigene Sprache ist eine Anzeigeeinstellung. Sie einem Benutzer zu
    # verwehren waere schikanoes und truege nichts zur Sicherheit bei.
    ("POST", "/api/v1/me/language", {"language": "de"}),
]
for method, path, body in ERLAUBT:
    code, res = call(path, body, method=method, hdr=usr)
    check(f"erlaubt: {method} {path}", code == 200, code)

code, res = call("/api/v1/me/password",
                 {"old_password": "tester-passwort",
                  "new_password": "neues-passwort-123"},
                 hdr=usr)
check("eigenes Passwort aenderbar", code == 200, code)

# --------------------------------------------------- Installations-Token
code, res = call("/api/v1/install-token", {}, hdr=adm)
check("Administrator erhaelt ein Token", code == 200, code)
tok = res.get("token", "") if code == 200 else ""
# Hiess frueher "Token ist nicht der API-Key". Den gibt es nicht mehr;
# verglichen wird jetzt gegen die Sitzung, mit der diese Reihe arbeitet -
# ein Installations-Token, das die eigene Sitzung zurueckgaebe, waere
# derselbe Fehler in neuer Form.
check("Installations-Token ist nicht die Sitzung",
      tok and tok != ADMIN_SESSION,
      "gleich!" if tok == ADMIN_SESSION else "")
check("Frist wird mitgeliefert", bool(res.get("expires_at")), res.get("expires_at"))
max_uses = res.get("max_uses", 0)

code, _ = call("/api/v1/packages/gibtsnicht.deb", hdr={"X-Install-Token": tok})
check("Token kommt an den Download", code == 404, code)

code, _ = call("/api/v1/packages/gibtsnicht.deb", hdr={"X-Install-Token": "falsch"})
check("falsches Token abgewiesen", code == 401, code)

code, _ = call("/api/v1/hosts", hdr={"X-Install-Token": tok})
check("Token taugt sonst zu nichts", code == 401, code)

# Abrufe sind begrenzt. Einer ist oben schon verbraucht.
for _ in range(max_uses - 1):
    call("/api/v1/packages/gibtsnicht.deb", hdr={"X-Install-Token": tok})
code, _ = call("/api/v1/packages/gibtsnicht.deb", hdr={"X-Install-Token": tok})
check(f"nach {max_uses} Abrufen verbraucht", code == 401, code)

# Zwei Token gleichzeitig, eines wieder zuruecknehmen
code, a = call("/api/v1/install-token", {}, hdr=adm)
code, b = call("/api/v1/install-token", {}, hdr=adm)
code, offen = call("/api/v1/install-token", hdr=adm)
check("offene Token werden aufgelistet", code == 200 and len(offen) >= 2, len(offen) if code == 200 else code)
check("Klartext taucht in der Liste nicht auf",
      all("token" not in row for row in offen))

# Gezielt das eigene Token zuruecknehmen, nicht das erste der Liste: es
# koennen andere offen sein, etwa aus einem vorher gelaufenen Test.
check("Erzeugung liefert eine Kennung", bool(a.get("id")), a.get("id"))
code, _ = call(f"/api/v1/install-token/{a['id']}", method="DELETE", hdr=adm)
check("Token zuruecknehmbar", code == 200, code)
code, _ = call("/api/v1/packages/gibtsnicht.deb", hdr={"X-Install-Token": a["token"]})
check("zurueckgenommenes Token wirkungslos", code == 401, code)

# --------------------------------------------------- Hostnamen-Pruefung
BOESE = ["a'); alert(1);//", "<script>x</script>", "hat leer", "", "." * 5,
         "a" * 80]
for name in BOESE:
    code, _ = call("/api/v1/agent/enroll",
                   {"hostname": name, "os_type": "linux",
                    "os_version": "x", "agent_version": "0.1"})
    check(f"Hostname abgewiesen: {name[:24]!r}", code == 400, code)

code, res = call("/api/v1/agent/enroll",
                 {"hostname": "TEST-LNX01", "os_type": "linux",
                  "os_version": "Debian 13", "agent_version": "0.20.2"})
check("gueltiger Hostname angenommen", code == 200, code)

# --------------------------------------------------- Agent-Token zuruecknehmen
code, res = call("/api/v1/agent/enroll",
                 {"hostname": "TEST-RESET01", "os_type": "linux",
                  "os_version": "Debian 13", "agent_version": "0.23.0"})
check("Host fuer den Token-Test angemeldet", code == 200, code)
alt_token = res.get("agent_token", "")
hosts = call("/api/v1/hosts", hdr=adm)[1]
rid = [h["id"] for h in hosts if h["hostname"] == "TEST-RESET01"][0]
call(f"/api/v1/hosts/{rid}/approve", {}, hdr=adm)

code, _ = call("/api/v1/agent/heartbeat",
               {"hostname": "TEST-RESET01", "os_type": "linux",
                "os_version": "Debian 13", "agent_version": "0.23.0"},
               hdr={"X-Agent-Token": alt_token})
check("altes Token funktioniert zunaechst", code == 200, code)

code, _ = call(f"/api/v1/hosts/{rid}/reset-token", {}, hdr=adm)
check("Token zuruecknehmbar", code == 200, code)

code, _ = call("/api/v1/agent/heartbeat",
               {"hostname": "TEST-RESET01", "os_type": "linux",
                "os_version": "Debian 13", "agent_version": "0.23.0"},
               hdr={"X-Agent-Token": alt_token})
check("altes Token danach abgewiesen", code == 401, code)

# Ein leeres Token darf nicht auf den NULL-Wert in der Datenbank treffen.
code, _ = call("/api/v1/agent/heartbeat",
               {"hostname": "TEST-RESET01", "os_type": "linux",
                "os_version": "Debian 13", "agent_version": "0.23.0"},
               hdr={"X-Agent-Token": ""})
check("leeres Token trifft nicht auf den offenen Host", code in (401, 422), code)

code, res = call("/api/v1/agent/enroll",
                 {"hostname": "TEST-RESET01", "os_type": "linux",
                  "os_version": "Debian 13", "agent_version": "0.23.0"})
check("erneute Anmeldung moeglich", code == 200, code)
neu = res.get("agent_token", "")
check("neues Token unterscheidet sich", neu and neu != alt_token)
check("wartet wieder auf Freigabe", res.get("approval_state") == "pending",
      res.get("approval_state"))

hosts = call("/api/v1/hosts", hdr=adm)[1]
same = [h for h in hosts if h["hostname"] == "TEST-RESET01"]
check("Host wurde nicht neu angelegt", len(same) == 1 and same[0]["id"] == rid,
      [h["id"] for h in same])

# Solange ein Token gilt, bleibt die Namenssperre bestehen
code, _ = call("/api/v1/agent/enroll",
               {"hostname": "TEST-RESET01", "os_type": "linux",
                "os_version": "Debian 13", "agent_version": "0.23.0"})
check("mit gueltigem Token weiterhin 409", code == 409, code)

# --------------------------------------------------- Sitzungscookie
# Das Token liegt nicht mehr im Browser, sondern in einem Cookie mit
# HttpOnly - eingeschleustes Skript kommt nicht mehr heran.
import http.cookiejar  # noqa: E402

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
req = urllib.request.Request(
    B + "/api/v1/login",
    data=json.dumps({"username": "admin", "password": "admin"}).encode(),
    headers={"Content-Type": "application/json"})
resp = opener.open(req, timeout=30)
roh = resp.headers.get("Set-Cookie") or ""

check("Anmeldung setzt ein Cookie", "co37_session=" in roh, roh[:60])
check("Cookie ist HttpOnly", "httponly" in roh.lower(), roh)
check("Cookie ist SameSite=Strict", "samesite=strict" in roh.lower(), roh)
# Secure nur bei aktivem HTTPS-Zwang - sonst naehme der Browser es ueber
# den unverschluesselten Zugang nicht an und niemand kaeme mehr hinein.
check("ohne HTTPS-Zwang kein Secure-Merker", "secure" not in roh.lower(), roh)
check("Token steht weiterhin auch im Rumpf (Rueckweg fuer Skripte)",
      bool(json.loads(resp.read()).get("session")))

# Mit dem Cookie allein muss der Zugriff gehen - ohne Kopfzeile.
r = urllib.request.Request(B + "/api/v1/me")
check("Cookie allein genuegt", opener.open(r, timeout=30).status == 200)

# Abmelden muss das Cookie loeschen.
r = urllib.request.Request(B + "/api/v1/logout", data=b"{}",
                           headers={"Content-Type": "application/json"})
aus = opener.open(r, timeout=30)
check("Abmelden loescht das Cookie",
      "co37_session=" in (aus.headers.get("Set-Cookie") or ""),
      aus.headers.get("Set-Cookie"))
try:
    opener.open(urllib.request.Request(B + "/api/v1/me"), timeout=30)
    check("nach dem Abmelden kein Zugriff mehr", False, "ging trotzdem")
except urllib.error.HTTPError as e:
    check("nach dem Abmelden kein Zugriff mehr", e.code == 401, e.code)

# --------------------------------------------------- Lizenz und Host-Limit
code, res = call("/api/v1/license", hdr=adm)
check("Lizenzstand abrufbar", code == 200, code)
check("ohne Schluessel gilt der Freibetrag",
      res.get("erlaubte_hosts") == res.get("frei_ohne_schluessel"),
      res.get("erlaubte_hosts"))
frei = res.get("frei_ohne_schluessel", 10)
check("Freibetrag ist 10", frei == 10, frei)

# Auch fuer einen Benutzer lesbar: wer am Limit scheitert, soll den Grund
# nachvollziehen koennen.
#
# Neu anmelden noetig: weiter oben hat der Test das Passwort dieses Kontos
# geaendert, und das beendet bestehende Sitzungen.
code, res2 = call("/api/v1/login", {"username": "tester",
                                    "password": "neues-passwort-123"})
usr2 = {"X-Session": res2.get("session", "")} if code == 200 else {}
code, _ = call("/api/v1/license", hdr=usr2)
check("Lizenzstand auch fuer Benutzer lesbar", code == 200, code)

code, _ = call("/api/v1/license", {"key": "Unsinn"}, hdr=adm)
check("ungueltiger Schluessel wird abgewiesen", code == 400, code)
code, res = call("/api/v1/license", hdr=adm)
check("Zustand bleibt danach unveraendert",
      res.get("erlaubte_hosts") == frei, res.get("erlaubte_hosts"))

# Bis zum Freibetrag auffuellen und die Grenze pruefen. Die bereits
# angelegten Hosts mitzaehlen.
belegt = call("/api/v1/license", hdr=adm)[1]["belegt"]
angelegt = []
for i in range(belegt, frei + 1):
    name = f"TEST-LIMIT{i:02d}"
    c, r = call("/api/v1/agent/enroll",
                {"hostname": name, "os_type": "linux",
                 "os_version": "Debian 13", "agent_version": "0.30.0"})
    if c != 200:
        continue
    hosts = call("/api/v1/hosts", hdr=adm)[1]
    hid = [h["id"] for h in hosts if h["hostname"] == name][0]
    angelegt.append((name, hid, call(f"/api/v1/hosts/{hid}/approve", {}, hdr=adm)[0]))

erlaubt = [a for a in angelegt if a[2] == 200]
verweigert = [a for a in angelegt if a[2] == 403]
check("Freigabe bis zum Freibetrag moeglich", len(erlaubt) >= 1, len(erlaubt))
check("darueber hinaus abgewiesen", len(verweigert) >= 1, len(verweigert))

code, res = call("/api/v1/license", hdr=adm)
check("Belegung entspricht dem Freibetrag", res.get("belegt") == frei,
      f"{res.get('belegt')} von {frei}")

# Wird ein Host geloescht, wird der Platz wieder frei.
if erlaubt and verweigert:
    call(f"/api/v1/hosts/{erlaubt[0][1]}", method="DELETE", hdr=adm)
    code, _ = call(f"/api/v1/hosts/{verweigert[0][1]}/approve", {}, hdr=adm)
    check("nach dem Loeschen wird der Platz frei", code == 200, code)

# Ein bereits freigegebener Host darf erneut freigegeben werden, auch am
# Limit - sonst scheitert ein harmloser Doppelklick.
if erlaubt:
    ziel = [a for a in erlaubt if a[1] != erlaubt[0][1]]
    if ziel:
        code, _ = call(f"/api/v1/hosts/{ziel[0][1]}/approve", {}, hdr=adm)
        check("erneute Freigabe am Limit moeglich", code == 200, code)

# =====================================================================
# F-22 - Neustarts sind ein eigenes Recht
# =====================================================================
# Bis 0.37.6 durfte jedes angemeldete Konto auf JEDEM freigegebenen Host
# jede Auftragsart anlegen. Beim Neustart war das besonders bitter, weil
# create_job() dabei selbst params["manual"]=True setzt - und der uebergeht
# Wartungsfenster UND Neustartrichtlinie. Ein einziger Aufruf startete
# damit einen Produktivserver mitten am Tag neu.
print("\n--- Neustart-Recht je Konto ---")

# Neu anmelden: weiter oben hat dieselbe Reihe das Passwort des Testkontos
# geaendert, und ein Passwortwechsel verwirft alle Sitzungen des Kontos.
# Ohne das antwortet hier alles mit 401 und die Pruefung sagt nichts aus.
code, res = call("/api/v1/login", {"username": "tester",
                                   "password": "neues-passwort-123"})
check("Testkonto meldet sich mit dem neuen Passwort an", code == 200, code)
usr = {"X-Session": res.get("session", "")} if code == 200 else {}

# Einen bereits freigegebenen Host mitbenutzen statt einen neuen anzulegen:
# der Freibetrag der Lizenz ist an dieser Stelle der Reihe schon
# ausgereizt, eine weitere Freigabe scheiterte und create_job() antwortete
# dann mit 400 ("Host ist nicht freigegeben") statt mit dem 403, um das es
# hier geht.
hosts = call("/api/v1/hosts", hdr=adm)[1]
hid = next((h["id"] for h in hosts
            if h.get("approval_state") == "approved"), None) \
    if isinstance(hosts, list) else None
check("ein freigegebener Host steht zur Verfuegung", hid is not None)
if hid:
    # Ohne das Recht: Scan und Patch ja, Neustart nein.
    code, _ = call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "scan"}, hdr=usr)
    check("ohne Recht: Scan erlaubt", code == 200, code)
    code, _ = call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "patch"}, hdr=usr)
    check("ohne Recht: Patch erlaubt", code == 200, code)
    code, _ = call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "reboot"}, hdr=usr)
    check("ohne Recht: Neustart abgewiesen", code == 403, code)
    # Die Selbstaktualisierung ist keine Bedienhandlung: sie laeuft
    # automatisch beim Heartbeat und von Hand ueber die Einstellungen,
    # beide Wege mit Staffelung und Doppel-Auftrag-Sperre.
    code, _ = call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "selfupdate"},
                   hdr=usr)
    check("ohne Recht: Selbstaktualisierung abgewiesen", code == 403, code)

    # Die Oberflaeche muss es erfahren, sonst stellt sie einen Knopf hin,
    # der 403 liefert.
    code, me = call("/api/v1/me", hdr=usr)
    check("/me meldet may_reboot=false", me.get("may_reboot") is False, me)

    liste = call("/api/v1/users", hdr=adm)[1]
    tester = next((u for u in liste if u["username"] == "tester"), None) \
        if isinstance(liste, list) else None
    check("der Testbenutzer steht in der Liste", tester is not None)

    if tester:
        code, _ = call(f"/api/v1/users/{tester['id']}/rechte",
                       {"may_reboot": True}, method="PATCH", hdr=usr)
        check("ein Benutzer darf sich das Recht nicht selbst geben",
              code == 403, code)
        code, _ = call(f"/api/v1/users/{tester['id']}/rechte",
                       {"may_reboot": True}, method="PATCH", hdr=adm)
        check("ein Administrator darf es vergeben", code == 200, code)

        # Das Vergeben verwirft die Sitzungen des Kontos. Ein Recht, das
        # erst bei der naechsten Anmeldung greift, ist beim Entziehen keines.
        code, _ = call("/api/v1/me", hdr=usr)
        check("die alte Sitzung ist danach ungueltig", code == 401, code)

        code, res = call("/api/v1/login", {"username": "tester",
                                           "password": "neues-passwort-123"})
        usr2 = {"X-Session": res.get("session", "")} if code == 200 else {}
        check("/me meldet may_reboot=true",
              call("/api/v1/me", hdr=usr2)[1].get("may_reboot") is True)
        code, _ = call(f"/api/v1/hosts/{hid}/jobs", {"job_type": "reboot"},
                       hdr=usr2)
        check("mit Recht: Neustart erlaubt", code == 200, code)

        code, _ = call(f"/api/v1/users/{tester['id']}/rechte",
                       {"may_reboot": False}, method="PATCH", hdr=adm)
        check("das Recht laesst sich entziehen", code == 200, code)

    # ----------------------------------------------- Steuerflaggen
    # params war ein freies dict, aus dem das Backend seine EIGENEN Flaggen
    # liest: manual, skip_downtime, allow_without_downtime, followup. Jede
    # dieser Bremsen liess sich vom Aufrufer abschalten.
    print("--- Steuerflaggen kommen nicht vom Aufrufer ---")
    code, job = call(f"/api/v1/hosts/{hid}/jobs",
                     {"job_type": "scan",
                      "params": {"manual": True, "skip_downtime": True,
                                 "allow_without_downtime": True,
                                 "followup": True, "erfunden": "x"}},
                     hdr=adm)
    check("der Auftrag wird angelegt", code == 200, code)
    if code == 200:
        p = job.get("params") or {}
        for flagge in ("manual", "skip_downtime", "allow_without_downtime",
                       "followup", "erfunden"):
            check(f"  {flagge} wurde verworfen", flagge not in p, p)

    # Ein fremder Bereich waere das Ausleihen einer fremden
    # Downtime-Einstellung - bis hin zu einer mit checkmk_downtime_all.
    code, _ = call(f"/api/v1/hosts/{hid}/jobs",
                   {"job_type": "patch",
                    "params": {"source_area_id": 999999}}, hdr=adm)
    check("ein fremder Bereich wird abgewiesen", code == 400, code)


# --------------------------------------------------- Sicherheitskopfzeilen
h = headers_of("/api/health")
check("Kopfzeilen ueberhaupt vorhanden", h is not None)
if h:
    csp = h.get("Content-Security-Policy") or ""
    check("Content-Security-Policy gesetzt", bool(csp), csp[:40])
    # Der eigentliche Gewinn: eingeschleustes Skript kann das Sitzungstoken
    # nirgendwohin senden und keinen fremden Code nachladen.
    check("connect-src auf die eigene Herkunft", "connect-src 'self'" in csp)
    check("script-src auf die eigene Herkunft", "script-src 'self'" in csp)
    check("nicht einbettbar (Clickjacking)", "frame-ancestors 'none'" in csp)
    check("X-Frame-Options gesetzt", h.get("X-Frame-Options") == "DENY",
          h.get("X-Frame-Options"))
    check("X-Content-Type-Options gesetzt",
          h.get("X-Content-Type-Options") == "nosniff",
          h.get("X-Content-Type-Options"))
    check("Referrer-Policy gesetzt", h.get("Referrer-Policy") == "no-referrer",
          h.get("Referrer-Policy"))
    # Ohne CO37_CORS_ORIGINS darf keine fremde Herkunft erlaubt sein.
    check("keine offene CORS-Freigabe",
          h.get("Access-Control-Allow-Origin") in (None, ""),
          h.get("Access-Control-Allow-Origin"))

# --------------------------------------------------- Drosselung ueber HTTP
# Die Feinheiten prueft tests/login-throttle-test.py ohne HTTP. Hier geht
# es nur darum, dass die Route sie ueberhaupt anwendet und 429 liefert.
for _ in range(8):
    code, _ = call("/api/v1/login", {"username": "gibtsnicht",
                                     "password": "falsch"})
check("Anmeldung wird nach mehreren Fehlversuchen gedrosselt", code == 429, code)

code, _ = call("/api/v1/login", {"username": "admin", "password": "admin"})
check("auch richtige Zugangsdaten sind waehrend der Sperre abgewiesen",
      code == 429, code)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
