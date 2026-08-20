"""
CO-37 - Meldet die Job-Rueckmeldung den Neustartbedarf nach derselben
Regel wie Heartbeat und Scan-Ergebnis?

Grund fuer diese Reihe: host.reboot_required wird im Backend von drei
Stellen geschrieben - Heartbeat, Scan-Ergebnis und Job-Rueckmeldung -
und es gewinnt der letzte Schreiber. Zwei davon rechnen
WEAK_REBOOT_REASONS heraus, die Rueckmeldung tat es nicht. Nach jedem
Windows-Update ohne Neustartbedarf sprang die Oberflaeche deshalb fuer
wenige Sekunden auf "Neustart noetig", bis der naechste Heartbeat es
zuruecknahm: Windows Update merkt praktisch immer Dateien zum Aufraeumen
vor, und genau das ist ein schwacher Grund.

Der wichtigste Fall hier ist der, der NICHT ausloesen darf - schwacher
Grund allein. Die starken Faelle stehen daneben, damit die Korrektur
nicht einfach alles auf "kein Neustart" stellt.

Laeuft ohne Backend und ohne Netz: agent.py wird in ein temporaeres
Verzeichnis kopiert und dort mit einer Wegwerf-agent.conf geladen, weil
das Modul beim Import eine Konfiguration verlangt und sonst aussteigt.

Aufruf:

    python3 tests/reboot-report-test.py
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
QUELLE = HIER.parent / "agent" / "agent.py"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def lade_agent():
    """
    Laedt agent.py aus einer Kopie mit eigener agent.conf.

    Nicht das Original im Repo verwenden: find_config() sucht die
    agent.conf neben der Datei, eine dort abgelegte Datei landete sonst
    im Arbeitsverzeichnis und im Paket.
    """
    tmp = Path(tempfile.mkdtemp(prefix="co37-agent-"))
    ziel = tmp / "agent.py"
    shutil.copy2(QUELLE, ziel)
    (tmp / "agent.conf").write_text(
        "server=http://127.0.0.1:9\ntoken=testtoken\nverify_ssl=false\n",
        encoding="utf-8")
    spec = importlib.util.spec_from_file_location("co37_agent_unter_test", ziel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp


def lauf(mod, job_type, reasons, approved=False):
    """
    Fuehrt einen Auftrag aus, ohne dass etwas installiert wird, und gibt
    die Nutzlast der Rueckmeldung zurueck.

    Alle Aussenkontakte sind ersetzt: kein Netz, keine Registry, kein
    Paketmanager. Gemessen wird ausschliesslich, welchen Wert
    handle_job im Feld reboot_required an das Backend schickt - genau
    das Feld, das die Farbe in der Oberflaeche bestimmt.
    """
    gesehen = []

    def fake_api(method, path, payload=None, *a, **kw):
        gesehen.append((path, payload))
        if path.endswith("/pre-reboot"):
            return {"approved": approved, "reason": "Testlauf"}
        return {}

    mod.api = fake_api
    mod.reboot_reasons = lambda: list(reasons)
    mod.IS_WINDOWS = False
    mod.patch_linux = lambda sink: True
    mod.patch_windows = lambda sink: True
    mod.scan_linux = lambda: []
    mod.scan_windows = lambda: []
    mod.do_scan = lambda job_id=None: {"found": 0, "reboot_required": False}
    mod.trigger_reboot = lambda delay: None

    mod.handle_job({"id": 1, "type": job_type, "params": {}})

    for path, payload in reversed(gesehen):
        if path == "/api/v1/agent/report":
            return payload
    return None


mod, tmp = lade_agent()
try:
    # ------------------------------------------------------------------
    # Der Fall, der nicht ausloesen darf
    # ------------------------------------------------------------------
    p = lauf(mod, "patch", ["pending_rename"])
    check("Patch, nur schwacher Grund: kein Neustart gemeldet",
          p is not None and p.get("reboot_required") is False, p)

    p = lauf(mod, "scan", ["pending_rename"])
    check("Scan, nur schwacher Grund: kein Neustart gemeldet",
          p is not None and p.get("reboot_required") is False, p)

    p = lauf(mod, "unbekannter_typ", ["pending_rename"])
    check("Vorgabeweg ohne uebergebenen Wert, schwacher Grund: kein Neustart",
          p is not None and p.get("reboot_required") is False, p)

    p = lauf(mod, "patch", [])
    check("Patch ohne jeden Grund: kein Neustart gemeldet",
          p is not None and p.get("reboot_required") is False, p)

    # ------------------------------------------------------------------
    # Die Faelle, die weiterhin ausloesen muessen
    # ------------------------------------------------------------------
    p = lauf(mod, "patch", ["windows_update"])
    check("Patch, starker Grund: Neustart gemeldet",
          p is not None and p.get("reboot_required") is True, p)

    p = lauf(mod, "patch", ["pending_rename", "windows_update"])
    check("Patch, schwach und stark gemischt: Neustart gemeldet",
          p is not None and p.get("reboot_required") is True, p)

    p = lauf(mod, "scan", ["cbs_pending"])
    check("Scan, starker Grund: Neustart gemeldet",
          p is not None and p.get("reboot_required") is True, p)

    p = lauf(mod, "unbekannter_typ", ["netlogon"])
    check("Vorgabeweg ohne uebergebenen Wert, starker Grund: Neustart",
          p is not None and p.get("reboot_required") is True, p)

    # ------------------------------------------------------------------
    # Die Regel selbst, damit ein Umbau der Auftragswege sie nicht
    # unbemerkt aushebelt
    # ------------------------------------------------------------------
    mod.reboot_reasons = lambda: ["pending_rename"]
    check("reboot_required_strong ignoriert schwache Gruende",
          mod.reboot_required_strong() is False)
    mod.reboot_reasons = lambda: ["pending_rename", "cbs_packages"]
    check("reboot_required_strong greift bei starkem Grund",
          mod.reboot_required_strong() is True)

    check("pending_rename gilt als schwacher Grund",
          "pending_rename" in mod.WEAK_REBOOT_REASONS)
    check("windows_update gilt nicht als schwacher Grund",
          "windows_update" not in mod.WEAK_REBOOT_REASONS)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
