"""
CO-37 - Auftragsprotokolle

Die laufende Ausgabe eines Auftrags landet in einer Datei je Auftrag, nicht in
der Datenbank. Grund: der Agent schickt im Sekundentakt neue Zeilen, und ein
UPDATE auf eine wachsende Textspalte bei jedem Eintrag schreibt die gesamte
Spalte neu. Bei einem 'apt upgrade' mit einigen Tausend Zeilen wird das teuer.

Gelesen wird inkrementell ueber einen Byte-Offset: die Oberflaeche merkt sich,
wie weit sie ist, und holt nur das Neue nach.
"""
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_DIR = Path(os.getenv("CO37_DATA", "/opt/co37/data")) / "joblogs"

# Obergrenze je Auftrag. Danach wird abgeschnitten - ein Paketmanager kann
# unbegrenzt viel ausgeben, und eine vollgeschriebene Platte legt das ganze
# System lahm.
MAX_BYTES = 2 * 1024 * 1024
TRUNCATED = "\n--- Protokoll gekuerzt, Obergrenze erreicht ---\n"

# Aufbewahrung
MAX_AGE_DAYS = 30


def _path(job_id: int) -> Path:
    return LOG_DIR / f"{job_id}.log"


def append(job_id: int, text: str) -> dict:
    """Haengt Text an. Gibt Groesse und ob gekuerzt wurde zurueck."""
    if not text:
        return {"size": size(job_id), "truncated": False}

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(job_id)
    current = path.stat().st_size if path.exists() else 0

    if current >= MAX_BYTES:
        return {"size": current, "truncated": True}

    data = text if text.endswith("\n") else text + "\n"
    raw = data.encode("utf-8", errors="replace")

    truncated = False
    if current + len(raw) > MAX_BYTES:
        raw = raw[: max(0, MAX_BYTES - current)] + TRUNCATED.encode()
        truncated = True

    with open(path, "ab") as f:
        f.write(raw)

    return {"size": path.stat().st_size, "truncated": truncated}


def read(job_id: int, offset: int = 0, limit: int = 256 * 1024) -> dict:
    """
    Liest ab 'offset'. Gibt den neuen Offset zurueck, damit der Aufrufer beim
    naechsten Mal dort weitermacht.
    """
    path = _path(job_id)
    if not path.exists():
        return {"offset": 0, "text": "", "size": 0, "exists": False}

    total = path.stat().st_size
    if offset < 0:
        offset = 0
    if offset > total:
        # Datei wurde ersetzt oder geleert - von vorn beginnen
        offset = 0

    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(limit)

    return {
        "offset": offset + len(raw),
        "text": raw.decode("utf-8", errors="replace"),
        "size": total,
        "exists": True,
        "more": offset + len(raw) < total,
    }


def size(job_id: int) -> int:
    path = _path(job_id)
    return path.stat().st_size if path.exists() else 0


def delete(job_id: int):
    _path(job_id).unlink(missing_ok=True)


def prune(max_age_days: int = MAX_AGE_DAYS) -> int:
    """Entfernt alte Protokolle. Wird beim Start aufgerufen."""
    if not LOG_DIR.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for f in LOG_DIR.glob("*.log"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def total_size() -> int:
    if not LOG_DIR.is_dir():
        return 0
    return sum(f.stat().st_size for f in LOG_DIR.glob("*.log"))
