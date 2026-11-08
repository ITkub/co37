"""
CO-37 - keine internen Hostnamen im Quelltext.

Die Hostnamen aus dem Heimnetz des Entwicklers sind am 2026-09-09 vor dem
Oeffentlichmachen aus dem Quelltext entfernt worden. Sechs Wochen spaeter
standen sieben neue drin - in Kommentaren, Docstrings und einem
Prueflabel, alle aus derselben Fehlersuche vom 2026-10-21.

Das ist kein Nachlaessigkeitsproblem, sondern ein fehlender Waechter:
niemand haelt beim Schreiben eines Kommentars inne, um zu pruefen, ob der
Rechnername, an dem der Fehler auftrat, in ein oeffentliches Repository
gehoert. Dieselbe Antwort wie bei i18n-guard-test.js - die Regel wird
gemessen statt erinnert.

Gesucht wird ausschliesslich das Praefix der internen Maschinen. Private
IP-Adressen sind ABSICHTLICH nicht Teil der Suche: README, build_deb.py
und die Platzhalter der Oberflaeche nennen bewusst 192.168.1.10 und
192.168.2.20 als Beispiele. Ein Waechter mit Fehlalarmen wird
abgeschaltet, und dann schuetzt er gar nichts.

Das Suchmuster steht hier NICHT woertlich, sondern wird aus zwei Haelften
zusammengesetzt - sonst faende die Reihe sich selbst.

Laeuft ohne Backend-Prozess und ohne Netz.

    python3 tests/hostnamen-test.py
"""
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

# Zusammengesetzt, nicht woertlich. Siehe Kopf.
PRAEFIX = "KK" + "-"
MUSTER = re.compile(PRAEFIX + r"[A-Z0-9]{2,}")

# Verzeichnisse, die nicht zum Quellstand gehoeren: fremder Code,
# Gebautes, Zustand, und der Ablageordner fuer unfertige Arbeiten.
UEBERSPRINGEN = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "vendor",
    "dist", "build", "cache", "_msi_build", "data", "state",
    "db_backups", "update_backups", "Claude outputs",
}

ENDUNGEN = {".py", ".js", ".sh", ".md", ".html", ".txt", ".example", ".ps1"}

# Dateien, die GELESEN WERDEN MUESSEN - unabhaengig davon, ob die
# Endungsliste sie zufaellig erfasst.
#
# Die Checkmk-Pruefskripte tragen keine Endung (Shell) oder eine, an die
# bei einer Endungsliste niemand denkt (PowerShell). Sie sind zugleich
# die Dateien, in denen ein Hostname am ehesten landet - sie handeln von
# Hosts. Ein Waechter, der ausgerechnet dort nicht hinsieht, ist die
# Sorte Pruefung, die richtig aussieht und den Fall nicht deckt (F-45,
# F-46).
MUSS_GELESEN = [
    "packaging/checkmk/co37_monitoring",
    "packaging/checkmk/co37_agent",
    "packaging/checkmk/co37_agent.ps1",
]

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def ist_text(pfad):
    """Grobe Unterscheidung: ein Nullbyte am Anfang heisst binaer."""
    try:
        with pfad.open("rb") as fh:
            return b"\0" not in fh.read(4096)
    except OSError:
        return False


def dateien():
    for pfad in WURZEL.rglob("*"):
        if not pfad.is_file():
            continue
        if UEBERSPRINGEN & set(pfad.relative_to(WURZEL).parts):
            continue
        if pfad.suffix in ENDUNGEN:
            yield pfad
        elif pfad.suffix == "" and ist_text(pfad):
            # Skripte ohne Endung. Binaeres faellt ueber ist_text() raus.
            yield pfad


# ----------------------------------------------------------------------
# 1. Das Suchmuster taugt ueberhaupt etwas
# ----------------------------------------------------------------------
# Zuerst, und das ist kein Formalismus: ohne diese Pruefung waere ein
# kaputtes Muster nicht von einem sauberen Quelltext zu unterscheiden.
# Beides ergaebe null Treffer und damit gruen - dieselbe Falle, in die
# run-tests.sh bei einem vertippten Reihennamen laeuft.
check("das Muster findet einen erfundenen Hostnamen",
      bool(MUSTER.search(f"Lief am 2026-10-21 auf {PRAEFIX}XYZ99 durch")))

check("und schlaegt nicht bei harmlosem Text an",
      not MUSTER.search("Beispiel 192.168.1.10, Stichwort KKW, Kuerzel kk-ops"))

# ----------------------------------------------------------------------
# 2. Es wurde wirklich etwas durchsucht
# ----------------------------------------------------------------------
# Zweite Haelfte derselben Absicherung: ein Lauf, der an einem falschen
# Wurzelverzeichnis oder einer zu strengen Ausschlussliste scheitert,
# findet ebenfalls nichts.
gelesen = list(dateien())
check("der Quellbaum wurde durchsucht", len(gelesen) >= 40,
      f"{len(gelesen)} Dateien")

gelesen_rel = {p.relative_to(WURZEL).as_posix() for p in gelesen}
fehlend = [p for p in MUSS_GELESEN
           if (WURZEL / p).is_file() and p not in gelesen_rel]
check("die Checkmk-Pruefskripte werden mitgelesen", not fehlend,
      ", ".join(fehlend) if fehlend else "alle drei")

# ----------------------------------------------------------------------
# 3. Keine internen Hostnamen
# ----------------------------------------------------------------------
treffer = []
for pfad in gelesen:
    try:
        text = pfad.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for nr, zeile in enumerate(text.splitlines(), 1):
        for fund in MUSTER.findall(zeile):
            treffer.append(f"{pfad.relative_to(WURZEL).as_posix()}:{nr}  {fund}")

check("kein interner Hostname im Quelltext", not treffer,
      f"{len(treffer)} Treffer")
for zeile in treffer:
    print(f"       {zeile}")

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
