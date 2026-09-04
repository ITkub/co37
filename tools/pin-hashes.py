#!/usr/bin/env python3
"""
Erzeugt die --hash-Angaben in agent/requirements.txt neu.

WARUM ES DIESES SKRIPT GIBT

agent/requirements.txt bindet seit 0.37.16 nicht mehr nur an eine
Fassungsnummer, sondern an eine DATEI: jede Anforderung traegt den
sha256 des Rades, das ausgeliefert wird (--require-hashes im pip-Aufruf
von build_msi.py).

Welche Datei das ist, entscheidet nicht die Fassung allein, sondern die
drei Angaben im Bau: --platform win_amd64, --python-version aus
PY_VERSION und --only-binary=:all:. Wer PY_VERSION anhebt, bekommt fuer
die ABI-gebundenen Pakete (cffi, charset-normalizer) andere Raeder -
cp314 wird cp315 - und der Bau bricht mit einem Hash-Fehler ab. Genau
dann wird dieses Skript gebraucht.

Von Hand ginge es auch, aber falsch abgeschrieben ist ein Hash schnell,
und ein falscher Hash sieht aus wie ein Angriff.

WIE ES ARBEITET

Es fragt nicht PyPI nach allen Dateien einer Fassung, sondern laesst pip
denselben Aufruf trocken laufen, den der Bau macht, und liest aus dessen
Bericht die tatsaechlich gewaehlten Dateien samt Hash. Damit stimmt das
Ergebnis mit dem Bau ueberein statt nur plausibel zu sein.

Das Skript wird NICHT ausgeliefert (tools/ bleibt seit F-45 draussen).
Es ist ein Werkzeug fuer den Baurechner.

    python3 tools/pin-hashes.py              zeigt, was sich aendert
    python3 tools/pin-hashes.py --schreiben  traegt es ein
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
REQ = WURZEL / "agent" / "requirements.txt"
MSI = WURZEL / "packaging" / "build_msi.py"


def py_version() -> str:
    """Die Nebenversion aus build_msi.py - eine Quelle, nicht zwei."""
    text = MSI.read_text(encoding="utf-8")
    treffer = re.search(r'^PY_VERSION = "([0-9.]+)"', text, re.M)
    if not treffer:
        raise SystemExit("PY_VERSION steht nicht in packaging/build_msi.py")
    return ".".join(treffer.group(1).split(".")[:2])


def gewaehlte_dateien(kurz: str) -> dict[str, tuple[str, str, str]]:
    """
    Fragt pip, welche Dateien es fuer diesen Bau nehmen wuerde.

    Rueckgabe: name -> (fassung, dateiname, sha256).
    """
    with tempfile.TemporaryDirectory() as tmp:
        bericht = Path(tmp) / "bericht.json"
        res = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--dry-run", "--report", str(bericht),
             "--target", str(Path(tmp) / "site"),
             "--platform", "win_amd64",
             "--python-version", kurz,
             "--only-binary=:all:",
             "-r", str(REQ)],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            # Ohne die Hashes probieren: beim Anheben von PY_VERSION passen
            # die alten nicht mehr, und genau dann soll dieses Skript ja
            # helfen. Die Fassungen stehen weiterhin fest, es wird also
            # nichts Beliebiges aufgeloest.
            ohne = Path(tmp) / "ohne-hashes.txt"
            ohne.write_text(
                "\n".join(f"{n}=={v}" for n, v in
                          sorted(nur_pins(REQ.read_text(encoding="utf-8")).items())),
                encoding="utf-8")
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet",
                 "--dry-run", "--report", str(bericht),
                 "--target", str(Path(tmp) / "site2"),
                 "--platform", "win_amd64",
                 "--python-version", kurz,
                 "--only-binary=:all:",
                 "-r", str(ohne)],
                capture_output=True, text=True,
            )
            if res.returncode != 0:
                raise SystemExit(f"pip fehlgeschlagen:\n{res.stderr[-800:]}")
        daten = json.loads(bericht.read_text(encoding="utf-8"))

    aus = {}
    for eintrag in daten["install"]:
        meta = eintrag["metadata"]
        info = eintrag["download_info"]
        sha = info.get("archive_info", {}).get("hashes", {}).get("sha256")
        if not sha:
            raise SystemExit(
                f"pip nennt fuer {meta['name']} keinen sha256 - "
                f"kommt das Paket wirklich von einem Index?")
        aus[meta["name"].lower().replace("_", "-")] = (
            meta["version"], info["url"].rsplit("/", 1)[-1], sha)
    return aus


def nur_pins(text: str) -> dict[str, str]:
    """name -> fassung, ohne Hashes. Bewusst schlicht gehalten."""
    pins = {}
    zusammen = re.sub(r"\\\s*\n", " ", text)
    for zeile in zusammen.splitlines():
        z = zeile.split("#", 1)[0].strip()
        z = z.split("--hash=", 1)[0].strip()
        if "==" in z:
            n, _, v = z.partition("==")
            pins[n.strip().lower().replace("_", "-")] = v.strip()
    return pins


def neuer_block(gewaehlt: dict) -> str:
    zeilen = []
    for name in sorted(gewaehlt):
        fassung, _, sha = gewaehlt[name]
        zeilen.append(f"{name}=={fassung} \\")
        zeilen.append(f"    --hash=sha256:{sha}")
    return "\n".join(zeilen) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--schreiben", action="store_true",
                   help="die Datei tatsaechlich aendern")
    args = p.parse_args()

    kurz = py_version()
    print(f"PY_VERSION aus build_msi.py: {kurz}")
    gewaehlt = gewaehlte_dateien(kurz)

    alt = nur_pins(REQ.read_text(encoding="utf-8"))
    for name in sorted(set(alt) | set(gewaehlt)):
        if name not in gewaehlt:
            print(f"  ENTFAELLT  {name}=={alt[name]}")
        elif name not in alt:
            print(f"  NEU        {name}=={gewaehlt[name][0]}")
        else:
            gleich = alt[name] == gewaehlt[name][0]
            print(f"  {'ok      ' if gleich else 'FASSUNG '}   "
                  f"{name}=={gewaehlt[name][0]}  {gewaehlt[name][1]}")

    text = REQ.read_text(encoding="utf-8")
    erste = re.search(r"^[A-Za-z0-9][A-Za-z0-9._-]*==", text, re.M)
    if not erste:
        raise SystemExit("In der Datei steht keine Anforderung")
    kopf = text[:erste.start()]
    neu = kopf + neuer_block(gewaehlt)

    if not args.schreiben:
        if neu == text:
            print("\nDie Datei ist bereits auf diesem Stand.")
        else:
            print("\nEs wuerde sich etwas aendern. Mit --schreiben eintragen.")
        return

    REQ.write_text(neu, encoding="utf-8")
    print(f"\n{REQ} geschrieben.")
    print("Danach den MSI-Bau einmal wirklich laufen lassen - der Bericht "
          "von pip und der tatsaechliche Bau sind zwei verschiedene Dinge.")


if __name__ == "__main__":
    main()
