"""
CO-37 - QR-Code als SVG, ohne zusaetzliche Abhaengigkeit.

Gebraucht fuer genau eine Sache: die otpauth-Adresse bei der Einrichtung
der Anmeldung in zwei Schritten. Ohne QR-Code muesste man ein
32-stelliges Geheimnis am Telefon abtippen.

----------------------------------------------------------------------
WARUM SELBST GESCHRIEBEN
----------------------------------------------------------------------
Eine Bibliothek waere zwanzig Zeilen statt zweihundert. Sie waere aber
auch eine weitere Lieferkette: Hash-Pin (F-14), CVE-Pruefung bei jeder
Runde, Pflege ueber Jahre - fuer eine Funktion, die an genau einer
Stelle gebraucht wird und sich seit 2006 nicht geaendert hat.

Der Preis dafuer ist, dass dieser Code stimmen muss, ohne dass jemand
ihn im Betrieb pruefen kann: ein falscher QR-Code faellt nicht auf, er
wird nur nicht gelesen - oder schlimmer, er wird gelesen und enthaelt
Unsinn.

Deshalb prueft die Reihe 'qr' ihn auf ZWEI unabhaengige Arten gegen
fremden Code:

  - Die Matrix wird Modul fuer Modul mit 'segno' verglichen.
  - Das gerenderte Bild wird mit OpenCV wieder DEKODIERT und der Text
    verglichen.

Beide Bibliotheken laufen nur im Test und werden nicht ausgeliefert.
Fehlt eine, sagt die Reihe das und prueft nur die Struktur - eine
stillschweigend uebersprungene Pruefung waere schlimmer als gar keine.

----------------------------------------------------------------------
UMFANG
----------------------------------------------------------------------
Byte-Modus, Fehlerkorrektur **M**, Versionen 1 bis 10. Das deckt bis zu
213 Byte ab; eine otpauth-Adresse liegt bei rund 130. Alles darueber
wirft - lieber ein klarer Fehler als ein Bild, das niemand lesen kann.

Absichtlich NICHT umgesetzt: Ziffern- und alphanumerischer Modus (die
otpauth-Adresse enthaelt Kleinbuchstaben und Doppelpunkte, also greift
ohnehin nur Byte), Kanji, ECI, strukturiertes Anhaengen.
"""
from typing import List

# ----------------------------------------------------------------------
# Tabellen aus ISO/IEC 18004, Fehlerkorrekturstufe M
# ----------------------------------------------------------------------
# Je Version: (Fehlerkorrektur-Codewoerter je Block,
#              [(Anzahl Bloecke, Daten-Codewoerter je Block), ...])
BLOECKE = {
    1:  (10, [(1, 16)]),
    2:  (16, [(1, 28)]),
    3:  (26, [(1, 44)]),
    4:  (18, [(2, 32)]),
    5:  (24, [(2, 43)]),
    6:  (16, [(4, 27)]),
    7:  (18, [(4, 31)]),
    8:  (22, [(2, 38), (2, 39)]),
    9:  (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

# Mittelpunkte der Ausrichtungsmuster je Version.
AUSRICHTUNG = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}

MAX_VERSION = 10


# ----------------------------------------------------------------------
# Galois-Feld GF(256) fuer Reed-Solomon
# ----------------------------------------------------------------------
# Erzeugerpolynom 0x11D, wie in ISO/IEC 18004 festgelegt.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(grad: int) -> List[int]:
    """Generatorpolynom fuer 'grad' Fehlerkorrektur-Codewoerter."""
    poly = [1]
    for i in range(grad):
        neu = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            neu[j] ^= c
            neu[j + 1] ^= _mul(c, _EXP[i])
        poly = neu
    return poly


def _ecc(daten: List[int], anzahl: int) -> List[int]:
    """Reed-Solomon-Codewoerter zu einem Datenblock."""
    gen = _generator(anzahl)
    rest = list(daten) + [0] * anzahl
    for i in range(len(daten)):
        f = rest[i]
        if f:
            for j, g in enumerate(gen):
                rest[i + j] ^= _mul(g, f)
    return rest[len(daten):]


# ----------------------------------------------------------------------
# Bitstrom
# ----------------------------------------------------------------------
def _codewoerter(text: str, version: int) -> List[int]:
    roh = text.encode("ascii")
    ecc_je_block, gruppen = BLOECKE[version]
    daten_gesamt = sum(n * k for n, k in gruppen)

    bits = []

    def schieben(wert, laenge):
        for i in range(laenge - 1, -1, -1):
            bits.append((wert >> i) & 1)

    schieben(0b0100, 4)                      # Byte-Modus
    # Laengenfeld: 8 Bit bis Version 9, danach 16.
    schieben(len(roh), 8 if version < 10 else 16)
    for b in roh:
        schieben(b, 8)

    # Abschluss, hoechstens vier Nullen, und nur so viele wie Platz ist.
    frei = daten_gesamt * 8 - len(bits)
    schieben(0, min(4, frei))
    while len(bits) % 8:
        bits.append(0)

    cw = [int("".join(str(b) for b in bits[i:i + 8]), 2)
          for i in range(0, len(bits), 8)]
    # Fuellbytes, abwechselnd - so steht es in der Norm: nach dem
    # Abschluss auf die Bytegrenze auffuellen, dann 0xEC und 0x11 im
    # Wechsel.
    #
    # segno haengt an dieser Stelle ein zusaetzliches 0x00 an, bevor es
    # mit 0xEC beginnt. Beide Fassungen werden von Lesegeraeten
    # anstandslos gelesen - was hinter der Nutzlast steht, wertet ein
    # Leser nicht mehr aus. Der Unterschied ist hier vermerkt, damit ihn
    # nicht beim naechsten Vergleich jemand fuer einen Fehler haelt.
    fuell = [0xEC, 0x11]
    while len(cw) < daten_gesamt:
        cw.append(fuell[(len(cw) - len(bits) // 8) % 2])

    # In Bloecke teilen, Fehlerkorrektur je Block, dann verschraenken.
    bloecke, ecc_bloecke, pos = [], [], 0
    for anzahl, groesse in gruppen:
        for _ in range(anzahl):
            block = cw[pos:pos + groesse]
            pos += groesse
            bloecke.append(block)
            ecc_bloecke.append(_ecc(block, ecc_je_block))

    ergebnis = []
    for i in range(max(len(b) for b in bloecke)):
        for b in bloecke:
            if i < len(b):
                ergebnis.append(b[i])
    for i in range(ecc_je_block):
        for b in ecc_bloecke:
            ergebnis.append(b[i])
    return ergebnis


# ----------------------------------------------------------------------
# Matrix
# ----------------------------------------------------------------------
def _pruefe_ascii(text: str):
    """
    Nur ASCII hinein.

    Der Byte-Modus traegt KEINE Angabe zur Zeichenkodierung. Die Norm
    schreibt fuer ihn ISO-8859-1 vor; welche Kodierung ein Leser
    tatsaechlich annimmt, ist in der Praxis Glueckssache. Nachgemessen
    mit zbar: aus "Grueße" wird beim Lesen etwas anderes, weil der Leser
    die UTF-8-Bytes als Shift-JIS deutet.

    Sauber loesen liesse sich das mit einem ECI-Kopf (Kennung 26 fuer
    UTF-8). Das ist hier aber ungebraucht: der einzige Inhalt ist eine
    otpauth-Adresse, und die ist durch quote() und urlencode()
    prozentkodiert, also immer ASCII.

    Deshalb ein klarer Abbruch statt einer stillen Falschkodierung. Wer
    das eines Tages fuer anderes braucht, baut ECI ein - und sieht an
    dieser Stelle, warum.
    """
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "Dieser QR-Code kann nur ASCII. Der Byte-Modus traegt keine "
            "Kodierungsangabe, und Lesegeraete deuten alles darueber "
            "unterschiedlich. Fuer die otpauth-Adresse reicht ASCII, weil "
            "sie prozentkodiert ist."
        ) from exc


def _leer(groesse):
    return [[None] * groesse for _ in range(groesse)]


def _muster(m, version):
    """Sucher, Trenner, Taktlinien, Ausrichtung, dunkles Modul."""
    n = len(m)

    def sucher(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < n and 0 <= cc < n:
                    rand = dr in (-1, 7) or dc in (-1, 7)
                    innen = 1 <= dr <= 5 and 1 <= dc <= 5
                    kern = 2 <= dr <= 4 and 2 <= dc <= 4
                    m[rr][cc] = 0 if (rand or (innen and not kern)) else 1

    sucher(0, 0)
    sucher(0, n - 7)
    sucher(n - 7, 0)

    for i in range(8, n - 8):
        wert = 1 if i % 2 == 0 else 0
        m[6][i] = wert
        m[i][6] = wert

    for r in AUSRICHTUNG[version]:
        for c in AUSRICHTUNG[version]:
            # Nicht ueber die Sucher legen.
            if (r < 8 and c < 8) or (r < 8 and c > n - 9) or (r > n - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = 0 if max(abs(dr), abs(dc)) == 1 else 1

    m[n - 8][8] = 1                      # das immer dunkle Modul


def _reserviert(m, version):
    """Felder fuer Format- und Versionsangaben freihalten."""
    n = len(m)
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][n - 1 - i] is None:
            m[8][n - 1 - i] = 0
        if m[n - 1 - i][8] is None:
            m[n - 1 - i][8] = 0
    if version >= 7:
        for i in range(6):
            for j in range(3):
                m[n - 11 + j][i] = 0
                m[i][n - 11 + j] = 0


def _ist_funktion(version, n, r, c):
    """Gehoert die Stelle zu einem Muster und darf keine Daten tragen?"""
    if r == 6 or c == 6:
        return True
    if r < 9 and c < 9:
        return True
    if r < 9 and c >= n - 8:
        return True
    if r >= n - 8 and c < 9:
        return True
    if version >= 7 and ((r >= n - 11 and c < 6) or (c >= n - 11 and r < 6)):
        return True
    for ar in AUSRICHTUNG[version]:
        for ac in AUSRICHTUNG[version]:
            if (ar < 8 and ac < 8) or (ar < 8 and ac > n - 9) or (ar > n - 9 and ac < 8):
                continue
            if abs(r - ar) <= 2 and abs(c - ac) <= 2:
                return True
    return False


def _daten_setzen(m, version, cw):
    n = len(m)
    bits = [(b >> i) & 1 for b in cw for i in range(7, -1, -1)]
    idx = 0
    spalte = n - 1
    aufwaerts = True
    while spalte > 0:
        if spalte == 6:              # die senkrechte Taktlinie ueberspringen
            spalte -= 1
        zeilen = range(n - 1, -1, -1) if aufwaerts else range(n)
        for r in zeilen:
            for c in (spalte, spalte - 1):
                if _ist_funktion(version, n, r, c):
                    continue
                m[r][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        spalte -= 2
        aufwaerts = not aufwaerts


MASKEN = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _strafe(m):
    """Bewertung nach ISO/IEC 18004, Abschnitt 8.8.2."""
    n = len(m)
    punkte = 0

    # Regel 1: Reihen gleicher Farbe.
    for linien in (m, list(map(list, zip(*m)))):
        for zeile in linien:
            lauf, vorher = 1, zeile[0]
            for wert in zeile[1:]:
                if wert == vorher:
                    lauf += 1
                else:
                    if lauf >= 5:
                        punkte += 3 + (lauf - 5)
                    lauf, vorher = 1, wert
            if lauf >= 5:
                punkte += 3 + (lauf - 5)

    # Regel 2: gleichfarbige 2x2-Bloecke.
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                punkte += 3

    # Regel 3: sucheraehnliche Folgen.
    muster1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    muster2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for linien in (m, list(map(list, zip(*m)))):
        for zeile in linien:
            for i in range(n - 10):
                if zeile[i:i + 11] in (muster1, muster2):
                    punkte += 40

    # Regel 4: Abweichung vom halben Dunkelanteil.
    dunkel = sum(sum(z) for z in m)
    anteil = dunkel * 100 // (n * n)
    punkte += 10 * min(abs(anteil - 50) // 5, abs(anteil - 50 + 4) // 5)
    return punkte


def _format_bits(maske: int) -> int:
    """15 Bit Formatangabe: Stufe M (0b00) plus Maske, BCH-gesichert."""
    daten = (0b00 << 3) | maske
    rest = daten << 10
    for i in range(4, -1, -1):
        if rest & (1 << (i + 10)):
            rest ^= 0b10100110111 << i
    return ((daten << 10) | rest) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    rest = version << 12
    for i in range(5, -1, -1):
        if rest & (1 << (i + 12)):
            rest ^= 0b1111100100101 << i
    return (version << 12) | rest


def _matrix_mit_maske(text: str, version: int, maske: int) -> List[List[int]]:
    """
    Die Matrix fuer EINE bestimmte Maske.

    Eigene Funktion, damit die Pruefreihe gegen einen fremden Encoder
    vergleichen kann: welche Maske die schoenste ist, entscheidet jede
    Umsetzung nach eigener Bewertung - die Module darunter muessen
    trotzdem dieselben sein.
    """
    n = version * 4 + 17
    cw = _codewoerter(text, version)
    m = _leer(n)
    _muster(m, version)
    _reserviert(m, version)
    _daten_setzen(m, version, cw)
    for r in range(n):
        for c in range(n):
            if not _ist_funktion(version, n, r, c) and MASKEN[maske](r, c):
                m[r][c] ^= 1

    f = _format_bits(maske)
    for i in range(15):
        # Hoechstwertiges Bit zuerst. Andersherum kommt derselbe Wert
        # spiegelverkehrt heraus - der Code sieht dann richtig aus, und
        # kein Lesegeraet erkennt ihn, weil ein Leser die Formatangabe
        # zuerst liest und danach aufgibt. Genau daran ist die erste
        # Fassung gescheitert.
        bit = (f >> (14 - i)) & 1
        if i < 6:
            m[8][i] = bit
        elif i == 6:
            m[8][7] = bit
        elif i == 7:
            m[8][8] = bit
        elif i == 8:
            m[7][8] = bit
        else:
            m[14 - i][8] = bit
        # Zweite Kopie: Bits 0-6 in die SPALTE 8 von unten, Bits 7-14 in
        # die ZEILE 8 von rechts. Auch das war zuerst vertauscht.
        if i < 7:
            m[n - 1 - i][8] = bit
        else:
            m[8][n - 15 + i] = bit
    m[n - 8][8] = 1

    if version >= 7:
        v = _version_bits(version)
        for i in range(18):
            bit = (v >> i) & 1
            m[i // 3][n - 11 + i % 3] = bit
            m[n - 11 + i % 3][i // 3] = bit
    return m


def _version_waehlen(text: str) -> int:
    # ASCII, nicht UTF-8, und das mit Absicht - siehe _pruefe_ascii().
    _pruefe_ascii(text)
    roh = len(text.encode("ascii"))
    for v in range(1, MAX_VERSION + 1):
        kapazitaet = sum(n * k for n, k in BLOECKE[v][1])
        noetig = 4 + (8 if v < 10 else 16) + roh * 8
        if noetig <= kapazitaet * 8:
            return v
    raise ValueError(
        f"{roh} Byte passen nicht in einen QR-Code bis Version "
        f"{MAX_VERSION} bei Fehlerkorrektur M.")


def matrix(text: str, version: int = None) -> List[List[int]]:
    """
    Die fertige Modulmatrix. 1 = dunkel.

    Von den acht Masken wird die mit der niedrigsten Strafbewertung
    genommen, wie in der Norm beschrieben.
    """
    if version is None:
        version = _version_waehlen(text)
    beste, bester_wert = None, None
    for maske in range(8):
        m = _matrix_mit_maske(text, version, maske)
        wert = _strafe(m)
        if bester_wert is None or wert < bester_wert:
            beste, bester_wert = m, wert
    return beste


def svg(text: str, modul: int = 4, rand: int = 4) -> str:
    """
    Der QR-Code als SVG.

    Ein einziger Pfad statt tausend Rechtecken - das spart in der
    Oberflaeche ein Vielfaches an Knoten und laesst sich als data:-URL
    einbetten.

    'rand' ist die Ruhezone: die Norm verlangt vier Module, und ohne sie
    finden viele Lesegeraete den Code nicht.
    """
    m = matrix(text)
    n = len(m)
    kante = (n + 2 * rand) * modul
    teile = []
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                teile.append(f"M{(c + rand) * modul} {(r + rand) * modul}"
                             f"h{modul}v{modul}h-{modul}z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{kante}" '
        f'height="{kante}" viewBox="0 0 {kante} {kante}" '
        f'shape-rendering="crispEdges" role="img">'
        f'<rect width="{kante}" height="{kante}" fill="#fff"/>'
        f'<path d="{"".join(teile)}" fill="#000"/></svg>')
