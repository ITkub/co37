#!/usr/bin/env python3
"""
CO-37 - Update-Watcher

Laeuft als root auf dem Host und beobachtet den Update-Ordner. Sobald das
Backend ein geprueftes Paket auf 'triggered' setzt, uebernimmt dieses Skript:

    sichern -> entpacken -> Abhaengigkeiten -> Dienst neu starten
    -> Health-Check -> bei Fehlschlag zurueckrollen

Aufbau analog TK-37 (update_watcher.py). Unterschied: dort werden Docker-Images
neu gebaut, hier wird Code getauscht und ein systemd-Dienst neu gestartet.

Warum getrennt vom Backend: das Backend laeuft unprivilegiert, darf nicht in
sein eigenes Programmverzeichnis schreiben und kann sich nicht selbst neu
starten. Wuerde es das koennen, waere ein Upload-Endpunkt gleichbedeutend mit
Codeausfuehrung als root.

Installation:
    cp update_watcher.py /opt/co37/
    cp co37-watcher.service /etc/systemd/system/
    systemctl enable --now co37-watcher
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(os.getenv("CO37_BASE", "/opt/co37"))
DATA_DIR = BASE / "data"
UPDATE_DIR = DATA_DIR / "update"
INCOMING_ZIP = UPDATE_DIR / "incoming.zip"
STATUS_FILE = UPDATE_DIR / "status.json"
WORK_DIR = UPDATE_DIR / "_work"

BACKUP_DIR = BASE / "update_backups"
KEEP_BACKUPS = 3

SERVICE = "co37-backend"
VENV_PIP = BASE / "venv" / "bin" / "pip"
HEALTH_URL = os.getenv("CO37_HEALTH_URL", "http://127.0.0.1:8080/api/health")
HEALTH_TIMEOUT = 120

# Diese Verzeichnisse werden ersetzt. 'data' bleibt unangetastet.
MANAGED_DIRS = ["backend", "frontend", "agent", "packaging"]

# Dateien auf oberster Ebene. Ohne sie blieben Hilfsskripte auf dem Stand
# der Erstinstallation - Korrekturen daran erreichten den Betrieb nie.
# Die systemd-Units stehen bewusst NICHT hier: setup.sh schreibt sie inline
# mit eingesetzten Pfaden und Schluesseln, build_deb.py bettet die Agent-Unit
# als Zeichenkette ein. Eine zusaetzliche .service-Datei im Projekt waere eine
# zweite Wahrheit, die niemand liest - wer sie aendert, aendert nichts.
MANAGED_FILES = [
    "build_packages.sh",
    "build_release.sh",
    "setup.sh",
    "README.md",
    "REVERSE-PROXY.md",
    "run-tests.sh",
    "GITHUB.md",
    "LICENSE",
    "build_release.py",
]

# Der Watcher ersetzt sich selbst. Die neue Fassung wird uebernommen, der
# Dienst aber erst am Ende neu gestartet - sonst wuerde der laufende
# Vorgang mitten im Austausch abgebrochen.
SELF_FILE = "update_watcher.py"

# Dateien, die ein Update nicht loeschen darf.
#
# MANAGED_DIRS werden vor dem Kopieren geloescht - anders liessen sich
# entfallene Dateien nie entfernen. Diese hier entscheiden aber, wem die
# Installation vertraut, und sie entstehen beim Herausgeber, nicht im
# Paket. Ein Paket ohne sie wuerde die Signaturpruefung stillschweigend
# abschalten: ohne release_key.pub prueft CO-37 nichts mehr.
#
# Genau so ist es passiert - ein Paket, das den Schluessel nicht enthielt,
# und danach ging jedes weitere Update ohne Signatur durch.
BEWAHRTE_DATEIEN = [
    "backend/release_key.pub",
    "backend/license_key.pub",
]

POLL_SECONDS = 10

# Kennung dieser Watcher-Fassung. Wird beim Start hinterlegt, damit in der
# Oberflaeche sichtbar ist, welcher Watcher tatsaechlich laeuft. Ohne das
# bleibt ein veralteter Watcher unbemerkt - und weil die Faehigkeit, sich
# selbst zu erneuern, erst ab 0.4.3 vorhanden ist, kann er sich aus eigener
# Kraft nie aktualisieren.
WATCHER_VERSION = "0.36.1"
WATCHER_INFO = UPDATE_DIR / "watcher.json"
WATCHER_FEATURES = ["managed_files", "self_update", "package_rebuild", "build_request"]


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def read_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {"state": "idle"}


def write_status(data: dict):
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(STATUS_FILE)
    # Backend laeuft als co37 und muss die Datei lesen koennen
    try:
        shutil.chown(STATUS_FILE, user="co37", group="co37")
    except Exception:  # noqa: BLE001
        pass


def append_log(status: dict, line: str):
    status.setdefault("log", []).append(f"[{datetime.now():%H:%M:%S}] {line}")
    write_status(status)
    log(line)


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ----------------------------------------------------------------------
LAST_HEALTH_ERROR = ""


def health_ok() -> bool:
    """
    Der Health-Endpunkt prueft auch das Datenbankschema. Passt es nicht zum
    Programmstand, liefert er 503 - dann wird zurueckgerollt.
    """
    global LAST_HEALTH_ERROR
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            if resp.status != 200:
                LAST_HEALTH_ERROR = f"HTTP {resp.status}"
                return False
            data = json.loads(resp.read().decode())
            if data.get("status") != "ok":
                LAST_HEALTH_ERROR = str(data)[:300]
                return False
            LAST_HEALTH_ERROR = ""
            return True
    except urllib.error.HTTPError as exc:
        try:
            LAST_HEALTH_ERROR = exc.read().decode()[:300]
        except Exception:  # noqa: BLE001
            LAST_HEALTH_ERROR = f"HTTP {exc.code}"
        return False
    except Exception as exc:  # noqa: BLE001
        LAST_HEALTH_ERROR = str(exc)[:200]
        return False


def wait_for_health(timeout: int = HEALTH_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health_ok():
            return True
        time.sleep(3)
    return False


# ----------------------------------------------------------------------
def backup_current(tag: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / tag
    dest.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_DIRS:
        src = BASE / name
        if src.exists():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
    for name in MANAGED_FILES + [SELF_FILE]:
        src = BASE / name
        if src.is_file():
            shutil.copy2(src, dest / name)

    backups = sorted(
        [p for p in BACKUP_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[KEEP_BACKUPS:]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def restore_backup(src: Path):
    # Auch hier bewahren. Die Sicherung stammt vom Stand vor dem Update -
    # enthielt schon der Schluessel, ist alles gut. Wurde er dagegen erst
    # nach der Sicherung angelegt, ginge er beim Zurueckrollen verloren,
    # und die Signaturpruefung waere danach still abgeschaltet.
    bewahrt = {}
    for rel in BEWAHRTE_DATEIEN:
        p = BASE / rel
        if p.is_file():
            bewahrt[rel] = p.read_bytes()

    for name in MANAGED_DIRS:
        target = BASE / name
        source = src / name
        if not source.exists():
            continue
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target)

    for rel, inhalt in bewahrt.items():
        ziel = BASE / rel
        if ziel.is_file():
            continue
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(inhalt)
    for name in MANAGED_FILES + [SELF_FILE]:
        source = src / name
        if source.is_file():
            shutil.copy2(source, BASE / name)
    run(["chown", "-R", "co37:co37", str(BASE)])


def find_update_root(extract_dir: Path) -> Path:
    entries = [e for e in extract_dir.iterdir() if e.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        candidate = entries[0]
        if (candidate / "backend").exists():
            return candidate
    return extract_dir


def swap_in_new_code(root: Path) -> bool:
    """
    Tauscht Verzeichnisse und Dateien auf oberster Ebene aus.
    Gibt zurueck, ob sich der Watcher selbst geaendert hat.
    """
    # Vor dem Loeschen sichern, danach zurueckschreiben, sofern das Paket
    # sie nicht selbst mitbringt.
    bewahrt = {}
    for rel in BEWAHRTE_DATEIEN:
        p = BASE / rel
        if p.is_file():
            bewahrt[rel] = p.read_bytes()

    for name in MANAGED_DIRS:
        source = root / name
        if not source.exists():
            continue
        target = BASE / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(source, target)

    for rel, inhalt in bewahrt.items():
        ziel = BASE / rel
        if ziel.is_file():
            continue          # Paket bringt die Datei mit - dann gilt sie
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(inhalt)
        log(f"{rel} aus dem bisherigen Stand uebernommen "
            f"(im Paket nicht enthalten)")

    for name in MANAGED_FILES:
        source = root / name
        if source.is_file():
            shutil.copy2(source, BASE / name)

    self_changed = False
    new_self = root / SELF_FILE
    if new_self.is_file():
        old = (BASE / SELF_FILE).read_bytes() if (BASE / SELF_FILE).is_file() else b""
        if new_self.read_bytes() != old:
            shutil.copy2(new_self, BASE / SELF_FILE)
            self_changed = True

    run(["chown", "-R", "co37:co37", str(BASE)])
    # Skripte muessen ausfuehrbar bleiben
    for name in MANAGED_FILES + [SELF_FILE]:
        f = BASE / name
        if f.is_file() and f.suffix in (".sh", ".py"):
            f.chmod(0o755)
    return self_changed


BUILD_REQUEST = UPDATE_DIR / "build_request.json"
BUILD_STATUS = UPDATE_DIR / "build_status.json"


def write_build_status(data: dict):
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BUILD_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(BUILD_STATUS)
    try:
        shutil.chown(BUILD_STATUS, user="co37", group="co37")
    except Exception:  # noqa: BLE001
        pass


def build_packages(log_lines: list[str] = None) -> tuple[bool, list[str]]:
    """
    Baut die Agent-Pakete. Wird nach jedem Update aufgerufen und kann
    zusaetzlich ueber die Oberflaeche angefordert werden.
    """
    lines = log_lines if log_lines is not None else []
    builder = BASE / "build_packages.sh"
    pkg_dir = DATA_DIR / "packages"

    if not builder.is_file():
        lines.append("build_packages.sh nicht gefunden")
        return False, lines

    lines.append("Baue Agent-Pakete...")
    res = run(["bash", str(builder)], timeout=1800)
    out = ((res.stdout or "") + (res.stderr or "")).strip()

    for line in out.splitlines():
        if line.strip():
            lines.append("  " + line.rstrip())

    names = sorted(p.name for p in pkg_dir.glob("co37-agent*")) \
        if pkg_dir.is_dir() else []

    if res.returncode != 0:
        lines.append("Paketbau fehlgeschlagen.")
        return False, lines

    lines.append("Fertig: " + (", ".join(names) or "keine Pakete erzeugt"))

    if not any(n.endswith(".msi") for n in names):
        # Ursache unterscheiden, sonst schickt die Meldung auf die falsche
        # Faehrte, wenn wixl laengst installiert ist.
        if not shutil.which("wixl"):
            lines.append(
                "Kein Windows-Paket: 'wixl' ist nicht installiert. "
                "Auf dem Server nachholen mit: apt install wixl"
            )
        elif "python.org" in out or "Embeddable" in out:
            lines.append(
                "Kein Windows-Paket: die Python-Distribution konnte nicht "
                "geladen werden. Der Server braucht Zugriff auf python.org, "
                "oder die Datei von Hand ablegen unter "
                "packaging/cache/python-3.12.8-embed-amd64.zip"
            )
        else:
            lines.append(
                "Kein Windows-Paket erzeugt. Grund siehe Ausgabe oben."
            )
    return True, lines
    try:
        run(["chown", "-R", "co37:co37", str(pkg_dir)], timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return True, lines


def handle_build_request():
    """Verarbeitet eine ueber die Oberflaeche angeforderte Paketerstellung."""
    log(">>> Paketbau angefordert")
    write_build_status({"state": "running", "log": ["Wird ausgefuehrt..."]})
    try:
        ok, lines = build_packages()
        write_build_status({
            "state": "success" if ok else "error",
            "log": lines,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        write_build_status({
            "state": "error",
            "log": [f"Ausnahme: {exc}"],
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        BUILD_REQUEST.unlink(missing_ok=True)


# ----------------------------------------------------------------------
def do_update():
    status = read_status()
    status["state"] = "running"
    status["started_at"] = datetime.now(timezone.utc).isoformat()
    status.setdefault("log", [])
    write_status(status)

    backup_path = None
    self_changed = False
    try:
        if not INCOMING_ZIP.exists():
            raise RuntimeError("incoming.zip fehlt")

        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        append_log(status, "Sichere aktuelle Version")
        backup_path = backup_current(tag)
        append_log(status, f"Gesichert unter update_backups/{tag}")

        append_log(status, "Entpacke Paket")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        WORK_DIR.mkdir(parents=True)
        with zipfile.ZipFile(INCOMING_ZIP) as zf:
            zf.extractall(WORK_DIR)
        root = find_update_root(WORK_DIR)

        new_version = (root / "backend" / "VERSION").read_text().strip()
        append_log(status, f"Neue Version: {new_version}")

        append_log(status, "Tausche Programmdateien")
        self_changed = swap_in_new_code(root)
        if self_changed:
            append_log(status, "Watcher wurde ebenfalls erneuert")

        append_log(status, "Aktualisiere Abhaengigkeiten")
        res = run([str(VENV_PIP), "install", "-q", "-r",
                   str(BASE / "backend" / "requirements.txt")], timeout=1200)
        if res.returncode != 0:
            raise RuntimeError(f"pip fehlgeschlagen: {res.stderr[-800:]}")

        append_log(status, "Starte Dienst neu")
        res = run(["systemctl", "restart", SERVICE], timeout=120)
        if res.returncode != 0:
            raise RuntimeError(f"Neustart fehlgeschlagen: {res.stderr[-800:]}")

        append_log(status, "Warte auf Health-Check")
        if not wait_for_health():
            raise RuntimeError(
                f"Health-Check nach dem Neustart fehlgeschlagen: "
                f"{LAST_HEALTH_ERROR or 'keine Antwort'}"
            )

        # Agent-Pakete liegen unter data/ und werden beim Update nicht
        # ausgetauscht. Sie enthalten eine Kopie der agent.py und waeren
        # danach veraltet.
        append_log(status, "Baue Agent-Pakete neu")
        ok, lines = build_packages()
        for line in lines[-25:]:
            append_log(status, line)
        if not ok:
            append_log(status, "Systemupdate bleibt trotzdem gueltig. "
                               "Neubau ueber die Oberflaeche moeglich.")

        append_log(status, "Update erfolgreich")
        status["state"] = "success"
        status["new_version"] = new_version
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_status(status)

        if self_changed:
            # Zum Schluss, damit der laufende Vorgang abgeschlossen ist.
            append_log(status, "Starte Watcher neu")
            run(["systemctl", "restart", "co37-watcher"], timeout=30)

    except Exception as exc:  # noqa: BLE001
        append_log(status, f"Fehler: {exc}")
        if backup_path and backup_path.exists():
            append_log(status, "Rolle auf die gesicherte Version zurueck")
            try:
                restore_backup(backup_path)
                run([str(VENV_PIP), "install", "-q", "-r",
                     str(BASE / "backend" / "requirements.txt")], timeout=1200)
                run(["systemctl", "restart", SERVICE], timeout=120)
                if wait_for_health():
                    append_log(status, "Rueckrollung erfolgreich, alte Version laeuft")
                    status["state"] = "rolled_back"
                else:
                    append_log(status, "Rueckrollung ohne Health-Check - Eingriff noetig")
                    status["state"] = "error"
            except Exception as rexc:  # noqa: BLE001
                append_log(status, f"Rueckrollung fehlgeschlagen: {rexc}")
                status["state"] = "error"
        else:
            status["state"] = "error"
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_status(status)

    finally:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        INCOMING_ZIP.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        print("Muss als root laufen", file=sys.stderr)
        sys.exit(1)

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)

    # Fassung hinterlegen, damit das Backend sie melden kann
    try:
        WATCHER_INFO.write_text(json.dumps({
            "version": WATCHER_VERSION,
            "features": WATCHER_FEATURES,
            "started_at": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2))
        try:
            shutil.chown(WATCHER_INFO, user="co37", group="co37")
        except Exception:  # noqa: BLE001
            pass
    except OSError as exc:
        log(f"watcher.json nicht schreibbar: {exc}")

    log(f"Watcher {WATCHER_VERSION} gestartet, beobachtet {STATUS_FILE} "
        f"und {BUILD_REQUEST}")

    while True:
        try:
            if read_status().get("state") == "triggered":
                log("Update angefordert")
                do_update()
            elif BUILD_REQUEST.exists():
                handle_build_request()
        except Exception as exc:  # noqa: BLE001
            log(f"Watcher-Fehler: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
