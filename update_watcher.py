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
WATCHER_VERSION = "0.36.18"
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


def eintrag(schluessel: str, **werte) -> dict:
    """Ein Protokolleintrag als Schluessel. Ohne Zeitstempel."""
    e = {"k": schluessel}
    if werte:
        e["p"] = werte
    return e


def eintrag_text(text: str) -> dict:
    """Ein Protokolleintrag, der unveraendert angezeigt wird."""
    return {"text": text}


def _journalzeile(e: dict) -> str:
    """
    Wie ein Eintrag im Journal steht.

    Der Schluessel samt Werten, nicht der uebersetzte Satz. Fuer jemanden,
    der auf dem Rechner sitzt, ist das eindeutiger: es bleibt ueber
    Fassungen und Sprachen hinweg gleich und laesst sich greppen.
    """
    if "k" not in e:
        return e.get("text", "")
    return e["k"] + "".join(f" {n}={w}" for n, w in (e.get("p") or {}).items())


def append_eintrag(status: dict, e: dict):
    """
    Haengt einen fertigen Eintrag an - mit Zeitstempel, geschrieben,
    protokolliert.

    Die eine Stelle, die Eintraege ins Protokoll bringt. append_log() und
    append_zeile() sind nur die bequemen Formen davon; build_packages()
    liefert seine Eintraege schon fertig und geht direkt hierher.
    """
    status.setdefault("log", []).append(
        {"t": f"{datetime.now():%H:%M:%S}", **e})
    write_status(status)
    log(_journalzeile(e))


def append_log(status: dict, schluessel: str, **werte):
    """
    Haengt einen Protokolleintrag an - als Schluessel, nicht als Satz.

    Uebersetzt wird erst beim Anzeigen. Backend und Agent liefern schon
    immer Schluessel; der Watcher war die letzte Stelle, die deutsche
    Prosa direkt in die Oberflaeche geschrieben hat - sie stand dort auch
    dann, wenn die Oberflaeche auf Englisch lief.
    """
    append_eintrag(status, eintrag(schluessel, **werte))


def append_zeile(status: dict, text: str):
    """
    Haengt eine Zeile an, die NICHT uebersetzt wird.

    Fuer die Ausgabe von build_packages.sh, apt und pip: die entsteht
    ausserhalb und laesst sich nicht in Schluessel fassen. Die
    Oberflaeche gibt solche Eintraege unveraendert aus - und ebenso
    Eintraege, die noch reine Zeichenketten sind, wie sie ein Watcher vor
    0.36.6 geschrieben hat.
    """
    append_eintrag(status, eintrag_text(text))


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def eigentuemer_setzen():
    """
    Stellt die Eigentumsverhaeltnisse nach jedem Eingriff wieder her:
    alles root, nur data/ gehoert co37.

    An einer Stelle, weil das Austauschen und das Zurueckrollen es beide
    brauchen und ein Auseinanderlaufen genau die Luecke oeffnen wuerde,
    die das hier schliesst: der Watcher fuehrt als root Dateien aus
    diesem Verzeichnis aus (sich selbst, build_packages.sh, pip aus dem
    venv). Waeren die fuer co37 schreibbar, waere jede Ausfuehrung als
    co37 gleichbedeutend mit root.
    """
    run(["chown", "-R", "root:root", str(BASE)])
    run(["chown", "-R", "co37:co37", str(DATA_DIR)])


# ----------------------------------------------------------------------
# Signatur des Pakets - noch einmal, hier oben
# ----------------------------------------------------------------------
# Das Backend prueft die Signatur bereits beim Hochladen. Das genuegt
# nicht: das Backend laeuft unprivilegiert, dieser Prozess als root, und
# incoming.zip liegt in data/ - dem einzigen Verzeichnis, in das das
# Backend schreiben darf. Wer Code als co37 ausfuehrt, umgeht die
# Anwendungslogik einfach, legt ein eigenes Paket hin und setzt den
# Status auf 'triggered'.
#
# Die Regel lautet deshalb: dieser Prozess verlaesst sich auf keine
# Pruefung, die jenseits der Rechtegrenze stattgefunden hat, und prueft
# selbst - vor dem Auspacken.
#
# Geprueft wird mit derselben Funktion wie im Backend (backend/
# release_sig.py), nicht mit einer zweiten Kopie der Kryptologik. Das
# Verzeichnis gehoert root, kann von co37 also nicht umgeschrieben
# werden - anders waere der Import selbst die Luecke.
INCOMING_SIG = UPDATE_DIR / "incoming.sig"


class PaketAbgewiesen(RuntimeError):
    """Ein Paket, das nicht ausgepackt werden darf."""


def pruefe_signatur(paket: Path, signatur: Path):
    """
    Wirft PaketAbgewiesen, wenn das Paket nicht vom Herausgeber stammt.

    Ohne ausgelieferten release_key.pub wird nicht geprueft - dieselbe
    Regel wie im Backend, damit ein selbst gebauter Quellstand ohne
    Signatur weiterlaeuft.
    """
    schluessel = BASE / "backend" / "release_key.pub"
    if not schluessel.is_file():
        log("Kein release_key.pub vorhanden - Paket wird ungeprueft "
            "eingespielt (selbst gebauter Stand)")
        return

    sys.path.insert(0, str(BASE / "backend"))
    try:
        import release_sig
    except Exception as exc:  # noqa: BLE001
        # Bewusst abweisen statt durchlassen. Ein Update, das die
        # Pruefung stillschweigend ueberspringt, ist genau der Vorfall,
        # der zu 0.34.0 gefuehrt hat.
        raise PaketAbgewiesen(
            f"Die Signatur kann nicht geprueft werden ({exc}). Der "
            f"Watcher laeuft mit dem System-Python und braucht dafuer "
            f"das Paket python3-cryptography: "
            f"apt install -y python3-cryptography"
        )

    if not signatur.is_file():
        raise PaketAbgewiesen(
            "Zu diesem Paket liegt keine Signatur vor. Es wird nicht "
            "eingespielt.")

    try:
        release_sig.pruefen(paket.read_bytes(),
                            signatur.read_text(encoding="ascii",
                                               errors="replace"))
    except release_sig.SignaturFehler as exc:
        raise PaketAbgewiesen(str(exc))

    log("Signatur des Pakets geprueft")


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
    eigentuemer_setzen()


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

    eigentuemer_setzen()
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


def build_packages(log_lines: list[dict] = None) -> tuple[bool, list[dict]]:
    """
    Baut die Agent-Pakete. Wird nach jedem Update aufgerufen und kann
    zusaetzlich ueber die Oberflaeche angefordert werden.
    """
    lines = log_lines if log_lines is not None else []
    builder = BASE / "build_packages.sh"
    pkg_dir = DATA_DIR / "packages"

    if not builder.is_file():
        lines.append(eintrag("pkg.log.script_missing"))
        return False, lines

    lines.append(eintrag("pkg.log.building"))
    res = run(["bash", str(builder)], timeout=1800)
    out = ((res.stdout or "") + (res.stderr or "")).strip()

    for line in out.splitlines():
        if line.strip():
            lines.append(eintrag_text("  " + line.rstrip()))

    names = sorted(p.name for p in pkg_dir.glob("co37-agent*")) \
        if pkg_dir.is_dir() else []

    if res.returncode != 0:
        lines.append(eintrag("pkg.log.failed"))
        return False, lines

    lines.append(eintrag("pkg.log.done", pakete=", ".join(names))
                 if names else eintrag("pkg.log.done_none"))

    if not any(n.endswith(".msi") for n in names):
        # Ursache unterscheiden, sonst schickt die Meldung auf die falsche
        # Faehrte, wenn wixl laengst installiert ist.
        if not shutil.which("wixl"):
            lines.append(eintrag("pkg.log.no_msi_wixl"))
        elif "python.org" in out or "Embeddable" in out:
            lines.append(eintrag("pkg.log.no_msi_python"))
        else:
            lines.append(eintrag("pkg.log.no_msi_other"))

    # Die neuen Pakete gehoeren root - build_packages.sh laeuft als root.
    # Lesen genuegt dem Backend, aber unter data/ soll durchgehend co37
    # stehen, wie ueberall sonst dort.
    #
    # Stand bis 0.36.5 HINTER einem 'return' und lief damit nie. Solange
    # das ganze Verzeichnis co37 gehoerte, fiel das nicht auf; seit $BASE
    # root gehoert, waere es aufgefallen. Dagegen gibt es jetzt eine
    # Pruefung: tests/watcher-sig-test.py sucht Anweisungen hinter einem
    # 'return' im ganzen Watcher.
    try:
        run(["chown", "-R", "co37:co37", str(pkg_dir)], timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return True, lines


def handle_build_request():
    """Verarbeitet eine ueber die Oberflaeche angeforderte Paketerstellung."""
    log(">>> Paketbau angefordert")
    write_build_status({"state": "running",
                        "log": [eintrag("pkg.log.running")]})
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
            "log": [eintrag("pkg.log.exception", fehler=str(exc))],
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

        # Vor allem anderen, insbesondere vor dem Auspacken und vor der
        # Sicherung: was nicht vom Herausgeber stammt, wird hier nicht
        # angefasst.
        append_log(status, "upd.log.check_sig")
        pruefe_signatur(INCOMING_ZIP, INCOMING_SIG)

        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        append_log(status, "upd.log.backup")
        backup_path = backup_current(tag)
        append_log(status, "upd.log.backup_done", ordner=tag)

        append_log(status, "upd.log.unpack")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        WORK_DIR.mkdir(parents=True)
        with zipfile.ZipFile(INCOMING_ZIP) as zf:
            zf.extractall(WORK_DIR)
        root = find_update_root(WORK_DIR)

        new_version = (root / "backend" / "VERSION").read_text().strip()
        append_log(status, "upd.log.new_version", version=new_version)

        append_log(status, "upd.log.swap")
        self_changed = swap_in_new_code(root)
        if self_changed:
            append_log(status, "upd.log.watcher_renewed")

        append_log(status, "upd.log.deps")
        res = run([str(VENV_PIP), "install", "-q", "-r",
                   str(BASE / "backend" / "requirements.txt")], timeout=1200)
        if res.returncode != 0:
            raise RuntimeError(f"pip fehlgeschlagen: {res.stderr[-800:]}")

        append_log(status, "upd.log.restart")
        res = run(["systemctl", "restart", SERVICE], timeout=120)
        if res.returncode != 0:
            raise RuntimeError(f"Neustart fehlgeschlagen: {res.stderr[-800:]}")

        append_log(status, "upd.log.health")
        if not wait_for_health():
            raise RuntimeError(
                f"Health-Check nach dem Neustart fehlgeschlagen: "
                f"{LAST_HEALTH_ERROR or 'keine Antwort'}"
            )

        # Agent-Pakete liegen unter data/ und werden beim Update nicht
        # ausgetauscht. Sie enthalten eine Kopie der agent.py und waeren
        # danach veraltet.
        append_log(status, "upd.log.build_agents")
        ok, lines = build_packages()
        for e in lines[-25:]:
            append_eintrag(status, e)
        if not ok:
            append_log(status, "upd.log.build_failed_ok")

        append_log(status, "upd.log.success")
        status["state"] = "success"
        status["new_version"] = new_version
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_status(status)

        if self_changed:
            # Zum Schluss, damit der laufende Vorgang abgeschlossen ist.
            append_log(status, "upd.log.restart_watcher")
            run(["systemctl", "restart", "co37-watcher"], timeout=30)

    except Exception as exc:  # noqa: BLE001
        append_log(status, "upd.log.error", fehler=str(exc))
        if backup_path and backup_path.exists():
            append_log(status, "upd.log.rollback")
            try:
                restore_backup(backup_path)
                run([str(VENV_PIP), "install", "-q", "-r",
                     str(BASE / "backend" / "requirements.txt")], timeout=1200)
                run(["systemctl", "restart", SERVICE], timeout=120)
                if wait_for_health():
                    append_log(status, "upd.log.rollback_ok")
                    status["state"] = "rolled_back"
                else:
                    append_log(status, "upd.log.rollback_no_health")
                    status["state"] = "error"
            except Exception as rexc:  # noqa: BLE001
                append_log(status, "upd.log.rollback_failed", fehler=str(rexc))
                status["state"] = "error"
        else:
            status["state"] = "error"
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_status(status)

    finally:
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        INCOMING_ZIP.unlink(missing_ok=True)
        INCOMING_SIG.unlink(missing_ok=True)


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
