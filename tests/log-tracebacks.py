"""
CO-37 - Stapelauszuege im Backend-Protokoll zaehlen.

Wird von run-tests.sh am Ende eines Laufs aufgerufen. Gibt in der ersten
Zeile die Anzahl aus, danach die Auszuege selbst.

Eigene Datei und nicht drei Zeilen grep in run-tests.sh, weil hier eine
Entscheidung getroffen wird - welcher Auszug zaehlt und welcher nicht -
und Entscheidungen gehoeren an eine Stelle, die sich pruefen laesst.
tests/harness-test.py tut das.

Aufruf:

    python3 tests/log-tracebacks.py <protokolldatei>
"""
import pathlib
import sys

# Auszuege, die nichts ueber CO-37 aussagen.
#
# Ein Muster zaehlt nur, wenn ALLE seine Zeilen in demselben Auszug
# vorkommen. Ein Wort allein waere zu grob: "ConnectionResetError" kann
# auch aus dem Backend selbst kommen und muesste dann gemeldet werden.
#
# WinError 10054 aus asyncio/proactor_events: unter Windows schliesst
# der Client die Verbindung, bevor der Server sie zurueckbaut. Der
# Auszug entsteht in der Ereignisschleife von Python, nicht in CO-37,
# und er kam am 2026-09-08 bei jedem Lauf auf LENOVO. Eine Warnung,
# die immer da ist, bringt einem das Wegsehen bei.
HARMLOS = (
    ("proactor_events", "WinError 10054"),
)

BEGINN = "Traceback (most recent call last):"


def bloecke(text: str) -> list[list[str]]:
    """
    Zerlegt das Protokoll in Stapelauszuege.

    Ein Auszug beginnt mit der Kopfzeile und endet bei der ersten Zeile,
    die nicht eingerueckt ist - das ist die Ausnahmezeile selbst, und die
    gehoert noch dazu.
    """
    gefunden: list[list[str]] = []
    offen: list[str] | None = None
    for zeile in text.splitlines():
        if BEGINN in zeile:
            if offen is not None:
                gefunden.append(offen)
            offen = [zeile]
            continue
        if offen is None:
            continue
        offen.append(zeile)
        if zeile.strip() and not zeile[0].isspace():
            gefunden.append(offen)
            offen = None
    if offen is not None:
        gefunden.append(offen)
    return gefunden


def echt(block: list[str]) -> bool:
    """Wahr, wenn dieser Auszug gemeldet gehoert."""
    for muster in HARMLOS:
        if all(any(teil in zeile for zeile in block) for teil in muster):
            return False
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("0")
        return 2
    pfad = pathlib.Path(sys.argv[1])
    if not pfad.exists():
        print("0")
        return 0
    text = pfad.read_text(encoding="utf-8", errors="replace")
    gemeldet = [b for b in bloecke(text) if echt(b)]
    print(len(gemeldet))
    for block in gemeldet:
        for zeile in block:
            print(zeile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
