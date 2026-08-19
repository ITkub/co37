"""
CO-37 - Lizenzpruefung.

Prueft signierte Lizenzschluessel und stellt fest, wie viele Hosts
freigegeben werden duerfen. Ausgestellt werden Schluessel mit
tools/make-license.py; der private Teil dazu liegt ausschliesslich beim
Lizenzgeber.

Verfahren: Ed25519. Hier steckt nur der oeffentliche Gegenpart - damit
lassen sich Schluessel pruefen, aber nicht erzeugen, auch nicht von
jemandem, der den gesamten Quelltext hat.

Kein Rueckruf zum Lizenzgeber, keine Internetverbindung noetig. CO-37
soll in abgeschotteten Netzen laufen koennen.

--------------------------------------------------------------------
Was die Pruefung NICHT leistet
--------------------------------------------------------------------
Der Quelltext ist einsehbar. Wer die Pruefung entfernen will, entfernt
sie - das gilt fuer jede selbstgehostete Software und ist dann eine
bewusste Lizenzverletzung, kein Versehen. Ebenso laesst sich die Uhr des
Servers zurueckstellen.

Was die Signatur leistet: einen Schluessel *faelschen* kann niemand.

--------------------------------------------------------------------
Was bei Ueberschreitung passiert
--------------------------------------------------------------------
Nichts hoert auf zu arbeiten. Ein Patch-Management-Werkzeug, das wegen
einer Lizenzfrage Systeme ungepatcht laesst, schafft genau die
Sicherheitsluecke, gegen die es angeschafft wurde.

Begrenzt wird ausschliesslich die *Freigabe neuer Hosts*. Bereits
freigegebene laufen unveraendert weiter - auch nach Ablauf eines
Schluessels, dauerhaft.
"""

import base64
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Hosts, die ohne Schluessel freigegeben werden duerfen. Deckt Homelabs
# ab und laesst groessere Umgebungen ausprobieren.
FREIE_HOSTS = 10

# Ab wann in der Oberflaeche auf den bevorstehenden Ablauf hingewiesen
# wird.
WARNUNG_AB_TAGEN = 30

VORSATZ = "CO37-"
PUB_DATEI = Path(__file__).resolve().parent / "license_key.pub"


def _unb64(text: str) -> bytes:
    """base64url ohne Auffuellzeichen - so wird der Schluessel ausgegeben."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _oeffentlich() -> Optional[Ed25519PublicKey]:
    """
    Oeffentlicher Schluessel, oder None wenn keiner ausgeliefert wurde.

    Ohne Datei laeuft CO-37 im Freibetrag weiter. Das ist der Zustand
    eines Quelltextes ohne ausgestellte Schluessel - kein Fehler.
    """
    try:
        roh = _unb64(PUB_DATEI.read_text(encoding="ascii").strip())
        return Ed25519PublicKey.from_public_bytes(roh)
    except Exception:  # noqa: BLE001
        return None


class Lizenz:
    """Ergebnis einer Pruefung. Immer vorhanden, auch ohne Schluessel."""

    def __init__(self, daten: Optional[dict] = None):
        self.daten = daten or {}

    # ---------------------------------------------------------------- Werte
    @property
    def vorhanden(self) -> bool:
        return bool(self.daten)

    @property
    def kunde(self) -> str:
        return self.daten.get("k", "")

    @property
    def nummer(self) -> Optional[int]:
        return self.daten.get("nr")

    @property
    def unbegrenzt(self) -> bool:
        """0 bedeutet unbegrenzt - so braucht es keinen Sonderwert."""
        return self.vorhanden and self.daten.get("h", 0) == 0

    @property
    def gueltig_bis(self) -> Optional[date]:
        wert = self.daten.get("exp")
        return date.fromisoformat(wert) if wert else None

    @property
    def unbefristet(self) -> bool:
        return self.vorhanden and self.daten.get("exp") is None

    @property
    def abgelaufen(self) -> bool:
        ende = self.gueltig_bis
        return ende is not None and date.today() > ende

    @property
    def tage_uebrig(self) -> Optional[int]:
        ende = self.gueltig_bis
        return None if ende is None else (ende - date.today()).days

    # ------------------------------------------------------------- Ergebnis
    @property
    def erlaubte_hosts(self) -> Optional[int]:
        """
        Wie viele Hosts freigegeben werden duerfen. None = unbegrenzt.

        Ein abgelaufener Schluessel faellt auf den Freibetrag zurueck -
        aber nur fuer NEUE Freigaben. Was schon freigegeben ist, bleibt
        es.
        """
        if not self.vorhanden or self.abgelaufen:
            return FREIE_HOSTS
        if self.unbegrenzt:
            return None
        return self.daten.get("h", FREIE_HOSTS)

    @property
    def hinweis(self) -> str:
        """Ein Satz fuer die Oberflaeche. Leer, wenn nichts zu sagen ist."""
        if not self.vorhanden:
            return ""
        if self.abgelaufen:
            return (f"Der Lizenzschluessel ist am "
                    f"{self.gueltig_bis:%d.%m.%Y} abgelaufen. Bereits "
                    f"freigegebene Hosts werden weiter gepatcht; neue "
                    f"Freigaben sind bis {FREIE_HOSTS} Hosts moeglich.")
        uebrig = self.tage_uebrig
        if uebrig is not None and uebrig <= WARNUNG_AB_TAGEN:
            return (f"Der Lizenzschluessel laeuft in {uebrig} Tagen ab "
                    f"({self.gueltig_bis:%d.%m.%Y}).")
        return ""

    def als_dict(self) -> dict:
        return {
            "vorhanden": self.vorhanden,
            "kunde": self.kunde,
            "nummer": self.nummer,
            "hosts": None if self.unbegrenzt else self.daten.get("h"),
            "erlaubte_hosts": self.erlaubte_hosts,
            "gueltig_bis": self.daten.get("exp"),
            "unbefristet": self.unbefristet,
            "abgelaufen": self.abgelaufen,
            "tage_uebrig": self.tage_uebrig,
            "hinweis": self.hinweis,
        }


class LizenzFehler(ValueError):
    """Ein Schluessel, der nicht angenommen werden kann."""


def pruefen(schluessel: str) -> Lizenz:
    """
    Prueft einen Schluessel und gibt ihn zurueck.

    Wirft LizenzFehler, wenn er nicht stimmt. Der Aufrufer behaelt dann
    den bisherigen Zustand bei - ein Tippfehler beim Einfuegen darf nicht
    die halbe Installation kosten.
    """
    schluessel = (schluessel or "").strip()
    if not schluessel:
        raise LizenzFehler("Kein Schluessel angegeben.")
    if not schluessel.startswith(VORSATZ) or "." not in schluessel:
        raise LizenzFehler("Das sieht nicht nach einem CO-37-Schluessel aus.")

    kern, sig = schluessel[len(VORSATZ):].split(".", 1)
    try:
        roh = _unb64(kern)
        daten = json.loads(roh)
    except Exception:  # noqa: BLE001
        raise LizenzFehler("Der Schluessel ist unvollstaendig oder beschaedigt.")

    pub = _oeffentlich()
    if pub is None:
        raise LizenzFehler(
            "In dieser Installation liegt kein oeffentlicher Lizenzschluessel. "
            "Schluessel koennen deshalb nicht geprueft werden.")

    try:
        pub.verify(_unb64(sig), roh)
    except InvalidSignature:
        raise LizenzFehler(
            "Die Signatur stimmt nicht. Der Schluessel wurde veraendert oder "
            "stammt nicht vom Herausgeber.")
    except Exception:  # noqa: BLE001
        raise LizenzFehler("Die Signatur laesst sich nicht pruefen.")

    if daten.get("v") != 1:
        raise LizenzFehler(
            f"Unbekannte Schluesselfassung ({daten.get('v')}). Eine neuere "
            f"Fassung von CO-37 wird benoetigt.")

    # Ausstellungsdatum in der Zukunft: entweder geht die Uhr des Servers
    # falsch, oder jemand hat sie zurueckgestellt. In beiden Faellen ist
    # der Wert nicht brauchbar - aber ein kleiner Vorlauf ist normal,
    # etwa bei Zeitzonenunterschieden.
    ausgestellt = daten.get("iat")
    if ausgestellt:
        # Das Auslesen und die Bewertung getrennt halten. LizenzFehler
        # erbt von ValueError - stuende der Vergleich im try-Block, finge
        # das except die eigene Meldung ab und ersetzte sie durch
        # "unlesbar".
        try:
            wann = date.fromisoformat(ausgestellt)
        except ValueError:
            raise LizenzFehler("Das Ausstellungsdatum ist unlesbar.")
        if wann > date.today() + timedelta(days=2):
            raise LizenzFehler(
                "Das Ausstellungsdatum liegt in der Zukunft. Geht die Uhr "
                "dieses Servers richtig?")

    if daten.get("exp"):
        try:
            date.fromisoformat(daten["exp"])
        except ValueError:
            raise LizenzFehler("Das Ablaufdatum ist unlesbar.")

    # Ein abgelaufener Schluessel wird angenommen, nicht abgewiesen: die
    # Oberflaeche soll anzeigen koennen, fuer wen er ausgestellt war und
    # wann er abgelaufen ist. Auf den Freibetrag faellt er in
    # erlaubte_hosts zurueck.
    return Lizenz(daten)
