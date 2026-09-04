#!/usr/bin/env python3
"""
Baut das Windows-Agentenpaket als .msi.

Voraussetzungen auf dem Bausystem:
    apt install wixl msitools (wixl baut, msiinfo prueft das Ergebnis)

Das Paket enthaelt KEIN Geheimnis. Server und Enrollment-Token werden beim
Installieren als MSI-Eigenschaften uebergeben, damit die Datei selbst
unbedenklich weitergegeben und per GPO verteilt werden kann:

    msiexec /i co37-agent.msi /qn ^
        CO37SERVER="http://192.168.1.10:8080"

Python wird als Embeddable-Distribution mitgeliefert, damit auf dem
Zielsystem nichts vorausgesetzt wird. Die Datei wird beim ersten Bau
heruntergeladen und danach zwischengespeichert. Ohne Netzzugang kann sie
auch von Hand unter cache/ abgelegt werden.

Statt eines Windows-Dienstes wird eine geplante Aufgabe registriert, die
beim Systemstart als SYSTEM laeuft. Ein Python-Skript laesst sich nicht
ohne Wrapper als echter Dienst betreiben - der SCM wuerde es beenden.

Aufruf:
    python3 build_msi.py --version 0.2.1 --out ../state/packages
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PY = HERE.parent / "agent" / "agent.py"
AGENT_REQ = HERE.parent / "agent" / "requirements.txt"
CACHE = HERE / "cache"

# Siehe build_deb.py - derselbe Schluessel, dieselbe Begruendung.
RELEASE_KEY = HERE.parent / "backend" / "release_key.pub"

# Die Python-Laufzeit, die im MSI mitgeliefert wird und auf dem Zielsystem
# als SYSTEM laeuft.
#
# BEIM ANHEBEN NICHT NUR DIE ZAHL ANSEHEN, SONDERN DEN ZWEIG:
#
# python.org baut Windows-Binaerdateien nur waehrend der Bugfix-Phase
# eines Zweigs (rund zwei Jahre), danach gibt es zu den Sicherheits-
# versionen ausschliesslich Quelltext. Wer auf einem Zweig in der
# Sicherheitsphase bleibt, liefert eine eingefrorene Laufzeit aus, und
# zwar dauerhaft - es kommt dort nie wieder eine Embeddable nach.
#
#   3.12  Sicherheitsphase seit 3.12.11, letzte Embeddable war 3.12.10
#         (April 2025). Ende der Pflege 10/2028, ohne Binaerdateien.
#   3.13  Bugfix nur noch bis etwa 10/2026 - als Ziel zu kurz, man
#         staende in wenigen Wochen wieder hier.
#   3.14  Bugfix bis 10/2027, Pflege bis 10/2030. Deshalb hier.
#
# Vorgehen vor jeder Anhebung, in dieser Reihenfolge:
#   1. https://devguide.python.org/versions/ - ist der Zielzweig noch
#      "bugfix"? Wenn nicht, ist es der falsche Zweig, nicht nur die
#      falsche Zahl.
#   2. https://www.python.org/ftp/python/<version>/ - gibt es dort eine
#      "*-embed-amd64.zip"? Fehlt sie, bricht der MSI-Bau am Herunter-
#      laden ab (das DEB baut trotzdem weiter - 2026-08-29 so erlebt,
#      beim Versuch auf 3.12.14 zu heben, wo es keine gibt).
#   3. Den pip-Aufruf weiter unten mit der neuen Nebenversion trocken
#      laufen lassen: gibt es fuer alle Pakete aus agent/requirements.txt
#      ein passendes Rad? Fehlt eines, meldet sich die Gegenprobe.
#
# Am 2026-08-31 fuer 3.14 durchgespielt: alle acht Pakete loesen sich zu
# denselben Fassungen auf wie unter 3.12 - cffi und charset-normalizer
# mit echten cp314-Raedern, cryptography ueber abi3. requirements.txt
# musste dafuer nicht angefasst werden.
PY_VERSION = "3.14.7"
PY_ZIP = f"python-{PY_VERSION}-embed-amd64.zip"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP}"

# Feste Kennung, damit Windows Aktualisierungen als solche erkennt
UPGRADE_CODE = "7F3A9C21-5D4E-4B18-9E62-C0A7B1D2E3F4"

TASK_SCRIPT = r'''"""
Registriert die geplante Aufgabe und schreibt die Agent-Konfiguration.
Wird vom Installationspaket aufgerufen.
"""
import os
import subprocess
import sys
from pathlib import Path

INSTALL_DIR = Path(sys.argv[0]).resolve().parent

# Windows-Hilfsprogramme mit vollem Pfad aufrufen (F-47 der Pruefung vom
# 2026-08-31). Dieses Skript laeuft als SYSTEM aus einer CustomAction.
# CreateProcess durchsucht bei einem blossen Namen unter anderem das
# aktuelle Verzeichnis und PATH - beides muss nicht dem gehoeren, dem der
# Prozess gehoert. Ob das auf einem konkreten Windows wirklich
# ausnutzbar ist, haengt am Arbeitsverzeichnis der Aufgabe und daran, ob
# ein PATH-Eintrag beschreibbar ist; der volle Pfad kostet nichts und
# macht die Frage gegenstandslos.
SYS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
ICACLS = str(SYS32 / "icacls.exe")
SCHTASKS = str(SYS32 / "schtasks.exe")
CONF_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "CO37"
CONF = CONF_DIR / "agent.conf"

server = sys.argv[1] if len(sys.argv) > 1 else ""
verify = sys.argv[2] if len(sys.argv) > 2 else "true"

# Lief auf diesem Rechner schon einmal eine Installation? Das
# entscheidet spaeter, ob die geplante Aufgabe auch dann angelegt wird,
# wenn die Konfiguration unbrauchbar ist (F-48). VOR dem mkdir ablesen.
gab_es_schon = CONF.exists()

CONF_DIR.mkdir(parents=True, exist_ok=True)

# Vorhandene Konfiguration einlesen, damit ein bereits vergebenes Token
# eine Neuinstallation ueberlebt. Sonst meldet sich der Host neu an und
# landet wieder auf "wartet auf Freigabe".
existing = {}
if CONF.exists():
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()

# Aus einer vorhandenen Konfiguration werden nur die drei Schluessel
# uebernommen, die der Agent ueberhaupt liest - und verify_ssl nur, wenn
# es dieses Mal ausdruecklich mitgegeben wird.
#
# ZUR GESCHICHTE (F-46 der Pruefung vom 2026-08-31): hier stand kurz eine
# Fassung, die AUCH den 'server' verwarf. Das war als Haertung gedacht -
# C:\\ProgramData erlaubt jedem Benutzer, ein Unterverzeichnis anzulegen,
# ein lokaler Benutzer koennte also vor der Erstinstallation eine
# agent.conf hinlegen, und dieses Skript liest sie, bevor icacls die
# Rechte setzt.
#
# Die Fassung war falsch: sie hat den WIEDERINSTALLATIONS-Pfad zerstoert.
# Beim Upgrade per Doppelklick gibt niemand CO37SERVER auf der
# Befehlszeile mit; ohne den 'server' aus der vorhandenen Datei stand
# dann gar keine Adresse mehr da, das Skript beendete sich mit exit(1),
# und der Installer meldete Fehler 1721. Auf KK-WIN01 am 2026-09-01
# passiert, in 0.37.11 zurueckgenommen.
#
# GEKLAERT am 2026-09-01 auf einem Windows-Testhost. Der Gedanke, eine
# untergeschobene Datei an den RECHTEN zu erkennen (kein vererbtes (I)),
# war falsch: wer Besitzer einer Datei ist, setzt ihre Rechte selbst und
# kann sie beliebig aussehen lassen. Was ein Unprivilegierter nicht kann,
# ist den BESITZ an die Administratoren abgeben - daran haengt die
# Pruefung jetzt. Siehe von_privilegierter_hand() gleich darunter.
#
# Am 2026-09-02 im Feld nachgestellt, alle drei Faelle:
#   installierte Datei (Besitzer Administratoren) -> uebernommen
#   vorbelegt von einem Unprivilegierten          -> verworfen
#   von Hand von einem Administrator angelegt     -> uebernommen
def von_privilegierter_hand(pfad):
    r"""
    Stammt diese Datei von jemandem, der ohnehin alles darf?

    Der Docstring ist roh (r""), weil "VORDEFINIERT\Administratoren"
    darin steht. Ohne das r meldet Python beim Uebersetzen
    "invalid escape sequence '\A'" - heute eine Warnung, ab 3.15 ein
    Fehler. Am 2026-09-02 auf dem Testhost gesehen: das erzeugte
    install_task.py schrieb die Warnung bei JEDEM Installationslauf
    nach stderr. Der Wortlaut steht so im Paket, nicht in dieser
    Datei - hier aussen ist alles roh, drinnen war es das nicht.

    Der Besitzer ist das verlaessliche Merkmal (F-46, geklaert am
    2026-09-01 auf einem Windows-Testhost). Nachgemessen:

      von SYSTEM angelegt (Installer)  -> Besitzer VORDEFINIERT\Administratoren
      von einem Benutzer vorbelegt     -> Besitzer bleibt dieser Benutzer,
                                          AUCH nach der Installation

    Die Rechte taugen dafuer nicht: wer Besitzer ist, darf sie selbst
    setzen und koennte eine untergeschobene Datei genauso aussehen
    lassen wie eine installierte. Den Besitz an die Administratoren
    abzugeben kann ein Unprivilegierter dagegen nicht.

    Ein Administrator, der die Datei von Hand angelegt hat, gilt als
    vertrauenswuerdig - er koennte sie ohnehin direkt schreiben. Genau
    dieser Fall hat die erste, zu strenge Fassung dieser Pruefung
    zerstoert (Fehler 1721, siehe F-48).

    Im Zweifel True: eine Pruefung, die bei einem unerwarteten Windows
    den Upgrade-Pfad bricht, richtet mehr Schaden an als der schmale
    Angriff, den sie abwehrt.
    """
    ps = str(SYS32 / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    # Den Pfad ueber die Umgebung uebergeben, NICHT als Argument:
    # "powershell -Command <skript> <arg>" fuellt $args nicht, sondern
    # versucht <arg> als eigenen Befehl auszufuehren. Am 2026-09-01 auf
    # dem Testhost gesehen - die erste Fassung lief damit immer in den
    # Fehlerzweig und haette wegen des Rueckfalls unten JEDE Datei
    # durchgewinkt. Eine Pruefung, die nicht laeuft, ist schlimmer als
    # keine: sie sieht im Quelltext aus wie eine.
    #
    # Ueber die Umgebung gibt es ausserdem kein Anfuehrungszeichen-
    # Problem und keine Einschleusung ueber den Dateinamen.
    umgebung = dict(os.environ, CO37_CONF_PRUEFEN=str(CONF))
    skript = (
        "$o=(Get-Acl -LiteralPath $env:CO37_CONF_PRUEFEN).Owner;"
        "$s=(New-Object Security.Principal.NTAccount($o))."
        "Translate([Security.Principal.SecurityIdentifier]).Value;"
        "if ($s -eq 'S-1-5-18' -or $s -eq 'S-1-5-32-544') { 'JA'; exit };"
        "$a=(Get-LocalGroupMember -SID 'S-1-5-32-544' -EA SilentlyContinue)"
        " | ForEach-Object { $_.SID.Value };"
        "if ($a -contains $s) { 'JA' } else { 'NEIN' }"
    )
    try:
        res = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-Command", skript],
            capture_output=True, text=True, timeout=60, env=umgebung,
        )
    except Exception:  # noqa: BLE001
        return True
    antwort = (res.stdout or "").strip().upper()
    if res.returncode != 0 or antwort not in ("JA", "NEIN"):
        # Unerwartete Lage - im Zweifel durchlassen, siehe oben. Aber
        # ins Protokoll, sonst faellt eine dauerhaft kaputte Pruefung
        # niemandem auf.
        sys.stderr.write(
            "Besitzpruefung der agent.conf nicht moeglich, "
            "Datei wird uebernommen: %r\n" % (antwort or res.stderr)[:200])
        return True
    return antwort == "JA"


uebernommen = {}
if existing and not von_privilegierter_hand(CONF):
    # Untergeschoben. Nichts davon uebernehmen - weder den Server, mit
    # dem der Agent als SYSTEM spraeche, noch das Token.
    sys.stderr.write(
        "Die vorhandene agent.conf stammt nicht von einem Administrator "
        "und wird ignoriert.\n")
    existing = {}

for schluessel in ("server", "token"):
    if existing.get(schluessel):
        uebernommen[schluessel] = existing[schluessel]
existing = uebernommen

if server:
    existing["server"] = server
if verify.lower() == "false":
    existing["verify_ssl"] = "false"

def registriere_aufgabe():
    """
    Legt die geplante Aufgabe an. Gibt den Rueckgabewert von schtasks
    zurueck.

    Steht als Funktion da, weil sie auf ZWEI Wegen gebraucht wird - im
    Normalfall unten, und im Fehlerfall gleich hier drunter (F-48).
    """
    python_exe = INSTALL_DIR / "python" / "pythonw.exe"
    agent_py = INSTALL_DIR / "agent.py"

    subprocess.run([SCHTASKS, "/Delete", "/TN", "CO37Agent", "/F"],
                   capture_output=True)

    return subprocess.run([
        SCHTASKS, "/Create",
        "/TN", "CO37Agent",
        "/TR", f'"{python_exe}" "{agent_py}"',
        # Alle 5 Minuten statt nur ONSTART. Ein ONSTART-Ausloeser allein
        # laesst den Agent bis zum naechsten Systemstart tot liegen, wenn
        # er sich einmal nicht selbst neu starten konnte. Mehrfachstarts
        # sind ungefaehrlich: der Agent haelt eine Einzelinstanz-Sperre
        # und beendet sich sofort, wenn schon einer laeuft.
        "/SC", "MINUTE", "/MO", "5",
        "/RU", "SYSTEM",
        "/RL", "HIGHEST",
        "/F",
    ], capture_output=True, text=True)


# Ohne Serveradresse ist der Agent nicht lauffaehig, und die Installation
# soll scheitern - der Administrator hat CO37SERVER vergessen und muss es
# merken.
#
# ABER: die Aufgabe vorher trotzdem anlegen, wenn hier schon einmal etwas
# lief (F-48 der Pruefung vom 2026-09-01).
#
# Der Grund steht in der InstallExecuteSequence: RemoveExistingProducts
# laeuft nach InstallInitialize und loescht dabei ueber RemoveTask der
# ALTEN Fassung die geplante Aufgabe. Erst danach kommt RegisterTask.
# Scheitert dieses Skript dazwischen, ist die Aufgabe weg - und eine
# CustomAction hat keine Rueckrollaktion, der Installer stellt sie nicht
# wieder her. Der Host steht dann ganz ohne Agent da und meldet sich nie
# wieder.
#
# Genau so passiert am 2026-09-01 auf KK-WIN01: eine Haertung verwarf den
# 'server' aus der vorhandenen agent.conf, das Skript brach hier ab, und
# der Host war anschliessend still. Erst dieser Vorfall hat gezeigt, dass
# die Abwaegung im alten Kommentar falsch herum stand: dort hiess es
# "lieber die Installation scheitern lassen, als eine Aufgabe zu
# hinterlassen, die bei jedem Start still abbricht". Eine Aufgabe, die
# abbricht, bringt der naechste Lauf wieder in Ordnung - eine fehlende
# bemerkt niemand.
#
# Bei einer ERSTinstallation ohne Konfiguration wird nichts angelegt:
# dort gibt es keine Aufgabe zu retten, und der Installer raeumt
# INSTALLDIR beim Ruecklauf wieder ab - die Aufgabe zeigte danach ins
# Leere.
if not existing.get("server"):
    if gab_es_schon:
        registriere_aufgabe()
        sys.stderr.write(
            "Die geplante Aufgabe wurde wiederhergestellt, die "
            "Konfiguration ist aber unbrauchbar.\n")
    sys.stderr.write(
        "CO37SERVER fehlt und es liegt keine brauchbare agent.conf vor.\n"
        'Aufruf: msiexec /i <paket>.msi /qn CO37SERVER="http://server:8080"\n'
    )
    sys.exit(1)

order = ["server", "token", "verify_ssl"]
out = [f"{k} = {existing[k]}" for k in order if k in existing]
out += [f"{k} = {v}" for k, v in existing.items() if k not in order]
CONF.write_text("\n".join(out) + "\n", encoding="utf-8")

# Zugriff auf die Konfiguration beschraenken (F-06 der
# Sicherheitspruefung vom 2026-08-22). In der Datei steht das Dauertoken
# des Hosts; C:\ProgramData vererbt an neue Dateien ein Leserecht fuer
# "Benutzer", und os.chmod bewirkt unter Windows nichts.
#
# Ordner UND Datei: der Ordner, damit spaeter angelegte Dateien richtig
# beginnen, die Datei, weil sie in diesem Lauf schon geschrieben wurde
# und die neuen Vorgaben des Ordners nicht rueckwirkend erbt.
#
# Ueber SIDs statt Namen - "Administrators" heisst auf einem deutschen
# Windows anders, und ein Befehl mit dem falschen Namen scheitert still.
# Drei Schritte statt einem (F-49 der Pruefung vom 2026-09-01).
#
# "/inheritance:r" entfernt nur die VERERBTEN Rechte. Ein ausdruecklich
# gesetzter Eintrag bleibt stehen, und "/grant:r" ersetzt nur die
# genannten Identitaeten. Am 2026-09-01 auf einem Windows-Testhost
# nachgestellt: ein Eintrag "Jeder: Vollzugriff" ueberlebt die
# Absicherung unveraendert. Damit war F-06 auf einem Rechner, auf dem
# jemand den Ordner vorher angelegt hatte, nie behoben - der konnte das
# Dauertoken weiter lesen und den Server umbiegen.
#
# setowner zuerst: der Besitzer darf die Rechte jederzeit selbst wieder
# aendern. reset danach: raeumt alle ausdruecklichen Eintraege weg.
# Ordner VOR Datei, sonst erbt die Datei beim reset die alten
# Ordnerrechte zurueck.
for ziel, rechte in ((CONF_DIR, "(OI)(CI)(F)"), (CONF, "(F)")):
    for schritt in (
        [ICACLS, str(ziel), "/setowner", "*S-1-5-32-544"],
        [ICACLS, str(ziel), "/reset"],
        [ICACLS, str(ziel), "/inheritance:r",
         "/grant:r", f"*S-1-5-18:{rechte}", f"*S-1-5-32-544:{rechte}"],
    ):
        subprocess.run(schritt, capture_output=True)

res = registriere_aufgabe()

if res.returncode != 0:
    sys.stderr.write(res.stderr)
    sys.exit(1)

# Nachpruefen statt vertrauen: schtasks meldet auch dann Erfolg, wenn eine
# Wiederholung gar nicht gesetzt wurde. Ausserdem entsteht je nach Windows
# die Kombination StopAtDurationEnd=True bei leerer Duration - dann laeuft
# die Wiederholung nach dem ersten Durchlauf aus und der Agent kaeme nach
# einem misslungenen Selbstneustart nie wieder.
FIX_REPETITION = r"""
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskName CO37Agent
$r = $t.Triggers[0].Repetition
if (-not $r -or $r.Interval -ne 'PT5M' -or $r.StopAtDurationEnd) {
  $tr = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5)
  $tr.Repetition.StopAtDurationEnd = $false
  Set-ScheduledTask -TaskName CO37Agent -Trigger $tr | Out-Null
}
$r = (Get-ScheduledTask -TaskName CO37Agent).Triggers[0].Repetition
if ($r.Interval -ne 'PT5M' -or $r.StopAtDurationEnd) {
  Write-Error "Wiederholung konnte nicht gesetzt werden: $($r.Interval)"
}
"""
chk = subprocess.run(
    [str(SYS32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
     "-NoProfile", "-NonInteractive",
     "-ExecutionPolicy", "Bypass", "-Command", FIX_REPETITION],
    capture_output=True, text=True,
)
if chk.returncode != 0:
    # Kein Abbruch: der Agent laeuft auch ohne Wiederholung, ihm fehlt nur
    # das Sicherheitsnetz. Ein Installationsabbruch waere hier schlimmer.
    sys.stderr.write("Warnung: 5-Minuten-Wiederholung nicht gesetzt.\n")
    sys.stderr.write(chk.stderr)

subprocess.run([SCHTASKS, "/Run", "/TN", "CO37Agent"], capture_output=True)
'''

UNINSTALL_SCRIPT = r'''import os
import subprocess
from pathlib import Path

# Voller Pfad, wie im Installationsskript (F-47).
SCHTASKS = str(Path(os.environ.get("SystemRoot", r"C:\Windows"))
               / "System32" / "schtasks.exe")

subprocess.run([SCHTASKS, "/End", "/TN", "CO37Agent"], capture_output=True)
subprocess.run([SCHTASKS, "/Delete", "/TN", "CO37Agent", "/F"],
               capture_output=True)
'''


def fetch_python() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / PY_ZIP
    if target.is_file():
        return target
    print(f"Lade {PY_URL}")
    try:
        urllib.request.urlretrieve(PY_URL, target)
    except Exception as exc:
        raise SystemExit(
            f"Python-Embeddable konnte nicht geladen werden: {exc}\n"
            f"Datei von Hand herunterladen und ablegen unter:\n  {target}"
        )
    return target


def stable_guid(name: str) -> str:
    """
    Kennung einer Komponente. Reproduzierbar aus dem Pfad.

    Bewusst OHNE die Version - obwohl am 2026-08-28 kurzzeitig anders
    versucht. Warum die Version hier nicht hineingehoert:

    Nach den Komponentenregeln von Windows Installer gehoert dieselbe
    Datei am selben Ort immer in dieselbe Komponente. Haelt man sich daran,
    zaehlt der Installer mit, wie viele Produkte eine Komponente benutzen.
    Bei einem Upgrade uebernimmt die neue Fassung die Komponenten der
    alten; RemoveExistingProducts zieht dann nur einen Verweis ab und
    loescht nichts.

    Mit der Version in der GUID entfaellt genau dieses Netz: wird eine
    Datei aus irgendeinem Grund uebersprungen, loescht das Entfernen der
    alten Fassung sie ersatzlos. Der Fehler, den man damit haette
    zudecken wollen, wuerde dadurch schlimmer statt besser.

    Der wirkliche Grund fuer das Ueberspringen war ein anderer - siehe
    DATEI_VERSION weiter unten.
    """
    return str(uuid.UUID(hashlib.md5(f"co37:{name}".encode()).hexdigest())).upper()


# ----------------------------------------------------------------------
# Was an Bibliotheken ins Paket wandert
# ----------------------------------------------------------------------
# Bis 0.37.2 zaehlte der pip-Aufruf weiter unten die Paketnamen selbst
# auf, ohne Versionen, und agent/requirements.txt las niemand. Jeder Bau
# holte damit, was PyPI in diesem Augenblick als neueste Fassung anbot;
# zwei Bauten derselben CO-37-Version konnten verschiedene Agenten
# ergeben, und zu einem fertigen MSI gab es kein Verzeichnis dessen, was
# darin steckt. Was dort landet, wird mit dem Release-Schluessel signiert
# und laeuft auf jedem Windows-Host als SYSTEM.
#
# Jetzt kommt die Liste aus agent/requirements.txt, und nach dem
# Installieren wird gegengeprueft. Der Vergleich ist die eigentliche
# Absicherung: ein Pin, den niemand nachhaelt, faellt sonst genauso
# lautlos aus wie die Datei vorher.


def fehlende_hashes(pins: dict, hashes: dict) -> list[str]:
    """
    Welche Anforderungen ohne Hash dastehen.

    pip weist eine fehlende Angabe mit --require-hashes zwar auch ab -
    aber erst, nachdem es den Index befragt und die Datei geladen hat, und
    mit einer Meldung ueber "hash-checking mode". Hier steht der Grund im
    Klartext, und der Bau haelt an, bevor irgendetwas heruntergeladen ist.

    Geprueft wird ausserdem die Form: sha256 und 64 Hexstellen. Ein
    abgeschnittener Hash faellt sonst erst bei pip auf und sieht dort aus
    wie eine Manipulation.
    """
    schlecht = []
    for name in sorted(pins):
        eintraege = hashes.get(name) or []
        if not eintraege:
            schlecht.append(name)
            continue
        for h in eintraege:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", h):
                schlecht.append(f"{name} (unbrauchbare Angabe: {h!r})")
    return schlecht


def normname(name: str) -> str:
    """Paketnamen nach PEP 503 vergleichbar machen."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _logische_zeilen(text: str):
    """
    Fasst Fortsetzungszeilen zusammen und wirft Kommentare weg.

    Seit die Hashes mit in der Datei stehen, ist eine Anforderung ueber
    mehrere Zeilen verteilt:

        requests==2.34.2 \\
            --hash=sha256:2a0d60c1...

    Wer hier zeilenweise liest, sieht eine Fassung mit einem angehaengten
    Backslash und eine Zeile, die wie ein Paket namens "--hash" aussieht.
    """
    puffer = ""
    for roh in text.splitlines():
        zeile = roh.split("#", 1)[0].strip()
        if not zeile:
            continue
        if zeile.endswith("\\"):
            puffer += zeile[:-1].strip() + " "
            continue
        yield (puffer + zeile).strip()
        puffer = ""
    if puffer.strip():
        yield puffer.strip()


def lies_pins(text: str) -> dict[str, str]:
    """
    Liest name==version aus einer requirements.txt.

    Alles andere ist ein Fehler und wird als solcher gemeldet - eine Zeile
    ohne "==" (etwa ">=" oder ein blosser Name) waere genau die Luecke,
    die hier geschlossen werden soll. --hash-Angaben duerfen dahinter
    stehen; sie werden hier abgeschnitten und von lies_hashes() gelesen.
    """
    pins: dict[str, str] = {}
    for zeile in _logische_zeilen(text):
        anforderung = zeile.split("--hash=", 1)[0].strip()
        treffer = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)",
                               anforderung)
        if not treffer:
            raise SystemExit(
                f"agent/requirements.txt: Zeile ohne feste Fassung: {zeile!r}"
            )
        pins[normname(treffer.group(1))] = treffer.group(2)
    return pins


def lies_hashes(text: str) -> dict[str, list[str]]:
    """
    Welche Dateihashes zu welchem Paket gehoeren.

    Getrennt von lies_pins(), damit der Bau BEIDES einzeln pruefen kann:
    fehlt die Fassung, ist es eine Sache; fehlt der Hash, eine andere.
    """
    hashes: dict[str, list[str]] = {}
    for zeile in _logische_zeilen(text):
        teile = zeile.split("--hash=")
        treffer = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==", teile[0].strip())
        if not treffer:
            continue
        hashes[normname(treffer.group(1))] = [t.strip() for t in teile[1:]]
    return hashes


def lies_installiert(site_dir: Path) -> dict[str, str]:
    """Liest aus den .dist-info-Ordnern, was wirklich in site-packages liegt."""
    gefunden: dict[str, str] = {}
    for eintrag in sorted(site_dir.glob("*.dist-info")):
        name, _, fassung = eintrag.name[: -len(".dist-info")].rpartition("-")
        if name and fassung:
            gefunden[normname(name)] = fassung
    return gefunden


def pruefe_vendorstand(pins: dict[str, str],
                       installiert: dict[str, str]) -> list[str]:
    """
    Vergleicht die Pins mit dem Installierten. Leere Liste heisst in Ordnung.

    Beide Richtungen zaehlen. Fehlt etwas, ist das Paket unvollstaendig;
    liegt etwas zusaetzlich da, ist ein mittelbarer Abhaengiger neu
    hinzugekommen und ungepinnt mitgereist - der Fall, der ohne diese
    Pruefung erst beim Kunden auffaellt.
    """
    maengel = []
    for name in sorted(set(pins) | set(installiert)):
        soll, ist = pins.get(name), installiert.get(name)
        if ist is None:
            maengel.append(f"{name}=={soll} steht in requirements.txt, fehlt aber")
        elif soll is None:
            maengel.append(f"{name}=={ist} liegt im Paket, steht aber nirgends")
        elif soll != ist:
            maengel.append(f"{name}: erwartet {soll}, installiert {ist}")
    return maengel


# Dateiendungen, die unter Windows eine Versionsressource tragen.
VERSIONIERT = (".exe", ".dll", ".pyd")

# Was in die Spalte Version der File-Tabelle geschrieben wird.
#
# Der Anlass, am 2026-08-28: ein Upgrade von 0.36.12 brach mit 1721 ab,
# weil python.exe zum Zeitpunkt der CustomAction nicht auf der Platte lag.
# Im Protokoll davor, dreissigmal:
#
#     Disallowing installation of component: {…} since the same component
#     with higher versioned keyfile exists
#
# Und in der File-Tabelle des Pakets, bei allen Binaerdateien:
#
#     python.exe  |Version=[]|
#
# wixl laeuft unter Linux und liest die Windows-Versionsressource nicht
# aus. Die Spalte bleibt leer. Fuer Windows Installer schlaegt damit jede
# Datei auf der Platte, die eine Version hat, die Datei im Paket, die
# keine hat - er ueberspringt sie. Anschliessend entfernt
# RemoveExistingProducts die alte Fassung samt dieser Dateien:
#
#     FileRemove(FileName=python.exe, ComponentId={B8EC3B81-…})
#
# Uebrig blieb eine Installation ohne Python-Laufzeit. Aufgefallen ist es
# nur, weil die CustomAction python.exe braucht und mit 1721 abbrach - was
# alles zurueckrollte und den Schaden verhinderte.
#
# Eine hoehere Zahl als jede echte Dateiversion (die Felder sind je 16
# Bit breit, 65535 ist das Groesste). Damit gewinnt immer das Paket, und
# ein Upgrade schreibt seine Binaerdateien wirklich neu.
#
# Die echten Versionen auszulesen wuerde nichts bringen: der Installer
# ueberspringt auch bei GLEICHER Version. Gebraucht wird "hoeher".
DATEI_VERSION = "65535.0.0.0"


def build(version: str, out_dir: Path) -> Path:
    if not shutil.which("wixl"):
        raise SystemExit(
            "wixl fehlt. Installieren mit:  apt install wixl msitools\n"
            "Hinweis: wixl ist ein eigenes Paket, nicht Teil von msitools -\n"
            "msitools wird zusaetzlich fuer die Gegenprobe gebraucht."
        )
    if not AGENT_PY.is_file():
        raise SystemExit(f"agent.py nicht gefunden unter {AGENT_PY}")

    work = HERE / "_msi_build"
    shutil.rmtree(work, ignore_errors=True)
    (work / "python").mkdir(parents=True)

    # Python entpacken
    with zipfile.ZipFile(fetch_python()) as zf:
        zf.extractall(work / "python")

    # Der Agent braucht requests - im Embeddable ist kein pip enthalten.
    # Deshalb site-packages danebenlegen und Pfad freischalten.
    #
    # Nicht auf "import site" pruefen: die Datei enthaelt ab Werk die Zeile
    # "#import site" auskommentiert. Ein Teilstring-Test haelt das faelschlich
    # fuer erledigt, site bleibt aus und site-packages unerreichbar - genau
    # der Fehler, der 0.12.1 unter Windows unbrauchbar gemacht hat.
    pth = next((work / "python").glob("python*._pth"), None)
    if pth is None:
        raise SystemExit("Keine ._pth in der Python-Embeddable gefunden")
    lines = [
        ln for ln in pth.read_text().splitlines()
        if ln.strip() not in ("import site", "#import site", "# import site")
        and ln.strip() != "Lib\\site-packages"
    ]
    lines += ["Lib\\site-packages", "import site"]
    pth.write_text("\n".join(lines) + "\n")

    # Gegenprobe: ohne diese beiden Zeilen ist das Paket wertlos
    check = pth.read_text().splitlines()
    if "Lib\\site-packages" not in check or "import site" not in check:
        raise SystemExit(f"._pth wurde nicht korrekt geschrieben: {pth}")

    site_dir = work / "python" / "Lib" / "site-packages"
    site_dir.mkdir(parents=True, exist_ok=True)

    if not AGENT_REQ.is_file():
        raise SystemExit(f"Fehlt: {AGENT_REQ}")
    # Erst lesen, dann installieren: eine Zeile ohne feste Fassung soll den
    # Bau anhalten, bevor irgendetwas heruntergeladen ist.
    req_text = AGENT_REQ.read_text(encoding="utf-8")
    pins = lies_pins(req_text)
    if not pins:
        raise SystemExit(f"{AGENT_REQ} nennt keine Pakete")

    # Und jede Anforderung braucht mindestens einen Hash.
    fehlende = fehlende_hashes(pins, lies_hashes(req_text))
    if fehlende:
        raise SystemExit(
            "In agent/requirements.txt fehlen die Hashes fuer: "
            + ", ".join(fehlende)
            + "\nMit 'python3 tools/pin-hashes.py --schreiben' erzeugen. "
              "Ein Pin ohne Hash bindet an eine Fassung, nicht an eine "
              "Datei - siehe den Kopf der Datei.")

    # Die Liste kommt aus agent/requirements.txt, nicht aus dieser Datei -
    # sonst steht sie an zwei Stellen und nur eine wird gepflegt. Darin
    # steckt auch cryptography, die der Agent fuer die Signaturpruefung
    # der Selbstaktualisierung braucht (F-07); unter Linux kommt sie aus
    # python3-cryptography, im Embeddable gibt es kein pip.
    #
    # Ausdruecklich Windows-Wheels anfordern. Ohne --platform wuerde pip die
    # Pakete fuer das Bausystem (Linux) holen, die unter Windows nicht laufen.
    #
    # --require-hashes bindet an die DATEI statt an die Fassungsnummer
    # (2026-09-04). Die drei Angaben darueber bestimmen mit, welche Datei
    # pip auswaehlt - wer eine davon aendert, aendert die erwarteten
    # Hashes mit. Siehe den Kopf von agent/requirements.txt.
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(site_dir),
         "--platform", "win_amd64",
         "--python-version", ".".join(PY_VERSION.split(".")[:2]),
         "--only-binary=:all:",
         "--require-hashes",
         "-r", str(AGENT_REQ)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        hinweis = ""
        if "hash" in (res.stderr + res.stdout).lower():
            hinweis = ("\n\nDas sieht nach den Hashes aus. Haeufigster Grund: "
                       "PY_VERSION wurde angehoben, damit waehlt pip andere "
                       "Raeder (cp314 -> cp315). Neu erzeugen mit "
                       "'python3 tools/pin-hashes.py --schreiben'.")
        raise SystemExit(f"pip fehlgeschlagen: {res.stderr[-600:]}{hinweis}")

    # Gegenprobe am Ergebnis, nicht an der Absicht - dieselbe Regel wie
    # beim fertigen MSI weiter unten. Faengt zwei Faelle: ein Pin, der
    # nicht angekommen ist, und einen neuen mittelbaren Abhaengigen, der
    # ungepinnt mitgereist waere.
    installiert = lies_installiert(site_dir)
    maengel = pruefe_vendorstand(pins, installiert)
    if maengel:
        raise SystemExit(
            "Der Inhalt von site-packages passt nicht zu "
            "agent/requirements.txt:\n  " + "\n  ".join(maengel)
            + "\nEntweder die Datei anpassen oder klaeren, woher das kommt."
        )

    # Ins Bauprotokoll, damit zu jedem Paket nachlesbar ist, was darin
    # steckt. Ohne diese Zeile gibt es auf "welche urllib3 laeuft beim
    # Kunden" keine Antwort ausser: auf dem Zielsystem nachsehen.
    print("    Mitgelieferte Bibliotheken: "
          + ", ".join(f"{n}=={v}" for n, v in sorted(installiert.items())))

    shutil.copy(AGENT_PY, work / "agent.py")
    # Muss neben agent.py liegen - der Agent sucht ihn dort.
    if RELEASE_KEY.is_file():
        shutil.copy(RELEASE_KEY, work / "release_key.pub")
    elif os.environ.get("CO37_OHNE_SIGNATUR") == "ja":
        print("!!! kein backend/release_key.pub - der Agent aus diesem Paket "
              "prueft seine Selbstaktualisierung NICHT. Ausdruecklich "
              "erlaubt ueber CO37_OHNE_SIGNATUR=ja.")
    else:
        # Abbrechen statt hinweisen (F-44 der Pruefung vom 2026-08-31).
        #
        # Ein Hinweis auf stdout geht im Bauprotokoll unter, und dem
        # fertigen Paket sieht man den Unterschied nicht an - seine
        # Agenten nehmen dann jeden Code an, den ihr Server ihnen
        # schickt. Dieselbe Art Fehler wie die von wixl stillschweigend
        # weggelassene CustomAction: ein Bau, der eine
        # Sicherheitseigenschaft leise fallen laesst, ist schlimmer als
        # einer, der abbricht.
        #
        # Der Weg fuer einen Stand ohne Signaturschluessel bleibt offen,
        # er muss nur ausgesprochen werden.
        raise SystemExit(
            "backend/release_key.pub fehlt. Agenten aus diesem Paket "
            "wuerden ihre Selbstaktualisierung nicht pruefen.\n"
            "Schluessel anlegen:  python3 tools/sign-release.py --init\n"
            "Oder ausdruecklich ohne:  CO37_OHNE_SIGNATUR=ja ...")
    (work / "install_task.py").write_text(TASK_SCRIPT, encoding="utf-8")
    (work / "uninstall_task.py").write_text(UNINSTALL_SCRIPT, encoding="utf-8")

    # ------------------------------------------------------------------
    # WiX-Beschreibung erzeugen
    # ------------------------------------------------------------------
    files, comps, refs = [], [], []

    def add_tree(base: Path, prefix: str = ""):
        """Erzeugt Directory- und Component-Elemente rekursiv."""
        entries = sorted(base.iterdir())
        dirs_xml, files_xml = [], []
        for entry in entries:
            rel = f"{prefix}{entry.name}"
            ident = "f_" + hashlib.md5(rel.encode()).hexdigest()[:16]
            if entry.is_dir():
                sub = add_tree(entry, rel + "/")
                dirs_xml.append(
                    f'<Directory Id="d_{ident}" Name="{entry.name}">{sub}</Directory>'
                )
            else:
                guid = stable_guid(rel)
                # Nur Binaerdateien - nur die tragen unter Windows eine
                # Versionsressource, und nur bei ihnen entsteht sonst der
                # Vergleich "hat eine Version" gegen "hat keine". Siehe
                # DATEI_VERSION.
                ver = (f' DefaultVersion="{DATEI_VERSION}"'
                       if entry.suffix.lower() in VERSIONIERT else "")
                files_xml.append(
                    f'<Component Id="c_{ident}" Guid="{guid}">'
                    f'<File Id="{ident}" Source="{entry}" KeyPath="yes"{ver}/>'
                    f'</Component>'
                )
                refs.append(f'<ComponentRef Id="c_{ident}"/>')
        return "".join(dirs_xml) + "".join(files_xml)

    tree = add_tree(work)

    # Hersteller steht spaeter in den Programmeigenschaften jedes Windows-
    # Hosts. Neutral als Vorgabe, damit ein weitergegebenes Paket nicht die
    # Firma dessen traegt, der es gebaut hat. Fuer eigene Pakete setzen:
    #     CO37_VENDOR="ITkub" python3 packaging/build_msi.py
    vendor = os.environ.get("CO37_VENDOR", "CO-37")

    wxs = f'''<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="CO-37 Agent" Language="1031" Version="{version}"
           Manufacturer="{vendor}" UpgradeCode="{UPGRADE_CODE}">
    <Package InstallerVersion="200" Compressed="yes" InstallScope="perMachine"
             Description="CO-37 Agent" Manufacturer="{vendor}"/>
    <Media Id="1" Cabinet="agent.cab" EmbedCab="yes"/>

    <Property Id="CO37SERVER" Secure="yes"/>
    <Property Id="CO37VERIFYSSL" Value="true" Secure="yes"/>

    <!-- Ohne das legt sich jede neue Fassung neben die alte, statt sie zu
         ersetzen: der UpgradeCode allein bewirkt nichts. Getestet gegen
         wixl 0.103 - Upgrade-Tabelle und RemoveExistingProducts landen
         beide im Paket. -->
    <Upgrade Id="{UPGRADE_CODE}">
      <UpgradeVersion Minimum="0.0.0" IncludeMinimum="yes"
                      Maximum="{version}" IncludeMaximum="no"
                      Property="OLDERFOUND"/>
    </Upgrade>

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFiles64Folder">
        <Directory Id="INSTALLDIR" Name="CO37">
          {tree}
        </Directory>
      </Directory>
    </Directory>

    <Feature Id="Main" Title="CO-37 Agent" Level="1">
      {"".join(refs)}
    </Feature>

    <!-- Der Pfad zu python.exe kommt aus einer Eigenschaft, NICHT aus der
         File-Tabelle.

         Vorher stand hier FileKey="…python.exe" - eine CustomAction vom
         Typ 18, "fuehre eine EXE aus der File-Tabelle aus". Die verlangt,
         dass genau diese Datei im laufenden Vorgang installiert wird.

         Bei einem Upgrade ist sie das nicht. Die Komponenten-GUIDs
         entstehen aus dem Dateipfad und sind zwischen zwei Fassungen
         gleich; python.exe traegt eine Dateiversion und liegt unveraendert
         schon da. Windows Installer entscheidet dann "Disallowing
         installation of component … since the same component with higher
         versioned keyfile exists" und ueberspringt sie. Die Aktion findet
         ihre EXE nicht und bricht mit Fehler 2753 ab - der ganze Vorgang
         wird zurueckgerollt.

         Beobachtet am 2026-08-26 beim ersten MSI-Upgrade ueberhaupt
         (0.36.12 -> 0.36.14). Der Fehler steckte vorher schon drin und
         wurde nur nie ausgeloest, weil Agents sich sonst selbst
         aktualisieren.

         Betroffen sind nur die 30 versionierten Dateien der
         Python-Embeddable. agent.py, release_key.pub und alles unter
         site-packages werden normal eingespielt - uebersprungen heisst
         hier nicht fehlend, python.exe liegt danach an Ort und Stelle.
         Es geht allein darum, wie die Aktion sie findet.

         Nicht ueber Directory= geloest: wixl laesst CustomActions mit
         diesem Attribut STILLSCHWEIGEND weg - die Tabelle bleibt leer und
         das Paket sieht heil aus. Deshalb der Weg ueber eine Eigenschaft
         (Typ 51 setzt sie, Typ 50 benutzt sie) und die Gegenprobe am
         fertigen Paket weiter unten. -->
    <CustomAction Id="SetPyExe" Property="CO37PYEXE"
                  Value="[INSTALLDIR]python\\python.exe" Execute="immediate"/>
    <CustomAction Id="RegisterTask" Property="CO37PYEXE"
                  ExeCommand="&quot;[INSTALLDIR]install_task.py&quot; &quot;[CO37SERVER]&quot; &quot;[CO37VERIFYSSL]&quot;"
                  Execute="deferred" Impersonate="no" Return="check"/>
    <CustomAction Id="RemoveTask" Property="CO37PYEXE"
                  ExeCommand="&quot;[INSTALLDIR]uninstall_task.py&quot;"
                  Execute="deferred" Impersonate="no" Return="ignore"/>

    <!-- Feste Nummern statt Before=/After=.

         wixl loest Before=/After= bei eigenen Aktionen nicht verlaesslich
         auf. Gemessen am 2026-08-26: derselbe Quellstand, zweimal gebaut,
         SetPyExe einmal auf 1401 und einmal auf 1. In einem Minimalbeispiel
         kam zwanzigmal von zwanzig die 1 heraus.

         Die 1 waere still toedlich: sie liegt vor CostFinalize (1000), und
         dort wird INSTALLDIR erst aufgeloest. CO37PYEXE bekaeme einen
         unvollstaendigen Pfad, die geplante Aufgabe zeigte ins Leere - und
         zwar nur in manchen Paketen. Ein Fehler, der vom Bau abhaengt und
         nicht vom Code, ist der schlechteste, den man haben kann.

         Mit ausdruecklicher Nummer uebernimmt wixl sie unveraendert
         (zwanzig von zwanzig). Die Zahlen sind die Standardplaetze:
         InstallValidate 1400, InstallInitialize 1500, RemoveFiles 3500,
         InstallFiles 4000. -->
    <InstallExecuteSequence>
      <!-- Vor dem Einspielen der neuen Dateien die alte Fassung entfernen.
           Bis 0.37.14 loeschte deren RemoveTask dabei die geplante Aufgabe,
           und RegisterTask legte sie zweitausend Sequenznummern spaeter neu
           an. Alles, was dazwischen abbrach, hinterliess einen Host ohne
           Agent (F-48). Seit 0.37.15 wird sie beim Upgrade gar nicht erst
           geloescht - siehe die Bedingung an RemoveTask weiter unten. -->
      <RemoveExistingProducts After="InstallInitialize"/>
      <!-- Nach CostFinalize, damit INSTALLDIR aufgeloest ist, und vor
           InstallInitialize: ab da schreibt der Installer das Skript fuer
           die aufgeschobenen Aktionen, und darin steht der Pfad dann schon
           eingesetzt. -->
      <Custom Action="SetPyExe" Sequence="1401"/>
      <!-- Nicht "NOT Installed": sonst laesst sich CO37SERVER nach einer
           Installation ohne Eigenschaft nie mehr nachreichen, weil die
           Aktion bei jedem weiteren Aufruf uebersprungen wird. -->
      <Custom Action="RegisterTask" Sequence="4001">NOT REMOVE</Custom>
      <!-- Die grosse Variante von F-48 (2026-09-03).

           REMOVE="ALL" allein trifft AUCH den Ausbau der alten Fassung
           waehrend eines Upgrades: Windows Installer deinstalliert sie
           dabei ganz gewoehnlich, mit REMOVE=ALL. Genau dort loeschte
           RemoveTask die geplante Aufgabe - und ab da haengt alles daran,
           dass RegisterTask sie wieder anlegt. Tut es das nicht, ist der
           Host still, und eine CustomAction hat keine Rueckrollaktion.

           UPGRADINGPRODUCTCODE setzt der Installer genau dann, wenn diese
           Deinstallation Teil eines Upgrades ist; es enthaelt den
           ProductCode der neuen Fassung. Mit der Bedingung bleibt die
           Aufgabe beim Upgrade einfach stehen. RegisterTask legt sie
           danach ohnehin neu an - schtasks laeuft mit /Delete und /F, das
           ist gefahrlos wiederholbar.

           Bei einer echten Deinstallation ist die Eigenschaft leer, die
           Bedingung greift, und die Aufgabe wird entfernt wie bisher.

           WIRKSAM AB WANN: die Bedingung steht im Paket, das ENTFERNT
           wird - also in der bereits installierten Fassung. Ein Upgrade
           von 0.37.14 auf 0.37.15 laeuft noch nach der alten Regel; erst
           beim naechsten Upgrade danach greift sie. Dieselbe Sache wie
           bei F-47 und F-49, nur eine Runde weiter.

           Auf einem Windows-Testhost gemessen (2026-09-03), mit zwei
           kleinen Wegwerf-Paketen, die nur diese beiden Aktionen tragen:
           ohne die Bedingung lief RemoveTask beim Upgrade, mit ihr
           nicht - und bei der Deinstallation in beiden Faellen. -->
      <Custom Action="RemoveTask" Sequence="3499">REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE</Custom>
    </InstallExecuteSequence>
  </Product>
</Wix>
'''
    wxs_path = work.parent / "co37-agent.wxs"
    wxs_path.write_text(wxs, encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"co37-agent-{version}.msi"

    # wixl schreibt die Datei selbst, hier laesst sich kein O_NOFOLLOW
    # setzen (F-29 der Pruefung vom 2026-08-31). Also vorher nachsehen:
    # eine Verknuepfung unter dem erwarteten Paketnamen wuerde den Bau
    # als root an eine frei gewaehlte Stelle schreiben lassen. Seit
    # 0.37.10 liegt das Ziel in state/ und gehoert root - das hier ist
    # die zweite Schranke, falls der Ausgabeort je wieder wandert.
    if target.is_symlink() or out_dir.is_symlink():
        raise SystemExit(
            f"{target} oder das Zielverzeichnis ist eine Verknuepfung. "
            f"Der Bau laeuft als root und schreibt nicht hindurch.")

    res = subprocess.run(
        ["wixl", "-v", "--arch", "x64", "-o", str(target), str(wxs_path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not target.is_file():
        raise SystemExit(f"wixl fehlgeschlagen:\n{res.stderr[-2000:]}")

    pruefe_paket(target)

    shutil.rmtree(work, ignore_errors=True)
    wxs_path.unlink(missing_ok=True)
    return target


# Zeichenketten, die im fertigen Paket vorkommen MUESSEN.
#
# Der Anlass, am 2026-08-26: wixl kennt das Attribut Directory= an einer
# CustomAction nicht und laesst die betroffenen Eintraege dann
# STILLSCHWEIGEND weg - Rueckgabewert 0, Paket entsteht, Tabelle leer. Ein
# Paket, das sich bauen laesst und beim Kunden nichts einrichtet, ist
# schlimmer als eines, das gar nicht erst entsteht.
#
# Diese Stufe kommt ohne Werkzeug aus - sie sucht die Zeichenketten im
# Paket. CO37PYEXE steht ausschliesslich in der CustomAction-Tabelle;
# fehlt die Zeichenkette, ist die Tabelle leer.
MUSS_ENTHALTEN = (
    "CO37PYEXE",         # die Eigenschaft mit dem Pfad zu python.exe
    "RegisterTask",      # die Aktion, die die geplante Aufgabe anlegt
    "UPGRADINGPRODUCTCODE",   # die Bedingung an RemoveTask (F-48, gross)
    "install_task.py",
    "agent.py",
)


# Wo die eigenen Aktionen im Ablauf stehen muessen. Siehe die Begruendung
# an der InstallExecuteSequence weiter oben.
ERWARTETE_SEQUENZ = {"SetPyExe": 1401, "RemoveTask": 3499, "RegisterTask": 4001}

# Was in der Bedingungsspalte stehen MUSS. Die grosse Variante von F-48
# haengt an genau diesem Wort - ohne es loescht die alte Fassung beim
# Upgrade wieder die geplante Aufgabe.
ERWARTETE_BEDINGUNG = {"RemoveTask": "UPGRADINGPRODUCTCODE"}


def pruefe_paket(msi: Path):
    """
    Gegenprobe am fertigen Paket.

    Zwei Stufen, weil nicht ueberall dasselbe Werkzeug da ist:

      Immer: die Zeichenketten aus MUSS_ENTHALTEN. Das faengt eine ganz
      weggelassene CustomAction-Tabelle.

      Wenn msiinfo vorliegt (Paket msitools): zusaetzlich die tatsaechlichen
      Nummern im Ablaufplan. Das faengt eine Aktion, die zwar da ist, aber
      an der falschen Stelle steht - der Fall, der sich sonst erst beim
      Kunden zeigt. Fehlt msiinfo, wird das gesagt statt stillschweigend
      uebergangen: eine ausgelassene Pruefung, die wie eine bestandene
      aussieht, ist schlimmer als gar keine.
    """
    roh = msi.read_bytes()

    def drin(s: str) -> bool:
        return s.encode("ascii") in roh or s.encode("utf-16-le") in roh

    fehlend = [s for s in MUSS_ENTHALTEN if not drin(s)]
    if fehlend:
        msi.unlink(missing_ok=True)
        raise SystemExit(
            "Das gebaute MSI ist unvollstaendig - es fehlt: "
            + ", ".join(fehlend)
            + "\nwixl hat vermutlich etwas stillschweigend ausgelassen. "
              "Das Paket wurde verworfen.")

    if not shutil.which("msiinfo"):
        print("Hinweis: msiinfo fehlt (apt install msitools) - der Ablaufplan "
              "im Paket wurde NICHT geprueft.")
        return

    res = subprocess.run(["msiinfo", "export", str(msi), "InstallExecuteSequence"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        msi.unlink(missing_ok=True)
        raise SystemExit(f"Ablaufplan im MSI nicht lesbar: {res.stderr[-300:]}")

    ist = {}
    for zeile in res.stdout.splitlines():
        teile = zeile.split("\t")
        if len(teile) >= 3 and teile[2].strip().isdigit():
            ist[teile[0]] = int(teile[2])

    falsch = [f"{name}: erwartet {nr}, ist {ist.get(name, 'gar nicht da')}"
              for name, nr in ERWARTETE_SEQUENZ.items() if ist.get(name) != nr]

    # Und die Bedingungen, nicht nur die Nummern. Die grosse Variante von
    # F-48 haengt an einem einzigen Wort in der Bedingungsspalte; faellt es
    # weg, sieht das Paket in jeder anderen Hinsicht richtig aus und
    # loescht beim naechsten Upgrade wieder die geplante Aufgabe. Genau die
    # Art Fehler, die man dem fertigen Paket nicht ansieht.
    bedingung = {}
    for zeile in res.stdout.splitlines():
        teile = zeile.split("\t")
        if len(teile) >= 3:
            bedingung[teile[0]] = teile[1]
    for name, muss in ERWARTETE_BEDINGUNG.items():
        ist_bed = bedingung.get(name, "")
        if muss.lower() not in ist_bed.lower():
            falsch.append(f"{name}: Bedingung {ist_bed!r} enthaelt "
                          f"{muss!r} nicht")
    if falsch:
        msi.unlink(missing_ok=True)
        raise SystemExit(
            "Der Ablaufplan im gebauten MSI stimmt nicht:\n  "
            + "\n  ".join(falsch)
            + "\nDas Paket wurde verworfen.")

    # Jede Binaerdatei braucht eine Version - sonst ueberspringt Windows
    # Installer sie beim Upgrade und RemoveExistingProducts loescht sie
    # ersatzlos. Siehe DATEI_VERSION. Das war genau der Fehler, den man
    # dem fertigen Paket nicht ansieht: es baut sauber, installiert sich
    # frisch einwandfrei und zerlegt sich erst beim Upgrade.
    res = subprocess.run(["msiinfo", "export", str(msi), "File"],
                         capture_output=True, text=True)
    if res.returncode != 0:
        msi.unlink(missing_ok=True)
        raise SystemExit(f"File-Tabelle im MSI nicht lesbar: {res.stderr[-300:]}")

    ohne = []
    for zeile in res.stdout.splitlines()[3:]:
        s = zeile.split("\t")
        if len(s) >= 5 and Path(s[2]).suffix.lower() in VERSIONIERT and not s[4].strip():
            ohne.append(s[2])
    if ohne:
        msi.unlink(missing_ok=True)
        raise SystemExit(
            "Im gebauten MSI stehen Binaerdateien ohne Version in der "
            "File-Tabelle:\n  " + ", ".join(ohne[:10])
            + (f" … ({len(ohne)} insgesamt)" if len(ohne) > 10 else "")
            + "\nEin Upgrade wuerde sie ueberspringen und anschliessend "
              "loeschen. Das Paket wurde verworfen.")


def agent_version() -> str:
    """Liest AGENT_VERSION aus agent.py - das ist die Paketversion."""
    for line in AGENT_PY.read_text(encoding="utf-8").splitlines():
        if line.startswith("AGENT_VERSION"):
            return line.split("=")[1].strip().strip('"\'')
    raise SystemExit("AGENT_VERSION nicht in agent.py gefunden")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=agent_version())
    ap.add_argument("--out", default=str(HERE.parent / "state" / "packages"))
    args = ap.parse_args()

    path = build(args.version, Path(args.out))
    print(f"{path}  ({path.stat().st_size / 1024 / 1024:.1f} MB)")
