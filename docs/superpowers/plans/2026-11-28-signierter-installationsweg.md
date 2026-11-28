# Signierter Installationsweg für den Windows-Agenten - Plan

**Spec:** `docs/superpowers/specs/2026-11-27-signierter-installationsweg-design.md` (Stand `e17f5f1`)

**Ziel:** Ein Administrator kann über CO-37 einen Windows-Host auf die
signierte MSI des aktuellen Releases bringen. Der Agent prüft die Signatur
selbst.

**Aufbau:** Teil 0 misst die Windows-Annahmen. Erst danach wird Code
geschrieben.

- **Teil A** baut die MSI auf dem Baurechner und prüft, signiert und packt sie
  auf dem Signierrechner.
- **Teil B** lässt den Watcher die signierte MSI übernehmen statt eine eigene zu
  bauen. A und B zusammen sind allein schon ein Release wert: eine MSI je
  Version, Python mit festem Hash.
- **Teil C** bringt die Auftragsart `install`: Server, Agent und Oberfläche.

**Arbeitszweig:** `installationsweg-entwurf`. Kein PR ohne Auftrag.

## Globale Regeln

- Commits als `Michael Kuban <michael.kuban@itkub.de>`, ohne Zusatzzeilen.
  Vor jedem Commit werden drei Dinge erledigt:
  - Den Diff auf Secrets, Injection und fehlende Eingabeprüfung ansehen und die
    Befunde nennen.
  - `python tests/ki-hinweise-test.py` ausführen.
  - `python tests/hostnamen-test.py` ausführen.
- Im Repo stehen keine internen Rechnernamen. In diesem Plan heißen die Rechner
  **Signierrechner** (Windows, privater Schlüssel), **Baurechner** (Linux,
  `wixl`, dort läuft auch das Backend) und **Windows-Testhost**.
- Signatur: nur Ed25519, Formatkennung `v1`, Verfahrenskennzeichen `ed25519`.
  Es wird genau eine Signatur akzeptiert.
- `MAX_ZIP_BYTES` bleibt bei 50 MB.
- Nur Windows. `.deb` und `.rpm` bleiben unverändert.
- `msiexec`-Argumente stehen fest: `/qn`, `REBOOT=ReallySuppress`,
  Protokolldatei, kein `CO37SERVER`. Kein Wert aus Server- oder Auftragsdaten
  geht in einen Befehl oder Pfad ein.
- Jede neue Prüfung wird durch eine benannte Mutation belegt (rot unter der
  Mutation, grün ohne sie).

## Entscheidungen, die der Plan trifft (über den Entwurf hinaus)

1. **Vergleich gegen die installierte Paketversion.** Der Vergleich läuft gegen
   `laufzeit.json` → `"version"` im Installationsverzeichnis, nicht gegen
   `own_version()`. Grund: Nach `selfupdate` trägt `agent.py` schon die neue
   Nummer. Ohne diese Regel liefe `/fa` gegen ein Paket mit anderer
   ProductVersion. Ein Host ohne `laufzeit.json` hat eine unbekannte Version,
   das gilt als „niedriger“ und führt zu `/i`.
2. **Zwei Abrufe statt einer Route mit allem.** Zuerst holt der Agent
   `GET /api/v1/agent/install-package` (JSON mit Erklärung und Signatur), dann
   `GET /api/v1/agent/install-package/msi` (Datei). Die 18 MB werden erst
   geladen, wenn die Signatur der Erklärung stimmt.
3. **Format der Erklärung**, exakt so. Kodierung UTF-8, Zeilenende LF, mit
   abschließendem LF, ohne CR, höchstens 4096 Byte:
   ```
   format=co37-install-v1
   datei=co37-agent-<version>.msi
   version=<version>
   sha256=<64 Kleinbuchstaben-Hex>
   python=<x.y.z>
   paket=<normname>==<fassung>
   ```
   Je Bibliothek steht eine `paket=`-Zeile, sortiert nach Name. Die Reihenfolge
   der Schlüssel ist fest. Unbekannte, doppelte oder fehlende Schlüssel führen
   zur Ablehnung. `datei` muss gleich `co37-agent-{version}.msi` sein. Muster:
   - `version` und `python`: `^\d+\.\d+\.\d+$`
   - `paket`: `^[a-z0-9][a-z0-9._-]*==[0-9A-Za-z.+!-]+$`
4. **Format der Signaturdatei:** genau eine Zeile `v1 ed25519 <base64url>` mit
   LF. Signiert werden die Bytes der Erklärung, nicht ihr SHA-256.
5. **Ablage beim Release:**
   - im ZIP: `agent-pakete/co37-agent-<v>.msi`, `.erklaerung`,
     `.erklaerung.sig`
   - auf dem Server unter `state/packages/` mit denselben Namen
6. **Ablage auf dem Host:** `C:\Program Files\CO37\installation\`. Dort liegen
   `co37-agent.msi` (fester Name, vorher `co37-agent.msi.tmp`), `auftrag.json`
   (Markierung) und `msiexec.log`.
7. **Einmalige Aufgabe:** Name `CO37Installation`, angelegt mit `schtasks`
   (`/SC ONCE /ST 00:00 /RU SYSTEM /RL HIGHEST /F`), gestartet mit
   `schtasks /Run`. Kein `/SD`, weil das Datumsformat von der Sprache abhängt.
8. **Ab welcher Agent-Version `install` angenommen wird:**
   `INSTALL_MIN_AGENT = "0.52.0"`. Das ist das Release, das Teil C enthält.
   Ändert sich die Nummer beim Stempeln, wird die Konstante im selben Commit
   angepasst. Eine Prüfung verlangt `INSTALL_MIN_AGENT <= AGENT_VERSION`.
9. **Frist:** `JOB_MAX_RUNTIME[JobType.install] = 30 Minuten`.
   **Abrufgrenze:** MSI höchstens 40 MB, Zeitgrenze 300 s. Begründung: Die MSI
   misst 18 MB, die Grenze lässt Platz für eine größere Laufzeit und bleibt
   unter dem ZIP-Limit.
10. **`build_msi.py` installiert mit `--no-compile`.** Damit enthält die MSI
   keine `.pyc` und ist auf dem Signierrechner nachbaubar. Die `.pyc` entstehen
   zur Laufzeit; `laufzeit.json` lässt `__pycache__` schon heute aus.

## Prüffokus (was keine Aufgabe von selbst trifft)

1. **Der alte Agent kommt im Fünfminutentakt zurück, während `msiexec` noch
   läuft.** Er liest die Markierung und darf den Auftrag nicht vorzeitig als
   fehlgeschlagen melden, solange `CO37Installation` läuft. Abgedeckt in C4.
2. **`selfupdate` vor `install`.** Die Versionsnummern sind gleich, die
   Paketversion ist älter: Der Agent muss `/i` wählen, nicht `/fa` oder
   „bereits aktuell“. Abgedeckt in C3.
3. **Eine ältere Erklärung ist echt signiert,** zum Beispiel weil ein Server
   eine alte MSI ausliefert. Abzulehnen, wenn ihre Version unter der
   installierten Paketversion liegt. Abgedeckt in C3.
4. **Das Zielverzeichnis gibt es schon** und es gehört einem fremden Besitzer
   oder hat fremdes Schreibrecht, oder `co37-agent.msi` ist eine Verknüpfung.
   Abzulehnen, nichts wird geschrieben. Abgedeckt in C4.
5. **Ein ZIP ohne `agent-pakete/`** (Release wie bisher) muss sich exakt wie
   heute verhalten: Der Watcher baut die MSI, der Soll-Stand kommt aus
   `build_msi.py`, die Route liefert 404 „kein Paket im Release“. Abgedeckt in
   B1 und C1.

## Dateien

| Datei | Änderung |
|---|---|
| `backend/installpaket.py` | **neu**: `schreibe_erklaerung()`, `lies_erklaerung()`, `pruefe_signatur()` (Server- und Bauseite) |
| `tools/sign-release.py` | neuer Modus `--erklaerung <datei>` |
| `tools/msi-inhalt.py` | **neu**: Auszug und Inhaltsabgleich auf dem Signierrechner |
| `packaging/build_msi.py` | `PY_SHA256`, `nutzlast_bauen()` herausgelöst, `--nur-nutzlast`, `--no-compile` |
| `build_release.py` | `--nur-stempeln`, `--msi <datei>`, `agent-pakete/` packen |
| `update_watcher.py`, `build_packages.sh` | signierte MSI übernehmen, dann `--deb-only` |
| `backend/models.py`, `backend/scheduling.py` | `JobType.install`, Frist |
| `backend/main.py` | `paket_soll()` aus der Erklärung, zwei Routen, Auftragsanlage, Sammelaktion mit Pilot, `list_packages` |
| `agent/agent.py` | Auftrag `install`, `installation_abschliessen()` beim Start |
| `frontend/app.js`, `frontend/i18n.js` | Knopf je Host, Sammelaktion |
| Tests | `tests/installationsweg-test.py` (**neu**, Reihe `installationsweg` in `run-tests.sh`), Ergänzungen in `watcher-sig`, `release-sig`, `roles`, `rollout`, `frontend` |

---

## Teil 0: Messungen (vor jedem Code)

Die Ergebnisse kommen in den Plan zurück (Abschnitt „Messergebnisse“ am Ende).
Wenn M2 oder M3 anders ausfallen als erwartet, wird Teil C angehalten und der
Entwurf geändert.

- [ ] **M0 `PY_SHA256`.** Den SHA-256 von `python-3.14.10-embed-amd64.zip`
  aus zwei unabhängigen Quellen ermitteln: frisch geladen auf dem
  Signierrechner und aus `packaging/cache` auf dem Baurechner. Beide Werte
  müssen gleich sein. Zusätzlich mit der Angabe auf der Release-Seite von
  python.org vergleichen, falls sie einen SHA-256 nennt.
- [ ] **M1 Auszug auf dem Signierrechner.** Die MSI von 0.51.7 ohne
  Administratorrechte auszugsweise entpacken:
  `msiexec /a <msi> /qn TARGETDIR=<leer>`. Festhalten:
  - ob das ohne Administratorrechte geht;
  - den Dateibaum;
  - die Anzahl der `__pycache__`-Einträge;
  - ob es die Dateien `RECORD`, `INSTALLER` und `REQUESTED` unter
    `*.dist-info` gibt.

  Scheitert `/a`, fällt Teil A auf das Lesen der Tabellen mit `msiinfo`
  zurück. Dann läuft die Prüfung auf dem Baurechner, und der Entwurf muss
  geändert werden.
- [ ] **M2 Windows-Testhost, Rechte des Zielordners.** Als SYSTEM
  `C:\Program Files\CO37\installation` anlegen (über eine einmalige Aufgabe,
  wie es der Agent täte). Mit `icacls` Besitzer und Rechte festhalten. Eine
  Probedatei hineinlegen und danach `/fa` und `/i` auf 0.51.7 ausführen.
  Erwartet: Der Ordner ist nur für SYSTEM und Administratoren beschreibbar,
  und die Probedatei übersteht beide Läufe.
- [ ] **M3 Windows-Testhost, einmalige Aufgabe.** Als SYSTEM
  `CO37Installation` mit `/SC ONCE /ST 00:00` und der festen
  `msiexec /fa`-Befehlszeile anlegen. Den anlegenden Prozess beenden, dann
  `schtasks /Run` ausführen. Festhalten:
  - ob schtasks wegen der vergangenen Startzeit warnt oder abbricht;
  - ob `msiexec` als SYSTEM läuft;
  - den Status während und nach dem Lauf (`schtasks /Query /TN
    CO37Installation /FO CSV /V`);
  - dass `CO37Agent` danach noch da ist;
  - die Länge der `/TR`-Zeile (die Grenze liegt bei 261 Zeichen).

## Teil A: Release-Bau

### A1: Erklärung und Signatur (`backend/installpaket.py`, `tools/sign-release.py`)

**Ergebnis für spätere Aufgaben:**
- `schreibe_erklaerung(datei: str, version: str, sha256: str, python: str, pakete: dict[str, str]) -> bytes`
- `lies_erklaerung(roh: bytes) -> dict` liefert die Schlüssel `datei`,
  `version`, `sha256`, `python` und `pakete` (`dict`). Bei jeder
  Formatverletzung wirft sie `ValueError` mit Text.
- `pruefe_signatur(roh: bytes, sig_zeile: str, pub: Ed25519PublicKey) -> None`
  wirft bei Ablehnung `ValueError`.
- `sign-release.py --erklaerung <datei>` schreibt `<datei>.sig` im Format v1.

- [ ] Die Reihe `tests/installationsweg-test.py` anlegen und in
  `run-tests.sh` eintragen (`installationsweg`). Teil 1 prüft:
  - Rundlauf: `schreibe_erklaerung` → `lies_erklaerung` ergibt dieselben
    Werte, die Ausgabe endet auf `\n` und enthält kein `\r`.
  - Eine Ablehnung (`ValueError`) bei jedem dieser Fälle: CR in der Datei,
    fehlendes abschließendes LF, unbekannter Schlüssel, doppelter Schlüssel,
    falsche Schlüsselreihenfolge, `datei` passt nicht zu `version`,
    `sha256` mit Großbuchstaben oder 63 Stellen, `version` als `0.52`,
    `paket` ohne `==`, mehr als 4096 Byte, `format=co37-install-v2`.
  - Signatur mit einem eigenen Schlüsselpaar im Temp-Verzeichnis (Muster
    `release-sig-test.py`): echt wird angenommen. Abgelehnt werden: ein Byte
    der Erklärung geändert, Präfix `v2`, Verfahren `ecdsa`, zwei Signaturen
    in einer Zeile, leere Zeile, beschädigtes base64.
  - Die Erklärung von `sign-release.py --erklaerung` wird von
    `pruefe_signatur` angenommen.
- [ ] Ausführen. Erwartet: rot (`installpaket` fehlt).
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - Musterprüfung von `sha256` entfernen,
  - Längenprüfung entfernen,
  - Verfahrenskennzeichen nicht prüfen.
- [ ] Commit.

### A2: Nutzlast nachbaubar machen (`packaging/build_msi.py`)

**Ergebnis für spätere Aufgaben:**
- `PY_SHA256: str` (der Wert aus M0)
- `nutzlast_bauen(work: Path) -> dict[str, str]`: entpackt die geprüfte
  Python-Datei, setzt `._pth` und installiert die Bibliotheken mit
  `--no-compile`. Rückgabe sind die installierten Fassungen.
- `build_msi.py --nur-nutzlast <verz>` (läuft auch unter Windows, ohne `wixl`)

- [ ] Testfälle in `installationsweg-test.py` ergänzen:
  - `fetch_python()` bricht bei einer Datei mit falschem Hash mit
    `SystemExit` ab und löscht die Datei. Geprüft mit einer gefälschten Datei
    im Cache-Ordner, ohne Netz.
  - `PY_SHA256` passt auf `^[0-9a-f]{64}$`.
  - Der pip-Aufruf in `nutzlast_bauen` enthält `--no-compile` und
    `--require-hashes`. Geprüft wird die Argumentliste über einen
    Ersatz-`subprocess.run`, nicht der Quelltext.
  - `build()` ruft `nutzlast_bauen()` auf: Ein Ersatz, der eine Marke
    schreibt, wird getroffen.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen. `PY_SHA256` neben `PY_VERSION`; die Prüfung steht in
  `fetch_python()`, auch für eine Datei aus dem Cache.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - die Hashprüfung nur nach dem Laden, nicht für den Cache,
  - `--no-compile` entfernt.
- [ ] `./run-tests.sh paketmanager` und `./run-tests.sh deps` bleiben grün.
- [ ] Commit.

### A3: Inhalt der MSI prüfen (`tools/msi-inhalt.py`)

**Ergebnis für spätere Aufgaben:**
- `vergleiche(auszug: Path, referenz: Path, agent_py: Path, release_key: Path) -> list[str]`
  liefert die Abweichungen; eine leere Liste heißt „gleich“.
- `pruefe_msi(msi: Path, agent_py: Path, release_key: Path) -> list[str]`
  baut den Auszug mit `msiexec /a` in ein frisches Temp-Verzeichnis. Die
  Referenz entsteht über `build_msi.py --nur-nutzlast`, plus
  `TASK_SCRIPT`/`UNINSTALL_SCRIPT` und ein neu berechnetes `laufzeit.json`.
  Dann ruft sie `vergleiche()` auf.

- [ ] Testfälle (`vergleiche()` mit zwei gebauten Bäumen im Temp-Verzeichnis,
  ohne Windows):
  - Gleiche Bäume ergeben `[]`.
  - Eine Datei nur im Auszug ergibt „fremde Datei: <pfad>“.
  - Eine Datei nur in der Referenz ergibt „fehlt: <pfad>“.
  - Ein geändertes Byte in `site-packages/certifi/cacert.pem` ergibt
    „abweichend: …“.
  - Ein abweichendes `agent.py` oder `release_key.pub` ergibt jeweils eine
    Meldung.
  - Ein abweichendes `laufzeit.json` ergibt eine Meldung.
  - Groß- und Kleinschreibung der Pfade wird gleich behandelt (`Lib` gegen
    `lib`).
  - Die Ausnahmen aus M1 (falls nötig, zum Beispiel `RECORD`) stehen als
    feste Liste `ERLAUBT_ABWEICHEND`. Eine Datei außerhalb der Liste ergibt
    weiter eine Meldung.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - fremde Dateien nicht melden,
  - Vergleich nur über die Größe,
  - `ERLAUBT_ABWEICHEND` als Präfix statt als exakter Name.
- [ ] Commit.

### A4: `build_release.py` zweigeteilt

**Verwendet:** A1 und A3.
**Ergebnis für spätere Aufgaben:** Das ZIP enthält `agent-pakete/` mit drei
Dateien, wenn `--msi` gesetzt ist.

- [ ] Testfälle (Muster `release-github-test.py`, ohne echten Schlüssel,
  Signieren über ein Testschlüsselpaar):
  - `--nur-stempeln 9.9.9` setzt die vier Versionsstempel und legt weder ZIP
    noch Signatur an.
  - `--msi` mit einer MSI, deren Version nicht `AGENT_VERSION` ist, bricht ab.
  - `--msi`, wenn `pruefe_msi()` eine Abweichung meldet, bricht vor dem
    Signieren ab; es entsteht kein ZIP und keine `.sig`.
  - Ein erfolgreicher Lauf ergibt ein ZIP mit
    `agent-pakete/co37-agent-9.9.9.msi`, `.erklaerung` und `.erklaerung.sig`.
    Die Erklärung enthält den SHA-256 der MSI und die Pins aus
    `agent/requirements.txt`, und die Signatur wird von `pruefe_signatur`
    angenommen.
  - Ohne `--msi` sieht das ZIP aus wie heute (kein `agent-pakete/`).
  - Das ZIP bleibt unter `MAX_ZIP_BYTES` (mit einer 18-MB-Attrappe).
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen. Die Passphrase- und Geheimnisprüfung bleibt vor der ersten
  Schreiboperation (Regel aus `main()`). `--msi` liest die Version aus der
  MSI über `msiinfo` oder die Property-Tabelle des Auszugs aus A3, je nach
  M1.
- [ ] Ausführen. Erwartet: grün. Mutation belegen: `pruefe_msi()` nicht
  aufrufen.
- [ ] Commit.

## Teil B: Server übernimmt die signierte MSI

### B1: Watcher und Soll-Stand

**Verwendet:** `lies_erklaerung`, `pruefe_signatur` (A1).
**Ergebnis für spätere Aufgaben:** `signiertes_paket(pkg_dir: Path, version: str) -> Optional[dict]`
in `backend/main.py` liefert die gelesene Erklärung oder `None`. Die
Signatur wird geprüft, wenn `release_key.pub` vorliegt.

- [ ] Testfälle (`watcher-sig-test.py` und `agent-stand-test.py` ergänzen):
  - Ein ZIP mit `agent-pakete/` führt dazu, dass der Watcher die drei Dateien
    nach `state/packages/` kopiert und `build_packages.sh --deb-only` aufruft.
    Geprüft über einen Ersatz für `run`. Es kommt keine
    `pkg.log.no_msi_*`-Meldung.
  - Passt der SHA-256 der MSI nicht zur Erklärung, wird nichts kopiert. Der
    Watcher meldet einen Fehler und baut wie heute selbst.
  - Ein ZIP ohne `agent-pakete/` verhält sich wie heute (Prüffokus 5).
  - `paket_soll()` liefert bei vorhandener Erklärung deren `python` und
    `pakete`, sonst wie heute den Wert aus `build_msi.py`.
  - `list_packages()` führt die `.erklaerung`- und `.sig`-Dateien nicht als
    Pakete auf.
  - Verknüpfung als Zieldatei in `state/packages/`: wird nicht verfolgt
    (Muster F-29).
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - SHA-Abgleich im Watcher entfernt,
  - `paket_soll()` ignoriert die Erklärung.
- [ ] Gesamtlauf `./run-tests.sh`. Erwartet: grün.
- [ ] Commit.

### B2: Release-Ablauf und Bau auf dem Baurechner

- [ ] Die Schrittfolge in `GITHUB.md` (öffentlich) und im Projektdokument
  `release-ablauf.md` festhalten, ohne Rechnernamen im Repo:
  1. `build_release.py --nur-stempeln <v>`
  2. commit und push
  3. auf dem Baurechner frischer Checkout des Stempel-Commits in ein leeres
     Verzeichnis, `packaging/build_msi.py --out <verz>`
  4. MSI kopieren, SHA-256 auf beiden Seiten vergleichen
  5. `build_release.py --msi <datei>`
  6. hochladen und Digest abgleichen
  7. taggen
- [ ] Probelauf mit einer Testversion auf einem Wegwerfzweig, ohne Tag und
  ohne Hochladen: Das ZIP enthält `agent-pakete/`, und
  `tools/msi-inhalt.py` meldet `[]`.
- [ ] Commit.

**Hier ist ein eigenes Release möglich (A und B).**

## Teil C: Auftragsart `install`

Teil C beginnt erst nach M2 und M3.

### C1: Backend, Auftragsart und Routen

**Ergebnis für spätere Aufgaben:**
- `JobType.install = "install"`
- `JOB_MAX_RUNTIME[JobType.install]`
- `INSTALL_MIN_AGENT`
- `GET /api/v1/agent/install-package` antwortet `{"erklaerung": str, "signatur": str}`
- `GET /api/v1/agent/install-package/msi`

- [ ] Testfälle (`installationsweg-test.py`, Teil 2 mit Backend, Muster
  `offline-auftraege-test.py`, höchstens ein eigener Host):
  - Ein Administrator legt `install` für einen Windows-Host an, der online ist
    und eine passende Agent-Version hat: 200, ein Eintrag im Prüfprotokoll.
  - Ablehnungen:
    - Nicht-Administrator: 403.
    - Host offline: 409.
    - Linux-Host: 422.
    - Agent-Version unter `INSTALL_MIN_AGENT`: 409.
    - Kein signiertes Paket: 409 „kein Paket im Release“.
  - Jede Ablehnung steht im Prüfprotokoll.
  - Ein zweites `install`, während eins offen ist, wird nicht angelegt.
  - Die Route `install-package`:
    - ohne Token: 401;
    - nicht freigegebener Host: 403;
    - Linux-Host: 403;
    - ohne Paket: 404 mit Text;
    - mit Paket: die Erklärung byte-gleich zur Datei.
  - `/msi` liefert die Datei byte-gleich.
  - `INSTALL_MIN_AGENT <= AGENT_VERSION`.
  - Der Schema-Abgleich (`./run-tests.sh schema-drift`) zeigt keine
    Abweichung. Zeigt er eine, kommt die Migration in diese Aufgabe.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen. Die Prüfung auf `install` steht in `create_job` neben der
  Prüfung auf `selfupdate`. Die Offline-Sperre wird auf `install` erweitert.
  In `roles-test.py` kommt `install` in die abgezählten Wege dazu.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - Offline-Sperre ohne `install`,
  - Prüfung auf Windows entfernt,
  - Versionsschranke entfernt.
- [ ] Commit.

### C2: Sammelaktion mit Pilot

**Verwendet:** C1 und `PILOT_BESTAETIGUNG_MINUTEN`.
**Ergebnis für spätere Aufgaben:**
- `POST /api/v1/install-rollout {"host_ids": [int]}` (nur Administratoren)
- `_install_roll_weiter(session, host) -> None`, aufgerufen im Heartbeat
- Einstellung `install_roll` (JSON: `version`, `pilot`, `offen`, `pilot_seit`)

- [ ] Testfälle (`rollout-test.py` ergänzen, Zeit vorspulen):
  - Die Sammelaktion legt nur für den Piloten einen Auftrag an. Pilot ist
    `agent_pilot_host`, wenn er in der Auswahl ist, sonst der erste
    berechtigte Host.
  - Die übrigen bekommen erst einen Auftrag, wenn der Pilot
    `PILOT_BESTAETIGUNG_MINUTEN` lang `bundled_state == "gleich"` meldet. Bei
    14 Minuten kommt keiner.
  - Ist der Auftrag des Piloten `failed`, bekommt niemand sonst einen Auftrag.
    Die Einstellung bleibt sichtbar.
  - Ein Host aus `offen`, der offline ist, bekommt seinen Auftrag erst bei
    seinem nächsten Heartbeat.
  - Eine neue Paketversion verwirft die alte `install_roll`.
  - Eine leere Liste ergibt 422 mit Text.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - Freigabe beim ersten passenden Heartbeat (A6-Fehler),
  - Fortsetzung trotz gescheitertem Piloten.
- [ ] Commit.

### C3: Agent, Abruf, Prüfung und Art bestimmen

**Verwendet:** die Routen aus C1 und das Format aus A1. Die Logik wird für den
Agenten nachgebaut, weil `agent.py` eigenständig ist.
**Ergebnis für spätere Aufgaben:**
- `lies_erklaerung_agent(roh: bytes) -> dict` (gleiches Verhalten wie A1)
- `installierte_paketversion() -> Optional[str]` (aus `laufzeit.json`)
- `install_art(erklaerung: dict, installiert: Optional[str], stand: Optional[dict], laufzeit: Optional[dict]) -> str`
  liefert `"upgrade"`, `"reparatur"` oder `"aktuell"`, oder wirft
  `ValueError("niedriger")`.
- `install_holen() -> tuple[dict, bytes]` wirft `ValueError` mit Grund.

- [ ] Testfälle:
  - **Gleichlauf der beiden Parser:** Alle Fälle aus A1 laufen durch
    `lies_erklaerung_agent`, und das Ergebnis oder die Ablehnung ist gleich.
  - **Agent ohne `release_key.pub`:** Ablehnung, auch wenn die Signatur fehlt.
  - Signatur falsch, MSI mit einem geänderten Byte, MSI über 40 MB:
    Ablehnung. Bei falscher Signatur wird `/msi` gar nicht abgerufen.
  - **`install_art`:**
    - Erklärung 0.52.0, `laufzeit.json` 0.51.7, `own_version()` 0.52.0:
      `"upgrade"` (Prüffokus 2).
    - Gleiche Version und gleicher Stand: `"aktuell"`.
    - Gleiche Version, eine Bibliothek anders: `"reparatur"`.
    - Gleiche Version, Laufzeit unvollständig: `"reparatur"`.
    - Erklärung 0.51.6 gegen installiert 0.51.7: `ValueError` (Prüffokus 3).
    - `laufzeit.json` fehlt: `"upgrade"`.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - Vergleich gegen `own_version()`,
  - MSI-Abruf vor der Signaturprüfung,
  - fehlender Schlüssel lässt durch.
- [ ] Commit.

### C4: Agent, Ablage, einmalige Aufgabe und Abschluss

**Verwendet:** C3.
**Ergebnis für spätere Aufgaben:**
- `ZIEL_INSTALL = Path(r"C:\Program Files\CO37\installation")`
- `ziel_sicher(pfad: Path) -> tuple[bool, str]` prüft Besitzer und
  Schreibrechte über `icacls` und lehnt Verknüpfungen ab.
- `msiexec_befehl(art: str) -> str` liefert die feste `/TR`-Zeile.
- `installation_starten(job_id: int, art: str, msi: bytes, erklaerung: dict) -> None`
- `installation_abschliessen() -> Optional[dict]` steht in `main()` vor dem
  ersten Heartbeat und liefert den Bericht oder `None`.

- [ ] Testfälle (Windows-Aufrufe über Ersatzfunktionen, Ausgabe nach M2 und
  M3):
  - `msiexec_befehl("upgrade")` und `msiexec_befehl("reparatur")` sind exakt
    die festen Zeilen: `/i` bzw. `/fa`, Pfad in Anführungszeichen, `/qn`,
    `REBOOT=ReallySuppress`, `/L*v` auf `msiexec.log`, kein `CO37SERVER`.
    Die Länge liegt unter 261 Zeichen. Keine Abhängigkeit von Server- oder
    Auftragsdaten: Ein Aufruf mit einer anderen `job_id` und einer anderen
    Erklärung ergibt dieselbe Zeile.
  - `ziel_sicher` mit einer `icacls`-Ausgabe aus M2: angenommen. Ein fremder
    Besitzer, `BUILTIN\Users:(W)` oder `(M)` und eine Verknüpfung werden
    abgelehnt (Prüffokus 4). Bei Ablehnung wird keine Datei geschrieben.
  - `installation_starten` schreibt zuerst `.tmp` und benennt danach um. Es
    schreibt `auftrag.json` mit `job_id` und Soll-Stand, ruft `schtasks
    /Create` und `/Run` mit der festen Zeile auf und beendet den Agenten (der
    Auftrag bleibt `running`).
  - `installation_abschliessen`:
    - Stand passt: Bericht `done`; Aufgabe, MSI und Markierung sind entfernt,
      `msiexec.log` bleibt.
    - Stand passt nicht: Bericht `failed` mit dem Ende von `msiexec.log`.
    - `CO37Installation` läuft noch: kein Bericht, nichts entfernt
      (Prüffokus 1).
    - Keine Markierung: `None`.
    - Kaputtes `auftrag.json`: `failed` mit Grund, Markierung entfernt.
  - Zwischen den Durchläufen wird die Frist aus `JOB_MAX_RUNTIME` nicht
    durch die Stilleregel (`JOB_LOG_SILENCE`) vorzeitig beendet. Das prüft der
    Backend-Teil über die Zeit, die die Reihe vorspult.
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen. In `handle_job` kommt der Zweig `install`: C3 ausführen; bei
  `"aktuell"` wird `done` mit „bereits aktuell“ gemeldet, sonst
  `installation_starten`.
- [ ] Ausführen. Erwartet: grün. Mutationen belegen:
  - Umbenennen vor der Prüfung,
  - Abschluss trotz laufender Aufgabe,
  - `ziel_sicher` nicht aufgerufen.
- [ ] Gesamtlauf `./run-tests.sh`. Erwartet: grün.
- [ ] Commit.

### C5: Oberfläche

Zuerst kommt der zurückgestellte kleine Frontend-PR, als eigener Commit oder
eigener PR nach Mikes Wahl:
- ein Test für `sammelMeldung()`;
- alle vier Sammelaktionen geben `ohneToast409` mit;
- eine Meldung bei leerer Hostliste.

- [ ] Testfälle (`frontend-test.js`):
  - Der Knopf „Paket installieren“ steht nur bei Administratoren und nur an
    Windows-Hosts mit `bundled_state === "abweichend"` oder unvollständiger
    Laufzeit.
  - Ein Klick ruft `POST /api/v1/jobs` mit `job_type: "install"` auf.
  - Die Sammelaktion ruft `install-rollout` auf.
  - Eine 409 wird als Text angezeigt.
  - Eine leere Auswahl wird gemeldet, ohne dass eine Anfrage abgeht.
  - Alle Texte stehen in `i18n.js` (der Wächter `i18n-guard` bleibt grün).
- [ ] Ausführen. Erwartet: rot.
- [ ] Umsetzen.
- [ ] Ausführen. Erwartet: grün. Mutation belegen: Knopf ohne Prüfung auf
  Administratorrechte.
- [ ] Commit.

### C6: Messung auf dem Windows-Testhost (Bedingung für ein Release mit Teil C)

Mit einem Testrelease aus diesem Zweig, auf einem Testserver oder mit einem
eigenen Bereich:

- [ ] Der Upgrade-Auftrag von 0.51.7 auf die Testversion läuft durch. Der neue
  Agent meldet den Soll-Stand, der Auftrag ist `done`, und es gibt
  `CO37Agent`, aber nicht mehr `CO37Installation`.
- [ ] `certifi\cacert.pem` löschen, dann `install` auslösen: `"reparatur"`,
  die Datei ist wieder da.
- [ ] Ein zweiter `install` bei stimmendem Stand ergibt „bereits aktuell“,
  ohne `msiexec`.
- [ ] Eine absichtlich kaputte MSI (signiert mit einem Testschlüssel auf dem
  Testhost) schlägt fehl, der alte Agent läuft weiter und meldet `failed` mit
  dem Protokoll.
- [ ] Die Rechte von `C:\Program Files\CO37\installation` sind nach dem
  Upgrade unverändert.
- [ ] Ergebnisse in diesen Plan und ins Projektdokument
  `agent-msi-ausrollen.md`.

## Abschluss

- [ ] superpowers:requesting-code-review über den ganzen Zweig.
- [ ] superpowers:verification-before-completion: Gesamtlauf, CI-Lauf
  (`workflow_dispatch`), `ki-hinweise`, `hostnamen`.
- [ ] Die Abweichung von der TR-02102-1 (Ed25519 allein, Frist 2035) in der
  öffentlichen Sicherheitsbeschreibung (README, Abschnitt Signaturen) offen
  nennen, wie im Entwurf festgelegt.
- [ ] Projektdokumente nachführen: `offene-punkte.md` (Vorhaben erledigt oder
  Stand), `komponenten-und-fassungen.md` (B-2026-11-17-01),
  `release-ablauf.md`.
- [ ] PR nur auf Mikes Auftrag, Merge nur auf „merge“, Tags setzt Mike.

## Messergebnisse

(wird aus Teil 0 und C6 gefüllt)
