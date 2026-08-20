"""
CO-37 - Die Vertrauensbasis darf ein Update nicht veraendern.

Zwei Regeln, beide aus einem Vorfall entstanden:

  1. Ein Paket, das release_key.pub nicht enthaelt, darf die Datei nicht
     loeschen. Der Watcher raeumt MANAGED_DIRS vor dem Kopieren ab - ein
     Paket ohne den Schluessel hat ihn dadurch mitgenommen, und danach
     ging jedes weitere Update ohne Signatur durch. Ohne Meldung.

  2. Ein Paket mit einem ANDEREN Schluessel wird abgewiesen. Sonst
     genuegte ein untergeschobenes Paket mit eigenem Schluessel, und ab
     dann waere jedes weitere Paket desselben Absenders gueltig.

Braucht kein Backend und kein Netz.

    python3 tests/keyguard-test.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ==================================================================
# Regel 1: der Watcher bewahrt vorhandene Schluessel
# ==================================================================
import importlib.util  # noqa: E402

TMP = Path(tempfile.mkdtemp())
BASE = TMP / "opt"
(BASE / "backend").mkdir(parents=True)
(BASE / "backend" / "release_key.pub").write_text("ECHTER-SCHLUESSEL\n")
(BASE / "backend" / "license_key.pub").write_text("LIZENZ-SCHLUESSEL\n")
(BASE / "backend" / "main.py").write_text("# alt\n")

# Ein Paket, das den Schluessel NICHT enthaelt
PAKET = TMP / "paket"
(PAKET / "backend").mkdir(parents=True)
(PAKET / "backend" / "main.py").write_text("# neu\n")

spec = importlib.util.spec_from_file_location(
    "watcher", WURZEL / "update_watcher.py")
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)
watcher.BASE = BASE
watcher.MANAGED_FILES = []
watcher.run = lambda *a, **k: None      # kein chown im Test
watcher.log = lambda *a, **k: None

check("Schluessel steht in BEWAHRTE_DATEIEN",
      "backend/release_key.pub" in watcher.BEWAHRTE_DATEIEN,
      watcher.BEWAHRTE_DATEIEN)

watcher.swap_in_new_code(PAKET)

check("Code wurde ausgetauscht",
      (BASE / "backend" / "main.py").read_text().strip() == "# neu")
check("release_key.pub hat das Update ueberlebt",
      (BASE / "backend" / "release_key.pub").is_file())
check("Inhalt unveraendert",
      (BASE / "backend" / "release_key.pub").read_text().strip()
      == "ECHTER-SCHLUESSEL")
check("license_key.pub ebenfalls",
      (BASE / "backend" / "license_key.pub").is_file())

# Bringt das Paket einen Schluessel mit, gilt der aus dem Paket
PAKET2 = TMP / "paket2"
(PAKET2 / "backend").mkdir(parents=True)
(PAKET2 / "backend" / "main.py").write_text("# neuer\n")
(PAKET2 / "backend" / "release_key.pub").write_text("AUS-DEM-PAKET\n")
watcher.swap_in_new_code(PAKET2)
check("Schluessel aus dem Paket hat Vorrang",
      (BASE / "backend" / "release_key.pub").read_text().strip()
      == "AUS-DEM-PAKET")

# Auch das Zurueckrollen darf ihn nicht verlieren
SICHERUNG = TMP / "sicherung"
(SICHERUNG / "backend").mkdir(parents=True)
(SICHERUNG / "backend" / "main.py").write_text("# alt\n")
watcher.restore_backup(SICHERUNG)
check("Zurueckrollen bewahrt den Schluessel",
      (BASE / "backend" / "release_key.pub").is_file())


# ==================================================================
# Regel 2: ein anderer Schluessel wird abgewiesen
# ==================================================================
import update_manager  # noqa: E402

ECHT = (WURZEL / "backend" / "release_key.pub").read_text().strip()

P = TMP / "pruef"
(P / "backend").mkdir(parents=True)
for pfad in update_manager.REQUIRED_PATHS:
    z = P / pfad
    z.parent.mkdir(parents=True, exist_ok=True)
    if not z.exists():
        z.write_text("x")

check("Paket ohne Schluessel wird angenommen",
      update_manager._validate_root(P) is None,
      update_manager._validate_root(P))

(P / "backend" / "release_key.pub").write_text(ECHT + "\n")
check("Paket mit demselben Schluessel wird angenommen",
      update_manager._validate_root(P) is None,
      update_manager._validate_root(P))

(P / "backend" / "release_key.pub").write_text("FREMDER-SCHLUESSEL\n")
fehler = update_manager._validate_root(P)
check("Paket mit fremdem Schluessel wird abgewiesen", fehler is not None)
check("Begruendung nennt den Schluessel",
      fehler and "release_key.pub" in fehler, (fehler or "")[:60])
check("Begruendung sagt, dass nichts eingespielt wird",
      fehler and "nicht eingespielt" in fehler, (fehler or "")[:80])

# Auch der Lizenzschluessel ist geschuetzt
(P / "backend" / "release_key.pub").unlink()
(P / "backend" / "license_key.pub").write_text("FREMDE-LIZENZ\n")
fehler = update_manager._validate_root(P)
check("fremder Lizenzschluessel wird ebenfalls abgewiesen",
      fehler is not None and "license_key.pub" in fehler, (fehler or "")[:60])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
