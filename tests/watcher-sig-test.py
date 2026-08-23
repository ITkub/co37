"""
CO-37 - Der Watcher prueft die Signatur selbst.

Der Anlass: das Backend prueft die Signatur schon beim Hochladen, aber es
laeuft unprivilegiert, und incoming.zip liegt in data/ - dem einzigen
Verzeichnis, in das es schreiben darf. Wer Code als co37 ausfuehrt, umgeht
die Anwendungslogik, legt ein eigenes Paket hin und setzt den Status auf
'triggered'. Der Watcher packt als root aus. Damit waere die Trennung
zwischen unprivilegiertem Backend und root-Watcher wirkungslos.

Der Watcher prueft deshalb selbst, vor dem Auspacken, und verlaesst sich
auf keine Pruefung jenseits der Rechtegrenze.

Zweiter Teil: die Eigentumsverhaeltnisse. Der Watcher fuehrt als root
Dateien aus BASE aus. Setzt er sie nach einem Update wieder auf co37,
oeffnet er die Luecke bei jedem Update aufs Neue.

Legt sich ein eigenes Schluesselpaar in einem temporaeren Verzeichnis an.
Braucht kein Backend und kein Netz.

    python3 tests/watcher-sig-test.py
"""

import base64
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

WURZEL = Path(__file__).resolve().parent.parent

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


# ======================================================================
# Aufbau: ein BASE-Verzeichnis mit eigenem Schluesselpaar
# ======================================================================
TMP = Path(tempfile.mkdtemp())
BASE = TMP / "opt"
(BASE / "backend").mkdir(parents=True)
(BASE / "data" / "update").mkdir(parents=True)

# release_sig.py aus dem echten Quellstand daneben legen - der Watcher
# importiert genau diese Datei, keine zweite Kopie der Kryptologik.
shutil.copy2(WURZEL / "backend" / "release_sig.py",
             BASE / "backend" / "release_sig.py")

privat = Ed25519PrivateKey.generate()
oeffentlich = privat.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
(BASE / "backend" / "release_key.pub").write_text(b64(oeffentlich) + "\n",
                                                  encoding="ascii")

spec = importlib.util.spec_from_file_location(
    "watcher_sig", WURZEL / "update_watcher.py")
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)

watcher.BASE = BASE
watcher.DATA_DIR = BASE / "data"
watcher.UPDATE_DIR = BASE / "data" / "update"
watcher.log = lambda *a, **k: None

PAKET = TMP / "paket.zip"
SIG = TMP / "paket.sig"
PAKET.write_bytes(b"Das ist nicht wirklich ein ZIP, fuer die Signatur egal.")


def signiere(daten: bytes) -> str:
    """Signiert wie tools/sign-release.py: Ed25519 ueber den SHA-256 als Hex."""
    import hashlib
    summe = hashlib.sha256(daten).hexdigest().encode("ascii")
    return b64(privat.sign(summe))


def abgewiesen(paket: Path, signatur: Path) -> str:
    """Gibt die Begruendung zurueck, oder "" wenn durchgelassen wurde."""
    try:
        watcher.pruefe_signatur(paket, signatur)
        return ""
    except watcher.PaketAbgewiesen as exc:
        return str(exc)


# ======================================================================
# Die Pruefung selbst
# ======================================================================
print("--- Signaturpruefung im Watcher ---")

check("PaketAbgewiesen existiert",
      issubclass(watcher.PaketAbgewiesen, Exception))
check("INCOMING_SIG liegt neben dem Paket",
      watcher.INCOMING_SIG.name == "incoming.sig")

# 1. Richtig signiert -> durchlassen
SIG.write_text(signiere(PAKET.read_bytes()), encoding="ascii")
check("korrekt signiertes Paket geht durch", abgewiesen(PAKET, SIG) == "",
      abgewiesen(PAKET, SIG))

# 2. Gar keine Signatur -> abweisen
FEHLT = TMP / "gibtsnicht.sig"
grund = abgewiesen(PAKET, FEHLT)
check("Paket ohne Signatur wird abgewiesen", grund != "")
check("Begruendung nennt die fehlende Signatur",
      "keine Signatur" in grund, grund)

# 3. Signatur eines FREMDEN Schluessels -> abweisen
fremd = Ed25519PrivateKey.generate()
import hashlib  # noqa: E402
FREMD_SIG = TMP / "fremd.sig"
FREMD_SIG.write_text(
    b64(fremd.sign(hashlib.sha256(PAKET.read_bytes()).hexdigest().encode())),
    encoding="ascii")
check("Signatur eines fremden Schluessels wird abgewiesen",
      abgewiesen(PAKET, FREMD_SIG) != "")

# 4. Paket nachtraeglich veraendert -> abweisen
#    Das ist der eigentliche Angriff: gueltige Signatur vom letzten
#    echten Paket, danach der Inhalt ausgetauscht.
VERAENDERT = TMP / "veraendert.zip"
VERAENDERT.write_bytes(PAKET.read_bytes() + b"  boesartiger Anhang")
check("nachtraeglich veraendertes Paket wird abgewiesen",
      abgewiesen(VERAENDERT, SIG) != "")

# 5. Beschaedigte Signatur -> abweisen, nicht durchreichen
KAPUTT = TMP / "kaputt.sig"
KAPUTT.write_text("das ist keine signatur", encoding="ascii")
check("beschaedigte Signatur wird abgewiesen",
      abgewiesen(PAKET, KAPUTT) != "")

# 6. Ohne release_key.pub wird nicht geprueft - selbst gebauter Stand.
#    Gleiche Regel wie im Backend, sonst liesse sich aus dem Quelltext
#    nichts mehr einspielen.
SCHLUESSEL = BASE / "backend" / "release_key.pub"
gemerkt = SCHLUESSEL.read_text()
SCHLUESSEL.unlink()
check("ohne release_key.pub wird nicht geprueft",
      abgewiesen(PAKET, FEHLT) == "")
SCHLUESSEL.write_text(gemerkt, encoding="ascii")
check("mit wieder vorhandenem Schluessel greift die Pruefung erneut",
      abgewiesen(PAKET, FEHLT) != "")

# 7. Schluessel da, aber die Pruefung nicht ladbar -> abweisen, nicht
#    stillschweigend durchlassen. Genau diese stille Abschaltung war der
#    Vorfall vor 0.34.0.
gemerkt_py = (BASE / "backend" / "release_sig.py").read_text()
(BASE / "backend" / "release_sig.py").write_text(
    "raise ImportError('nicht ladbar')\n")
for name in ("release_sig", "watcher_sig_release_sig"):
    sys.modules.pop(name, None)
sys.modules.pop("release_sig", None)
grund = abgewiesen(PAKET, SIG)
check("nicht ladbare Pruefung weist ab statt durchzulassen", grund != "",
      grund)
check("Begruendung nennt python3-cryptography",
      "python3-cryptography" in grund, grund)
(BASE / "backend" / "release_sig.py").write_text(gemerkt_py)
sys.modules.pop("release_sig", None)

# 8. Die Pruefung steht VOR dem Auspacken im Ablauf
quelle = (WURZEL / "update_watcher.py").read_text(encoding="utf-8")
pos_pruefung = quelle.find("pruefe_signatur(INCOMING_ZIP")
pos_extract = quelle.find("zf.extractall(WORK_DIR)")
check("pruefe_signatur wird in do_update aufgerufen", pos_pruefung > 0)
check("Aufruf steht vor extractall",
      0 < pos_pruefung < pos_extract, f"{pos_pruefung} < {pos_extract}")
check("die Signatur wird am Ende wieder weggeraeumt",
      "INCOMING_SIG.unlink(missing_ok=True)" in quelle)

# ======================================================================
# Eigentumsverhaeltnisse
# ======================================================================
print("--- Eigentuemer nach einem Update ---")

befehle = []
watcher.run = lambda cmd, **k: befehle.append(cmd)
watcher.eigentuemer_setzen()

check("BASE wird auf root gesetzt",
      ["chown", "-R", "root:root", str(BASE)] in befehle, befehle)
check("data/ gehoert weiterhin co37",
      ["chown", "-R", "co37:co37", str(BASE / "data")] in befehle, befehle)
check("BASE wird NICHT auf co37 gesetzt",
      ["chown", "-R", "co37:co37", str(BASE)] not in befehle, befehle)

# Reihenfolge: erst alles root, dann data/ zurueck an co37. Andersherum
# wuerde der zweite Aufruf den ersten wieder ueberschreiben.
#
# Mit -1 statt index(), damit ein kaputter Stand hier eine Fehlermeldung
# ergibt und keinen Abbruch - sonst bleiben die Pruefungen darunter
# ungelaufen und der Bericht sieht kuerzer aus, als er ist.
def wo(cmd):
    return befehle.index(cmd) if cmd in befehle else -1


i_root = wo(["chown", "-R", "root:root", str(BASE)])
i_data = wo(["chown", "-R", "co37:co37", str(BASE / "data")])
check("root-Aufruf kommt zuerst, data/ danach",
      i_root >= 0 and i_data >= 0 and i_root < i_data,
      f"{i_root} / {i_data}")

# Beide Wege, die Dateien anfassen, muessen es aufrufen - sonst hebt das
# naechste Update oder die naechste Rueckrollung die Haertung wieder auf.
check("swap_in_new_code setzt die Eigentuemer",
      "eigentuemer_setzen()" in quelle[quelle.find("def swap_in_new_code"):
                                       quelle.find("BUILD_REQUEST =")])
check("restore_backup setzt die Eigentuemer",
      "eigentuemer_setzen()" in quelle[quelle.find("def restore_backup"):
                                       quelle.find("def find_update_root")])
check("kein chown von BASE auf co37 mehr im Quelltext",
      'chown", "-R", "co37:co37", str(BASE)' not in quelle)

# ======================================================================
# Das Backend legt die Signatur ueberhaupt erst ab
# ======================================================================
print("--- Backend hinterlegt die Signatur ---")

um_quelle = (WURZEL / "backend" / "update_manager.py").read_text(encoding="utf-8")
check("update_manager kennt INCOMING_SIG", "INCOMING_SIG" in um_quelle)
check("Signatur wird geschrieben",
      "INCOMING_SIG.write_text(signature" in um_quelle)
check("Abbruch raeumt die Signatur weg",
      um_quelle.count("INCOMING_SIG.unlink(missing_ok=True)") >= 2)

# ======================================================================
print()
print(f"{'FEHLER: ' + str(fails) if fails else 'alle Pruefungen bestanden'}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
