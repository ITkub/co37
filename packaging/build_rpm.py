#!/usr/bin/env python3
"""
Baut das Linux-Agentenpaket als .rpm - fuer Red Hat, Oracle, Rocky,
AlmaLinux und SUSE.

WARUM HIER rpmbuild UND NICHT REINES PYTHON

`build_deb.py` baut das .deb selbst, ohne dpkg-deb: ein .deb ist ein
ar-Archiv aus drei Mitgliedern, das kann man ehrlich nachbauen. Ein .rpm
ist etwas anderes - ein binaeres Kopfformat mit Tag-Tabellen,
Region-Tags und Pflichtfeldern. Das von Hand nachzubauen ist machbar und
genau das Muster, das in der MSI-Saga drei Fehldiagnosen gekostet hat:
ein Format, dessen stillschweigend fehlendes Feld erst beim Kunden
auffaellt.

rpmbuild ist korrekt per Konstruktion und prueft sich selbst. Der Preis
ist eine Abhaengigkeit auf dem Bauserver - das Debian-Paket heisst `rpm`.
Der Grund fuer reines Python bei .deb ("laeuft auch ohne dpkg-deb")
greift hier nicht: gebaut wird auf dem Server, und dort ist `rpm` ein
apt install entfernt.

Das Paket enthaelt KEIN Geheimnis. Server und Enrollment-Token werden
beim Installieren uebergeben:

    CO37_SERVER=https://co37.example.de rpm -i co37-agent-0.37.26-1.noarch.rpm

Aufruf:
    python3 build_rpm.py --version 0.37.26 --out ../state/packages
"""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PY = HERE.parent / "agent" / "agent.py"

# Der oeffentliche Signaturschluessel wird mit ausgeliefert, damit der
# Agent die eigene Selbstaktualisierung pruefen kann (F-07 der
# Sicherheitspruefung vom 2026-08-22). Er liegt im Backend - dasselbe
# Schluesselpaar, mit dem auch die Update-Pakete signiert werden.
RELEASE_KEY = HERE.parent / "backend" / "release_key.pub"

# Dieselben Pfade wie im .deb. Ein Agent, der auf Debian unter
# /usr/lib/co37/agent.py liegt und auf RHEL woanders, waere zwei Produkte:
# jede Anleitung, jeder Handgriff und jede Fehlersuche muesste dann nach
# Distribution unterscheiden.
#
# Einzige Abweichung: die systemd-Unit liegt unter /usr/lib/systemd, nicht
# /lib/systemd. Auf RHEL und SUSE ist /lib nur noch eine Verknuepfung, und
# rpm weist ein Paket zurueck, das in eine Verknuepfung hineininstalliert.
ZIEL_AGENT = "/usr/lib/co37/agent.py"
ZIEL_KEY = "/usr/lib/co37/release_key.pub"
ZIEL_CONNECT = "/usr/bin/co37-connect"
ZIEL_UNIT = "/usr/lib/systemd/system/co37-agent.service"

SERVICE = """[Unit]
Description=CO-37 Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /usr/lib/co37/agent.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""

ENROLL_CMD = r"""#!/bin/sh
# Traegt den CO-37-Server ein und startet den Agent.
#   co37-connect http://192.168.1.10:8080 [--insecure]
set -e

if [ $# -lt 1 ]; then
    echo "Aufruf: co37-connect <server-url> [--insecure]"
    exit 1
fi

mkdir -p /etc/co37
{
  echo "server = $1"
  [ "${2:-}" = "--insecure" ] && echo "verify_ssl = false"
} > /etc/co37/agent.conf
chmod 600 /etc/co37/agent.conf

if [ -d /run/systemd/system ]; then
    systemctl restart co37-agent.service
    echo "Agent gestartet. Der Host wartet nun auf Freigabe im Dashboard."
else
    echo "Konfiguration geschrieben."
fi
"""

# %post laeuft bei Installation ($1 = 1) UND bei Aktualisierung ($1 = 2).
# Beides ist richtig so: die Unit muss auch nach einem Upgrade
# eingeschaltet und neu gestartet werden.
POST = r"""
CONF=/etc/co37/agent.conf
mkdir -p /etc/co37

if [ ! -f "$CONF" ]; then
    if [ -z "$CO37_SERVER" ]; then
        echo "-------------------------------------------------------------"
        echo " CO-37 Agent installiert, aber nicht konfiguriert."
        echo ""
        echo " Server nachtraeglich eintragen mit:"
        echo "   co37-connect <server-url>"
        echo "-------------------------------------------------------------"
    else
        {
          echo "server = $CO37_SERVER"
          [ -n "$PP_VERIFY_SSL" ] && echo "verify_ssl = $PP_VERIFY_SSL"
        } > "$CONF"
        chmod 600 "$CONF"
        echo "CO-37: Konfiguration geschrieben."
    fi
fi

if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
    systemctl enable co37-agent.service || true
    if [ -f "$CONF" ]; then
        systemctl restart co37-agent.service || true
    fi
fi
exit 0
"""

# $1 = 0 heisst Deinstallation, $1 = 1 heisst "die alte Fassung geht bei
# einem Upgrade". Ohne diese Unterscheidung wuerde jedes Upgrade den
# Dienst abschalten - und ihn erst im %post wieder einschalten, mit einer
# Luecke dazwischen. Dieselbe Falle wie F-48 unter Windows, wo die
# geplante Aufgabe beim Upgrade geloescht und erst zweitausend
# Sequenznummern spaeter wieder angelegt wurde.
PREUN = r"""
if [ "$1" = "0" ]; then
    if [ -d /run/systemd/system ]; then
        systemctl stop co37-agent.service || true
        systemctl disable co37-agent.service || true
    fi
fi
exit 0
"""

# /etc/co37 wird NICHT geloescht - auch nicht bei der Deinstallation.
#
# Das .deb tut das bei 'apt purge', und das ist Debian-Praxis. rpm kennt
# kein purge; eine Deinstallation entspricht dort 'apt remove', und die
# laesst die Konfiguration ebenfalls stehen. Dazu kommt der Grund aus
# Runde 5: Agent und Backend teilen sich /etc/co37, und dort liegt die
# backend.env des Servers. Ein rm -rf an dieser Stelle nimmt sie mit.
POSTUN = r"""
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
exit 0
"""

SPEC = """Name:           co37-agent
Version:        {version}
Release:        1
Summary:        CO-37 Agent
License:        Proprietary
BuildArch:      noarch
Vendor:         {vendor}
Requires:       {requires}

%description
Meldet sich ausgehend beim CO-37-Backend, sucht nach Paketaktualisierungen
und fuehrt sie auf Anforderung aus. Erkennt selbstaendig, ob ein Neustart
aussteht.

# Kein %%prep, %%build, %%install: der Buildroot wird von build_rpm.py
# fertig hingestellt. rpmbuild soll das Paket schnueren, nicht bauen.

%files
%dir /usr/lib/co37
{dateien}

%post
{post}

%preun
{preun}

%postun
{postun}

%changelog
"""

# Was der Agent zur Laufzeit braucht.
#
# Die Namen sind auf beiden Familien dieselben: RHEL, Oracle, Rocky und
# Alma liefern python3-requests und python3-cryptography aus baseos bzw.
# appstream; openSUSE baut sie als python3XX-* und laesst die Fassung des
# Vorgabe-Python 'python3-requests' bereitstellen.
#
# Das ist die Stelle, an der ein falscher Name das Paket auf einer ganzen
# Distributionsfamilie uninstallierbar macht, ohne dass der Bau etwas
# merkt. Deshalb prueft der Bau unten nach, dass die Abhaengigkeiten im
# fertigen Paket wirklich stehen - und deshalb gehoert eine Installation
# auf einer echten Anlage zur Abnahme.
REQUIRES = ["python3", "python3-requests", "python3-cryptography"]


def _lege_ab(wurzel: Path, ziel: str, daten: bytes, rechte: int):
    pfad = wurzel / ziel.lstrip("/")
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(daten)
    pfad.chmod(rechte)


def pruefe_paket(pfad: Path, dateien: list, mit_schluessel: bool):
    """
    Das FERTIGE Paket gegen sich selbst pruefen.

    Die Lehre aus der MSI-Saga: wixl liess CustomActions stillschweigend
    weg, Rueckgabewert 0, Paket entstand, Tabelle leer. Seitdem prueft
    jeder Bau sein Ergebnis statt seiner Absicht. Fuer rpm gilt dasselbe
    - ein Makro, das ins Leere laeuft, faellt sonst erst beim Kunden auf.
    """
    def rpm(*args) -> str:
        return subprocess.run(["rpm", *args, str(pfad)], check=True,
                              capture_output=True, text=True).stdout

    inhalt = set(rpm("-qlp").split())
    fehlt = [d for d in dateien if d not in inhalt]
    if fehlt:
        raise SystemExit(f"Im Paket fehlen Dateien: {', '.join(fehlt)}")

    if mit_schluessel and ZIEL_KEY not in inhalt:
        raise SystemExit(
            f"{ZIEL_KEY} fehlt im Paket, obwohl der Schluessel vorliegt. "
            f"Agenten daraus wuerden ihre Selbstaktualisierung nicht pruefen.")

    skripte = rpm("-qp", "--scripts")
    for name in ("postinstall", "preuninstall", "postuninstall"):
        if name not in skripte:
            raise SystemExit(f"Das Paket hat kein {name}-Skript.")
    if "systemctl enable co37-agent.service" not in skripte:
        raise SystemExit("Das postinstall-Skript schaltet die Unit nicht ein.")
    # Die Unterscheidung Upgrade/Deinstallation aus PREUN. Ohne sie legt
    # jedes Upgrade den Dienst still.
    if '"$1" = "0"' not in skripte:
        raise SystemExit(
            "Das preuninstall-Skript unterscheidet Upgrade und "
            "Deinstallation nicht - ein Upgrade wuerde den Dienst "
            "abschalten.")

    verlangt = set(rpm("-qp", "--requires").split())
    fehlende = [r for r in REQUIRES if r not in verlangt]
    if fehlende:
        raise SystemExit(
            f"Das Paket verlangt {', '.join(fehlende)} nicht. "
            f"Auf einer Anlage ohne diese Pakete startet der Agent nicht.")


def build(version: str, out_dir: Path) -> Path:
    if not AGENT_PY.is_file():
        raise SystemExit(f"agent.py nicht gefunden unter {AGENT_PY}")
    if not shutil.which("rpmbuild"):
        raise SystemExit(
            "rpmbuild fehlt. Installieren mit:  apt install rpm\n"
            "Ohne rpmbuild entsteht kein RPM - das DEB und das MSI sind "
            "davon nicht betroffen.")

    dateien = [ZIEL_AGENT, ZIEL_CONNECT, ZIEL_UNIT]
    mit_schluessel = RELEASE_KEY.is_file()

    if not mit_schluessel:
        if os.environ.get("CO37_OHNE_SIGNATUR") == "ja":
            print("!!! kein backend/release_key.pub - der Agent aus diesem "
                  "Paket prueft seine Selbstaktualisierung NICHT. "
                  "Ausdruecklich erlaubt ueber CO37_OHNE_SIGNATUR=ja.")
        else:
            # Abbrechen statt hinweisen, wie in build_deb.py (F-44).
            raise SystemExit(
                "backend/release_key.pub fehlt. Agenten aus diesem Paket "
                "wuerden ihre Selbstaktualisierung nicht pruefen.\n"
                "Schluessel anlegen:  python3 tools/sign-release.py --init\n"
                "Oder ausdruecklich ohne:  CO37_OHNE_SIGNATUR=ja ...")
    else:
        dateien.append(ZIEL_KEY)

    with tempfile.TemporaryDirectory(prefix="co37-rpm-") as tmp:
        oben = Path(tmp)
        wurzel = oben / "buildroot"

        _lege_ab(wurzel, ZIEL_AGENT, AGENT_PY.read_bytes(), 0o755)
        _lege_ab(wurzel, ZIEL_CONNECT, ENROLL_CMD.encode(), 0o755)
        _lege_ab(wurzel, ZIEL_UNIT, SERVICE.encode(), 0o644)
        if mit_schluessel:
            _lege_ab(wurzel, ZIEL_KEY, RELEASE_KEY.read_bytes(), 0o644)

        spec = oben / "co37-agent.spec"
        spec.write_text(SPEC.format(
            version=version,
            # Verantwortlicher steht spaeter in den Paketeigenschaften.
            # Neutral als Vorgabe, damit ein weitergegebenes Paket nicht
            # die Firma dessen traegt, der es gebaut hat.
            vendor=os.environ.get("CO37_VENDOR",
                                  "CO-37 <noreply@example.invalid>"),
            requires=", ".join(REQUIRES),
            dateien="\n".join(dateien),
            post=POST.strip(),
            preun=PREUN.strip(),
            postun=POSTUN.strip(),
        ), encoding="utf-8")

        ergebnis = subprocess.run(
            ["rpmbuild", "-bb",
             "--define", f"_topdir {oben}",
             "--define", f"buildroot {wurzel}",
             # Ohne das haengt der Paketname an der Architektur des
             # Bauservers, obwohl BuildArch noarch dasteht.
             "--target", "noarch",
             str(spec)],
            capture_output=True, text=True)
        if ergebnis.returncode != 0:
            raise SystemExit("rpmbuild fehlgeschlagen:\n"
                             + (ergebnis.stdout or "") + (ergebnis.stderr or ""))

        gebaut = list((oben / "RPMS").rglob("co37-agent-*.rpm"))
        if len(gebaut) != 1:
            raise SystemExit(
                f"rpmbuild hat {len(gebaut)} Pakete hinterlassen, erwartet "
                f"war genau eins: {[p.name for p in gebaut]}")

        out_dir.mkdir(parents=True, exist_ok=True)
        ziel = out_dir / gebaut[0].name

        # Kein blankes copy: dieses Skript laeuft als root, wenn der
        # Watcher es startet, und eine Verknuepfung unter dem erwarteten
        # Paketnamen liesse root woandershin schreiben (F-29). Das Ziel
        # liegt seit 0.37.10 in state/ und gehoert root, der Weg ist also
        # schon oben zu - das hier ist die zweite Schranke.
        if ziel.is_symlink():
            raise SystemExit(f"{ziel} ist eine Verknuepfung. Abbruch.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
        fd = os.open(ziel, flags, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(gebaut[0].read_bytes())

    pruefe_paket(ziel, dateien, mit_schluessel)
    return ziel


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

    pfad = build(args.version, Path(args.out))
    print(f"{pfad}  ({pfad.stat().st_size / 1024:.0f} KB)")
