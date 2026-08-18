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
from datetime import datetime

from utctime import utcnow
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("CO37_DATA", "/opt/co37/data"))
UPDATE_DIR = DATA_DIR / "update"
INCOMING_ZIP = UPDATE_DIR / "incoming.zip"
STATUS_FILE = UPDATE_DIR / "status.json"
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


def get_status() -> dict:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATUS_FILE.exists():
        return _default_status()
    try:
        data = json.loads(STATUS_FILE.read_text())
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


def _validate_root(root: Path) -> Optional[str]:
    missing = [p for p in REQUIRED_PATHS if not (root / p).exists()]
    if missing:
        return "Kein gueltiges CO-37-Update-Paket. Es fehlen: " + ", ".join(missing)
    return None


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


def validate_and_store_update(file_bytes: bytes, filename: str) -> dict:
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

    INCOMING_ZIP.unlink(missing_ok=True)
    shutil.move(str(PROBE_ZIP), str(INCOMING_ZIP))
    shutil.rmtree(PROBE_DIR, ignore_errors=True)

    status = {
        "state": "uploaded",
        "filename": filename,
        "new_version": new_version,
        "current_version": get_current_version(),
        "uploaded_at": utcnow().isoformat(),
    }
    _write_status(status)
    return status


def trigger_update() -> dict:
    status = get_status()
    if status.get("state") != "uploaded":
        raise ValueError("Kein geprueftes Update-Paket vorhanden. Bitte zuerst hochladen.")
    if not INCOMING_ZIP.exists():
        raise ValueError("Update-Datei nicht mehr vorhanden, bitte erneut hochladen.")
    status["state"] = "triggered"
    status["triggered_at"] = utcnow().isoformat()
    status["log"] = ["Update angefordert, warte auf Verarbeitung durch den Host..."]
    _write_status(status)
    return status


def cancel_update() -> dict:
    status = get_status()
    if status.get("state") != "uploaded":
        raise ValueError("Es liegt kein wartendes Update vor - Abbruch nicht moeglich.")
    INCOMING_ZIP.unlink(missing_ok=True)
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
