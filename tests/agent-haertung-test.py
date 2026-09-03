"""
CO-37 - Haertung des Agents: Signatur des Codes (F-07) und Rechte an der
Konfiguration (F-06).

Aus der Sicherheitspruefung vom 2026-08-22.

F-07 - Der Agent holt seinen eigenen Code vom Backend und ersetzt damit
die Datei, die er als root beziehungsweise als SYSTEM ausfuehrt. Geprueft
wurde nur eine SHA-256, die in DERSELBEN Antwort steht wie der Code. Wer
die Antwort faelschen kann, faelscht beide - die Pruefsumme schuetzt
gegen einen abgebrochenen Download, nicht gegen Manipulation. Dieselbe
Ueberlegung hat beim Update-Paket zur Signatur gefuehrt (F-01); hier gilt
sie genauso.

F-06 - In agent.conf steht das Dauertoken des Hosts. Unter Linux stand
die Datei immer auf 600 und gehoert root. Unter Windows lief os.chmod ins
Leere, und C:\\ProgramData vererbt an neue Dateien ein Leserecht fuer
"Benutzer": das Token war fuer jedes Konto auf dem Rechner lesbar.

WAS DIESE REIHE LEISTET UND WAS NICHT - hier ehrlich, damit sie nicht
mehr zu versprechen scheint, als sie kann:

  F-07 wird ECHT geprueft. Die Reihe erzeugt ein eigenes Wegwerf-
  Schluesselpaar, signiert damit einen Codestand und laesst die Funktion
  des Agents darueber urteilen - in beide Richtungen.

  F-06 wird NUR STATISCH geprueft. Hier laeuft kein Windows, icacls gibt
  es nicht. Die Reihe stellt fest, dass der Aufruf da ist, an den
  richtigen Stellen steht und SIDs statt uebersetzbarer Gruppennamen
  benutzt. Ob die Datei danach tatsaechlich zu ist, zeigt auf dem
  Zielsystem nur:

      icacls C:\\ProgramData\\CO37\\agent.conf

Braucht kein laufendes Backend und kein Netz.

    python3 tests/agent-haertung-test.py
"""
import base64
import hashlib
import importlib.util
import inspect
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def b64(roh: bytes) -> str:
    return base64.urlsafe_b64encode(roh).decode("ascii").rstrip("=")


# ======================================================================
# F-07, Agent-Seite: die Pruefung urteilt richtig
# ======================================================================
print("--- Der Agent prueft die Signatur seines Codes ---")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

# Ein eigenes Paar, nur fuer diese Reihe. Der echte private
# Signaturschluessel liegt ausschliesslich beim Herausgeber und hat in
# einem Test nichts verloren - er wird hier weder gelesen noch gebraucht.
PRIVAT = Ed25519PrivateKey.generate()
OEFFENTLICH = b64(PRIVAT.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def signiere(text: str) -> str:
    """Signiert wie tools/sign-release.py: Ed25519 ueber den SHA-256-Hex."""
    summe = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return b64(PRIVAT.sign(summe.encode("ascii")))


def lade_agent(mit_schluessel: bool):
    """
    Laedt agent.py als Modul aus einem eigenen Verzeichnis.

    Eigenes Verzeichnis, weil der Zwang genau daran haengt, ob
    release_key.pub NEBEN der Datei liegt - das laesst sich nur so
    herstellen. Eine agent.conf muss dabei sein: ohne sie beendet sich
    der Agent schon beim Laden.
    """
    ordner = TMP / ("mit_key" if mit_schluessel else "ohne_key")
    ordner.mkdir(parents=True, exist_ok=True)
    shutil.copy(WURZEL / "agent" / "agent.py", ordner / "agent.py")
    (ordner / "agent.conf").write_text("server = http://127.0.0.1:1\n",
                                       encoding="utf-8")
    if mit_schluessel:
        (ordner / "release_key.pub").write_text(OEFFENTLICH + "\n",
                                                encoding="ascii")
    name = "co37agent_" + ("mit" if mit_schluessel else "ohne")
    spec = importlib.util.spec_from_file_location(name, ordner / "agent.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


mit = lade_agent(True)
check("mit ausgeliefertem Schluessel gilt Signaturzwang", mit.signaturzwang())

CODE = "print('das ist der agent')\n"

ok, grund = mit.pruefe_code_signatur(CODE, signiere(CODE))
check("eine gueltige Signatur wird angenommen", ok, grund)

ok, grund = mit.pruefe_code_signatur(CODE, "")
check("eine fehlende Signatur wird abgewiesen", not ok)
check("und die Meldung sagt, was zu tun ist",
      "Server" in grund and "aktualisieren" in grund, grund)

sig = signiere(CODE)
ok, grund = mit.pruefe_code_signatur(CODE + "# untergeschoben\n", sig)
check("veraenderter Code faellt durch", not ok)
check("und die Meldung nennt den Grund", "veraendert" in grund, grund)

# Signatur eines ANDEREN Schluessels - der Fall "Angreifer signiert selbst".
fremd = Ed25519PrivateKey.generate()
fremde_sig = b64(fremd.sign(
    hashlib.sha256(CODE.encode("utf-8")).hexdigest().encode("ascii")))
ok, _ = mit.pruefe_code_signatur(CODE, fremde_sig)
check("eine Signatur von fremder Hand faellt durch", not ok)

ok, _ = mit.pruefe_code_signatur(CODE, "kein-base64-!!!")
check("eine beschaedigte Signatur faellt durch", not ok)

# Gegenprobe zur Aussagekraft: ohne ausgelieferten Schluessel darf nichts
# geprueft werden. Sonst liesse sich aus dem Quelltext gebauter Code nie
# mehr aktualisieren, und die Pruefungen oben blieben gruen, wenn die
# Funktion schlicht immer ablehnte.
ohne = lade_agent(False)
check("ohne ausgelieferten Schluessel kein Zwang", not ohne.signaturzwang())
ok, _ = ohne.pruefe_code_signatur(CODE, "")
check("und dann wird auch ohne Signatur angenommen", ok)


# ======================================================================
# F-07, Reihenfolge im Agent
# ======================================================================
print("--- Geprueft wird, bevor geschrieben wird ---")
# Eine Signaturpruefung hinter dem Schreibvorgang waere wertlos. Genau
# dieser Fehler ist beim Watcher schon einmal beinahe passiert (F-01).
agent_quelle = (WURZEL / "agent" / "agent.py").read_text(encoding="utf-8")
korpus = agent_quelle[agent_quelle.index("def self_update("):]
korpus = korpus[:korpus.index("\ndef ", 10)]

i_pruef = korpus.find("pruefe_code_signatur(")
i_schreib = korpus.find("tmp.write_text(")
i_compile = korpus.find("compile(code")
check("self_update ruft die Pruefung auf", i_pruef > 0, i_pruef)
check("und zwar vor dem Schreiben", 0 < i_pruef < i_schreib,
      f"pruefen@{i_pruef} schreiben@{i_schreib}")
check("und vor der Syntaxpruefung", 0 < i_pruef < i_compile,
      f"pruefen@{i_pruef} compile@{i_compile}")

# Eine fehlgeschlagene Pruefung muss aussteigen, nicht nur protokollieren.
abschnitt = korpus[i_pruef:i_pruef + 400]
check("und steigt bei Ablehnung aus",
      re.search(r"if not echt:\s*\n\s*return False", abschnitt) is not None,
      abschnitt[:120])


# ======================================================================
# F-07, Backend-Seite: die Signatur wird ausgeliefert
# ======================================================================
print("--- Das Backend liefert die Signatur mit ---")
os.environ["CO37_DB"] = f"sqlite:///{TMP}/haertung.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"
sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from models import ApprovalState, Host, OSType  # noqa: E402

SQLModel.metadata.create_all(main.engine)

AGENT_TOKEN = "agent-token-fuer-die-pruefung"
with Session(main.engine) as s:
    s.add(Host(hostname="haertung-host", os_type=OSType.linux,
               agent_token_hash=main.hash_token(AGENT_TOKEN),
               approval_state=ApprovalState.approved,
               enrolled_at=main.utcnow()))
    s.commit()


def hole_code():
    with Session(main.engine) as s:
        return main.agent_code(AGENT_TOKEN, s)


# Eigene agent.py samt Signatur unterschieben, statt die echte zu
# beruehren: die echte .sig entsteht erst beim Bauen und liegt im
# Arbeitsstand oft gar nicht vor.
gefaelschte_quelle = TMP / "serviert" / "agent.py"
gefaelschte_quelle.parent.mkdir(parents=True, exist_ok=True)
gefaelschte_quelle.write_text('AGENT_VERSION = "9.9.9"\nprint("hallo")\n',
                              encoding="utf-8")
merke_src = main.AGENT_SRC
try:
    main.AGENT_SRC = gefaelschte_quelle

    antwort = hole_code()
    check("die Antwort hat ein Feld signature", "signature" in antwort,
          sorted(antwort))
    check("ohne .sig-Datei bleibt es leer", antwort.get("signature") == "",
          antwort.get("signature"))
    check("Code und Pruefsumme kommen weiterhin mit",
          antwort.get("code") and antwort.get("sha256"), sorted(antwort))

    inhalt = gefaelschte_quelle.read_text(encoding="utf-8")
    echte_sig = signiere(inhalt)
    gefaelschte_quelle.with_name("agent.py.sig").write_text(
        echte_sig + "\n", encoding="ascii")

    antwort = hole_code()
    check("liegt eine .sig daneben, wird sie ausgeliefert",
          antwort.get("signature") == echte_sig,
          str(antwort.get("signature"))[:20])

    # Und der Agent nimmt genau das an, was das Backend liefert. Diese
    # Pruefung ist der eigentliche Punkt: sie verbindet beide Seiten und
    # faellt auch dann, wenn eine Seite fuer sich stimmig bleibt - etwa
    # weil eine davon ueber andere Bytes signiert oder prueft.
    ok, grund = mit.pruefe_code_signatur(antwort.get("code", ""),
                                         antwort.get("signature", ""))
    check("und der Agent nimmt sie an", ok, grund)
finally:
    main.AGENT_SRC = merke_src


# ======================================================================
# F-07, Baukette
# ======================================================================
print("--- Die Signatur entsteht beim Bauen, zur richtigen Zeit ---")
bau = (WURZEL / "build_release.py").read_text(encoding="utf-8")
check("build_release.py signiert agent.py", "def signiere_agent(" in bau)

# Nur der Ablauf in main() zaehlt, und darin nur Anweisungen. Die
# Definition der Funktion steht weiter oben in der Datei, ihr Name auch in
# einem Kommentar weiter unten - beide Fundstellen saehen nach der
# richtigen Reihenfolge aus und sagten nichts ueber den Ablauf. Genau
# daran ist diese Pruefung beim ersten Entwurf vorbeigelaufen.
zeilen = [z for z in bau[bau.index("def main():"):].splitlines()
          if not z.lstrip().startswith("#")]
ablauf = "\n".join(zeilen)
i_version = ablauf.find("setze_versionen(version)")
i_sig = ablauf.find("signiere_agent()")
i_sammle = ablauf.find("dateien = sammle()")
# Die Reihenfolge ist der ganze Punkt: vor der Versionszeile signiert
# passt die Signatur nicht mehr zur Datei, nach dem Packen liegt sie
# nicht im Paket. Beides faellt beim Bauen nicht auf, sondern erst, wenn
# ein Agent beim Kunden die Aktualisierung ablehnt.
check("signiert wird NACH dem Setzen der Version", 0 < i_version < i_sig,
      f"version@{i_version} signieren@{i_sig}")
check("und VOR dem Einsammeln der Dateien", 0 < i_sig < i_sammle,
      f"signieren@{i_sig} sammeln@{i_sammle}")
check("eine alte Signatur wird vorher entfernt",
      "sig.unlink(missing_ok=True)" in bau)
check("und die frische gegengeprueft", '"--pruefen"' in bau)

check(".gitignore haelt die Signatur aus dem Repository heraus",
      "agent/agent.py.sig" in (WURZEL / ".gitignore").read_text(encoding="utf-8"))


print("--- Die Pakete liefern den oeffentlichen Schluessel mit ---")
# Ohne ihn prueft der Agent nichts - der Zwang haengt genau daran.
deb = (WURZEL / "packaging" / "build_deb.py").read_text(encoding="utf-8")
check("build_deb.py legt release_key.pub neben agent.py",
      '"usr/lib/co37/release_key.pub"' in deb and '"usr/lib/co37/agent.py"' in deb)
check("und setzt python3-cryptography als Abhaengigkeit",
      "python3-cryptography" in deb)

msi = (WURZEL / "packaging" / "build_msi.py").read_text(encoding="utf-8")
check("build_msi.py kopiert release_key.pub ins Installationsverzeichnis",
      'work / "release_key.pub"' in msi)
# cryptography steht seit 0.37.3 nicht mehr als Name in build_msi.py,
# sondern in agent/requirements.txt - siehe der F-14-Abschnitt unten.
check("und bringt cryptography als Wheel mit",
      "cryptography==" in (WURZEL / "agent" / "requirements.txt")
      .read_text(encoding="utf-8"))


# ======================================================================
print("--- Das MSI richtet die geplante Aufgabe auch beim Upgrade ein ---")
# Der Anlass, am 2026-08-26: das erste MSI-Upgrade ueberhaupt
# (0.36.12 -> 0.36.14) scheiterte mit Fehler 2753 und rollte zurueck.
#
# Die CustomActions liefen ueber FileKey= - Typ 18, "fuehre eine EXE aus
# der File-Tabelle aus". Das setzt voraus, dass die Datei im laufenden
# Vorgang installiert wird. Bei einem Upgrade ist python.exe das nicht:
# gleiche Komponenten-GUID, gleiche Dateiversion, schon vorhanden - der
# Installer ueberspringt sie ("Disallowing installation of component …
# since the same component with higher versioned keyfile exists") und die
# Aktion findet ihre EXE nicht mehr.
#
# Die Reihe kann kein MSI installieren. Sie haelt nur fest, dass der Weg
# ueber die File-Tabelle nicht zurueckkommt.
# Gezielt auf das Element, nicht auf die ganze Datei: der alte Weg wird
# oben im Kommentar erklaert, und den Namen dort zu treffen waere ein
# Fehlalarm - der Kommentar ist genau das, was ihn fernhaelt.
check("keine CustomAction ueber die File-Tabelle (FileKey=)",
      re.search(r"<CustomAction[^>]*FileKey=", msi) is None)
check("der Pfad zu python.exe kommt aus einer Eigenschaft",
      'Property="CO37PYEXE"' in msi and r'Value="[INSTALLDIR]python' in msi)
check("beide Aktionen benutzen sie",
      msi.count('Property="CO37PYEXE"') == 3)   # setzen + Register + Remove

# ----------------------------------------------------------------------
# Feste Nummern im Ablaufplan
# ----------------------------------------------------------------------
# Zweiter Anlass am selben Tag: mit Before="InstallInitialize" vergibt wixl
# die Nummer nicht verlaesslich. Derselbe Quellstand, zweimal gebaut - einmal
# 1401, einmal 1. In einem Minimalbeispiel zwanzigmal von zwanzig die 1.
#
# Die 1 liegt vor CostFinalize (1000), und dort wird INSTALLDIR erst
# aufgeloest. CO37PYEXE bekaeme einen unvollstaendigen Pfad, und die
# geplante Aufgabe zeigte ins Leere - in manchen Paketen, in anderen nicht.
# Ein Fehler, der vom Bau abhaengt statt vom Code, ist der schlechteste,
# den man haben kann: er laesst sich nicht nachstellen.
check("SetPyExe hat eine feste Nummer, kein Before=",
      '<Custom Action="SetPyExe" Sequence="1401"/>' in msi)
check("und liegt nach CostFinalize (1000) und vor InstallInitialize (1500)",
      1000 < 1401 < 1500)
check("RegisterTask hat eine feste Nummer",
      '<Custom Action="RegisterTask" Sequence="4001">' in msi)
check("RemoveTask hat eine feste Nummer",
      '<Custom Action="RemoveTask" Sequence="3499">' in msi)
check("keine eigene Aktion mehr ueber Before=/After=",
      re.search(r'<Custom Action="[^"]+"\s+(Before|After)=', msi) is None)

# Und die Nummern im Quelltext muessen zu denen passen, die der Bau
# anschliessend im fertigen Paket nachliest. Zwei Zahlen, die
# auseinanderlaufen koennen, sind eine Zeitbombe.
for name, nr in (("SetPyExe", 1401), ("RemoveTask", 3499), ("RegisterTask", 4001)):
    check(f"  Gegenprobe kennt {name} = {nr}",
          re.search(rf'"{name}":\s*{nr}\b', msi) is not None)
check("und der Bau liest den Ablaufplan wirklich aus",
      '"InstallExecuteSequence"' in msi and "msiinfo" in msi)
check("fehlt msiinfo, wird das gesagt statt uebergangen",
      'shutil.which("msiinfo")' in msi and "NICHT geprueft" in msi)

# Und die Gegenprobe am fertigen Paket. Ohne sie faellt genau der Fehler
# nicht auf, der hier zwei Anlaeufe gekostet hat: wixl kennt das Attribut
# Directory= an einer CustomAction nicht und laesst solche Eintraege
# STILLSCHWEIGEND weg - Rueckgabewert 0, Paket entsteht, Tabelle leer.
# ----------------------------------------------------------------------
# Binaerdateien tragen eine Version in der File-Tabelle
# ----------------------------------------------------------------------
# Der eigentliche Fehler hinter 2753 und 1721, gefunden am 2026-08-28 im
# Installationsprotokoll:
#
#   dreissigmal:  Disallowing installation of component: {…} since the
#                 same component with higher versioned keyfile exists
#   19:57:46      FileRemove(FileName=python.exe, ComponentId={B8EC3B81-…})
#   19:57:49      CustomActionSchedule(RegisterTask, Source=…\python.exe)
#   -> 1721, die Datei war nicht mehr da
#
# Und in der File-Tabelle des Pakets, bei jeder Binaerdatei: Version leer.
# wixl laeuft unter Linux und liest die Windows-Versionsressource nicht
# aus. Fuer Windows Installer schlaegt damit jede Datei auf der Platte,
# die eine Version hat, die Datei im Paket, die keine hat: er
# ueberspringt sie, und RemoveExistingProducts loescht sie anschliessend
# ersatzlos. Nicht nur python.exe - die ganze Python-Laufzeit.
#
# HIER STAND ZWISCHENZEITLICH ETWAS ANDERES. Der erste Erklaerungsversuch
# war, die Komponenten-GUIDs seien schuld, und die Version gehoere in die
# GUID. Das Protokoll des naechsten Versuchs hat es widerlegt: auch die
# neue GUID wurde verboten. Die GUIDs bleiben deshalb pfadbasiert - mit
# der Version darin verlaere man ausserdem die Verweiszaehlung, die
# ueberspringende Dateien vor dem Loeschen schuetzt.
#
# Aufgerufen statt im Quelltext gesucht: ein Aufruf beweist das
# Verhalten, eine Zeichenkette beweist nur, dass jemand etwas
# hingeschrieben hat.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("co37_build_msi",
                                     WURZEL / "packaging" / "build_msi.py")
_bm = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_bm)

check("es gibt eine Version fuer Binaerdateien", bool(_bm.DATEI_VERSION))
teile = [int(x) for x in _bm.DATEI_VERSION.split(".")]
check("sie ist hoeher als jede echte Dateiversion sein kann",
      teile[0] == 65535, _bm.DATEI_VERSION)
check("und syntaktisch eine Dateiversion",
      len(teile) == 4 and all(0 <= x <= 65535 for x in teile), _bm.DATEI_VERSION)
check("betroffen sind exe, dll und pyd",
      set(_bm.VERSIONIERT) == {".exe", ".dll", ".pyd"}, _bm.VERSIONIERT)
check("und der Paketbau setzt sie nur dort",
      'DefaultVersion="{DATEI_VERSION}"' in msi
      and "entry.suffix.lower() in VERSIONIERT" in msi)

# Die Kennungen bleiben pfadbasiert - ohne Version.
check("stable_guid nimmt nur den Pfad",
      len(inspect.signature(_bm.stable_guid).parameters) == 1,
      list(inspect.signature(_bm.stable_guid).parameters))
check("und liefert dieselbe Kennung wie bisher",
      _bm.stable_guid("python/python.exe")
      == "B8EC3B81-F4E3-0F51-F794-0E136EEF6966",
      _bm.stable_guid("python/python.exe"))

# Und der Bau muss es am fertigen Paket nachlesen, nicht nur behaupten.
check("das gebaute Paket wird auf fehlende Versionen geprueft",
      '"File"' in msi and "ohne Version in der" in msi)

check("das gebaute Paket wird gegengeprueft", "def pruefe_paket(" in msi)
for zeichen in ("CO37PYEXE", "RegisterTask", "install_task.py", "agent.py"):
    check(f"  darin verlangt: {zeichen}", f'"{zeichen}"' in msi)
check("und ein unvollstaendiges Paket wird verworfen",
      "msi.unlink(missing_ok=True)" in msi)


# ======================================================================
# F-14 - die Bibliotheken im MSI kommen aus einer gepinnten Datei
# ======================================================================
print("--- Was an Bibliotheken ins Windows-Paket wandert ---")
# Der Anlass, 2026-08-29: build_msi.py zaehlte die Paketnamen selbst auf,
# ohne Versionen, und agent/requirements.txt las niemand. Jeder Bau holte
# damit, was PyPI gerade anbot - signiert mit dem Release-Schluessel und
# auf jedem Windows-Host als SYSTEM ausgefuehrt. Tags zuvor war an genau
# dieser Datei ein Befund "behoben" worden, der das Paket nie erreicht hat.
#
# Diese Reihe haelt beide Haelften fest: dass die Datei gelesen wird, und
# dass der Bau das Ergebnis dagegenprueft.
req_pfad = WURZEL / "agent" / "requirements.txt"
req_text = req_pfad.read_text(encoding="utf-8")

check("build_msi.py kennt den Pfad zu agent/requirements.txt",
      'AGENT_REQ = HERE.parent / "agent" / "requirements.txt"' in msi)
check("und installiert darueber, nicht ueber eine eigene Namensliste",
      '"-r", str(AGENT_REQ)' in msi)
# Die alte Liste darf nicht danebenstehen bleiben - zwei Quellen fuer
# denselben Inhalt sind genau der Fehler, um den es hier geht.
check("die frueher fest eingetragene Namensliste ist weg",
      re.search(r'"requests",\s*"urllib3",\s*"certifi"', msi) is None)

# Jede Zeile mit ==, sonst waere die Luecke nur kleiner statt zu.
# Steht bewusst VOR dem Aufruf von lies_pins: die Funktion beendet bei
# einer losen Zeile den Prozess (so soll der Bau sich verhalten), und ein
# Test, der mittendrin aussteigt, meldet den Rest nicht mehr.
lose = [z for z in req_text.splitlines()
        if z.split("#", 1)[0].strip() and "==" not in z]
check("keine Zeile ohne feste Fassung", not lose, lose)

# Aufgerufen statt gesucht: die Funktionen sollen sich richtig verhalten,
# nicht nur vorhanden sein.
try:
    pins = _bm.lies_pins(req_text)
    check("requirements.txt laesst sich lesen", len(pins) >= 8, len(pins))
except SystemExit as e:
    pins = {}
    check("requirements.txt laesst sich lesen", False, e)
for paket in ("requests", "urllib3", "cryptography",
              "certifi", "charset-normalizer", "idna", "cffi", "pycparser"):
    check(f"  gepinnt: {paket}", paket in pins, pins.get(paket))

# Eine unscharfe Angabe muss den Bau anhalten, nicht durchrutschen.
try:
    _bm.lies_pins("requests>=2.0\n")
    check("eine Zeile mit >= wird abgewiesen", False)
except SystemExit:
    check("eine Zeile mit >= wird abgewiesen", True)
try:
    _bm.lies_pins("requests\n")
    check("ein blosser Paketname wird abgewiesen", False)
except SystemExit:
    check("ein blosser Paketname wird abgewiesen", True)
check("Kommentare und Leerzeilen stoeren nicht",
      _bm.lies_pins("# nur ein Hinweis\n\nrequests==2.34.2\n")
      == {"requests": "2.34.2"})

# Namen nach PEP 503 - charset_normalizer und charset-normalizer sind
# dasselbe Paket, und die .dist-info-Ordner schreiben es mit Unterstrich.
check("Paketnamen werden vergleichbar gemacht",
      _bm.normname("Charset_Normalizer") == "charset-normalizer",
      _bm.normname("Charset_Normalizer"))

# Der Vergleich selbst, in allen vier Ausgaengen.
gleich = {"requests": "2.34.2", "urllib3": "2.7.0"}
check("gleicher Stand wird nicht beanstandet",
      _bm.pruefe_vendorstand(gleich, dict(gleich)) == [])
check("ein fehlendes Paket faellt auf",
      len(_bm.pruefe_vendorstand(gleich, {"requests": "2.34.2"})) == 1)
check("ein zusaetzliches Paket faellt auf",
      len(_bm.pruefe_vendorstand(gleich, {**gleich, "cffi": "2.1.1"})) == 1)
check("eine abweichende Fassung faellt auf",
      len(_bm.pruefe_vendorstand(gleich, {**gleich, "urllib3": "2.3.0"})) == 1)

# Und das Lesen der .dist-info-Ordner, an echten Verzeichnissen.
_site = TMP / "site-packages"
_site.mkdir(parents=True, exist_ok=True)
(_site / "requests-2.34.2.dist-info").mkdir(exist_ok=True)
(_site / "charset_normalizer-3.5.1.dist-info").mkdir(exist_ok=True)
(_site / "requests").mkdir(exist_ok=True)          # kein dist-info, ignorieren
check("site-packages wird richtig ausgelesen",
      _bm.lies_installiert(_site)
      == {"requests": "2.34.2", "charset-normalizer": "3.5.1"},
      _bm.lies_installiert(_site))

# Der Bau muss die Pruefung auch anwenden, nicht nur koennen.
check("der Bau ruft die Gegenprobe auf", "pruefe_vendorstand(pins, installiert)" in msi)
check("und bricht bei Abweichung ab",
      re.search(r"if maengel:\s*\n\s*raise SystemExit", msi) is not None)
check("der Inhalt wird ins Bauprotokoll geschrieben",
      "Mitgelieferte Bibliotheken" in msi)


# ======================================================================
# F-15 - die mitgelieferte Python-Laufzeit
# ======================================================================
print("--- Die Python-Laufzeit im Windows-Paket ---")
# Der Zweig laesst sich von hier aus nicht pruefen (kein Netz im Testlauf,
# und eine fest eingetragene Liste toter Zweige waere genau die Fehlerart,
# die uns F-14 eingebracht hat). Was hier geprueft wird, ist der andere
# Teil: dass eine Anhebung nicht halb passieren kann.
check("PY_VERSION ist eine dreiteilige Zahl",
      re.fullmatch(r"\d+\.\d+\.\d+", _bm.PY_VERSION) is not None,
      _bm.PY_VERSION)
check("Dateiname und Adresse werden daraus abgeleitet",
      'PY_ZIP = f"python-{PY_VERSION}-embed-amd64.zip"' in msi
      and "{PY_VERSION}/{PY_ZIP}" in msi)
# Sonst zeigt PY_VERSION auf die eine Nebenversion und pip holt Raeder
# fuer eine andere - der Fehler faellt dann erst auf dem Zielsystem auf.
check("pip bekommt die Nebenversion aus PY_VERSION",
      '".".join(PY_VERSION.split(".")[:2])' in msi)

# Und die Meldung im Watcher muss den tatsaechlichen Dateinamen noch
# treffen. Genau hier stand bis 0.37.0 eine fest eingetragene Fassung,
# die nach einer Anhebung auf die veraltete Datei zeigte.
watcher = (WURZEL / "update_watcher.py").read_text(encoding="utf-8")
_muster = re.search(r're\.search\(r"([^"]+)", out\)', watcher)
check("der Watcher liest den Dateinamen ueber einen Ausdruck",
      _muster is not None)
if _muster:
    check("und der Ausdruck trifft die aktuelle PY_ZIP",
          re.fullmatch(_muster.group(1), _bm.PY_ZIP) is not None,
          _bm.PY_ZIP)

# Dieselbe Frage fuer die Anleitung. In README.md stand der Dateiname fest
# eingetragen und zeigte seit 0.37.4 (PY_VERSION 3.14.7) auf
# "python-3.12.8-embed-amd64.zip" - wer der Anleitung folgte und die Datei
# ohne Netz von Hand ablegte, legte genau die Fassung ab, die F-15
# loswerden sollte. Dritter Fall derselben Art nach i18n.js (0.37.0) und
# den Pins des Agents (F-14).
#
# Geprueft wird die Regel, nicht die Zahl: in der README darf ueberhaupt
# kein Embeddable-Dateiname mehr stehen. Eine Pruefung auf "steht die
# richtige Fassung drin" waere dieselbe Falle noch einmal - sie ginge bei
# der naechsten Anhebung mit durch, solange jemand beide Stellen pflegt.
_readme = (WURZEL / "README.md").read_text(encoding="utf-8")
_fest = re.findall(r"python-\d+\.\d+\.\d+-embed-amd64\.zip", _readme)
check("die README nennt keinen festen Embeddable-Dateinamen mehr",
      not _fest, _fest)
check("sie verweist stattdessen auf PY_VERSION",
      "PY_VERSION" in _readme)
# Gegenprobe: der Ausdruck findet so einen Namen ueberhaupt.
check("der Ausdruck wuerde einen festen Namen finden",
      re.findall(r"python-\d+\.\d+\.\d+-embed-amd64\.zip",
                 "siehe python-3.12.8-embed-amd64.zip hier"))


# ======================================================================
# F-26 - der Agent schreibt kein Servertoken ungeprueft in agent.conf
# ======================================================================
print("--- Token aus der Serverantwort wird geprueft ---")
# Der Wert kommt aus einer Serverantwort und wird zeilenweise in
# agent.conf geschrieben; load_config() nimmt bei mehrfachem Schluessel den
# zuletzt gelesenen. Ein Zeilenumbruch im Token reichte damit, um
# "server = http://angreifer" und "verify_ssl = false" nachzuschieben - der
# Agent haette dauerhaft einen anderen Server befragt und dabei kein
# Zertifikat mehr geprueft. Erreichbar fuer jeden, der die Verbindung
# kontrolliert: die Anmeldung laeuft ohne Token, und eine Neuanmeldung
# laesst sich mit zwei Antworten 401 erzwingen.
_ag = TMP / "f26"
_ag.mkdir(exist_ok=True)
_conf = _ag / "agent.conf"
_conf.write_text("server = https://co37.example\ntoken = alt\n", encoding="utf-8")

# Das oben schon geladene Modul weiterbenutzen. agent.py beendet sich beim
# Laden, wenn keine agent.conf danebenliegt - lade_agent() richtet dafuer
# eigens ein Verzeichnis ein, und das noch einmal zu tun waere doppelt.
_ag_mod = mit
_ag_mod.CFG_PATH = _conf
_ag_mod.sichere_rechte = lambda *a, **k: None
_ag_mod.log = lambda *a, **k: None

check("der Agent kennt eine Form fuer Token",
      getattr(_ag_mod, "TOKEN_ERLAUBT", None) is not None)

boese = "abc\nserver = http://angreifer\nverify_ssl = false"
try:
    _ag_mod.set_token(boese)
    check("ein Token mit Zeilenumbruch wird abgewiesen", False)
except ValueError:
    check("ein Token mit Zeilenumbruch wird abgewiesen", True)
inhalt = _conf.read_text(encoding="utf-8")
check("die Konfiguration ist unveraendert",
      "angreifer" not in inhalt and "verify_ssl" not in inhalt, inhalt)

for schlecht in ("", "kurz", "hat leerzeichen drin", "semikolon;drin"):
    try:
        _ag_mod.set_token(schlecht)
        check(f"abgewiesen: {schlecht[:22]!r}", False)
    except ValueError:
        check(f"abgewiesen: {schlecht[:22]!r}", True)

# Ein echtes Token muss weiterhin durchgehen - sonst waere die Anmeldung
# kaputt. Form wie secrets.token_urlsafe(32).
gut = "aBc-123_XyZ" * 3
_ag_mod.set_token(gut)
check("ein gueltiges Token wird gespeichert",
      f"token = {gut}" in _conf.read_text(encoding="utf-8"))


# ======================================================================
# F-06 - nur statisch, siehe Kopf dieser Datei
# ======================================================================
print("--- Rechte an agent.conf (statisch) ---")
check("der Agent kennt eine Funktion dafuer",
      "def sichere_rechte(" in agent_quelle)
# Seit 0.37.13 ueber _sys32("icacls.exe") - voller Pfad aus einem
# SYSTEM-Prozess (F-47). Der Name allein wuerde die Umstellung nicht
# ueberleben, deshalb beides zulassen.
check("sie ruft icacls auf",
      '"icacls"' in agent_quelle or '_sys32("icacls.exe")' in agent_quelle)

# Ueber SIDs, nicht ueber Namen. Auf einem deutschen Windows heisst die
# Gruppe "Administratoren" - ein Befehl mit dem englischen Namen
# scheitert dort still, und die Datei bliebe offen.
check("ueber die SID des Systems", "*S-1-5-18" in agent_quelle)
check("ueber die SID der Administratoren", "*S-1-5-32-544" in agent_quelle)
check("keine uebersetzbaren Gruppennamen im Befehl",
      not re.search(r'"[^"]*\bAdministrators\b[^"]*:\(', agent_quelle))
check("die Vererbung wird abgeschnitten", "/inheritance:r" in agent_quelle)

# An allen drei Stellen, an denen die Datei entsteht oder vorgefunden wird.
for stelle, muster in (
    ("beim Speichern des Tokens", r"def set_token\(.*?sichere_rechte\(CFG_PATH\)"),
    ("beim Verwerfen des Tokens", r"def clear_token\(.*?sichere_rechte\(CFG_PATH\)"),
):
    check(f"aufgerufen {stelle}",
          re.search(muster, agent_quelle, re.S) is not None)

korpus_main = agent_quelle[agent_quelle.index("def main():"):]
check("und einmal beim Start", "sichere_rechte(CFG_PATH)" in korpus_main)

# Der alte, unter Windows wirkungslose Weg darf nicht danebenstehen
# bleiben - sonst sieht die Datei geschuetzt aus und ist es nicht.
check("kein blosses os.chmod mehr auf die Konfiguration",
      "os.chmod(CFG_PATH" not in agent_quelle)

check("auch die Installation unter Windows setzt die Rechte",
      "icacls" in msi and "S-1-5-32-544" in msi)
check("und zwar auf Ordner und Datei",
      "(OI)(CI)(F)" in msi and "(CONF_DIR," in msi and "(CONF," in msi)

print("       (nicht pruefbar ohne Windows: ob die Datei danach wirklich")
print("        zu ist. Auf dem Zielsystem nachsehen mit")
print("        icacls C:\\ProgramData\\CO37\\agent.conf)")


# ======================================================================
# Ein Paket ohne Signaturschluessel entsteht nicht aus Versehen (F-44)
# ======================================================================
# Bis 0.37.9 stand dort nur ein print(). Ein Hinweis auf stdout geht im
# Bauprotokoll unter, und dem fertigen Paket sieht man den Unterschied
# nicht an - seine Agenten nehmen dann jeden Code an, den ihr Server
# ihnen schickt. Dieselbe Art Fehler wie die von wixl stillschweigend
# weggelassene CustomAction.
print()
print("--- Kein stiller Bau ohne release_key.pub (F-44) ---")
for name, inhalt in (("build_deb.py", deb), ("build_msi.py", msi)):
    check(f"{name} bricht ohne Schluessel ab",
          "raise SystemExit(" in inhalt and "release_key.pub fehlt" in inhalt)
    check(f"{name} laesst den Weg ohne Signatur ausdruecklich zu",
          "CO37_OHNE_SIGNATUR" in inhalt)
    check(f"{name} weist nicht mehr nur hin",
          'print("Hinweis: kein backend/release_key.pub' not in inhalt)

# ======================================================================
# tools/ wird nicht ausgeliefert (F-45)
# ======================================================================
# make-license.py sagt in seiner zweiten Zeile selbst: "Gehoert NICHT auf
# den Server und nicht in das Auslieferungspaket - build_release.sh nimmt
# tools/ bewusst nicht mit." Beide Baustrecken nahmen es mit. Kein
# Schluesselmaterial betroffen, aber sign-release.py --init lag damit auf
# jedem Kundensystem - und eine falsche Aussage im Quelltext ist das,
# woran man sich spaeter orientiert.
print()
print("--- tools/ bleibt draussen (F-45) ---")
check("build_release.py packt tools/ nicht ein",
      '"tools"' not in bau.split("VERZEICHNISSE =")[1].split("]")[0])
_sh = (WURZEL / "build_release.sh").read_text(encoding="utf-8")
_zip = _sh.split("zip -rq")[1].split("-x")[0] if "zip -rq" in _sh else ""
check("build_release.sh packt tools/ nicht ein", "tools" not in _zip, _zip.strip())
check("die Geheimnissuche durchsucht tools/ weiterhin",
      "tests tools ." in _sh or "tools" in _sh.split("GEHEIM=")[1].split("\n")[0])

# ======================================================================
# Die Konfigurationslogik des Installationsskripts (F-46, Fehler 1721)
# ======================================================================
# Der Anlass, 2026-09-01: eine Haertung fuer F-46 verwarf den 'server'
# aus einer vorhandenen agent.conf. Beim Upgrade per Doppelklick gibt
# aber niemand CO37SERVER auf der Befehlszeile mit - danach stand gar
# keine Adresse mehr da, das Skript beendete sich mit exit(1), und der
# Installer meldete Fehler 1721. Auf KK-WIN01 passiert.
#
# Die Pruefung davor sah nur nach, DASS die Zeile da ist. Sie prueft
# jetzt, was sie BEWIRKT - und zwar fuer alle vier Wege, auf denen
# installiert wird. Das ist ohne Windows moeglich: es ist reine
# Python-Logik ueber ein dict.
print()
print("--- Konfigurationslogik des Installationsskripts (F-46) ---")

_marker = "TASK_SCRIPT = r" + "\'\'\'"
_ts = msi.split(_marker, 1)[1].split("\'\'\'", 1)[0]
check("TASK_SCRIPT laesst sich herausloesen", len(_ts) > 500, len(_ts))

# Nur den Teil ausfuehren, der die Konfiguration zusammensetzt: vom
# Einlesen bis vor das Schreiben. Alles davor und danach braucht Windows.
_von = _ts.find("existing = {}")
_bis = _ts.find("# Ohne Serveradresse ist der Agent nicht lauffaehig")
check("der Konfigurationsteil ist auffindbar", 0 < _von < _bis, f"{_von}..{_bis}")


def _konfig(datei_inhalt, argv_server, argv_verify="true", besitz="JA"):
    """
    Fuehrt genau den Abschnitt des erzeugten Skripts aus.

    'besitz' bildet die Antwort der Besitzpruefung nach (F-46): "JA" =
    die vorhandene agent.conf stammt von einem Administrator, "NEIN" =
    sie wurde untergeschoben. Der echte Weg dorthin ist ein
    PowerShell-Aufruf, den es hier nicht gibt - deshalb die Attrappe.
    """
    import types as _t

    class _Res:
        returncode = 0
        stdout = besitz
        stderr = ""

    class _Datei:
        def exists(self):
            return datei_inhalt is not None

        def read_text(self, encoding=None):
            return datei_inhalt

    raum = {
        "server": argv_server,
        "verify": argv_verify,
        "CONF": _Datei(),
        "SYS32": Path("C:/Windows/System32"),
        "os": _t.SimpleNamespace(environ={}),
        "subprocess": _t.SimpleNamespace(run=lambda *a, **k: _Res()),
        "sys": _t.SimpleNamespace(
            stderr=_t.SimpleNamespace(write=lambda x: None)),
    }
    exec(_ts[_von:_bis], raum)  # noqa: S102
    return raum["existing"]


_alt = ("server = https://co37.itkub.net\n"
        "token = abc123def456ghi789\n"
        "verify_ssl = false\n"
        "boeses = x\n")

# 1. Der Fall, der 1721 ausgeloest hat.
_e = _konfig(_alt, "")
check("Upgrade per Doppelklick behaelt den Server",
      _e.get("server") == "https://co37.itkub.net", _e)
check("und behaelt das Token",
      _e.get("token") == "abc123def456ghi789", _e)

# 2. Die Befehlszeile gewinnt.
_e = _konfig(_alt, "https://neu.example")
check("CO37SERVER ueberschreibt den alten Server",
      _e.get("server") == "https://neu.example", _e)

# 3. Was aus einer vorhandenen Datei NICHT uebernommen wird.
_e = _konfig(_alt, "")
check("verify_ssl aus der Datei wird verworfen",
      "verify_ssl" not in _e, _e)
check("unbekannte Schluessel werden verworfen", "boeses" not in _e, _e)

# 4. Nur wenn es diesmal mitgegeben wird, gilt verify_ssl.
_e = _konfig(_alt, "https://x", argv_verify="false")
check("verify_ssl von der Befehlszeile gilt",
      _e.get("verify_ssl") == "false", _e)

# 5. Erstinstallation ohne alles muss weiterhin scheitern.
_e = _konfig(None, "")
check("Erstinstallation ohne alles hat keinen Server", not _e.get("server"), _e)

# 6. Untergeschobene Datei: nichts davon wird uebernommen (F-46).
_e = _konfig(_alt, "", besitz="NEIN")
check("untergeschobene agent.conf: Server wird verworfen",
      not _e.get("server"), _e)
check("untergeschobene agent.conf: auch das Token wird verworfen",
      not _e.get("token"), _e)

# 7. Aber mit CO37SERVER laeuft die Installation trotzdem - der Angreifer
#    soll sie nicht verhindern koennen.
_e = _konfig(_alt, "https://neu", besitz="NEIN")
check("untergeschobene Datei blockiert eine Installation mit CO37SERVER nicht",
      _e.get("server") == "https://neu", _e)

# ======================================================================
# Ein Abbruch darf den Host nicht ohne Agent zuruecklassen (F-48)
# ======================================================================
# In der InstallExecuteSequence steht RemoveExistingProducts nach
# InstallInitialize. Bei einem Upgrade loescht dabei RemoveTask der ALTEN
# Fassung die geplante Aufgabe; erst danach kommt RegisterTask. Scheitert
# dieses Skript dazwischen, ist die Aufgabe weg - und eine CustomAction
# hat keine Rueckrollaktion, der Installer stellt sie nicht wieder her.
# Der Host steht dann ganz ohne Agent da und meldet sich nie wieder.
#
# Am 2026-09-01 auf KK-WIN01 genau so passiert.
#
# Geprueft wird der ganze Ablauf, nicht nur die Konfiguration: schtasks
# und icacls werden abgefangen, und wir sehen, WAS aufgerufen worden
# waere.
print()
print("--- Ein Abbruch laesst keine Aufgabe verschwinden (F-48) ---")

_seq = msi[msi.find("<InstallExecuteSequence>"):msi.find("</InstallExecuteSequence>")]
check("RemoveExistingProducts laeuft vor RegisterTask",
      "RemoveExistingProducts" in _seq and "RegisterTask" in _seq)

import types as _types  # noqa: E402


def _ablauf(conf_inhalt, argv_server):
    """
    Fuehrt den Ablauf des erzeugten Skripts nach - von 'gab_es_schon' bis
    vor die Wiederholungspruefung. Gibt zurueck, ob abgebrochen wurde und
    ob die Aufgabe angelegt worden waere.
    """
    aufrufe = []

    class _Res:
        returncode = 0
        stderr = ""
        # "JA" = die Besitzpruefung (F-46) haelt die vorhandene Datei fuer
        # vertrauenswuerdig. Hier geht es um F-48, nicht um F-46 - der
        # Wert muss nur definiert sein.
        stdout = "JA"

    def _run(cmd, **kw):
        aufrufe.append(next((a for a in cmd if str(a).startswith("/")),
                            str(cmd[0])))
        return _Res()

    def _ende(code=0):
        # MUSS werfen. Die erste Fassung dieser Attrappe war
        # 'exit=SystemExit' - die Klasse, nicht eine Funktion, die wirft.
        # sys.exit(1) hat damit nur ein Objekt erzeugt, das Skript lief
        # weiter, und alle fuenf Faelle meldeten "durchgelaufen". Eine
        # Attrappe, die den geprueften Fall gar nicht herstellt, prueft
        # nichts.
        raise SystemExit(code)

    class _Datei:
        def __init__(self, i):
            self.inhalt = i
            self.geschrieben = None

        def exists(self):
            return self.inhalt is not None

        def read_text(self, encoding=None):
            return self.inhalt

        def write_text(self, t, encoding=None):
            self.geschrieben = t

    class _Ordner:
        def mkdir(self, **kw):
            pass

        def __truediv__(self, x):
            return self

    _conf = _Datei(conf_inhalt)
    _raum = {
        "os": _types.SimpleNamespace(environ={"SystemRoot": r"C:\Windows"}),
        "subprocess": _types.SimpleNamespace(run=_run),
        "sys": _types.SimpleNamespace(
            argv=["x", argv_server, "true"],
            stderr=_types.SimpleNamespace(write=lambda s: None),
            exit=_ende),
        "Path": Path,
        "CONF": _conf, "CONF_DIR": _Ordner(), "INSTALL_DIR": _Ordner(),
        "ICACLS": "icacls.exe", "SCHTASKS": "schtasks.exe",
        "SYS32": Path("C:/Windows/System32"),
        "server": argv_server, "verify": "true",
    }
    _von = _ts.find("# Lief auf diesem Rechner")
    _bis = _ts.find("# Nachpruefen statt vertrauen")
    abgebrochen = False
    try:
        exec(_ts[_von:_bis], _raum)  # noqa: S102
    except SystemExit:
        abgebrochen = True
    return abgebrochen, "/Create" in aufrufe


check("der Ablaufteil ist auffindbar",
      0 < _ts.find("# Lief auf diesem Rechner")
      < _ts.find("# Nachpruefen statt vertrauen"))

# Die Attrappe muss den Abbruch ueberhaupt herstellen koennen - sonst
# sagen die Faelle darunter nichts aus.
_ab, _ = _ablauf(None, "")
check("die Attrappe stellt einen Abbruch her", _ab is True)

# Der Fall von KK-WIN01: Upgrade, Konfiguration unbrauchbar.
_ab, _aufgabe = _ablauf("token = abc\n", "")
check("Upgrade mit unbrauchbarer Konfiguration bricht ab", _ab is True)
check("aber die Aufgabe wird vorher wiederhergestellt", _aufgabe is True)

# Erstinstallation ohne alles: nichts anlegen, was ins Leere zeigt.
_ab, _aufgabe = _ablauf(None, "")
check("Erstinstallation ohne alles bricht ab", _ab is True)
check("und legt keine Aufgabe an", _aufgabe is False)

# Die Normalwege bleiben Normalwege.
for _inhalt, _srv, _name in (
        ("server = https://x\ntoken = abc\n", "", "Upgrade per Doppelklick"),
        ("server = https://x\ntoken = abc\n", "https://neu", "Upgrade mit CO37SERVER"),
        (None, "https://neu", "Erstinstallation mit CO37SERVER")):
    _ab, _aufgabe = _ablauf(_inhalt, _srv)
    check(f"{_name}: laeuft durch", _ab is False)
    check(f"{_name}: Aufgabe angelegt", _aufgabe is True)

# ======================================================================
# Windows-Hilfsprogramme mit vollem Pfad (F-47)
# ======================================================================
# Agent und Installationsskript laufen als SYSTEM. CreateProcess
# durchsucht bei einem blossen Namen unter anderem das aktuelle
# Verzeichnis und PATH - beides muss nicht dem gehoeren, dem der Prozess
# gehoert. Ob das auf einem konkreten Windows ausnutzbar ist, laesst sich
# von hier nicht klaeren; der volle Pfad kostet nichts.
print()
print("--- Volle Pfade fuer Windows-Hilfsprogramme (F-47) ---")
check("der Agent bildet den System32-Pfad", "def _sys32" in agent_quelle)
check("der Agent ruft powershell ueber die Konstante auf",
      "POWERSHELL = _sys32(" in agent_quelle
      and '"powershell.exe", "-NoProfile"' not in agent_quelle)
check("der Agent ruft shutdown ueber die Konstante auf",
      "SHUTDOWN = _sys32(" in agent_quelle
      and '["shutdown.exe"' not in agent_quelle)
check("faellt auf den blossen Namen zurueck, wenn die Datei fehlt",
      "return str(pfad) if pfad.is_file() else name" in agent_quelle)
check("das Installationsskript benutzt volle Pfade",
      "SCHTASKS = str(SYS32" in msi and "ICACLS = str(SYS32" in msi)

# Die Pfade werden in einem raw-String erzeugt - dort ergibt \\ ZWEI
# Backslashes, nicht einen. Ein so entstandener Ruecklfallpfad
# ("C:\\\\Windows") faellt beim Bauen nicht auf und erst auf einem
# Windows ohne SystemRoot. Deshalb nicht den Wortlaut pruefen, sondern
# den Wert ausrechnen.
_umgebung = {"os": os, "Path": Path}
for _zeile in _ts.splitlines():
    if _zeile.startswith(("SYS32 =", "ICACLS =", "SCHTASKS =")):
        exec(_zeile, _umgebung)  # noqa: S102
_ohne_sysroot = {"os": type("o", (), {"environ": {}})(), "Path": Path}
for _zeile in _ts.splitlines():
    if _zeile.startswith("SYS32 ="):
        exec(_zeile, _ohne_sysroot)  # noqa: S102
_rueckfall = str(_ohne_sysroot["SYS32"])
check("der Rueckfallpfad hat keine doppelten Backslashes",
      "\\\\" not in _rueckfall, _rueckfall)
check("der Rueckfallpfad endet auf System32",
      _rueckfall.replace("/", "\\").endswith("Windows\\System32"), _rueckfall)
check("keine blossen schtasks-Aufrufe mehr im MSI-Bau",
      '["schtasks", ' not in msi and '"schtasks", "/Create"' not in msi)

# ======================================================================
# Private Schluessel entstehen mit 0600 (F-35)
# ======================================================================
# Bis 0.37.9 stand dort write_bytes() und DANACH chmod(0600). Zwischen
# beiden lag die Datei mit der Umask-Vorgabe auf der Platte, ueblich
# 0644, in einem Verzeichnis mit 0755. Wer den Release-Schluessel in
# diesem Fenster liest, kann Code als root auf jedem Kundensystem
# ausfuehren. Genau derselbe Fall wird in setup.sh mit 'umask 177'
# richtig geloest.
print()
print("--- Private Schluessel entstehen zu (F-35) ---")
import importlib.util as _iu  # noqa: E402

# Ueber den Syntaxbaum: eine Zeichenkettensuche nach "0o600" faende den
# Wert auch in einem chmod() NACH dem Schreiben - und genau das ist der
# Befund. Geprueft wird deshalb, dass der Modus am os.open() haengt und
# dass in der Funktion ueberhaupt kein chmod auf die Zieldatei mehr
# vorkommt.
for werkzeug in ("sign-release.py", "make-license.py"):
    _q = (WURZEL / "tools" / werkzeug).read_text(encoding="utf-8")
    _fn = next((k for k in ast.walk(ast.parse(_q))
                if isinstance(k, ast.FunctionDef)
                and k.name == "schluessel_schreiben"), None)
    check(f"{werkzeug} hat schluessel_schreiben()", _fn is not None)
    if _fn:
        _open = [k for k in ast.walk(_fn) if isinstance(k, ast.Call)
                 and isinstance(k.func, ast.Attribute) and k.func.attr == "open"]
        check(f"{werkzeug} legt die Datei mit os.open an", len(_open) == 1)
        check(f"{werkzeug} gibt den Modus 0600 beim Anlegen mit",
              bool(_open) and len(_open[0].args) >= 3
              and getattr(_open[0].args[2], "value", None) == 0o600,
              ast.dump(_open[0]) if _open else "")
        check(f"{werkzeug} setzt die Rechte der Datei nicht erst hinterher",
              not [k for k in ast.walk(_fn) if isinstance(k, ast.Call)
                   and isinstance(k.func, ast.Attribute)
                   and k.func.attr == "chmod"
                   and "parent" not in ast.dump(k)])
        check(f"{werkzeug} schreibt die Datei nicht mit write_bytes",
              not [k for k in ast.walk(_fn) if isinstance(k, ast.Call)
                   and isinstance(k.func, ast.Attribute)
                   and k.func.attr == "write_bytes"])
    check(f"{werkzeug} setzt die Rechte nicht erst hinterher",
          "PRIVAT.chmod(0o600)" not in _q)

# Und die Wirkung, unter einer Umask, die den Fehler sichtbar machen
# wuerde.
_spec = _iu.spec_from_file_location("sr_haertung", WURZEL / "tools" / "sign-release.py")
_m = _iu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_m)
except SystemExit:
    pass
_alt = os.umask(0o022)
try:
    _d = Path(tempfile.mkdtemp()) / "co37"
    _z = _d / "release-private.pem"
    _m.schluessel_schreiben(_z, b"-----BEGIN PRIVATE KEY-----\n")
    check("der Schluessel liegt mit 0600 auf der Platte",
          oct(_z.stat().st_mode & 0o777) == "0o600",
          oct(_z.stat().st_mode & 0o777))
    check("das Verzeichnis darueber ist 0700",
          oct(_d.stat().st_mode & 0o777) == "0o700",
          oct(_d.stat().st_mode & 0o777))
    try:
        _m.schluessel_schreiben(_z, b"neu")
        _ueberschrieben = True
    except FileExistsError:
        _ueberschrieben = False
    check("ein vorhandener Schluessel wird nicht ueberschrieben",
          not _ueberschrieben)
finally:
    os.umask(_alt)


# ======================================================================
# Rechte: /inheritance:r allein raeumt untergeschobene Eintraege nicht
# weg (F-49)
# ======================================================================
# Am 2026-09-01 auf einem Windows-Testhost nachgestellt:
#
#   vorher:  Jeder  FullControl  vererbt=False   <- untergeschoben
#            SYSTEM FullControl  vererbt=True
#   icacls <datei> /inheritance:r /grant:r SYSTEM Administratoren
#   nachher: Jeder  FullControl  vererbt=False   <- UEBERLEBT
#            SYSTEM FullControl  vererbt=False
#            Administratoren     vererbt=False
#
# "/inheritance:r" entfernt nur die VERERBTEN Eintraege, "/grant:r"
# ersetzt nur die genannten Identitaeten. C:\ProgramData vererbt
# "Benutzer: Write" und ERSTELLER-BESITZER (ebenfalls nachgemessen) - wer
# dort vor der Installation eine agent.conf hinterlegt und sich selbst
# Vollzugriff gibt, kann sie danach weiter lesen und schreiben. Damit war
# F-06 auf einem so vorbereiteten Host nie behoben.
#
# Wirksam ist erst: setowner (der Besitzer darf die Rechte sonst selbst
# zurueckdrehen), dann reset (raeumt alle ausdruecklichen Eintraege),
# dann inheritance:r und grant:r. Auf dem Testhost geprueft - danach
# stehen genau zwei Eintraege.
print()
print("--- Rechte werden vollstaendig neu gesetzt (F-49) ---")


def _icacls_schritte(quelle, funktionsname):
    """Die icacls-Aufrufe einer Funktion in der Reihenfolge des Codes."""
    fn = next((k for k in ast.walk(ast.parse(quelle))
               if isinstance(k, ast.FunctionDef) and k.name == funktionsname), None)
    if fn is None:
        return None
    schalter = []
    for k in ast.walk(fn):
        if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                and k.value.startswith("/"):
            schalter.append(k.value)
    return schalter


_agent_schritte = _icacls_schritte(agent_quelle, "sichere_rechte")
check("agent.py: sichere_rechte() gefunden", _agent_schritte is not None)
if _agent_schritte:
    for _s in ("/setowner", "/reset", "/inheritance:r", "/grant:r"):
        check(f"agent.py: {_s} kommt vor", _s in _agent_schritte, _agent_schritte)
    check("agent.py: setowner steht vor reset",
          _agent_schritte.index("/setowner") < _agent_schritte.index("/reset"))
    check("agent.py: reset steht vor inheritance:r",
          _agent_schritte.index("/reset") < _agent_schritte.index("/inheritance:r"))

# Ueber den Syntaxbaum, NICHT ueber Textpositionen: die Begruendung
# oben nennt "/inheritance:r" im Kommentar, und zwar vor dem Code. Die
# erste Fassung dieser Pruefung verglich _ts.index(...) und meldete
# deshalb einen Fehler, den es nicht gab. Zum fuenften Mal dieselbe
# Falle - Zeichenkettensuchen treffen Kommentare.
_tsbaum2 = ast.parse(_ts)
_icacls_schleife = None
for _k in ast.walk(_tsbaum2):
    if isinstance(_k, ast.For):
        _text = ast.dump(_k)
        if "CONF_DIR" in _text and "setowner" in _text:
            _icacls_schleife = _k
            break
check("Installationsskript: die Rechte-Schleife ist auffindbar",
      _icacls_schleife is not None)

if _icacls_schleife is not None:
    _schalter = [k.value for k in ast.walk(_icacls_schleife)
                 if isinstance(k, ast.Constant) and isinstance(k.value, str)
                 and k.value.startswith("/")]
    for _s in ("/setowner", "/reset", "/inheritance:r", "/grant:r"):
        check(f"Installationsskript: {_s} kommt vor", _s in _schalter, _schalter)
    check("Installationsskript: setowner steht vor reset",
          _schalter.index("/setowner") < _schalter.index("/reset"), _schalter)
    check("Installationsskript: reset steht vor inheritance:r",
          _schalter.index("/reset") < _schalter.index("/inheritance:r"),
          _schalter)

    # Ordner vor Datei - sonst erbt die Datei beim reset die alten
    # Ordnerrechte zurueck.
    _ziele = [k.id for k in ast.walk(_icacls_schleife.iter)
              if isinstance(k, ast.Name)]
    check("Installationsskript: Ordner wird vor der Datei behandelt",
          _ziele.index("CONF_DIR") < _ziele.index("CONF"), _ziele)

# ======================================================================
# Eine vorbelegte agent.conf wird an ihrem BESITZER erkannt (F-46)
# ======================================================================
# Auf dem Testhost nachgemessen:
#
#   von SYSTEM angelegt (Installer) -> Besitzer VORDEFINIERT\Administratoren
#   von einem Benutzer vorbelegt    -> Besitzer bleibt dieser Benutzer,
#                                      AUCH nachdem die Installation lief
#
# Die Rechte taugen dafuer NICHT: wer Besitzer ist, darf sie selbst
# setzen und kann eine untergeschobene Datei genauso aussehen lassen wie
# eine installierte. Den Besitz an die Administratoren abzugeben kann ein
# Unprivilegierter dagegen nicht. Der Vorschlag im Pruefdokument, die
# Vererbung als Merkmal zu nehmen, war damit falsch.
print()
print("--- Vorbelegte agent.conf am Besitzer erkennen (F-46) ---")
check("es gibt eine Besitzpruefung",
      "def von_privilegierter_hand" in _ts)
check("ein Administrator gilt als vertrauenswuerdig",
      "S-1-5-32-544" in _ts and "Get-LocalGroupMember" in _ts)
check("die Gruppe wird ueber die SID angesprochen, nicht ueber den Namen",
      "-SID 'S-1-5-32-544'" in _ts)

# Der Pfad darf NICHT als Argument uebergeben werden: "powershell
# -Command <skript> <arg>" fuellt $args nicht, sondern versucht <arg> als
# eigenen Befehl auszufuehren. Auf dem Testhost gesehen - die erste
# Fassung lief immer in den Fehlerzweig und haette wegen des Rueckfalls
# JEDE Datei durchgewinkt.
# Nicht nur, dass die Variable im Skripttext steht - sondern dass der
# Aufruf sie auch UEBERGIBT und den Pfad nicht als Argument anhaengt.
# Genau daran ist die erste Fassung gescheitert, und eine Suche nach
# "CO37_CONF_PRUEFEN" haette sie fuer richtig gehalten.
check("der Pfad steht nicht mehr in $args", "$args[0]" not in _ts)
_vph = next((k for k in ast.walk(ast.parse(_ts))
             if isinstance(k, ast.FunctionDef)
             and k.name == "von_privilegierter_hand"), None)
check("von_privilegierter_hand() ist auffindbar", _vph is not None)
if _vph is not None:
    _run = next((k for k in ast.walk(_vph)
                 if isinstance(k, ast.Call)
                 and isinstance(k.func, ast.Attribute)
                 and k.func.attr == "run"), None)
    check("sie ruft subprocess.run auf", _run is not None)
    if _run is not None:
        _kw = {k.arg for k in _run.keywords}
        check("der Aufruf uebergibt eine eigene Umgebung", "env" in _kw, _kw)
        # Die Befehlsliste muss mit dem Skript enden - kommt danach noch
        # etwas, versucht powershell es als eigenen Befehl auszufuehren.
        _liste = _run.args[0]
        _letztes = _liste.elts[-1] if isinstance(_liste, ast.List) else None
        check("die Befehlsliste endet mit dem Skript",
              isinstance(_letztes, ast.Name) and _letztes.id == "skript",
              ast.dump(_letztes) if _letztes else "keine Liste")
check("nur ein ausdrueckliches JA gilt als vertrauenswuerdig",
      'antwort == "JA"' in _ts)
check("eine unklare Antwort wird protokolliert",
      "Besitzpruefung der agent.conf nicht moeglich" in _ts)

# Und die Wirkung im Ablauf: eine als untergeschoben erkannte Datei darf
# nichts beitragen - auch das Token nicht.
_i = _ts.find("if existing and not von_privilegierter_hand")
check("die Pruefung wird im Ablauf verwendet", _i > 0)
check("bei einer untergeschobenen Datei wird alles verworfen",
      "existing = {}" in _ts[_i:_i + 500], _ts[_i:_i + 400])


# ======================================================================
# Die mitgelieferten Skripte uebersetzen sauber (2026-09-02)
# ======================================================================
# Der Anlass: install_task.py und uninstall_task.py stehen in build_msi.py
# als rohe Zeichenketten (r'''...'''). Was darin steht, wird NICHT beim
# Uebersetzen von build_msi.py geprueft - es ist dort nur Text. Geprueft
# wird es erst auf dem Zielrechner, beim Installationslauf.
#
# Am 2026-09-02 auf dem Windows-Testhost gesehen: der Docstring von
# von_privilegierter_hand nennt "VORDEFINIERT\\Administratoren". Aussen
# ist alles roh, der Docstring drinnen war es nicht - Python meldete bei
# JEDEM Lauf
#
#     SyntaxWarning: "\\A" is an invalid escape sequence
#
# nach stderr. Heute eine Warnung, ab Python 3.15 ein Fehler; eine
# CustomAction, die etwas nach stderr schreibt, ist ausserdem genau das,
# was einen Installationslauf schwer lesbar macht.
#
# Uebersetzt wird mit aufgezeichneten Warnungen: eine einzige reicht zum
# Durchfallen.
print()
print("--- Die erzeugten Windows-Skripte uebersetzen ohne Warnung ---")

import warnings as _warn  # noqa: E402

_skripte = {}
for _name in ("TASK_SCRIPT", "UNINSTALL_SCRIPT"):
    for _k in ast.parse(msi).body:
        if (isinstance(_k, ast.Assign)
                and getattr(_k.targets[0], "id", "") == _name):
            _skripte[_name] = ast.literal_eval(_k.value)

check("beide Skripte sind auffindbar", len(_skripte) == 2, list(_skripte))

for _name, _text in _skripte.items():
    with _warn.catch_warnings(record=True) as _w:
        _warn.simplefilter("always")
        try:
            compile(_text, _name, "exec")
            _fehler = [str(x.message) for x in _w]
        except SyntaxError as _exc:
            _fehler = [f"SyntaxError: {_exc}"]
    check(f"{_name} uebersetzt ohne Warnung", not _fehler, _fehler)

# Gegenprobe: dieselbe Pruefung schlaegt an, wenn die Warnung wieder
# hineinkommt. Ohne das sagte die Reihe nichts - ein compile(), das
# stillschweigend alles durchlaesst, sieht genauso aus.
_kaputt = 'def f():\n    """C:\\Administratoren"""\n'
with _warn.catch_warnings(record=True) as _w:
    _warn.simplefilter("always")
    compile(_kaputt, "gegenprobe", "exec")
check("die Pruefung erkennt eine ungueltige Folge ueberhaupt",
      any("escape" in str(x.message) for x in _w),
      [str(x.message) for x in _w])



# ======================================================================
# Die geplante Aufgabe ueberlebt ein Upgrade (F-48, grosse Variante)
# ======================================================================
# Die kleine Variante (0.37.12) stellt die Aufgabe im Fehlerfall wieder
# her. Die grosse sorgt dafuer, dass sie beim Upgrade gar nicht erst
# geloescht wird: RemoveTask haengt jetzt zusaetzlich an
# "NOT UPGRADINGPRODUCTCODE".
#
# Windows Installer deinstalliert die alte Fassung waehrend eines Upgrades
# ganz gewoehnlich, mit REMOVE=ALL - deshalb griff die alte Bedingung auch
# dort. UPGRADINGPRODUCTCODE ist genau in diesem Fall gesetzt.
#
# AM 2026-09-03 AUF DEM WINDOWS-TESTHOST GEMESSEN, mit zwei winzigen
# Wegwerf-Paketen, die nur diese beiden Aktionen tragen und in ein
# Protokoll schreiben:
#
#   ohne Bedingung   Upgrade -> REGISTER 1.0.0, REMOVE 1.0.0, REGISTER 2.0.0
#   mit  Bedingung   Upgrade -> REGISTER 1.0.0,               REGISTER 2.0.0
#   beide            Deinstallation -> REMOVE 2.0.0
#
# Die dritte Zeile ist die wichtige Gegenprobe: die Bedingung darf die
# Aufgabe bei einer echten Deinstallation nicht stehen lassen.
print()
print("--- RemoveTask laeuft beim Upgrade nicht mehr (F-48 gross) ---")

_ies = msi[msi.index("<InstallExecuteSequence>"):
           msi.index("</InstallExecuteSequence>")]
_zeilen = [z.strip() for z in _ies.splitlines()
           if "<Custom Action=" in z]
_remove = next((z for z in _zeilen if 'Action="RemoveTask"' in z), "")
_register = next((z for z in _zeilen if 'Action="RegisterTask"' in z), "")
check("die RemoveTask-Zeile ist auffindbar", bool(_remove), _zeilen)
check("RemoveTask laeuft beim Upgrade nicht",
      "NOT UPGRADINGPRODUCTCODE" in _remove, _remove)
check("bei einer echten Deinstallation aber schon",
      'REMOVE="ALL"' in _remove, _remove)
# Gegenprobe: die Bedingung darf NICHT versehentlich auch an RegisterTask
# haengen - dann liefe beim Upgrade weder das eine noch das andere, und
# der Host haette danach gar keine Aufgabe mehr.
check("RegisterTask traegt die Bedingung nicht",
      "UPGRADINGPRODUCTCODE" not in _register, _register)

# Und der Bau muss es nachpruefen. Eine Bedingung, die beim naechsten
# Umbau still verschwindet, faellt sonst erst beim uebernaechsten Upgrade
# eines Kunden auf.
check("build_msi.py kennt eine erwartete Bedingung",
      "ERWARTETE_BEDINGUNG" in msi
      and "UPGRADINGPRODUCTCODE" in msi.split("ERWARTETE_BEDINGUNG")[1][:200],
      msi.split("ERWARTETE_BEDINGUNG")[1][:120] if "ERWARTETE_BEDINGUNG" in msi else "")
_pp = next((k for k in ast.walk(ast.parse(msi))
            if isinstance(k, ast.FunctionDef) and k.name == "pruefe_paket"), None)
check("pruefe_paket() wertet sie aus",
      _pp is not None and "ERWARTETE_BEDINGUNG" in ast.unparse(_pp))

# Wenn die Werkzeuge da sind: wirklich bauen und wirklich nachsehen.
# Ohne wixl wird das gesagt statt uebergangen - eine ausgelassene
# Pruefung, die wie eine bestandene aussieht, ist schlimmer als keine.
if shutil.which("wixl") and shutil.which("msiinfo"):
    _pdir = Path(tempfile.mkdtemp())
    (_pdir / "marker.txt").write_text("probe\n")
    _wxs = """<?xml version="1.0"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="P" Language="1033" Version="1.0.0" Manufacturer="P"
           UpgradeCode="{3C1F5A70-9B2E-4D61-8A03-77E5C4B2A1D9}">
    <Package InstallerVersion="200" Compressed="yes" InstallScope="perMachine"/>
    <Media Id="1" Cabinet="p.cab" EmbedCab="yes"/>
    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFilesFolder">
        <Directory Id="INSTALLDIR" Name="P">
          <Component Id="C.m" Guid="{8A1B2C3D-4E5F-6071-8293-A4B5C6D7E8F0}">
            <File Id="F.m" Name="marker.txt" Source="marker.txt" KeyPath="yes"/>
          </Component>
        </Directory>
      </Directory>
    </Directory>
    <Feature Id="M" Level="1"><ComponentRef Id="C.m"/></Feature>
    <Property Id="CO37PYEXE" Value="x"/>
    <CustomAction Id="RemoveTask" Property="CO37PYEXE" ExeCommand="x"
                  Execute="deferred" Impersonate="no" Return="ignore"/>
    <InstallExecuteSequence>
      <Custom Action="RemoveTask" Sequence="3499">BEDINGUNG</Custom>
    </InstallExecuteSequence>
  </Product>
</Wix>
"""
    _ergebnis = {}
    for _name, _bed in (("mit", 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE'),
                        ("ohne", 'REMOVE="ALL"')):
        (_pdir / f"{_name}.wxs").write_text(_wxs.replace("BEDINGUNG", _bed))
        _r = subprocess.run(["wixl", "-o", str(_pdir / f"{_name}.msi"),
                             str(_pdir / f"{_name}.wxs")],
                            capture_output=True, text=True, cwd=_pdir)
        if _r.returncode != 0:
            _ergebnis[_name] = f"wixl: {_r.stderr[-120:]}"
            continue
        _e = subprocess.run(["msiinfo", "export", str(_pdir / f"{_name}.msi"),
                             "InstallExecuteSequence"],
                            capture_output=True, text=True)
        _bedingungen = {z.split("\t")[0]: z.split("\t")[1]
                        for z in _e.stdout.splitlines() if "\t" in z}
        _ergebnis[_name] = _bedingungen.get("RemoveTask", "")
    check("wixl traegt die Bedingung wirklich ins Paket",
          "UPGRADINGPRODUCTCODE" in _ergebnis.get("mit", ""), _ergebnis.get("mit"))
    check("und ohne sie steht sie auch nicht drin",
          "UPGRADINGPRODUCTCODE" not in _ergebnis.get("ohne", "?"),
          _ergebnis.get("ohne"))
    shutil.rmtree(_pdir, ignore_errors=True)
else:
    print("       (wixl/msiinfo fehlen - der Bau selbst wurde NICHT geprueft)")


print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
