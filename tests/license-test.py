"""
CO-37 - Lizenzpruefung.

Legt sich ein eigenes Schluesselpaar in einem temporaeren Verzeichnis an
und stellt damit Schluessel aus. Der echte private Schluessel liegt beim
Lizenzgeber und ist hier weder vorhanden noch noetig.

Braucht kein Backend und kein Netz.

    python3 tests/license-test.py

Der wichtigste Fall darin ist der, der NICHT ausloesen darf: ein
abgelaufener oder fehlender Schluessel bringt nichts zum Stillstand. Ein
Patch-Management-Werkzeug, das wegen einer Lizenzfrage Systeme ungepatcht
laesst, schafft genau die Luecke, gegen die es angeschafft wurde.
"""

import base64
import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import license  # noqa: E402

TMP = Path(tempfile.mkdtemp())

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


# Eigenes Paar, und license.py darauf zeigen lassen
PRIVAT = Ed25519PrivateKey.generate()
PUB = TMP / "license_key.pub"
PUB.write_text(b64(PRIVAT.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)) + "\n",
    encoding="ascii")
license.PUB_DATEI = PUB


def schluessel(kunde="Testkunde", hosts=100, exp=None, nr=1, v=1,
               iat=None, signieren_mit=PRIVAT):
    daten = {
        "v": v, "k": kunde, "h": hosts, "nr": nr,
        "iat": (iat or date.today()).isoformat(),
        "exp": exp.isoformat() if isinstance(exp, date) else exp,
    }
    roh = json.dumps(daten, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return f"license-".replace("license-", "CO37-") + b64(roh) + "." \
        + b64(signieren_mit.sign(roh))


# ------------------------------------------------------------ ohne Schluessel
leer = license.Lizenz()
check("ohne Schluessel gilt der Freibetrag",
      leer.erlaubte_hosts == license.FREIE_HOSTS, leer.erlaubte_hosts)
check("Freibetrag ist 10", license.FREIE_HOSTS == 10, license.FREIE_HOSTS)
check("ohne Schluessel kein Hinweis", leer.hinweis == "", leer.hinweis)

# -------------------------------------------------------------- gueltiger Fall
l = license.pruefen(schluessel(hosts=100, exp=date.today() + timedelta(days=400)))
check("gueltiger Schluessel wird angenommen", l.vorhanden)
check("Hostzahl uebernommen", l.erlaubte_hosts == 100, l.erlaubte_hosts)
check("Kunde uebernommen", l.kunde == "Testkunde", l.kunde)
check("nicht abgelaufen", not l.abgelaufen)
check("kein Hinweis bei langer Restlaufzeit", l.hinweis == "", l.hinweis)

# ------------------------------------------------------------------- Warnung
l = license.pruefen(schluessel(exp=date.today() + timedelta(days=10)))
check("Hinweis kurz vor Ablauf", "10 Tagen" in l.hinweis, l.hinweis)
check("trotz Warnung volle Hostzahl", l.erlaubte_hosts == 100, l.erlaubte_hosts)

l = license.pruefen(schluessel(exp=date.today() + timedelta(days=60)))
check("kein Hinweis bei 60 Tagen", l.hinweis == "", l.hinweis)

# ----------------------------------------------------------------- abgelaufen
l = license.pruefen(schluessel(hosts=100, exp=date.today() - timedelta(days=1)))
check("abgelaufener Schluessel wird trotzdem gelesen", l.vorhanden)
check("abgelaufen erkannt", l.abgelaufen)
check("faellt auf den Freibetrag zurueck",
      l.erlaubte_hosts == license.FREIE_HOSTS, l.erlaubte_hosts)
check("Hinweis nennt den Ablauf", "abgelaufen" in l.hinweis, l.hinweis)
check("Hinweis sagt, dass weiter gepatcht wird",
      "weiter gepatcht" in l.hinweis, l.hinweis)

# ------------------------------------------------------------------ unbegrenzt
l = license.pruefen(schluessel(hosts=0, exp=date.today() + timedelta(days=400)))
check("unbegrenzt erkannt", l.unbegrenzt)
check("unbegrenzt -> keine Grenze", l.erlaubte_hosts is None, l.erlaubte_hosts)

l = license.pruefen(schluessel(hosts=0, exp=None))
check("unbefristet erkannt", l.unbefristet)
check("unbefristet laeuft nie ab", not l.abgelaufen)
check("unbefristet und unbegrenzt", l.erlaubte_hosts is None)

l = license.pruefen(schluessel(hosts=50, exp=None))
check("unbefristet mit Hostzahl", l.erlaubte_hosts == 50, l.erlaubte_hosts)

# ------------------------------------------------------------------ Faelschung
gut = schluessel(hosts=10, exp=date.today() + timedelta(days=100))
kern, sig = gut[5:].split(".", 1)

# Hostzahl von 10 auf 999 aendern und neu kodieren - ohne den privaten
# Schluessel kann die Signatur dazu nicht stimmen.
daten = json.loads(base64.urlsafe_b64decode(kern + "=" * (-len(kern) % 4)))
daten["h"] = 999
neu = base64.urlsafe_b64encode(
    json.dumps(daten, sort_keys=True, separators=(",", ":")).encode()
).decode().rstrip("=")
try:
    license.pruefen(f"CO37-{neu}.{sig}")
    check("veraenderte Hostzahl wird abgewiesen", False, "wurde angenommen!")
except license.LizenzFehler as e:
    check("veraenderte Hostzahl wird abgewiesen", "Signatur" in str(e), str(e))

# Fremdes Paar
fremd = Ed25519PrivateKey.generate()
try:
    license.pruefen(schluessel(signieren_mit=fremd))
    check("fremde Signatur wird abgewiesen", False, "wurde angenommen!")
except license.LizenzFehler as e:
    check("fremde Signatur wird abgewiesen", "Signatur" in str(e), str(e))

# --------------------------------------------------------------- Fehlerfaelle
for eingabe, was in [
    ("", "leer"),
    ("Unsinn", "kein Vorsatz"),
    ("CO37-abc", "kein Punkt"),
    ("CO37-###.###", "unlesbar"),
]:
    try:
        license.pruefen(eingabe)
        check(f"abgewiesen: {was}", False, "wurde angenommen!")
    except license.LizenzFehler:
        check(f"abgewiesen: {was}", True)

# Unbekannte Fassung
try:
    license.pruefen(schluessel(v=2))
    check("unbekannte Fassung wird abgewiesen", False, "wurde angenommen!")
except license.LizenzFehler as e:
    check("unbekannte Fassung wird abgewiesen", "Fassung" in str(e), str(e))

# Ausstellungsdatum in der Zukunft - Uhr verstellt
try:
    license.pruefen(schluessel(iat=date.today() + timedelta(days=30)))
    check("Ausstellung in der Zukunft wird abgewiesen", False, "wurde angenommen!")
except license.LizenzFehler as e:
    check("Ausstellung in der Zukunft wird abgewiesen", "Zukunft" in str(e), str(e))

# Kleiner Vorlauf muss durchgehen - Zeitzonen
l = license.pruefen(schluessel(iat=date.today() + timedelta(days=1)))
check("ein Tag Vorlauf stoert nicht", l.vorhanden)

# ------------------------------------------------- fehlender oeffentlicher Teil
license.PUB_DATEI = TMP / "gibtsnicht.pub"
try:
    license.pruefen(gut)
    check("ohne oeffentlichen Schluessel keine Pruefung", False, "wurde angenommen!")
except license.LizenzFehler as e:
    check("ohne oeffentlichen Schluessel keine Pruefung",
          "oeffentlicher" in str(e), str(e))
license.PUB_DATEI = PUB

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
