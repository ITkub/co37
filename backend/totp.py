"""
CO-37 - Anmeldung in zwei Schritten (TOTP nach RFC 6238).

Warum ueberhaupt: OPS.1.2.5.A17 verlangt Mehr-Faktor-Verfahren fuer
Fernwartungszugaenge, OPS.1.1.7.A6 eine "angemessene" Authentisierung
samt dokumentierter Auswahl. Ein Konto, das auf der ganzen Flotte
Auftraege als SYSTEM anlegen darf, hing bis 0.37.22 an einem Passwort.

----------------------------------------------------------------------
WARUM TOTP UND NICHTS ANDERES
----------------------------------------------------------------------
Die Auswahl ist hier festgehalten, weil OPS.1.1.7.A6 genau das verlangt:
nicht nur eine Methode, sondern eine begruendete.

CO-37 soll in abgeschotteten Netzen laufen - das steht in der ersten
Zeile der README und ist der Grund, warum es keinen Rueckruf zum
Hersteller gibt. Damit scheiden aus:

  - E-Mail-Codes: brauchen einen Mailserver, den es dort nicht gibt.
  - SMS: braucht ein Mobilfunknetz und ist ohnehin das schwaechste
    Verfahren (SIM-Tausch).
  - Ein externer Anmeldedienst: braucht das Internet.
  - WebAuthn/Passkeys: waere kryptografisch das beste Verfahren, setzt
    aber eine HTTPS-Herkunft voraus. CO-37 ist im Auslieferungszustand
    ueber HTTP erreichbar (der Proxy kommt spaeter dazu), und ein
    Verfahren, das bei der Ersteinrichtung nicht funktioniert, wird nicht
    eingerichtet. Bleibt als spaeterer Zusatz sinnvoll.

TOTP braucht nichts ausser einer synchronen Uhr - und die verlangt
OPS.1.1.7.A3 ohnehin.

----------------------------------------------------------------------
KEINE NEUE ABHAENGIGKEIT
----------------------------------------------------------------------
Absichtlich ohne pyotp. Das Verfahren ist ein HMAC ueber einen Zaehler,
die Umsetzung passt in vierzig Zeilen, und jede zusaetzliche
Abhaengigkeit ist eine zusaetzliche Lieferkette, die gepflegt,
gehashed (F-14) und auf CVEs geprueft werden muss. Fuer vierzig Zeilen
lohnt das nicht.

----------------------------------------------------------------------
WAS HIER NICHT DRIN STEHT
----------------------------------------------------------------------
Der Schutz gegen Wiederverwendung eines Codes und die Drosselung der
Eingabe stehen NICHT hier, sondern im Backend: beides braucht einen
Zustand je Konto, und der gehoert in die Datenbank, nicht in ein Modul
ohne Gedaechtnis. Dieses Modul rechnet nur.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse
from typing import Optional

# Schrittweite in Sekunden. 30 ist der Wert, den jede Authenticator-App
# als Vorgabe nimmt; abweichen hiesse, dass Standard-Apps nicht
# funktionieren.
SCHRITT = 30

# Stellen des Codes. Sechs ist die Vorgabe von RFC 6238 und das, was
# Apps anzeigen.
STELLEN = 6

# Wie viele Schritte vor und nach dem aktuellen noch gelten. 1 bedeutet
# ein Fenster von 90 Sekunden.
#
# Das ist ein Tausch: mehr Toleranz gegen schiefe Uhren, dafuer ein
# groesseres Zeitfenster fuer einen abgefangenen Code. Ein Schritt ist
# die uebliche Wahl und deckt die Uhrabweichung ab, die ein Telefon
# ueblicherweise hat.
TOLERANZ = 1

# Laenge des Geheimnisses in Byte. 20 Byte = 160 Bit, so viel verlangt
# RFC 4226 als Mindestmass fuer HMAC-SHA1.
GEHEIMNIS_BYTES = 20

# Wiederherstellungscodes. 10 Stueck, je 20 Zeichen aus einem Alphabet
# von 32 - das sind 100 Bit Zufall je Code.
#
# Diese Zahl ist der Grund, warum die Codes mit SHA-256 statt scrypt
# abgelegt werden: bei 100 Bit gibt es nichts zu raten, ein langsames
# Verfahren schuetzt hier vor nichts. Bei einem Passwort waere es
# umgekehrt - das hat vielleicht 30 Bit und braucht scrypt.
#
# Ausserdem praktisch: beim Einloesen muss gegen alle zehn geprueft
# werden. Mit scrypt waeren das zehnmal 196 ms.
RETTUNG_ANZAHL = 10
RETTUNG_LAENGE = 20
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne I, O, 0, 1


def geheimnis_erzeugen() -> str:
    """Neues Geheimnis, base32 wie von den Apps erwartet."""
    return base64.b32encode(secrets.token_bytes(GEHEIMNIS_BYTES)).decode("ascii")


def code(geheimnis: str, zeitpunkt: float = None, versatz: int = 0) -> str:
    """
    Den Code fuer einen Zeitpunkt rechnen.

    versatz verschiebt um ganze Schritte - gebraucht fuer die Toleranz
    und fuer die Pruefreihe, die damit die Zeit vorspult.
    """
    zaehler = int((zeitpunkt if zeitpunkt is not None else time.time())
                  // SCHRITT) + versatz
    # base32 ohne Auffuellzeichen kommt vor, wenn jemand das Geheimnis
    # von Hand abtippt - deshalb hier auffuellen statt scheitern.
    roh = base64.b32decode(geheimnis + "=" * (-len(geheimnis) % 8), casefold=True)
    hs = hmac.new(roh, struct.pack(">Q", zaehler), hashlib.sha1).digest()
    # Dynamic truncation, RFC 4226 Abschnitt 5.3.
    versch = hs[-1] & 0x0F
    zahl = struct.unpack(">I", hs[versch:versch + 4])[0] & 0x7FFFFFFF
    return str(zahl % (10 ** STELLEN)).zfill(STELLEN)


def passt(geheimnis: str, eingabe: str,
          zeitpunkt: float = None) -> Optional[int]:
    """
    Prueft eine Eingabe. Gibt den passenden Schritt zurueck, sonst None.

    Der Schritt wird zurueckgegeben und nicht nur True/False, weil der
    Aufrufer ihn speichern MUSS: ohne das laesst sich derselbe Code
    innerhalb seiner Gueltigkeit mehrfach verwenden. Wer eine Anmeldung
    mitliest, haette damit 30 bis 90 Sekunden Zeit, sie zu wiederholen.

    Der Vergleich laeuft ueber compare_digest - eine Zeichenkette mit ==
    zu vergleichen verraet ueber die Laufzeit, wie viele Stellen stimmen.
    Bei sechs Stellen ist das kaum ausnutzbar, aber es kostet nichts,
    es richtig zu machen.
    """
    eingabe = (eingabe or "").strip().replace(" ", "")
    if not eingabe.isdigit() or len(eingabe) != STELLEN:
        return None
    for versatz in range(-TOLERANZ, TOLERANZ + 1):
        if hmac.compare_digest(code(geheimnis, zeitpunkt, versatz), eingabe):
            zaehler = int((zeitpunkt if zeitpunkt is not None else time.time())
                          // SCHRITT) + versatz
            return zaehler
    return None


def einrichtungs_adresse(geheimnis: str, konto: str, anlage: str) -> str:
    """
    otpauth-Adresse fuer die Authenticator-App.

    Konto und Anlagenname werden kodiert - ein Leerzeichen oder ein
    Doppelpunkt im Namen zerlegt sonst die Adresse.
    """
    kennung = urllib.parse.quote(f"{anlage}:{konto}", safe="")
    frage = urllib.parse.urlencode({
        "secret": geheimnis, "issuer": anlage,
        "algorithm": "SHA1", "digits": STELLEN, "period": SCHRITT})
    return f"otpauth://totp/{kennung}?{frage}"


# ======================================================================
# Wiederherstellungscodes
# ======================================================================
def rettungscodes() -> list[str]:
    """Zehn Codes im Klartext. Werden genau einmal angezeigt."""
    codes = []
    for _ in range(RETTUNG_ANZAHL):
        roh = "".join(secrets.choice(ALPHABET) for _ in range(RETTUNG_LAENGE))
        # In Vierergruppen, damit man sie abschreiben kann, ohne sich zu
        # verzaehlen.
        codes.append("-".join(roh[i:i + 4] for i in range(0, len(roh), 4)))
    return codes


def rettung_hash(code_text: str) -> str:
    """
    SHA-256 ueber den normalisierten Code.

    Warum kein scrypt: siehe die Begruendung bei RETTUNG_ANZAHL. Bei 100
    Bit Zufall gibt es nichts zu raten.
    """
    sauber = (code_text or "").upper().replace("-", "").replace(" ", "")
    return hashlib.sha256(sauber.encode("ascii", "ignore")).hexdigest()


def rettung_passt(eingabe: str, hashes: list[str]) -> Optional[str]:
    """
    Sucht den passenden Hash. Gibt ihn zurueck, damit der Aufrufer ihn
    entfernen kann - ein Wiederherstellungscode gilt genau einmal.
    """
    gesucht = rettung_hash(eingabe)
    for h in hashes:
        if hmac.compare_digest(h, gesucht):
            return h
    return None
