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
import time
from datetime import timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/throttle.db"
os.environ["CO37_DATA"] = str(TMP)
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


# ======================================================================
# Der Vergleichs-Hash steht fest (F-37)
# ======================================================================
# In login() stand bis 0.37.9 'hash_password("x")' fuer unbekannte
# Benutzernamen - bei JEDEM Versuch neu gebildet. Damit lief scrypt
# zweimal statt einmal, und die Maszahme tat das Gegenteil dessen, was
# ihr Kommentar versprach:
#
#     bekanntes Konto  : 41.5 ms  (1x scrypt)
#     unbekanntes Konto: 83.0 ms  (2x scrypt)
#
# 41 ms Unterschied sind aus dem Netz ablesbar. In der Pruefung davor
# stand diese Stelle unter "geprueft und in Ordnung": gelesen wurde die
# Absicht, nicht gezaehlt wurde, wie oft scrypt laeuft.
print("--- Der Blindhash verraet keine Kontonamen mehr (F-37) ---")
check("es gibt einen festen Vergleichs-Hash",
      isinstance(getattr(main, "BLIND_HASH", None), str))
check("er ist ein echter scrypt-Hash",
      str(getattr(main, "BLIND_HASH", "")).startswith("scrypt$"))

import ast as _ast  # noqa: E402
from pathlib import Path as _P  # noqa: E402
_q = (_P(__file__).resolve().parent.parent
      / "backend" / "main.py").read_text(encoding="utf-8")
_baum = _ast.parse(_q)
_login = next((k for k in _ast.walk(_baum)
               if isinstance(k, _ast.FunctionDef) and k.name == "login"), None)
check("login() gefunden", _login is not None)

if _login:
    # Ueber den Syntaxbaum, nicht per Zeichenkettensuche: die alte
    # Fassung steht oben im Kommentar und wuerde jede Suche gruen halten.
    _rufe = {k.func.id for k in _ast.walk(_login)
             if isinstance(k, _ast.Call) and isinstance(k.func, _ast.Name)}
    check("login() bildet keinen Hash mehr selbst",
          "hash_password" not in _rufe, sorted(_rufe))

    # Und die Zeitmessung selbst - die ist der eigentliche Befund.
    zeiten = {}
    for name, gespeichert in (("bekannt", main.hash_password("geheim")),
                              ("unbekannt", main.BLIND_HASH)):
        t0 = time.perf_counter()
        for _ in range(5):
            main.verify_password("falsch", gespeichert)
        zeiten[name] = (time.perf_counter() - t0) / 5
    verhaeltnis = zeiten["unbekannt"] / max(zeiten["bekannt"], 1e-9)
    check("bekannt und unbekannt kosten gleich viel",
          0.5 < verhaeltnis < 2.0,
          f"{zeiten['bekannt']*1000:.0f} ms vs "
          f"{zeiten['unbekannt']*1000:.0f} ms")

# ======================================================================
# Der Versuch wird gezaehlt, BEVOR geprueft wird (F-36)
# ======================================================================
# note_login_failure() stand bis 0.37.9 hinter der Passwortpruefung. Der
# Zaehler kannte damit nur ABGESCHLOSSENE Versuche: gleichzeitig
# eintreffende Anfragen passierten die Schranke alle, bevor eine von
# ihnen gezaehlt war. Dazwischen liegen zwei scrypt-Aufrufe zu je 16 MiB
# in einer synchronen Route - die belegt einen Arbeiter aus dem
# Threadpool, aus dem auch die Agent-Routen bedient werden.
print("--- Die Drosselung zaehlt beim Eintritt (F-36) ---")
if _login:
    _koerper = _login.body
    # Erste Stelle, an der note_login_failure vorkommt, und erste Stelle,
    # an der verify_password vorkommt - ueber die Zeilennummern im Baum.
    def _zeile(name):
        for k in _ast.walk(_login):
            if isinstance(k, _ast.Call) and isinstance(k.func, _ast.Name) \
                    and k.func.id == name:
                return k.lineno
        return None
    z_zaehlen = _zeile("note_login_failure")
    z_pruefen = _zeile("verify_password")
    check("note_login_failure() wird in login() aufgerufen",
          z_zaehlen is not None)
    check("verify_password() wird in login() aufgerufen",
          z_pruefen is not None)
    check("gezaehlt wird vor der teuren Passwortpruefung",
          z_zaehlen is not None and z_pruefen is not None
          and z_zaehlen < z_pruefen, f"zaehlen {z_zaehlen}, pruefen {z_pruefen}")
    check("eine gelungene Anmeldung loescht den Zaehler wieder",
          "note_login_success" in {k.func.id for k in _ast.walk(_login)
                                   if isinstance(k, _ast.Call)
                                   and isinstance(k.func, _ast.Name)})

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
