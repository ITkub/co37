# CO-37 - Tests

## Alle auf einmal

```
./run-tests.sh          # alles
./run-tests.sh roles    # nur eine Reihe
./run-tests.sh -v       # volle Ausgabe
```

Startet ein eigenes Backend auf Port 8099 mit eigener Datenbank in einem
temporaeren Verzeichnis, laesst alle Reihen in der richtigen Reihenfolge
durchlaufen und raeumt danach auf. Die Produktivdaten werden nie
angefasst. Anderer Port ueber `CO37_TEST_PORT`.

Rueckgabewert 0, wenn alles besteht - damit laesst es sich verketten.
Tracebacks im Backend-Log gelten dabei als Fehler, auch wenn keine
Pruefung fehlschlaegt.

**Die Reihenfolge ist nicht beliebig.** `roles` muss zuletzt laufen: die
Reihe loest am Ende die Drosselung der Anmeldung aus, die fuer die
gesamte Absender-Adresse gilt und eine Viertelstunde bestehen bleibt.
Jede danach gestartete Reihe, die sich anmeldet, liefe in die Sperre.

---

## Frontend

Führt das Skript aus `frontend/index.html` in Node gegen ein laufendes Backend
aus. Statt eines Browsers eine minimale DOM-Attrappe — damit lässt sich prüfen,
ob die Funktionen durchlaufen und was sie in die Elemente schreiben.

Grund für dieses Gerüst: ein fehlender Funktionsname hat einmal den Aufbau des
Agents-Reiters abgebrochen und drei Bereiche leer gelassen. Statische Prüfung
hatte das nicht gefunden.

```
# Backend starten (eigenes Fenster)
cd backend
CO37_ADMIN_TOKEN=t CO37_SECRET_KEY=k \
  CO37_DB=sqlite:///./test.db CO37_DATA=../data \
  ../venv/bin/uvicorn main:app --port 8085

# Test
node tests/frontend-test.js 8085 t frontend/index.html
```

Zwei Pruefungen setzen Daten voraus, die es in einer frischen Testdatenbank
nicht gibt, und melden dann `uebersprungen` statt einen Fehler: der echte
Paketname (dafuer muss der Watcher gelaufen sein und Pakete gebaut haben) und
der Neustart-Dialog (dafuer muss ein Host angemeldet sein).

Die Attrappe bildet absichtlich einen **unsicheren Kontext** nach
(`window.isSecureContext = false`, kein `navigator.clipboard`), weil CO-37
über HTTP läuft. Sie bricht ab, wenn ein Reiter-Handler die ungenauen
Selektoren `.tabs button` oder `.pane` verwendet — beides hat schon einmal die
Bereiche beider Dialoge gegenseitig ausgeblendet.

## Agent-API

Prueft die Schnittstelle, ueber die der Agent spricht — Heartbeat, Meldung,
Auftraege. Der Frontend-Test deckt nur die Bedienoberflaeche ab; die
Agent-Seite blieb ungeprueft, und dort ist es schiefgegangen: eine
Boot-Zeit ohne Zeitzone liess jeden Heartbeat eines aktuellen Agents mit
500 scheitern. Aufgefallen ist das erst auf einem echten Host, weil der
Agent unter `pythonw` ohne Konsole laeuft und die Fehlermeldung ins Leere
schreibt.

```
# Backend wie oben starten, dann
python3 tests/agent-api-test.py
```

Legt einen eigenen Host an und meldet ihn frei. Nur gegen eine
Testdatenbank laufen lassen, nie gegen den Produktivbestand.

## Selbstheilung des Agents

Prueft, wann eine fehlerhafte Fassung zurueckgenommen wird und wann nicht,
sowie das Protokoll. Braucht kein Backend und faellt weder ueber das
Arbeitsverzeichnis noch ueber `/etc` her — der Test legt sich eine eigene
Kopie von `agent.py` samt Konfiguration in einem temporaeren Verzeichnis an.

```
python3 tests/agent-selfheal-test.py
```

Der wichtigste Fall darin ist der, der **nicht** ausloesen darf: ein nicht
erreichbarer Server. Wuerde er zaehlen, rollte die gesamte Flotte zurueck,
sobald das Backend fuer ein Update kurz steht.

## Rollentrennung

**Als letzten Test gegen ein Backend laufen lassen.** Am Ende loest er die
Drosselung der Anmeldung aus; die gilt fuer die gesamte Absender-Adresse
und bleibt eine Viertelstunde bestehen. Wer danach einen Test startet, der
sich anmeldet, bekommt Fehler, die nichts mit diesem Test zu tun haben.

Prueft, was ein Konto mit der Rolle `user` **nicht** darf, und dass ein
Installations-Token ausschliesslich Pakete herunterladen kann — begrenzt
auf wenige Abrufe und eine kurze Frist.

```
# Backend wie oben starten, dann
python3 tests/roles-test.py
```

Hintergrund: die Pruefung hiess `require_admin`, liess aber jeden
angemeldeten Benutzer durch. Ein Benutzer konnte damit Hosts freigeben,
die Checkmk-Zugangsdaten aendern und ueber das Hochladen eines
Systemupdates Code als root ausfuehren. Kein Test hat die Rollen geprueft
— aufgefallen ist es nur beim Durchlesen.

## Faelligkeit von Patch-Laeufen

Prueft, wann ein Nachschlag angelegt wird und wann nicht. Braucht kein
Backend und kein Netz — die Logik wird direkt gegen eine SQLite-Datei im
temporaeren Verzeichnis geprueft.

```
python3 tests/patchdue-test.py
```

Anlass: ein von Hand ausgeloester Scan hat sofort einen Patch-Lauf nach
sich gezogen. Der Nachschlag prueft nur, ob Updates gemeldet sind — woher
die Zahl stammt, hat er nicht unterschieden. Auf einem zweiten Host trat
es nicht auf, weil dort der Nachlauf des Termins schon abgelaufen war; es
sah nach Zufall aus, war aber dieselbe Regel mit anderem Ausgangszustand.

## Drosselung der Anmeldung

```
python3 tests/login-throttle-test.py
```

Braucht kein Backend: die Funktionen werden direkt geprueft, damit sich
die Zeit vorspulen laesst statt eine Viertelstunde zu warten. Dass die
Route die Drosselung auch anwendet, prueft `roles-test.py` ueber HTTP.

Gezaehlt wird pro Absender-IP, **nicht** pro Konto. Eine Kontosperre
koennte jeder ausloesen, der den Namen des Administrator-Kontos kennt —
und wuerde den einzigen Administrator aus seinem eigenen System
aussperren, moeglicherweise mitten in einem Wartungsfenster.

## Reverse Proxy und HTTPS-Zwang

```
python3 tests/proxy-fallback-test.py    # ohne Backend
python3 tests/proxy-https-test.py       # gegen ein laufendes Backend
```

Der erste prueft den Rueckfall: ist der Zwang an und der Proxy falsch
eingetragen, kommt niemand mehr hinein — auch nicht, um ihn wieder
abzuschalten. Nach Ablauf der Frist muss er sich von selbst
zuruecknehmen. Ueber HTTP laesst sich das nicht pruefen, weil in genau
diesem Zustand auch der Weg zum Abschalten zu ist.

Ebenfalls dort: `/api/health` ueber Loopback bleibt frei. Ohne diese
Ausnahme haelt der Watcher ein eingespieltes Update fuer fehlgeschlagen
und spielt die Sicherung zurueck.
