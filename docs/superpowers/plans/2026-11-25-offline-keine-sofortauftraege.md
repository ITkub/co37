# Keine Sofort-Auftraege fuer Offline-Hosts - Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /api/v1/hosts/{id}/jobs` lehnt Scan und Patch ohne Termin bei einem Host ab, der offline ist; die Oberflaeche meldet uebersprungene Hosts.

**Architecture:** Die lokale Funktion `_online` im Statusbericht (`backend/main.py`, ~Zeile 6968) wird zur Modulfunktion `host_ist_online`; `create_job` nutzt sie. Das Frontend zaehlt bei den Sammelaktionen Erfolge und Ablehnungen.

**Tech Stack:** FastAPI/SQLModel, Plain-JS-Frontend, Testreihen in `tests/*.py` und `tests/frontend-test.js`, Aufruf ueber `./run-tests.sh`.

**Spec:** `docs/superpowers/specs/2026-11-25-offline-keine-sofortauftraege-design.md`

## Global Constraints

- Schwellwert bleibt `AGENT_OFFLINE_AFTER` (180 s); online heisst: freigegeben (`ApprovalState.approved`), `last_seen` gesetzt und hoechstens so alt.
- Nur `job_type` scan und patch ohne `scheduled_at` werden abgelehnt (HTTP 409). Eingeplante Auftraege und Neustart bleiben unveraendert.
- `JOB_MAX_WAIT` und `auftraege_aufraeumen` werden nicht angefasst.
- Texte zweisprachig in `frontend/i18n.js` (DE und EN); keine internen Hostnamen in Quelltext oder Kommentaren; keine Hinweise auf KI-Mitarbeit in Dateien, Commits oder PR-Text.
- Pruefung vor jedem Commit: Diff auf Secrets, Injection, fehlende Eingabepruefung.

## Review Focus

- Host nie gemeldet (`last_seen` leer): gilt als offline, Sofort-Auftrag abgelehnt, kein Absturz bei `None`.
- Scan mit `patch_after` auf Offline-Host: wird wie Scan abgelehnt.
- Scan oder Patch mit `scheduled_at` in der Zukunft auf Offline-Host: weiter erlaubt.
- Neustart auf Offline-Host: Verhalten unveraendert.
- Sammelaktion, bei der alle Hosts offline sind: Meldung nennt die Zahl der Uebersprungenen, nicht "N eingeplant".

---

### Task 1: Server lehnt Sofort-Auftraege fuer Offline-Hosts ab

**Files:**
- Modify: `backend/main.py` (`_online` im Statusbericht ~6968 herausziehen; `create_job` ab ~4669)
- Create: `tests/offline-auftraege-test.py`
- Modify: bestehende Reihen, die Sofort-Auftraege auf nie gemeldeten Hosts anlegen (`roles-test.py`, `eingaben-test.py`, `agent-api-test.py`, `heartbeat-plan-test.py`, `jobstille-test.py`, `area-test.py`, `agent-haertung-test.py`)

**Interfaces:**
- Produces: `host_ist_online(host: Host, now: datetime) -> bool` (Modulebene in `backend/main.py`), Regel wie oben; der Statusbericht ruft sie statt `_online` auf.

- [ ] **Step 1: Failing test schreiben.** `tests/offline-auftraege-test.py` im Stil von `heartbeat-plan-test.py` (eigene `check`/`call`/`host_anlegen`/`heartbeat`-Helfer). Faelle mit Assertions:
  - frisch freigegebener Host ohne Heartbeat: `POST jobs scan` und `patch` -> Status 409, Text enthaelt "offline"; in `GET /api/v1/jobs?host_id=` kein neuer Auftrag.
  - nach einem Heartbeat: dieselben Aufrufe -> 200.
  - offline mit `scheduled_at` in der Zukunft (scan und patch): 200.
  - offline `scan` mit `patch_after: true`: 409.
  - offline `reboot` (Aufruf wie in `roles-test.py`): kein 409 wegen offline.
  - Host mit `last_seen` aelter als 180 s: 409 (Alter ueber den Testhelfer herstellen, den `heartbeat-plan-test.py` fuer Zeitverschiebung nutzt; sonst direkt in der Testdatenbank setzen).
- [ ] **Step 2: Test laufen lassen:** `CO37_PYTHON=/root/co37wo/.venv/bin/python ./run-tests.sh offline-auftraege` - erwartet: FAIL (Aufrufe liefern 200).
- [ ] **Step 3: `host_ist_online(host, now)` auf Modulebene** in `backend/main.py` aus `_online` herausziehen (gleiche Regel, `ensure_utc`); Statusbericht auf die neue Funktion umstellen.
- [ ] **Step 4: Gate in `create_job`** nach der Freigabepruefung und vor dem Anlegen: gilt fuer `JobType.scan` und `JobType.patch`, wenn `payload.scheduled_at is None` und nicht `host_ist_online(host, utcnow())` -> `HTTPException(409, ...)` mit Text "Host ist offline (letzte Meldung vor <Dauer> bzw. noch nie gemeldet). Auftrag nicht angelegt." Kommentar nach Projektstil: Grund (Auftraege stauten sich bis zu 7 Tage) und warum eingeplante Auftraege ausgenommen sind.
- [ ] **Step 5: Neue Reihe gruen:** `./run-tests.sh offline-auftraege` - erwartet: alle Pruefungen ok.
- [ ] **Step 6: Gesamtlauf:** `CO37_PYTHON=/root/co37wo/.venv/bin/python ./run-tests.sh` - jede Reihe, die jetzt an der Sperre scheitert, vor dem Auftrag einen Heartbeat des Test-Agents senden lassen. Die Sperre nicht lockern, keine Umgehung im Server. Erwartet: Gesamtlauf gruen.
- [ ] **Step 7: Commit** (Diff-Pruefung auf Secrets/Injection/Eingabepruefung, Befunde nennen): `git add backend/main.py tests/offline-auftraege-test.py <geaenderte Reihen>`; Meldung ohne Pfade der Projektdoku und ohne Werkzeugnamen.

### Task 2: Oberflaeche meldet uebersprungene Hosts

**Files:**
- Modify: `frontend/app.js` (`scanAll` ~1348, `scanPatchAll` ~1372, `scanArea` ~3181, `patchArea` ~3190; Einzelaktionen `scan`/`patch` ~1263/1288)
- Modify: `frontend/i18n.js` (DE ~893, EN ~264)
- Test: `tests/frontend-test.js`, `tests/i18n-test.js`

**Interfaces:**
- Consumes: Server antwortet bei offline mit Status 409.
- Produces: i18n-Schluessel `msg.skipped_offline` mit Platzhalter `{anzahl}` (DE: "{anzahl} Hosts uebersprungen, weil offline", EN: "{anzahl} hosts skipped because they are offline", Umlaute wie in der Datei ueblich).

- [ ] **Step 1: Failing test** in `tests/frontend-test.js`: Hilfsfunktion `sammelAuftraege(hosts, bauer)` (neu in `app.js`, in `EXPORTS` aufnehmen) bekommt eine `api`-Attrappe, die fuer zwei von fuenf Hosts mit Status 409 wirft; erwartet `{angelegt: 3, offline: 2}`. Zweiter Fall: andere Fehler (500) zaehlen weder als angelegt noch als offline und fuehren zu `fehler: 1`. Dritter Fall: alle offline -> `angelegt: 0`.
- [ ] **Step 2: Test laufen lassen:** `node tests/frontend-test.js` - erwartet: FAIL (Funktion fehlt).
- [ ] **Step 3: `sammelAuftraege(hosts, bauer) -> Promise<{angelegt, offline, fehler}>`** in `frontend/app.js` implementieren; `scanAll`, `scanPatchAll`, `scanArea`, `patchArea` darauf umstellen. Toast zeigt `msg.*_many` mit `angelegt` statt `ok.length`; bei `offline > 0` zusaetzlich `msg.skipped_offline` (als Fehler-Toast, wenn `angelegt` 0). Die `api`-Funktion muss den Status am Fehler tragen; falls nicht, dort nachruesten.
- [ ] **Step 4: i18n-Schluessel in DE und EN eintragen;** `node tests/i18n-test.js` und `node tests/i18n-guard-test.js` ausfuehren - erwartet: gruen.
- [ ] **Step 5: Einzelaktionen:** `scan`/`patch` zeigen die Serverfehlermeldung (pruefen, ob `api` das schon tut; Test dafuer nur falls Code geaendert wird).
- [ ] **Step 6: Gesamtlauf** `./run-tests.sh` und `node tests/frontend-test.js` - erwartet: gruen.
- [ ] **Step 7: Commit** (Diff-Pruefung wie oben): `git add frontend/app.js frontend/i18n.js tests/frontend-test.js`.

### Task 3: Dokumentation und Abschluss

**Files:**
- Modify: Projektdoku (offene Punkte und passende Betriebsdoku), falls dort Auftragsverhalten beschrieben ist.

- [ ] **Step 1: Verifikation** (superpowers:verification-before-completion): Gesamtlauf, `tests/ki-hinweise-test.py`, `tests/hostnamen-test.py` ausfuehren; Ausgabe belegen.
- [ ] **Step 2: Code-Review** (superpowers:requesting-code-review) auf den Branch `offline-keine-sofortauftraege`.
- [ ] **Step 3: Doku nachziehen:** Verhalten "Sofort-Auftraege nur bei Online-Hosts; eingeplante bis zu 7 Tage" festhalten.
- [ ] **Step 4: Branch pushen; PR nur auf Mikes Wunsch; Version/Release macht Mike.**

## Selbstpruefung
Spec-Abdeckung: Gate (Task 1), gemeinsame Online-Definition (Task 1), Sammelmeldung (Task 2), Nicht-Ziele (Global Constraints). Typen: `host_ist_online`, `sammelAuftraege`, `msg.skipped_offline` in beiden Tasks gleich benannt.
