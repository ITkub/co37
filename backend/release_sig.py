"""
CO-37 - Signatur von Update-Paketen.

Ein Update-Paket wird vom Watcher als root ausgepackt. Ein
untergeschobenes Paket waere damit Codeausfuehrung als root - auf jedem
System, das es einspielt. Deshalb wird vor dem Auspacken geprueft, ob das
Paket vom Herausgeber stammt.

Eine Pruefsumme allein genuegt dafuer nicht: wer die Datei austauschen
kann, kann auch die Pruefsummendatei daneben austauschen. Sie schuetzt
gegen einen abgebrochenen Download, nicht gegen Manipulation.

Verfahren: Ed25519 ueber den SHA-256 des Pakets. Signiert wird mit
tools/sign-release.py; der private Teil liegt ausschliesslich beim
Herausgeber.

--------------------------------------------------------------------
Bewusst getrennt vom Lizenzschluessel
--------------------------------------------------------------------
Zwei Schluesselpaare, weil sie sehr unterschiedlich wiegen. Der
Lizenzschluessel erlaubt, Lizenzen auszustellen - aergerlich, aber
begrenzt. Dieser hier erlaubt, Code als root auf jedem Kundensystem
auszufuehren. Und er wird viel seltener gebraucht: bei jeder
Veroeffentlichung statt bei jedem Verkauf.
"""

import base64
import hashlib
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUB_DATEI = Path(__file__).resolve().parent / "release_key.pub"

# Wird der Signaturzwang erzwungen?
#
# Ohne ausgelieferten oeffentlichen Schluessel laeuft CO-37 ohne Pruefung
# weiter - das ist der Zustand eines Quelltextes, aus dem sich jeder
# selbst baut. Wer aus dem Quelltext baut, signiert nichts und hat auch
# nichts davon: er hat den Code ohnehin in der Hand.
#
# Liegt der Schluessel dagegen vor, ist die Signatur Pflicht. Sonst
# koennte ein Angreifer sie einfach weglassen.
def zwang() -> bool:
    return PUB_DATEI.is_file()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _oeffentlich() -> Optional[Ed25519PublicKey]:
    try:
        return Ed25519PublicKey.from_public_bytes(
            _unb64(PUB_DATEI.read_text(encoding="ascii").strip()))
    except Exception:  # noqa: BLE001
        return None


def pruefsumme(daten: bytes) -> str:
    """SHA-256 des Pakets, wie ihn auch sign-release.py bildet."""
    return hashlib.sha256(daten).hexdigest()


class SignaturFehler(ValueError):
    """Ein Paket, das nicht angenommen werden darf."""


def pruefen(paket: bytes, signatur: Optional[str]):
    """
    Prueft die Signatur eines Pakets.

    Wirft SignaturFehler, wenn etwas nicht stimmt. Der Aufrufer verwirft
    das Paket dann, ohne es auszupacken.
    """
    if not zwang():
        return

    pub = _oeffentlich()
    if pub is None:
        raise SignaturFehler(
            "Der ausgelieferte Signaturschluessel ist unlesbar. Ohne ihn "
            "kann kein Update geprueft und deshalb keines eingespielt "
            "werden.")

    signatur = (signatur or "").strip()
    if not signatur:
        raise SignaturFehler(
            "Zu diesem Paket fehlt die Signatur. Sie liegt als Datei mit der "
            "Endung .sig neben dem Download.")

    try:
        roh = _unb64(signatur)
    except Exception:  # noqa: BLE001
        raise SignaturFehler("Die Signatur ist beschaedigt.")

    # Signiert wird der SHA-256 als Hexzeichenkette, nicht das Paket
    # selbst: so muss zum Signieren nichts Grosses im Speicher gehalten
    # werden, und die Pruefsumme laesst sich unabhaengig vergleichen.
    inhalt = pruefsumme(paket).encode("ascii")
    try:
        pub.verify(roh, inhalt)
    except InvalidSignature:
        raise SignaturFehler(
            "Die Signatur passt nicht zu diesem Paket. Es wurde veraendert "
            "oder stammt nicht vom Herausgeber. Das Paket wird nicht "
            "eingespielt.")
    except Exception:  # noqa: BLE001
        raise SignaturFehler("Die Signatur laesst sich nicht pruefen.")
