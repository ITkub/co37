# Signierter Installationsweg für den Windows-Agenten — Entwurf

Stand 2026-11-27. Gehört zu B-2026-11-17-01 (`komponenten-und-fassungen`).
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
  aktuellen Paket abweichen, über CO-37 aktualisieren lassen, ohne den Host
  anzufassen.
- Der **Agent prüft die Signatur selbst**, mit dem bei ihm liegenden
  `release_key.pub`. Ein kompromittierter Server kann dem Host kein Paket
  unterschieben.
- Die MSI wird vom **Herausgeber** gebaut und signiert, nicht beim Kunden.
- Alles kommt mit dem normalen Release: ein `.zip` und ein `.zip.sig` auf
  GitHub, wie bisher.
- Es gibt einen Weg zurück, wenn die Installation misslingt, und der Host
  bleibt nicht ohne Agent zurück.

## Nicht-Ziele

- Linux-Pakete (`.deb`, `.rpm`): Laufzeit und Bibliotheken kommen dort aus der
  Distribution und über den normalen Patchlauf.
- Selbsttätiges Auslösen. Der erste Wurf wird von einem Administrator
  angestoßen (Knopf je Host und Sammelaktion), nicht vom Server von selbst.
- Authenticode, Hardware-Schlüsselspeicher, Post-Quanten-Verfahren.
- Der bestehende Weg `selfupdate` für `agent.py` bleibt unverändert.

## Anti-Ziele (das wäre ein Fehlschlag, auch wenn es läuft)

- Irgendein Weg, auf dem allein der Server entscheidet, was als SYSTEM läuft.
- Ein Installer, dessen Eingabe vor dem Start nicht geprüft wurde.
- Ein Host, der nach misslungener Installation ohne laufenden Agenten bleibt
  und sich nicht mehr meldet.
- Das manuelle Einspielen der MSI (Doppelklick, GPO, `msiexec`) wird
  schlechter oder anders.

## Randbedingungen aus dem Bestand

- Der Agent spricht nur mit seinem Server (Token). Er holt das Paket **vom
  Server**, nie von GitHub.
- Der Server holt Releases über `github_update.py` (ZIP und `.sig`) und prüft
  die ZIP-Signatur selbst. Grenze `MAX_ZIP_BYTES` = 50 MB.
- Heute entsteht die MSI beim Kunden aus dem ZIP (`wixl`), die Python-Laufzeit
  wird von python.org **ohne festen Hash** geladen. Beides entfällt für den
  Verteilweg.
- `wixl` läuft nur unter Linux; `build_release.py` läuft beim Herausgeber
  unter Windows und signiert mit dem passphrasegeschützten Schlüssel.
- Der Agent läuft als SYSTEM über die geplante Aufgabe `CO37Agent`. Die MSI
  beendet beim Upgrade den laufenden Agenten (Restart Manager) und legt die
  Aufgabe neu an. Ein `msiexec` als Kindprozess des Agenten stürbe mit ihm.
- Die MSI ist für alle Kunden gleich; Server und Token stammen aus der
  vorhandenen `agent.conf` (wird beim Upgrade übernommen).
- Der Agent kennt vier Auftragsarten (`scan`, `patch`, `reboot`,
  `selfupdate`); der Server legt Sofort-Aufträge für Offline-Hosts seit 0.51.6
  nicht mehr an.

## Ansätze

**A. Vom Herausgeber gebaute und signierte MSI im Release-ZIP.** Der Agent
holt sie vom Server, prüft die Signatur selbst und startet einen vom Agenten
losgelösten Installer. Gewählt.

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

## Entwurf

### 1. Release-Bau

- Die MSI entsteht beim Herausgeber auf einem Linux-Baurechner mit `wixl` aus
  demselben Stand wie das ZIP. Die Python-Laufzeit wird gegen einen
  **festen SHA-256** (`PY_SHA256` neben `PY_VERSION` in `build_msi.py`)
  geprüft; stimmt er nicht, bricht der Bau ab.
- `build_release.py` bekommt `--msi <Datei>`. Es prüft, dass die MSI zur
  `AGENT_VERSION` des Standes passt, signiert sie und legt beides in das ZIP
  (neues Verzeichnis `agent-pakete/`). Ohne `--msi` ist das Verhalten wie heute
  (das ZIP enthält keine MSI, und der Installationsweg meldet „kein Paket im
  Release").
- **Signiert wird eine kleine Erklärung, nicht nur der Hash:**
  `co37-install-v1`, Dateiname, `AGENT_VERSION`, SHA-256 der MSI, jeweils eine
  Zeile. Damit deckt die Signatur Name und Version mit ab und ein altes, echt
  signiertes Paket lässt sich nicht als neues ausgeben.
- Die `.sig` trägt die Formatkennung `v1` und ein Verfahrenskennzeichen
  (`ed25519`), damit später ein zweites Verfahren ergänzt werden kann, ohne
  das Format zu brechen. Heute wird genau eine Signatur akzeptiert.

### 2. Server

- `github_update` und der Upload-Weg übernehmen `agent-pakete/` aus dem
  geprüften ZIP an einen Ort, der dem Dienstkonto nicht beschreibbar nachträglich
  verändert werden kann (Muster: wie die Pakete, F-29).
- Neue Route `GET /api/v1/agent/install-package` (Token, nur freigegebene
  Hosts, nur Windows-Hosts): liefert MSI, Erklärung und Signatur. Fehlt das
  Paket, 404 mit klarem Text.
- Neue Auftragsart `install` (nur Windows). Anlage nur durch Administratoren,
  mit den Regeln aus 0.51.6: für Offline-Hosts nicht angelegt (409). Das
  Prüfprotokoll (Audit) hält Anlage, Ergebnis und Ablehnung fest.
- Gestaffelt: bei Sammelaktionen zuerst ein Pilot-Host, die übrigen erst nach
  der Bestätigungszeit (Muster A6).
- Die Hostliste zeigt schon „Paketupdate — Agent muss neu installiert werden";
  daran hängt der Knopf „Paket installieren".

### 3. Agent

Auftrag `install`:

1. Paket, Erklärung und Signatur vom Server holen (Größengrenze, Zeitgrenze).
2. **Alles prüfen, bevor etwas anderes geschieht:** Signatur gegen
   `release_key.pub` (ohne Schlüssel: Ablehnung, weil Signaturzwang vereinbart
   ist), Dateiname und Version aus der Erklärung, SHA-256 der Datei, und dass
   die Version **nicht niedriger** ist als die installierte und nicht die
   zurückgerollte (`rejected_version`).
3. Datei in ein geschütztes Verzeichnis schreiben (nur SYSTEM und
   Administratoren), nicht in einen für Benutzer beschreibbaren Ort; erst
   nach der Prüfung, per Umbenennen.
4. Einen **vom Agenten losgelösten Installer** starten, der den Agentenprozess
   beendet, `msiexec /i … /qn REBOOT=ReallySuppress` ausführt (ohne
   `CO37SERVER`, die vorhandene Konfiguration bleibt), prüft, dass die
   Laufzeit vollständig ist (`certifi\cacert.pem`), und die Aufgabe `CO37Agent`
   startet. Ablauf wie in `agent-msi-ausrollen`.
5. Der Auftrag bleibt im Zustand „Installation läuft", bis der **neue** Agent
   sich meldet und die erwartete Fassung nachweist (Marker-Datei mit erwarteter
   Version); dann „erledigt". Meldet sich innerhalb der Frist kein Agent mit
   der erwarteten Fassung, wird der Auftrag „fehlgeschlagen" und der Server
   zeigt es.

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
  die Aufgabe bleiben.
- Der Installer startet in jedem Fall am Ende die Aufgabe `CO37Agent`, damit
  ein Agent läuft; der fünf-Minuten-Takt der Aufgabe ist das Netz dahinter.
- Eine Version, die zurückgerollt wurde, wird nicht erneut eingespielt (wie
  bei `self_update()`).

## Abgleich mit der TR-02102-1 (Fassung 2026-01, gelesen am 2026-11-27)

- **Ed25519 kommt in der TR nicht vor** (kein Treffer für EdDSA, Ed25519,
  Edwards). Empfohlen sind klassisch RSA ab 3000 Bit, DSA bis 2029, ECDSA und
  Brainpool-Varianten mit `brainpoolP256r1` bis `P512r1`; quantensicher
  ML-DSA, SLH-DSA, LMS/HSS und XMSS (Tabelle 5.3, Tabelle B.3).
- **Bewusste Abweichung, von Mike entschieden:** Ed25519 allein. Begründung:
  die Prüfkette für `agent.py`, ZIP und Lizenz nutzt es bereits und ist
  gemessen; ein zweiter Schlüssel verteilt sich nur über einen
  signierten Agent-Update-Weg und erhöht die Betriebslast. Sie wird bei einer
  Prüfung offen genannt, nicht verschwiegen.
- Hash: SHA-256 ist empfohlen.
- **Frist:** klassische Signaturen werden voraussichtlich nur bis Ende 2035
  empfohlen; für Produkte mit Lebensdauer über 2030 wird ein Upgradepfad auf
  quantensichere Update-Signaturen dringend empfohlen (Abschnitt 5.3). Die
  Formatkennung in der `.sig` ist der Anschluss dafür; ein zusätzliches
  Verfahren wäre eine eigene Änderung.
- Schlüsselschutz: die TR verlangt „zuverlässig geheim"; der private Schlüssel
  bleibt passphrasegeschützt beim Herausgeber, ohne Hardware-Token.

## Sicherheitsbetrachtung

- Eingaben: Dateiname und Version aus der Erklärung werden gegen feste Muster
  geprüft, nie als Pfad oder Befehlsteil verwendet; die Zieldatei heißt
  fest. Größen- und Zeitgrenzen für den Abruf.
- Der Installer bekommt nur den Pfad zur geprüften Datei und feste Argumente
  (keine Shell-Zeichenkette aus Server- oder Auftragsdaten).
- Auftrag nur für Administratoren, protokolliert; Pilot vor der Fläche.
- Der Server kann den Agenten zu einer Installation auffordern, aber kein
  Paket unterschieben, das der Herausgeber nicht signiert hat.

## Prüfung (TDD)

- Prüfreihe `installationsweg`: Signatur ok / falsch / fehlt, veränderte Datei,
  veränderte Version in der Erklärung, niedrigere Version, zurückgerollte
  Version, übergroße Datei, Auftrag für Offline-Host (409), Auftrag für
  Linux-Host (abgewiesen), nur Administratoren, Pilotstaffelung. Jede Prüfung
  mit benannter Mutation belegt.
- Was im Container nicht prüfbar ist (`msiexec`, losgelöster Prozess,
  Aufgabenplaner), wird **auf einem Windows-Testhost gemessen**, bevor ein
  Release dies enthält: der Installer überlebt das Ende des Agenten, der
  neue Agent meldet die erwartete Fassung, die Aufgabe bleibt, bei absichtlich
  kaputter MSI bleibt ein laufender Agent.

## Festlegungen aus der Entwurfsprüfung

Ein unabhängiger Leser hat den Entwurf geprüft. Aus den Antworten:

**Aus dem Bestand beantwortet:**

- **Version.** Jedes Release setzt `AGENT_VERSION` auf die Release-Version
  (`build_release.py`, `setze_versionen`). Auch ein Release, das nur Laufzeit
  oder Bibliotheken anhebt, bekommt also eine neue Agent-Version, und die MSI
  trägt sie.
- **Größe.** Die MSI misst 18 MB (gebaut mit Python 3.14.10 und acht
  Bibliotheken). Das ZIP bleibt mit rund 19 MB weit unter `MAX_ZIP_BYTES`
  (50 MB); die Grenze wird nicht angehoben.
- **Fähigkeit der Hosts.** Der Auftrag `install` erreicht einen Host erst, wenn
  sein `agent.py` ihn kennt. Reihenfolge: zuerst das normale `selfupdate`
  (signiertes `agent.py`), danach `install`. Der Server legt `install` nur für
  Hosts an, deren gemeldete Agent-Version die erste mit dieser Fähigkeit
  erreicht (eine Konstante im Backend). Ein Host ohne `release_key.pub` kann
  den Weg nicht nutzen: der Agent lehnt dann ab, weil Signaturzwang nicht
  vereinbart ist.

**Meine Entscheidungen (zum Gegenlesen):**

- Der Versionsvergleich im Agenten lautet „nicht niedriger" (`>=`), nicht
  „höher". So bleibt die Reparatur mit gleicher Fassung möglich
  („Laufzeit unvollständig — Agent reparieren"). Das Ergebnis eines Auftrags
  wird deshalb **nicht** an der Versionsnummer abgelesen, sondern daran, dass
  der neue Agent im Heartbeat den erwarteten Paketstand meldet (Laufzeit und
  Bibliotheken, wie seit 0.51.0 gemessen).
- Der losgelöste Installer ist ein **PowerShell-Skript mit festem Inhalt**,
  das als Konstante in der signierten `agent.py` steht und zur Laufzeit in ein
  geschütztes Verzeichnis geschrieben wird (nicht aus Server- oder
  Auftragsdaten). Es läuft über eine einmalige Aufgabe als SYSTEM
  (`schtasks`), die den Agentenprozess und dessen Prozessbaum verlässt; es
  nutzt nicht die eingebettete Python-Laufzeit, die die MSI ersetzt. Dass es
  das Ende des Agenten überlebt, wird auf einem Windows-Testhost gemessen.
- Die **Erklärung** ist eine eigene Textdatei (UTF-8, Zeilenende LF, mit
  abschließendem LF), die `.sig` die Ed25519-Signatur über genau diese Bytes
  (base64url wie bei `agent.py`). Der Agent prüft die Signatur über die
  empfangenen Bytes der Erklärung und danach, dass deren SHA-256-Zeile zur
  heruntergeladenen MSI passt.
- Ergebnis über den Neustart: der Installer schreibt eine Markierung
  (Auftrag, erwarteter Paketstand); der neue Agent liest sie beim Start und
  meldet das Ergebnis im ersten Heartbeat. Der Auftrag bleibt bis dahin
  `running`; die Frist stellt der Server (wie bei Aufträgen sonst). Ob dafür
  eine Schema-Änderung nötig ist, klärt die Umsetzung.

## Offene Punkte für die Umsetzung

- Ablauf, wie die MSI vom Linux-Baurechner zum Signierrechner kommt
  (Vorschlag: Bauschritt erzeugt die MSI, du kopierst sie, `build_release.py
  --msi` signiert). Entscheidung: Mike.
- Wohin der Server das Paket legt und wie das Dienstkonto davon getrennt wird
  (Umsetzungsdetail; das Muster von `build_packages.sh` gilt).
- Prüfgrenzen (Größe, Frist für die Meldung des neuen Agenten) legt die
  Umsetzung fest und begründet sie.
