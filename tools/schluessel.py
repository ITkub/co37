"""
CO-37 - private Schluesseldateien schuetzen.

Gemeinsame Sache von tools/sign-release.py und tools/make-license.py. Bis
0.37.20 lagen beide privaten Schluessel unverschluesselt auf der Platte:

    ~/.co37/release-private.pem    signiert Update-Pakete
    ~/.co37/license-private.pem    stellt Lizenzschluessel aus

Der erste wiegt schwerer. Ein Update-Paket wird vom Watcher als root
ausgepackt - wer damit signieren kann, fuehrt Code als root auf jedem
Kundensystem aus. Rechte und Anlegen sind seit F-35 in Ordnung (0600 von
Anfang an, O_EXCL); was fehlte, war der Schutz der Datei selbst. Eine
gestohlene Platte, ein verlorenes Notebook, ein Sicherungsband beim
falschen Anbieter - in jedem dieser Faelle war der Schluessel weg.

----------------------------------------------------------------------
WARUM NICHT EINFACH BestAvailableEncryption
----------------------------------------------------------------------
Der naheliegende Weg waere gewesen:

    privat.private_bytes(..., serialization.BestAvailableEncryption(pw))

Nachgemessen am 2026-09-05 mit cryptography 50.0.1, was dabei
herauskommt (openssl asn1parse ueber die erzeugte Datei):

    PBES2, PBKDF2 mit hmacWithSHA256, INTEGER 0x0800, AES-256-CBC

0x0800 sind **2048 Durchlaeufe**. Das ist die PKCS#8-Vorgabe von OpenSSL
aus den Neunzigern. Zum Vergleich: fuer PBKDF2-HMAC-SHA256 werden heute
sechshunderttausend empfohlen. Mit 2048 Durchlaufen faellt eine
menschlich gewaehlte Passphrase auf einer Grafikkarte in Minuten - der
Schutz waere ein Gefuehl gewesen, kein Schutz.

Anheben laesst es sich in dieser Fassung nicht: encryption_builder() mit
kdf_rounds() gibt es nur fuer OpenSSH und PKCS12, nicht fuer PKCS8
("encryption_builder only supported with PrivateFormat.OpenSSH and
PrivateFormat.PKCS12"). Auch nachgemessen, nicht vermutet.

----------------------------------------------------------------------
WAS STATTDESSEN HIER STEHT
----------------------------------------------------------------------
Die Passphrase, mit der PKCS8 verschluesselt wird, ist nicht die des
Menschen, sondern aus ihr abgeleitet:

    abgeleitet = base64( scrypt(passphrase, salt, N=2^14, r=8, p=5) )

Dieselben Parameter, mit denen das Backend seit 0.37.15 Benutzer-
passwoerter behandelt, und aus demselben Grund gewaehlt (siehe SCRYPT_N
in backend/main.py). Gemessen: 196 ms je Versuch, 128 MiB Speicherbedarf.
Der Angriff auf die gestohlene Datei kostet damit nicht mehr Mikrosekunden
je Versuch, sondern eine Fuenftelsekunde und Arbeitsspeicher - und
scrypt ist speicherhart, die Grafikkarte verliert ihren Vorteil.

Der Behaelter bleibt Standard-PKCS8. Die Datei laesst sich weiterhin von
openssl lesen, wenn man die abgeleitete Passphrase kennt - und wie sie
entsteht, steht in der Datei selbst:

    # CO-37 scrypt n=16384 r=8 p=5 salt=<hex>
    -----BEGIN ENCRYPTED PRIVATE KEY-----

Eine Kommentarzeile vor dem PEM-Block wird von load_pem_private_key
gelesen und ueberlesen (nachgeprueft). Sie ist da, damit dieser
Schluessel auch dann noch zu retten ist, wenn es dieses Werkzeug nicht
mehr gibt - ein Schluessel, dessen Wiederherstellung an einem Skript
haengt, ist schlecht gesichert.

----------------------------------------------------------------------
WAS DAS NICHT LEISTET
----------------------------------------------------------------------
Nichts gegen Schadsoftware, die mitlaeuft, waehrend gebaut wird: dann
liegt die Passphrase im Speicher und der entschluesselte Schluessel
ebenso. Der Schutz gilt der ruhenden Datei - gestohlenes Notebook,
abgeflossene Sicherung, falsch geteiltes Verzeichnis.
"""
import base64
import getpass
import hashlib
import os
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

# Dieselben Werte wie SCRYPT_N/R/P im Backend. Wer sie hier aendert, macht
# bestehende Dateien unlesbar - deshalb stehen sie im Kopf JEDER Datei
# und werden von dort gelesen, nicht von hier.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 5
SCRYPT_MAXMEM = 256 * 1024 * 1024

KOPF_VORSATZ = "# CO-37 scrypt "

# Untergrenze fuer die Passphrase. Zwoelf wie beim Anmeldepasswort des
# Backends, und aus demselben Grund keine Zeichenklassenregeln: Laenge
# wirkt staerker, Sonderzeichenzwang treibt Menschen zu "Passwort1!".
#
# Empfohlen sind mehrere zufaellige Woerter. Bei diesem Schluessel haengt
# alles daran: scrypt macht jeden Versuch teuer, aber gegen "sommer2026"
# hilft auch das nicht ewig.
MIN_LEN = 12


def ist_verschluesselt(roh: bytes) -> bool:
    """Am PEM-Kopf ablesbar, ohne die Datei zu entschluesseln."""
    return b"ENCRYPTED PRIVATE KEY" in roh


def _kopfzeile_lesen(roh: bytes) -> dict:
    """
    Die scrypt-Parameter aus der Kommentarzeile holen.

    Fehlt sie, ist die Datei mit BestAvailableEncryption entstanden - dann
    ist die Passphrase des Menschen unmittelbar die des Behaelters. Diesen
    Fall gibt es bei uns nicht, aber eine von Hand erzeugte Datei soll
    nicht am Kopf scheitern.
    """
    erste = roh.split(b"\n", 1)[0].decode("ascii", "replace")
    if not erste.startswith(KOPF_VORSATZ):
        return {}
    werte = {}
    for stueck in erste[len(KOPF_VORSATZ):].split():
        if "=" in stueck:
            name, wert = stueck.split("=", 1)
            werte[name] = wert
    return werte


def _ableiten(passphrase: bytes, salt: bytes,
              n: int, r: int, p: int) -> bytes:
    return base64.urlsafe_b64encode(hashlib.scrypt(
        passphrase, salt=salt, n=n, r=r, p=p,
        maxmem=SCRYPT_MAXMEM, dklen=32))


def _kopf_pruefen(kopf: dict, pfad=None):
    """
    Die Angaben aus der Kopfzeile auf Brauchbarkeit pruefen.

    Ohne das kam bei einer beschaedigten Zeile ein nackter Rueckverfolg
    heraus - 'KeyError: salt', 'invalid literal for int()' (F-68 der
    Pruefung vom 2026-09-05). Das ist der denkbar schlechteste Zeitpunkt
    fuer eine unverstaendliche Meldung: wer hier steht, versucht gerade,
    an den wertvollsten Schluessel zu kommen, den er besitzt, vermutlich
    unter Druck und womoeglich nach einem Plattenschaden.

    Die Meldung nennt deshalb die Datei, die kaputte Angabe und den Weg
    zurueck.
    """
    wo = f"\n    {pfad}" if pfad else ""
    for name in ("n", "r", "p", "salt"):
        if name not in kopf:
            sys.exit(f"Die Kopfzeile der Schluesseldatei ist unvollstaendig - "
                     f"'{name}' fehlt.{wo}\n\n"
                     f"Erwartet wird eine erste Zeile der Form\n"
                     f"    {KOPF_VORSATZ}n=16384 r=8 p=5 salt=<32 Hexzeichen>\n\n"
                     f"Ist die Zeile verlorengegangen, hilft die "
                     f"Papiersicherung (--sicherung).")
    try:
        n, r, p = int(kopf["n"]), int(kopf["r"]), int(kopf["p"])
    except ValueError:
        sys.exit(f"Die Kopfzeile der Schluesseldatei enthaelt keine Zahlen "
                 f"fuer n, r, p.{wo}")
    # scrypt verlangt eine Zweierpotenz. Die Obergrenzen sind da, damit
    # eine verfaelschte Zeile nicht in einen Speicheranfall laeuft, bevor
    # ueberhaupt jemand etwas merkt.
    if n < 2 ** 10 or n > 2 ** 20 or n & (n - 1):
        sys.exit(f"Unbrauchbares n in der Kopfzeile: {n}.{wo}")
    if not (1 <= r <= 64) or not (1 <= p <= 64):
        sys.exit(f"Unbrauchbares r/p in der Kopfzeile: r={r}, p={p}.{wo}")
    try:
        salt = bytes.fromhex(kopf["salt"])
    except ValueError:
        sys.exit(f"Das Salz in der Kopfzeile ist keine Hexzahl: "
                 f"{kopf['salt']!r}.{wo}")
    if not 8 <= len(salt) <= 64:
        sys.exit(f"Das Salz in der Kopfzeile hat eine unbrauchbare Laenge: "
                 f"{len(salt)} Byte.{wo}")
    return salt, n, r, p


def behaelter_passwort(roh: bytes, passphrase: bytes, pfad=None) -> bytes:
    """Aus der Passphrase des Menschen die des PKCS8-Behaelters machen."""
    kopf = _kopfzeile_lesen(roh)
    if not kopf:
        return passphrase
    salt, n, r, p = _kopf_pruefen(kopf, pfad)
    return _ableiten(passphrase, salt, n, r, p)


def _eingabe(text: str) -> str:
    """
    Eine Zeile verdeckt einlesen.

    Eigene Funktion, damit es genau eine Stelle gibt, an der auf eine
    Tastatur zugegriffen wird - die Pruefreihe haengt sich hier ein.

    getpass liest unter POSIX von /dev/tty und unter Windows ueber
    msvcrt - also auch dann, wenn stdout und stderr abgefangen sind. Das
    ist der Fall, in dem build_release.py dieses Werkzeug aufruft; dort
    wird die Passphrase aber EINMAL im Elternprozess erfragt und ueber die
    Umgebung des Kindprozesses weitergereicht, damit nicht dreimal je Bau
    gefragt wird.
    """
    if not sys.stdin.isatty():
        sys.exit("Hier wird eine Passphrase gebraucht, aber es sitzt "
                 "niemand an der Tastatur (kein Terminal).")
    return getpass.getpass(text)


def passphrase_fragen(zweck: str, bestaetigen: bool = False) -> bytes:
    """Passphrase von der Tastatur holen, auf Wunsch mit Bestaetigung."""
    while True:
        eins = _eingabe(f"Passphrase {zweck}: ").encode("utf-8")
        if not bestaetigen:
            return eins
        if len(eins) < MIN_LEN:
            print(f"Zu kurz. Mindestens {MIN_LEN} Zeichen - besser mehrere "
                  f"zufaellige Woerter.")
            continue
        zwei = _eingabe("Noch einmal: ").encode("utf-8")
        if eins != zwei:
            print("Die beiden Eingaben sind nicht gleich.")
            continue
        return eins


def passphrase_holen(umgebung: str, zweck: str) -> bytes:
    """
    Erst die Umgebung, dann die Tastatur.

    Die Umgebungsvariable ist NICHT dafuer gedacht, dass ein Mensch sie
    setzt - dann stuende die Passphrase in der Shell-Historie. Sie ist der
    Weg, auf dem build_release.py die einmal erfragte Passphrase an seine
    Kindprozesse weiterreicht.
    """
    aus_umgebung = os.environ.get(umgebung)
    if aus_umgebung:
        return aus_umgebung.encode("utf-8")
    return passphrase_fragen(zweck)


def schluessel_schreiben(ziel: Path, pem: bytes, ersetzen: bool = False):
    """
    Privaten Schluessel mit 0600 anlegen - von Anfang an (F-35 der
    Pruefung vom 2026-08-31).

    Bis 0.37.9 stand hier write_bytes() und danach chmod(0600). Zwischen
    beiden lag die Datei mit der Umask-Vorgabe auf der Platte, ueblich
    0644, in einem Verzeichnis mit 0755. Wer in diesem Fenster liest, hat
    den Schluessel.

    O_EXCL: eine vorhandene Datei wird nicht ueberschrieben. Beim
    Verschluesseln einer bestehenden Datei ist genau das aber gewollt -
    dann wird ueber eine Nebendatei gegangen und erst nach der Gegenprobe
    umbenannt (ersetzen=True).
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ziel.parent, 0o700)
    except OSError:
        # Unter Windows wirkungslos.
        pass
    if ersetzen:
        neben = ziel.with_name(ziel.name + ".neu")
        neben.unlink(missing_ok=True)
        fd = os.open(neben, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        os.replace(neben, ziel)
        return
    fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)


def laden(pfad: Path, umgebung: str, zweck: str):
    """
    Privaten Schluessel lesen, verschluesselt oder nicht.

    Eine falsche Passphrase kommt aus cryptography als ValueError heraus,
    und zwar mit demselben Wortlaut wie eine kaputte Datei. Deshalb hier
    eine eigene, brauchbare Meldung.
    """
    roh = pfad.read_bytes()
    if not ist_verschluesselt(roh):
        return serialization.load_pem_private_key(roh, password=None)
    pw = behaelter_passwort(roh, passphrase_holen(umgebung, zweck), pfad)
    try:
        return serialization.load_pem_private_key(roh, password=pw)
    except ValueError:
        sys.exit("Die Passphrase passt nicht zu dieser Schluesseldatei.")


def als_pem(privat, passphrase: bytes = None) -> bytes:
    """
    PKCS8-PEM erzeugen, mit oder ohne Passphrase.

    Mit Passphrase entsteht ein neues Salz und die Kommentarzeile davor -
    siehe die Begruendung im Kopf dieser Datei.
    """
    if not passphrase:
        return privat.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
    salt = secrets.token_bytes(16)
    abgeleitet = _ableiten(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    pem = privat.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(abgeleitet))
    kopf = (f"{KOPF_VORSATZ}n={SCRYPT_N} r={SCRYPT_R} p={SCRYPT_P} "
            f"salt={salt.hex()}\n").encode("ascii")
    return kopf + pem


def verschluesseln(pfad: Path, umgebung: str, zweck: str, name: str):
    """
    Eine bestehende Schluesseldatei verschluesseln oder die Passphrase
    wechseln.

    Reihenfolge mit Absicht: erst laden (bei einer bereits verschluesselten
    Datei kostet das die alte Passphrase), dann neue Passphrase zweimal
    erfragen, dann in eine Nebendatei schreiben, dann GEGENPROBE - erst
    wenn sich die neue Datei mit der neuen Passphrase oeffnen laesst, wird
    umbenannt. Ein halb geschriebener Schluessel waere der Totalverlust.
    """
    if not pfad.is_file():
        sys.exit(f"Keine Schluesseldatei unter {pfad}.")

    vorher = pfad.read_bytes()
    print(f"""
{'=' * 68}
{name} verschluesseln
{'=' * 68}

  Datei: {pfad}
  Stand: {'bereits verschluesselt' if ist_verschluesselt(vorher)
           else 'UNVERSCHLUESSELT'}

WICHTIG - vorher sichern. Ist die Passphrase weg, ist der Schluessel weg,
und der oeffentliche Teil ist bei jedem Kunden ausgeliefert und laesst
sich nicht ersetzen.
{'=' * 68}
""".strip())
    print()

    privat = laden(pfad, umgebung, f"({zweck}, die BISHERIGE)")
    neu = passphrase_fragen(f"({zweck}, die NEUE)", bestaetigen=True)

    schluessel_schreiben(pfad, als_pem(privat, neu), ersetzen=True)

    # Gegenprobe mit der frisch geschriebenen Datei, ohne Umgebung: sie
    # muss sich mit genau dieser Passphrase oeffnen lassen.
    roh = pfad.read_bytes()
    try:
        serialization.load_pem_private_key(
            roh, password=behaelter_passwort(roh, neu, pfad))
    except ValueError:
        sys.exit("Die neu geschriebene Datei laesst sich nicht oeffnen. "
                 "Sicherung einspielen.")

    kopf = _kopfzeile_lesen(roh)
    print()
    print(f"{name} ist jetzt verschluesselt.")
    print(f"  scrypt N={kopf.get('n')} r={kopf.get('r')} p={kopf.get('p')}")
    print(f"  Behaelter: PKCS8, AES-256-CBC")
    print()
    print("Die Passphrase gehoert an denselben Ort wie der Schluessel selbst -")
    print("Passwortmanager, Papier, ausser Haus. Ohne sie ist die Datei Muell.")
