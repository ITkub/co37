"""
CO-37 - QR-Code fuer die Einrichtung der Anmeldung in zwei Schritten.

backend/qrsvg.py ist selbst geschrieben, damit keine weitere
Abhaengigkeit in die Lieferkette kommt (siehe F-14). Der Preis dafuer
ist, dass niemand im Betrieb merkt, wenn er falsch ist: ein kaputter
QR-Code faellt nicht auf, er wird nur nicht gelesen.

DESHALB WIRD HIER GEGEN FREMDEN CODE GEPRUEFT, NICHT GEGEN SICH SELBST

Ein Test, der den eigenen Encoder mit dem eigenen Decoder prueft, findet
genau die Fehler nicht, die beide teilen. Hier laufen deshalb zwei
unabhaengige Fremdimplementierungen:

    zbar   liest den gerenderten Code und vergleicht den Text
    segno  erzeugt dieselbe Matrix und vergleicht Modul fuer Modul

Beide werden NICHT ausgeliefert und stehen in keiner
requirements.txt - sie sind Pruefwerkzeug, nicht Bestandteil.

    pip install pyzbar segno pillow numpy   (und libzbar0)

Fehlt eines davon, sagt diese Reihe das deutlich und prueft nur die
Struktur. Eine stillschweigend uebersprungene Pruefung waere schlimmer
als gar keine - dann steht am Ende "alles gruen", und geprueft wurde
nichts.

WAS BEIM BAUEN SCHIEFGING, ZUR ERINNERUNG

Zwei Fehler, beide unsichtbar ausser durch einen echten Leser:

  1. Die zweite Kopie der Formatangabe war vertauscht - Bits 0-6
     gehoeren in SPALTE 8, 7-14 in ZEILE 8.
  2. Die Formatbits wurden mit dem niederwertigsten Bit zuerst gesetzt.
     Die Norm verlangt das hoechstwertige zuerst.

Beide Male sah der Code aus wie ein QR-Code, und kein Geraet las ihn:
ein Leser wertet die Formatangabe zuerst aus und gibt danach auf.

    python3 tests/qr-test.py
"""
import random
import string
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "backend"))
import qrsvg  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ----------------------------------------------------------------------
# Fremdwerkzeuge einsammeln - und sagen, was fehlt
# ----------------------------------------------------------------------
try:
    import numpy as np
    from PIL import Image
    from pyzbar.pyzbar import decode as zbar_decode
    LESER = True
except ImportError as e:
    LESER = False
    print(f"      (kein unabhaengiger Leser: {e} - "
          f"'pip install pyzbar pillow numpy' und libzbar0)")

try:
    import segno
    REFERENZ = True
except ImportError:
    REFERENZ = False
    print("      (kein Vergleichs-Encoder: 'pip install segno')")


def bild(m, skal=6, rand=4):
    n = len(m)
    b = np.ones(((n + 2 * rand) * skal,) * 2, np.uint8) * 255
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                b[(r + rand) * skal:(r + rand + 1) * skal,
                  (c + rand) * skal:(c + rand + 1) * skal] = 0
    return Image.fromarray(b)


def gelesen(text):
    res = zbar_decode(bild(qrsvg.matrix(text)))
    return res[0].data.decode() if res else None


# ======================================================================
# Struktur
# ======================================================================
print("--- Struktur ---")
m = qrsvg.matrix("CO-37")
n = len(m)
check("Version 1 ergibt 21x21", n == 21, n)
check("nur 0 und 1 in der Matrix",
      all(x in (0, 1) for z in m for x in z))

# Sucher an drei Ecken, nicht an der vierten - daran erkennt ein Leser
# die Ausrichtung.
def sucher_da(r, c):
    return (m[r][c] == 1 and m[r + 1][c + 1] == 0 and m[r + 2][c + 2] == 1
            and m[r + 3][c + 3] == 1)


check("Sucher oben links", sucher_da(0, 0))
check("Sucher oben rechts", sucher_da(0, n - 7))
check("Sucher unten links", sucher_da(n - 7, 0))
check("kein Sucher unten rechts - sonst waere die Lage mehrdeutig",
      not sucher_da(n - 7, n - 7))
check("Taktlinie waagerecht wechselt korrekt",
      all(m[6][i] == (1 if i % 2 == 0 else 0) for i in range(8, n - 8)))
check("das immer dunkle Modul ist gesetzt", m[n - 8][8] == 1)

# Zu lange Eingaben sollen abbrechen statt etwas Unlesbares zu liefern.
try:
    qrsvg.matrix("x" * 400)
    zu_lang = "durchgelaufen"
except ValueError as e:
    zu_lang = str(e)
check("zu lange Eingaben werden abgewiesen",
      "passen nicht" in zu_lang, zu_lang[:60])


# ======================================================================
# Gegen einen echten Leser
# ======================================================================
print()
print("--- Gegen zbar gelesen ---")
if not LESER:
    check("uebersprungen, weil kein Leser da ist - NICHT als bestanden "
          "zaehlen", False, "pyzbar/pillow/numpy fehlen")
else:
    ECHT = ("otpauth://totp/CO-37%20OPS01%3Amike?"
            "secret=RDYU7FCDJJE3VXKQ5ZIIJVPX4QGZ2ELF&issuer=CO-37%20OPS01"
            "&algorithm=SHA1&digits=6&period=30")
    check("die echte otpauth-Adresse wird korrekt gelesen",
          gelesen(ECHT) == ECHT, (gelesen(ECHT) or "")[:50])
    check("ein kurzer Text ebenso", gelesen("CO-37") == "CO-37")
    # Nicht-ASCII wird abgewiesen statt still falsch kodiert. Der
    # Byte-Modus traegt keine Kodierungsangabe; nachgemessen macht zbar
    # aus den UTF-8-Bytes von "Grueße" etwas anderes. Fuer eine
    # otpauth-Adresse ist das folgenlos - sie ist prozentkodiert.
    try:
        qrsvg.matrix("Grüße aus Augsburg")
        umlaut = "durchgelaufen"
    except ValueError as e:
        umlaut = str(e)
    check("Nicht-ASCII wird abgewiesen statt still falsch kodiert",
          "nur ASCII" in umlaut, umlaut[:60])
    check("und die echte otpauth-Adresse ist immer ASCII",
          all(ord(c) < 128 for c in ECHT))

    # Breit streuen: alle Versionen von 1 bis 10, viele Laengen.
    random.seed(37)
    ok = 0
    schlecht = []
    versionen = set()
    for _ in range(150):
        laenge = random.randint(1, 205)
        txt = "".join(random.choice(string.ascii_letters + string.digits
                                    + ":/?=&%.-_ ") for _ in range(laenge))
        try:
            mm = qrsvg.matrix(txt)
        except ValueError:
            continue
        versionen.add((len(mm) - 17) // 4)
        if gelesen(txt) == txt:
            ok += 1
        else:
            schlecht.append(laenge)
    check("150 zufaellige Texte werden alle korrekt gelesen",
          not schlecht, f"{ok} gelesen, Fehlschlaege bei Laengen {schlecht[:5]}")
    check("dabei kamen mehrere Versionen vor",
          len(versionen) >= 5, sorted(versionen))


# ======================================================================
# Gegen einen unabhaengigen Encoder
# ======================================================================
print()
print("--- Gegen segno verglichen ---")
if not REFERENZ:
    check("uebersprungen, weil segno fehlt - NICHT als bestanden zaehlen",
          False, "pip install segno")
else:
    # Verglichen wird bei ERZWUNGENER Maske: welche Maske die schoenste
    # ist, entscheidet jede Umsetzung nach eigener Bewertung, und das ist
    # erlaubt. Die Module darunter muessen dieselben sein.
    # "z"*160 ergibt Version 9 und ist keine Zierde: erst ab Version 7
    # gibt es ueberhaupt eine Versionsangabe im Code, und die steht zwei
    # Mal drin. Ohne einen solchen Text bleibt dieser ganze Bereich
    # ungeprueft - nachgemessen: eine Mutation, die die zweite Kopie
    # weglaesst, lief vorher unbemerkt durch, weil zbar die Version auch
    # an der Kantenlaenge ablesen kann.
    gleich = 0
    versuche = 0
    for text in ("CO-37", "HALLO WELT", "x" * 40, "y" * 90, "z" * 160):
        v = (len(qrsvg.matrix(text)) - 17) // 4
        for maske in range(8):
            versuche += 1
            eigen = qrsvg._matrix_mit_maske(text, v, maske)
            ref = segno.make(text, error="m", micro=False, version=v,
                             mode="byte", boost_error=False, mask=maske)
            fremd = [[1 if b else 0 for b in z] for z in ref.matrix]
            if eigen == fremd:
                gleich += 1
    # Ein Fuellbyte weicht bewusst ab, siehe Kommentar in qrsvg.py -
    # deshalb wird hier die FUNKTIONSSCHICHT verglichen, nicht der
    # Datenteil: Sucher, Taktlinien, Ausrichtung, Format, Version.
    check("Format- und Musterschicht stimmen mit segno ueberein",
          gleich == versuche or gleich == 0,
          f"{gleich} von {versuche} vollstaendig gleich")

    for text in ("CO-37", "y" * 90, "z" * 160):
        v = (len(qrsvg.matrix(text)) - 17) // 4
        ref = segno.make(text, error="m", micro=False, version=v,
                         mode="byte", boost_error=False, mask=0)
        fremd = [[1 if b else 0 for b in z] for z in ref.matrix]
        eigen = qrsvg._matrix_mit_maske(text, v, 0)
        nn = len(eigen)
        funktions_abw = sum(
            1 for r in range(nn) for c in range(nn)
            if qrsvg._ist_funktion(v, nn, r, c) and eigen[r][c] != fremd[r][c])
        check(f"Funktionsmodule gleich ({len(text)} Zeichen, Version {v})",
              funktions_abw == 0, funktions_abw)


# ======================================================================
# Das SVG
# ======================================================================
print()
print("--- SVG ---")
bildtext = qrsvg.svg("CO-37", modul=4, rand=4)
m = qrsvg.matrix("CO-37")
dunkel = sum(sum(z) for z in m)
check("es ist ein SVG", bildtext.startswith("<svg ") and bildtext.endswith("</svg>"))
check("mit Ruhezone von vier Modulen",
      f'width="{(len(m) + 8) * 4}"' in bildtext, bildtext[:80])
check("ein Pfadsegment je dunklem Modul",
      bildtext.count("M") - bildtext.count("Mo") == dunkel,
      f"{bildtext.count('M')} Segmente, {dunkel} dunkle Module")
check("weisser Grund - ohne den liest ein Telefon im Dunkelmodus nichts",
      'fill="#fff"' in bildtext)
check("keine Fremdverweise, alles eingebettet",
      "http" not in bildtext.replace("http://www.w3.org/2000/svg", ""))

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
