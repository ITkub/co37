"""
CO-37 - Schiebt das Archivwerkzeug die richtigen Pakete beiseite?

Zwei Dinge duerfen hier nicht schiefgehen, und beide sind still, wenn sie
schiefgehen:

Die Sortierung. Nach Namen sortiert stuende 0_34_10 vor 0_34_9 - das
Werkzeug behielte dann die falschen Pakete und verschoebe das neueste ins
Archiv. Danach zeigte der Ordner ein Paket, das nicht das aktuelle ist,
und niemand merkte es.

Die Begleitdateien. Wandert die ZIP ohne ihre .sig, bleiben zwei halbe
Pakete zurueck: eines im Archiv, das sich nicht mehr einspielen laesst,
und eine Signatur im Projektordner, die zu nichts gehoert.

Laeuft ohne Backend und ohne Netz, alles in einem temporaeren Ordner.

Aufruf:

    python3 tests/archiv-test.py
"""
import importlib.util
import tempfile
from pathlib import Path

HIER = Path(__file__).resolve().parent
WERKZEUG = HIER.parent / "tools" / "archiv-pakete.py"

spec = importlib.util.spec_from_file_location("co37_archiv", WERKZEUG)
archiv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archiv)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def baue(ordner: Path, versionen, ohne_begleiter=()):
    """Legt Attrappen an: je ZIP mit .sig und .sha256, sofern nicht ausgenommen."""
    for v in versionen:
        name = f"co37_v{v}.zip"
        (ordner / name).write_text("paket", encoding="ascii")
        if v in ohne_begleiter:
            continue
        (ordner / (name + ".sig")).write_text("sig", encoding="ascii")
        (ordner / (name + ".sha256")).write_text("summe", encoding="ascii")


def namen(ordner: Path):
    return sorted(p.name for p in ordner.iterdir())


still = lambda *_a, **_k: None   # noqa: E731 - Ausgabe im Test unterdruecken

# ----------------------------------------------------------------------
# Sortierung nach Zahlen, nicht nach Text
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    # 0_34_9 und 0_34_10 sind der entscheidende Fall: nach Text sortiert
    # kaeme 10 vor 9.
    baue(ordner, ["0_34_8", "0_34_9", "0_34_10"])

    archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2, ausgabe=still)

    geblieben = namen(ordner)
    check("die zwei hoechsten Versionen bleiben liegen",
          geblieben == ["co37_v0_34_10.zip", "co37_v0_34_10.zip.sha256",
                        "co37_v0_34_10.zip.sig", "co37_v0_34_9.zip",
                        "co37_v0_34_9.zip.sha256", "co37_v0_34_9.zip.sig"],
          geblieben)
    check("0_34_10 gilt als neuer als 0_34_9",
          "co37_v0_34_10.zip" in geblieben)
    check("die aeltere wandert ins Archiv",
          "co37_v0_34_8.zip" in namen(ziel), namen(ziel))

# ----------------------------------------------------------------------
# Begleitdateien wandern mit
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    baue(ordner, ["0_33_1", "0_34_4", "0_34_5"])

    archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2, ausgabe=still)

    check("Signatur wandert mit der ZIP",
          "co37_v0_33_1.zip.sig" in namen(ziel), namen(ziel))
    check("Pruefsumme wandert mit der ZIP",
          "co37_v0_33_1.zip.sha256" in namen(ziel), namen(ziel))
    check("keine verwaiste Signatur bleibt zurueck",
          not any(n.startswith("co37_v0_33_1") for n in namen(ordner)),
          namen(ordner))

# ----------------------------------------------------------------------
# Paket ohne Signatur - der Fall co37_v0_34_0
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    baue(ordner, ["0_34_0", "0_34_4", "0_34_5"], ohne_begleiter=["0_34_0"])

    archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2, ausgabe=still)

    check("Paket ohne Signatur wandert trotzdem",
          "co37_v0_34_0.zip" in namen(ziel), namen(ziel))

# ----------------------------------------------------------------------
# Was nicht angefasst werden darf
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    baue(ordner, ["0_33_1", "0_34_4", "0_34_5"])
    (ordner / "README.md").write_text("text", encoding="ascii")
    (ordner / "co37_alt.zip").write_text("kein Muster", encoding="ascii")
    (ordner / "backend").mkdir()

    archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2, ausgabe=still)

    check("fremde Dateien bleiben unberuehrt",
          "README.md" in namen(ordner), namen(ordner))
    check("ZIP ohne Versionsmuster bleibt liegen",
          "co37_alt.zip" in namen(ordner), namen(ordner))
    check("Verzeichnisse bleiben unberuehrt", (ordner / "backend").is_dir())

# ----------------------------------------------------------------------
# Probe veraendert nichts
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    baue(ordner, ["0_33_1", "0_34_4", "0_34_5"])
    vorher = namen(ordner)

    ergebnis = archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2,
                                 probe=True, ausgabe=still)

    check("Probe verschiebt nichts", namen(ordner) == vorher)
    check("Probe legt kein Ziel an", not ziel.exists())
    check("Probe meldet trotzdem, was geschaehe",
          "co37_v0_33_1.zip" in ergebnis["verschoben"], ergebnis["verschoben"])

# ----------------------------------------------------------------------
# Vorhandenes im Archiv wird nicht stillschweigend ueberschrieben
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    ziel.mkdir()
    (ziel / "co37_v0_33_1.zip").write_text("aeltere Fassung", encoding="ascii")
    baue(ordner, ["0_33_1", "0_34_4", "0_34_5"])

    ergebnis = archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2,
                                 ausgabe=still)

    check("vorhandene Datei im Archiv bleibt unangetastet",
          (ziel / "co37_v0_33_1.zip").read_text(encoding="ascii")
          == "aeltere Fassung")
    check("und das wird gemeldet, nicht verschwiegen",
          "co37_v0_33_1.zip" in ergebnis["uebersprungen"],
          ergebnis["uebersprungen"])

# ----------------------------------------------------------------------
# Weniger Pakete als behalten werden sollen
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    ziel = Path(tmp) / "archiv"
    baue(ordner, ["0_34_5"])

    ergebnis = archiv.archiviere(ordner=ordner, ziel=ziel, behalten=2,
                                 ausgabe=still)
    check("ein einzelnes Paket wird nicht verschoben",
          ergebnis["verschoben"] == [], ergebnis["verschoben"])
    check("und kein leeres Archiv angelegt", not ziel.exists())

# ----------------------------------------------------------------------
# behalten=0 waere ein leerer Ordner - muss abgelehnt werden
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory(prefix="co37-archiv-") as tmp:
    ordner = Path(tmp) / "projekt"
    ordner.mkdir()
    baue(ordner, ["0_34_5"])
    abgelehnt = False
    try:
        archiv.archiviere(ordner=ordner, ziel=Path(tmp) / "a", behalten=0,
                          ausgabe=still)
    except ValueError:
        abgelehnt = True
    check("behalten=0 wird abgelehnt", abgelehnt)

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
