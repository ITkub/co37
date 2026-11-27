# run-tests.sh findet auf einem installierten System den Interpreter

## Absicht
`setup.sh` legt das venv unter `$BASE/venv` an (`/opt/co37/venv`). Die
Pruefreihen werden mit ausgeliefert und liegen dort neben `run-tests.sh`.
`run-tests.sh` sucht aber `$HIER/.venv/...`, dann `python3`, `python`, `py`.
Auf einem installierten System findet es so einen Interpreter ohne die
Backend-Pakete und bricht ab. Mit `CO37_PYTHON` geht es, man muss es wissen.

## Ziel
Neben `.venv` auch `$HIER/venv/bin/python` als Kandidat, in der Reihenfolge
nach den Arbeitsumgebungen des Projekts und vor dem, was im Pfad liegt.

## Nicht-Ziel
Kein Windows-Pfad fuer `venv` (installiert wird nur unter Linux). Keine
Aenderung an der Fehlermeldung, an `CO37_PYTHON` oder an `taugt()`.

## Ansatz
Eine Variable `VENV_I="$HIER/venv/bin/python"` und ein Eintrag in der
Kandidatenliste. Verworfen: `$BASE` aus der Umgebung lesen (das Skript soll
auch ohne gesetzte Umgebung laufen); nur die Meldung verbessern (der Fix
ist genauso klein und behebt die Ursache).

## Pruefung
Neue Pruefung in `tests/harness-test.py`: eine Kopie von `run-tests.sh` in
einem Ordner mit `venv/bin/python` und unbrauchbaren `python3`, `python`, `py`
im Pfad. Ohne den Fix meldet das Skript "Kein Python 3 gefunden", mit ihm
kommt es ueber die Suche hinaus.
