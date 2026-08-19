#!/usr/bin/env python3
"""
CO-37 - Update-Paket bauen.

    python build_release.py            Version aus backend/VERSION
    python build_release.py 0.33.0     setzt die Version vorher
    python build_release.py --no-sign  ohne Signatur

Ersetzt build_release.sh. Grund: das Shell-Skript braucht 'zip' und
'unzip', und Git Bash unter Windows bringt beide nicht mit. Python kann
ZIP-Dateien von Haus aus - damit laeuft das Bauen auf jedem Rechner
gleich.
"""

import argparse
import hashlib
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

HIER = Path(__file__).resolve().parent

# Verzeichnisse und Dateien, die ins Paket gehoeren.
VERZEICHNISSE = ["backend", "frontend", "agent", "packaging", "tests", "tools"]
DATEIEN = [
    "update_watcher.py", "setup.sh", "build_packages.sh",
    "build_release.sh", "build_release.py", "migrate_to_co37.sh",
    "README.md", "REVERSE-PROXY.md", "GITHUB.md", "LICENSE",
    "run-tests.sh", ".gitignore", ".gitattributes",
]

AUSSCHLUSS = ("__pycache__", ".pyc", ".db", ".db-shm", ".db-wal", ".DS_Store")
AUSSCHLUSS_ORDNER = ("__pycache__", "packaging/cache", "packaging/_msi_build")

# Diese Dateien brauchen im Paket das Ausfuehrungsrecht. Pythons
# ZIP-Werkzeug uebertraegt Dateirechte nicht von selbst - ohne das kaeme
# setup.sh beim Kunden ohne Ausfuehrungsrecht an und liesse sich nicht
# starten.
AUSFUEHRBAR = (".sh",)


def fehler(text: str):
    print(f"!!! {text}")
    sys.exit(1)


# ======================================================================
# Versionsnummern
# ======================================================================
def setze_versionen(version: str):
    """
    Traegt die Version an allen Stellen ein.

    Der Agent traegt dieselbe Nummer wie das System. Zwei getrennte
    Versionen haben nur verwirrt - und beim Wechsel des Schemas ging die
    Paketversion rueckwaerts, worauf apt die Installation als Downgrade
    verweigert hat.
    """
    ersetzungen = [
        ("agent/agent.py", r'^AGENT_VERSION = .*',
         f'AGENT_VERSION = "{version}"'),
        ("update_watcher.py", r'^WATCHER_VERSION = .*',
         f'WATCHER_VERSION = "{version}"'),
        # Kennung der Oberflaeche. Die Selbstdiagnose im Backend sucht
        # genau diese Zeile; ohne sie meldet sie dauerhaft eine
        # Oberflaeche ohne Kennung.
        ("frontend/index.html", r'CO37_FRONTEND_VERSION: .*',
         f'CO37_FRONTEND_VERSION: {version} -->'),
    ]
    for name, muster, neu in ersetzungen:
        p = HIER / name
        text = p.read_text(encoding="utf-8")
        text, n = re.subn(muster, neu, text, count=1, flags=re.M)
        if n != 1:
            fehler(f"In {name} liess sich die Version nicht setzen "
                   f"(Muster nicht gefunden).")
        # newline="" verhindert, dass Windows die Zeilenenden umschreibt.
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    print(f"Agent-, Watcher- und Frontend-Version auf {version} gesetzt")


def pruefe_versionen(version: str):
    """
    Gegenpruefung. Eine vergessene Nummer faellt sonst erst im Betrieb
    auf, und zwar als scheinbar nicht erneuerter Watcher.
    """
    for name, var in (("agent/agent.py", "AGENT_VERSION"),
                      ("update_watcher.py", "WATCHER_VERSION")):
        text = (HIER / name).read_text(encoding="utf-8")
        m = re.search(rf'^{var} = "([^"]*)"', text, re.M)
        if not m or m.group(1) != version:
            fehler(f"{var} in {name} steht auf "
                   f"'{m.group(1) if m else '?'}', erwartet '{version}'.")

    text = (HIER / "frontend/index.html").read_text(encoding="utf-8")
    m = re.search(r"CO37_FRONTEND_VERSION: ([0-9.]+)", text)
    if not m or m.group(1) != version:
        fehler(f"CO37_FRONTEND_VERSION steht auf "
               f"'{m.group(1) if m else '?'}', erwartet '{version}'.")


# ======================================================================
# Sicherungen vor dem Packen
# ======================================================================
def pruefe_keine_geheimnisse():
    """
    Kein privater Schluessel im Projektverzeichnis.

    Er gehoert nach ~/.co37 und niemals hierher. Laege er trotzdem hier -
    versehentlich hineinkopiert, aus einer Sicherung zurueckgeholt - waere
    er sonst in jedem ausgelieferten Paket.
    """
    treffer = []
    for muster in ("*.pem", "*.key", "license-private*", "release-private*"):
        for p in HIER.rglob(muster):
            if "__pycache__" in p.parts or p.name == "secret.key":
                continue
            treffer.append(p.relative_to(HIER))
    if treffer:
        print("!!! Privater Schluessel im Projektverzeichnis gefunden:")
        for t in sorted(set(str(t) for t in treffer)):
            print(f"    {t}")
        print("    Er darf nicht ins Paket. Nach ~/.co37 verschieben und "
              "erneut bauen.")
        sys.exit(1)


def managed_files() -> list:
    """Was der Watcher austauschen will - steht in update_watcher.py."""
    text = (HIER / "update_watcher.py").read_text(encoding="utf-8")
    m = re.search(r"MANAGED_FILES = \[(.*?)\]", text, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


# ======================================================================
# Packen
# ======================================================================
def sammle() -> list:
    """Alle Dateien, die ins Paket gehoeren, als (Pfad, Name im Paket)."""
    raus = []

    def passt(p: Path) -> bool:
        rel = p.relative_to(HIER).as_posix()
        if any(rel.startswith(o + "/") or f"/{o}/" in f"/{rel}"
               for o in AUSSCHLUSS_ORDNER):
            return False
        return not any(p.name.endswith(e) or e == p.name for e in AUSSCHLUSS)

    for name in VERZEICHNISSE:
        d = HIER / name
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and passt(p):
                raus.append((p, p.relative_to(HIER).as_posix()))

    for name in DATEIEN:
        p = HIER / name
        if p.is_file():
            raus.append((p, name))

    return raus


def packe(ziel: Path, dateien: list):
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as zf:
        for pfad, name in dateien:
            info = zipfile.ZipInfo.from_file(pfad, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Rechte ausdruecklich setzen. Ohne das kaemen Shell-Skripte
            # ohne Ausfuehrungsrecht beim Kunden an - unter Windows gibt
            # es dieses Recht gar nicht, es kann also nicht uebernommen
            # werden.
            rechte = 0o755 if name.endswith(AUSFUEHRBAR) else 0o644
            info.external_attr = (stat.S_IFREG | rechte) << 16
            zf.writestr(info, pfad.read_bytes())


def pruefe_paket(ziel: Path):
    """
    Alles, was der Watcher austauschen will, muss auch im Paket liegen.

    Fehlt eine Datei, ueberspringt er sie stillschweigend - und die
    Korrektur daran erreicht den Betrieb nie.
    """
    with zipfile.ZipFile(ziel) as zf:
        drin = set(zf.namelist())
    fehlend = [f for f in managed_files() if f not in drin]
    if fehlend:
        print("!!! Diese Dateien stehen in MANAGED_FILES, fehlen aber im Paket:")
        print("   " + " ".join(fehlend))
        print("    Der Watcher wuerde sie stillschweigend ueberspringen.")
        ziel.unlink(missing_ok=True)
        sys.exit(1)


def signiere(ziel: Path) -> bool:
    """
    Signiert das Paket, wenn ein privater Signaturschluessel vorliegt.

    Ohne Schluessel kein Fehler: wer aus dem Quelltext baut, signiert
    nichts und hat auch nichts davon.
    """
    werkzeug = HIER / "tools" / "sign-release.py"
    if not werkzeug.is_file():
        return False
    r = subprocess.run([sys.executable, str(werkzeug), str(ziel)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        # Kein Abbruch: ohne Schluessel ist das der Normalfall.
        print(f"    (nicht signiert: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()})")
        return False
    print(r.stdout.rstrip())
    return True


# ======================================================================
def main():
    p = argparse.ArgumentParser(description="CO-37 Update-Paket bauen.")
    p.add_argument("version", nargs="?", help="Version, sonst backend/VERSION")
    p.add_argument("--no-sign", action="store_true",
                   help="nicht signieren, auch wenn ein Schluessel vorliegt")
    a = p.parse_args()

    vfile = HIER / "backend" / "VERSION"
    if a.version:
        with open(vfile, "w", encoding="ascii", newline="\n") as fh:
            fh.write(a.version + "\n")
    version = vfile.read_text(encoding="ascii").strip()

    setze_versionen(version)
    pruefe_versionen(version)
    pruefe_keine_geheimnisse()

    ziel = HIER / f"co37_v{version.replace('.', '_')}.zip"
    ziel.unlink(missing_ok=True)

    dateien = sammle()
    packe(ziel, dateien)
    pruefe_paket(ziel)

    groesse = ziel.stat().st_size
    summe = hashlib.sha256(ziel.read_bytes()).hexdigest()
    print(f"{ziel.name}  ({groesse // 1024} KB, {len(dateien)} Dateien)")
    print(f"SHA-256: {summe}")

    if not a.no_sign:
        signiere(ziel)


if __name__ == "__main__":
    main()
