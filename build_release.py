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
import getpass
import hashlib
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

HIER = Path(__file__).resolve().parent

# Verzeichnisse und Dateien, die ins Paket gehoeren.
# tools/ ist NICHT dabei (F-45 der Pruefung vom 2026-08-31).
#
# make-license.py sagt in seiner zweiten Zeile selbst: "Gehoert NICHT auf
# den Server und nicht in das Auslieferungspaket" - und wurde trotzdem
# mitgeliefert, hier und in build_release.sh. Kein Schluesselmaterial
# betroffen (pruefe_keine_geheimnisse haelt *.pem heraus), aber
# sign-release.py --init lag damit auf jedem Kundensystem, und eine
# falsche Aussage im Quelltext ist das, woran man sich spaeter orientiert.
#
# Der Watcher fasst tools/ ohnehin nicht an: MANAGED_DIRS kennt nur
# backend, frontend, agent und packaging. Ein bereits installiertes
# tools/ bleibt also stehen und muss von Hand weg.
VERZEICHNISSE = ["backend", "frontend", "agent", "packaging", "tests"]
DATEIEN = [
    "update_watcher.py", "setup.sh", "build_packages.sh",
    "build_release.sh", "build_release.py", "migrate_to_co37.sh",
    "README.md", "REVERSE-PROXY.md", "GITHUB.md", "LICENSE",
    "run-tests.sh", ".gitignore", ".gitattributes",
]

# .log/.log.1: der Agent schreibt sein Protokoll neben agent.py. Wer ihn
# einmal im Projektverzeichnis von Hand startet, hat danach eine
# agent.log darin liegen - sie ist per .gitignore nicht im Repository,
# wanderte aber bis 0.36.12 in jedes Paket. Damit hing der Paketinhalt
# davon ab, was auf dem Baurechner zufaellig herumlag, und im schlechten
# Fall stuenden dort Hostnamen und Fehlermeldungen aus einem fremden Netz.
AUSSCHLUSS = ("__pycache__", ".pyc", ".db", ".db-shm", ".db-wal", ".DS_Store",
              ".log", ".log.1")
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


# ======================================================================
# Passphrase des Signaturschluessels (0.37.21)
# ======================================================================
# Der private Signaturschluessel liegt seit 0.37.21 verschluesselt. Ohne
# den folgenden Umweg wuerde er DREIMAL je Bau nach der Passphrase
# fragen - einmal fuer agent.py, einmal fuer die Gegenprobe darueber,
# einmal fuer das Paket.
#
# Schlimmer: signiere() und signiere_agent() rufen das Werkzeug mit
# capture_output=True auf. Die Eingabeaufforderung von getpass ginge
# damit in den abgefangenen Strom und waere unsichtbar - der Bau saehe
# aus, als haenge er, und stuende in Wahrheit auf einer Eingabe, die
# niemand sieht.
#
# Also: hier einmal fragen, im Speicher halten, und ueber die Umgebung
# NUR DER KINDPROZESSE weiterreichen. Nicht ueber os.environ des eigenen
# Prozesses - das wuerde an alles vererbt, was sonst noch gestartet wird.
PRIVATER_SCHLUESSEL = Path.home() / ".co37" / "release-private.pem"
if os.environ.get("CO37_LICENSE_HOME"):
    PRIVATER_SCHLUESSEL = (Path(os.environ["CO37_LICENSE_HOME"])
                           / "release-private.pem")

_UMGEBUNG = {}


def passphrase_vorbereiten():
    """
    Wenn der Schluessel verschluesselt ist: einmal fragen und pruefen.

    Geprueft wird SOFORT, nicht erst beim ersten Signieren. Ein Vertipper
    soll vor dem Bau auffallen und nicht mittendrin - zu dem Zeitpunkt
    sind die Versionsnummern schon in die Dateien geschrieben.
    """
    if not PRIVATER_SCHLUESSEL.is_file():
        return
    # Am PEM-Kopf ablesbar, ohne zu entschluesseln und ohne cryptography.
    if b"ENCRYPTED PRIVATE KEY" not in PRIVATER_SCHLUESSEL.read_bytes():
        return

    werkzeug = HIER / "tools" / "sign-release.py"
    if not werkzeug.is_file():
        return

    if not sys.stdin.isatty():
        fehler("Der Signaturschluessel ist mit einer Passphrase geschuetzt,\n"
               "aber hier sitzt niemand an der Tastatur. Ohne Terminal\n"
               "laesst sich nicht signieren - mit --no-sign bauen oder von\n"
               "Hand aufrufen.")

    pw = getpass.getpass("Passphrase fuer den Signaturschluessel: ")
    umgebung = dict(os.environ, CO37_RELEASE_PASSPHRASE=pw)
    r = subprocess.run([sys.executable, str(werkzeug), "--schluessel-pruefen"],
                       capture_output=True, text=True, env=umgebung)
    if r.returncode != 0:
        fehler((r.stdout + r.stderr).strip() or "Die Passphrase passt nicht.")
    _UMGEBUNG["CO37_RELEASE_PASSPHRASE"] = pw
    print("Passphrase angenommen.")


def _kindumgebung():
    """Die Umgebung fuer sign-release.py - nur dort steht die Passphrase."""
    return dict(os.environ, **_UMGEBUNG) if _UMGEBUNG else None


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
                       capture_output=True, text=True, env=_kindumgebung())
    if r.returncode != 0:
        # Kein Abbruch: ohne Schluessel ist das der Normalfall.
        print(f"    (nicht signiert: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()})")
        return False
    print(r.stdout.rstrip())
    return True


def signiere_agent() -> bool:
    """
    Signiert agent/agent.py, damit der Agent seine eigene Aktualisierung
    pruefen kann (F-07 der Sicherheitspruefung vom 2026-08-22).

    Reihenfolge ist entscheidend und deshalb hier festgehalten: das muss
    NACH setze_versionen() geschehen - die schreibt die Zeile
    AGENT_VERSION in die Datei und aendert damit ihre Pruefsumme - und VOR
    sammle(), damit die entstandene .sig mit ins Paket wandert. Beides
    falschherum faellt nicht beim Bauen auf, sondern erst, wenn ein Agent
    beim Kunden die Aktualisierung ablehnt.

    Ohne privaten Schluessel kein Fehler, aber die alte .sig muss weg:
    sonst bliebe eine Signatur zu einer frueheren Fassung liegen, wanderte
    ins Paket, und jeder Agent mit ausgeliefertem Schluessel wiese die
    Aktualisierung mit "Signatur passt nicht" zurueck. Eine fehlende
    Signatur ist die ehrlichere Auskunft.
    """
    quelle = HIER / "agent" / "agent.py"
    sig = quelle.with_name(quelle.name + ".sig")
    # sign-release.py legt neben der Signatur auch eine .sha256 an. Beim
    # Paket ist die zum Abgleich von Hand gedacht; hier braucht sie
    # niemand und im Paket waere sie nur eine zweite Wahrheit.
    summe = quelle.with_name(quelle.name + ".sha256")
    werkzeug = HIER / "tools" / "sign-release.py"
    sig.unlink(missing_ok=True)
    summe.unlink(missing_ok=True)
    if not werkzeug.is_file():
        return False
    r = subprocess.run([sys.executable, str(werkzeug), str(quelle)],
                       capture_output=True, text=True, env=_kindumgebung())
    summe.unlink(missing_ok=True)
    if r.returncode != 0 or not sig.is_file():
        print("    (agent.py nicht signiert - Agents mit ausgeliefertem "
              "Schluessel lehnen die Selbstaktualisierung ab)")
        sig.unlink(missing_ok=True)
        return False

    # Gegenprobe mit dem ausgelieferten oeffentlichen Schluessel - genau
    # dem, den der Agent spaeter benutzt. Faengt ab, was hier am ehesten
    # schiefgeht: eine Signatur ueber einen Stand, den es nach dem
    # naechsten Schreibvorgang so nicht mehr gibt.
    p = subprocess.run([sys.executable, str(werkzeug), "--pruefen", str(quelle)],
                       capture_output=True, text=True, env=_kindumgebung())
    if p.returncode != 0:
        sig.unlink(missing_ok=True)
        fehler(f"Die Signatur von agent.py passt nicht zur Datei: "
               f"{(p.stdout + p.stderr).strip()[:200]}")
    print(f"agent/agent.py signiert -> {sig.name}")
    return True


# ======================================================================
def main():
    p = argparse.ArgumentParser(description="CO-37 Update-Paket bauen.")
    p.add_argument("version", nargs="?", help="Version, sonst backend/VERSION")
    p.add_argument("--no-sign", action="store_true",
                   help="nicht signieren, auch wenn ein Schluessel vorliegt")
    p.add_argument("--kein-archiv", action="store_true",
                   help="alte Pakete liegen lassen statt ins Archiv zu schieben")
    p.add_argument("--behalten", type=int, default=2,
                   help="wie viele Pakete im Ordner bleiben (Vorgabe 2)")
    a = p.parse_args()

    # Passphrase GANZ am Anfang, vor der ersten Schreiboperation. Steht
    # sie weiter unten, hat setze_versionen() die Versionsnummern schon in
    # agent.py, update_watcher.py und index.html geschrieben - und ein
    # Vertipper hinterlaesst einen halb angehobenen Arbeitsstand, den
    # niemand als solchen erkennt.
    if not a.no_sign:
        passphrase_vorbereiten()

    vfile = HIER / "backend" / "VERSION"
    if a.version:
        with open(vfile, "w", encoding="ascii", newline="\n") as fh:
            fh.write(a.version + "\n")
    version = vfile.read_text(encoding="ascii").strip()

    setze_versionen(version)
    pruefe_versionen(version)
    pruefe_keine_geheimnisse()

    if not a.no_sign:
        signiere_agent()
    else:
        # Auch hier weg - siehe Begruendung in signiere_agent().
        (HIER / "agent" / "agent.py.sig").unlink(missing_ok=True)
        (HIER / "agent" / "agent.py.sha256").unlink(missing_ok=True)

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

    if not a.kein_archiv:
        archiviere_alte(a.behalten)


def archiviere_alte(behalten: int):
    """
    Schiebt alte Pakete ins Archiv - als eigener Prozess, wie beim
    Signieren, damit der Dateiname mit Bindestrich kein Importproblem ist.

    Bewusst zum Schluss und ausdruecklich fehlertolerant: das frische
    Paket ist zu diesem Zeitpunkt gebaut und signiert. Scheitert das
    Aufraeumen - Ziel nicht beschreibbar, Datei gesperrt - darf der Bau
    deswegen nicht als gescheitert dastehen. Ein Hinweis genuegt.
    """
    werkzeug = HIER / "tools" / "archiv-pakete.py"
    if not werkzeug.is_file():
        return
    r = subprocess.run(
        [sys.executable, str(werkzeug), "--behalten", str(behalten)],
        capture_output=True, text=True)
    ausgabe = (r.stdout or "").rstrip() or (r.stderr or "").rstrip()
    if ausgabe:
        print(ausgabe)
    if r.returncode != 0:
        print("Hinweis: Archivieren fehlgeschlagen. Das Paket selbst ist fertig.")


if __name__ == "__main__":
    main()
