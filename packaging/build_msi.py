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
    python3 build_msi.py --version 0.2.1 --out ../data/packages
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PY = HERE.parent / "agent" / "agent.py"
CACHE = HERE / "cache"

# Siehe build_deb.py - derselbe Schluessel, dieselbe Begruendung.
RELEASE_KEY = HERE.parent / "backend" / "release_key.pub"

# 3.12.10 war die letzte 3.12.x-Fassung mit Windows-Binärdateien - ab
# 3.12.11 liefert python.org nur noch Quelltext-Tarballs (Sicherheits-
# fixe bis 10/2028, aber ohne Embeddable). Vor jeder Anhebung dieser
# Zahl im FTP-Verzeichnis der Zielversion pruefen, ob es dort ueberhaupt
# noch eine "*-embed-amd64.zip" gibt - sonst bricht der MSI-Bau am
# Herunterladen ab, das DEB baut trotzdem weiter (2026-08-29 erlebt,
# beim Versuch auf 3.12.14 zu heben).
PY_VERSION = "3.12.10"
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
CONF_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "CO37"
CONF = CONF_DIR / "agent.conf"

server = sys.argv[1] if len(sys.argv) > 1 else ""
verify = sys.argv[2] if len(sys.argv) > 2 else "true"

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

if server:
    existing["server"] = server
if verify.lower() == "false":
    existing["verify_ssl"] = "false"
else:
    existing.pop("verify_ssl", None)

# Ohne Serveradresse ist der Agent nicht lauffaehig. Lieber die
# Installation scheitern lassen, als eine Aufgabe zu hinterlassen, die
# bei jedem Start still abbricht.
if not existing.get("server"):
    sys.stderr.write(
        "CO37SERVER fehlt und es liegt keine agent.conf vor.\n"
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
for ziel, rechte in ((CONF_DIR, "(OI)(CI)(F)"), (CONF, "(F)")):
    subprocess.run(
        ["icacls", str(ziel), "/inheritance:r",
         "/grant:r", f"*S-1-5-18:{rechte}", f"*S-1-5-32-544:{rechte}"],
        capture_output=True,
    )

python_exe = INSTALL_DIR / "python" / "pythonw.exe"
agent_py = INSTALL_DIR / "agent.py"

subprocess.run(["schtasks", "/Delete", "/TN", "CO37Agent", "/F"],
               capture_output=True)

res = subprocess.run([
    "schtasks", "/Create",
    "/TN", "CO37Agent",
    "/TR", f'"{python_exe}" "{agent_py}"',
    # Alle 5 Minuten statt nur ONSTART. Ein ONSTART-Ausloeser allein
    # laesst den Agent bis zum naechsten Systemstart tot liegen, wenn er
    # sich einmal nicht selbst neu starten konnte. Mehrfachstarts sind
    # ungefaehrlich: der Agent haelt eine Einzelinstanz-Sperre und
    # beendet sich sofort, wenn schon einer laeuft.
    "/SC", "MINUTE", "/MO", "5",
    "/RU", "SYSTEM",
    "/RL", "HIGHEST",
    "/F",
], capture_output=True, text=True)

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
    ["powershell.exe", "-NoProfile", "-NonInteractive",
     "-ExecutionPolicy", "Bypass", "-Command", FIX_REPETITION],
    capture_output=True, text=True,
)
if chk.returncode != 0:
    # Kein Abbruch: der Agent laeuft auch ohne Wiederholung, ihm fehlt nur
    # das Sicherheitsnetz. Ein Installationsabbruch waere hier schlimmer.
    sys.stderr.write("Warnung: 5-Minuten-Wiederholung nicht gesetzt.\n")
    sys.stderr.write(chk.stderr)

subprocess.run(["schtasks", "/Run", "/TN", "CO37Agent"], capture_output=True)
'''

UNINSTALL_SCRIPT = r'''import subprocess
subprocess.run(["schtasks", "/End", "/TN", "CO37Agent"], capture_output=True)
subprocess.run(["schtasks", "/Delete", "/TN", "CO37Agent", "/F"],
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
    # Ausdruecklich Windows-Wheels anfordern. Ohne --platform wuerde pip die
    # Pakete fuer das Bausystem (Linux) holen, die unter Windows nicht laufen.
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet",
         "--target", str(site_dir),
         "--platform", "win_amd64",
         "--python-version", ".".join(PY_VERSION.split(".")[:2]),
         "--only-binary=:all:",
         # cryptography fuer die Signaturpruefung der Selbstaktualisierung
         # (F-07). Unter Linux kommt sie aus python3-cryptography; im
         # Embeddable gibt es kein pip, also muss sie hier mit hinein.
         "requests", "urllib3", "certifi", "charset-normalizer", "idna",
         "cryptography"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise SystemExit(f"pip fehlgeschlagen: {res.stderr[-600:]}")

    shutil.copy(AGENT_PY, work / "agent.py")
    # Muss neben agent.py liegen - der Agent sucht ihn dort.
    if RELEASE_KEY.is_file():
        shutil.copy(RELEASE_KEY, work / "release_key.pub")
    else:
        print("Hinweis: kein backend/release_key.pub - der Agent aus diesem "
              "Paket prueft seine Selbstaktualisierung nicht.")
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
           Die geplante Aufgabe wird dabei von deren RemoveTask geloescht
           und anschliessend von RegisterTask neu angelegt. -->
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
      <Custom Action="RemoveTask" Sequence="3499">REMOVE="ALL"</Custom>
    </InstallExecuteSequence>
  </Product>
</Wix>
'''
    wxs_path = work.parent / "co37-agent.wxs"
    wxs_path.write_text(wxs, encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"co37-agent-{version}.msi"

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
    "install_task.py",
    "agent.py",
)


# Wo die eigenen Aktionen im Ablauf stehen muessen. Siehe die Begruendung
# an der InstallExecuteSequence weiter oben.
ERWARTETE_SEQUENZ = {"SetPyExe": 1401, "RemoveTask": 3499, "RegisterTask": 4001}


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
    ap.add_argument("--out", default=str(HERE.parent / "data" / "packages"))
    args = ap.parse_args()

    path = build(args.version, Path(args.out))
    print(f"{path}  ({path.stat().st_size / 1024 / 1024:.1f} MB)")
