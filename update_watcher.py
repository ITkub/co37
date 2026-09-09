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
import re
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

# ----------------------------------------------------------------------
# Zwei Richtungen, zwei Verzeichnisse - und das ist der Kern der Trennung
# ----------------------------------------------------------------------
# EINGANG: data/update/ gehoert co37 (das Backend laeuft unprivilegiert und
# muss das hochgeladene Paket dort ablegen koennen). Alles, was der Watcher
# hier liest, ist damit von einem unprivilegierten Prozess kontrolliert.
#
# AUSGANG: state/ gehoert root und ist fuer co37 nur lesbar. Alles, was der
# Watcher SCHREIBT, gehoert hierher.
#
# Bis 0.37.5 lagen beide Richtungen in data/update/. Das war eine
# Rechteausweitung von co37 nach root, an drei Stellen gleichzeitig
# (Sicherheitspruefung 2026-08-31, F-18): der Watcher schrieb dort
# status.json, build_status.json und watcher.json nach dem Muster
# write_text -> replace -> shutil.chown. Beides folgt Verknuepfungen. Wer
# als co37 vorher eine Verknuepfung unter dem erwarteten Namen ablegte,
# liess root durch sie hindurchschreiben und bekam anschliessend das Ziel
# per chown uebereignet - also eine beliebige root-Datei zum
# Weiterbeschreiben.
#
# Der Ausweg ist nicht, jeden einzelnen Aufruf gegen Verknuepfungen zu
# haerten: O_NOFOLLOW schuetzt nur die letzte Pfadkomponente, und das
# ELTERNverzeichnis gehoert co37 - es liesse sich als Ganzes umhaengen.
# Deshalb ein Verzeichnis, in dem co37 gar nichts anlegen kann.
UPDATE_DIR = DATA_DIR / "update"        # Eingang, co37 schreibt
STATE_DIR = BASE / "state"              # Ausgang, nur root schreibt
# Einmalige Freigabe fuer ein gewolltes Zurueckrollen (F-28). Liegt in
# state/ und nicht im Eingang: co37 darf sie nicht anlegen koennen,
# sonst waere die Schranke keine.
DOWNGRADE_OK = STATE_DIR / "allow_downgrade"

INCOMING_ZIP = UPDATE_DIR / "incoming.zip"

# Die geschuetzte Kopie, mit der wirklich gearbeitet wird. Siehe
# ins_sichere_holen() - das Paket wird EINMAL hierher kopiert, und danach
# werden Signaturpruefung und Auspacken auf diese Kopie angewandt.
SAFE_DIR = STATE_DIR / "work"
SAFE_ZIP = SAFE_DIR / "incoming.zip"
SAFE_SIG = SAFE_DIR / "incoming.sig"
WORK_DIR = SAFE_DIR / "_work"

# Der Status hat zwei Schreiber, und deshalb ab 0.37.6 zwei Dateien:
#
#   STATUS_EINGANG  schreibt das BACKEND (uploaded, triggered, cancelled)
#                   und der Watcher liest sie, um den Auftrag zu erkennen.
#   STATUS_FILE     schreibt der WATCHER (running, success, error) und das
#                   Backend liest sie.
#
# Vorher war es eine Datei, die beide beschrieben - und weil sie in einem
# Verzeichnis lag, das co37 gehoert, war jede Schreiboperation des Watchers
# ein Hebel nach root (F-18). Wer welche Datei besitzt, ist jetzt an ihrem
# Ort abzulesen. Welche der beiden fuer die Anzeige gilt, entscheidet der
# Zeitstempel; das Backend macht das in update_manager.get_status().
STATUS_EINGANG = UPDATE_DIR / "status.json"
STATUS_FILE = STATE_DIR / "status.json"

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
WATCHER_VERSION = "0.38.0"
WATCHER_INFO = STATE_DIR / "watcher.json"
WATCHER_INFO_ALT = UPDATE_DIR / "watcher.json"
WATCHER_FEATURES = ["managed_files", "self_update", "package_rebuild", "build_request"]


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def state_dir_bereit() -> Path:
    """
    Legt das Ausgangsverzeichnis an - root, fuer alle lesbar.

    0755 statt 0700: das Backend laeuft als co37 und muss die Statusdateien
    lesen. Schreiben kann es dort nichts, und genau darauf kommt es an.
    Angelegt wird es hier und nicht in setup.sh, weil ein Update setup.sh
    nicht ausfuehrt - sonst haette eine im Betrieb aktualisierte Anlage das
    Verzeichnis nie.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(STATE_DIR, 0, 0)
        os.chmod(STATE_DIR, 0o755)
    except OSError:
        pass
    return STATE_DIR


def _schreibe_json(ziel: Path, data: dict):
    """
    Schreibt eine Statusdatei nach state/ - fuer co37 lesbar, nicht
    beschreibbar.

    Kein chown mehr auf den Benutzer co37. Das war der Hebel aus F-18:
    os.chown folgt Verknuepfungen und uebereignete damit das Ziel. Hier
    bleibt die Datei root und wird ueber das Leserecht zugaenglich gemacht.
    """
    state_dir_bereit()
    tmp = ziel.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o644)
    tmp.replace(ziel)


def read_status() -> dict:
    """
    Der Stand, wie ihn das Backend hinterlassen hat.

    Bewusst der EINGANG: dort steht der Auftrag ('triggered') und der
    Protokollanfang, den das Backend gesetzt hat. Der Watcher schreibt
    seinen eigenen Verlauf danach in den Ausgang.

    Die Datei gehoert co37 und ist damit nicht vertrauenswuerdig. Gelesen
    wird sie nur mit json.loads, und der einzige Wert, auf den hin
    gehandelt wird, ist die Zeichenkette 'triggered' - eine untergeschobene
    Verknuepfung wuerde hier hoechstens einen Auftrag ausloesen, den ein
    Administrator ohnehin ausloesen darf.
    """
    for pfad in (STATUS_EINGANG, STATUS_FILE):
        try:
            return json.loads(pfad.read_text())
        except Exception:  # noqa: BLE001
            continue
    return {"state": "idle"}


def write_status(data: dict):
    _schreibe_json(STATUS_FILE, data)


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


def ins_sichere_holen(quelle: Path, ziel: Path):
    """
    Kopiert eine Datei aus dem Eingang in das nur fuer root beschreibbare
    Arbeitsverzeichnis.

    Der Grund ist der Befund F-18/F-19 vom 2026-08-31: bis 0.37.5 wurde die
    Signatur auf incoming.zip geprueft (die Funktion liest die Datei und
    schliesst sie wieder) und dieselbe Datei danach ERNEUT von der Platte
    geoeffnet, um sie auszupacken. Dazwischen lag das Anlegen der
    Sicherung, also Sekunden bis Minuten. incoming.zip liegt aber im
    Eingang und gehoert co37.

    Das Rennen war nicht einmal knapp: co37 kann am Protokolleintrag
    upd.log.backup ablesen, dass die Pruefung durch ist, und erst dann
    tauschen. Geprueft wurde dann das eine Paket, eingespielt das andere.

    Deshalb: einmal hierher kopieren, und ab da ausschliesslich mit dieser
    Kopie arbeiten - pruefen UND auspacken. Die Kopie liegt in einem
    Verzeichnis, in dem co37 nichts anlegen kann.

    O_NOFOLLOW: die Quelle darf keine Verknuepfung sein. Root wuerde ihr
    sonst folgen und eine beliebige Datei des Systems hereinholen.
    """
    try:
        fd = os.open(quelle, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PaketAbgewiesen(
            f"{quelle.name} laesst sich nicht lesen ({exc}). Eine "
            f"symbolische Verknuepfung wird hier bewusst nicht verfolgt."
        )
    try:
        with os.fdopen(fd, "rb") as src, open(ziel, "wb") as dst:
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        raise PaketAbgewiesen(f"{quelle.name} nicht kopierbar: {exc}")


def sicher_auspacken(zf: zipfile.ZipFile, ziel: Path):
    """
    Packt aus und laesst nichts aus dem Zielverzeichnis heraus.

    Dieselbe Pruefung, die update_manager._safe_extract im Backend schon
    macht. Sie fehlte hier - ausgerechnet auf der Seite, die als root
    auspackt (F-17). Ein Paket ist zwar signiert, aber eine Pruefung, die
    auf der unprivilegierten Seite steht und auf der privilegierten fehlt,
    ist genau falsch herum aufgehaengt.
    """
    ziel = ziel.resolve()
    for info in zf.infolist():
        dest = (ziel / info.filename).resolve()
        if not str(dest).startswith(str(ziel) + os.sep):
            raise PaketAbgewiesen(
                f"Das Paket enthaelt einen Pfad ausserhalb des "
                f"Arbeitsverzeichnisses: {info.filename}"
            )
    zf.extractall(ziel)


# Diese Dateien entscheiden, wem die Installation vertraut.
GESCHUETZTE_SCHLUESSEL = ["backend/release_key.pub", "backend/license_key.pub"]


def pruefe_schluessel(root: Path):
    """
    Ein Update darf die Vertrauensbasis nicht austauschen.

    Dieselbe Regel wie in update_manager._pruefe_schluessel() - dort steht
    sie seit jeher, hier fehlte sie (F-27). Das war die falsche Verteilung:
    das Backend prueft unprivilegiert und laesst sich umgehen, der Watcher
    spielt tatsaechlich ein. swap_in_new_code() uebernahm einen im Paket
    mitgebrachten Schluessel kommentarlos ("Paket bringt die Datei mit -
    dann gilt sie").

    Wirkung ohne diese Pruefung: ein einziges untergeschobenes Paket macht
    den Absender dauerhaft zum legitimen Herausgeber - fuer alle weiteren
    Pakete UND fuer den Agent-Quelltext, der auf jedem verwalteten Host als
    SYSTEM laeuft.

    Fehlt der Schluessel im Paket, ist das kein Fehler: BEWAHRTE_DATEIEN
    traegt ihn dann aus dem bisherigen Stand nach.
    """
    for rel in GESCHUETZTE_SCHLUESSEL:
        vorhanden = BASE / rel
        im_paket = root / rel
        if not vorhanden.is_file() or not im_paket.is_file():
            continue
        alt = vorhanden.read_text(encoding="ascii", errors="replace").strip()
        neu = im_paket.read_text(encoding="ascii", errors="replace").strip()
        if alt and neu and alt != neu:
            raise PaketAbgewiesen(
                f"Das Paket bringt einen anderen {Path(rel).name} mit als "
                f"den hier hinterlegten. Ein Update darf die Vertrauensbasis "
                f"nicht austauschen. Soll der Schluessel wirklich gewechselt "
                f"werden, muss er von Hand ersetzt werden."
            )


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


# Die Anforderung kommt vom Backend, liegt also im Eingang. Der Stand geht
# zurueck und liegt damit im Ausgang.
BUILD_REQUEST = UPDATE_DIR / "build_request.json"
BUILD_STATUS = STATE_DIR / "build_status.json"
BUILD_STATUS_ALT = UPDATE_DIR / "build_status.json"


def write_build_status(data: dict):
    _schreibe_json(BUILD_STATUS, data)
    try:
        BUILD_STATUS_ALT.unlink()
    except OSError:
        pass


def pip_zeile_auswerten(ausgabe: str) -> list[dict]:
    """
    Macht aus der Ausgabe eines pip-Trockenlaufs Protokolleintraege.

    pip schreibt bei --dry-run genau eine Zeile, die zaehlt:

        Would install anyio-4.15.0 SQLAlchemy-2.0.52

    Steht sie nicht da, aendert sich nichts. Getrennt von pip_vorschau(),
    damit die Auswertung ohne pip pruefbar ist - eine Zeichenkettensuche
    im Quelltext bewiese nur, dass jemand etwas hingeschrieben hat.
    """
    for zeile in ausgabe.splitlines():
        zeile = zeile.strip()
        if zeile.startswith("Would install "):
            pakete = zeile[len("Would install "):].strip()
            if pakete:
                return [eintrag("upd.log.deps_plan", pakete=pakete)]
    return [eintrag("upd.log.deps_none")]


def pip_vorschau() -> list[dict]:
    """
    Sagt VOR dem Einspielen, welche Pakete sich tatsaechlich aendern.

    Bis 0.37.31 lief 'pip install -r' bei jedem Update blind. Alle 29
    Abhaengigkeiten sind gepinnt (F-59), also aendert sich normalerweise
    nichts - aber ob das stimmt, war nur von Hand vor dem Ausrollen zu
    sehen (0.37.17, 0.37.19) und danach nirgends festgehalten. Aendert
    eine neue Fassung Pakete mit, steht es jetzt im Update-Protokoll.

    Der Schritt ist eine AUSKUNFT, keine Schranke: was hier schiefgeht,
    darf das Update nicht aufhalten. Das ist die Lehre aus F-48 - eine
    Haertung, die den Betriebspfad bricht, richtet mehr Schaden an als
    das, was sie abwehrt. Deshalb faengt der Aufruf alles ab und meldet
    den Grund, statt zu werfen.
    """
    try:
        res = run([str(VENV_PIP), "install", "--dry-run", "-r",
                   str(BASE / "backend" / "requirements.txt")], timeout=600)
    except Exception as exc:  # noqa: BLE001
        return [eintrag("upd.log.deps_preview_failed", fehler=str(exc)[:200])]
    if res.returncode != 0:
        # Aeltere pip-Fassungen kennen --dry-run nicht. Kein Fehler,
        # nur keine Auskunft.
        grund = (res.stderr or res.stdout or "")[-200:]
        return [eintrag("upd.log.deps_preview_failed", fehler=grund)]
    return pip_zeile_auswerten(res.stdout or "")


def build_packages(log_lines: list[dict] = None) -> tuple[bool, list[dict]]:
    """
    Baut die Agent-Pakete. Wird nach jedem Update aufgerufen und kann
    zusaetzlich ueber die Oberflaeche angefordert werden.
    """
    lines = log_lines if log_lines is not None else []
    builder = BASE / "build_packages.sh"
    # Ausgang, nicht Eingang (F-29 der Pruefung vom 2026-08-31). Bis
    # 0.37.9 lag das Verzeichnis unter data/ und gehoerte co37, waehrend
    # dieses Skript als root laeuft - build_deb.py schreibt mit
    # open(ziel, "wb") und folgt dabei einer Verknuepfung. Eine einzige
    # Datei im Eingang (build_request.json) genuegte, um root dazu zu
    # bringen, an eine frei gewaehlte Stelle zu schreiben.
    pkg_dir = STATE_DIR / "packages"

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
            # Den erwarteten Dateinamen aus der eigenen Meldung von
            # build_msi.py lesen statt ihn hier zu wiederholen - sonst
            # veraltet er beim naechsten Anheben von PY_VERSION lautlos,
            # so wie es der fest eingetragenen Fassung in i18n.js
            # passiert ist (2026-08-29 gefunden: nannte noch 3.12.8,
            # obwohl laengst auf eine neuere Zahl gestellt).
            treffer = re.search(r"python-[\d.]+-embed-amd64\.zip", out)
            datei = treffer.group(0) if treffer else "python-<version>-embed-amd64.zip"
            lines.append(eintrag("pkg.log.no_msi_python", datei=datei))
        else:
            lines.append(eintrag("pkg.log.no_msi_other"))

    # KEIN chown auf co37 mehr (F-29).
    #
    # Bis 0.37.9 stand hier ein 'chown -R co37:co37' auf das
    # Paketverzeichnis - richtig, solange es unter data/ lag, wo
    # durchgehend co37 steht. Seit die Pakete nach state/ gebaut werden,
    # waere es das Gegenteil: es gaebe co37 genau das Schreibrecht
    # zurueck, dessen Fehlen die Absicherung ist. Das Backend braucht nur
    # Lesen, und state/ ist 0755.
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

def _version_tupel(text: str) -> tuple:
    """
    '0.37.10' -> (0, 37, 10). Nicht als Zeichenkette vergleichen: dort
    stuende '0.37.9' hinter '0.37.10'.

    Von jedem Stueck zaehlen die fuehrenden Ziffern; ein Zusatz wird
    abgeschnitten, '0.38.0-rc1' gilt also als GLEICH mit '0.38.0' und
    damit als einspielbar. Das ist Absicht - dieselbe Fassung noch einmal
    einzuspielen muss gehen, sonst liesse sich ein misslungenes Update
    nicht wiederholen. Ein Stueck ganz ohne Ziffern wird -1 und sortiert
    nach unten.

    Faelschen laesst sich die Zahl ohnehin nicht: sie steht in
    backend/VERSION im Paket, und darueber laeuft die Signatur.
    """
    teile = []
    for stueck in (text or "").strip().split("."):
        ziffern = ""
        for zeichen in stueck:
            if not zeichen.isdigit():
                break
            ziffern += zeichen
        teile.append(int(ziffern) if ziffern else -1)
    return tuple(teile)


def pruefe_kein_rueckschritt(neue_version: str):
    """
    Ein aelteres Paket wird nicht eingespielt (F-28 der Pruefung vom
    2026-08-31).

    Die Signatur beweist die HERKUNFT einer Datei, nicht dass sie noch
    aktuell ist. Jede je ausgestellte Signatur gilt unbefristet - und
    Pakete werden verteilt und archiviert (tools/archiv-pakete.py gibt
    es genau dafuer). Wer Code als co37 ausfuehrt, legte also einfach ein
    echtes, gueltig signiertes altes Paket in den Eingang und setzte den
    Status auf 'triggered'. Der Watcher prueft, findet alles in Ordnung,
    und spielt als root eine Fassung ein, in der die Befunde von damals
    noch offen sind - F-19 zum Beispiel, und damit beliebiger Code als
    root. Ueber MANAGED_DIRS und agent_autoroll geht die Rueckstufung
    danach auf die ganze Flotte.

    Der Rueckfallschutz faengt das nicht ab: migrate.verify() meldet nur
    FEHLENDE Spalten, und ein aelteres Backend findet gegen ein neueres
    Schema alle seine eigenen.

    Die Versionsnummer selbst ist nicht faelschbar - sie steht in
    backend/VERSION im Paket, und ueber dessen Pruefsumme laeuft die
    Signatur. Es fehlte allein der Vergleich.

    Gewollt zurueckrollen geht weiter, braucht aber eine Hand am Server:
    eine Datei state/allow_downgrade, und die kann nur root anlegen.
    Sie gilt genau einmal.
    """
    aktuell = ""
    try:
        aktuell = (BASE / "backend" / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        # Kein Vergleichswert - dann nicht blockieren. Eine Erstinstallation
        # oder ein kaputter Stand soll sich einspielen lassen.
        return

    if not aktuell or _version_tupel(neue_version) >= _version_tupel(aktuell):
        return

    if DOWNGRADE_OK.exists():
        # Verbraucht, damit die Freigabe nicht dauerhaft stehen bleibt.
        try:
            DOWNGRADE_OK.unlink()
        except OSError:
            pass
        log(f"Rueckschritt {aktuell} -> {neue_version} ausdruecklich freigegeben")
        return

    raise RuntimeError(
        f"Rueckschritt abgelehnt: das Paket ist {neue_version}, installiert "
        f"ist {aktuell}. Ein aelteres Paket kann Luecken zurueckbringen, die "
        f"in dieser Fassung geschlossen sind. Gewollt? Dann als root "
        f"'touch {DOWNGRADE_OK}' und erneut ausloesen."
    )

def raeume_arbeitsverzeichnis():
    """
    Raeumt das Arbeitsverzeichnis und den Eingang.

    ZWEIMAL AUFGERUFEN, und das ist Absicht: einmal im Normalfall vor dem
    Neustart des Watchers, einmal im finally fuer die Abbruchwege. Der
    Aufruf ist wiederholbar - rmtree mit ignore_errors und unlink mit
    missing_ok.

    WARUM NICHT NUR IM finally (F-60 der Pruefung vom 2026-09-04):

    Genau dort stand es, und es hat kein einziges Mal gelaufen. Am Ende
    des try-Zweigs steht

        run(["systemctl", "restart", "co37-watcher"], timeout=30)

    und systemctl beendet dabei den laufenden Prozess. Was danach kommt -
    auch ein finally - findet nicht mehr statt. Der Zweig greift also nur,
    wenn der Watcher sich NICHT selbst ausgetauscht hat.

    Und das ist praktisch nie: build_release.py schreibt bei jedem Bau
    WATCHER_VERSION neu in diese Datei, damit sichtbar ist, welcher
    Watcher laeuft. Die Datei unterscheidet sich damit in jedem Release
    von der installierten, self_changed ist jedes Mal wahr, der Neustart
    kommt jedes Mal.

    Aufgefallen ist es erst im Feld: nach dem Update auf 0.37.17 lag
    state/work weiterhin voll da, obwohl 0.37.15 die Zeile schon
    enthielt. Die Pruefung dazu hatte den QUELLTEXT angesehen und
    bestaetigt, dass dort SAFE_DIR steht - nicht, ob die Stelle erreicht
    wird. Dieselbe Familie wie die Besitzpruefung aus F-46, die im
    Quelltext richtig aussah und nie lief.
    """
    shutil.rmtree(SAFE_DIR, ignore_errors=True)
    INCOMING_ZIP.unlink(missing_ok=True)
    INCOMING_SIG.unlink(missing_ok=True)


def do_update():
    status = read_status()
    status["state"] = "running"
    status["started_at"] = datetime.now(timezone.utc).isoformat()
    status.setdefault("log", [])
    write_status(status)

    # Auftrag verbraucht. Ohne das stuende im Eingang weiter 'triggered'
    # und die naechste Runde der Hauptschleife finge dasselbe Update noch
    # einmal an. unlink entfernt eine Verknuepfung, nicht ihr Ziel - in
    # einem Verzeichnis, das co37 gehoert, ist das der einzige Zugriff,
    # den root sich hier erlauben darf.
    try:
        STATUS_EINGANG.unlink()
    except OSError:
        pass

    backup_path = None
    self_changed = False
    try:
        if not INCOMING_ZIP.exists():
            raise RuntimeError("incoming.zip fehlt")

        # ZUERST in Sicherheit bringen, dann pruefen, dann diese Kopie
        # auspacken. Die Reihenfolge ist der ganze Punkt: was geprueft
        # wurde, muss dasselbe sein wie das, was eingespielt wird. Solange
        # beides aus dem Eingang gelesen wurde, war es das nicht (F-19).
        state_dir_bereit()
        shutil.rmtree(SAFE_DIR, ignore_errors=True)
        SAFE_DIR.mkdir(parents=True)
        os.chmod(SAFE_DIR, 0o700)
        ins_sichere_holen(INCOMING_ZIP, SAFE_ZIP)
        if INCOMING_SIG.exists():
            ins_sichere_holen(INCOMING_SIG, SAFE_SIG)

        # Was nicht vom Herausgeber stammt, wird hier nicht angefasst.
        append_log(status, "upd.log.check_sig")
        pruefe_signatur(SAFE_ZIP, SAFE_SIG)

        tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        append_log(status, "upd.log.backup")
        backup_path = backup_current(tag)
        append_log(status, "upd.log.backup_done", ordner=tag)

        append_log(status, "upd.log.unpack")
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        WORK_DIR.mkdir(parents=True)
        with zipfile.ZipFile(SAFE_ZIP) as zf:
            sicher_auspacken(zf, WORK_DIR)
        root = find_update_root(WORK_DIR)

        new_version = (root / "backend" / "VERSION").read_text().strip()
        append_log(status, "upd.log.new_version", version=new_version)

        # Nach dem Auspacken, vor dem Einspielen: der Vertrauensanker
        # bleibt, wie er ist.
        pruefe_schluessel(root)

        # Und kein Rueckschritt.
        pruefe_kein_rueckschritt(new_version)

        append_log(status, "upd.log.swap")
        self_changed = swap_in_new_code(root)
        if self_changed:
            append_log(status, "upd.log.watcher_renewed")

        append_log(status, "upd.log.deps")
        for eintrag_ in pip_vorschau():
            append_eintrag(status, eintrag_)
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

        # Auch hierhin, nicht nur ins Update-Protokoll. Der Agents-Reiter
        # zeigt ausschliesslich build_status.json an - schreibt dieser Weg
        # es nicht, steht dort nach einem Update weiter der Stand des
        # letzten Bauens ueber den Knopf, samt dessen gruenem "fertig",
        # neben frisch gebauten Paketen einer neueren Fassung. Am
        # 2026-08-31 genau so aufgefallen: Dateien 0.37.4, Protokoll
        # 0.37.3.
        #
        # Bewusst nur der Endzustand, kein "running" vorweg: bricht das
        # Update zwischendurch ab, bliebe ein "running" stehen und der
        # Knopf waere dauerhaft gesperrt. Lieber kurz der alte Stand als
        # eine Sperre, die sich nicht von selbst loest.
        write_build_status({
            "state": "success" if ok else "error",
            "log": lines,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })

        if not ok:
            append_log(status, "upd.log.build_failed_ok")

        append_log(status, "upd.log.success")
        status["state"] = "success"
        status["new_version"] = new_version
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_status(status)

        # VOR dem Neustart aufraeumen. Was danach steht, laeuft nicht mehr
        # (F-60, siehe raeume_arbeitsverzeichnis).
        raeume_arbeitsverzeichnis()

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
        # Fuer die Abbruchwege. Der Normalfall raeumt schon weiter oben auf,
        # weil dieser Zweig ihn nicht mehr erreicht - siehe F-60.
        raeume_arbeitsverzeichnis()


def main():
    if os.geteuid() != 0:
        print("Muss als root laufen", file=sys.stderr)
        sys.exit(1)

    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    state_dir_bereit()

    # Fassung hinterlegen, damit das Backend sie melden kann
    try:
        _schreibe_json(WATCHER_INFO, {
            "version": WATCHER_VERSION,
            "features": WATCHER_FEATURES,
            "started_at": datetime.now().isoformat(),
        })
        try:
            WATCHER_INFO_ALT.unlink()
        except OSError:
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
