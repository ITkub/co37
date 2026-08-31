#!/usr/bin/env python3
"""
CO-37 - Update-Pakete signieren.

Nur fuer den Herausgeber. Gehoert nicht auf den Server.

    python sign-release.py --init
    python sign-release.py co37_v0_33_0.zip
    python sign-release.py --pruefen co37_v0_33_0.zip
    python sign-release.py --sicherung

Warum ueberhaupt signiert wird: der Watcher packt ein Update-Paket als
root aus. Ein untergeschobenes Paket waere damit Codeausfuehrung als root
auf jedem System, das es einspielt.

Eine Pruefsumme allein genuegt nicht - wer die Datei austauschen kann,
kann auch die Pruefsummendatei daneben austauschen.

--------------------------------------------------------------------
Getrennt vom Lizenzschluessel
--------------------------------------------------------------------
Eigenes Paar, weil die beiden Schluessel sehr unterschiedlich wiegen.
Der Lizenzschluessel erlaubt, Lizenzen auszustellen. Dieser hier erlaubt,
Code als root auf jedem Kundensystem auszufuehren - und wird viel
seltener gebraucht: bei jeder Veroeffentlichung statt bei jedem Verkauf.
"""

import argparse
import base64
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature
except ImportError:
    sys.exit("Es fehlt die Bibliothek 'cryptography'.\n"
             "    pip install cryptography")

HOME = Path(os.environ.get("CO37_LICENSE_HOME", Path.home() / ".co37"))
PRIVAT = HOME / "release-private.pem"

PROJEKT = Path(__file__).resolve().parent.parent
OEFFENTLICH = PROJEKT / "backend" / "release_key.pub"



def schluessel_schreiben(ziel: Path, pem: bytes):
    """
    Privaten Schluessel mit 0600 anlegen - von Anfang an (F-35 der
    Pruefung vom 2026-08-31).

    Bis 0.37.9 stand hier write_bytes() und danach chmod(0600). Zwischen
    beiden lag die Datei mit der Umask-Vorgabe auf der Platte, ueblich
    0644, in einem Verzeichnis mit 0755. Wer in diesem Fenster liest, hat
    den Schluessel - und mit dem Release-Schluessel Code als root auf
    jedem Kundensystem.

    Genau derselbe Fall wird in setup.sh fuenfzig Zeilen nach der
    secret.key-Stelle mit 'umask 177' richtig geloest; hier fehlte er.

    O_EXCL: eine vorhandene Datei wird nicht ueberschrieben. Die Aufrufer
    pruefen das ohnehin vorher, aber ein Schluessel ist nichts, was man
    versehentlich ersetzt.
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ziel.parent, 0o700)
    except OSError:
        # Unter Windows wirkungslos, siehe den Hinweis beim Anlegen.
        pass
    fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)

def _b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pruefsumme(pfad: Path) -> str:
    """SHA-256 des Pakets, blockweise gelesen."""
    h = hashlib.sha256()
    with open(pfad, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ======================================================================
def init():
    if PRIVAT.is_file():
        sys.exit(f"Es gibt bereits einen privaten Signaturschluessel:\n"
                 f"    {PRIVAT}\n\n"
                 f"Er wird nicht ueberschrieben.")
    if OEFFENTLICH.is_file():
        # Wird der ausgelieferte Schluessel ersetzt, laesst sich kein
        # bisher signiertes Paket mehr einspielen - bei jedem Kunden.
        sys.exit(f"Es gibt bereits einen ausgelieferten oeffentlichen "
                 f"Signaturschluessel:\n"
                 f"    {OEFFENTLICH}\n\n"
                 f"Er wird nicht ueberschrieben. Alle damit signierten Pakete\n"
                 f"liessen sich sonst nicht mehr einspielen.")

    HOME.mkdir(parents=True, exist_ok=True)
    privat = Ed25519PrivateKey.generate()
    schluessel_schreiben(PRIVAT, privat.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))

    roh = privat.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    OEFFENTLICH.parent.mkdir(parents=True, exist_ok=True)
    with open(OEFFENTLICH, "w", encoding="ascii", newline="\n") as fh:
        fh.write(_b64(roh) + "\n")

    print(f"""
Signaturschluessel erzeugt.

  Privat:      {PRIVAT}
  Oeffentlich: {OEFFENTLICH}

Dieser Schluessel wiegt schwerer als der Lizenzschluessel: wer ihn hat,
kann ein Update-Paket signieren, das auf jedem Kundensystem als root
ausgepackt wird.

Genauso sichern wie den Lizenzschluessel - Passwortmanager, Papier,
verschluesselt ausser Haus. Nicht auf dem CO-37-Server, nicht im
Homelab-Backup, niemals in ein Git-Repository.

    python tools/sign-release.py --sicherung

ACHTUNG beim Uebergang: das Paket, das die Pruefung einfuehrt, muss noch
von der bisherigen Fassung eingespielt werden koennen - die prueft
nichts. Ab dann ist jedes Paket signaturpflichtig.
""".strip())


def signieren(pfad: Path):
    if not pfad.is_file():
        sys.exit(f"Datei nicht gefunden: {pfad}")
    if not PRIVAT.is_file():
        sys.exit(f"Kein privater Signaturschluessel unter {PRIVAT}.\n"
                 f"Erst erzeugen:  python tools/sign-release.py --init")

    privat = serialization.load_pem_private_key(PRIVAT.read_bytes(),
                                                password=None)
    summe = pruefsumme(pfad)
    # Signiert wird die Pruefsumme als Hexzeichenkette, nicht das Paket
    # selbst: so muss nichts Grosses im Speicher gehalten werden, und die
    # Pruefsumme laesst sich unabhaengig vergleichen.
    sig = _b64(privat.sign(summe.encode("ascii")))

    sig_datei = pfad.with_suffix(pfad.suffix + ".sig")
    with open(sig_datei, "w", encoding="ascii", newline="\n") as fh:
        fh.write(sig + "\n")

    summen_datei = pfad.with_suffix(pfad.suffix + ".sha256")
    with open(summen_datei, "w", encoding="ascii", newline="\n") as fh:
        fh.write(f"{summe}  {pfad.name}\n")

    print(f"  Paket:      {pfad.name}")
    print(f"  SHA-256:    {summe}")
    print(f"  Signatur:   {sig_datei.name}")
    print(f"  Pruefsumme: {summen_datei.name}")
    print()
    print("Beide Dateien zusammen mit dem Paket veroeffentlichen.")
    print("Die .sig-Datei wird beim Einspielen gebraucht, die .sha256 nur")
    print("zum Abgleich von Hand.")


def pruefen(pfad: Path):
    """Gegenprobe mit dem ausgelieferten oeffentlichen Schluessel."""
    if not pfad.is_file():
        sys.exit(f"Datei nicht gefunden: {pfad}")
    sig_datei = pfad.with_suffix(pfad.suffix + ".sig")
    if not sig_datei.is_file():
        sys.exit(f"Keine Signatur gefunden: {sig_datei}")
    if not OEFFENTLICH.is_file():
        sys.exit(f"Kein oeffentlicher Signaturschluessel unter {OEFFENTLICH}.")

    pub = Ed25519PublicKey.from_public_bytes(
        _unb64(OEFFENTLICH.read_text(encoding="ascii").strip()))
    summe = pruefsumme(pfad)
    try:
        pub.verify(_unb64(sig_datei.read_text(encoding="ascii").strip()),
                   summe.encode("ascii"))
    except InvalidSignature:
        sys.exit("Die Signatur passt NICHT zu diesem Paket.")
    print(f"  {pfad.name}")
    print(f"  SHA-256:  {summe}")
    print("  Signatur passt zum ausgelieferten oeffentlichen Schluessel.")


def sicherung():
    if not PRIVAT.is_file():
        sys.exit(f"Kein privater Signaturschluessel unter {PRIVAT}.")
    roh = PRIVAT.read_text(encoding="ascii")
    print("=" * 68)
    print("CO-37 - PRIVATER SIGNATURSCHLUESSEL")
    print("Erstellt am", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print()
    print("Wer diesen Schluessel hat, kann Update-Pakete signieren, die auf")
    print("jedem Kundensystem als root ausgepackt werden.")
    print("=" * 68)
    print()
    print(roh.rstrip())
    print()
    print(f"Pruefsumme (SHA-256, erste 16 Zeichen): "
          f"{hashlib.sha256(roh.encode('ascii')).hexdigest()[:16]}")
    print("=" * 68)


def main():
    p = argparse.ArgumentParser(
        description="CO-37 Update-Pakete signieren.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("paket", nargs="?", help="ZIP-Datei, die signiert wird")
    p.add_argument("--init", action="store_true",
                   help="Signatur-Schluesselpaar erzeugen (einmalig)")
    p.add_argument("--pruefen", metavar="ZIP",
                   help="vorhandene Signatur gegenpruefen")
    p.add_argument("--sicherung", action="store_true",
                   help="privaten Schluessel druckfreundlich ausgeben")
    a = p.parse_args()

    if a.init:
        return init()
    if a.sicherung:
        return sicherung()
    if a.pruefen:
        return pruefen(Path(a.pruefen))
    if a.paket:
        return signieren(Path(a.paket))
    p.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
