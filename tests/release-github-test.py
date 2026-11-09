"""
CO-37 - die Pruefungen vor dem Veroeffentlichen.

DER ANLASS

Acht Tags (v0.40.0 bis v0.45.1) zeigen auf Quelltext mit
VERSION = 0.39.0. Die Ursache ist eine Reihenfolge: build_release.py
SCHREIBT beim Bauen vier Versionsstempel, und die waren nicht committet,
als der Tag gesetzt wurde. Eine einzige Gegenprobe haette jedes Mal
gereicht - 'git show <tag>:backend/VERSION'.

tools/release-github.py macht aus dieser Gegenprobe ein Werkzeug. Es
schreibt nichts in git; Tag und Push bleiben Handarbeit. Es prueft, und
es verweigert.

WAS DIESE REIHE TUT

Sie baut in einem temporaeren Verzeichnis echte git-Repositories samt
eigenem origin, setzt Stempel, Tags und Pakete RICHTIG UND FALSCH, und
prueft, dass jede einzelne Pruefung anschlaegt, wenn sie soll.

Der wichtigste Fall ist der sechste: ein Tag auf dem Commit VOR den
Stempeln. Genau die Lage von 0.40.0. Wuerde die Reihe dabei gruen
bleiben, waere das Werkzeug wertlos - es gaebe vor zu pruefen, was es
nicht prueft.

Signiert wird mit einem eigenen Schluesselpaar, das hier entsteht. Der
echte private Schluessel liegt beim Herausgeber und wird nicht
gebraucht.

Laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/release-github-test.py
"""
import base64
import hashlib
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())

spec = importlib.util.spec_from_file_location(
    "relgh", WURZEL / "tools" / "release-github.py")
relgh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relgh)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


# ----------------------------------------------------------------------
# Ein vollstaendiges, KORREKTES Release in einem Wegwerf-Repository
# ----------------------------------------------------------------------
PRIVAT = Ed25519PrivateKey.generate()
PUB = base64.urlsafe_b64encode(
    PRIVAT.public_key().public_bytes_raw()).decode().rstrip("=")


def signiere(daten: bytes) -> str:
    """Wie sign-release.py: signiert wird der SHA-256 als Hexkette."""
    hexkette = hashlib.sha256(daten).hexdigest().encode("ascii")
    return base64.urlsafe_b64encode(PRIVAT.sign(hexkette)).decode().rstrip("=")


def stempel_schreiben(repo: Path, version: str):
    (repo / "backend").mkdir(parents=True, exist_ok=True)
    (repo / "agent").mkdir(parents=True, exist_ok=True)
    (repo / "frontend").mkdir(parents=True, exist_ok=True)
    (repo / "backend" / "VERSION").write_text(version + "\n", encoding="utf-8")
    (repo / "agent" / "agent.py").write_text(
        f'AGENT_VERSION = "{version}"\n', encoding="utf-8")
    (repo / "update_watcher.py").write_text(
        f'WATCHER_VERSION = "{version}"\n', encoding="utf-8")
    (repo / "frontend" / "index.html").write_text(
        f"<!-- CO37_FRONTEND_VERSION: {version} -->\n", encoding="utf-8")


def paket_schreiben(repo: Path, version: str, signatur_von: bytes = None):
    """Legt ZIP, .sha256 und .sig an. signatur_von: was STATTDESSEN
    signiert wird - fuer den Fall einer falschen Signatur."""
    name = "co37_v" + version.replace(".", "_") + ".zip"
    pfad = repo / name
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("backend/VERSION", version + "\n")
    daten = pfad.read_bytes()
    (repo / (name + ".sha256")).write_text(
        f"{hashlib.sha256(daten).hexdigest()}  {name}\n", encoding="utf-8")
    (repo / (name + ".sig")).write_text(
        signiere(signatur_von if signatur_von is not None else daten) + "\n",
        encoding="ascii")


def neues_repo(version="0.49.0") -> Path:
    """Ein Repository in dem Zustand, in dem alles stimmt."""
    repo = Path(tempfile.mkdtemp(dir=TMP))
    bare = Path(tempfile.mkdtemp(dir=TMP)) / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")

    stempel_schreiben(repo, version)

    # Dieselben Ausschluesse wie im echten Projekt. OHNE DIESE ZEILEN
    # misst die Reihe etwas anderes als der Betrieb tut: die drei
    # Paketdateien erscheinen dann als unverfolgt, und der Lauf scheitert
    # an der Sauberkeitspruefung statt an dem, was er pruefen will. Beim
    # ersten Lauf am 2026-11-09 genau so passiert.
    #
    # Dass das Werkzeug auf eine richtige .gitignore angewiesen ist, ist
    # kein Mangel, sondern die Absicht: eine unverfolgte Datei KANN
    # bedeuten, dass etwas Neues im Tag fehlt.
    (repo / ".gitignore").write_text(
        "co37_v*.zip\nco37_v*.zip.sig\nco37_v*.zip.sha256\n",
        encoding="utf-8")

    # release_sig.py aus dem echten Projekt, dazu UNSER oeffentlicher
    # Schluessel - damit prueft das Werkzeug mit dem Code des Projekts
    # und gegen ein Schluesselpaar, das nur hier existiert.
    shutil.copy2(WURZEL / "backend" / "release_sig.py",
                 repo / "backend" / "release_sig.py")
    (repo / "backend" / "release_key.pub").write_text(PUB + "\n", encoding="ascii")

    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"{version}")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "tag", "v" + version)

    paket_schreiben(repo, version)
    return repo


VERSION = "0.49.0"

# ----------------------------------------------------------------------
# 1. Der gute Fall
# ----------------------------------------------------------------------
# Zuerst, und das ist kein Formalismus: schlaegt hier schon etwas an,
# sind alle folgenden Pruefungen wertlos - sie wuerden rot, ohne dass
# der eingebaute Fehler etwas damit zu tun haette.
gut = neues_repo(VERSION)
befunde = relgh.pruefe(gut, VERSION)
check("ein vollstaendiges Release wird durchgewinkt", befunde == [], befunde)

# Die Fassung geht als Ref an git. Ohne Pruefung liest git eine
# Zeichenkette mit fuehrendem Bindestrich als OPTION, nicht als Namen.
check("eine Fassungsangabe, die git als Option lesen wuerde, wird abgewiesen",
      relgh.pruefe(gut, "--upload-pack=egal") != [])
check("ein Ref-Ausdruck als Fassung wird abgewiesen",
      relgh.pruefe(gut, "HEAD~3") != [])

# ----------------------------------------------------------------------
# 2. Arbeitsbaum nicht sauber
# ----------------------------------------------------------------------
# Eine VERFOLGTE Datei wird geaendert und nicht committet. Der Inhalt
# bleibt gueltig (dieselbe Fassung, nur eine Leerzeile mehr) - es geht
# allein darum, dass der Baum nicht sauber ist.
r = neues_repo(VERSION)
(r / "backend" / "VERSION").write_text("0.49.0\n\n", encoding="utf-8")
befunde = relgh.pruefe(r, VERSION)
check("ein schmutziger Arbeitsbaum faellt auf", befunde != [], befunde)
check("und zwar als Sauberkeitsbefund, nicht ueber einen Umweg",
      any("Arbeitsbaum" in b for b in befunde), befunde)

# ----------------------------------------------------------------------
# 3. Ein Stempel haengt zurueck
# ----------------------------------------------------------------------
# Der haeufigste Einzelfehler: drei Dateien gesetzt, eine vergessen.
r = neues_repo(VERSION)
(r / "update_watcher.py").write_text('WATCHER_VERSION = "0.48.0"\n', encoding="utf-8")
git(r, "add", "-A")
git(r, "commit", "-q", "-m", "stempel zurueck")
git(r, "push", "-q", "-f", "origin", "main")
git(r, "tag", "-f", "v" + VERSION)
befunde = relgh.pruefe(r, VERSION)
check("ein zurueckhaengender Versionsstempel faellt auf", befunde != [], befunde)
check("und der Befund nennt die betroffene Datei",
      any("update_watcher" in b for b in befunde), befunde)

# ----------------------------------------------------------------------
# 4. Stempel geschrieben, aber nicht committet
# ----------------------------------------------------------------------
r = neues_repo("0.48.0")
stempel_schreiben(r, VERSION)      # geschrieben
paket_schreiben(r, VERSION)
check("geschriebene, aber nicht committete Stempel fallen auf",
      relgh.pruefe(r, VERSION) != [])

# ----------------------------------------------------------------------
# 5. Der Tag fehlt
# ----------------------------------------------------------------------
r = neues_repo(VERSION)
git(r, "tag", "-d", "v" + VERSION)
check("ein fehlender Tag faellt auf", relgh.pruefe(r, VERSION) != [])

# ----------------------------------------------------------------------
# 6. DER FALL VON 0.40.0: der Tag zeigt auf den Commit VOR den Stempeln
# ----------------------------------------------------------------------
# Das Repository ist sauber, gepusht, der Tag existiert, das Paket
# stimmt - alles sieht richtig aus. Nur traegt der Commit, auf den der
# Tag zeigt, noch die alte Versionsnummer. Genau so ist es achtmal
# passiert, und genau das muss das Werkzeug fangen.
r = neues_repo("0.48.0")
alt = git(r, "rev-parse", "HEAD").stdout.strip()
stempel_schreiben(r, VERSION)
git(r, "add", "-A")
git(r, "commit", "-q", "-m", VERSION)
git(r, "push", "-q", "origin", "main")
git(r, "tag", "-f", "v" + VERSION, alt)      # <- auf den Stand DAVOR
paket_schreiben(r, VERSION)
befunde = relgh.pruefe(r, VERSION)
check("ein Tag auf dem Stand VOR den Stempeln faellt auf",
      befunde != [], befunde)
check("und der Befund nennt den Tag",
      any("v" + VERSION in b or "Tag" in b for b in befunde), befunde)

# ----------------------------------------------------------------------
# 7. Der Stand ist nicht gepusht
# ----------------------------------------------------------------------
r = neues_repo(VERSION)
(r / "README.md").write_text("nachtrag\n", encoding="utf-8")
git(r, "add", "-A")
git(r, "commit", "-q", "-m", "nachtrag")
git(r, "tag", "-f", "v" + VERSION)
check("ein ungepushter Stand faellt auf", relgh.pruefe(r, VERSION) != [])

# ----------------------------------------------------------------------
# 8. Das Paket fehlt
# ----------------------------------------------------------------------
r = neues_repo(VERSION)
(r / ("co37_v" + VERSION.replace(".", "_") + ".zip")).unlink()
check("ein fehlendes Paket faellt auf", relgh.pruefe(r, VERSION) != [])

# ----------------------------------------------------------------------
# 9. Die Pruefsumme passt nicht
# ----------------------------------------------------------------------
r = neues_repo(VERSION)
name = "co37_v" + VERSION.replace(".", "_") + ".zip"
(r / (name + ".sha256")).write_text(
    "0" * 64 + f"  {name}\n", encoding="utf-8")
check("eine falsche Pruefsumme faellt auf", relgh.pruefe(r, VERSION) != [])

# ----------------------------------------------------------------------
# 10. Die Signatur gehoert zu einem anderen Paket
# ----------------------------------------------------------------------
# Der haeufigste Bedienfehler beim Signieren, und er ist von aussen
# nicht zu sehen: Dateiname und Pruefsumme stimmen, die Signatur gilt
# einem anderen Inhalt.
r = neues_repo(VERSION)
paket_schreiben(r, VERSION, signatur_von=b"ein anderes Paket")
befunde = relgh.pruefe(r, VERSION)
check("eine Signatur zu einem anderen Paket faellt auf", befunde != [], befunde)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
