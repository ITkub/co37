"""
CO-37 - Signatur von Update-Paketen.

Legt sich ein eigenes Schluesselpaar in einem temporaeren Verzeichnis an.
Der echte private Signaturschluessel liegt beim Herausgeber und ist hier
weder vorhanden noch noetig.

Braucht kein Backend und kein Netz.

    python3 tests/release-sig-test.py

Der Anlass: der Watcher packt ein Update-Paket als root aus. Ein
untergeschobenes Paket waere damit Codeausfuehrung als root auf jedem
System, das es einspielt.
"""

import base64
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import release_sig  # noqa: E402

TMP = Path(tempfile.mkdtemp())
fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


PAKET = b"das waere das ZIP" * 500

# ------------------------------------------------- ohne ausgelieferten Teil
release_sig.PUB_DATEI = TMP / "gibtsnicht.pub"
check("ohne Schluessel kein Zwang", not release_sig.zwang())
# Wer aus dem Quelltext baut, signiert nichts - und hat auch nichts davon.
release_sig.pruefen(PAKET, "")
check("ohne Schluessel geht alles durch", True)

# --------------------------------------------------------- mit Schluessel
PRIVAT = Ed25519PrivateKey.generate()
PUB = TMP / "release_key.pub"
PUB.write_text(b64(PRIVAT.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)) + "\n",
    encoding="ascii")
release_sig.PUB_DATEI = PUB

check("mit Schluessel gilt der Zwang", release_sig.zwang())

summe = release_sig.pruefsumme(PAKET)
check("Pruefsumme ist ein SHA-256", len(summe) == 64, len(summe))

echte = b64(PRIVAT.sign(summe.encode("ascii")))
release_sig.pruefen(PAKET, echte)
check("echte Signatur wird angenommen", True)


def muss_scheitern(label, paket, sig, erwartet=""):
    try:
        release_sig.pruefen(paket, sig)
        check(label, False, "wurde angenommen!")
    except release_sig.SignaturFehler as e:
        check(label, erwartet in str(e) if erwartet else True, str(e)[:60])


muss_scheitern("fehlende Signatur wird abgewiesen", PAKET, "", "fehlt")
muss_scheitern("leere Signatur wird abgewiesen", PAKET, "   ", "fehlt")
muss_scheitern("veraendertes Paket wird abgewiesen", PAKET + b"x", echte,
               "passt nicht")
muss_scheitern("ein einziges Byte weniger faellt auf", PAKET[:-1], echte,
               "passt nicht")
muss_scheitern("Unsinn als Signatur wird abgewiesen", PAKET, "###")

# Ein Zeichen in der Signatur austauschen
kaputt = echte[:-2] + ("A" if echte[-2] != "A" else "B") + echte[-1]
muss_scheitern("verfaelschte Signatur wird abgewiesen", PAKET, kaputt)

# Fremdes Paar
fremd = Ed25519PrivateKey.generate()
muss_scheitern("fremd signiertes Paket wird abgewiesen", PAKET,
               b64(fremd.sign(summe.encode("ascii"))), "passt nicht")

# Signatur eines anderen Pakets - der haeufigste Bedienfehler
anderes = b"ein anderes Paket"
muss_scheitern("Signatur eines anderen Pakets wird abgewiesen", PAKET,
               b64(PRIVAT.sign(release_sig.pruefsumme(anderes).encode())),
               "passt nicht")

# Unlesbarer oeffentlicher Teil
(TMP / "kaputt.pub").write_text("###nicht base64###", encoding="ascii")
release_sig.PUB_DATEI = TMP / "kaputt.pub"
muss_scheitern("unlesbarer Schluessel laesst nichts durch", PAKET, echte,
               "unlesbar")

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
