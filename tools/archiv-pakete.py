#!/usr/bin/env python3
"""
CO-37 - alte Pakete aus dem Projektordner ins Archiv schieben.

Im Projektordner sammeln sich mit der Zeit alle je gebauten Pakete samt
Signatur und Pruefsumme an. Loeschen waere die falsche Antwort: ein Neubau
aus demselben Quellstand ergibt keine byteidentische ZIP - im Archiv
stecken Zeitstempel - und damit passt die alte Signatur nicht mehr. Ein
geloeschtes Paket ist als Erzeugnis endgueltig weg. Sobald Schluessel
verkauft werden, will man zu einer Fehlermeldung genau die Fassung
hervorholen koennen, die ausgeliefert wurde.

Darum verschieben statt loeschen. Standardziel ist ein Ordner neben dem
Projekt, damit der Arbeitsordner uebersichtlich bleibt.

Aufruf:

    python tools/archiv-pakete.py                  zwei neueste behalten
    python tools/archiv-pakete.py --behalten 5     mehr behalten
    python tools/archiv-pakete.py --probe          nur zeigen, nichts tun
    python tools/archiv-pakete.py --ziel D:/Archiv anderes Ziel

Wird am Ende von build_release.py selbst aufgerufen.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent.parent

# Die Begleitdateien wandern mit. Ohne die .sig laesst sich ein Paket
# nicht mehr einspielen - sie im Projektordner zurueckzulassen, waehrend
# die ZIP ins Archiv geht, ergaebe zwei halbe Pakete.
BEGLEITER = (".sig", ".sha256")

# Genauer Anker statt "faengt mit co37_v an": eine Teilzeichenkette hat in
# diesem Projekt schon mehrfach das Falsche getroffen.
MUSTER = re.compile(r"^co37_v(\d+)_(\d+)_(\d+)\.zip$")


def version_von(pfad: Path):
    """
    Gibt die Version als Zahlentripel zurueck, oder None.

    Als Zahlen, nicht als Text: nach Namen sortiert stuende 0_34_10 vor
    0_34_9, und dann behielte das Werkzeug die falschen Pakete.
    """
    m = MUSTER.match(pfad.name)
    return tuple(int(g) for g in m.groups()) if m else None


def finde_pakete(ordner: Path) -> list[tuple[tuple, Path]]:
    """Alle Pakete im Ordner, aeltestes zuerst."""
    gefunden = []
    for pfad in ordner.iterdir():
        if not pfad.is_file():
            continue
        v = version_von(pfad)
        if v is not None:
            gefunden.append((v, pfad))
    gefunden.sort(key=lambda e: e[0])
    return gefunden


def archiviere(ordner: Path = HIER, ziel: Path = None, behalten: int = 2,
               probe: bool = False, ausgabe=print) -> dict:
    """
    Verschiebt alle bis auf die 'behalten' neuesten Pakete nach 'ziel'.

    Gibt zurueck, was verschoben und was uebersprungen wurde - damit der
    Aufrufer und die Pruefreihe es auswerten koennen, statt die Ausgabe
    lesen zu muessen.
    """
    if behalten < 1:
        raise ValueError("Es muss mindestens ein Paket im Ordner bleiben.")
    ziel = Path(ziel) if ziel else ordner.parent / "co37-releases"

    pakete = finde_pakete(ordner)
    zu_verschieben = pakete[:-behalten] if len(pakete) > behalten else []

    ergebnis = {"verschoben": [], "uebersprungen": [], "ziel": str(ziel),
                "geblieben": [p.name for _, p in pakete[len(zu_verschieben):]]}

    if not zu_verschieben:
        ausgabe(f"Archiv: nichts zu tun, {len(pakete)} Paket(e) im Ordner.")
        return ergebnis

    if not probe:
        ziel.mkdir(parents=True, exist_ok=True)

    for _, zip_pfad in zu_verschieben:
        # Die ZIP zuerst, die Begleiter danach: bricht es dazwischen ab,
        # liegt die ZIP schon im Archiv und der Rest folgt beim naechsten
        # Lauf. Umgekehrt haette man eine .sig ohne Paket.
        for pfad in [zip_pfad] + [zip_pfad.with_name(zip_pfad.name + e)
                                  for e in BEGLEITER]:
            if not pfad.exists():
                continue
            neu = ziel / pfad.name
            if neu.exists():
                # Nicht stillschweigend ueberschreiben. Wer zweimal
                # dieselbe Version gebaut hat, soll selbst entscheiden,
                # welche gilt.
                ergebnis["uebersprungen"].append(pfad.name)
                ausgabe(f"Archiv: {pfad.name} liegt dort schon, uebersprungen.")
                continue
            if probe:
                ergebnis["verschoben"].append(pfad.name)
                continue
            shutil.move(str(pfad), str(neu))
            ergebnis["verschoben"].append(pfad.name)

    wort = "waeren" if probe else "sind"
    ausgabe(f"Archiv: {len(ergebnis['verschoben'])} Datei(en) {wort} nach "
            f"{ziel} verschoben, {behalten} Paket(e) bleiben liegen.")
    return ergebnis


def main():
    p = argparse.ArgumentParser(
        description="Alte CO-37-Pakete ins Archiv verschieben.")
    p.add_argument("--behalten", type=int, default=2,
                   help="wie viele der neuesten Pakete liegen bleiben (Vorgabe 2)")
    p.add_argument("--ziel", default=None,
                   help="Archivordner (Vorgabe: co37-releases neben dem Projekt)")
    p.add_argument("--probe", action="store_true",
                   help="nur zeigen, was geschehen wuerde")
    a = p.parse_args()
    try:
        archiviere(behalten=a.behalten, ziel=a.ziel, probe=a.probe)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
