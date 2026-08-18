# CO-37

Patch-Management für Windows- und Linux-Systeme mit Checkmk-Anbindung.

Vorgesehen für den Betrieb **im lokalen Netz**. Ein Server je Netz, Zugriff von
außen über WireGuard. Es gibt keine Domain-, TLS- oder Enrollment-Token-Verwaltung.

---

# Teil 1 — Server einrichten

Einmal pro Netz. Ziel: Debian 13 oder Ubuntu, physisch, VM oder LXC.
Bedarf: 1 CPU, 1 GB RAM, 4 GB Platte.

### 1.1 ZIP auf den Server kopieren

Auf **deinem Rechner**:

```
scp co37_v0_4_1.zip root@192.168.1.10:/tmp/
```

### 1.2 Auf dem Server anmelden

```
ssh root@192.168.1.10
```

### 1.3 unzip installieren

```
apt update && apt install -y unzip
```

### 1.4 Entpacken

```
mkdir -p /opt/co37 && unzip -o /tmp/co37_v0_4_1.zip -d /opt/co37
```

### 1.5 Installieren

```
bash /opt/co37/setup.sh
```

Das Skript legt Benutzer, venv, systemd-Dienste und Schlüssel an. Am Ende werden
**Adresse und Admin-Token** ausgegeben. Beides notieren — der Token wird beim
ersten Aufruf der Oberfläche gebraucht.

Läuft mehrfach ohne Schaden; bestehende Schlüssel und Daten bleiben erhalten.

### 1.6 Prüfen, dass der Dienst läuft

```
systemctl status co37-backend --no-pager
```

Falls nicht:

```
journalctl -u co37-backend -n 40 --no-pager
```

### 1.7 Agent-Pakete bauen

Für Windows wird `wixl` gebraucht. Das Paket heißt **wixl**, nicht `msitools`:

```
apt install -y wixl
```

Dann bauen:

```
cd /opt/co37 && bash build_packages.sh
```

Nur Linux, ohne Windows-Paket:

```
cd /opt/co37 && bash build_packages.sh --deb-only
```

Der MSI-Bau lädt beim ersten Mal die Python-Embeddable-Distribution von
python.org und legt sie in `packaging/cache/` ab. Ohne Netzzugang die Datei
`python-3.12.8-embed-amd64.zip` von Hand dort hinterlegen.

### 1.8 Oberfläche öffnen

```
http://192.168.1.10:8080
```

Beim ersten Aufruf nach dem Admin-Token aus Schritt 1.5 gefragt.

---

# Teil 2 — Checkmk verbinden

Optional, aber nötig für automatische Downtimes.

### 2.1 Rolle anlegen

In Checkmk: **Setup → Users → Roles**, neue Rolle auf Basis von
`no_permissions`. Nur zwei Rechte setzen:

- Hostliste lesen
- Downtimes setzen und entfernen

Nicht die admin-Rolle kopieren. Ein kompromittierter CO-37 soll keine
Hosts anlegen und keine Konfiguration ändern können.

### 2.2 Automation-Benutzer anlegen

**Setup → Users → Add user.** Authentifizierung auf
*Automation secret for machine accounts*, obige Rolle zuweisen, Secret notieren.

In Checkmk 2.4 existiert **kein** Benutzer `automation` mehr von Haus aus.

### 2.3 In CO-37 eintragen

**Einstellungen → Checkmk.** URL, Site, Benutzer, Secret. Die Verbindung wird
sofort geprüft; das Secret wird nur bei Erfolg gespeichert, verschlüsselt, und
ist danach nicht mehr abrufbar.

---

# Teil 3 — Agent ausrollen

Hosts werden **nicht** von Hand angelegt. Sie entstehen durch die Anmeldung des
Agents und warten dann auf Freigabe.

## Linux

### 3.1 Befehl aus der Oberfläche holen

**Einstellungen → Agents → Linux → Befehl kopieren.** Enthält Serveradresse und
API-Key bereits eingesetzt.

### 3.2 Auf dem Zielsystem ausführen

Als root, Beispiel:

```
curl -fsSL -H "X-API-Key: DEIN-TOKEN" http://192.168.1.10:8080/api/v1/packages/co37-agent_0.4.1_all.deb -o /tmp/pp-agent.deb && CO37_SERVER="http://192.168.1.10:8080" apt-get install -y /tmp/pp-agent.deb
```

`apt-get install` statt `dpkg -i` — sonst wird `python3-requests` nicht
mitaufgelöst.

### 3.3 Prüfen

```
systemctl status co37-agent --no-pager
```

### 3.4 Server nachträglich eintragen

Falls ohne `CO37_SERVER` installiert wurde:

```
co37-connect http://192.168.1.10:8080
```

## Windows

### 3.5 MSI herunterladen

**Einstellungen → Agents → Windows (.msi) → Herunterladen**, dann auf das
Zielsystem kopieren.

### 3.6 Installieren

In einer Eingabeaufforderung als Administrator:

```
msiexec /i co37-agent-0.4.1.msi /qn CO37SERVER="http://192.168.1.10:8080"
```

Python ist im Paket enthalten, auf dem Zielsystem wird nichts vorausgesetzt.
Registriert wird eine **geplante Aufgabe**, die beim Systemstart als `SYSTEM`
läuft — kein Windows-Dienst, weil ein Python-Skript ohne Wrapper vom
Dienststeuerungs-Manager beendet würde.

Das Paket ist unsigniert, Windows zeigt daher eine Warnung.

### 3.7 Prüfen

```
schtasks /Query /TN CO37Agent
```

---

# Teil 4 — Host in Betrieb nehmen

### 4.1 Freigeben

Der Host erscheint in der Übersicht als **wartet auf Freigabe** und bekommt bis
dahin keine Aufträge. Auf **Freigeben** klicken.

Das ist der einzige Schutz gegen fremde Anmeldungen — im LAN-Betrieb verlangt
das System bewusst kein Anmeldetoken.

### 4.2 Checkmk verknüpfen

Auf **…** beim Host. Unter *Checkmk-Verknüpfung* mehrere Hosts auswählbar: fällt
der Host aus, sind oft weitere Objekte betroffen — Cluster-Ressourcen, Dienste,
abhängige Anwendungen. Alle ausgewählten bekommen dieselbe Downtime.

Darunter die **Downtime-Dauer**, die bei Neustarts gesetzt wird.

### 4.3 Neustart-Richtlinie festlegen

Im selben Dialog. Beim ersten Host **aus** lassen — dann siehst du im Verlauf,
ob die Erkennung korrekt meldet, ohne dass etwas neu startet.

Das Wartungsfenster ist **kein Zeitplan**, sondern eine Schranke: es entscheidet,
ob ein nach dem Patchen fälliger Neustart sofort erfolgen darf. Es startet
nichts von selbst. Für einen festen Termin siehe *Neustart planen*.

Format:

```
SA,SO 02:00-05:00
```

Auch über Mitternacht:

```
MO-FR 22:00-06:00
```

Leeres Feld bedeutet jederzeit.

### 4.4 Erste Prüfung

**Prüfen** anklicken. Beim nächsten Agent-Kontakt — spätestens nach 60 Sekunden
— erscheinen die gefundenen Updates.

---

# Wie der Neustart abläuft

1. Agent installiert die Updates
2. Agent stellt **selbst** fest, ob ein Neustart aussteht
   (Registry unter Windows, `/var/run/reboot-required` bzw. Kernelvergleich
   unter Linux)
3. Steht einer an, fragt der Agent beim Backend um Freigabe und **wartet**
4. Backend prüft: Richtlinie erlaubt? Wartungsfenster passt? Dann Downtime auf
   allen verknüpften Checkmk-Hosts
5. Erst wenn die Downtime steht, kommt die Freigabe zurück
6. Agent startet neu
7. Nach dem Neustart meldet sich der Agent, Downtime wird aufgehoben

Scheitert Schritt 4, **unterbleibt der Neustart**. Der Host bleibt als
neustartbedürftig markiert und sichtbar.

Der **Neustart-Knopf** in der Übersicht nutzt dieselbe Kette, umgeht aber
Richtlinie und Wartungsfenster — nicht die Downtime. Ist kein Checkmk-Host
verknüpft, kommt eine ausdrückliche Warnung, dass das Monitoring Alarm schlagen
wird.

---

# Update planen

Im Host-Dialog unter *Update planen*. Wochentage, Uhrzeit, Nachlauf, und ein
Haken, ob bei Bedarf automatisch neu gestartet werden soll.

Ausgewertet wird beim Kontakt des Agents — auch hier kein Hintergrunddienst.
Die Genauigkeit entspricht dem Abfrageintervall, was für ein nächtliches Fenster
ausreicht.

**Der Nachlauf ist der wichtige Teil.** War der Host zum Termin ausgeschaltet,
wird der Patch-Lauf innerhalb des Nachlaufs noch nachgeholt. Danach wird der
Termin übersprungen — sonst würde ein Server, der über das Wochenende aus war,
Montagmorgen im Betrieb patchen. Vorgabe sind vier Stunden.

Ein Termin läuft nur einmal: `last_patch_run` wird gesetzt und gegen den Termin
verglichen. Solange noch ein Patch-Auftrag offen ist, wird kein zweiter angelegt.

---

# Neustart planen

Für einen **reinen Neustart ohne Updates** — etwa wenn ein Dienst hängt und
tagsüber nicht neu gestartet werden kann.

Im Host-Dialog unter *Neustart planen*. Datum, Uhrzeit, und ein Haken, ob vorher
eine Downtime gesetzt werden soll — der ist standardmäßig gesetzt.

Der Auftrag wird angelegt und erst beim Erreichen des Zeitpunkts an den Agent
übergeben. Das läuft **ohne Hintergrunddienst**: der Heartbeat gibt geplante
Aufträge nur heraus, wenn die Zeit erreicht ist. Ein Neustart des Backends
schadet daher nicht, und es kann nichts auseinanderlaufen.

Richtlinie und Wartungsfenster werden bei geplanten Neustarts übergangen — der
Zeitpunkt ist ja ausdrücklich gewählt. Die Downtime nicht: ist der Haken
gesetzt und die Downtime scheitert, unterbleibt der Neustart.

**Nachlaufzeit.** Meldet sich der Host bis zum Ablauf dieser Frist nicht, wird
der Neustart **nicht nachgeholt**, sondern als abgelaufen verworfen. Ohne diese
Grenze würde ein Server, der übers Wochenende aus war, Montagfrüh im laufenden
Betrieb neu starten. Vorgabe zwei Stunden bei geplanten, zehn Minuten beim Knopf
**Neustart** in der Übersicht.

Ein verfallener Auftrag steht im Verlauf als *abgebrochen* mit dem Grund —
geplanter Zeitpunkt, Nachlauf und der Hinweis, dass sich der Host nicht gemeldet
hat.

Eingeplante Neustarts stehen im selben Reiter und in der Übersicht am Host. Sie
lassen sich abbrechen, solange der Zeitpunkt nicht erreicht ist.

Scans und Patch-Aufträge verfallen **nicht** — die schaden auch später nicht.

---

# Live-Ausgabe

Läuft ein Auftrag, erscheint am Host in der Übersicht ein Knopf **Live** und der
aktuelle Fortschritt. Das Protokoll wird im Sekundentakt nachgeladen, während der
Vorgang läuft.

Abgeschlossene Aufträge sind über **Verlauf** erreichbar — dort ist jeder Auftrag
anklickbar und zeigt sein vollständiges Protokoll.

Der Agent liest die Ausgabe zeilenweise mit und schickt sie gebündelt alle 1,5
Sekunden. Abgelegt wird je Auftrag eine Datei unter `data/joblogs/`, nicht in der
Datenbank — sonst würde bei jedem Schub die gesamte Textspalte neu geschrieben.
Obergrenze 2 MB je Auftrag, Aufbewahrung 30 Tage.

**Unterschied zwischen den Systemen:** Unter Linux ist die Ausgabe von `apt` und
`dnf` echt zeilenweise, du siehst den Vorgang mitlaufen. Die Windows Update COM
API kennt keinen Datenstrom — Download und Installation sind blockierende
Aufrufe. Deshalb werden Updates dort einzeln durchlaufen, was `Installiere 3 von
12` ergibt statt laufender Prozentwerte.

---

# Downtime von Hand

Im Host-Dialog unter *Downtime von Hand setzen*. Entweder Minuten ab jetzt oder
eine feste Zeitspanne von–bis. Wirkt auf alle verknüpften Checkmk-Hosts.
Aufheben trifft nur Downtimes, die von CO-37 gesetzt wurden — fremde
Wartungsfenster bleiben unberührt.

---

# Agents aktualisieren

Unter **Einstellungen → Agents → Agent-Updates**.

**Automatisch ausrollen** ist abschaltbar. Eingeschaltet zieht CO-37 die
Agents nach jedem Systemupdate selbsttätig nach. Abschaltbar deshalb, weil es
bedeutet, dass Code ohne weiteres Zutun auf Kundensysteme geschoben wird — das
will man vielleicht bewusst entscheiden.

**Gestaffelt** beginnt mit einem wählbaren Pilot-Host. Erst wenn der sich mit
der neuen Version zurückmeldet, folgen die übrigen. Wähl dafür ein unkritisches
System. Schlägt der Pilot fehl oder meldet er sich 30 Minuten nicht, wird
**nicht weiter ausgerollt**.

**Alle auf einmal** aktualisiert gleichzeitig. Schneller, aber ein fehlerhafter
Agent trifft die gesamte Flotte.

## Kein Neustart ohne Grund

Der Agent vergleicht den neuen Code mit seinem eigenen und blendet dabei die
Versionszeile aus. Hat sich nur die Nummer geändert, wird die Datei geschrieben,
aber **kein Dienstneustart** ausgelöst. Der Agent liest seine Version danach aus
der Datei, meldet also sofort die neue Nummer.

## Selbstheilung

Vor einem Neustart legt der Agent eine Markierung ab. Meldet er sich danach
erfolgreich, verschwindet sie. Findet er sie beim Start noch vor, ist der neue
Code nicht angelaufen — dann spielt er die gesicherte Fassung zurück und läuft
mit der alten weiter.

Ohne das müsste man bei einem fehlerhaften Rollout auf jedes betroffene System
einzeln, um die `.bak` von Hand zurückzuspielen. Erst dadurch wird automatisches
Ausrollen verantwortbar.

---

# Agents von Hand aktualisieren

Der Server kennt die Version der ausgelieferten `agent.py`. Weicht ein Host ab,
erscheint dort in der Übersicht ein Hinweis und ein Knopf **Agent**.

Für alle auf einmal: **Einstellungen → Agents → Alle veralteten Agents
aktualisieren.**

Der Agent lädt den Code, prüft die SHA-256-Summe, prüft die Syntax, legt die
alte Fassung als `.bak` daneben und startet neu. Schlägt eine der Prüfungen
fehl, bleibt die alte Fassung aktiv.

---

# Wichtig nach jedem Systemupdate

Die Agent-Pakete enthalten eine Kopie der `agent.py`. Sie liegen unter `data/`,
das der Update-Watcher bewusst nie anfasst — nach einem Systemupdate sind sie
daher veraltet und müssen neu gebaut werden:

```
cd /opt/co37 && bash build_packages.sh
```

Die Oberfläche kennzeichnet veraltete Pakete und bietet sie nicht zur
Installation an. Steht dort ein Hinweis, ist dieser Befehl fällig.

**Agent und System tragen dieselbe Versionsnummer.** `build_release.sh` setzt
`AGENT_VERSION` in `agent/agent.py` beim Bauen auf die Release-Version.

Bis 0.8.4 waren es zwei unabhängige Nummern. Das hat nur verwirrt — und beim
Wechsel des Benennungsschemas ging die Paketversion rückwärts, worauf `apt` die
Installation als Downgrade verweigerte. Eine Nummer für alles beseitigt beides.

Nach jedem Release melden die Agents daher eine abweichende Version und lassen
sich über *Alle veralteten Agents aktualisieren* mit einem Klick nachziehen.

Bereits ausgerollte Agents brauchen kein neues Paket: sie aktualisieren sich
über den Knopf **Agent** bzw. *Alle veralteten Agents aktualisieren*. Der
Neubau ist nur für **neue** Installationen nötig.

---

# System aktualisieren

Neues Paket bauen:

```
cd /opt/co37 && bash build_release.sh 0.5.0
```

Unter **Einstellungen → Update** hochladen. Das Backend prüft die ZIP-Struktur
und stellt bereit, führt aber nichts aus. Erst nach Bestätigung übernimmt
`update_watcher.py` als root:

1. aktuellen Stand nach `update_backups/<zeitstempel>` sichern
2. `backend/`, `frontend/` und `agent/` austauschen
3. `pip install -r requirements.txt`
4. Dienst neu starten
5. `/api/health` abfragen — schlägt das fehl, wird zurückgerollt

Ausgetauscht werden `backend/`, `frontend/`, `agent/`, `packaging/`, `tests/`
sowie die Skripte auf oberster Ebene (`setup.sh`, `build_packages.sh`,
`build_release.sh`) und der Watcher selbst.

`build_release.sh` prüft beim Bauen, dass alles aus `MANAGED_FILES` auch
wirklich im Paket liegt, und bricht sonst ab. Eine fehlende Datei würde der
Watcher stillschweigend überspringen — ihre Korrekturen erreichten den Betrieb
dann nie. `data/` wird nie angefasst. Die letzten drei
Sicherungen bleiben.

Bis 0.4.2 wurden nur die drei Verzeichnisse getauscht — Korrekturen an den
Hilfsskripten erreichten eine laufende Installation daher nie. Wer von einer
Fassung vor 0.4.3 kommt, muss `update_watcher.py` einmalig von Hand ersetzen:

```
systemctl stop co37-watcher
```

```
unzip -o -j /tmp/co37_v0_4_3.zip update_watcher.py build_packages.sh build_release.sh setup.sh -d /opt/co37
```

```
systemctl start co37-watcher
```

Danach laufen Updates vollständig über die Oberfläche.

Backend und Watcher sind getrennt, weil das Backend unprivilegiert läuft. Dürfte
es sich selbst ersetzen, wäre der Upload-Endpunkt gleichbedeutend mit
Codeausführung als root.

---

# Zeitzonen

**Jeder Zeitstempel im Programm trägt eine Zeitzone.** Das ist keine Kosmetik,
sondern eine Fehlerbremse.

Ein zeitzonenloser Wert sieht gleich aus, egal ob er UTC oder Ortszeit enthält.
Python vergleicht zwei solche Werte klaglos und rechnet mit dem Ergebnis weiter.
Genau daran ist der Update-Zeitplan gescheitert: `last_patch_run` stand in UTC,
der Termin wurde in Ortszeit berechnet, und die zwei Stunden Differenz fielen
nirgends auf. Mit Zeitzone wirft derselbe Vergleich einen `TypeError` — der
Fehler tritt sofort auf, statt still ein falsches Ergebnis zu liefern.

Umgesetzt über einen eigenen Spaltentyp in `backend/utctime.py`. SQLite
verwirft die Zeitzone beim Schreiben, auch mit `DateTime(timezone=True)` —
deshalb wird beim Schreiben nach UTC umgerechnet und die Zeitzone beim Lesen
wieder angeheftet. Das Dateiformat bleibt unverändert, bestehende Datenbanken
laufen ohne Umschreiben weiter.

Ein zeitzonenloser Wert wird beim Schreiben **abgewiesen**, nicht stillschweigend
gedeutet.

Über die API gehen Zeitstempel mit ausdrücklicher Zeitzone raus, der Browser
rechnet sie in deine Ortszeit um.

**Zeitpläne dagegen sind Ortszeit.** Trägst du `03:00` ein, ist drei Uhr nachts
vor Ort gemeint — nicht UTC. Beim Vergleich mit gespeicherten Zeitstempeln wird
umgerechnet.

Bis 0.10.0 fehlte diese Umrechnung: `last_patch_run` stand in UTC, der Termin
wurde in Ortszeit berechnet und beides direkt verglichen. Auf einem Server mit
Zeitzone Europe/Berlin waren das zwei Stunden Versatz — ein Update-Zeitplan
konnte dadurch doppelt auslösen oder einen Termin überspringen. Ebenso wurden
Zeitstempel ohne Zeitzone ausgeliefert und in der Oberfläche zwei Stunden zu
früh angezeigt.

Der Server sollte auf der Zeitzone stehen, in der du die Zeitpläne denkst:

```
timedatectl set-timezone Europe/Berlin
```

---

# systemd-Units

Es liegen **keine `.service`-Dateien** im Projekt. Die Units werden erzeugt:

- `setup.sh` schreibt `co37-backend.service` und
  `co37-watcher.service` direkt nach `/etc/systemd/system/`, mit
  eingesetzten Pfaden, Ports und Schlüsseln
- `packaging/build_deb.py` bettet `co37-agent.service` als Zeichenkette
  ins Paket ein

Bis 0.11.0 lagen zusätzlich drei `.service`-Dateien im Projekt, die von nichts
gelesen wurden — eine zweite Wahrheit, in der Änderungen wirkungslos
verpufften. Sie sind entfernt.

Willst du eine Unit ändern, ändere sie an der Stelle, die sie erzeugt.

---

# Umgang mit Geheimnissen

| Wert | Ablage |
|---|---|
| Checkmk-Automation-Secret | Fernet-verschlüsselt in der Datenbank, Schlüssel aus `CO37_SECRET_KEY` |
| Agent-Tokens | nur als SHA-256-Hash; der Agent erzeugt sie bei der Anmeldung selbst |
| Admin-Token | Umgebungsvariable der systemd-Unit |

`secret.key` und `co37.db` gehören **nicht in dasselbe Backup-Ziel**.
Sonst ist die Trennung wirkungslos.

Datensicherung:

```
/opt/co37/data/co37.db
/opt/co37/data/secret.key
```

---

# Was das Agent-Token ist und was nicht

Es beantwortet die Frage „welcher Host bin ich?", nicht „darf ich rein?". Ohne
Token müsste das Backend Hosts am Namen erkennen — dann könnte jedes Gerät im
Netz behaupten, der Domaincontroller zu sein und dessen Aufträge abholen.

Der Agent erzeugt es bei der Anmeldung selbst, du siehst es nie.

Das eigentliche Risiko: wer den Server kontrolliert, kontrolliert alle Hosts,
weil dort überall ein privilegierter Prozess auf Aufträge wartet. Entschärfend
wirkt, dass das Auftragsprotokoll nur `scan`, `patch`, `reboot` und `selfupdate`
kennt. Es gibt keinen Auftragstyp „führe folgenden Befehl aus", und Parameter
gehen als Argumentliste an `subprocess`, nie durch eine Shell.

---

# Der Update-Watcher

Unter **Einstellungen → Update** steht, welche Fassung des Watchers läuft.
Meldet er sich nicht oder ist er älter als das System, steht dort der Grund.

**Das ist wichtiger, als es klingt.** Ein Watcher aus einer Fassung vor 0.4.3
tauscht beim Update nur `backend/`, `frontend/` und `agent/` aus. Alles auf
oberster Ebene bleibt liegen — `build_packages.sh`, `setup.sh` und der Watcher
selbst. Er kann sich also aus eigener Kraft nie erneuern, und Korrekturen an
den Hilfsskripten erreichen den Betrieb nie. Von außen sieht es so aus, als
hätten die Updates keine Wirkung.

Einmalig von Hand auflösen:

```
systemctl stop co37-watcher
```

```
cd /opt/co37 && unzip -o -j /tmp/co37_vX_Y_Z.zip update_watcher.py build_packages.sh build_release.sh setup.sh -d /opt/co37
```

```
chmod +x /opt/co37/*.sh && systemctl start co37-watcher
```

Danach prüfen:

```
curl -s -H "X-API-Key: DEIN-TOKEN" localhost:8080/api/v1/watcher
```

---

# Wenn nach einem Update Fehler auftreten

Erste Anlaufstelle:

```
journalctl -u co37-backend -n 60 --no-pager
```

Schema prüfen:

```
curl -s localhost:8080/api/health
```

Liefert das `503` mit `schema_mismatch`, passt die Datenbank nicht zum
Programmstand. Ab 0.4.1 wird das Schema beim Start automatisch angeglichen —
tritt der Fall trotzdem auf, den Bericht abrufen:

```
curl -s -H "X-API-Key: DEIN-TOKEN" localhost:8080/api/v1/schema
```

Notfalls Datenbank beiseitelegen. Alle Hosts melden sich danach neu an und
warten auf Freigabe:

```
systemctl stop co37-backend
```

```
mv /opt/co37/data/co37.db /opt/co37/data/co37.db.alt
```

```
systemctl start co37-backend
```

Wird ein Agent-Token vom Server abgelehnt, verwirft der Agent es nach zwei
Versuchen selbst und meldet sich neu an. Der Host erscheint dann wieder als
*wartet auf Freigabe*. Ein Eingriff auf dem Zielsystem ist nicht nötig.

Watcher-Protokoll bei fehlgeschlagenem Update:

```
journalctl -u co37-watcher -n 40 --no-pager
```

---

# Betrieb über HTTP: was das bedeutet

CO-37 läuft im lokalen Netz ohne TLS. Zwei Auswirkungen, die im Browser
sichtbar werden:

Die Zwischenablage-Schnittstelle `navigator.clipboard` steht nur in einem
sicheren Kontext bereit — HTTPS oder `localhost`. Über `http://<ip>:8080` gibt
es sie nicht. Die Kopierknöpfe nutzen daher einen Rückfallweg über ein
verstecktes Textfeld, der auch ohne TLS funktioniert.

Der Admin-Token geht unverschlüsselt über das Netz. Für ein internes Netz
vertretbar, aber der Token gehört getauscht, sobald das System über WireGuard
oder einen Reverse Proxy erreichbar wird.

---

# Tests

Für das Frontend liegt ein Testgerüst unter `tests/` bereit, das das echte
Skript in Node gegen ein laufendes Backend ausführt. Siehe `tests/README.md`.

---

# Offene Punkte

- Agent- und Update-Pakete sind unsigniert
- Keine Token-Rotation: ein kompromittiertes Agent-Token gilt, bis der Host
  entfernt wird
- Ein einziger Admin-Token, keine Rollen oder Mehrbenutzerbetrieb
- Der Agent fragt beim Server nach (15 Sekunden im Ruhezustand, 5 Sekunden
  während Aufträge laufen). Der Server kann nichts von sich aus zustellen.
  Long Polling wäre der nächste Schritt für sofortige Zustellung — ein
  lauschender Dienst auf jedem Host wäre die schlechtere Lösung.
- Prüfen wird nicht nach Zeitplan ausgelöst, nur Patchen und Neustarts.
- Aufträge von vor 0.6.0 haben kein Protokoll. Dort bleibt die Ansicht leer.
- Das Frontend greift auf Formularfelder über den Element-Namen zu
  (`cUrl.value`). Browser stellen das bereit, es ist aber empfindlich
  gegenüber Namensgleichheit mit Variablen.
- Kein Rollback von Patches, kein Ausschluss einzelner Updates
- Die Schema-Migration ergänzt fehlende Spalten, entfernt aber keine alten.
  Ungenutzte Felder aus früheren Fassungen bleiben in der Datenbank stehen.
