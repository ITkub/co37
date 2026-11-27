# run-tests.sh: venv des installierten Systems - Plan

> Ausfuehrung inline, ein Task.

**Spec:** `docs/superpowers/specs/2026-11-27-run-tests-installiert-design.md`

## Task 1: Kandidat `$HIER/venv/bin/python`

**Files:** Modify `run-tests.sh` (Kandidatenliste ~Zeile 105); Test `tests/harness-test.py`.

- [ ] Test schreiben in `tests/harness-test.py`: Kopie von `run-tests.sh` (Helfer `abseits`), im selben Ordner ein Skript `venv/bin/python`, das `exec`t auf `sys.executable`; `store_attrappen` fuer python3/python/py; belegter Port; Aufruf mit `skript=`. Erwartung: Ausgabe enthaelt nicht "Kein Python 3 gefunden" und nicht "ohne die Pakete".
- [ ] `./run-tests.sh harness` ausfuehren: erwartet FEHLER (Skript meldet "Kein Python 3 gefunden").
- [ ] `run-tests.sh`: `VENV_I="$HIER/venv/bin/python"` anlegen und in der Kandidatenliste nach `$VENV_W` einsetzen; Kommentar nach Projektstil.
- [ ] `./run-tests.sh harness` ausfuehren: erwartet gruen; danach Gesamtlauf `./run-tests.sh`.
- [ ] Commit (Diff auf Secrets, Injection, Eingabepruefung pruefen; `python tests/ki-hinweise-test.py`).
