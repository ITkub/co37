#!/usr/bin/env python3
"""
CO-37 - Lizenzschluessel erzeugen.

Nur fuer den Lizenzgeber. Gehoert NICHT auf den Server und nicht in das
Auslieferungspaket - build_release.sh nimmt tools/ bewusst nicht mit.

    python make-license.py --init
    python make-license.py --kunde "Firma Meier" --hosts 100 --jahre 3
    python make-license.py --kunde "ITkub intern" --hosts unbegrenzt --unbefristet
    python make-license.py --sicherung
    python make-license.py --pruefen sicherung.txt
    python make-license.py --zeigen CO37-...

Verfahren: Ed25519. Der private Schluessel bleibt beim Lizenzgeber, in
CO-37 steckt nur der oeffentliche Gegenpart. Damit lassen sich Schluessel
pruefen, aber nicht erzeugen - auch nicht von jemandem, der den gesamten
Quelltext hat.

Kein Rueckruf zum Lizenzgeber, keine Internetverbindung noetig. CO-37
soll in abgeschotteten Netzen laufen koennen.
"""

import argparse
import base64
import csv
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
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

# Der private Schluessel liegt ausserhalb des Projektverzeichnisses.
# Innerhalb waere er nur eine Unachtsamkeit von einem 'git add -A'
# entfernt - und ein einmal eingecheckter Schluessel ist praktisch nicht
# mehr aus der Historie zu entfernen.
HOME = Path(os.environ.get("CO37_LICENSE_HOME", Path.home() / ".co37"))
PRIVAT = HOME / "license-private.pem"
REGISTER = HOME / "lizenzen.csv"

# Der oeffentliche Teil wird ausgeliefert und gehoert ins Repository.
PROJEKT = Path(__file__).resolve().parent.parent
OEFFENTLICH = PROJEKT / "backend" / "license_key.pub"

VORSATZ = "CO37-"


# ======================================================================
# Kodierung
# ======================================================================
# base64url ohne Auffuellzeichen: der Schluessel wandert per Mail und
# wird von Hand eingefuegt. '+' und '/' werden dabei oft zerstoert, '='
# am Ende laesst manches Formular weg.
def _b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _nutzdaten(daten: dict) -> bytes:
    """
    Kanonische Form der Nutzdaten.

    Sortierte Schluessel und keine Leerzeichen: die Signatur gilt fuer
    genau diese Bytes. Wuerde die Reihenfolge schwanken, waere ein
    erzeugter Schluessel spaeter nicht mehr pruefbar.
    """
    return json.dumps(daten, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# ======================================================================
# Schluesselpaar
# ======================================================================
def lade_privat() -> Ed25519PrivateKey:
    if not PRIVAT.is_file():
        sys.exit(f"Kein privater Schluessel unter {PRIVAT}.\n"
                 f"Erst erzeugen:  python make-license.py --init")
    return serialization.load_pem_private_key(PRIVAT.read_bytes(), password=None)


def init():
    if PRIVAT.is_file():
        # Ein versehentliches Ueberschreiben waere der Totalverlust: alle
        # ausgestellten Schluessel wuerden ungueltig.
        sys.exit(f"Es gibt bereits einen privaten Schluessel:\n"
                 f"    {PRIVAT}\n\n"
                 f"Er wird nicht ueberschrieben. Soll wirklich ein neues Paar\n"
                 f"entstehen, die Datei vorher von Hand wegsichern und loeschen -\n"
                 f"ALLE bisher ausgestellten Schluessel werden damit ungueltig.")

    HOME.mkdir(parents=True, exist_ok=True)
    privat = Ed25519PrivateKey.generate()

    pem = privat.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    PRIVAT.write_bytes(pem)
    try:
        PRIVAT.chmod(0o600)
    except OSError:
        pass

    roh = privat.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    OEFFENTLICH.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" erzwingen: unter Windows wuerde sonst CRLF entstehen
    # und die Datei je nach Rechner unterschiedlich aussehen.
    with open(OEFFENTLICH, "w", encoding="ascii", newline="\n") as fh:
        fh.write(_b64(roh) + "\n")

    print(f"""
Schluesselpaar erzeugt.

  Privat:      {PRIVAT}
  Oeffentlich: {OEFFENTLICH}

DER PRIVATE SCHLUESSEL IST DAS EINZIGE, WAS NICHT ERSETZBAR IST.

Geht er verloren, koennen keine neuen Schluessel mehr ausgestellt werden.
Bereits verkaufte laufen weiter - aber jeder Neukunde und jede
Verlaengerung waere unmoeglich, bis ein neues Paar erzeugt ist. Dann
muessten ALLE bestehenden Kunden neue Schluessel bekommen und eine neue
Fassung veroeffentlicht werden.

Jetzt sichern, an mindestens drei Orten in unterschiedlicher Form:

  1. Passwortmanager, als sicherer Notiz-Eintrag
  2. Ausgedruckt auf Papier    ->  python make-license.py --sicherung
  3. Verschluesselt ausser Haus

NICHT auf dem CO-37-Server und nicht im Homelab-Backup - beides faellt
bei demselben Ereignis mit aus, gegen das die Sicherung schuetzen soll.
NIEMALS in ein Git-Repository, auch nicht in ein privates.

Die Sicherung anschliessend pruefen:

    python make-license.py --pruefen <datei>

Eine ungeprueffte Sicherung ist keine Sicherung.
""".strip())

    if os.name == "nt":
        print("\nHinweis Windows: Dateirechte lassen sich hier nicht wie unter\n"
              "Linux setzen. Der Schluessel liegt unter deinem Benutzerprofil\n"
              "und ist fuer andere Konten auf diesem Rechner lesbar, falls es\n"
              "welche gibt.")


# ======================================================================
# Schluessel ausstellen
# ======================================================================
def ausstellen(kunde: str, hosts: int, jahre, unbefristet: bool) -> str:
    privat = lade_privat()
    heute = date.today()

    daten = {
        "v": 1,
        "k": kunde,
        # 0 bedeutet unbegrenzt - so muss kein Sonderwert erfunden werden.
        "h": hosts,
        "iat": heute.isoformat(),
        "exp": None if unbefristet else (heute + timedelta(days=365 * jahre)).isoformat(),
        "nr": naechste_nummer(),
    }

    roh = _nutzdaten(daten)
    schluessel = f"{VORSATZ}{_b64(roh)}.{_b64(privat.sign(roh))}"
    eintragen(daten, schluessel)
    return schluessel


def naechste_nummer() -> int:
    """Fortlaufende Nummer aus dem Register."""
    if not REGISTER.is_file():
        return 1
    # Trennzeichen wie beim Schreiben. Ohne die Angabe liest der Leser
    # die ganze Zeile als ein Feld, findet die Spalte 'nr' nie und faengt
    # jedes Mal wieder bei 1 an - alle Schluessel haetten dieselbe Nummer.
    with open(REGISTER, encoding="utf-8", newline="") as fh:
        nummern = [int(z["nr"]) for z in csv.DictReader(fh, delimiter=";")
                   if z.get("nr", "").strip().isdigit()]
    return max(nummern, default=0) + 1


def eintragen(daten: dict, schluessel: str):
    """
    Jeden ausgestellten Schluessel festhalten.

    Ohne das weiss man nach zwei Jahren nicht mehr, wem was verkauft
    wurde - und kann einen Kunden, der seinen Schluessel verlegt hat,
    nicht bedienen.
    """
    HOME.mkdir(parents=True, exist_ok=True)
    neu = not REGISTER.is_file()
    with open(REGISTER, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        if neu:
            w.writerow(["nr", "ausgestellt", "kunde", "hosts", "gueltig_bis",
                        "schluessel"])
        w.writerow([
            daten["nr"], daten["iat"], daten["k"],
            "unbegrenzt" if daten["h"] == 0 else daten["h"],
            daten["exp"] or "unbefristet",
            schluessel,
        ])


# ======================================================================
# Pruefen und Anzeigen
# ======================================================================
def lade_oeffentlich() -> Ed25519PublicKey:
    if not OEFFENTLICH.is_file():
        sys.exit(f"Kein oeffentlicher Schluessel unter {OEFFENTLICH}.")
    return Ed25519PublicKey.from_public_bytes(
        _unb64(OEFFENTLICH.read_text(encoding="ascii").strip()))


def zeigen(schluessel: str):
    """Zeigt, was in einem Schluessel steht - fuer Rueckfragen von Kunden."""
    schluessel = schluessel.strip()
    if not schluessel.startswith(VORSATZ) or "." not in schluessel:
        sys.exit("Das sieht nicht nach einem CO-37-Schluessel aus.")

    kern, sig = schluessel[len(VORSATZ):].split(".", 1)
    try:
        roh = _unb64(kern)
        daten = json.loads(roh)
    except Exception:
        sys.exit("Der Schluessel ist beschaedigt.")

    try:
        lade_oeffentlich().verify(_unb64(sig), roh)
        echt = "gueltig"
    except InvalidSignature:
        echt = "SIGNATUR FALSCH - gefaelscht oder veraendert"
    except SystemExit:
        raise
    except Exception as exc:
        echt = f"nicht pruefbar: {exc}"

    abgelaufen = ""
    if daten.get("exp"):
        rest = (date.fromisoformat(daten["exp"]) - date.today()).days
        abgelaufen = f"  ({rest} Tage)" if rest >= 0 else "  (ABGELAUFEN)"

    print(f"  Nummer:      {daten.get('nr')}")
    print(f"  Kunde:       {daten.get('k')}")
    print(f"  Hosts:       {'unbegrenzt' if daten.get('h') == 0 else daten.get('h')}")
    print(f"  Ausgestellt: {daten.get('iat')}")
    print(f"  Gueltig bis: {daten.get('exp') or 'unbefristet'}{abgelaufen}")
    print(f"  Signatur:    {echt}")


def sicherung():
    """Private Datei in druckfreundlicher Form, mit Pruefsumme."""
    roh = lade_privat().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")

    pruefsumme = sha256(roh.encode("ascii")).hexdigest()[:16]
    print("=" * 68)
    print("CO-37 - PRIVATER LIZENZSCHLUESSEL")
    print("Erstellt am", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print()
    print("Sicher aufbewahren. Wer diesen Schluessel hat, kann beliebige")
    print("Lizenzschluessel ausstellen.")
    print("=" * 68)
    print()
    print(roh.rstrip())
    print()
    print(f"Pruefsumme (SHA-256, erste 16 Zeichen): {pruefsumme}")
    print()
    print("Zum Pruefen der Sicherung diesen Block in eine Datei kopieren,")
    print("dann:   python make-license.py --pruefen <datei>")
    print("=" * 68)


def pruefen(pfad: str):
    """
    Prueft, ob eine Sicherung zum ausgelieferten oeffentlichen Teil passt.

    Eine Sicherung, die nie geprueft wurde, ist keine Sicherung - das
    gilt hier genauso wie bei virtuellen Maschinen.
    """
    text = Path(pfad).read_text(encoding="utf-8", errors="replace")
    anfang = text.find("-----BEGIN PRIVATE KEY-----")
    ende = text.find("-----END PRIVATE KEY-----")
    if anfang < 0 or ende < 0:
        sys.exit("In der Datei steht kein privater Schluessel "
                 "(-----BEGIN PRIVATE KEY-----).")
    pem = text[anfang:ende + len("-----END PRIVATE KEY-----")] + "\n"

    try:
        privat = serialization.load_pem_private_key(pem.encode("ascii"),
                                                    password=None)
    except Exception as exc:
        sys.exit(f"Der Schluessel laesst sich nicht lesen: {exc}\n"
                 f"Beim Abtippen vertippt? Die Pruefsumme haette es gezeigt.")

    aus_sicherung = privat.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    try:
        ausgeliefert = _unb64(OEFFENTLICH.read_text(encoding="ascii").strip())
    except OSError:
        sys.exit(f"Kein oeffentlicher Schluessel unter {OEFFENTLICH} zum Vergleich.")

    if aus_sicherung != ausgeliefert:
        sys.exit("Die Sicherung passt NICHT zum ausgelieferten oeffentlichen\n"
                 "Schluessel. Entweder gehoert sie zu einem anderen Paar, oder\n"
                 "sie ist beschaedigt.")

    # Gegenprobe: einen Schluessel signieren und pruefen
    probe = b"probe"
    lade_oeffentlich().verify(privat.sign(probe), probe)
    print("Die Sicherung ist vollstaendig und passt zum ausgelieferten\n"
          "oeffentlichen Schluessel. Damit liessen sich im Ernstfall wieder\n"
          "Lizenzschluessel ausstellen.")


# ======================================================================
def main():
    p = argparse.ArgumentParser(
        description="CO-37 Lizenzschluessel erzeugen und pruefen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--init", action="store_true",
                   help="Schluesselpaar erzeugen (einmalig)")
    p.add_argument("--kunde", help="Name des Kunden, steht im Schluessel")
    p.add_argument("--hosts",
                   help="Anzahl Hosts, oder 'unbegrenzt'")
    p.add_argument("--jahre", type=int, choices=(1, 2, 3),
                   help="Laufzeit in Jahren")
    p.add_argument("--unbefristet", action="store_true",
                   help="ohne Ablaufdatum")
    p.add_argument("--sicherung", action="store_true",
                   help="privaten Schluessel druckfreundlich ausgeben")
    p.add_argument("--pruefen", metavar="DATEI",
                   help="Sicherung gegen den oeffentlichen Schluessel pruefen")
    p.add_argument("--zeigen", metavar="SCHLUESSEL",
                   help="Inhalt eines Schluessels anzeigen")
    a = p.parse_args()

    if a.init:
        return init()
    if a.sicherung:
        return sicherung()
    if a.pruefen:
        return pruefen(a.pruefen)
    if a.zeigen:
        return zeigen(a.zeigen)

    if not a.kunde:
        p.print_help()
        sys.exit(1)

    if a.hosts is None:
        sys.exit("--hosts fehlt (Zahl oder 'unbegrenzt').")
    if a.hosts.lower() in ("unbegrenzt", "unlimited", "0"):
        hosts = 0
    else:
        try:
            hosts = int(a.hosts)
        except ValueError:
            sys.exit("--hosts muss eine Zahl sein oder 'unbegrenzt'.")
        if hosts < 1:
            sys.exit("--hosts muss mindestens 1 sein.")

    if not a.unbefristet and not a.jahre:
        sys.exit("Entweder --jahre 1|2|3 oder --unbefristet angeben.")
    if a.unbefristet and a.jahre:
        sys.exit("--jahre und --unbefristet schliessen sich aus.")

    if a.unbefristet and hosts == 0:
        # Der eine Schluessel, der alles aushebelt, wenn er abhanden kommt:
        # er laeuft nie ab, es gibt also keine natuerliche
        # Schadensbegrenzung.
        print("Achtung: unbegrenzt UND unbefristet. Dieser Schluessel laeuft\n"
              "nie ab und hat kein Host-Limit. Wenn er abhanden kommt, gibt es\n"
              "keinen Weg, ihn zurueckzunehmen.\n")
        if input("Trotzdem ausstellen? [ja/nein] ").strip().lower() != "ja":
            sys.exit("Abgebrochen.")

    schluessel = ausstellen(a.kunde, hosts, a.jahre, a.unbefristet)
    print()
    zeigen(schluessel)
    print()
    print("Diesen Schluessel an den Kunden geben:")
    print()
    print(f"  {schluessel}")
    print()
    print(f"Im Register vermerkt: {REGISTER}")


if __name__ == "__main__":
    main()
