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
    # Frueher hiess diese Pruefung "login() bildet keinen Hash mehr
    # selbst" und verbot hash_password() in der ganzen Funktion. Das war
    # zu grob: seit 0.37.15 erneuert login() den Hash eines Bestandskontos
    # mit schwaecheren scrypt-Vorgaben - aber ERST, nachdem das Passwort
    # geprueft ist und der Fehlerpfad hinter ihr liegt.
    #
    # Was F-37 verlangt, ist praeziser: auf dem Weg zum 401 darf kein
    # zweiter scrypt-Lauf liegen. Genau das wird jetzt geprueft - ueber
    # die Zeilennummern im Syntaxbaum, nicht ueber das blosse Vorkommen.
    _raise401 = [k.lineno for k in _ast.walk(_login)
                 if isinstance(k, _ast.Raise)
                 and isinstance(k.exc, _ast.Call)
                 and getattr(k.exc.func, "id", "") == "HTTPException"
                 and k.exc.args and getattr(k.exc.args[0], "value", None) == 401]
    check("der Fehlerpfad mit 401 ist auffindbar", bool(_raise401), _raise401)
    _hashzeilen = [k.lineno for k in _ast.walk(_login)
                   if isinstance(k, _ast.Call)
                   and getattr(k.func, "id", "") == "hash_password"]
    check("vor dem 401 wird kein Hash gebildet",
          all(z > max(_raise401) for z in _hashzeilen) if _raise401 else False,
          f"hash_password in Zeile(n) {_hashzeilen}, 401 in {_raise401}")

    # Gegenprobe zur Aussagekraft: die Zeilennummern muessen ueberhaupt
    # etwas hergeben. Ohne das bestuende die Reihe auch bei leeren Listen.
    check("die Pruefung hat ueberhaupt Zeilen zu vergleichen",
          bool(_hashzeilen) and bool(_raise401),
          (_hashzeilen, _raise401))
    check("und das Erneuern liegt hinter der Passwortpruefung",
          "veraltet" in _rufe, sorted(_rufe))

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

# ======================================================================
# Der Aufwand von scrypt (Kleinkram aus Runde 4, behoben 2026-09-03)
# ======================================================================
# Im Pruefdokument stand seit Runde 4: "scrypt mit N=2^14 entspricht dem
# Stand von etwa 2010; heutige Empfehlungen liegen bei N=2^17. Das ist
# eine Zahl, keine Umstellung." Die zweite Haelfte war falsch - N=2^17
# braucht 128 MB je gleichzeitigem Anmeldeversuch und waere ein Hebel
# gegen den eigenen Speicher gewesen. Gewaehlt wurde die von OWASP
# ausdruecklich als gleichwertig genannte Variante 2^14/8/5: derselbe
# Speicherbedarf wie bisher, vierfache Rechenzeit.
#
# Geprueft wird der AUFWAND, nicht die einzelnen Zahlen - sonst schriebe
# die Reihe eine bestimmte Parameterwahl fest, und die naechste Anpassung
# muesste sie mit aendern statt von ihr bestaetigt zu werden.
print()
print("--- Der Aufwand von scrypt ---")

_alt = 2 ** 14 * 8 * 1        # Stand bis 0.37.14
_jetzt = main.SCRYPT_N * main.SCRYPT_R * main.SCRYPT_P
check("der Aufwand liegt deutlich ueber dem alten Stand",
      _jetzt >= 4 * _alt, f"{_jetzt} gegen {_alt}")
check("und der Speicherbedarf bleibt vertretbar",
      128 * main.SCRYPT_N * main.SCRYPT_R <= 32 * 1024 * 1024,
      f"{128 * main.SCRYPT_N * main.SCRYPT_R / 1048576:.0f} MB")
# OpenSSL deckelt scrypt sonst bei 32 MB; ohne maxmem scheitert jede
# Anhebung ueber N=2^15 mit "memory limit exceeded" statt zu rechnen.
check("maxmem wird mitgegeben", "maxmem=SCRYPT_MAXMEM" in _q)

# Und der Weg fuer Bestandskonten: die Vorgaben stehen IM Hash und werden
# beim Pruefen von dort gelesen. Ohne Erneuern beim Anmelden erreicht eine
# Anhebung der Konstanten kein einziges vorhandenes Konto - dieselbe
# Falle wie bei F-14, ein Wert, den niemand liest.
import hashlib as _hl  # noqa: E402
import secrets as _sec  # noqa: E402

_salt = _sec.token_bytes(16)
_dk = _hl.scrypt(b"altes-passwort", salt=_salt, n=2 ** 14, r=8, p=1, dklen=32)
_althash = f"scrypt$16384$8$1${_salt.hex()}${_dk.hex()}"
check("ein alter Hash laesst sich weiterhin pruefen",
      main.verify_password("altes-passwort", _althash))
check("er gilt aber als veraltet", main.veraltet(_althash))
check("ein frisch gebildeter nicht",
      not main.veraltet(main.hash_password("neues-passwort")))
check("und Unsinn gilt ebenfalls als veraltet",
      main.veraltet("kaputt") and main.veraltet(""))


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
