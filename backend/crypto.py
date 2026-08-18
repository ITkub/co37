"""
CO-37 - Verschluesselung gespeicherter Zugangsdaten

Muster uebernommen aus TK-37 (crypto_util.py), mit zwei Abweichungen:

1. Kein stiller Klartext-Fallback. TK-37 gibt Werte unverschluesselt zurueck,
   wenn kein Schluessel gesetzt ist - sinnvoll dort, weil es um nachtraeglich
   eingefuehrte Verschluesselung bei bestehenden Daten ging. Hier ist der
   einzige gespeicherte Geheimwert das Checkmk-Automation-Secret. Faellt der
   Schluessel weg, wird nicht gespeichert, sondern ein Fehler geworfen.

2. Eigene Variable CO37_SECRET_KEY statt JWT_SECRET. CO-37 hat
   keine JWT-Anmeldung, eine Doppelnutzung waere irrefuehrend.

Schluessel erzeugen:
    openssl rand -base64 48
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)
_PREFIX = "enc:v1:"


class CryptoNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    secret = os.getenv("CO37_SECRET_KEY", "").strip()
    if not secret:
        raise CryptoNotConfigured(
            "CO37_SECRET_KEY ist nicht gesetzt. Ohne diesen Schluessel "
            "werden keine Zugangsdaten gespeichert. Erzeugen mit: "
            "openssl rand -base64 48"
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def is_configured() -> bool:
    return bool(os.getenv("CO37_SECRET_KEY", "").strip())


def encrypt(value: str) -> str:
    """Verschluesselt einen Wert. Ohne Schluessel: CryptoNotConfigured."""
    if not value:
        return value
    if value.startswith(_PREFIX):
        return value
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """
    Entschluesselt einen Wert.
    Leerstring bedeutet: nicht lesbar. Aufrufer muss das behandeln.
    """
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        # Sollte nicht vorkommen - waere ein Klartext-Altbestand.
        logger.warning("Unverschluesselter Wert in der Datenbank gefunden.")
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except CryptoNotConfigured:
        logger.error("Verschluesselter Wert vorhanden, aber Schluessel fehlt.")
        return ""
    except InvalidToken:
        logger.error(
            "Wert nicht entschluesselbar. CO37_SECRET_KEY geaendert?"
        )
        return ""


# ----------------------------------------------------------------------
# Agent-Tokens
# ----------------------------------------------------------------------
def hash_token(token: str) -> str:
    """
    Agent-Tokens werden nur als Hash gespeichert.

    Ein einfacher SHA-256 ohne Salt genuegt: die Tokens stammen aus
    secrets.token_urlsafe(32), haben also 256 Bit Entropie. Ein
    Woerterbuchangriff ist ausgeschlossen, und der Hash muss deterministisch
    sein, damit beim Heartbeat nachgeschlagen werden kann.
    """
    return hashlib.sha256(token.encode()).hexdigest()
