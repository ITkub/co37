"""
CO-37 - Update-Manager

Nimmt ZIP-Update-Pakete ueber die Oberflaeche entgegen, prueft sie und legt
sie im Update-Ordner ab.

Aufbau analog TK-37 (update_manager.py): dieses Modul fuehrt selbst KEINEN
Austausch durch. Das Backend laeuft als unprivilegierter Benutzer und darf
weder in sein eigenes Programmverzeichnis schreiben noch sich neu starten.
Die eigentliche Ausfuehrung macht update_watcher.py als root auf dem Host.

Zustaende:
    idle -> uploaded -> triggered -> running -> success | rolled_back | error
"""
import json
import logging
import os
import shutil
import zipfile

import release_sig
from datetime import datetime

from utctime import utcnow
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("CO37_DATA", "/opt/co37/data"))
UPDATE_DIR = DATA_DIR / "update"
INCOMING_ZIP = UPDATE_DIR / "incoming.zip"
# Die Signatur wird neben dem Paket abgelegt, nicht nur als "signed": true
# im Status vermerkt. Der Watcher prueft sie vor dem Auspacken noch einmal
# selbst und braucht sie dafuer. Er darf sich auf die Pruefung hier nicht
# verlassen: sie findet unprivilegiert statt, ausgepackt wird als root.
INCOMING_SIG = UPDATE_DIR / "incoming.sig"

# Zwei Statusdateien, zwei Schreiber - siehe den Kopf von update_watcher.py.
# Hier schreibt das Backend (uploaded, triggered, cancelled), im Ausgang
# schreibt der Watcher (running, success, error). Frueher war es eine Datei
# in einem Verzeichnis, das co37 gehoert; damit war jeder Schreibzugriff des
# als root laufenden Watchers eine Rechteausweitung (F-18, 2026-08-31).
STATUS_FILE = UPDATE_DIR / "status.json"
STATE_DIR = Path(os.getenv("CO37_BASE", "/opt/co37")) / "state"
STATUS_WATCHER = STATE_DIR / "status.json"

PROBE_DIR = UPDATE_DIR / "_probe"
PROBE_ZIP = UPDATE_DIR / "_probe.zip"

# Ohne diese Pfade gilt ein Paket nicht als CO-37-Update
REQUIRED_PATHS = [
    "backend/main.py",
    "backend/models.py",
    "backend/requirements.txt",
    "backend/VERSION",
    "frontend/index.html",
    "agent/agent.py",
]

MAX_ZIP_BYTES = 50 * 1024 * 1024      # 50 MB
MAX_UNPACKED_BYTES = 200 * 1024 * 1024  # Schutz vor ZIP-Bomben

VERSION_FILE = Path(__file__).parent / "VERSION"


def get_current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        return "unbekannt"


def _default_status() -> dict:
    return {"state": "idle", "current_version": get_current_version()}


def _neuere_statusdatei() -> Optional[Path]:
    """
    Welche der beiden Statusdateien den aktuellen Stand hat.

    Die juengere gewinnt, und das bildet den Ablauf richtig ab: nach dem
    Hochladen und beim Ausloesen schreibt das Backend, waehrend und nach
    dem Einspielen der Watcher. Der Watcher raeumt die Eingangsdatei
    ausserdem weg, sobald er den Auftrag angenommen hat - danach gibt es
    ohnehin nur noch eine.
    """
    kandidaten = []
    for pfad in (STATUS_FILE, STATUS_WATCHER):
        try:
            kandidaten.append((pfad.stat().st_mtime_ns, pfad))
        except OSError:
            continue
    if not kandidaten:
        return None
    return max(kandidaten)[1]


def get_status() -> dict:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    pfad = _neuere_statusdatei()
    if pfad is None:
        return _default_status()
    try:
        data = json.loads(pfad.read_text())
    except Exception:  # noqa: BLE001
        return _default_status()
    data["current_version"] = get_current_version()
    return data


def _write_status(data: dict):
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _find_update_root(extract_dir: Path) -> Path:
    """
    Ueberspringt einen eventuellen Wrapper-Ordner, falls das ZIP alles in
    einem einzigen Ordner der obersten Ebene verpackt hat.
    """
    entries = [e for e in extract_dir.iterdir() if e.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        candidate = entries[0]
        if (candidate / "backend").exists() and (candidate / "frontend").exists():
            return candidate
    return extract_dir


# Oeffentliche Schluessel, die ein Update niemals stillschweigend
# veraendern darf. Sie entscheiden, wem diese Installation vertraut.
GESCHUETZTE_SCHLUESSEL = ["backend/release_key.pub", "backend/license_key.pub"]


def _pruefe_schluessel(root: Path) -> Optional[str]:
    """
    Ein Update darf die Vertrauensbasis nicht austauschen.

    Bringt ein Paket einen ANDEREN oeffentlichen Schluessel mit als den
    hier vorhandenen, wird es abgewiesen. Sonst genuegte ein
    untergeschobenes Paket mit eigenem Schluessel, und ab dann waere jedes
    weitere Paket desselben Absenders gueltig - ohne Meldung, ohne Spur.

    Fehlt der Schluessel im Paket, ist das kein Fehler: er bleibt beim
    Einspielen erhalten (siehe update_watcher.py). Genau so ist die
    Pruefung schon einmal stillschweigend ausgefallen - ein Paket ohne
    release_key.pub, und der Watcher hatte die Datei mitgeloescht.
    """
    hier = Path(__file__).resolve().parent.parent
    for rel in GESCHUETZTE_SCHLUESSEL:
        vorhanden = hier / rel
        im_paket = root / rel
        if not vorhanden.is_file() or not im_paket.is_file():
            continue
        alt = vorhanden.read_text(encoding="ascii", errors="replace").strip()
        neu = im_paket.read_text(encoding="ascii", errors="replace").strip()
        if alt and neu and alt != neu:
            name = Path(rel).name
            return (
                f"Das Paket bringt einen anderen {name} mit als den hier "
                f"hinterlegten. Ein Update darf die Vertrauensbasis nicht "
                f"austauschen - das Paket wird nicht eingespielt. Soll der "
                f"Schluessel wirklich gewechselt werden, muss er von Hand "
                f"ersetzt werden."
            )
    return None


def _validate_root(root: Path) -> Optional[str]:
    missing = [p for p in REQUIRED_PATHS if not (root / p).exists()]
    if missing:
        return "Kein gueltiges CO-37-Update-Paket. Es fehlen: " + ", ".join(missing)
    return _pruefe_schluessel(root)


def _safe_extract(zf: zipfile.ZipFile, target: Path):
    """
    Entpackt mit Pfadpruefung. Verhindert Zip-Slip (Eintraege wie ../../etc)
    und begrenzt die entpackte Gesamtgroesse.
    """
    total = 0
    target = target.resolve()
    for info in zf.infolist():
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError("Entpacktes Paket ist zu gross")
        dest = (target / info.filename).resolve()
        if not str(dest).startswith(str(target) + os.sep):
            raise ValueError(f"Unzulaessiger Pfad im Archiv: {info.filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def validate_and_store_update(file_bytes: bytes, filename: str,
                              signature: str = "") -> dict:
    """
    Prueft ein hochgeladenes ZIP. Bei Erfolg wird es als incoming.zip abgelegt
    und der Status auf 'uploaded' gesetzt - die Ausfuehrung muss danach noch
    ausdruecklich ausgeloest werden.
    """
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)

    if len(file_bytes) > MAX_ZIP_BYTES:
        raise ValueError("Datei ist groesser als 50 MB")

    if get_status().get("state") in ("triggered", "running"):
        raise ValueError("Es laeuft bereits ein Update. Bitte abwarten.")

    # Signatur zuerst, vor dem Auspacken. Der Watcher entpackt das Paket
    # spaeter als root - ein untergeschobenes Paket waere damit
    # Codeausfuehrung als root. Was nicht vom Herausgeber stammt, wird
    # gar nicht erst angefasst.
    try:
        release_sig.pruefen(file_bytes, signature)
    except release_sig.SignaturFehler as exc:
        raise ValueError(str(exc))

    shutil.rmtree(PROBE_DIR, ignore_errors=True)
    PROBE_DIR.mkdir(parents=True)
    PROBE_ZIP.write_bytes(file_bytes)

    def cleanup():
        PROBE_ZIP.unlink(missing_ok=True)
        shutil.rmtree(PROBE_DIR, ignore_errors=True)

    try:
        with zipfile.ZipFile(PROBE_ZIP) as zf:
            _safe_extract(zf, PROBE_DIR)
    except zipfile.BadZipFile:
        cleanup()
        raise ValueError("Die Datei ist keine gueltige ZIP-Datei")
    except ValueError:
        cleanup()
        raise

    root = _find_update_root(PROBE_DIR)
    error = _validate_root(root)
    if error:
        cleanup()
        raise ValueError(error)

    new_version = (root / "backend" / "VERSION").read_text().strip()

    # Rueckschritt schon hier abweisen (F-28 der Pruefung vom 2026-08-31).
    #
    # Die eigentliche Schranke sitzt im Watcher - der ist die Instanz, die
    # als root einspielt, und nur was dort geprueft wird, ist geprueft.
    # Diese Pruefung ist die Hoeflichkeit fuer den Menschen davor: sonst
    # laedt jemand ein aelteres Paket hoch, sieht "bereit", loest aus und
    # findet den Grund erst im Protokoll des Watchers.
    aktuell = get_current_version()
    if _aelter(new_version, aktuell):
        cleanup()
        raise ValueError(
            f"Das Paket ist Fassung {new_version}, installiert ist "
            f"{aktuell}. Ein aelteres Paket kann Luecken zurueckbringen, "
            f"die in dieser Fassung geschlossen sind. Ist das gewollt, "
            f"muss die Rueckstufung auf dem Server einmalig freigegeben "
            f"werden: als root 'touch {STATE_DIR / 'allow_downgrade'}'.")

    INCOMING_ZIP.unlink(missing_ok=True)
    shutil.move(str(PROBE_ZIP), str(INCOMING_ZIP))
    shutil.rmtree(PROBE_DIR, ignore_errors=True)

    # Signatur fuer den Watcher hinterlegen. Ohne Signatur die alte
    # ausdruecklich wegraeumen - sonst bliebe die eines frueheren Pakets
    # liegen und der Watcher pruefte das neue gegen die falsche Datei.
    INCOMING_SIG.unlink(missing_ok=True)
    if signature:
        INCOMING_SIG.write_text(signature, encoding="ascii")

    status = {
        "state": "uploaded",
        "filename": filename,
        # Zum Abgleich mit der Angabe neben dem Download. Ersetzt keine
        # Signatur, hilft aber bei der Frage "habe ich die richtige Datei".
        "sha256": release_sig.pruefsumme(file_bytes),
        "signed": bool(signature),
        "new_version": new_version,
        "current_version": get_current_version(),
        "uploaded_at": utcnow().isoformat(),
    }
    _write_status(status)
    return status



def _version_tupel(text: str) -> tuple:
    """Wie im Watcher: '0.37.10' -> (0, 37, 10), nicht als Zeichenkette."""
    teile = []
    for stueck in (text or "").strip().split("."):
        ziffern = ""
        for zeichen in stueck:
            if not zeichen.isdigit():
                break
            ziffern += zeichen
        teile.append(int(ziffern) if ziffern else -1)
    return tuple(teile)


def _aelter(paket: str, installiert: str) -> bool:
    """Ist das Paket aelter als der installierte Stand?"""
    if not paket or not installiert:
        return False
    return _version_tupel(paket) < _version_tupel(installiert)


def ist_neuer(entfernt: str, installiert: str) -> bool:
    """
    Ist die entfernte Fassung neuer als die installierte?

    Eine Stelle fuer diese Frage, damit der GitHub-Check und /api/health
    nicht zweimal dasselbe rechnen - dieselbe Falle wie F-12/F-14. Ein
    leerer oder unlesbarer Wert ist nie neuer.
    """
    if not entfernt or not installiert:
        return False
    return _version_tupel(entfernt) > _version_tupel(installiert)


def trigger_update() -> dict:
    status = get_status()
    if status.get("state") != "uploaded":
        raise ValueError("Kein geprueftes Update-Paket vorhanden. Bitte zuerst hochladen.")
    if not INCOMING_ZIP.exists():
        raise ValueError("Update-Datei nicht mehr vorhanden, bitte erneut hochladen.")
    status["state"] = "triggered"
    status["triggered_at"] = utcnow().isoformat()
    # Schluessel statt Satz - die Oberflaeche uebersetzt beim Anzeigen,
    # gleiche Form wie die Eintraege des Watchers.
    status["log"] = [{"t": datetime.now().strftime("%H:%M:%S"),
                      "k": "upd.log.queued"}]
    _write_status(status)
    return status


def cancel_update() -> dict:
    status = get_status()
    if status.get("state") != "uploaded":
        raise ValueError("Es liegt kein wartendes Update vor - Abbruch nicht moeglich.")
    INCOMING_ZIP.unlink(missing_ok=True)
    INCOMING_SIG.unlink(missing_ok=True)
    status = _default_status()
    _write_status(status)
    return status


def acknowledge() -> dict:
    """Setzt einen abgeschlossenen Vorgang zurueck auf idle."""
    status = get_status()
    if status.get("state") not in ("success", "rolled_back", "error"):
        raise ValueError("Es liegt kein abgeschlossener Vorgang vor.")
    status = _default_status()
    _write_status(status)
    return status
