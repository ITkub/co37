# Signierter Installationsweg für den Windows-Agenten — Entwurf

Stand 2026-11-28. Gehört zu B-2026-11-17-01 (`komponenten-und-fassungen`).
Status: Entwurf, zur Freigabe vorgelegt.

## Anlass und Ziel

Die Selbstaktualisierung (`self_update()`) ersetzt nur `agent.py`. Die
eingebettete Python-Laufzeit und die acht gepinnten Bibliotheken stecken im
MSI. Ein Sicherheitsrelease, das nur Laufzeit oder Bibliotheken anhebt,
erreicht deshalb keinen bereits angemeldeten Windows-Host; bisher heißt das:
jeden Host von Hand oder per GPO neu bespielen (bei 0.51.5 so geschehen).

Ziel: CO-37 kann ein Agent-Paket auf einen bestehenden Windows-Host bringen
**und hält dabei die Eigenschaft ein, die CO-37 sonst überall hat: ausführbarer
Inhalt kommt nur mit der Signatur des Herausgebers auf einen Host.** Ein
Installer, den ein SYSTEM-Prozess ausführt, ist beliebiger Code; ohne
Signatur gäbe CO-37 diese Eigenschaft auf.

## Ziele

- Ein Administrator kann Windows-Hosts, deren Laufzeit oder Bibliotheken vom
  aktuellen Paket abweichen, über CO-37 aktualisieren oder reparieren lassen,
  ohne den Host anzufassen.
- Der **Agent prüft die Signatur selbst**, mit dem bei ihm liegenden
  `release_key.pub`. Ein kompromittierter Server kann dem Host kein Paket
  unterschieben.
- Die MSI wird vom **Herausgeber** gebaut und signiert, nicht beim Kunden.
  Je Version gibt es **genau eine** MSI.
- Alles kommt mit dem normalen Release: ein `.zip` und ein `.zip.sig` auf
  GitHub, wie bisher.

## Nicht-Ziele

- Linux-Pakete (`.deb`, `.rpm`): Laufzeit und Bibliotheken kommen dort aus der
  Distribution und über den normalen Patchlauf.
- Selbsttätiges Auslösen. Der erste Wurf wird von einem Administrator
  angestoßen (Knopf je Host und Sammelaktion), nicht vom Server von selbst.
- Automatische Rücknahme auf die alte Fassung (siehe „Rückweg").
- Authenticode, Hardware-Schlüsselspeicher, Post-Quanten-Verfahren.
- Der bestehende Weg `selfupdate` für `agent.py` bleibt unverändert.

## Anti-Ziele (das wäre ein Fehlschlag, auch wenn es läuft)

- Irgendein Weg, auf dem allein der Server entscheidet, was als SYSTEM läuft.
- Ein Installer, dessen Eingabe vor dem Start nicht geprüft wurde.
- Zusätzlicher, ungeprüfter Code, der als SYSTEM läuft.
- Das manuelle Einspielen der MSI (Doppelklick, GPO, `msiexec`) wird
  schlechter oder anders.

## Randbedingungen aus dem Bestand

- Der Agent spricht nur mit seinem Server (Token). Er holt das Paket **vom
  Server**, nie von GitHub.
- Der Server holt Releases über `github_update.py` (ZIP und `.sig`) und prüft
  die ZIP-Signatur selbst. Grenze `MAX_ZIP_BYTES` = 50 MB.
- Heute entsteht die MSI beim Kunden aus dem ZIP (`wixl`, über
  `build_packages.sh`), die Python-Laufzeit wird von python.org **ohne festen
  Hash** geladen. Beides entfällt, sobald das ZIP eine signierte MSI enthält.
- `wixl` läuft nur unter Linux; `build_release.py` läuft beim Herausgeber
  unter Windows und signiert mit dem passphrasegeschützten Schlüssel.
- Der Agent läuft als SYSTEM über die geplante Aufgabe `CO37Agent`. Die MSI
  beendet beim Upgrade laufende Prozesse über den Restart Manager, legt die
  Aufgabe neu an und startet sie am Ende (`agent-msi-ausrollen`). Ein
  `msiexec` als Kindprozess des Agenten stürbe mit ihm.
- Die MSI ist für alle Kunden gleich; Server und Token stammen aus der
  vorhandenen `agent.conf` (wird beim Upgrade übernommen).
- `msiexec /i` mit derselben, bereits installierten Fassung installiert nicht
  neu; eine Reparatur ist `msiexec /fa`.
- Der Agent kennt vier Auftragsarten (`scan`, `patch`, `reboot`,
  `selfupdate`); der Server legt Sofort-Aufträge für Offline-Hosts seit 0.51.6
  nicht mehr an.
- Jedes Release setzt `AGENT_VERSION` auf die Release-Version
  (`build_release.py`, `setze_versionen`). Auch ein Release, das nur Laufzeit
  oder Bibliotheken anhebt, hat damit eine neue Agent-Version.
- Die MSI misst 18 MB (Python 3.14.10, acht Bibliotheken). Das ZIP bleibt mit
  rund 19 MB unter `MAX_ZIP_BYTES`; die Grenze wird nicht angehoben.

## Ansätze

**A. Vom Herausgeber gebaute und signierte MSI im Release-ZIP.** Der Agent
holt sie vom Server, prüft die Signatur selbst und lässt `msiexec` losgelöst
von sich laufen. Gewählt.

**B. Signiertes Laufzeitpaket ohne MSI.** Der Agent entpackt Python und
Bibliotheken selbst. Umgeht `msiexec`, baut aber einen eigenen Installer
nach (Dateien in Benutzung, DLL-Tausch) und lässt den getesteten MSI-Weg
liegen. Verworfen.

**C. MSI weiter beim Kunden bauen, Server signiert.** Der Server bürgt für ein
Paket, das er selbst gebaut hat, aus einer Laufzeit ohne festen Hash. Schwächste
Variante nach dem Gedanken des BSI (Echtheit vom Herausgeber). Verworfen.

Zur Signatur der MSI **im ZIP** und nicht als zusätzliche Release-Datei:
Die ZIP-Signatur prüft der Server; der Agent sieht das ZIP nie. Er braucht
zu seiner Datei einen eigenen Beweis. Das ist dasselbe Muster wie
`agent.py` + `agent.py.sig` im ZIP.

## Entscheidungen von Mike

- Weg A, Windows-MSI, MSI samt Signatur **im** ZIP (ein `.zip`, ein `.zip.sig`
  auf GitHub bleibt).
- **Ed25519 allein.** Bewusste Abweichung von der TR-02102-1 (siehe unten).
- Anstoß zunächst durch einen Administrator.
- Keine automatische Rücknahme im ersten Wurf (2026-11-27).
- Übernommen am 2026-11-28: kein eigenes Installer-Skript; Upgrade und
  Reparatur getrennt; erwarteter Stand in der signierten Erklärung; nur eine
  MSI je Version; Bauablauf als eigener Punkt (siehe „Release-Bau").
- MSI-Bau auf dem eigenen Linux-Server aus sauberem Checkout (2026-11-28).

## Entwurf

### 1. Release-Bau

- Die MSI enthält das **gestempelte** `agent.py`. Sie entsteht deshalb nach
  dem Setzen der Versionen und vor dem Packen des ZIP, unter Linux mit
  `wixl`. Die Python-Laufzeit wird gegen einen **festen SHA-256**
  (`PY_SHA256` neben `PY_VERSION` in `build_msi.py`) geprüft; stimmt er nicht,
  bricht der Bau ab.
- **Entscheidung Mike (2026-11-28): der Linux-Schritt läuft auf dem eigenen
  Linux-Server des Herausgebers**, auf dem `wixl` schon installiert ist.
  Verworfen: WSL auf dem Signierrechner (nicht installiert) und ein Bau in
  GitHub Actions (änderte die Entscheidung „CI baut nicht").
  - Der Bau läuft aus einem **sauberen Checkout des gestempelten Commits** in
    einem eigenen Arbeitsverzeichnis, nicht aus der installierten Anlage und
    nicht mit deren venv oder Paketordner.
  - `build_release.py` wird dafür zweigeteilt: zuerst Versionen setzen (wie
    heute, dann Commit und Push); nach dem MSI-Bau mit `--msi <Datei>`
    signieren und packen.
  - Vor dem Signieren **prüft** `build_release.py`, dass das `agent.py` in der
    MSI byte-gleich mit dem im ZIP ist und die MSI-Version zu `AGENT_VERSION`
    passt; sonst bricht es ab.
  - Die MSI kommt per Kopie vom Linux-Server zum Signierrechner; der private
    Schlüssel verlässt den Signierrechner nie.
- `build_release.py` signiert die MSI und legt sie samt Erklärung und Signatur
  in das ZIP (Verzeichnis `agent-pakete/`). Ohne MSI ist das Verhalten wie
  heute (das ZIP enthält keine MSI, der Installationsweg meldet „kein Paket
  im Release", der Server baut wie bisher selbst).
- **Die Erklärung** ist eine eigene Textdatei (UTF-8, Zeilenende LF, mit
  abschließendem LF) mit Formatkennung `co37-install-v1`, Dateiname der MSI,
  `AGENT_VERSION`, SHA-256 der MSI, Python-Fassung und den Fassungen der acht
  Bibliotheken. Die Signatur deckt damit Name, Version und den **erwarteten
  Stand** mit ab; ein altes, echt signiertes Paket lässt sich nicht als neues
  ausgeben.
- Die `.sig` ist die Ed25519-Signatur über genau die Bytes der Erklärung
  (base64url wie bei `agent.py`) und trägt Formatkennung `v1` und
  Verfahrenskennzeichen `ed25519`, damit später ein zweites Verfahren
  ergänzt werden kann, ohne das Format zu brechen. Heute wird genau eine
  Signatur akzeptiert.

### 2. Server

- Enthält ein eingespieltes ZIP `agent-pakete/`, legt der Watcher **diese**
  MSI in den Paketordner und baut **keine** eigene MSI (DEB und RPM baut er
  weiter). Der Download für die Handinstallation (`/api/v1/packages`) ist
  damit dieselbe signierte Datei; ein Kunden-Server braucht für Windows kein
  `wixl` mehr. Der Paketordner bleibt so geschützt wie heute (F-29).
- Der erwartete Stand für die Hostliste („Paketupdate — Agent muss neu
  installiert werden") kommt aus der signierten Erklärung, nicht mehr aus
  einem lokal gebauten Paket.
- Neue Route `GET /api/v1/agent/install-package` (Token, nur freigegebene
  Hosts, nur Windows-Hosts): liefert MSI, Erklärung und Signatur. Fehlt das
  Paket, 404 mit klarem Text.
- Neue Auftragsart `install` (nur Windows). Anlage nur durch Administratoren,
  mit den Regeln aus 0.51.6: für Offline-Hosts nicht angelegt (409). Nur für
  Hosts, deren gemeldete Agent-Version die erste mit dieser Fähigkeit erreicht
  (Konstante im Backend); die Reihenfolge ist also `selfupdate` zuerst, dann
  `install`. Das Prüfprotokoll hält Anlage, Ergebnis und Ablehnung fest.
- Gestaffelt: bei Sammelaktionen zuerst ein Pilot-Host, die übrigen erst nach
  der Bestätigungszeit (Muster A6).
- An der Hostzeile mit „Paketupdate" oder „Laufzeit unvollständig" hängt der
  Knopf „Paket installieren".

### 3. Agent

Auftrag `install`:

1. Paket, Erklärung und Signatur vom Server holen (Größengrenze, Zeitgrenze).
2. **Alles prüfen, bevor etwas anderes geschieht:** Signatur über die Bytes
   der Erklärung gegen `release_key.pub` (ohne Schlüssel: Ablehnung, weil kein
   Signaturzwang vereinbart ist und der Weg ihn voraussetzt), Format und
   Felder der Erklärung gegen feste Muster, SHA-256 der MSI gegen die
   Erklärung, und die Version der Erklärung **nicht niedriger** als die
   installierte.
3. **Art bestimmen:** höhere Version → Upgrade (`msiexec /i`); gleiche Version
   und abweichender oder unvollständiger Stand → Reparatur (`msiexec /fa`);
   gleiche Version und Stand stimmt → **nichts tun**, Auftrag erledigt mit
   „bereits aktuell" (Schutz gegen Schleifen).
4. MSI in ein Verzeichnis schreiben, das nur SYSTEM und Administratoren
   beschreiben dürfen (unter `ProgramData\CO37`, das schon so geschützt ist),
   per Umbenennen erst nach der Prüfung. Daneben die **Markierung**: Auftrag
   und erwarteter Stand aus der Erklärung.
5. Eine **einmalige SYSTEM-Aufgabe** anlegen, die nur `msiexec` mit festen
   Argumenten ausführt (Pfad zur geprüften Datei, `/qn`,
   `REBOOT=ReallySuppress`, Protokolldatei, kein `CO37SERVER`), sie starten
   und sich selbst beenden. Kein eigenes Installer-Skript. Die Aufgabe läuft
   im Aufgabenplaner, nicht im Prozessbaum des Agenten.
6. Den Rest erledigt die MSI: der Restart Manager beendet noch laufende
   Prozesse, die Aufgabe `CO37Agent` wird angelegt und gestartet.
7. Der **neue** Agent liest beim Start die Markierung, vergleicht den
   tatsächlichen Stand (Python-Fassung, Bibliotheken, Laufzeit vollständig)
   mit dem erwarteten und meldet das Ergebnis im ersten Heartbeat. Er räumt
   die einmalige Aufgabe, die MSI-Kopie und die Markierung ab und behält das
   `msiexec`-Protokoll für die Fehlersuche.
8. Bis dahin bleibt der Auftrag `running`. Meldet sich in der Frist kein Agent
   mit dem erwarteten Stand, wird er „fehlgeschlagen" (Frist und Zustand wie
   bei Aufträgen sonst; ob eine Schema-Änderung nötig ist, klärt die
   Umsetzung).

### 4. Rückweg

**Entscheidung von Mike (2026-11-27): keine automatische Rücknahme im ersten
Wurf.** Läuft `msiexec` durch, der neue Agent ist aber kaputt oder meldet sich
nicht, wird der Auftrag nach der Frist als „fehlgeschlagen" angezeigt; der
Host wird von Hand wiederhergestellt (MSI wie bisher, siehe
`agent-msi-ausrollen`). Das Risiko für die Fläche begrenzt die Staffelung:
der Pilot-Host kommt zuerst, die übrigen erst nach der Bestätigungszeit. Eine
automatische Rücknahme (alte MSI behalten und bei ausbleibender Meldung neu
installieren) ist ein möglicher späterer, eigener Schritt.

- Scheitert `msiexec`, rollt der Installer selbst zurück; die alte Fassung und
  die Aufgabe bleiben, und der alte Agent kommt über den Takt der Aufgabe
  zurück und meldet den Fehler mit dem Protokoll.

## Abgleich mit der TR-02102-1 (Fassung 2026-01, gelesen am 2026-11-27)

- **Ed25519 kommt in der TR nicht vor** (kein Treffer für EdDSA, Ed25519,
  Edwards). Empfohlen sind klassisch RSA ab 3000 Bit, DSA bis 2029, ECDSA und
  Brainpool-Varianten mit `brainpoolP256r1` bis `P512r1`; quantensicher
  ML-DSA, SLH-DSA, LMS/HSS und XMSS (Tabelle 5.3, Tabelle B.3).
- **Bewusste Abweichung, von Mike entschieden:** Ed25519 allein. Begründung:
  die Prüfkette für `agent.py`, das Release-ZIP und die Lizenzschlüssel nutzt
  es bereits (`tools/sign-release.py`, `tools/make-license.py`) und ist
  gemessen; ein zweiter Schlüssel ließe sich nur über einen signierten
  Agent-Update-Weg verteilen und erhöht die Betriebslast. Die Abweichung wird
  bei einer Prüfung offen genannt, nicht verschwiegen.
- Hash: SHA-256 ist empfohlen.
- **Frist:** klassische Signaturen werden voraussichtlich nur bis Ende 2035
  empfohlen; für Produkte mit Lebensdauer über 2030 wird ein Upgradepfad auf
  quantensichere Update-Signaturen dringend empfohlen (Abschnitt 5.3). Die
  Formatkennung in der `.sig` ist der Anschluss dafür; ein zusätzliches
  Verfahren wäre eine eigene Änderung.
- Schlüsselschutz: die TR verlangt „zuverlässig geheim"; der private Schlüssel
  bleibt passphrasegeschützt beim Herausgeber, ohne Hardware-Token.

## Sicherheitsbetrachtung

- Eingaben: alle Felder der Erklärung werden gegen feste Muster geprüft und nie
  als Pfad oder Befehlsteil verwendet; die Zieldatei heißt fest. Größen- und
  Zeitgrenzen für den Abruf.
- Kein zusätzlicher SYSTEM-Code: die einmalige Aufgabe ruft nur `msiexec` mit
  festen Argumenten auf, ohne Zeichenkette aus Server- oder Auftragsdaten.
  Damit greifen auch keine PowerShell-Ausführungsrichtlinien oder AppLocker-
  Regeln des Kunden dazwischen.
- Auftrag nur für Administratoren, protokolliert; Pilot vor der Fläche; ein
  Auftrag für einen Host, dessen Stand stimmt, tut nichts.
- Der Server kann den Agenten zu einer Installation auffordern, aber kein
  Paket unterschieben, das der Herausgeber nicht signiert hat, und keine
  ältere Fassung einspielen.

## Prüfung (TDD)

- Prüfreihe `installationsweg`: Signatur ok / falsch / fehlt, kein Schlüssel,
  veränderte MSI, veränderte Erklärung, Feld außerhalb des Musters, niedrigere
  Version, gleiche Version mit stimmendem Stand (nichts tun), gleiche Version
  mit abweichendem Stand (Reparatur), höhere Version (Upgrade), feste
  `msiexec`-Argumente, übergroße Datei, Auftrag für Offline-Host (409), für
  Linux-Host (abgewiesen), für zu alten Agenten (abgewiesen), nur
  Administratoren, Pilotstaffelung, Watcher übernimmt die signierte MSI und
  baut keine eigene. Jede Prüfung mit benannter Mutation belegt.
- Was im Container nicht prüfbar ist (`msiexec`, Aufgabenplaner, Restart
  Manager), wird **auf einem Windows-Testhost gemessen**, bevor ein Release
  dies enthält: die einmalige Aufgabe überlebt das Ende des Agenten, der neue
  Agent meldet den erwarteten Stand, die Aufgabe `CO37Agent` bleibt, die
  Reparatur mit `/fa` stellt eine entfernte `certifi\cacert.pem` wieder her,
  und bei absichtlich kaputter MSI bleibt ein laufender Agent.

## Offene Punkte für die Umsetzung

- Wie die MSI vom Linux-Server zum Signierrechner kommt (Kopierbefehl, Prüfung
  der Prüfsumme nach dem Kopieren) — wird im Plan als Schrittfolge für den
  Release-Ablauf festgelegt.
- Wohin der Watcher die signierte MSI legt und wie das Dienstkonto davon
  getrennt bleibt (Umsetzungsdetail; das Muster von `build_packages.sh` gilt).
- Prüfgrenzen (Größe, Frist für die Meldung des neuen Agenten) legt die
  Umsetzung fest und begründet sie.
