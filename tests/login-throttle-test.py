"""
CO-37 - Drosselung der Anmeldung.

Gezaehlt wird pro Absender-IP, nicht pro Konto: eine Kontosperre koennte
jeder ausloesen, der den Namen des Administrator-Kontos kennt, und wuerde
den einzigen Administrator aus seinem eigenen System aussperren.

Die Funktionen werden direkt geprueft, ohne HTTP - so laesst sich die Zeit
vorspulen, statt eine Viertelstunde zu warten.

    python3 tests/login-throttle-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/throttle.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_ADMIN_TOKEN"] = "test"
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import main  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


A, B = "192.0.2.50", "192.0.2.51"
main._LOGIN_FAILS.clear()

check("frische Adresse ist frei", main.login_retry_after(A) is None)

for i in range(main.LOGIN_MAX_FAILS - 1):
    main.note_login_failure(A)
check(f"{main.LOGIN_MAX_FAILS - 1} Fehlversuche -> noch frei",
      main.login_retry_after(A) is None)

n = main.note_login_failure(A)
check(f"{main.LOGIN_MAX_FAILS}. Fehlversuch -> gesperrt",
      main.login_retry_after(A) is not None, f"zaehler={n}")
check("Wartezeit ist plausibel",
      0 < main.login_retry_after(A) <= main.LOGIN_WINDOW.total_seconds(),
      main.login_retry_after(A))

check("andere Adresse bleibt frei", main.login_retry_after(B) is None)

# Zeit vorspulen: alle Versuche aus dem Fenster schieben
main._LOGIN_FAILS[A] = [t - main.LOGIN_WINDOW - timedelta(seconds=1)
                        for t in main._LOGIN_FAILS[A]]
check("nach Ablauf des Fensters wieder frei", main.login_retry_after(A) is None)
check("abgelaufene Eintraege werden weggeraeumt", A not in main._LOGIN_FAILS)

# Teilweise abgelaufen: nur der aelteste faellt raus
main._LOGIN_FAILS.clear()
for i in range(main.LOGIN_MAX_FAILS):
    main.note_login_failure(A)
main._LOGIN_FAILS[A][0] -= main.LOGIN_WINDOW + timedelta(seconds=1)
check("ein abgelaufener Versuch reicht zum Entsperren",
      main.login_retry_after(A) is None)
check("die uebrigen Versuche bleiben erhalten",
      len(main._LOGIN_FAILS.get(A, [])) == main.LOGIN_MAX_FAILS - 1,
      len(main._LOGIN_FAILS.get(A, [])))

# Erfolg loescht den Zaehler
main._LOGIN_FAILS.clear()
for i in range(main.LOGIN_MAX_FAILS):
    main.note_login_failure(A)
check("gesperrt vor dem Erfolg", main.login_retry_after(A) is not None)
main.note_login_success(A)
check("erfolgreiche Anmeldung loescht den Zaehler",
      main.login_retry_after(A) is None and A not in main._LOGIN_FAILS)

# Ohne bekannte Adresse darf nichts passieren
main._LOGIN_FAILS.clear()
check("ohne Adresse keine Sperre", main.login_retry_after(None) is None)
check("ohne Adresse kein Zaehler",
      main.note_login_failure(None) == 0 and not main._LOGIN_FAILS)

# Obergrenze der gemerkten Quellen
main._LOGIN_FAILS.clear()
main.LOGIN_MAX_SOURCES, keep = 10, main.LOGIN_MAX_SOURCES
for i in range(50):
    main.note_login_failure(f"10.0.0.{i}")
check("Zahl der gemerkten Quellen bleibt begrenzt",
      len(main._LOGIN_FAILS) <= 10, len(main._LOGIN_FAILS))
main.LOGIN_MAX_SOURCES = keep

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
