"""
CO-37 - die privaten Schluesseldateien liegen verschluesselt (F-65).

Bis 0.37.20 lagen beide privaten Schluessel im Klartext auf der Platte:

    ~/.co37/release-private.pem    signiert Update-Pakete
    ~/.co37/license-private.pem    stellt Lizenzschluessel aus

Der erste ist der schwerere: ein Update-Paket wird vom Watcher als root
ausgepackt. Rechte und Anlegen waren seit F-35 in Ordnung - was fehlte,
war der Schutz der Datei selbst gegen ein gestohlenes Notebook oder eine
abgeflossene Sicherung.

WAS DIESE REIHE PRUEFT UND WARUM SIE MEHR PRUEFT ALS "IST VERSCHLUESSELT"

Der naheliegende Weg waere BestAvailableEncryption gewesen. Nachgemessen
am 2026-09-05 mit cryptography 50.0.1, was dabei entsteht: PBKDF2-SHA256
mit **2048 Durchlaeufen** und AES-256-CBC. 2048 ist die PKCS#8-Vorgabe
von OpenSSL; empfohlen werden heute sechshunderttausend. Eine menschlich
gewaehlte Passphrase faellt damit auf einer Grafikkarte in Minuten - der
Schutz waere ein Gefuehl gewesen.

Deshalb wird die Passphrase des Behaelters aus der des Menschen mit
scrypt abgeleitet (dieselben Parameter wie beim Anmeldepasswort im
Backend). Diese Reihe prueft genau das nach: dass die menschliche
Passphrase die Datei NICHT oeffnet, dass die abgeleitete es tut, und dass
die 2048 Durchlaeufe zwar immer noch dastehen, aber nichts mehr tragen.

Braucht kein Backend und kein Netz.

    python3 tests/schluessel-test.py
"""
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_LICENSE_HOME"] = str(TMP)

sys.path.insert(0, str(WURZEL / "tools"))
import schluessel  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


PW = b"drei zufaellige woerter hier"
PRIVAT = Ed25519PrivateKey.generate()
OEFFENTLICH = PRIVAT.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# ======================================================================
# Die Form der Datei
# ======================================================================
print("--- Die Form der Datei ---")
offen = schluessel.als_pem(PRIVAT)
zu = schluessel.als_pem(PRIVAT, PW)

check("ohne Passphrase bleibt es ein offener Schluessel",
      not schluessel.ist_verschluesselt(offen))
check("mit Passphrase wird es ein verschluesselter",
      schluessel.ist_verschluesselt(zu))
check("und der Kopf sagt, wie abgeleitet wurde",
      zu.startswith(b"# CO-37 scrypt "), zu.split(b"\n")[0][:60])

kopf = schluessel._kopfzeile_lesen(zu)
check("der Kopf nennt N", int(kopf["n"]) == schluessel.SCRYPT_N, kopf.get("n"))
check("der Kopf nennt r", int(kopf["r"]) == schluessel.SCRYPT_R, kopf.get("r"))
check("der Kopf nennt p", int(kopf["p"]) == schluessel.SCRYPT_P, kopf.get("p"))
check("und ein Salz von 16 Byte", len(bytes.fromhex(kopf["salt"])) == 16,
      len(kopf.get("salt", "")))

# Zweimal dieselbe Passphrase, zweimal ein anderes Salz - sonst waere aus
# zwei Dateien ablesbar, dass dieselbe Passphrase dahintersteht.
kopf2 = schluessel._kopfzeile_lesen(schluessel.als_pem(PRIVAT, PW))
check("jede Datei bekommt ein eigenes Salz",
      kopf["salt"] != kopf2["salt"])


# ======================================================================
# Die Ableitung traegt, nicht die 2048 Durchlaeufe
# ======================================================================
print()
print("--- Die Ableitung traegt (nicht PBKDF2 mit 2048) ---")
# Das ist der Kern. Waere die menschliche Passphrase unmittelbar die des
# Behaelters, haenge alles an PBKDF2 mit 2048 Durchlaeufen.
try:
    serialization.load_pem_private_key(zu, password=PW)
    unmittelbar = True
except ValueError:
    unmittelbar = False
check("die menschliche Passphrase oeffnet den Behaelter NICHT",
      not unmittelbar)

abgeleitet = schluessel.behaelter_passwort(zu, PW)
geladen = serialization.load_pem_private_key(zu, password=abgeleitet)
check("die abgeleitete oeffnet ihn",
      geladen.public_key().public_bytes(
          serialization.Encoding.Raw,
          serialization.PublicFormat.Raw) == OEFFENTLICH)
check("die Ableitung ist laenger als die Eingabe",
      len(abgeleitet) > len(PW), len(abgeleitet))
check("und reproduzierbar",
      schluessel.behaelter_passwort(zu, PW) == abgeleitet)
check("mit einer anderen Passphrase kommt etwas anderes heraus",
      schluessel.behaelter_passwort(zu, b"etwas anderes ganz") != abgeleitet)

# Die 2048 Durchlaeufe stehen weiterhin in der Datei - das ist in Ordnung,
# solange sie auf der ABGELEITETEN Passphrase arbeiten. Faellt diese
# Pruefung eines Tages um, weil cryptography die Vorgabe angehoben hat,
# gehoert der Kommentar in tools/schluessel.py nachgezogen.
def _pbkdf2_durchlaeufe(pem: bytes) -> int:
    """Die Durchlaufzahl aus dem PBES2-Kopf holen, ohne openssl."""
    roh = base64.b64decode(b"".join(
        z for z in pem.split(b"\n")
        if z and not z.startswith(b"-----") and not z.startswith(b"#")))
    # Kleines Muster: INTEGER (0x02) direkt nach dem 16-Byte-Salz.
    i = roh.index(b"\x04\x10")          # OCTET STRING, Laenge 16 = Salz
    j = i + 2 + 16
    assert roh[j] == 0x02, roh[j]
    laenge = roh[j + 1]
    return int.from_bytes(roh[j + 2:j + 2 + laenge], "big")


runden = _pbkdf2_durchlaeufe(zu)
check("PKCS8 selbst rechnet weiterhin nur mit der OpenSSL-Vorgabe",
      runden == 2048, runden)
check("genau deshalb steht scrypt davor",
      "scrypt" in (WURZEL / "tools" / "schluessel.py").read_text(
          encoding="utf-8"))


# ======================================================================
# Eine beschaedigte Kopfzeile (F-67 der Pruefung vom 2026-09-05)
# ======================================================================
print()
print("--- Beschaedigte Kopfzeile ---")
# Bis 0.37.21 kam hier ein nackter Rueckverfolg heraus: KeyError 'salt',
# "invalid literal for int()". Das ist der schlechteste denkbare
# Zeitpunkt fuer eine unverstaendliche Meldung - wer hier steht, versucht
# gerade an den wertvollsten Schluessel zu kommen, den er besitzt,
# vermutlich nach einem Plattenschaden.
_ENDE = b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nAAAA\n"
_kaputt = {
    "salt fehlt":            b"# CO-37 scrypt n=16384 r=8 p=5\n" + _ENDE,
    "n fehlt":               b"# CO-37 scrypt r=8 p=5 salt=" + b"aa" * 16 + b"\n" + _ENDE,
    "salt ist kein Hex":     b"# CO-37 scrypt n=16384 r=8 p=5 salt=zzzz\n" + _ENDE,
    "n ist keine Zahl":      b"# CO-37 scrypt n=viel r=8 p=5 salt=" + b"aa" * 16 + b"\n" + _ENDE,
    "n ist absurd gross":    b"# CO-37 scrypt n=1073741824 r=8 p=5 salt=" + b"aa" * 16 + b"\n" + _ENDE,
    "n keine Zweierpotenz":  b"# CO-37 scrypt n=16385 r=8 p=5 salt=" + b"aa" * 16 + b"\n" + _ENDE,
    "Salz zu kurz":          b"# CO-37 scrypt n=16384 r=8 p=5 salt=aabb\n" + _ENDE,
}
for _name, _roh in _kaputt.items():
    try:
        schluessel.behaelter_passwort(_roh, b"passphrase", "/pfad/zur/datei.pem")
        _erg = "durchgelaufen, keine Meldung"
    except SystemExit as e:
        _erg = str(e)
    except Exception as e:  # noqa: BLE001
        _erg = f"ROHE AUSNAHME {type(e).__name__}: {e}"
    check(f"{_name}: verstaendliche Meldung statt Rueckverfolg",
          isinstance(_erg, str) and not _erg.startswith("ROHE")
          and _erg != "durchgelaufen, keine Meldung",
          _erg.splitlines()[0][:70] if isinstance(_erg, str) else _erg)
    check(f"{_name}: die Meldung nennt die Datei",
          isinstance(_erg, str) and "/pfad/zur/datei.pem" in _erg)

# Und die Gegenprobe: eine heile Kopfzeile geht weiterhin durch.
check("eine heile Kopfzeile wird nicht abgewiesen",
      schluessel.behaelter_passwort(zu, PW, "/pfad") == abgeleitet)


# ======================================================================
# Laden ueber die Umgebung und die Fehlermeldung
# ======================================================================
print()
print("--- Laden ---")
datei = TMP / "probe.pem"
schluessel.schluessel_schreiben(datei, zu)
check("die Datei hat 0600", oct(datei.stat().st_mode)[-3:] == "600"
      or os.name == "nt", oct(datei.stat().st_mode)[-3:])

os.environ["CO37_TEST_PW"] = PW.decode()
k = schluessel.laden(datei, "CO37_TEST_PW", "zum Test")
check("mit der richtigen Passphrase aus der Umgebung",
      k.public_key().public_bytes(
          serialization.Encoding.Raw,
          serialization.PublicFormat.Raw) == OEFFENTLICH)

os.environ["CO37_TEST_PW"] = "falsch falsch falsch"
try:
    schluessel.laden(datei, "CO37_TEST_PW", "zum Test")
    ergebnis = "keine Ausnahme"
except SystemExit as e:
    ergebnis = str(e)
check("mit der falschen eine brauchbare Meldung",
      "Passphrase passt nicht" in str(ergebnis), ergebnis)
os.environ["CO37_TEST_PW"] = PW.decode()

# Eine offene Datei muss weiterhin gehen - sonst waere jeder, der noch
# nicht verschluesselt hat, mit dem naechsten Update ausgesperrt.
offen_datei = TMP / "offen.pem"
schluessel.schluessel_schreiben(offen_datei, offen)
k = schluessel.laden(offen_datei, "GIBT-ES-NICHT", "zum Test")
check("eine unverschluesselte Datei geht weiterhin, ohne Nachfrage",
      k.public_key().public_bytes(
          serialization.Encoding.Raw,
          serialization.PublicFormat.Raw) == OEFFENTLICH)


# ======================================================================
# Passphrase wechseln
# ======================================================================
print()
print("--- Verschluesseln und Passphrase wechseln ---")
# Eingehaengt wird an _eingabe(): das ist die einzige Stelle im Werkzeug,
# die eine Tastatur anfasst, und genau dafuer steht sie dort allein.
echt = schluessel._eingabe
eingaben = []


def tastatur(text):
    return eingaben.pop(0)


wechsel = TMP / "wechsel.pem"
schluessel.schluessel_schreiben(wechsel, offen)
NEU = "eine ganz neue passphrase"
schluessel._eingabe = tastatur
try:
    eingaben[:] = [NEU, NEU]
    schluessel.verschluesseln(wechsel, "GIBT-ES-NICHT", "zum Test", "Der Testschluessel")
finally:
    schluessel._eingabe = echt
check("aus offen wird verschluesselt",
      schluessel.ist_verschluesselt(wechsel.read_bytes()))
os.environ["CO37_TEST_PW"] = NEU
k = schluessel.laden(wechsel, "CO37_TEST_PW", "zum Test")
check("und laesst sich mit der neuen Passphrase oeffnen",
      k.public_key().public_bytes(
          serialization.Encoding.Raw,
          serialization.PublicFormat.Raw) == OEFFENTLICH)
check("keine Nebendatei bleibt liegen",
      not (TMP / "wechsel.pem.neu").exists())

# Zu kurz wird abgewiesen. Sonst waere die Passphrase die schwaechste
# Stelle einer sonst teuren Ableitung.
schluessel._eingabe = tastatur
try:
    eingaben[:] = ["kurz", "kurz", "lang genug fuer die Regel", "lang genug fuer die Regel"]
    ergebnis = schluessel.passphrase_fragen("zum Test", bestaetigen=True)
finally:
    schluessel._eingabe = echt
check("eine zu kurze Passphrase wird abgewiesen und erneut gefragt",
      ergebnis == b"lang genug fuer die Regel", ergebnis)

schluessel._eingabe = tastatur
try:
    eingaben[:] = ["erste eingabe hier", "zweite eingabe hier",
                   "beide gleich diesmal", "beide gleich diesmal"]
    ergebnis = schluessel.passphrase_fragen("zum Test", bestaetigen=True)
finally:
    schluessel._eingabe = echt
check("zwei ungleiche Eingaben werden abgewiesen",
      ergebnis == b"beide gleich diesmal", ergebnis)


# ======================================================================
# Das Werkzeug von aussen
# ======================================================================
print()
print("--- sign-release.py von aussen ---")
WERKZEUG = WURZEL / "tools" / "sign-release.py"
(TMP / "release-private.pem").write_bytes(zu)
paket = TMP / "probe.zip"
paket.write_bytes(b"kein echtes ZIP, aber eine Datei mit Inhalt")


def ruf(*args, pw=None, tty=False):
    umgebung = dict(os.environ)
    umgebung.pop("CO37_RELEASE_PASSPHRASE", None)
    if pw is not None:
        umgebung["CO37_RELEASE_PASSPHRASE"] = pw
    return subprocess.run([sys.executable, str(WERKZEUG), *args],
                          capture_output=True, text=True, env=umgebung,
                          stdin=subprocess.DEVNULL)


r = ruf("--schluessel-pruefen", pw=PW.decode())
check("--schluessel-pruefen geht mit der richtigen Passphrase",
      r.returncode == 0, (r.stdout + r.stderr).strip()[:120])

r = ruf("--schluessel-pruefen", pw="voellig falsche passphrase")
check("und scheitert mit der falschen", r.returncode != 0)
check("die Meldung sagt, woran es liegt",
      "Passphrase passt nicht" in (r.stdout + r.stderr), (r.stdout + r.stderr).strip()[:120])

# Ohne Passphrase und ohne Tastatur: abbrechen, nicht haengen. Genau das
# war der Grund, die Abfrage nach build_release.py zu holen.
r = ruf("--schluessel-pruefen")
check("ohne Passphrase und ohne Terminal wird abgebrochen, nicht gewartet",
      r.returncode != 0)
check("und die Meldung nennt den Grund",
      "Terminal" in (r.stdout + r.stderr), (r.stdout + r.stderr).strip()[:120])

r = ruf(str(paket), pw=PW.decode())
check("signieren geht", r.returncode == 0, (r.stdout + r.stderr).strip()[:150])
check("es entsteht eine Signatur", (TMP / "probe.zip.sig").is_file())

# Die Signatur selbst gegenpruefen, mit dem oeffentlichen Teil des
# Testschluessels. NICHT ueber "--pruefen": das Werkzeug prueft immer
# gegen den ausgelieferten backend/release_key.pub, und der gehoert zu
# Mikes echtem Schluessel. Genau so soll es sein - der oeffentliche Teil
# ist bewusst nicht ueber die Umgebung umbiegbar, sonst liesse sich die
# Gegenprobe auf einen fremden Schluessel lenken.
import hashlib as _h  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature  # noqa: E402


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


summe = _h.sha256(paket.read_bytes()).hexdigest()
sig = (TMP / "probe.zip.sig").read_text(encoding="ascii").strip()
try:
    Ed25519PublicKey.from_public_bytes(OEFFENTLICH).verify(
        _unb64(sig), summe.encode("ascii"))
    passt = True
except InvalidSignature:
    passt = False
check("die Signatur stammt wirklich vom verschluesselten Schluessel", passt)

r = ruf("--pruefen", str(paket))
check("gegen den ausgelieferten Schluessel passt sie folgerichtig NICHT - "
      "und das ohne Passphrase", r.returncode != 0
      and "Passphrase" not in (r.stdout + r.stderr),
      (r.stdout + r.stderr).strip()[:100])

# Die Papiersicherung muss ohne die Passphrase brauchbar sein.
r = ruf("--sicherung", pw=PW.decode())
check("--sicherung gibt den ENTSCHLUESSELTEN Schluessel aus",
      "BEGIN PRIVATE KEY" in r.stdout and "ENCRYPTED" not in r.stdout,
      r.stdout.strip()[:80])


# ======================================================================
# --verschluesseln von aussen, fuer BEIDE Werkzeuge
# ======================================================================
print()
print("--- --verschluesseln, beide Werkzeuge ---")
# Diese Pruefung fehlte bei der ersten Fassung, und der Fehler kam
# prompt: tools/make-license.py hat an mehreren Stellen eine lokale
# Variable 'schluessel' - und Python entscheidet je Funktion anhand der
# ZUWEISUNGEN, ob ein Name lokal ist, nicht anhand der Reihenfolge im
# Text. Der Aufruf schluessel.verschluesseln() ganz oben in main()
# scheiterte deshalb mit UnboundLocalError, obwohl die Zuweisung in einem
# Zweig steht, der bei --verschluesseln gar nicht durchlaufen wird.
#
# Die Modulfunktion allein zu pruefen genuegte nicht. Nur der Aufruf von
# aussen, so wie ein Mensch ihn tippt, faengt so etwas.
#
# Keine gleichnamige lokale Variable mehr - das ist die allgemeine Regel,
# nicht nur der eine Fall.
import ast as _ast  # noqa: E402

for _werkzeug in ("sign-release.py", "make-license.py"):
    _q = (WURZEL / "tools" / _werkzeug).read_text(encoding="utf-8")
    _baum = _ast.parse(_q)
    _modul = [n.names[0].asname or n.names[0].name for n in _ast.walk(_baum)
              if isinstance(n, _ast.Import)
              and n.names[0].name == "schluessel"]
    _kollision = []
    for _k in _ast.walk(_baum):
        if not isinstance(_k, _ast.FunctionDef):
            continue
        _lokale = {t.id for n in _ast.walk(_k) if isinstance(n, _ast.Assign)
                   for t in n.targets if isinstance(t, _ast.Name)}
        if set(_modul) & _lokale:
            _kollision.append(_k.name)
    check(f"{_werkzeug}: keine lokale Variable verdeckt das Modul",
          not _kollision, _kollision)

if os.name != "posix":
    print("      (die Laeufe darunter brauchen ein Pseudoterminal - "
          "unter Windows uebersprungen)")
else:
    import pty as _pty      # noqa: E402
    import select as _sel   # noqa: E402
    import time as _zeit    # noqa: E402

    def unter_tty(befehl, antworten, umgebung=None, grenze=25):
        """
        Einen Aufruf mit echtem Terminal fahren und auf jede Frage nach
        einer Passphrase die naechste Antwort tippen.

        Ohne Pseudoterminal geht das nicht: die Werkzeuge brechen ohne
        Terminal ab, und genau das sollen sie.
        """
        umg = dict(os.environ, **(umgebung or {}))
        pid, fd = _pty.fork()
        if pid == 0:
            os.environ.clear()
            os.environ.update(umg)
            os.execvp(sys.executable, [sys.executable, *befehl])
            os._exit(1)
        aus = b""
        offen = list(antworten)
        gesehen = 0
        ende = _zeit.time() + grenze
        while _zeit.time() < ende:
            r, _, _ = _sel.select([fd], [], [], 1.0)
            if r:
                try:
                    teil = os.read(fd, 4096)
                except OSError:
                    break
                if not teil:
                    break
                aus += teil
                # An der Eingabeaufforderung erkennen, nicht am Wort
                # "Passphrase": das steht auch im Warnhinweis darueber.
                # getpass endet immer mit ": " und ohne Zeilenumbruch -
                # genau darauf wird gewartet.
                #
                # Die erste Fassung zaehlte Wortvorkommen und tippte
                # deshalb zu frueh; wenn dabei eine Antwort verlorenging,
                # lief der Aufruf in die Zeitgrenze von zwei Minuten.
                # Zweimal das in einer Reihe, und der Testlauf stand.
                while offen and aus.rstrip(b" ").endswith(b":"):
                    os.write(fd, offen.pop(0).encode() + b"\n")
                    gesehen += 1
                    aus += b"\n"
            else:
                w, st = os.waitpid(pid, os.WNOHANG)
                if w:
                    return os.waitstatus_to_exitcode(st), aus.decode(errors="replace")
        try:
            _, st = os.waitpid(pid, 0)
        except ChildProcessError:
            st = 0
        return os.waitstatus_to_exitcode(st), aus.decode(errors="replace")

    ALT = "die alte passphrase hier"
    NEU2 = "die neue passphrase hier"

    for _werkzeug, _datei, _umg in (
            ("sign-release.py", "release-private.pem", "CO37_RELEASE_PASSPHRASE"),
            ("make-license.py", "license-private.pem", "CO37_LICENSE_PASSPHRASE")):
        _pfad = TMP / _datei
        _k = Ed25519PrivateKey.generate()
        _oeff = _k.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        _pfad.unlink(missing_ok=True)
        schluessel.schluessel_schreiben(_pfad, schluessel.als_pem(_k, ALT.encode()))

        _code, _aus = unter_tty(
            [str(WURZEL / "tools" / _werkzeug), "--verschluesseln"],
            [NEU2, NEU2], {_umg: ALT, "CO37_LICENSE_HOME": str(TMP)})
        check(f"{_werkzeug} --verschluesseln laeuft durch",
              _code == 0, _aus.strip()[-200:])
        check(f"{_werkzeug}: die Datei ist danach verschluesselt",
              schluessel.ist_verschluesselt(_pfad.read_bytes()))

        os.environ["CO37_TEST_PW"] = NEU2
        try:
            _neu = schluessel.laden(_pfad, "CO37_TEST_PW", "zum Test")
            _passt = _neu.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw) == _oeff
        except SystemExit as e:
            _passt = str(e)
        check(f"{_werkzeug}: dieselbe Schluesselzahl, neue Passphrase",
              _passt is True, _passt)

        os.environ["CO37_TEST_PW"] = ALT
        try:
            schluessel.laden(_pfad, "CO37_TEST_PW", "zum Test")
            _alt_geht = True
        except SystemExit:
            _alt_geht = False
        check(f"{_werkzeug}: die alte Passphrase oeffnet nicht mehr",
              not _alt_geht)


# ======================================================================
# Die Baustrecke fragt einmal, nicht dreimal
# ======================================================================
print()
print("--- build_release.py ---")
bau = (WURZEL / "build_release.py").read_text(encoding="utf-8")
check("es wird genau einmal nach der Passphrase gefragt",
      bau.count("getpass.getpass(") == 1, bau.count("getpass.getpass("))
check("alle drei Aufrufe des Werkzeugs bekommen die Umgebung mit",
      bau.count("env=_kindumgebung()") == 3,
      bau.count("env=_kindumgebung()"))
check("die Passphrase wird vor dem Bauen geprueft",
      "--schluessel-pruefen" in bau)
check("und landet nicht in os.environ des eigenen Prozesses",
      "os.environ[\"CO37_RELEASE_PASSPHRASE\"]" not in bau
      and "os.environ['CO37_RELEASE_PASSPHRASE']" not in bau)
check("ohne Terminal wird abgebrochen statt gewartet",
      "sys.stdin.isatty()" in bau)

# Die Abfrage muss VOR der ersten Schreiboperation stehen. Steht sie
# dahinter, hat setze_versionen() die Nummern schon in agent.py,
# update_watcher.py und index.html geschrieben - und ein Vertipper bei
# der Passphrase hinterlaesst einen halb angehobenen Arbeitsstand.
_i_pass = bau.index("passphrase_vorbereiten()\n", bau.index("def main("))
_i_ver = bau.index("setze_versionen(version)", bau.index("def main("))
check("gefragt wird vor der ersten Schreiboperation", _i_pass < _i_ver,
      (_i_pass, _i_ver))

# Nirgends darf die Passphrase in eine Datei geschrieben werden.
for name in ("build_release.py", "tools/sign-release.py",
             "tools/make-license.py", "tools/schluessel.py"):
    text = (WURZEL / name).read_text(encoding="utf-8")
    verdaechtig = [z for z in text.splitlines()
                   if ("write" in z or "print" in z)
                   and ("passphrase" in z.lower() and "=" in z)
                   and "help=" not in z and "#" not in z.split("passphrase")[0]]
    check(f"{name} schreibt die Passphrase nirgends weg",
          not verdaechtig, verdaechtig[:2])


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
