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
import os
import sys
import tempfile
import zipfile
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
#
# Seit 0.37.6 laufen Pruefung und Auspacken beide auf der geschuetzten
# Kopie (SAFE_ZIP) statt auf incoming.zip - deshalb stehen hier diese
# Namen. Dass es dieselbe Datei ist, prueft der F-19-Abschnitt weiter
# unten; hier geht es nur um die Reihenfolge.
quelle = (WURZEL / "update_watcher.py").read_text(encoding="utf-8")
pos_pruefung = quelle.find("pruefe_signatur(SAFE_ZIP")
pos_extract = quelle.find("sicher_auspacken(zf, WORK_DIR)")
check("pruefe_signatur wird in do_update aufgerufen", pos_pruefung > 0)
check("Aufruf steht vor dem Auspacken",
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

# Die Agent-Pakete werden nach state/ gebaut, nicht nach data/ (F-29).
#
# Bis 0.37.9 lag das Ziel unter data/, gehoerte damit co37, und
# build_packages.sh laeuft als root: open(ziel,"wb") in build_deb.py folgt
# einer Verknuepfung, die dort jemand unter dem erwarteten Paketnamen
# ablegt. Ausgeloest wurde der Bau durch eine einzige Datei im Eingang.
# Damit schrieb root an eine frei gewaehlte Stelle - dasselbe Muster wie
# F-18 und F-20.
#
# Die alte Pruefung an dieser Stelle verlangte das Gegenteil: einen chown
# der Pakete auf co37. Das war richtig, solange sie unter data/ lagen.
check("Pakete werden nach state/ gebaut, nicht nach data/",
      'pkg_dir = STATE_DIR / "packages"' in quelle)
check("kein chown der Pakete auf co37 mehr",
      'chown", "-R", "co37:co37", str(pkg_dir)' not in quelle)

# ======================================================================
# Kein toter Code hinter einem return
# ======================================================================
# Der Anlass, gefunden beim Umbau auf Schluessel in 0.36.6: in
# build_packages() stand
#
#     return True, lines
#     try:
#         run(["chown", "-R", "co37:co37", str(pkg_dir)])
#     ...
#     return True, lines
#
# Das chown lief also nie. Aufgefallen ist es nur beim Lesen - kein Test
# hat es gemerkt, und die Wirkung war lange unsichtbar, weil ohnehin das
# ganze Verzeichnis co37 gehoerte. Seit $BASE root gehoert, waeren die
# Pakete root geblieben.
#
# Deshalb hier eine allgemeine Schranke fuer die ganze Datei statt einer
# Pruefung nur auf diese eine Stelle: was hinter einem return, raise,
# break oder continue im selben Block steht, laeuft nie.
print("--- Kein toter Code ---")
import ast  # noqa: E402

baum = ast.parse((WURZEL / "update_watcher.py").read_text(encoding="utf-8"))
ABBRUCH = (ast.Return, ast.Raise, ast.Break, ast.Continue)
tot = []
for knoten in ast.walk(baum):
    for feld in ("body", "orelse", "finalbody"):
        block = getattr(knoten, feld, None)
        if not isinstance(block, list):
            continue
        for i, anweisung in enumerate(block[:-1]):
            if isinstance(anweisung, ABBRUCH):
                naechste = block[i + 1]
                tot.append(
                    f"Zeile {naechste.lineno} nach {type(anweisung).__name__} "
                    f"in Zeile {anweisung.lineno}")
check("keine Anweisung hinter return/raise im selben Block",
      not tot, "; ".join(tot))

# ======================================================================
# F-18 - der Watcher schreibt nirgends hin, wo co37 schreiben darf
# ======================================================================
print("--- Root schreibt nicht in co37-Gebiet ---")
# Der Befund vom 2026-08-31: status.json, build_status.json und
# watcher.json lagen unter data/update/. Das Verzeichnis gehoert co37.
# Muster war jeweils write_text -> replace -> shutil.chown, und beides
# folgt Verknuepfungen: co37 legt unter dem erwarteten Namen eine
# Verknuepfung ab, root schreibt hindurch und uebereignet danach das Ziel.
# Damit gehoerte co37 eine beliebige root-Datei.
#
# Geprueft wird die Eigenschaft, nicht die Schreibweise: KEIN Ziel, in das
# der Watcher schreibt, darf unterhalb von data/ liegen.
quelle = (WURZEL / "update_watcher.py").read_text(encoding="utf-8")

spec_roh = importlib.util.spec_from_file_location(
    "watcher_roh", WURZEL / "update_watcher.py")
w_roh = importlib.util.module_from_spec(spec_roh)
spec_roh.loader.exec_module(w_roh)

for name in ("STATUS_FILE", "BUILD_STATUS", "WATCHER_INFO", "SAFE_DIR"):
    ziel = getattr(w_roh, name, None)
    check(f"{name} liegt nicht unter data/",
          ziel is not None and w_roh.DATA_DIR not in Path(ziel).parents,
          ziel)
check("state/ ist das Ausgangsverzeichnis",
      getattr(w_roh, "STATE_DIR", None) == w_roh.BASE / "state",
      getattr(w_roh, "STATE_DIR", None))
check("der Eingang bleibt unter data/",
      w_roh.DATA_DIR in Path(w_roh.STATUS_EINGANG).parents,
      w_roh.STATUS_EINGANG)

# ----------------------------------------------------------------------
# Das Arbeitsverzeichnis wird ganz geraeumt
# ----------------------------------------------------------------------
# Der finally-Zweig raeumte bis 0.37.14 nur WORK_DIR (= SAFE_DIR/_work).
# Die Kopie des Pakets liegt aber eine Ebene darueber, direkt in SAFE_DIR,
# und blieb liegen - gut 550 KB nach jedem Update, am 2026-08-31 auf
# KK-OPS01 gesehen.
#
# Ueber den Syntaxbaum, weil im Kommentar daneben beide Namen stehen und
# eine Zeichenkettensuche deshalb nichts aussagt.
import ast as _ast2  # noqa: E402

_baum_w = _ast2.parse(quelle)
_do = next((k for k in _ast2.walk(_baum_w)
            if isinstance(k, _ast2.FunctionDef) and k.name == "do_update"), None)
check("do_update() ist auffindbar", _do is not None)
_finallys = [k for k in _ast2.walk(_do or _ast2.Module(body=[], type_ignores=[]))
             if isinstance(k, _ast2.Try) and k.finalbody]
_geraeumt_finally = set()
for _t in _finallys:
    for _knoten in _ast2.walk(_ast2.Module(body=_t.finalbody, type_ignores=[])):
        if isinstance(_knoten, _ast2.Call):
            _geraeumt_finally.add(_ast2.unparse(_knoten.func))
check("der finally-Zweig raeumt ueber raeume_arbeitsverzeichnis()",
      any("raeume_arbeitsverzeichnis" in a for a in _geraeumt_finally),
      sorted(_geraeumt_finally))

# ----------------------------------------------------------------------
# F-60: das Aufraeumen muss VOR dem Neustart stehen
# ----------------------------------------------------------------------
# Bis 0.37.17 stand es ausschliesslich im finally - und ist kein einziges
# Mal gelaufen. Am Ende des try-Zweigs steht
#
#     run(["systemctl", "restart", "co37-watcher"], timeout=30)
#
# und systemctl beendet dabei den laufenden Prozess. Was danach kommt,
# auch ein finally, findet nicht mehr statt. Der Zweig greift nur, wenn
# der Watcher sich NICHT selbst ausgetauscht hat - und das ist praktisch
# nie, weil build_release.py bei jedem Bau WATCHER_VERSION in diese Datei
# schreibt.
#
# Die Vorgaengerpruefung hat den QUELLTEXT angesehen und bestaetigt, dass
# dort SAFE_DIR steht. Nicht, ob die Stelle erreicht wird. Aufgefallen ist
# es im Feld, nach dem Update auf 0.37.17: state/work lag weiterhin voll
# da, obwohl 0.37.15 die Zeile schon enthielt.
def _zeilen_raeumen(knoten):
    return [k.lineno for k in _ast2.walk(knoten)
            if isinstance(k, _ast2.Call)
            and getattr(k.func, "id", "") == "raeume_arbeitsverzeichnis"]


def _zeilen_neustart(knoten):
    """Wo der Watcher sich selbst neu startet - ueber den Syntaxbaum.

    Nicht ueber eine Textsuche: 'systemctl restart co37-watcher' steht
    seit 0.37.18 auch im Docstring von raeume_arbeitsverzeichnis, als
    Begruendung. Eine Suche faende die Erklaerung und nicht den Aufruf.
    """
    treffer = []
    for k in _ast2.walk(knoten):
        if not (isinstance(k, _ast2.Call)
                and getattr(k.func, "id", "") == "run" and k.args):
            continue
        werte = [e.value for e in _ast2.walk(k.args[0])
                 if isinstance(e, _ast2.Constant) and isinstance(e.value, str)]
        if "co37-watcher" in werte and "restart" in werte:
            treffer.append(k.lineno)
    return treffer


_raeumen = _zeilen_raeumen(_do) if _do else []
_neustart = _zeilen_neustart(_do) if _do else []
check("do_update() raeumt ueberhaupt auf", bool(_raeumen), _raeumen)
check("und startet den Watcher neu - sonst prueft die naechste Zeile nichts",
      bool(_neustart), _neustart)
check("aufgeraeumt wird VOR dem Neustart",
      bool(_raeumen) and bool(_neustart) and min(_raeumen) < min(_neustart),
      f"raeumen in {_raeumen}, Neustart in {_neustart}")
check("und zusaetzlich im finally, fuer die Abbruchwege",
      bool(_geraeumt_finally), sorted(_geraeumt_finally))

# Die Funktion selbst, wirklich ausgefuehrt - nicht nur gelesen. Auf
# eigenen Pfaden, damit hier niemals /opt/co37 angefasst wird: die Reihe
# koennte auch auf dem Server laufen.
_f60 = TMP / "f60"
(_f60 / "work" / "_work" / "backend").mkdir(parents=True)
(_f60 / "work" / "incoming.zip").write_bytes(b"PK\x03\x04paket")
(_f60 / "work" / "incoming.sig").write_text("signatur", encoding="ascii")
(_f60 / "work" / "_work" / "backend" / "main.py").write_text("x", encoding="ascii")
(_f60 / "eingang").mkdir()
(_f60 / "eingang" / "incoming.zip").write_bytes(b"PK\x03\x04eingang")
(_f60 / "eingang" / "incoming.sig").write_text("sig", encoding="ascii")

_alt = (watcher.SAFE_DIR, watcher.INCOMING_ZIP, watcher.INCOMING_SIG)
watcher.SAFE_DIR = _f60 / "work"
watcher.INCOMING_ZIP = _f60 / "eingang" / "incoming.zip"
watcher.INCOMING_SIG = _f60 / "eingang" / "incoming.sig"
try:
    check("vorher liegt etwas da", (_f60 / "work" / "incoming.zip").is_file())
    watcher.raeume_arbeitsverzeichnis()
    check("das Arbeitsverzeichnis ist danach weg",
          not (_f60 / "work").exists(), list((_f60 / "work").rglob("*"))
          if (_f60 / "work").exists() else "")
    check("und der Eingang ebenfalls geleert",
          not (_f60 / "eingang" / "incoming.zip").exists()
          and not (_f60 / "eingang" / "incoming.sig").exists())
    # Zweimal aufrufen muss gefahrlos sein - genau das passiert im
    # Normalfall, einmal oben und einmal im finally.
    watcher.raeume_arbeitsverzeichnis()
    check("ein zweiter Aufruf schadet nicht", True)
except Exception as _exc:  # noqa: BLE001
    check("raeume_arbeitsverzeichnis() laeuft durch", False, _exc)
finally:
    watcher.SAFE_DIR, watcher.INCOMING_ZIP, watcher.INCOMING_SIG = _alt

# Und der Hebel selbst darf nicht zurueckkommen: kein chown auf einen
# Benutzer mehr. Kommentarzeilen aussortiert - der Befund wird oben im
# Quelltext erklaert, und den Namen dort zu treffen waere ein Fehlalarm.
ohne_kommentar = "\n".join(
    z for z in quelle.splitlines() if not z.lstrip().startswith("#"))
check("kein shutil.chown mehr im Watcher",
      "shutil.chown" not in ohne_kommentar)
check("die Statusdateien werden ueber eine Stelle geschrieben",
      ohne_kommentar.count("def _schreibe_json(") == 1)


# ======================================================================
# F-19 - geprueft wird dieselbe Datei, die auch eingespielt wird
# ======================================================================
print("--- Signatur und Auspacken greifen auf dieselbe Kopie zu ---")
# Bis 0.37.5 wurde die Signatur auf incoming.zip geprueft und dieselbe
# Datei danach ERNEUT von der Platte geoeffnet. Dazwischen lag das Anlegen
# der Sicherung. incoming.zip gehoert co37 - das Rennen war nicht knapp,
# es war am Protokolleintrag ablesbar.
check("do_update prueft die geschuetzte Kopie",
      "pruefe_signatur(SAFE_ZIP, SAFE_SIG)" in ohne_kommentar)
check("und packt dieselbe Kopie aus",
      "zipfile.ZipFile(SAFE_ZIP)" in ohne_kommentar)
check("das Paket wird vorher hineingeholt",
      "ins_sichere_holen(INCOMING_ZIP, SAFE_ZIP)" in ohne_kommentar)
check("incoming.zip wird nicht mehr direkt ausgepackt",
      "ZipFile(INCOMING_ZIP)" not in ohne_kommentar)

# Aufgerufen statt gesucht: eine Verknuepfung als Quelle muss abgewiesen
# werden, sonst holt root sich eine beliebige Datei des Systems herein.
_q = TMP / "f19"
_q.mkdir(exist_ok=True)
(_q / "echt.zip").write_bytes(b"inhalt")
(_q / "verknuepft.zip").symlink_to(_q / "echt.zip")
try:
    w_roh.ins_sichere_holen(_q / "verknuepft.zip", _q / "ziel.zip")
    check("eine Verknuepfung als Paket wird abgewiesen", False)
except w_roh.PaketAbgewiesen:
    check("eine Verknuepfung als Paket wird abgewiesen", True)
w_roh.ins_sichere_holen(_q / "echt.zip", _q / "ziel.zip")
check("eine echte Datei wird kopiert",
      (_q / "ziel.zip").read_bytes() == b"inhalt")


# ======================================================================
# F-17 - der Watcher packt nichts aus dem Arbeitsverzeichnis heraus
# ======================================================================
print("--- Zip-Slip auch auf der root-Seite ---")
_z = TMP / "f17"
_z.mkdir(exist_ok=True)
boese = _z / "boese.zip"
with zipfile.ZipFile(boese, "w") as zf:
    zf.writestr("../ausgebrochen.txt", "nein")
brav = _z / "brav.zip"
with zipfile.ZipFile(brav, "w") as zf:
    zf.writestr("backend/VERSION", "9.9.9")

ziel = _z / "aus"
ziel.mkdir(exist_ok=True)
try:
    with zipfile.ZipFile(boese) as zf:
        w_roh.sicher_auspacken(zf, ziel)
    check("ein Pfad ausserhalb wird abgewiesen", False)
except w_roh.PaketAbgewiesen:
    check("ein Pfad ausserhalb wird abgewiesen", True)
check("nichts ist ausserhalb gelandet",
      not (_z / "ausgebrochen.txt").exists())
with zipfile.ZipFile(brav) as zf:
    w_roh.sicher_auspacken(zf, ziel)
check("ein braves Paket wird ausgepackt",
      (ziel / "backend" / "VERSION").read_text() == "9.9.9")


# ======================================================================
# F-27 - ein Update darf den Vertrauensanker nicht austauschen
# ======================================================================
print("--- Der Herausgeberschluessel bleibt ---")
# Die Pruefung stand nur in update_manager.py, also auf der Seite, die
# unprivilegiert laeuft und sich ueber F-19 umgehen liess. Der Watcher
# uebernahm einen mitgebrachten Schluessel kommentarlos. Wirkung: ein
# einziges untergeschobenes Paket macht den Absender dauerhaft zum
# legitimen Herausgeber - auch fuer den Agent-Quelltext.
_p = TMP / "f27"
(_p / "backend").mkdir(parents=True, exist_ok=True)

# Gleicher Schluessel wie installiert: geht durch.
(_p / "backend" / "release_key.pub").write_text(
    (BASE / "backend" / "release_key.pub").read_text(encoding="ascii"),
    encoding="ascii")
try:
    w_roh.BASE = BASE
    w_roh.pruefe_schluessel(_p)
    check("derselbe Schluessel wird nicht beanstandet", True)
except w_roh.PaketAbgewiesen as e:
    check("derselbe Schluessel wird nicht beanstandet", False, e)

# Anderer Schluessel: muss abgewiesen werden.
(_p / "backend" / "release_key.pub").write_text("AAAA-ein-anderer-Schluessel\n",
                                                encoding="ascii")
try:
    w_roh.pruefe_schluessel(_p)
    check("ein anderer Schluessel wird abgewiesen", False)
except w_roh.PaketAbgewiesen:
    check("ein anderer Schluessel wird abgewiesen", True)

# Gar keiner im Paket: kein Fehler, BEWAHRTE_DATEIEN traegt ihn nach.
(_p / "backend" / "release_key.pub").unlink()
try:
    w_roh.pruefe_schluessel(_p)
    check("ein Paket ohne Schluessel bleibt zulaessig", True)
except w_roh.PaketAbgewiesen as e:
    check("ein Paket ohne Schluessel bleibt zulaessig", False, e)

# Ueber den Syntaxbaum, nicht ueber die Zeichenkette: ein auskommentierter
# Aufruf hat diese Pruefung beim ersten Anlauf gruen bleiben lassen. Genau
# der Fehler, der in diesem Projekt schon zweimal aufgetreten ist -
# 'kein FileKey= mehr' und 'signiere_agent() in main()' fanden beide ihren
# Namen in einem Kommentar wieder.
import ast as _ast  # noqa: E402

_baum = _ast.parse(quelle)
_do = next((k for k in _ast.walk(_baum)
            if isinstance(k, _ast.FunctionDef) and k.name == "do_update"), None)
check("do_update ist auffindbar", _do is not None)


def _aufrufzeile(fn, name):
    """Zeilennummer des ersten echten Aufrufs von 'name' - Kommentare zaehlen nicht."""
    for k in _ast.walk(fn):
        if isinstance(k, _ast.Call) and isinstance(k.func, _ast.Name) \
                and k.func.id == name:
            return k.lineno
    return None


if _do:
    z_schluessel = _aufrufzeile(_do, "pruefe_schluessel")
    z_swap = _aufrufzeile(_do, "swap_in_new_code")
    z_signatur = _aufrufzeile(_do, "pruefe_signatur")
    z_holen = _aufrufzeile(_do, "ins_sichere_holen")
    check("do_update ruft pruefe_schluessel wirklich auf", z_schluessel is not None)
    check("und der Aufruf steht vor dem Einspielen",
          z_schluessel is not None and z_swap is not None and z_schluessel < z_swap,
          f"{z_schluessel} < {z_swap}")
    check("das Paket wird geholt, bevor die Signatur geprueft wird",
          z_holen is not None and z_signatur is not None and z_holen < z_signatur,
          f"{z_holen} < {z_signatur}")


# ======================================================================
# Jeder Bauweg meldet seinen Stand
# ======================================================================
# Am 2026-08-31 aufgefallen: der Agents-Reiter zeigt ausschliesslich
# build_status.json. Geschrieben hat es nur handle_build_request() - der
# Weg ueber den Knopf. do_update() baut die Pakete ebenfalls, meldete den
# Stand aber nur ins Update-Protokoll. Ergebnis in der Oberflaeche:
# Pakete der Fassung 0.37.4, daneben das Protokoll von 0.37.3 mit einem
# gruenen "fertig", das zu einem anderen Lauf gehoerte.
#
# Ueber den Baum statt ueber Zeichenketten, damit ein dritter Bauweg
# genauso auffaellt: WER build_packages() aufruft, MUSS in derselben
# Funktion auch write_build_status() aufrufen.
print("--- Jeder Bauweg schreibt build_status.json ---")


def _aufrufe(fn):
    return {k.func.id for k in ast.walk(fn)
            if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)}


bauer = [k for k in ast.walk(baum)
         if isinstance(k, ast.FunctionDef) and "build_packages" in _aufrufe(k)]
check("es gibt ueberhaupt Aufrufer von build_packages()",
      len(bauer) >= 2, [f.name for f in bauer])
ohne = [f.name for f in bauer if "write_build_status" not in _aufrufe(f)]
check("jeder Aufrufer meldet den Stand auch nach build_status.json",
      not ohne, ohne)

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
# Kein Rueckschritt auf ein aelteres Paket (F-28)
# ======================================================================
# Die Signatur beweist die Herkunft einer Datei, nicht dass sie noch
# aktuell ist. Jede je ausgestellte Signatur gilt unbefristet, und alte
# Pakete werden verteilt und archiviert. Wer Code als co37 ausfuehrt,
# legte also ein ECHTES, gueltig signiertes altes Paket in den Eingang -
# der Watcher prueft, findet alles in Ordnung, und spielt als root eine
# Fassung ein, in der die Befunde von damals noch offen sind. Ueber F-19
# ist das dann beliebiger Code als root, und ueber MANAGED_DIRS plus
# agent_autoroll geht die Rueckstufung auf die ganze Flotte.
print()
print("--- Rueckschritt wird abgelehnt (F-28) ---")

watcher.STATE_DIR = BASE / "state"
watcher.DOWNGRADE_OK = BASE / "state" / "allow_downgrade"
(BASE / "state").mkdir(parents=True, exist_ok=True)
(BASE / "backend" / "VERSION").write_text("0.37.10\n", encoding="utf-8")


def rueckschritt(version):
    """True, wenn das Paket abgelehnt wird."""
    try:
        watcher.pruefe_kein_rueckschritt(version)
        return False
    except RuntimeError:
        return True


check("Zeichenkettenvergleich reicht nicht: 0.37.9 < 0.37.10",
      watcher._version_tupel("0.37.9") < watcher._version_tupel("0.37.10"))
check("aelteres Paket wird abgelehnt", rueckschritt("0.37.5"))
check("viel aelteres Paket wird abgelehnt", rueckschritt("0.36.3"))
check("gleiche Fassung ist erlaubt", not rueckschritt("0.37.10"))
check("neuere Fassung ist erlaubt", not rueckschritt("0.38.0"))
check("Sprung in die naechste Hauptzahl ist erlaubt", not rueckschritt("1.0.0"))

# Die Freigabe liegt in state/ und nicht im Eingang: koennte co37 sie
# anlegen, waere die Schranke keine.
check("die Freigabedatei liegt nicht in einem Verzeichnis von co37",
      watcher.DATA_DIR not in Path(watcher.DOWNGRADE_OK).parents,
      watcher.DOWNGRADE_OK)

watcher.DOWNGRADE_OK.write_text("", encoding="utf-8")
check("mit Freigabe geht der Rueckschritt", not rueckschritt("0.37.5"))
check("die Freigabe ist danach verbraucht",
      not watcher.DOWNGRADE_OK.exists())
check("der naechste Versuch wird wieder abgelehnt", rueckschritt("0.37.5"))

# Ohne Vergleichswert nicht blockieren - eine Erstinstallation oder ein
# kaputter Stand soll sich einspielen lassen.
(BASE / "backend" / "VERSION").unlink()
check("ohne installierte Fassung wird nicht blockiert",
      not rueckschritt("0.1.0"))
(BASE / "backend" / "VERSION").write_text("0.37.10\n", encoding="utf-8")

# Der Aufruf muss auch wirklich in do_update() stehen - ueber den
# Syntaxbaum, weil eine Zeichenkettensuche den Kommentar oben findet.
_du = next((k for k in ast.walk(ast.parse(quelle))
            if isinstance(k, ast.FunctionDef) and k.name == "do_update"), None)
check("do_update() gefunden", _du is not None)
check("do_update() ruft pruefe_kein_rueckschritt() auf",
      _du is not None and "pruefe_kein_rueckschritt" in _aufrufe(_du))

# ======================================================================
# Der Paketbau schreibt nicht in Gebiet von co37 (F-29)
# ======================================================================
print()
print("--- Paketbau schreibt nach state/ (F-29) ---")

bp = (WURZEL / "build_packages.sh").read_text(encoding="utf-8")
check("build_packages.sh baut nach state/", 'CO37_STATE' in bp and
      '/packages"' in bp)
check("build_packages.sh baut nicht mehr nach data/",
      'CO37_DATA:-$(pwd)/data}/packages' not in bp)
check("build_packages.sh weist ein verknuepftes Zielverzeichnis ab",
      '-L "$OUT"' in bp)

deb = (WURZEL / "packaging" / "build_deb.py").read_text(encoding="utf-8")
check("build_deb.py schreibt ohne Verknuepfungen zu folgen",
      "O_NOFOLLOW" in deb)
check("build_deb.py benutzt kein blankes open(target, 'wb') mehr",
      'with open(target, "wb")' not in deb)

msi = (WURZEL / "packaging" / "build_msi.py").read_text(encoding="utf-8")
check("build_msi.py prueft das Ziel auf eine Verknuepfung",
      "target.is_symlink()" in msi)

# Und die Wirkung, nicht nur der Wortlaut: eine Verknuepfung unter dem
# erwarteten Paketnamen darf nicht durchgeschrieben werden.
sys.path.insert(0, str(WURZEL / "packaging"))
import build_deb as _bd  # noqa: E402

_pkg = TMP / "pkgziel"
_pkg.mkdir()
_opfer = TMP / "opfer.txt"
_opfer.write_text("ORIGINAL", encoding="utf-8")
os.symlink(_opfer, _pkg / "co37-agent_0.0.1_all.deb")
try:
    _bd.sicher_schreiben(_pkg / "co37-agent_0.0.1_all.deb", b"BOESE")
    _durch = True
except OSError:
    _durch = False
check("Schreiben durch eine Verknuepfung wird abgewiesen", not _durch)
check("die Zieldatei ist unveraendert",
      _opfer.read_text(encoding="utf-8") == "ORIGINAL")
# Gegenprobe: eine echte Datei muss geschrieben werden, sonst prueft das
# hier nur, dass gar nichts mehr geht.
_bd.sicher_schreiben(_pkg / "echt.deb", b"!<arch>\n")
check("eine echte Datei wird geschrieben",
      (_pkg / "echt.deb").read_bytes() == b"!<arch>\n")
_bd.sicher_schreiben(_pkg / "echt.deb", b"zweiter Bau")
check("ein zweiter Bau darf ueberschreiben",
      (_pkg / "echt.deb").read_bytes() == b"zweiter Bau")

# ======================================================================
print()
print(f"{'FEHLER: ' + str(fails) if fails else 'alle Pruefungen bestanden'}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fails else 0)
