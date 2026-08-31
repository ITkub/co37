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
import os
import re
import shutil
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


# ======================================================================
# F-06 - nur statisch, siehe Kopf dieser Datei
# ======================================================================
print("--- Rechte an agent.conf (statisch) ---")
check("der Agent kennt eine Funktion dafuer",
      "def sichere_rechte(" in agent_quelle)
check("sie ruft icacls auf", '"icacls"' in agent_quelle)

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

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
