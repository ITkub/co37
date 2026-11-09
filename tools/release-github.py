#!/usr/bin/env python3
"""
CO-37 - Pruefungen vor dem Veroeffentlichen eines Releases.

    python tools/release-github.py 0.49.0

DER ANLASS

Acht Tags (v0.40.0 bis v0.45.1) zeigen auf Quelltext mit
VERSION = 0.39.0. Nicht weil jemand unachtsam war, sondern wegen einer
Reihenfolge: build_release.py SCHREIBT beim Bauen vier Versionsstempel,
und die waren nicht committet, als der Tag gesetzt wurde. Von aussen
sah jedes dieser Releases richtig aus - das ZIP trug die richtige
Fassung, nur der Tag zeigte daneben. Aufgefallen ist es erst am
2026-11-08, Wochen spaeter.

Eine einzige Gegenprobe haette jedes Mal gereicht:

    git show v<version>:backend/VERSION

Dieses Werkzeug macht daraus eine Schranke.

WAS ES NICHT TUT

Es schreibt nichts in git. Kein Tag, kein Push, kein Commit. Tag und
Push bleiben Handarbeit, und das ist Absicht: die Luecke lag nie im
Ausfuehren, sondern im Pruefen davor. Ein Werkzeug, das selbst taggt,
waere ein git-Wrapper mit eigenen Fehlerquellen.

Es laedt auch nichts hoch. Dafuer braeuchte es ein Token mit
Schreibrecht auf das Repository - eine dauerhafte Zugangsberechtigung
fuer etwas, das dreimal im Jahr vorkommt. Wenn sich im Einsatz zeigt,
dass das Hochladen von Hand wirklich stoert, laesst sich ein
--hochladen nachruesten. Vorher nicht.

AUFBAU

pruefe() nimmt ein Verzeichnis und eine Fassung und gibt eine Liste von
Befunden zurueck - leer heisst in Ordnung. Kein Netz, keine
Seiteneffekte, kein echtes Release noetig. Nur so laesst sich das
Werkzeug messen; eines, das sich ausschliesslich gegen GitHub pruefen
laesst, wird nie geprueft (siehe tests/release-github-test.py).
"""
import argparse
import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# Die vier Dateien, in die build_release.py die Fassung schreibt, mit
# dem Ausdruck, der sie dort wiederfindet. Wer hier eine ergaenzt, muss
# sie auch in build_release.py ergaenzen - und umgekehrt.
STEMPEL = {
    "backend/VERSION": None,                       # nackte Zahl
    "agent/agent.py": r'^AGENT_VERSION\s*=\s*"([^"]+)"',
    "update_watcher.py": r'^WATCHER_VERSION\s*=\s*"([^"]+)"',
    "frontend/index.html": r'CO37_FRONTEND_VERSION:\s*([0-9][^\s\-]*)',
}


def paketname(version: str) -> str:
    return "co37_v" + version.replace(".", "_") + ".zip"


def _git(wurzel: Path, *args):
    """Gibt (rueckgabe, ausgabe) zurueck. Wirft nicht - ein
    fehlgeschlagener git-Aufruf ist hier ein Befund, kein Absturz."""
    r = subprocess.run(["git", "-C", str(wurzel), *args],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _fassung_aus(text: str, muster) -> str:
    if muster is None:
        return text.strip()
    t = re.search(muster, text, re.MULTILINE)
    return t.group(1).strip() if t else ""


def stempel_im_baum(wurzel: Path) -> dict:
    """Die vier Fassungen, wie sie auf der Platte stehen."""
    out = {}
    for rel, muster in STEMPEL.items():
        p = wurzel / rel
        out[rel] = _fassung_aus(
            p.read_text(encoding="utf-8", errors="replace"), muster
        ) if p.is_file() else ""
    return out


def stempel_an_ref(wurzel: Path, ref: str) -> dict:
    """Dieselben vier Fassungen, wie sie an einem Tag oder Commit
    stehen. Das ist der eigentliche Punkt: nicht was auf der Platte
    liegt, sondern was jemand bekommt, der den Tag auscheckt."""
    out = {}
    for rel, muster in STEMPEL.items():
        rc, text = _git(wurzel, "show", f"{ref}:{rel}")
        out[rel] = _fassung_aus(text, muster) if rc == 0 else ""
    return out


def _release_sig(wurzel: Path):
    """Laedt die Signaturpruefung des Projekts - nicht eine zweite
    Kopie der Kryptologik."""
    pfad = wurzel / "backend" / "release_sig.py"
    if not pfad.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_relsig", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def pruefe(wurzel: Path, version: str) -> list:
    """
    Alle Pruefungen. Gibt die Befunde zurueck; leer heisst in Ordnung.

    Die Reihenfolge ist die des Ablaufs, nicht die der Wichtigkeit -
    wer den Lauf von oben liest, sieht, an welcher Stelle er steht.
    """
    wurzel = Path(wurzel)
    befunde = []

    # Die Fassung geht als REF an git ("git show v0.49.0:backend/VERSION").
    # Eine Shell ist nicht beteiligt - die Aufrufe gehen als Liste raus,
    # Injection ist damit ausgeschlossen. Aber eine Zeichenkette, die mit
    # '-' anfaengt, liest git als Option, und ein Ref-Ausdruck wie
    # 'HEAD~3' zeigt woandershin als gemeint. Alle Aufrufe hier lesen
    # nur; trotzdem wird geprueft, statt sich darauf zu verlassen.
    if not re.fullmatch(r"\d+(\.\d+)*", version or ""):
        return [f"'{version}' sieht nicht wie eine Fassung aus. Erwartet "
                f"wird etwas wie 0.49.0."]

    tag = "v" + version

    # 1. Sauberer Arbeitsbaum
    rc, aus = _git(wurzel, "status", "--porcelain")
    if rc != 0:
        return [f"Kein git-Repository in {wurzel}: {aus.strip()[:120]}"]
    if aus.strip():
        befunde.append(
            "Der Arbeitsbaum ist nicht sauber. Was nicht committet ist, "
            "steckt auch nicht im Tag:\n       "
            + "\n       ".join(aus.strip().splitlines()[:8]))

    # 2. Die vier Stempel auf der Platte
    for rel, gefunden in stempel_im_baum(wurzel).items():
        if gefunden != version:
            befunde.append(
                f"{rel} traegt {gefunden or '(nichts)'}, erwartet {version}. "
                f"Wurde build_release.py mit dieser Fassung gebaut?")

    # 3. Gepusht
    rc, oben = _git(wurzel, "rev-parse", "HEAD")
    rc2, drueben = _git(wurzel, "rev-parse", "origin/main")
    if rc != 0:
        befunde.append("HEAD laesst sich nicht lesen.")
    elif rc2 != 0:
        befunde.append(
            "origin/main ist nicht bekannt. Ohne Push zeigt der Tag auf "
            "GitHub ins Leere.")
    elif oben.strip() != drueben.strip():
        befunde.append(
            "main ist nicht gepusht. Der Tag wuerde auf einen Commit "
            "zeigen, den GitHub nicht hat.")

    # 4. Der Tag existiert
    rc, tag_commit = _git(wurzel, "rev-parse", "-q", "--verify", tag + "^{commit}")
    if rc != 0:
        befunde.append(f"Den Tag {tag} gibt es nicht.")
    else:
        # 5. und zeigt auf HEAD
        if tag_commit.strip() != oben.strip():
            befunde.append(
                f"Der Tag {tag} zeigt auf {tag_commit.strip()[:7]}, HEAD "
                f"steht auf {oben.strip()[:7]}.")

        # 6. DIE Pruefung. Was bekommt jemand, der den Tag auscheckt?
        #
        # Nummer 5 allein genuegt nicht: ein Tag kann auf HEAD zeigen
        # und HEAD trotzdem die alten Stempel tragen, wenn nach dem Bau
        # nicht committet wurde. Deshalb wird hier nicht verglichen,
        # wohin der Tag zeigt, sondern WAS DORT STEHT.
        for rel, gefunden in stempel_an_ref(wurzel, tag).items():
            if gefunden != version:
                befunde.append(
                    f"Am Tag {tag} traegt {rel} die Fassung "
                    f"{gefunden or '(nichts)'}, nicht {version}. Genau so "
                    f"sind v0.40.0 bis v0.45.1 entstanden: gebaut, "
                    f"getaggt, aber die Stempel nie committet.")

    # 7. Die drei Dateien
    name = paketname(version)
    zipdatei = wurzel / name
    sha = wurzel / (name + ".sha256")
    sig = wurzel / (name + ".sig")
    for p in (zipdatei, sha, sig):
        if not p.is_file():
            befunde.append(f"{p.name} fehlt. Erst bauen: "
                           f"python build_release.py {version}")
    if not zipdatei.is_file():
        return befunde

    daten = zipdatei.read_bytes()

    # 8. Pruefsumme
    if sha.is_file():
        erwartet = sha.read_text(encoding="ascii", errors="replace").split()
        gerechnet = hashlib.sha256(daten).hexdigest()
        if not erwartet or erwartet[0] != gerechnet:
            befunde.append(
                f"Die Pruefsumme passt nicht zu {name}. In der Datei steht "
                f"{(erwartet[0][:16] + '...') if erwartet else '(nichts)'}, "
                f"gerechnet {gerechnet[:16]}...")

    # 9. Signatur - mit dem Code des Projekts
    if sig.is_file():
        modul = _release_sig(wurzel)
        if modul is None:
            befunde.append("backend/release_sig.py fehlt - ohne sie laesst "
                           "sich die Signatur nicht pruefen.")
        elif not modul.zwang():
            befunde.append(
                "backend/release_key.pub fehlt. Ohne ihn wird nicht "
                "geprueft - und ein Paket, dessen Signatur niemand prueft, "
                "ist eines ohne Signatur.")
        else:
            try:
                modul.pruefen(daten, sig.read_text(encoding="ascii",
                                                   errors="replace"))
            except Exception as exc:  # noqa: BLE001
                befunde.append(f"Die Signatur von {name}: {exc}")

    return befunde


def main():
    p = argparse.ArgumentParser(
        description="Prueft, ob ein Release veroeffentlicht werden darf. "
                    "Schreibt nichts.")
    p.add_argument("version", help="z. B. 0.49.0")
    p.add_argument("--wurzel", default=None,
                   help="Projektverzeichnis (Vorgabe: das dieses Skripts)")
    a = p.parse_args()

    wurzel = Path(a.wurzel) if a.wurzel else Path(__file__).resolve().parent.parent
    befunde = pruefe(wurzel, a.version)

    if not befunde:
        print(f"In Ordnung. {paketname(a.version)} samt .sig und .sha256 "
              f"kann an das Release v{a.version} angehaengt werden.")
        print(f"  https://github.com/ITkub/co37/releases/new?tag=v{a.version}")
        return 0

    print(f"{len(befunde)} Befund(e) - NICHT veroeffentlichen:\n")
    for b in befunde:
        print(f"  - {b}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
