#!/usr/bin/env python3
"""
CO-37 Agent
Laeuft als Dienst (Windows) bzw. systemd-Unit (Linux).
Kommunikation ausschliesslich ausgehend per HTTPS-Polling.

Konfiguration: agent.conf im gleichen Verzeichnis oder /etc/co37/agent.conf
    server = https://patch.example.de
    token  = <agent-token>

Protokoll: agent.log neben dieser Datei, mit einer Vorgaengerfassung
agent.log.1. Unter Windows laeuft der Agent ohne Konsole - ohne diese
Datei ist jede Fehlermeldung unsichtbar.
"""
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import urllib3
import requests

AGENT_VERSION = "0.35.4"
IS_WINDOWS = platform.system() == "Windows"


# ======================================================================
# Protokoll
# ======================================================================
# Unter Windows laeuft der Agent als pythonw.exe - ohne Konsole. Alles,
# was auf stdout oder stderr geht, verschwindet spurlos. Ein Agent, der
# bei jedem Heartbeat scheitert, meldet das zwar brav, nur liest es
# niemand: sichtbar wurde es erst beim Start von Hand aus einer Konsole.
# Darum zusaetzlich in eine Datei neben agent.py.
LOG_FILE = Path(__file__).resolve().with_suffix(".log")
LOG_MAX_BYTES = 1_000_000


def log(message: str, err: bool = False):
    """
    Schreibt ins Protokoll und zusaetzlich auf die Konsole, falls es eine
    gibt. Darf unter keinen Umstaenden eine Ausnahme durchlassen - ein
    Agent, der am Protokollieren stirbt, waere schlimmer als einer ohne
    Protokoll.
    """
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'FEHLER ' if err else ''}{message}"
    try:
        print(line, file=sys.stderr if err else sys.stdout, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        # Umlauf ueber genau eine Vorgaengerdatei. Mehr Staende bringen
        # nichts: interessant ist immer der Zeitraum um den Fehler herum.
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            LOG_FILE.with_suffix(".log.1").unlink(missing_ok=True)
            LOG_FILE.rename(LOG_FILE.with_suffix(".log.1"))
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


# ======================================================================
# Konfiguration
# ======================================================================
def find_config() -> Path:
    for path in [
        Path(__file__).parent / "agent.conf",
        Path("/etc/co37/agent.conf"),
        Path(r"C:\ProgramData\CO37\agent.conf"),
    ]:
        if path.exists():
            return path
    log("Keine agent.conf gefunden", err=True)
    sys.exit(1)


def load_config(path: Path) -> dict:
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip()
    return cfg


CFG_PATH = find_config()
CFG = load_config(CFG_PATH)
SERVER = CFG["server"].rstrip("/")
TOKEN = CFG.get("token", "").strip()
VERIFY = CFG.get("verify_ssl", "true").lower() != "false"
if not VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {"Content-Type": "application/json"}


def clear_token():
    """
    Verwirft das eigene Token. Notwendig, wenn das Backend es nicht mehr
    kennt - etwa weil der Host dort entfernt oder die Datenbank ersetzt
    wurde. Der Agent meldet sich danach neu an.
    """
    global TOKEN
    TOKEN = ""
    try:
        lines = [
            line for line in CFG_PATH.read_text(encoding="utf-8").splitlines()
            if (line.split("=")[0].strip() if "=" in line else "") != "token"
        ]
        CFG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(CFG_PATH, 0o600)
    except OSError as exc:
        log(f"Konfiguration nicht schreibbar: {exc}", err=True)


def set_token(token: str):
    """Speichert das erhaltene Dauertoken und entfernt das Enrollment-Token."""
    global TOKEN
    TOKEN = token
    lines, seen = [], False
    for line in CFG_PATH.read_text(encoding="utf-8").splitlines():
        key = line.split("=")[0].strip() if "=" in line else ""
        if key == "token":
            lines.append(f"token = {token}")
            seen = True
        else:
            lines.append(line)
    if not seen:
        lines.append(f"token = {token}")
    CFG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(CFG_PATH, 0o600)
    except OSError:
        pass


def api(method: str, path: str, payload=None, params=None, timeout=120, auth=True):
    url = f"{SERVER}{path}"
    headers = dict(HEADERS)
    if auth:
        headers["X-Agent-Token"] = TOKEN
    resp = requests.request(
        method, url, json=payload, params=params,
        headers=headers, verify=VERIFY, timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def run(cmd: list[str], timeout=3600) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "Zeitueberschreitung"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


class LogSink:
    """
    Sammelt Ausgabezeilen und schickt sie gebuendelt ans Backend.

    Nicht jede Zeile einzeln: bei 'apt upgrade' waeren das hunderte Anfragen.
    Gebuendelt alle FLUSH_SECONDS oder ab FLUSH_LINES Zeilen.
    """
    FLUSH_SECONDS = 1.5
    FLUSH_LINES = 40

    def __init__(self, job_id: int):
        self.job_id = job_id
        self.buffer: list[str] = []
        self.last_flush = time.monotonic()
        self.stopped = False        # Obergrenze erreicht
        self.local: list[str] = []  # vollstaendige Kopie fuer den Abschlussbericht

    def write(self, line: str, progress: str = None):
        line = line.rstrip("\n")
        self.local.append(line)
        if len(self.local) > 4000:
            del self.local[:1000]
        if self.stopped:
            return
        self.buffer.append(line)
        if (len(self.buffer) >= self.FLUSH_LINES
                or time.monotonic() - self.last_flush >= self.FLUSH_SECONDS
                or progress):
            self.flush(progress)

    def flush(self, progress: str = None):
        if self.stopped or (not self.buffer and not progress):
            return
        text = "\n".join(self.buffer)
        self.buffer.clear()
        self.last_flush = time.monotonic()
        try:
            res = api("POST", "/api/v1/agent/job-log", {
                "job_id": self.job_id, "text": text, "progress": progress,
            }, timeout=30)
            if res.get("truncated"):
                self.stopped = True
        except Exception:  # noqa: BLE001
            # Protokollierung darf den Auftrag nie zum Scheitern bringen
            pass

    def tail(self, limit: int = 40000) -> str:
        return "\n".join(self.local)[-limit:]


def run_streaming(cmd: list[str], sink: LogSink, timeout=7200,
                  progress_prefix: str = None) -> int:
    """
    Fuehrt einen Befehl aus und liest die Ausgabe zeilenweise mit, statt bis
    zum Ende zu sammeln. Nur so entsteht eine laufende Ausgabe.
    """
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        sink.write(f"Start fehlgeschlagen: {exc}")
        return 1

    deadline = time.monotonic() + timeout
    count = 0
    try:
        for line in proc.stdout:
            count += 1
            if progress_prefix and count % 20 == 0:
                sink.write(line, progress=f"{progress_prefix} ({count} Zeilen)")
            else:
                sink.write(line)
            if time.monotonic() > deadline:
                proc.kill()
                sink.write("Zeitueberschreitung - Vorgang abgebrochen")
                return 124
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        sink.flush()
    return proc.returncode if proc.returncode is not None else 1


# powershell.exe schreibt in der Konsolen-Codepage, unter deutschem
# Windows meist cp850. Python dekodiert die Ausgabe als UTF-8, wodurch
# jeder Umlaut als Ersatzzeichen ankam. Statt beim Dekodieren zu raten,
# wird die Ausgabekodierung im Skript festgelegt.
PS_UTF8 = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
    "$OutputEncoding = [Text.Encoding]::UTF8\n"
)


def powershell_streaming(script: str, sink: LogSink, timeout=7200,
                         progress_prefix: str = None) -> int:
    return run_streaming(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", PS_UTF8 + script],
        sink, timeout=timeout, progress_prefix=progress_prefix,
    )


def powershell(script: str, timeout=3600) -> tuple[int, str]:
    return run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", PS_UTF8 + script],
        timeout=timeout,
    )


# ======================================================================
# Reboot-Erkennung
# ======================================================================
PS_REBOOT_CHECK = r"""
$reasons = @()
$paths = @{
  'windows_update' = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired';
  'cbs_pending'    = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending';
  'cbs_inprogress' = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress';
  'cbs_packages'   = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending'
}
foreach ($k in $paths.Keys) { if (Test-Path $paths[$k]) { $reasons += $k } }

$sm = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
$pfro = (Get-ItemProperty -Path $sm -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
if ($pfro.PendingFileRenameOperations) { $reasons += 'pending_rename' }

$cn = 'HKLM:\SYSTEM\CurrentControlSet\Services\Netlogon'
if ((Test-Path "$cn\JoinDomain") -or (Test-Path "$cn\AvoidSpnSet")) { $reasons += 'netlogon' }

# Als letzte Zeile, damit die Auswertung nicht von Warnungen gestoert wird
"REASONS=" + ($reasons -join ',')
"""

# Gruende, die einen Neustart nur anzeigen, ihn aber nicht rechtfertigen.
#
# PendingFileRenameOperations wird von allem Moeglichen gefuellt - OneDrive
# und der Edge-Updater tragen dort staendig alte DLLs zum Aufraeumen ein,
# voellig unabhaengig von Windows Update. Wer darauf automatisch neu
# startet, startet Server wegen Fremdsoftware neu, ohne dass je ein Update
# dahintersteckt. Fuer die Anzeige bleibt der Grund sichtbar.
WEAK_REBOOT_REASONS = {"pending_rename"}


def own_version() -> str:
    """
    Liest AGENT_VERSION aus der eigenen Datei.

    Notwendig, weil bei einer Aktualisierung ohne Neustart die Datei bereits
    die neue Nummer traegt, der laufende Prozess aber noch die alte Konstante
    im Speicher haelt. Ohne das meldete der Agent dauerhaft die alte Version
    und gaelte faelschlich als veraltet.
    """
    try:
        for line in Path(__file__).resolve().read_text(encoding="utf-8").splitlines():
            if line.startswith("AGENT_VERSION"):
                return line.split("=")[1].strip().strip('"\'')
    except Exception:  # noqa: BLE001
        pass
    return AGENT_VERSION


def _which(tool: str) -> bool:
    return shutil.which(tool) is not None


def is_proxmox() -> bool:
    """
    Erkennt Proxmox VE eindeutig - keine Heuristik ueber Hostnamen o.ae.

    pveversion liegt auf jedem PVE-Knoten. Der dpkg-Test faengt den Fall ab,
    dass PATH beim Dienststart eingeschraenkt ist.
    """
    if IS_WINDOWS:
        return False
    if Path("/usr/bin/pveversion").exists():
        return True
    if not _which("dpkg-query"):
        return False
    code, out = run(
        ["dpkg-query", "-W", "-f=${db:Status-Status}", "proxmox-ve"], timeout=60
    )
    # Exakter Vergleich: 'not-installed' enthaelt 'installed' als Teilkette.
    return code == 0 and out.strip() == "installed"


def apt_upgrade_verb() -> str:
    """
    'upgrade' fuer normales Debian, 'dist-upgrade' fuer Proxmox VE.

    Proxmox verlangt dist-upgrade. Mit upgrade bleiben Kernel und alle
    Pakete mit geaenderten Abhaengigkeiten zurueckgehalten: der Scan meldet
    sie weiter, der Patchlauf installiert sie nie. Auf normalem Debian
    bleibt es bei upgrade - dist-upgrade darf Pakete entfernen, und mit
    -y geschieht das ohne Rueckfrage.
    """
    return "dist-upgrade" if is_proxmox() else "upgrade"


# Klartext fuer die Oberflaeche und die Auftragsausgabe
REBOOT_REASON_TEXT = {
    "windows_update": "Windows Update",
    "cbs_pending": "Komponentenspeicher (Paket ausstehend)",
    "cbs_inprogress": "Komponentenspeicher (Neustart laeuft)",
    "cbs_packages": "Komponentenspeicher (Pakete vorgemerkt)",
    "pending_rename": "Dateien zum Aufraeumen vorgemerkt",
    "netlogon": "Domaenenbeitritt",
    "package_manager": "Paketverwaltung",
    "kernel": "neuer Kernel installiert",
}


def reason_text(reasons) -> str:
    if not reasons:
        return "kein Grund"
    return ", ".join(REBOOT_REASON_TEXT.get(r, r) for r in reasons)


def reboot_reasons() -> list[str]:
    """
    Ermittelt, ob ein Neustart aussteht - und warum.

    Der Grund ist wichtiger, als es zuerst aussieht: nicht jeder Treffer
    ist gleich viel wert. Ein Eintrag im CBS-Zweig heisst, dass Windows
    selbst einen Neustart braucht. PendingFileRenameOperations heisst oft
    nur, dass OneDrive eine DLL zum Aufraeumen vorgemerkt hat.

    Kein Rateschluss: fehlt ein Pruefwerkzeug, wird es uebersprungen statt
    als Treffer gewertet.
    """
    if IS_WINDOWS:
        code, out = powershell(PS_REBOOT_CHECK, timeout=120)
        if code != 0:
            return []
        # Gezielt die Marke suchen statt die letzte Zeile zu nehmen:
        # run() haengt stderr an stdout, eine Warnung koennte hinten stehen.
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("REASONS="):
                raw = line.split("=", 1)[1].strip()
                return [r for r in raw.split(",") if r]
        return []

    # Debian / Ubuntu
    if Path("/var/run/reboot-required").exists():
        return ["package_manager"]

    # RHEL / Rocky / Alma - nur wenn das Werkzeug wirklich vorhanden ist.
    # Exit 1 = Neustart noetig, 0 = nicht noetig.
    if _which("needs-restarting"):
        code, _ = run(["needs-restarting", "-r"], timeout=180)
        if code == 1:
            return ["package_manager"]
        if code == 0:
            return []

    return _kernel_mismatch_reason()


def reboot_required_strong() -> bool:
    """
    Wahr nur bei Gruenden, die einen Neustart tatsaechlich rechtfertigen.
    Massgeblich fuer automatische Neustarts und fuer alles, was
    host.reboot_required im Backend schreibt.

    Es gab hier einmal ein Gegenstueck reboot_required(), das jeden Grund
    zaehlte - auch einen schwachen. Es ist entfernt worden, nachdem die
    Job-Rueckmeldung versehentlich danach gegriffen hatte: der
    naheliegendere Name war der falsche. Wer einen Neustartbedarf ohne
    diese Filterung braucht, soll reboot_reasons() nehmen und sichtbar
    selbst entscheiden, was er damit tut.
    """
    return bool(set(reboot_reasons()) - WEAK_REBOOT_REASONS)


def _kernel_mismatch_reason() -> list[str]:
    # Arch / openSUSE und Fallback: laufenden Kernel gegen den
    # installierten vergleichen.
    if _which("zypper"):
        code, _ = run(["zypper", "ps", "-s"], timeout=180)

    code, running = run(["uname", "-r"], timeout=30)
    if code != 0:
        return []
    running = running.strip()

    newest = ""
    boot = Path("/boot")
    if boot.is_dir():
        versions = []
        for entry in boot.glob("vmlinuz-*"):
            versions.append(entry.name.replace("vmlinuz-", ""))
        if versions:
            # Debian-Versionsvergleich, wo verfuegbar
            if _which("dpkg"):
                newest = versions[0]
                for cand in versions[1:]:
                    code, _ = run(["dpkg", "--compare-versions", cand, "gt", newest], timeout=15)
                    if code == 0:
                        newest = cand
            else:
                newest = sorted(versions)[-1]

    if newest and running and newest != running:
        return ["kernel"]

    return []


# ======================================================================
# Update-Scan
# ======================================================================
PS_SCAN = r"""
$ErrorActionPreference = 'Stop'
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
$list = @()
foreach ($u in $result.Updates) {
  $kb = ($u.KBArticleIDs | Select-Object -First 1)
  $sec = $false
  foreach ($c in $u.Categories) { if ($c.Name -match 'Security') { $sec = $true } }
  $list += [pscustomobject]@{
    id             = if ($kb) { "KB$kb" } else { $u.Identity.UpdateID }
    title          = $u.Title
    new_version    = $null
    is_security    = $sec
    requires_reboot= [bool]$u.RebootRequired
    size_bytes     = [int64]$u.MaxDownloadSize
  }
}
$list | ConvertTo-Json -Depth 4 -Compress
"""


def scan_windows() -> list[dict]:
    code, out = powershell(PS_SCAN, timeout=1800)
    out = out.strip()
    if code != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


def scan_linux() -> list[dict]:
    updates = []

    if Path("/usr/bin/apt-get").exists():
        run(["apt-get", "update", "-qq"], timeout=600)
        # Muss denselben Modus simulieren, den patch_linux spaeter ausfuehrt.
        # Sonst zeigt der Scan Pakete an, die der Patchlauf nicht anfasst.
        verb = apt_upgrade_verb()
        code, out = run(
            ["sh", "-c",
             f"apt-get -s -o Debug::NoLocking=true {verb} | grep '^Inst '"],
            timeout=600,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[1]
            new_version = parts[2].strip("()[]")
            is_sec = "security" in line.lower()
            updates.append({
                "id": name,
                "title": name,
                "new_version": new_version,
                "is_security": is_sec,
                "requires_reboot": name.startswith((
                    "linux-image", "linux-generic",
                    "proxmox-kernel", "pve-kernel",
                )),
            })
        return updates

    if Path("/usr/bin/dnf").exists() or Path("/usr/bin/yum").exists():
        mgr = "dnf" if Path("/usr/bin/dnf").exists() else "yum"
        code, out = run([mgr, "-q", "check-update"], timeout=900)
        sec_code, sec_out = run(
            [mgr, "-q", "check-update", "--security"], timeout=900
        )
        sec_names = {l.split()[0] for l in sec_out.splitlines() if len(l.split()) >= 3}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3 or line.startswith(("Last metadata", "Obsoleting")):
                continue
            name = parts[0]
            updates.append({
                "id": name,
                "title": name,
                "new_version": parts[1],
                "is_security": name in sec_names,
                "requires_reboot": name.startswith("kernel"),
            })
        return updates

    return updates


def do_scan(job_id=None) -> dict:
    updates = scan_windows() if IS_WINDOWS else scan_linux()
    reasons = reboot_reasons()
    # Nur belastbare Gruende zaehlen als Neustartbedarf. Dateireste, die
    # OneDrive oder der Edge-Updater vormerken, sind kein Grund, einen
    # Server neu zu starten - sie wuerden den Host sonst dauerhaft als
    # neustartbeduerftig anzeigen, ohne dass es je etwas zu tun gibt.
    needs_reboot = bool(set(reasons) - WEAK_REBOOT_REASONS)
    api("POST", "/api/v1/agent/scan-result", {
        "job_id": job_id,
        "reboot_required": needs_reboot,
        "reboot_reasons": reasons,
        "updates": updates,
    })
    return {"found": len(updates), "reboot_required": needs_reboot}


# ======================================================================
# Patch-Installation
# ======================================================================
# Die Windows Update COM API kennt keinen Datenstrom - Download und
# Installation sind blockierende Aufrufe. Deshalb werden die Updates einzeln
# durchlaufen, damit zwischen den Schritten berichtet werden kann. Das ergibt
# "Installiere 3 von 12" statt laufender Prozentwerte.
PS_INSTALL = r"""
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()

Write-Output "Suche verfuegbare Updates..."
$result = $searcher.Search("IsInstalled=0 and IsHidden=0")
$total = $result.Updates.Count
Write-Output "$total Update(s) gefunden"
if ($total -eq 0) { Write-Output 'KEINE_UPDATES'; exit 0 }

$toInstall = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $result.Updates) {
  if (-not $u.EulaAccepted) { $u.AcceptEula() | Out-Null }
  $toInstall.Add($u) | Out-Null
  Write-Output "  - $($u.Title)"
}

Write-Output ""
Write-Output "Lade $total Update(s) herunter..."
$downloader = $session.CreateUpdateDownloader()
$downloader.Updates = $toInstall
$dl = $downloader.Download()
Write-Output "DOWNLOAD_RESULT=$($dl.ResultCode)"

Write-Output ""
$rebootNeeded = $false
$failed = 0
for ($i = 0; $i -lt $toInstall.Count; $i++) {
  $u = $toInstall.Item($i)
  $n = $i + 1
  Write-Output "FORTSCHRITT|Installiere $n von $total"
  Write-Output "[$n/$total] $($u.Title)"

  $single = New-Object -ComObject Microsoft.Update.UpdateColl
  $single.Add($u) | Out-Null
  $installer = $session.CreateUpdateInstaller()
  $installer.Updates = $single
  try {
    $r = $installer.Install()
    Write-Output "      Ergebnis: $($r.ResultCode)  (2 = erfolgreich)"
    if ($r.RebootRequired) { $rebootNeeded = $true }
    if ($r.ResultCode -ne 2) { $failed++ }
  } catch {
    Write-Output "      FEHLER: $($_.Exception.Message)"
    $failed++
  }
}

Write-Output ""
Write-Output "INSTALL_RESULT=$(if ($failed -eq 0) { 2 } else { 4 })"
Write-Output "FEHLGESCHLAGEN=$failed von $total"
Write-Output "REBOOT_REQUIRED=$rebootNeeded"
"""


def patch_windows(sink: LogSink) -> bool:
    code = powershell_streaming(PS_INSTALL, sink, timeout=7200,
                                progress_prefix="Windows-Updates")
    # Fortschrittsmarken auswerten und als Kurztext weitergeben
    for line in sink.local[-200:]:
        if line.startswith("FORTSCHRITT|"):
            sink.flush(progress=line.split("|", 1)[1])
    tail = "\n".join(sink.local[-60:])
    if code != 0:
        return False
    # Nichts zu tun ist kein Fehlschlag. Das Skript steigt bei leerer
    # Trefferliste vorzeitig aus und schreibt daher kein INSTALL_RESULT.
    # Ohne diesen Zweig galt der Lauf als fehlgeschlagen, der Nachlauf-Scan
    # unterblieb und die Oberflaeche zeigte weiter die alten Zahlen.
    if "KEINE_UPDATES" in tail:
        return True
    return "INSTALL_RESULT=2" in tail


def patch_linux(sink: LogSink) -> bool:
    if Path("/usr/bin/apt-get").exists():
        sink.write("Aktualisiere Paketlisten...", progress="Paketlisten")
        run_streaming(["sh", "-c", "DEBIAN_FRONTEND=noninteractive apt-get update"],
                      sink, timeout=900)
        sink.write("")
        verb = apt_upgrade_verb()
        if verb == "dist-upgrade":
            sink.write("Proxmox VE erkannt - verwende apt-get dist-upgrade")
        sink.write("Installiere Aktualisierungen...", progress="Installiere Pakete")
        code = run_streaming(
            ["sh", "-c",
             "DEBIAN_FRONTEND=noninteractive apt-get -y "
             "-o Dpkg::Options::=--force-confdef "
             f"-o Dpkg::Options::=--force-confold {verb}"],
            sink, timeout=7200, progress_prefix="Installiere Pakete",
        )
        return code == 0

    if Path("/usr/bin/dnf").exists():
        sink.write("Installiere Aktualisierungen (dnf)...", progress="Installiere Pakete")
        return run_streaming(["dnf", "-y", "upgrade"], sink, timeout=7200,
                             progress_prefix="Installiere Pakete") == 0

    if Path("/usr/bin/yum").exists():
        sink.write("Installiere Aktualisierungen (yum)...", progress="Installiere Pakete")
        return run_streaming(["yum", "-y", "update"], sink, timeout=7200,
                             progress_prefix="Installiere Pakete") == 0

    sink.write("Kein unterstuetzter Paketmanager gefunden")
    return False


# Sobald ein Neustart angestossen ist, werden keine Auftraege mehr
# angenommen. Zwischen dem Absetzen von shutdown und dem tatsaechlichen
# Herunterfahren liegen je nach Verzoegerung Sekunden bis Minuten - in
# denen der Agent weiter fragt und einen Auftrag annehmen wuerde, den er
# nicht zu Ende bringen kann. Genau so ist ein Patch-Lauf mitten in der
# Installation abgeschnitten worden und blieb auf 'laeuft' stehen.
REBOOT_PENDING = False


def trigger_reboot(delay_seconds: int = 30):
    global REBOOT_PENDING
    REBOOT_PENDING = True
    if IS_WINDOWS:
        run(["shutdown.exe", "/r", "/t", str(delay_seconds),
             "/c", "CO-37: Neustart nach Update", "/d", "p:2:17"])
    else:
        minutes = max(1, delay_seconds // 60)
        run(["shutdown", "-r", f"+{minutes}", "CO-37: Neustart nach Update"])


_BOOT_TIME = None


def boot_time() -> float:
    """
    Zeitpunkt des letzten Systemstarts als Unix-Zeit.

    Einmal ermittelt und gemerkt. Der Wert aendert sich waehrend der
    Laufzeit des Prozesses nicht, und auf Windows kostet die Abfrage einen
    PowerShell-Start - bei einem Heartbeat alle fuenf bis fuenfzehn
    Sekunden waere das jedes Mal ein eigener Prozess.

    Das Backend verwirft damit Auftraege, die vor dem letzten Start
    begonnen haben - der Agent selbst hat kein Gedaechtnis ueber einen
    Neustart hinweg. Ohne diese Angabe bleibt ein abgeschnittener Auftrag
    bis zum Ablauf der Hoechstlaufzeit stehen, bei einem Patch-Auftrag
    also vier Stunden, und blockiert solange jeden weiteren.

    Liefert 0.0, wenn sich der Wert nicht ermitteln laesst. Das Backend
    wertet 0.0 als 'unbekannt' und laesst den Auftrag in Ruhe - lieber
    keine Aufraeumung als eine auf falscher Grundlage.
    """
    global _BOOT_TIME
    if _BOOT_TIME is not None:
        return _BOOT_TIME

    _BOOT_TIME = 0.0
    if not IS_WINDOWS:
        try:
            # /proc/uptime ist auf Linux immer da und braucht kein Zusatzpaket.
            with open("/proc/uptime", encoding="ascii") as fh:
                _BOOT_TIME = time.time() - float(fh.read().split()[0])
        except (OSError, ValueError, IndexError):
            pass
        return _BOOT_TIME

    # Ein einziger Ausdruck ohne Variablen. Mit $-Variablen haengt der
    # Befehl davon ab, wer ihn uebergibt: wird er aus einer PowerShell
    # heraus in doppelten Anfuehrungszeichen weitergereicht, ersetzt die
    # aeussere Shell sie vorher durch Leerzeichenketten.
    #
    # Kein Cast ueber [datetimeoffset](...): der wuerde sich auf das
    # CimInstance-Objekt beziehen statt auf die Eigenschaft. Der
    # Epoch-Zeitpunkt wird konstruiert statt aus einer Zeichenkette
    # gelesen, damit die Landeseinstellung keine Rolle spielt.
    code, out = run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "[int64]((((Get-CimInstance Win32_OperatingSystem)"
        ".LastBootUpTime.ToUniversalTime()) - "
        "(New-Object DateTime 1970,1,1,0,0,0,([DateTimeKind]::Utc)))"
        ".TotalSeconds)",
    ], timeout=120)
    if code == 0:
        try:
            _BOOT_TIME = float(out.strip())
        except ValueError:
            pass
    return _BOOT_TIME


# ======================================================================
# Job-Verarbeitung
# ======================================================================
def handle_job(job: dict):
    job_id = job["id"]
    job_type = job["type"]
    params = job.get("params", {})
    sink = LogSink(job_id)

    def report(state, error=None, result=None, reboot=None):
        """
        Meldet den Auftragszustand zurueck.

        reboot: bereits ermittelter Neustartbedarf nach der strengen Regel.
        None heisst: selbst ermitteln.

        Die strenge Regel ist Pflicht, nicht Geschmack. Heartbeat,
        Scan-Ergebnis und diese Rueckmeldung schreiben im Backend
        dasselbe Feld host.reboot_required, und es gewinnt der letzte
        Schreiber. Solange hier die laxe Regel stand, ueberschrieb die
        Schlussmeldung eines Patchlaufs den soeben korrekt gemeldeten
        Zustand mit einem schwachen Grund: die Oberflaeche sprang auf
        "Neustart noetig", bis der naechste Heartbeat es zuruecknahm.
        Sichtbar als kurzes Rotwerden nach jedem Update ohne
        Neustartbedarf, weil Windows Update praktisch immer Dateien zum
        Aufraeumen vormerkt.

        Wo der Wert im Auftrag schon feststeht, wird er durchgereicht
        statt neu erhoben. Ein zweiter Registry-Lauf kostet nicht nur
        Zeit, sein Ergebnis koennte auch von dem abweichen, was der
        Auftrag eine Zeile darueber ins Protokoll geschrieben hat.
        """
        sink.flush()
        api("POST", "/api/v1/agent/report", {
            "job_id": job_id,
            "state": state,
            "log": sink.tail(),
            "error": error,
            "result": result or {},
            "reboot_required": (
                reboot_required_strong() if reboot is None else bool(reboot)),
        })

    try:
        sink.write(f"=== {job_type} auf {socket.gethostname()} ===",
                   progress="Startet")

        # ------------------------------------------------------------------
        if job_type == "scan":
            sink.write("Suche nach verfuegbaren Updates...", progress="Suche Updates")
            updates = scan_windows() if IS_WINDOWS else scan_linux()
            scan_reasons = reboot_reasons()
            needs_reboot = bool(set(scan_reasons) - WEAK_REBOOT_REASONS)

            sink.write(f"{len(updates)} Update(s) gefunden")
            sec = sum(1 for u in updates if u.get("is_security"))
            if sec:
                sink.write(f"davon {sec} sicherheitsrelevant")
            for u in updates[:200]:
                mark = "[SIC]" if u.get("is_security") else "     "
                sink.write(f"  {mark} {u.get('id')}  {u.get('new_version') or ''}")
            if len(updates) > 200:
                sink.write(f"  ... und {len(updates)-200} weitere")
            strong_scan = [r for r in scan_reasons if r not in WEAK_REBOOT_REASONS]
            # Zwei verschiedene Aussagen, frueher in einer Zeile vermengt:
            # ob gerade ein Neustart aussteht, und ob die gefundenen
            # Updates einen nach sich ziehen. Bei Kernel-Paketen stand
            # dort "Neustart erforderlich: nein", weil vor der
            # Installation tatsaechlich noch keiner ausstand.
            sink.write(f"Neustart steht aus: {'ja' if needs_reboot else 'nein'}"
                       + (f" ({reason_text(strong_scan)})" if strong_scan else ""))

            reboot_pkgs = [u for u in updates if u.get("requires_reboot")]
            if reboot_pkgs:
                names = ", ".join(str(u.get("id")) for u in reboot_pkgs[:5])
                if len(reboot_pkgs) > 5:
                    names += f" und {len(reboot_pkgs) - 5} weitere"
                sink.write(f"Anstehende Updates erfordern Neustart: ja "
                           f"({len(reboot_pkgs)}: {names})")
            else:
                sink.write("Anstehende Updates erfordern Neustart: nein")

            api("POST", "/api/v1/agent/scan-result", {
                "job_id": job_id, "reboot_required": needs_reboot,
                "reboot_reasons": scan_reasons, "updates": updates,
            })
            report("done", result={"found": len(updates), "reboot_required": needs_reboot},
                   reboot=needs_reboot)
            return

        # ------------------------------------------------------------------
        if job_type == "patch":
            ok = patch_windows(sink) if IS_WINDOWS else patch_linux(sink)

            def rescan():
                """
                Nachlauf-Scan, damit das Portal aktuelle Zahlen zeigt.
                Laeuft auch nach einem Fehlschlag: sonst bleiben dort
                Zahlen stehen, die nichts mehr mit der Lage zu tun haben.
                """
                sink.write("Ermittle verbleibende Updates...",
                           progress="Nachlauf-Suche")
                try:
                    res = do_scan(job_id)
                    sink.write(f"noch offen: {res['found']}")
                except Exception as exc:  # noqa: BLE001
                    sink.write(f"Nachlauf-Suche fehlgeschlagen: {exc}")

            if not ok:
                sink.write("", progress="Fehlgeschlagen")
                sink.write("Installation fehlgeschlagen.")
                rescan()
                report("failed", error="Installation fehlgeschlagen")
                return

            sink.write("")
            sink.write("Installation abgeschlossen.", progress="Pruefe Neustartbedarf")

            reasons = reboot_reasons()
            strong = [r for r in reasons if r not in WEAK_REBOOT_REASONS]
            needs_reboot = bool(strong)
            if strong:
                sink.write(f"Neustart angezeigt durch: {reason_text(strong)}")
            weak = [r for r in reasons if r in WEAK_REBOOT_REASONS]
            if weak and not strong:
                # Kein Grund, dafuer einen Server neu zu starten. Tritt
                # regelmaessig auf, weil OneDrive und der Edge-Updater
                # staendig Dateien zum Aufraeumen vormerken.
                sink.write(f"Nachrangig, kein Neustartbedarf: {reason_text(weak)}")
            # Gleiche Formulierung wie im Scan-Bericht. Nach der
            # Installation beantwortet reboot_reasons() genau diese Frage.
            sink.write(f"Neustart steht aus: {'ja' if needs_reboot else 'nein'}")

            rescan()

            if not needs_reboot:
                sink.write("Fertig.", progress="Fertig")
                report("done", result={"rebooting": False, "reboot_required": False},
                       reboot=False)
                return

            sink.write("", progress="Fordere Freigabe an")
            sink.write("Neustart steht an, fordere Freigabe beim Server an...")
            decision = api("POST", "/api/v1/agent/pre-reboot",
                           params={"job_id": job_id}, timeout=90)

            if not decision.get("approved"):
                sink.write(f"Keine Freigabe: {decision.get('reason')}",
                           progress="Neustart nicht freigegeben")
                sink.write("Host bleibt als neustartbeduerftig markiert.")
                report("done", result={
                    "rebooting": False, "reboot_required": True,
                    "blocked_reason": decision.get("reason"),
                }, reboot=True)
                return

            if decision.get("downtime_set"):
                hosts = ", ".join(decision.get("hosts", []))
                sink.write(f"Checkmk-Downtime gesetzt ({decision.get('minutes')} min)"
                           + (f" auf: {hosts}" if hosts else ""))
            else:
                sink.write(f"Ohne Downtime freigegeben: {decision.get('reason')}")

            delay = params.get("reboot_delay", 30)
            sink.write(f"Neustart in {delay} Sekunden.", progress="Startet neu")
            report("done", result={"rebooting": True, "reboot_required": True},
                   reboot=True)
            time.sleep(3)
            trigger_reboot(delay)
            return

        # ------------------------------------------------------------------
        if job_type == "reboot":
            sink.write("Neustart angefordert.", progress="Fordere Freigabe an")
            decision = api("POST", "/api/v1/agent/pre-reboot",
                           params={"job_id": job_id}, timeout=90)

            if not decision.get("approved"):
                sink.write(f"Keine Freigabe: {decision.get('reason')}",
                           progress="Nicht freigegeben")
                report("failed", error=decision.get("reason"))
                return

            if decision.get("downtime_set"):
                hosts = ", ".join(decision.get("hosts", []))
                sink.write(f"Checkmk-Downtime gesetzt ({decision.get('minutes')} min)"
                           + (f" auf: {hosts}" if hosts else ""))
            else:
                sink.write(f"Ohne Downtime: {decision.get('reason')}")

            delay = params.get("reboot_delay", 30)
            sink.write(f"Neustart in {delay} Sekunden.", progress="Startet neu")
            report("done", result={"rebooting": True})
            time.sleep(3)
            trigger_reboot(delay)
            return

        # ------------------------------------------------------------------
        if job_type == "selfupdate":
            sink.write("Hole aktuellen Agent-Code vom Server...",
                       progress="Lade Agent")
            ok, msg, needs_restart = self_update()
            sink.write(msg)
            if not ok:
                report("failed", error=msg)
                return

            report("done", result={"message": msg, "restarted": needs_restart})

            if needs_restart:
                sink.write("Agent startet neu.", progress="Startet neu")
                sink.flush()
                # Markierung setzen: meldet sich der Agent danach nicht
                # zurueck, wird beim naechsten Start die Sicherung eingespielt.
                mark_update_attempt(own_version())
                restart_self_detached()
                time.sleep(2)
                sys.exit(0)
            return

        # ------------------------------------------------------------------
        sink.write(f"Unbekannter Auftragstyp: {job_type}")
        report("failed", error=f"Unbekannter Auftragstyp: {job_type}")

    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        sink.write(f"Ausnahme: {exc}", progress="Fehler")
        try:
            report("failed", error=str(exc))
        except Exception:  # noqa: BLE001
            pass


# ======================================================================
# Hauptschleife
# ======================================================================
def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:  # noqa: BLE001
        return ""


def enroll() -> bool:
    """
    Freie Anmeldung beim Backend. Es wird kein Enrollment-Token verlangt -
    der Host landet im Zustand 'pending' und bekommt erst nach Freigabe im
    Dashboard Auftraege.
    """
    payload = {
        "hostname": socket.gethostname(),
        "os_type": "windows" if IS_WINDOWS else "linux",
        "os_version": platform.platform(),
        "ip_address": local_ip(),
        "agent_version": own_version(),
    }
    try:
        res = api("POST", "/api/v1/agent/enroll", payload, timeout=60, auth=False)
    except requests.HTTPError as exc:
        body = exc.response.text[:250] if exc.response is not None else ""
        log(f"Anmeldung abgelehnt: {body}", err=True)
        return False

    set_token(res["agent_token"])
    log("Angemeldet. Wartet auf Freigabe im Dashboard.")
    return True


# ======================================================================
# Selbstaktualisierung
# ======================================================================
UPDATE_MARKER = Path(__file__).resolve().with_suffix(".update-pending")


def mark_update_attempt(old_version: str):
    """Vermerkt einen Aktualisierungsversuch, bevor neu gestartet wird."""
    try:
        UPDATE_MARKER.write_text(json.dumps({
            "old_version": old_version, "at": time.time(), "starts": 0,
        }))
    except OSError:
        pass


def clear_update_marker():
    UPDATE_MARKER.unlink(missing_ok=True)


def update_marker_info() -> Optional[dict]:
    """Vermerk der letzten Aktualisierung, oder None wenn keiner liegt."""
    if not UPDATE_MARKER.exists():
        return None
    try:
        return json.loads(UPDATE_MARKER.read_text())
    except Exception:  # noqa: BLE001
        return {}


# Erst nach so vielen Starts ohne erfolgreichen Heartbeat wird
# zurueckgerollt. Der Start unmittelbar nach der Aktualisierung ist der
# Normalfall und darf nicht zaehlen.
MAX_STARTS_BEFORE_ROLLBACK = 3

# Zweiter Weg zur Selbstheilung, fuer den Fall, dass der Prozess gar nicht
# neu startet: er laeuft durch, scheitert aber bei jedem Heartbeat. Der
# Zaehler oben bleibt dann stehen, und der Agent waere unbegrenzt tot,
# ohne dass etwas anschlaegt - genau so ist eine fehlerhafte Fassung
# unbemerkt liegen geblieben.
#
# Gezaehlt werden nur *beantwortete* Fehlschlaege: der Server hat geredet
# und uns abgelehnt, das liegt also an uns. Ein nicht erreichbarer Server
# zaehlt nicht, sonst wuerde jeder Agent zurueckrollen, sobald das Backend
# fuer ein Update kurz steht.
MAX_HTTP_FAILS_BEFORE_ROLLBACK = 5
# Zusaetzlich eine Mindestdauer seit der Aktualisierung. Fuenf Fehlschlaege
# im Abstand von Sekunden koennen auch ein anlaufendes Backend sein.
MIN_SECONDS_BEFORE_HTTP_ROLLBACK = 120


def recover_failed_update():
    """
    Prueft beim Start, ob die letzte Aktualisierung durchgelaufen ist.

    Die Markierung wird vor dem Neustart gesetzt und erst nach einem
    erfolgreichen Heartbeat geloescht. Der Start unmittelbar danach findet
    sie also immer vor - das ist der Normalfall und darf nicht zum
    Rueckrollen fuehren. Frueher wurde genau hier bedingungslos die .bak
    eingespielt, wodurch sich jedes Update selbst zurueckgenommen hat:
    hochheben, neu starten, zurueckfallen, von vorn. Nach aussen sah das
    aus wie ein erfolgreicher Auftrag bei unveraenderter Version.

    Stattdessen wird bei jedem Start ohne zwischenzeitlichen Heartbeat
    hochgezaehlt. Erst ab MAX_STARTS_BEFORE_ROLLBACK greift die
    Selbstheilung - dann laeuft der neue Code tatsaechlich nicht.
    """
    if not UPDATE_MARKER.exists():
        return

    try:
        info = json.loads(UPDATE_MARKER.read_text())
    except Exception:  # noqa: BLE001
        info = {}

    starts = int(info.get("starts", 0)) + 1
    if starts < MAX_STARTS_BEFORE_ROLLBACK:
        info["starts"] = starts
        try:
            UPDATE_MARKER.write_text(json.dumps(info))
        except OSError:
            pass
        log(f"Start {starts} nach der Aktualisierung, noch keine "
              f"Rueckmeldung. Rueckrollen ab Start "
              f"{MAX_STARTS_BEFORE_ROLLBACK}.", err=True)
        return

    roll_back_update(f"nach {starts} Starts keine Rueckmeldung", info)


# Merkt sich die Fassung, die gerade zurueckgenommen wurde. Ohne das holt
# der Agent sie beim naechsten Selbstaktualisierungs-Auftrag sofort wieder
# und scheitert erneut - hoch, zurueck, hoch, endlos.
REJECTED_MARKER = Path(__file__).resolve().with_suffix(".rejected-version")


def rejected_version() -> Optional[str]:
    try:
        return json.loads(REJECTED_MARKER.read_text()).get("version") or None
    except Exception:  # noqa: BLE001
        return None


def roll_back_update(reason: str, info: dict) -> bool:
    """
    Spielt die Sicherung zurueck und startet neu. Liefert False, wenn
    nichts zurueckgenommen werden konnte - dann laeuft der Agent weiter,
    das ist besser als gar keiner.
    """
    clear_update_marker()

    own = Path(__file__).resolve()
    backup = own.with_suffix(".py.bak")
    if not backup.is_file():
        log(f"Aktualisierung fehlerhaft ({reason}), aber keine Sicherung "
            f"vorhanden. Laufe mit der aktuellen Fassung weiter.", err=True)
        return False

    old = info.get("old_version", "?")
    broken = own_version()
    log(f"Aktualisierung fehlerhaft ({reason}, vorher {old}, jetzt "
        f"{broken}). Spiele die Sicherung zurueck.", err=True)
    try:
        shutil.copy2(backup, own)
    except OSError as exc:
        log(f"Ruecknahme fehlgeschlagen: {exc}", err=True)
        return False

    # Die verworfene Fassung vormerken, damit sie nicht sofort wieder
    # eingespielt wird.
    try:
        REJECTED_MARKER.write_text(json.dumps(
            {"version": broken, "at": time.time(), "reason": reason}))
    except OSError:
        pass

    # Ins Portal melden, sonst steht der Rueckfall nur auf dem Zielsystem
    # und die Oberflaeche zeigt weiter erfolgreiche Auftraege bei
    # unveraenderter Version. Der Versuch darf scheitern - gerade wenn der
    # Heartbeat der Grund war, ist auch das hier nicht verlaesslich.
    try:
        api("POST", "/api/v1/agent/notice", {
            "message": (f"Agent-Update auf {broken} zurueckgerollt auf {old}: "
                        f"{reason}. Diese Fassung wird auf diesem Host nicht "
                        f"erneut eingespielt."),
            "result": {"rolled_back_to": old, "rejected": broken,
                       "reason": reason},
        }, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log(f"Rueckfall konnte nicht gemeldet werden: {exc}", err=True)

    restart_self_detached()
    time.sleep(2)
    sys.exit(0)


def restart_self_detached():
    """
    Startet den Agent neu, ohne dass der eigene Prozess dafuer leben muss.
    Linux: systemd startet nach dem Beenden von selbst neu.
    Windows: die geplante Aufgabe muss angestossen werden, das uebernimmt
    ein abgekoppelter Helfer.
    """
    if IS_WINDOWS:
        # Kein 'schtasks /End': der Agent beendet sich gleich selbst, und
        # /End reisst den Prozessbaum der Aufgabe ab - der Helfer haette
        # sich damit selbst umgebracht, bevor er zum /Run kommt.
        helper = (
            'Start-Sleep -Seconds 5; '
            'schtasks /Run /TN CO37Agent'
        )
        # DETACHED reicht nicht: der Aufgabenplaner raeumt beim Ende der
        # Aufgabe das gesamte Job-Objekt ab, samt schlafendem Helfer.
        # BREAKAWAY loest ihn daraus. Erlaubt das Job-Objekt kein
        # Ausbrechen, scheitert CreateProcess - dann ohne den Zusatz.
        DETACHED, NEW_GROUP, BREAKAWAY = 0x00000008, 0x00000200, 0x01000000
        cmd = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
               "-Command", helper]
        for flags in (DETACHED | NEW_GROUP | BREAKAWAY, DETACHED | NEW_GROUP):
            try:
                subprocess.Popen(cmd, creationflags=flags)
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
        log(f"Neustart des Agents fehlgeschlagen: {last}", err=True)
    else:
        try:
            subprocess.Popen(
                ["sh", "-c",
                 "sleep 3; systemctl restart co37-agent.service"],
                start_new_session=True,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"Neustart des Agents fehlgeschlagen: {exc}", err=True)


def _strip_version(code: str) -> str:
    """Blendet die AGENT_VERSION-Zeile aus, damit nur echte Codeaenderungen zaehlen."""
    return "\n".join(
        line for line in code.splitlines()
        if not line.startswith("AGENT_VERSION")
    )


def self_update() -> tuple[bool, str, bool]:
    """
    Holt den aktuellen Agent-Code und ersetzt die eigene Datei.

    Rueckgabe: (erfolgreich, Meldung, Neustart noetig)

    Hat sich ausser der Versionsnummer nichts geaendert, wird die Datei zwar
    geschrieben, aber NICHT neu gestartet. Sonst loeste jedes Systemupdate auf
    jedem Host einen Dienstneustart aus, obwohl sich am Agent nichts getan hat.
    """
    res = api("GET", "/api/v1/agent/code", timeout=120)
    new_version = res.get("version", "?")
    code = res.get("code", "")
    expected = res.get("sha256", "")

    if not code:
        return False, "Leere Antwort vom Server", False

    # Genau die Fassung, die hier zuletzt zurueckgenommen wurde, nicht
    # erneut einspielen. Sonst holt der naechste Auftrag sie sofort wieder
    # und der Host pendelt zwischen kaputt und zurueckgerollt. Der
    # Vergleich ist exakt: eine neuere Fassung wird normal angenommen.
    blocked = rejected_version()
    if blocked and blocked == new_version:
        return False, (
            f"Version {new_version} wurde auf diesem Host bereits "
            f"zurueckgerollt und wird nicht erneut eingespielt. Eine neuere "
            f"Fassung veroeffentlichen, oder auf dem Host "
            f"{REJECTED_MARKER.name} entfernen."
        ), False

    actual = hashlib.sha256(code.encode("utf-8")).hexdigest()
    if expected and actual != expected:
        return False, "Pruefsumme stimmt nicht - Aktualisierung abgebrochen", False

    own = Path(__file__).resolve()
    try:
        current = own.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Eigene Datei nicht lesbar: {exc}", False

    if current == code:
        return True, f"Bereits auf Version {new_version}", False

    # Syntaxpruefung, damit sich der Agent nicht selbst lahmlegt
    try:
        compile(code, "agent.py", "exec")
    except SyntaxError as exc:
        return False, f"Neuer Code ist fehlerhaft: {exc}", False

    only_version = _strip_version(current) == _strip_version(code)
    old_version = own_version()

    try:
        shutil.copy2(own, own.with_suffix(".py.bak"))
        tmp = own.with_suffix(".py.new")
        tmp.write_text(code, encoding="utf-8")
        tmp.replace(own)
    except OSError as exc:
        return False, f"Datei konnte nicht ersetzt werden: {exc}", False

    # Eine andere Fassung ist durchgelaufen - die alte Sperre ist damit
    # gegenstandslos.
    REJECTED_MARKER.unlink(missing_ok=True)

    if only_version:
        return (True,
                f"Auf {new_version} gehoben. Am Agent-Code hat sich nichts "
                f"geaendert, daher kein Neustart.",
                False)

    return (True,
            f"Aktualisiert von {old_version} auf {new_version}. "
            f"Der Agent-Code hat sich geaendert, Neustart folgt.",
            True)


def claim_single_instance() -> bool:
    """
    Stellt sicher, dass nur ein Agent laeuft.

    Notwendig, weil die geplante Aufgabe unter Windows alle paar Minuten
    ausgeloest wird. Das ist die Absicherung dafuer, dass der Agent nach
    einem misslungenen Selbstneustart ueberhaupt wiederkommt - ohne sie
    bliebe er bis zum naechsten Systemstart tot. Ob der Aufgabenplaner
    zusaetzliche Starts von sich aus unterdrueckt, haengt von der
    Mehrfachinstanz-Richtlinie ab; darauf wird sich hier nicht verlassen.

    Die Sperre wird beim Beenden des Prozesses vom Betriebssystem
    freigegeben, ueberlebt also auch einen Absturz.
    """
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Global\\CO37Agent")
        if not handle:
            return True   # Sperre nicht moeglich - lieber laufen als gar nicht
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        # Handle absichtlich offen halten, sonst faellt die Sperre sofort weg
        globals()["_INSTANCE_MUTEX"] = handle
        return True
    except Exception:  # noqa: BLE001
        return True


def main():
    if not claim_single_instance():
        log("Es laeuft bereits ein Agent. Dieser Start wird beendet.")
        return
    interval = 60
    recover_failed_update()
    log(f"CO-37 Agent {own_version()} - Server {SERVER}")

    # Solange kein eigenes Token vorliegt, Erstanmeldung versuchen
    while not TOKEN:
        if enroll():
            break
        log("Neuer Versuch in 60 Sekunden", err=True)
        time.sleep(60)

    announced = False
    version_notice_shown = False
    unauthorized = 0
    http_fails = 0

    while True:
        try:
            payload = {
                "hostname": socket.gethostname(),
                "os_type": "windows" if IS_WINDOWS else "linux",
                "os_version": platform.platform(),
                "ip_address": local_ip(),
                "agent_version": own_version(),
                "reboot_required": bool(
                    set(_hb_reasons := reboot_reasons()) - WEAK_REBOOT_REASONS),
                "reboot_reasons": _hb_reasons,
                "boot_time": boot_time(),
                "reboot_pending": REBOOT_PENDING,
            }
            resp = api("POST", "/api/v1/agent/heartbeat", payload, timeout=60)
            interval = resp.get("poll_interval", interval)

            server_agent = resp.get("agent_version")
            if (server_agent and server_agent not in ("unbekannt", AGENT_VERSION)
                    and not version_notice_shown):
                log(f"Hinweis: Server liefert Agent {server_agent}, "
                      f"lokal laeuft {AGENT_VERSION}. "
                      f"Aktualisierung im Dashboard ausloesbar.")
                version_notice_shown = True

            state = resp.get("approval_state")
            if state and state != "approved":
                if not announced:
                    log(f"Host ist noch nicht freigegeben (Zustand: {state}). "
                          f"Es werden keine Auftraege ausgefuehrt.")
                    announced = True
                time.sleep(interval)
                continue
            announced = False

            unauthorized = 0
            http_fails = 0
            clear_update_marker()   # Aktualisierung hat sich zurueckgemeldet

            # Nach angestossenem Neustart nur noch melden, nicht arbeiten.
            # Der Auftrag bleibt beim Backend liegen und wird nach dem
            # Start erneut ausgeliefert.
            if REBOOT_PENDING:
                if resp.get("jobs"):
                    log(f"{len(resp['jobs'])} Auftrag/Auftraege zurueckgestellt: "
                          f"Neustart steht unmittelbar bevor")
                time.sleep(interval)
                continue

            for job in resp.get("jobs", []):
                log(f"Job {job['id']} ({job['type']}) wird ausgefuehrt")
                handle_job(job)

        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0

            # 401 zaehlt nicht: das heisst, der Host wurde entfernt oder die
            # Datenbank ersetzt - kein Grund, den Agent-Code zu verdaechtigen.
            # Dafuer gibt es weiter unten die Neuanmeldung.
            marker = update_marker_info() if code != 401 else None
            if marker is not None:
                http_fails += 1
                since = time.time() - float(marker.get("at") or 0)
                log(f"Heartbeat vom Server mit {code} abgelehnt "
                    f"({http_fails}. Mal seit der Aktualisierung).", err=True)
                if (http_fails >= MAX_HTTP_FAILS_BEFORE_ROLLBACK
                        and since >= MIN_SECONDS_BEFORE_HTTP_ROLLBACK):
                    roll_back_update(
                        f"{http_fails} mal mit HTTP {code} abgelehnt, ohne "
                        f"dass der Agent je durchkam", marker)

            if code == 401:
                # Das Backend kennt unser Token nicht mehr - etwa weil der
                # Host entfernt oder die Datenbank ersetzt wurde. Ein 401 ist
                # eindeutig, daher nur ein zweiter Versuch nach kurzer Pause,
                # um einen Neustart des Backends auszuschliessen.
                unauthorized += 1
                if unauthorized >= 2:
                    log("Token wird vom Server abgelehnt. Melde neu an.", err=True)
                    clear_token()
                    unauthorized = 0
                    announced = False
                    if not enroll():
                        time.sleep(60)
                    continue
                log("Nicht autorisiert - pruefe erneut in 15 Sekunden", err=True)
                time.sleep(15)
                continue

            elif code == 409:
                # Hostname bereits vergeben, aber unser Token passt nicht.
                # Ohne Eingriff im Dashboard nicht loesbar.
                log("Hostname ist im Dashboard mit einem anderen Token "
                      "belegt. Host dort entfernen, damit sich der Agent "
                      "neu anmelden kann.", err=True)
                time.sleep(300)
                continue

            elif code in (500, 502, 503, 504):
                log(f"Server meldet {code} - vermutlich Wartung oder ein "
                      f"Fehler auf dem Server. Neuer Versuch spaeter.", err=True)
            else:
                log(f"Verbindungsfehler: {exc}", err=True)

        except requests.RequestException as exc:
            log(f"Verbindungsfehler: {exc}", err=True)
        except Exception as exc:  # noqa: BLE001
            log(f"Fehler: {exc}", err=True)

        time.sleep(interval)


if __name__ == "__main__":
    main()
