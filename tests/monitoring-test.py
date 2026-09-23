"""
CO-37 - /api/v1/monitoring ist NUR lokal erreichbar und gibt keine
Geheimnisse heraus (seit 0.40.0).

Der Endpunkt speist die Checkmk-Ueberwachung. Er verlangt beides: die
TCP-Gegenstelle ist loopback UND das lokale Token (X-Monitor-Token)
stimmt. Alles andere bekommt 404 - nach aussen soll die Route nicht
einmal existieren. Das ist wichtig, weil ein Reverse Proxy auf demselben
Host als 127.0.0.1 erscheint: ohne die Token-Schranke waere der Endpunkt
ueber so einen Proxy von aussen offen.

Geprueft wird im selben Prozess, wie bei health-info-test.py: das
Testgeruest bindet nichts ans Netz, die Anfragen werden gebaut.

    python3 tests/monitoring-test.py

Braucht kein laufendes Backend und kein Netz.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/mon.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from starlette.requests import Request  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from models import ApprovalState, Host, HostStatus, OSType  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def anfrage(ip, token=None):
    kopf = [(b"x-monitor-token", token.encode())] if token is not None else []
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/api/v1/monitoring", "raw_path": b"/api/v1/monitoring",
        "query_string": b"", "root_path": "", "scheme": "http",
        "server": ("test", 80),
        "client": (ip, 40000) if ip else None,
        "headers": kopf,
    })


def hole(ip, token=None):
    return main.monitoring(anfrage(ip, token), token or "")


TOKEN = main.monitor_token()
check("Token-Datei wird angelegt", bool(TOKEN), TOKEN[:6])
check("Token-Datei hat Rechte 600",
      (main.MONITOR_TOKEN_FILE.stat().st_mode & 0o777) == 0o600,
      oct(main.MONITOR_TOKEN_FILE.stat().st_mode & 0o777))


def erwarte_404(label, ip, token):
    try:
        hole(ip, token)
        check(label, False, "kein 404")
    except HTTPException as e:
        check(label, e.status_code == 404, e.status_code)


# ---------------------------------------------------------------- Schranke
print("--- Nur lokal, nur mit Token ---")
erwarte_404("von aussen mit gueltigem Token: 404", "192.0.2.50", TOKEN)
erwarte_404("loopback ohne Token: 404", "127.0.0.1", None)
erwarte_404("loopback mit falschem Token: 404", "127.0.0.1", "falsch")
erwarte_404("von aussen ohne Token: 404", "203.0.113.9", None)

daten = hole("127.0.0.1", TOKEN)
check("loopback mit richtigem Token: Antwort", isinstance(daten, dict), type(daten))
check("auch ueber ::1", isinstance(hole("::1", TOKEN), dict))


# --------------------------------------------------------------- Struktur
print("--- Struktur ---")
for feld in ("co37", "fleet", "license", "hosts"):
    check(f"Block {feld} vorhanden", feld in daten, list(daten))
for feld in ("hosts_total", "approved", "pending_approval", "online",
             "offline", "updates_total", "security_total",
             "reboot_required", "agents_outdated"):
    check(f"fleet.{feld} vorhanden", feld in daten["fleet"], daten["fleet"])
check("co37.backend_ok ist bool", isinstance(daten["co37"]["backend_ok"], bool))


# -------------------------------------------------------- keine Geheimnisse
print("--- Keine Geheimnisse in der Antwort ---")
roh = json.dumps(daten, ensure_ascii=False)
for verboten in ("agent_token", "token_hash", "password", "secret",
                 "kunde", "nummer", TOKEN):
    check(f"'{verboten if verboten != TOKEN else 'das Token selbst'}' nicht in der Antwort",
          verboten not in roh, verboten)


# ------------------------------------------------------------ mit Hosts
print("--- Zaehlungen mit Hosts ---")
with Session(main.engine) as s:
    now = main.utcnow()
    s.add(Host(hostname="web01", os_type=OSType.linux,
               approval_state=ApprovalState.approved, status=HostStatus.online,
               last_seen=now, agent_version=main.agent_source_version(),
               updates_available=12, security_updates=4, reboot_required=True,
               checkmk_hosts=["web01.lan"], agent_token_hash="x"))
    s.add(Host(hostname="alt01", os_type=OSType.linux,
               approval_state=ApprovalState.approved, status=HostStatus.online,
               last_seen=now, agent_version="0.0.1",  # veraltet
               updates_available=0, security_updates=0, reboot_required=False,
               agent_token_hash="y"))
    s.add(Host(hostname="neu01", os_type=OSType.windows,
               approval_state=ApprovalState.pending, agent_token_hash="z"))
    s.commit()

d2 = hole("127.0.0.1", TOKEN)
fleet = d2["fleet"]
check("zwei freigegebene Hosts", fleet["approved"] == 2, fleet["approved"])
check("ein Host wartet auf Freigabe", fleet["pending_approval"] == 1, fleet["pending_approval"])
check("12 Updates gesamt", fleet["updates_total"] == 12, fleet["updates_total"])
check("4 sicherheitsrelevant", fleet["security_total"] == 4, fleet["security_total"])
check("ein Host braucht Neustart", fleet["reboot_required"] == 1, fleet["reboot_required"])
check("ein Agent veraltet", fleet["agents_outdated"] == 1, fleet["agents_outdated"])
check("Lizenz used == freigegebene", d2["license"]["used"] == 2, d2["license"])

web = next((h for h in d2["hosts"] if h["hostname"] == "web01"), None)
check("web01 ist in der Hostliste", web is not None)
check("web01 traegt die Checkmk-Verknuepfung",
      web and web["checkmk_hosts"] == ["web01.lan"], web)
check("die Hostliste enthaelt kein agent_token_hash",
      "agent_token_hash" not in json.dumps(d2["hosts"]))

# ------------------------------------------------ HTTPS-Zwang-Ausnahme
# Der Local-Check spricht das Backend unverschluesselt ueber Loopback an.
# Ist HTTPS-Zwang an, muss die Middleware /api/v1/monitoring ueber Loopback
# trotzdem durchlassen - sonst kaeme der Check nie an den Endpunkt (403).
print("--- HTTPS-Zwang: Loopback-Ausnahme ---")


def route(pfad, ip):
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": pfad, "raw_path": pfad.encode(), "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("test", 80),
        "client": (ip, 40000) if ip else None, "headers": [],
    })


alt = main._PROXY_CFG["https_only"]
main._PROXY_CFG["https_only"] = True
try:
    check("monitoring ueber Loopback trotz HTTPS-Zwang durchgelassen",
          main.https_only_check(route("/api/v1/monitoring", "127.0.0.1")) is None)
    check("health ueber Loopback weiterhin frei",
          main.https_only_check(route("/api/health", "127.0.0.1")) is None)
    check("monitoring von aussen unter HTTPS-Zwang abgewiesen",
          main.https_only_check(route("/api/v1/monitoring", "192.0.2.50")) is not None)
    check("andere Route ueber Loopback bleibt gesperrt",
          main.https_only_check(route("/api/v1/schema", "127.0.0.1")) is not None)
finally:
    main._PROXY_CFG["https_only"] = alt


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
