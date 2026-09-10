#!/usr/bin/env python3
"""
Baut das Linux-Agentenpaket als .deb.

Bewusst in reinem Python: ein .deb ist ein ar-Archiv aus drei Mitgliedern
(debian-binary, control.tar.gz, data.tar.gz). Damit laeuft der Bau auch dort,
wo dpkg-deb fehlt.

Das Paket enthaelt KEIN Geheimnis. Server und Enrollment-Token werden beim
Installieren uebergeben:

    CO37_SERVER=http://192.168.1.10:8080 apt-get install -y ./co37-agent.deb

Aufruf:
    python3 build_deb.py --version 0.2.1 --out ../state/packages
"""
import argparse
import gzip
import io
import os
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PY = HERE.parent / "agent" / "agent.py"

# Der oeffentliche Signaturschluessel wird mit ausgeliefert, damit der
# Agent die eigene Selbstaktualisierung pruefen kann (F-07 der
# Sicherheitspruefung vom 2026-08-22). Er liegt im Backend - dasselbe
# Schluesselpaar, mit dem auch die Update-Pakete signiert werden.
#
# Fehlt er, wird das Paket trotzdem gebaut: das ist der Stand eines
# Quelltextes ohne Signaturschluessel. Der Agent prueft dann nicht - er
# richtet sich danach, ob der Schluessel neben ihm liegt.
RELEASE_KEY = HERE.parent / "backend" / "release_key.pub"

CONTROL = """Package: co37-agent
Version: {version}
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-requests, python3-cryptography
Maintainer: {vendor}
Description: CO-37 Agent
 Meldet sich ausgehend beim CO-37-Backend, sucht nach Paketaktualisierungen
 und fuehrt sie auf Anforderung aus. Erkennt selbstaendig, ob ein Neustart
 aussteht.
"""

POSTINST = r"""#!/bin/sh
set -e

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

PRERM = r"""#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
    systemctl stop co37-agent.service || true
    systemctl disable co37-agent.service || true
fi
exit 0
"""

POSTRM = r"""#!/bin/sh
set -e
if [ "$1" = "purge" ]; then
    rm -rf /etc/co37
fi
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
exit 0
"""

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


def _tar_gz(entries: list[tuple[str, bytes, int]]) -> bytes:
    """entries: (Pfad im Archiv, Inhalt, Dateirechte)"""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        seen_dirs = set()
        for path, data, mode in entries:
            # Uebergeordnete Verzeichnisse anlegen
            parts = Path(path).parent.parts
            for i in range(1, len(parts) + 1):
                d = "/".join(parts[:i])
                if d and d != "." and d not in seen_dirs:
                    seen_dirs.add(d)
                    ti = tarfile.TarInfo("./" + d)
                    ti.type = tarfile.DIRTYPE
                    ti.mode = 0o755
                    ti.mtime = int(time.time())
                    tar.addfile(ti)
            ti = tarfile.TarInfo("./" + path)
            ti.size = len(data)
            ti.mode = mode
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))
    return gzip.compress(raw.getvalue())


def _ar_member(name: str, data: bytes) -> bytes:
    header = (
        f"{name:<16}"
        f"{int(time.time()):<12}"
        f"{'0':<6}{'0':<6}"
        f"{'100644':<8}"
        f"{len(data):<10}"
        "`\n"
    ).encode()
    out = header + data
    if len(data) % 2:
        out += b"\n"
    return out



def sicher_schreiben(ziel: Path, daten: bytes):
    """
    Datei anlegen, ohne einer Verknuepfung zu folgen (F-29 der Pruefung
    vom 2026-08-31).

    Dieses Skript laeuft als root, wenn der Watcher es startet. Ein
    blankes open(ziel, "wb") folgt einer Verknuepfung, die jemand unter
    dem erwarteten Paketnamen abgelegt hat - root schreibt dann dorthin,
    wohin sie zeigt.

    Seit 0.37.10 liegt das Ziel in state/ und gehoert root, der Weg ist
    also schon oben zu. Das hier ist die zweite Schranke: wer den
    Ausgabeort spaeter einmal verlegt, soll die Luecke nicht mit
    verlegen.

    O_TRUNC statt O_EXCL, damit ein zweiter Bau dieselbe Fassung
    ueberschreiben darf - eine vorhandene Verknuepfung weist O_NOFOLLOW
    ohnehin ab.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(ziel, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(daten)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise

def build(version: str, out_dir: Path) -> Path:
    if not AGENT_PY.is_file():
        raise SystemExit(f"agent.py nicht gefunden unter {AGENT_PY}")

    agent_code = AGENT_PY.read_bytes()

    inhalt = [
        ("usr/lib/co37/agent.py", agent_code, 0o755),
        ("usr/bin/co37-connect", ENROLL_CMD.encode(), 0o755),
        ("lib/systemd/system/co37-agent.service", SERVICE.encode(), 0o644),
    ]
    # Muss neben agent.py liegen - der Agent sucht ihn dort.
    if RELEASE_KEY.is_file():
        inhalt.append(
            ("usr/lib/co37/release_key.pub", RELEASE_KEY.read_bytes(), 0o644))
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

    data_tar = _tar_gz(inhalt)

    control_tar = _tar_gz([
        # Verantwortlicher steht spaeter in den Paketeigenschaften. Neutral
        # als Vorgabe, damit ein weitergegebenes Paket nicht die Firma
        # dessen traegt, der es gebaut hat. Fuer eigene Pakete setzen:
        #     CO37_VENDOR="ITkub <michael.kuban@itkub.de>" ...
        ("control", CONTROL.format(
            version=version,
            vendor=os.environ.get("CO37_VENDOR", "CO-37 <noreply@example.invalid>"),
        ).encode(), 0o644),
        ("postinst", POSTINST.encode(), 0o755),
        ("prerm", PRERM.encode(), 0o755),
        ("postrm", POSTRM.encode(), 0o755),
        ("conffiles", b"", 0o644),
    ])

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"co37-agent_{version}_all.deb"

    sicher_schreiben(target, b"".join([
        b"!<arch>\n",
        _ar_member("debian-binary", b"2.0\n"),
        _ar_member("control.tar.gz", control_tar),
        _ar_member("data.tar.gz", data_tar),
    ]))

    return target


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
    print(f"{path}  ({path.stat().st_size / 1024:.0f} KB)")
