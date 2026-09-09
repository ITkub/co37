"""
CO-37 - Aufbewahrungsfrist des Pruefprotokolls (F-64).

Bis 0.37.19 wurde im Pruefprotokoll nie etwas geloescht. Das war eine
Entscheidung, keine Nachlaessigkeit - wer sich selbst herausschreiben
kann, macht das Protokoll wertlos. Nur ist "nie loeschen" keine Loesung,
sondern die Abwesenheit einer.

BSI OPS.1.1.5.A5, Basis-Anforderung, am Primaertext der Edition 2023
gelesen (Mike hat die PDF am 2026-09-05 beigelegt, weil sie ueber den
Abrufweg des Prueflaufs nicht auslesbar ist):

    "Protokollierungsdaten MUESSEN nach einem festgelegten Prozess
     geloescht werden. Es MUSS technisch unterbunden werden, dass
     Protokollierungsdaten unkontrolliert geloescht oder veraendert
     werden."

Beide Haelften. Die zweite ist der Grund fuer die Bauform, die hier
geprueft wird - zusammen mit OPS.1.1.5.A10:

    "Es SOLLTE sichergestellt sein, dass die ausfuehrenden
     Administrierenden selbst keine Berechtigung haben, die
     aufgezeichneten Protokollierungsdaten zu veraendern oder zu
     loeschen."

Eine Frist, die ein Administrator im Dashboard auf einen Tag stellen
kann, waere genau diese Berechtigung durch die Hintertuer: Frist runter,
warten, Frist zurueck. Deshalb steht sie ausschliesslich in der Umgebung.
Diese Reihe prueft, dass es dabei bleibt.

Direkt gegen die Funktionen, ohne HTTP - so laesst sich ein Jahr
vorspulen, statt eines zu warten.

    python3 tests/protokoll-test.py
"""
import ast
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/protokoll.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402
from models import AuditEntry  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


SQLModel.metadata.create_all(main.engine)


def leeren():
    with Session(main.engine) as s:
        for zeile in s.exec(select(AuditEntry)).all():
            s.delete(zeile)
        s.commit()


def eintrag(alter_tage: float, actor="alt", action="test"):
    with Session(main.engine) as s:
        s.add(AuditEntry(actor=actor, action=action,
                         at=main.utcnow() - timedelta(days=alter_tage)))
        s.commit()


def bestand():
    with Session(main.engine) as s:
        return [(e.actor, e.action, e.detail)
                for e in s.exec(select(AuditEntry)).all()]


# ======================================================================
# Die Vorgabe
# ======================================================================
print("--- Die Vorgabe ---")
check("ab Werk 365 Tage", main.AUDIT_DAYS == 365, main.AUDIT_DAYS)
check("und sie kommt aus der Umgebung",
      'os.getenv("CO37_AUDIT_DAYS"' in (WURZEL / "backend" / "main.py")
      .read_text(encoding="utf-8"))


# ======================================================================
# Was geloescht wird und was nicht
# ======================================================================
print()
print("--- Die Grenze ---")
# Knapp darunter und knapp darueber, nicht "viel juenger" gegen "viel
# aelter": eine Frist, die um Wochen danebenliegt, faellt sonst nicht auf.
leeren()
eintrag(364.9, actor="jung")
eintrag(365.1, actor="alt")
with Session(main.engine) as s:
    entfernt = main.pruefprotokoll_bereinigen(s)
    s.commit()
check("genau ein Eintrag ist entfernt worden", entfernt == 1, entfernt)
namen = [a for a, _, _ in bestand()]
check("der juengere steht noch da", "jung" in namen, namen)
check("der aeltere ist weg", "alt" not in namen, namen)


# ======================================================================
# Die Loeschung steht selbst im Protokoll
# ======================================================================
print()
print("--- Der Vermerk ---")
# Eine Loeschung, die man dem Protokoll nicht ansieht, ist selbst eine
# unbemerkte Aenderung am Protokoll - also genau das, wogegen es da ist.
vermerke = [(a, ak, d) for a, ak, d in bestand() if ak == "audit.pruned"]
check("die Bereinigung hat sich selbst vermerkt", len(vermerke) == 1, vermerke)
if vermerke:
    _, _, detail = vermerke[0]
    check("der Vermerk nennt die Anzahl", "1 Eintr" in str(detail), detail)
    check("und die Frist, gegen die geraeumt wurde",
          "365" in str(detail), detail)
    check("er stammt von 'system', nicht von einem Benutzer",
          vermerke[0][0] == "system", vermerke[0][0])

# Kein Fund, kein Vermerk - sonst waechst das Protokoll jeden Tag um eine
# Zeile, die nichts sagt.
leeren()
eintrag(1, actor="jung")
with Session(main.engine) as s:
    entfernt = main.pruefprotokoll_bereinigen(s)
    s.commit()
check("ohne Fund wird nichts entfernt", entfernt == 0, entfernt)
check("und nichts vermerkt",
      not [1 for _, ak, _ in bestand() if ak == "audit.pruned"], bestand())


# ======================================================================
# Ein Loeschbefehl, nicht einer je Zeile (F-67)
# ======================================================================
print()
print("--- Ein Befehl statt tausend ---")
# Die erste Fassung holte jede faellige Zeile als Objekt und rief
# session.delete() darauf auf. Gemessen: 200 000 Eintraege brauchten
# 6,62 s statt 0,49 s - und zwar INNERHALB der Anfrage, die zufaellig die
# erste nach Ablauf des Tages ist, mit der Schreibsperre von SQLite in
# der Hand.
#
# Gezaehlt wird hier, was wirklich an die Datenbank geht. Eine Pruefung
# auf "steht delete( im Quelltext" saehe dasselbe und wuesste nichts.
from sqlalchemy import event  # noqa: E402

befehle = []


def _mitschreiben(conn, cursor, anweisung, parameter, kontext, viele):
    if anweisung.lstrip().upper().startswith("DELETE"):
        befehle.append(anweisung)


leeren()
for _ in range(50):
    eintrag(400)
event.listen(main.engine, "before_cursor_execute", _mitschreiben)
try:
    with Session(main.engine) as s:
        entfernt = main.pruefprotokoll_bereinigen(s)
        s.commit()
finally:
    event.remove(main.engine, "before_cursor_execute", _mitschreiben)
check("fuenfzig Eintraege sind weg", entfernt == 50, entfernt)
check("dafuer ging genau EIN Loeschbefehl an die Datenbank",
      len(befehle) == 1, f"{len(befehle)} Befehle")
check("und der loescht ueber eine Bedingung, nicht ueber eine Kennung",
      bool(befehle) and " WHERE " in befehle[0].upper()
      and "id" not in befehle[0].split("WHERE")[-1],
      befehle[0].replace("\n", " ")[:90] if befehle else "")


# ======================================================================
# Abschalten
# ======================================================================
print()
print("--- Abschalten ---")
leeren()
eintrag(4000, actor="uralt")
alt_wert = main.AUDIT_DAYS
main.AUDIT_DAYS = 0
with Session(main.engine) as s:
    entfernt = main.pruefprotokoll_bereinigen(s)
    s.commit()
check("mit 0 wird nichts geloescht", entfernt == 0, entfernt)
check("der uralte Eintrag steht noch",
      "uralt" in [a for a, _, _ in bestand()], bestand())
main.AUDIT_DAYS = alt_wert


# ======================================================================
# audit() sieht nach, aber nicht bei jedem Eintrag
# ======================================================================
print()
print("--- Hoechstens einmal am Tag ---")
# Das Protokoll kann NUR ueber audit() wachsen. Wer dort nachsieht, sieht
# genau dann nach, wenn etwas dazugekommen ist. Nur eben nicht bei jedem
# einzelnen Eintrag - sonst laeuft bei einem Ausrollen ueber die Flotte
# ein DELETE-Durchlauf je Auftrag.
leeren()
eintrag(400, actor="alt1")
eintrag(400, actor="alt2")
main._letzte_bereinigung = None
with Session(main.engine) as s:
    main.audit(s, "wer", "erste")
    s.commit()
namen = [a for a, _, _ in bestand()]
check("der erste Aufruf raeumt auf",
      "alt1" not in namen and "alt2" not in namen, namen)

eintrag(400, actor="alt3")
with Session(main.engine) as s:
    main.audit(s, "wer", "zweite")
    s.commit()
namen = [a for a, _, _ in bestand()]
check("der zweite Aufruf gleich danach raeumt NICHT noch einmal auf",
      "alt3" in namen, namen)

# Zeit vorspulen statt einen Tag warten.
main._letzte_bereinigung -= main.AUDIT_PRUNE_INTERVAL + 1

# Gegenprobe zum Anfangswert: None heisst "noch nie gelaufen". Stuende
# dort 0.0, haenge der erste Lauf an der Betriebsdauer der Maschine -
# time.monotonic() zaehlt unter Linux ab dem Systemstart. Genau daran ist
# die erste Fassung dieser Reihe haengengeblieben.
quelle_anfang = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
check("der Anfangswert ist None und nicht 0.0",
      "_letzte_bereinigung: Optional[float] = None" in quelle_anfang)
check("und die Bedingung faengt None ab",
      "_letzte_bereinigung is None" in quelle_anfang)
with Session(main.engine) as s:
    main.audit(s, "wer", "dritte")
    s.commit()
namen = [a for a, _, _ in bestand()]
check("nach Ablauf des Zeitraums wieder",
      "alt3" not in namen, namen)


# ======================================================================
# Eine misslungene Bereinigung darf den Eintrag nicht mitreissen
# ======================================================================
print()
print("--- Der Eintrag ist wichtiger als das Aufraeumen ---")
leeren()
main._letzte_bereinigung = None
echt = main.pruefprotokoll_bereinigen


def kaputt(session):
    raise RuntimeError("absichtlich")


main.pruefprotokoll_bereinigen = kaputt
try:
    with Session(main.engine) as s:
        main.audit(s, "wer", "trotzdem")
        s.commit()
    geschrieben = [ak for _, ak, _ in bestand()]
except Exception as exc:  # noqa: BLE001
    geschrieben = f"Ausnahme durchgeschlagen: {exc}"
finally:
    main.pruefprotokoll_bereinigen = echt
check("der Protokolleintrag steht trotz gescheiterter Bereinigung",
      geschrieben == ["trotzdem"], geschrieben)


# ======================================================================
# OPS.1.1.5.A10 - kein Weg ueber die Schnittstelle
# ======================================================================
print()
print("--- Kein Weg ueber die Schnittstelle (OPS.1.1.5.A10) ---")
quelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
baum = ast.parse(quelle)

# Alle Routen einsammeln, die einen Pfad mit 'audit' bedienen.
audit_routen = []
for knoten in ast.walk(baum):
    if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for deko in knoten.decorator_list:
        if not isinstance(deko, ast.Call):
            continue
        for arg in deko.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                    and "audit" in arg.value:
                verb = getattr(deko.func, "attr", "?")
                audit_routen.append((verb, arg.value, knoten.name))

check("es gibt genau eine Route zum Pruefprotokoll",
      len(audit_routen) == 1, audit_routen)
check("und die liest nur",
      all(v == "get" for v, _, _ in audit_routen), audit_routen)

# Loeschen darf ausschliesslich in der Bereinigungsfunktion stehen.
loescher = []
for knoten in ast.walk(baum):
    if not isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    inhalt = ast.get_source_segment(quelle, knoten) or ""
    if "session.delete" in inhalt and "AuditEntry" in inhalt:
        loescher.append(knoten.name)
check("AuditEntry wird nur an einer Stelle geloescht",
      loescher == ["pruefprotokoll_bereinigen"], loescher)

# Und die Frist darf nirgends aus einer Anfrage kommen.
check("die Frist wird nirgends zur Laufzeit gesetzt",
      quelle.count("AUDIT_DAYS =") == 1, quelle.count("AUDIT_DAYS ="))
check("es gibt keine Einstellung dafuer in der Datenbank",
      "CO37_AUDIT_DAYS" in quelle and "SET_AUDIT" not in quelle)


# ======================================================================
# Archivierung vor dem Loeschen (OPS.1.1.5.A8)
# ======================================================================
# A5 verlangt einen Loeschprozess, A8 die Archivierung. Beides zugleich
# geht nur, wenn das Geloeschte vorher woanders steht. Ab Werk aus; ist
# ein Ziel eingetragen, gilt: erst archivieren, dann loeschen - und wenn
# das Archivieren misslingt, wird NICHT geloescht.
print("\n--- Archivierung ---")
import json as _json  # noqa: E402

check("ab Werk ist kein Archiv eingetragen", main.AUDIT_ARCHIVE == "",
      repr(main.AUDIT_ARCHIVE))
check("und das Ziel kommt aus der Umgebung",
      'os.getenv("CO37_AUDIT_ARCHIVE"' in (WURZEL / "backend" / "main.py")
      .read_text(encoding="utf-8"))

_archiv = TMP / "archiv"
_echt_archive = main.AUDIT_ARCHIVE
main.AUDIT_ARCHIVE = str(_archiv)
try:
    leeren()
    # Zwei faellige Eintraege aus verschiedenen Jahren, einer aktuell.
    eintrag(400, actor="alt-a", action="login.failed")
    eintrag(800, actor="alt-b", action="host.approve")
    eintrag(1, actor="neu", action="login.ok")
    with Session(main.engine) as s:
        entfernt = main.pruefprotokoll_bereinigen(s)
        s.commit()

    check("die faelligen Eintraege wurden entfernt", entfernt == 2, entfernt)
    _dateien = sorted(p.name for p in _archiv.glob("audit-*.jsonl"))
    check("es entstanden Archivdateien", len(_dateien) >= 1, _dateien)

    _zeilen = []
    for _p in _archiv.glob("audit-*.jsonl"):
        for _z in _p.read_text(encoding="utf-8").splitlines():
            if _z.strip():
                _zeilen.append(_json.loads(_z))
    _actors = sorted(z["actor"] for z in _zeilen)
    check("beide entfernten Eintraege stehen im Archiv",
          _actors == ["alt-a", "alt-b"], _actors)
    check("der aktuelle Eintrag steht NICHT im Archiv",
          "neu" not in _actors, _actors)
    check("jede Zeile ist fuer sich lesbares JSON", len(_zeilen) == 2, _zeilen)
    check("die Zeile traegt Zeitpunkt und Aktion",
          all(z.get("at") and z.get("action") for z in _zeilen), _zeilen)
    # Nach Jahr des EINTRAGS benannt, nicht nach dem der Bereinigung -
    # sonst laege beim ersten Lauf auf einer alten Anlage der ganze
    # Ueberhang in einer einzigen Datei des laufenden Jahres.
    _jahre = {int(n[len("audit-"):-len(".jsonl")]) for n in _dateien}
    _erwartet = {main.ensure_utc(main.utcnow()).year - j
                 for j in (2, 3)} | {main.ensure_utc(main.utcnow()).year - 1}
    check("die Dateien sind nach dem Jahr des Eintrags benannt",
          len(_jahre) == 2 and _jahre <= _erwartet, sorted(_jahre))

    # Zweiter Lauf: es ist nichts mehr faellig, also kommt auch nichts
    # doppelt ins Archiv.
    _vorher = sum(len(p.read_text(encoding="utf-8").splitlines())
                  for p in _archiv.glob("audit-*.jsonl"))
    with Session(main.engine) as s:
        main.pruefprotokoll_bereinigen(s)
        s.commit()
    _nachher = sum(len(p.read_text(encoding="utf-8").splitlines())
                   for p in _archiv.glob("audit-*.jsonl"))
    check("ein zweiter Lauf schreibt nichts doppelt",
          _vorher == _nachher, f"{_vorher} -> {_nachher}")

    # Der Kern: laesst sich nicht archivieren, wird nicht geloescht.
    leeren()
    eintrag(400, actor="bleibt", action="login.failed")
    main.AUDIT_ARCHIVE = str(TMP / "gibtsnicht" / "\0ungueltig")
    with Session(main.engine) as s:
        entfernt2 = main.pruefprotokoll_bereinigen(s)
        s.commit()
    check("bei misslungener Archivierung wird nichts geloescht",
          entfernt2 == 0, entfernt2)
    _rest = bestand()
    check("der Eintrag steht noch da",
          any(a == "bleibt" for a, _, _ in _rest), _rest)
    check("und der Grund steht im Protokoll",
          any(ak == "audit.archive_failed" for _, ak, _ in _rest), _rest)
finally:
    main.AUDIT_ARCHIVE = _echt_archive

# Ohne eingetragenes Ziel bleibt es beim alten Verhalten.
leeren()
eintrag(400, actor="weg", action="login.failed")
with Session(main.engine) as s:
    entfernt3 = main.pruefprotokoll_bereinigen(s)
    s.commit()
check("ohne Archivziel wird wie bisher geloescht", entfernt3 == 1, entfernt3)


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
