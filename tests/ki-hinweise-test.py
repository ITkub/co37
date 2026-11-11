"""
CO-37 - keine Werkzeughinweise in dem, was Git kennt.

Der Quelltext soll nicht ausweisen, mit welchen Werkzeugen er entstanden
ist. Am 2026-11-11 standen drei solche Hinweise im Repository: eine
Ausschlussregel in .gitignore, derselbe Ordnername in der
Ueberspringen-Liste von hostnamen-test.py, und ein Kommentarverweis auf
einen Ablagepfad in .github/workflows/tests.yml.

Alle drei waren in derselben Woche entstanden, keiner davon absichtlich,
und zwei von ihnen erst durch die Gegenmassnahme zum Vorfall vom
2026-09-23. Das ist dasselbe Muster wie bei den internen Hostnamen
(hostnamen-test.py): niemand haelt beim Schreiben einer Ausschlussregel
inne, um zu pruefen, ob der Ordnername in ein oeffentliches Repository
gehoert. Deshalb hier derselbe Ansatz - die Regel wird gemessen statt
erinnert.

WAS GEPRUEFT WIRD

Der Stand von HEAD, und zwar in drei Richtungen: Dateiinhalte, Pfad- und
Ordnernamen, und die Kopfdaten des Commits (Autor und Nachricht).

Die Pfadnamen sind kein Beiwerk. Der Fund vom 2026-11-11 war ein
ORDNERNAME und fiel nur auf, weil er zufaellig als Text in der
.gitignore stand. Kaeme der Ordner selbst wieder herein, waere eine
reine Inhaltssuche blind dafuer. Und 'co-auth' + 'ored' kommt in
Dateien praktisch nicht vor - dieser Begriff lohnt sich erst mit der
Commit-Nachricht.

WAS NICHT GEPRUEFT WIRD, und das ist ausdruecklich so entschieden:

- Die HISTORIE. Die drei Funde von 2026-11-11 stehen weiter in den
  Commits de17c30, ff3e4da und 5004f24. Das bleibt bewusst so: beim
  Vorfall vom 2026-09-23 ging es um 18 MB unveroeffentlichte Medien,
  hier um einen Ordnernamen in drei Kommentarzeilen, und ein zweiter
  Force-Push auf ein oeffentliches Repository mit fremden Klonen waere
  dafuer unverhaeltnismaessig. Eine gruene Reihe heisst also "der
  aktuelle Stand ist frei davon", nicht "das Repository ist es".
- In einem flachen Klon (actions/checkout holt standardmaessig einen
  Commit) ist ohnehin nur die Nachricht von HEAD sichtbar. Das genuegt
  fuer den Zweck: geprueft wird der Stand, der gerade gepusht wurde.
- Binaerdateien. git grep -I laesst sie aus. Deshalb wird unten
  gemessen, wie viele der verfolgten Dateien wirklich durchsucht
  wurden - eine stillschweigend auf null geschrumpfte Teilmenge ist
  dieselbe Falle, gegen die die Vorabpruefungen hier stehen.

ZWEI ABWAEGUNGEN, die beim Lesen auffallen sollten:

- Die Begriffe stehen hier NICHT woertlich, sondern werden aus zwei
  Haelften zusammengesetzt - sonst faende die Reihe sich selbst. Einen
  Ausschluss fuer diese Datei gibt es bewusst NICHT: er waere genau der
  blinde Fleck, den er zu verhindern vorgibt. Wer den Kopf umschreibt
  und dabei einen Begriff woertlich hinschreibt, soll Rot sehen.
  Pruefung 2 unten misst das zusaetzlich.
- Wird die Reihe rot, druckt sie Fundstelle UND Begriff. In der CI eines
  oeffentlichen Repositorys landet das im Protokoll, das jeder lesen
  kann. Ohne Fundstelle waere der Befund unbrauchbar, also ist das der
  Preis: ein roter Lauf ist selbst eine kleine Veroeffentlichung. Umso
  mehr Grund, ihn nicht entstehen zu lassen.

Laeuft ohne Backend-Prozess und ohne Netz, braucht aber git und das
.git des Arbeitsverzeichnisses. Im entpackten Release-ZIP ist beides
nicht gegeben; build_release.py laesst diese Datei deshalb aus dem
Paket.

    python3 tests/ki-hinweise-test.py
"""
import re
import subprocess
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

# Zusammengesetzt, nicht woertlich. Siehe Kopf.
#
# NICHT in der Liste, und das ist gemessen statt vermutet: 'cursor'
# kommt im eigenen Quelltext in fuenf Dateien legitim vor (DBAPI-Cursor
# in backend/, 'cursor:pointer' im Frontend), 'llm' in elf. Beide waeren
# reine Fehlalarmquellen, und ein Waechter mit Fehlalarmen wird
# abgeschaltet - dann schuetzt er gar nichts.
BEGRIFFE = [
    "cla" + "ude",
    "anthro" + "pic",
    "co-auth" + "ored",
    "chat" + "gpt",
    "co" + "pilot",
    "gem" + "ini",
    "open" + "ai",
    "ai-gen" + "erated",
    "gpt" + "-",
]
MUSTER = re.compile("|".join(BEGRIFFE), re.IGNORECASE)

# Dateien, die DURCHSUCHT WERDEN MUESSEN - unabhaengig davon, was die
# Dateiliste sonst hergibt. Genau diese zwei sind am 2026-11-11 die
# Fundstellen gewesen. Eine Liste, die ausgerechnet sie nicht erfasst,
# ist die Sorte Pruefung, die richtig aussieht und den Fall nicht deckt.
MUSS_DURCHSUCHT = [
    ".gitignore",
    ".github/workflows/tests.yml",
]

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def git(*args, erlaubte=(0,)):
    """
    git aufrufen. Fehlt git selbst, ist das eine FEHLER-Zeile und kein
    Rueckverfolgungsprotokoll quer durch die Ausgabe - run-tests.sh
    zeigt im Fehlerfall nur die letzten Zeilen, und dort soll der Grund
    stehen.
    """
    try:
        r = subprocess.run(["git", "-C", str(WURZEL), *args],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return r.stdout if r.returncode in erlaubte else None


# ----------------------------------------------------------------------
# 1. Das Suchmuster taugt ueberhaupt etwas
# ----------------------------------------------------------------------
# Zuerst, und das ist kein Formalismus: ohne diese Pruefung waere ein
# kaputtes Muster nicht von einem sauberen Repository zu unterscheiden.
# Beides ergaebe null Treffer und damit gruen.
nicht_gefunden = [b for b in BEGRIFFE if not MUSTER.search(f"Zeile mit {b} darin")]
check("das Muster findet jeden der Begriffe", not nicht_gefunden,
      f"{len(BEGRIFFE)} Begriffe" if not nicht_gefunden else len(nicht_gefunden))

# Die Gegenprobe mit genau den Woertern, die im eigenen Quelltext
# legitim vorkommen. Schlaegt eines davon an, ist die Begriffsliste zu
# weit gefasst und die Reihe waere von morgen an nutzlos.
HARMLOS = ("zeiger = verbindung.cursor()",
           "Zeilen zaehlen, auf einem DBAPI-Cursor.",
           "button:disabled{opacity:.4;cursor:not-allowed}",
           "authored by hand, Stichwort Clausthal",
           "Fachgebiet anthropologisch, Kuerzel ca")
anschlaege = [t for t in HARMLOS if MUSTER.search(t)]
check("und schlaegt bei keinem harmlosen Wort an", not anschlaege, anschlaege)

# ----------------------------------------------------------------------
# 2. Die eigene Datei nennt die Begriffe nicht woertlich
# ----------------------------------------------------------------------
# Es gibt keinen Selbstausschluss (siehe Kopf). Diese Pruefung sagt es
# ausdruecklich, damit der Fall nicht erst ueber die Suche unten
# auffaellt, wo er nach einem Fund im Projekt aussaehe.
eigen = Path(__file__).read_text(encoding="utf-8")
check("die Reihe selbst nennt die Begriffe nur zusammengesetzt",
      not MUSTER.search(eigen))

# ----------------------------------------------------------------------
# 3. Es wurde wirklich etwas durchsucht
# ----------------------------------------------------------------------
# Zweite Haelfte derselben Absicherung: ein Lauf ohne git, am falschen
# Verzeichnis oder in einem Auspack ohne .git findet ebenfalls nichts.
verfolgt = git("ls-tree", "-r", "--name-only", "HEAD")
check("git und das Repository sind erreichbar", verfolgt is not None,
      "kein git oder kein .git" if verfolgt is None else "")
verfolgt = [n for n in (verfolgt or "").splitlines() if n]

# Was git grep tatsaechlich ansieht. [[:print:]] trifft jede Textzeile,
# -I laesst Binaeres aus - die Differenz ist genau die Menge, die der
# eigentlichen Suche unten entgeht.
roh = git("grep", "-I", "-l", "-E", "[[:print:]]", "HEAD", "--", erlaubte=(0, 1))
durchsucht = [z.split(":", 1)[1] for z in (roh or "").splitlines() if ":" in z]

check("die Dateiliste kam zustande", len(durchsucht) >= 50,
      f"{len(durchsucht)} von {len(verfolgt)} verfolgten Dateien")

# Faellt der Anteil ein, ist etwas mit der Textkennung passiert (eine
# .gitattributes-Regel etwa) - und die Suche misst dann fast nichts.
anteil = len(durchsucht) / len(verfolgt) if verfolgt else 0
check("und deckt den Grossteil der verfolgten Dateien ab", anteil >= 0.9,
      f"{anteil:.0%}, binaer uebersprungen: {len(verfolgt) - len(durchsucht)}")

# Der Umweg ueber 'p in verfolgt' ist Absicht: wird eine der beiden
# Dateien einmal legitim umbenannt, soll das kein Fehlalarm sein. Dass
# ueberhaupt eine Liste vorliegt, prueft die Zeile darueber.
fehlend = [p for p in MUSS_DURCHSUCHT if p in verfolgt and p not in durchsucht]
check("die beiden Fundstellen von 2026-11-11 sind dabei",
      bool(verfolgt) and not fehlend,
      ", ".join(fehlend) if fehlend else ("beide" if verfolgt else "keine Liste"))

# ----------------------------------------------------------------------
# 4. Keine Werkzeughinweise in dem, was Git kennt
# ----------------------------------------------------------------------
treffer = []

# Inhalte. Gegen HEAD, nicht gegen den Arbeitsbaum: oeffentlich ist, was
# committet ist. Eine Datei, die im Arbeitsbaum bereinigt und im Commit
# noch belastet ist, waere beim Lesen aus dem Arbeitsbaum unsichtbar.
inhalt = git("grep", "-I", "-n", "-i", "-E", MUSTER.pattern, "HEAD", "--",
             erlaubte=(0, 1))
check("die Inhaltssuche lief", inhalt is not None)
for zeile in (inhalt or "").splitlines():
    if zeile.startswith("HEAD:"):
        treffer.append("Inhalt   " + zeile[len("HEAD:"):])

# Pfad- und Ordnernamen.
for name in verfolgt:
    for fund in MUSTER.findall(name):
        treffer.append(f"Pfad     {name}  {fund}")

# Kopfdaten von HEAD: Autor, Adresse, Nachricht.
kopf = git("log", "-1", "--format=%an%n%ae%n%B")
check("die Kopfdaten von HEAD sind lesbar", kopf is not None)
for nr, zeile in enumerate((kopf or "").splitlines(), 1):
    for fund in MUSTER.findall(zeile):
        treffer.append(f"Commit   HEAD:{nr}  {fund}")

# Diese Zeile ist die Aussage der ganzen Reihe, also darf sie nicht
# gruen werden, wenn eine der drei Quellen ausgefallen ist. Null Treffer
# aus einer Suche, die nie lief, sahen sonst genauso aus wie null
# Treffer aus einem sauberen Repository - dieselbe Falle, gegen die die
# Vorabpruefungen oben stehen.
gesucht = inhalt is not None and kopf is not None and bool(verfolgt)
check("kein Werkzeughinweis in Inhalt, Pfaden oder Kopfdaten",
      gesucht and not treffer,
      f"{len(treffer)} Treffer" if gesucht else "nicht durchsucht")
for zeile in treffer:
    print(f"       {zeile}")

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
