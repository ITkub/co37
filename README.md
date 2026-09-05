# CO-37 — Core Operations Manager

Patch-Management für Windows- und Linux-Systeme mit Checkmk-Anbindung.
Selbst gehostet, agentenbasiert, ohne Cloud-Anbindung.

Vorgesehen für den Betrieb **im lokalen Netz**. Ein Server je Netz. TLS über
einen vorgeschalteten Reverse Proxy — siehe `REVERSE-PROXY.md`.

**Bis zu 10 Hosts kostenlos**, auch geschäftlich. Darüber hinaus wird eine
kommerzielle Lizenz benötigt: siehe [Lizenz](#lizenz).

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

Das Skript legt Benutzer, venv, systemd-Dienste und Schlüssel an. Am Ende wird
die **Adresse** ausgegeben. Anmeldung beim ersten Mal mit `admin` / `admin`.

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

Für Windows wird `wixl` gebraucht. Das Paket heißt **wixl**, nicht `msitools`.
`msitools` gehört trotzdem dazu: es bringt `msiinfo`, und damit prüft der Bau
das fertige MSI gegen sich selbst. Ohne das Paket wird gebaut, aber nicht
geprüft — und der Bau sagt das auch:

```
apt install -y wixl msitools
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
von Hand dort hinterlegen. **Welche Fassung, steht in `packaging/build_msi.py`
unter `PY_VERSION`** — dort nachsehen statt hier: an dieser Stelle stand der
Dateiname fest eingetragen und war seit 0.37.4 falsch. Wer der Anleitung
folgte, legte genau die Fassung ab, die F-15 loswerden sollte. Dieselbe
Fehlerart wie in `i18n.js` (0.37.0) und bei den Pins des Agents (F-14): ein
Wert an zwei Stellen, von denen nur eine wirkt.

```
grep PY_VERSION packaging/build_msi.py
```

### 1.8 Oberfläche öffnen

```
http://192.168.1.10:8080
```

Anmeldung beim ersten Mal mit **`admin` / `admin`**. Das Passwort gehört
danach unter *Einstellungen → Konto* geändert.

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

Ab Checkmk 2.4 existiert **kein** Benutzer `automation` mehr von Haus aus.

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
ein frisch erzeugtes **Installations-Token** bereits eingesetzt. Das Token läuft
nach 15 Minuten ab und gilt für wenige Abrufe — was davon in der Verlaufsdatei
des Zielsystems zurückbleibt, ist dann wertlos. Deshalb erst kurz vor der
Einrichtung erzeugen.


### 3.2 Auf dem Zielsystem ausführen

Als root, Beispiel:

```
curl -fsSL -H "X-Install-Token: DEIN-INSTALL-TOKEN" http://192.168.1.10:8080/api/v1/packages/co37-agent_VERSION_all.deb -o /tmp/co37-agent.deb && CO37_SERVER="http://192.168.1.10:8080" apt-get install -y --allow-downgrades /tmp/co37-agent.deb
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

**Über eine bestehende Installation** läuft dasselbe Kommando — das Paket
entfernt die alte Fassung selbst und legt die geplante Aufgabe neu an. Die
`agent.conf` unter `C:\ProgramData\CO37` bleibt dabei stehen, der Host behält
also sein Token und muss nicht erneut freigegeben werden.

> **Paket vor 0.36.18?** Dann bricht ein Upgrade mit **Fehler 2753** oder
> **1721** ab und setzt sich vollständig zurück — die alte Fassung läuft danach
> unverändert weiter.
>
> Ursache: `wixl` baut das MSI unter Linux und liest die Windows-Versionsangabe
> aus `python.exe` und den übrigen Binärdateien nicht aus. In der Dateitabelle
> des Pakets blieb die Spalte *Version* leer. Für Windows Installer schlägt
> damit jede Datei auf der Platte, die eine Version hat, die gleichnamige im
> Paket, die keine hat — er überspringt die 30 Binärdateien der mitgelieferten
> Python-Umgebung. Danach entfernt er die alte Fassung und löscht dabei genau
> diese Dateien. Zurückgelegt hat sie niemand.
>
> Der Abbruch war dabei der Glücksfall: er rollte alles zurück. Ohne ihn wäre
> ein Agent ohne Python-Laufzeit übriggeblieben — eine Installation, die sauber
> aussieht und nie wieder startet.
>
> Behoben in 0.36.18. Mit einem älteren Paket bleibt nur: erst in
> *Apps & Features* deinstallieren, dann neu installieren. Das funktioniert
> zuverlässig — betroffen ist nur der Weg über eine bestehende Installation.
> Die `agent.conf` unter `C:\ProgramData\CO37` überlebt das Deinstallieren,
> der Host behält sein Token.

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

Begrenzt ist dagegen die **Menge**. Zwei Schranken, seit 0.36.14:

* Höchstens **50 Anmeldungen je Absender-Adresse in 15 Minuten**. Wer darüber
  kommt, bekommt `429` und einen `Retry-After`.
* Höchstens **100 Hosts gleichzeitig in „wartet auf Freigabe"**. Ist die Liste
  voll, wird keine neue Zeile mehr angelegt, bis du freigibst oder entfernst.

Ohne das konnte jeder, der die Route erreicht, in einer Schleife beliebig viele
Hostzeilen anlegen — der eine echte neue Host wäre in der Freigabeliste
untergegangen. Ein bereits bekannter Host, dessen Token du zurückgezogen hast,
kommt auch bei voller Liste noch durch: er legt nichts Neues an.

Wenn du wirklich mehr als 100 Hosts auf einmal ausrollst, gib zwischendurch
frei. Die Grenzen stehen in `backend/main.py` unter *Drosselung der
Agent-Anmeldung*.

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

Der Agent lädt den Code, **prüft die Signatur**, prüft die SHA-256-Summe, prüft
die Syntax, legt die alte Fassung als `.bak` daneben und startet neu. Schlägt
eine der Prüfungen fehl, bleibt die alte Fassung aktiv.

## Warum der Agent-Code signiert wird

Der Agent ersetzt damit die Datei, die er als `root` beziehungsweise als
`SYSTEM` ausführt. Die SHA-256-Summe steht in **derselben Antwort** wie der
Code — wer die Antwort fälschen kann, fälscht beide. Sie schützt gegen einen
abgebrochenen Download, nicht gegen Manipulation. Das ist dieselbe Überlegung,
die beim Update-Paket zur Signatur geführt hat.

Deshalb signiert `build_release.py` beim Bauen auch `agent/agent.py` und legt
`agent/agent.py.sig` daneben. Der Agent prüft sie gegen `release_key.pub`, der
mit dem `.deb` beziehungsweise `.msi` ausgeliefert wird und neben `agent.py`
liegt.

Die Regel ist dieselbe wie im Backend: **liegt der Schlüssel vor, ist die
Signatur Pflicht.** Liegt er nicht vor, wird nicht geprüft — das ist der
Zustand eines Quelltextes, aus dem sich jemand selbst baut.

> **Beim Übergang wichtig.** Ein Agent, der sich selbst aktualisiert, tauscht
> nur `agent.py` aus. Den Schlüssel bekommt er dabei **nie**. Auf einem
> bestehenden Host beginnt die Prüfung also erst, wenn dort das `.deb` oder
> `.msi` neu installiert wurde. Bis dahin läuft er wie bisher weiter — er ist
> nicht schlechter dran als vorher, aber auch nicht besser. Wer die Prüfung
> überall haben will, rollt die Agent-Pakete einmal neu aus.

Umgekehrt gilt: ein Agent **mit** Schlüssel gegen einen Server **ohne**
Signatur im Paket lehnt die Selbstaktualisierung ab und sagt das auch. Der
Server ist dann älter als die Signaturpflicht und muss zuerst aktualisiert
werden.

Für Linux braucht der Agent dafür `python3-cryptography`; das Paket setzt es
als Abhängigkeit. Unter Windows liegt die Bibliothek im MSI (dadurch wächst es
um gut 10 MB).

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

Unter **Einstellungen → Update** hochladen. Das Backend prüft Signatur und
ZIP-Struktur und stellt bereit, führt aber nichts aus. Erst nach Bestätigung
übernimmt `update_watcher.py` als root:

1. **Signatur erneut prüfen** — stimmt sie nicht, endet es hier
2. aktuellen Stand nach `update_backups/<zeitstempel>` sichern
3. `backend/`, `frontend/` und `agent/` austauschen
4. `pip install -r requirements.txt`
5. Dienst neu starten
6. `/api/health` abfragen — schlägt das fehl, wird zurückgerollt

Ausgetauscht werden `backend/`, `frontend/`, `agent/`, `packaging/`, `tests/`
sowie die Skripte auf oberster Ebene (`setup.sh`, `build_packages.sh`,
`build_release.sh`) und der Watcher selbst.

`build_release.sh` prüft beim Bauen, dass alles aus `MANAGED_FILES` auch
wirklich im Paket liegt, und bricht sonst ab. Eine fehlende Datei würde der
Watcher stillschweigend überspringen — ihre Korrekturen erreichten den Betrieb
dann nie. `data/` wird nie angefasst. Die letzten drei
Sicherungen bleiben.

Backend und Watcher sind getrennt, weil das Backend unprivilegiert läuft. Dürfte
es sich selbst ersetzen, wäre der Upload-Endpunkt gleichbedeutend mit
Codeausführung als root.

## Warum die Signatur zweimal geprüft wird

Einmal im Backend beim Hochladen, ein zweites Mal im Watcher vor dem
Auspacken. Das ist keine Verdopplung aus Versehen.

Das Backend läuft unprivilegiert. `incoming.zip` liegt unter `data/` — dem
einzigen Verzeichnis, in das es schreiben darf. Wer Code als `co37`
ausführt, umgeht damit die gesamte Anwendungslogik: eigenes Paket
hinlegen, `status.json` auf `triggered` setzen, fertig. Ohne die zweite
Prüfung packt der Watcher es als root aus — und genau die Trennung, für
die es ihn gibt, wäre keine mehr.

Die Regel lautet deshalb: **der Watcher verlässt sich auf keine Prüfung,
die jenseits der Rechtegrenze stattgefunden hat.** Geprüft wird mit
derselben Funktion wie im Backend (`backend/release_sig.py`), nicht mit
einer zweiten Kopie — das Verzeichnis gehört root, `co37` kann es nicht
umschreiben.

Der Watcher läuft mit dem System-Python, nicht mit dem venv: er muss auch
dann noch zurückrollen können, wenn ein misslungenes Update das venv
zerlegt hat. Dafür braucht er **`python3-cryptography`** als
Systempaket. `setup.sh` installiert es mit. Fehlt es, wird ein Update
**abgewiesen** statt ungeprüft eingespielt — die Meldung nennt den Befehl.

Ohne ausgelieferten `release_key.pub` wird nicht geprüft. Das ist der
Zustand eines selbst gebauten Quellstands, und es ist dieselbe Regel wie
im Backend.

## Wem die Dateien gehören

`/opt/co37` gehört **root**, nur `/opt/co37/data` gehört `co37`.

Der Watcher führt als root Dateien aus diesem Verzeichnis aus: sich
selbst, `build_packages.sh`, `pip` aus dem venv. Gehörten die `co37`,
könnte jeder, der Code als `co37` ausführt, sie austauschen und wäre
damit root — ganz ohne Update-Mechanik.

Der Backend-Prozess verliert dadurch nichts: durch `ProtectSystem=strict`
und `ReadWritePaths` darf er ohnehin nur nach `data/` schreiben. Lesen und
Ausführen bleibt über die Modusbits erhalten.

`setup.sh` setzt das, und der Watcher stellt es nach jedem Austausch und
nach jeder Rückrollung wieder her. Wichtig ist das Zweite: täte er es
nicht, hübe das nächste Update die Trennung wieder auf.

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
| Benutzerpasswörter | scrypt-Hash mit eigenem Salt je Konto |
| `CO37_SECRET_KEY` | `/etc/co37/backend.env`, root:root mit 600 — plus `data/secret.key`, damit `setup.sh` ihn über Neuinstallationen hinweg wiederfindet |

**Nicht in der Unit-Datei.** `/etc/systemd/system/co37-backend.service`
entsteht mit den Vorgaberechten und ist damit für jeden lokalen Benutzer
lesbar; `systemctl show` gibt `Environment=`-Zeilen ohnehin im Klartext
aus. `CO37_SECRET_KEY` entschlüsselt das Checkmk-Secret in der Datenbank
und steht deshalb in einer `EnvironmentFile`, deren Inhalt systemd nicht
anzeigt. Pfade und Intervalle bleiben als `Environment=` in der Unit —
`systemctl cat co37-backend` soll weiter zum Nachsehen taugen, und so ist
auf einen Blick klar, welcher Wert geheim ist.

Bewusst **nicht** unter `data/`: das gehört `co37` und ist für den
Backend-Prozess schreibbar.

**Es gibt keinen globalen Admin-Token mehr.** Bis 0.36.11 gab es einen —
aus der Zeit vor den Benutzerkonten. Er lief nie ab, galt auf jeder Route
und ließ sich, anders als das Passwort, unbegrenzt durchprobieren; die
Drosselung sitzt nur an der Anmelderoute. Entfallen in 0.36.12. Wer sich
aussperrt, kommt über die Datenbank zurück — siehe *Wenn du dich
aussperrst*.

Was bleibt: `/proc/<pid>/environ`, lesbar für root und für `co37` selbst.
Das ist unvermeidbar — `co37` hält den Schlüssel ohnehin.

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

Es steht in `agent.conf` — unter Linux in `/etc/co37/agent.conf` mit `600` und
`root` als Eigentümer, unter Windows in `C:\ProgramData\CO37\agent.conf`. Dort
gilt seit 0.36.14 dasselbe: die Vererbung ist abgeschnitten, Zugriff haben nur
`SYSTEM` und die lokale Administratorengruppe. Vorher erbte die Datei von
`C:\ProgramData` ein Leserecht für **Benutzer** — jedes Konto auf dem Rechner
konnte das Token lesen. Nachsehen mit:

```
icacls C:\ProgramData\CO37\agent.conf
```

Erwartet werden genau zwei Einträge (`NT-AUTORITÄT\SYSTEM` und die
Administratorengruppe), beide mit `(F)`, und kein `(I)` davor — das `I` stünde
für „geerbt".

Das eigentliche Risiko: wer den Server kontrolliert, kontrolliert alle Hosts,
weil dort überall ein privilegierter Prozess auf Aufträge wartet. Entschärfend
wirkt, dass das Auftragsprotokoll nur `scan`, `patch`, `reboot` und `selfupdate`
kennt. Es gibt keinen Auftragstyp „führe folgenden Befehl aus", und Parameter
gehen als Argumentliste an `subprocess`, nie durch eine Shell.

---

# Der Update-Watcher

Unter **Einstellungen → Update** steht, welche Fassung des Watchers läuft.
Meldet er sich nicht oder ist er älter als das System, steht dort der Grund.

**Das ist wichtiger, als es klingt.** Ein zurückgebliebener Watcher tauscht
beim Update weniger aus, als er soll — im schlimmsten Fall nur `backend/`,
`frontend/` und `agent/`, während alles auf oberster Ebene liegen bleibt.
Korrekturen an den Hilfsskripten erreichen den Betrieb dann nie, und von
außen sieht es so aus, als hätten die Updates keine Wirkung.

Welche Fassung tatsächlich läuft, steht unter **Einstellungen → Update**.
Von der Kommandozeile aus braucht es eine Anmeldung:

```
S=$(curl -s -X POST -H "Content-Type: application/json" \
      -d '{"username":"admin","password":"DEIN-PASSWORT"}' \
      localhost:8080/api/v1/login | python3 -c 'import sys,json;print(json.load(sys.stdin)["session"])')
curl -s -H "X-Session: $S" localhost:8080/api/v1/watcher
```

Steht der HTTPS-Zwang, geht das über `localhost` nicht — dann denselben
Aufruf gegen die eingerichtete `https://`-Adresse richten.

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

Seit 0.36.14 stehen `version`, `agent_version` und `schema_version` in dieser
Antwort **nur noch für Aufrufer über `127.0.0.1` und für angemeldete
Benutzer**. Von einem anderen Rechner aus kommt nur `status` und
`default_language` zurück — die Route muss offen bleiben (der Watcher fragt sie
ab, die Anmeldeseite braucht die Vorgabesprache), aber sie muss nicht jedem
Unangemeldeten sagen, welche Fassung hier läuft. Der Befehl oben läuft **auf dem
Server**, dort siehst du alles.

Liefert das `503` mit `schema_mismatch`, passt die Datenbank nicht zum
Programmstand. Ab 0.4.1 wird das Schema beim Start automatisch angeglichen —
tritt der Fall trotzdem auf, den Bericht abrufen:

```
curl -s -H "X-Session: $S" localhost:8080/api/v1/schema
```

`$S` wie oben durch Anmelden besorgen.

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

# Prüfprotokoll

Unter **Einstellungen → Prüfprotokoll** steht, wer was wann getan hat —
Anmeldungen (auch die gescheiterten), Freigaben, Rollenänderungen,
Updates, Neustarts.

**Es wird nur angehängt.** Es gibt keine Route zum Ändern oder Löschen,
und zwar mit Absicht: wer sich selbst herausschreiben kann, macht das
Protokoll wertlos.

**Aufbewahrungsfrist: 365 Tage.** Ältere Einträge werden automatisch
entfernt, und die Bereinigung schreibt selbst eine Zeile ins Protokoll
(`audit.pruned`) mit Anzahl und Frist — eine Lücke, die man nicht sieht,
wäre eine unbemerkte Änderung am Protokoll.

Ändern lässt sich die Frist nur in `/etc/co37/backend.env`:

```
CO37_AUDIT_DAYS=365
```

`0` schaltet die Bereinigung ab. **Absichtlich nicht in der Oberfläche:**
BSI IT-Grundschutz OPS.1.1.5.A10 verlangt, dass Administrierende die
Protokolldaten nicht selbst löschen können. Eine Frist, die im Dashboard
auf einen Tag stellbar wäre, wäre genau diese Möglichkeit durch die
Hintertür — Frist runter, warten, Frist zurück. Für `backend.env` braucht
es root, und root ist hier bewusst jemand anderes als die
Administratorenrolle.

Die Länge der Felder ist begrenzt (Akteur 120, Aktion 80, Beschreibung
1000 Zeichen). Ohne das ließ sich das Protokoll ohne Zugangsdaten mit
einem überlangen Benutzernamen an der Anmeldung vollschreiben.

---

# Weiterleitung an eine zentrale Protokollierung

**Einstellungen → Protokollierung.** Ab Werk **aus**.

CO-37 schrieb bis 0.37.22 ausschließlich in die eigene Datenbank. Für ein
einzelnes kleines Netz reicht das; in einem Betrieb mit Logserver oder
SIEM ist es der Unterschied zwischen einsetzbar und nicht einsetzbar.
Zwei BSI-Bausteine verlangen es ausdrücklich — OPS.1.1.5.A6 und
OPS.1.1.7.A15.

| Feld | Bedeutung |
|---|---|
| Ziel, Port | Adresse des Logservers, Vorgabe 514 |
| Übertragung | `udp`, `tcp` oder `tls` |
| Facility | `local0` … `local7`, `auth`, `authpriv`, `daemon`, `user` |
| Zertifikat prüfen | nur bei `tls`; **an** lassen, außer der Logserver hat ein eigenes Zertifikat |

**Format ist RFC 5424**, nicht das ältere RFC 3164 — dem fehlen Jahr und
Zeitzone im Zeitstempel, und CO-37 arbeitet durchgängig in UTC. Bei `tcp`
und `tls` mit vorangestellter Länge nach RFC 6587, damit der Empfänger
weiß, wo eine Meldung endet.

```
<131>1 2026-09-05T12:00:00.000000Z kk-ops01 co37 - login.failed
  [co37@0 actor="mike" src="192.168.2.10"] Anmeldung fehlgeschlagen
```

Die Felder stehen einzeln in den strukturierten Daten, damit ein SIEM sie
ohne Textzerlegung findet.

**Was hinausgeht:** jeder Eintrag des Prüfprotokolls und jedes
abgeschlossene Auftragsereignis (erledigt, fehlgeschlagen, abgebrochen).
**Nicht** die Auftragsprotokolle selbst — ein `apt upgrade` erzeugt
tausende Zeilen je Host, das flutet jedes SIEM und kostet dort Geld nach
Datenvolumen. Die stehen weiterhin als Datei bereit.

**Der Knopf „Testen"** stellt eine Meldung sofort zu und sagt, ob sie
durchging. Das ist die einzige Stelle, an der synchron gesendet wird.

## Was passiert, wenn der Logserver weg ist

**Nichts, was du merkst.** Das ist keine Nebensache, sondern die Regel,
nach der das gebaut ist: Meldungen gehen in eine Warteschlange, ein
eigener Faden stellt zu. Der Aufrufer wartet nie.

Gemessen gegen einen Server, der absichtlich nicht antwortet: **4
Mikrosekunden je Meldung**. Ohne diese Trennung wären es 100 Millisekunden
— eine Anmeldung, die zwei Minuten braucht, weil ein Logserver neu
startet, wäre ein selbst gebautes Verfügbarkeitsproblem.

**Die Warteschlange ist auf 5000 Einträge begrenzt und wirft bei Überlauf
weg.** Bewusst so: eine unbegrenzte Warteschlange frisst bei einem
tagelang toten Logserver den Arbeitsspeicher und nimmt das Backend mit.
Ein verlorener Protokolleintrag ist ärgerlich, ein Backend, das wegen der
Protokollierung stirbt, ist ein Ausfall.

Wie viele verloren gingen, wird gezählt und beim nächsten erfolgreichen
Kontakt **selbst gemeldet** (`queue.dropped`). Eine stille Lücke wäre das
Schlechteste von beidem. Der Zähler steht auch in der Oberfläche.

**Das Prüfprotokoll in der Datenbank bleibt die Wahrheit**, die
Weiterleitung ist die Kopie. Geht die Kopie schief, ändert das am
Eintrag nichts.

---

# Wenn du dich aussperrst

Drei Fälle: das einzige Administratorkonto ist deaktiviert, das Passwort ist
weg, oder der HTTPS-Zwang steht auf einem Proxy, der nicht mehr antwortet.

**Warte erst einmal ab.** Der HTTPS-Zwang schaltet sich nach 15 Minuten selbst
ab, wenn sich in dieser Zeit niemand über HTTPS angemeldet hat. Genau dafür
gibt es die Frist — ein falsch eingetragener Proxy soll nicht dauerhaft
aussperren. Der Vorgang steht danach im Prüfprotokoll.

Hilft das nicht, gibt es den Weg über die Datenbank. Er braucht **root auf dem
Server** — was angemessen ist: wer sich aussperrt, muss sich ausweisen können,
und die Shell ist der Ausweis.

### HTTPS-Zwang abschalten

```
systemctl stop co37-backend
```

```
sqlite3 /opt/co37/data/co37.db "update setting set value='false' where key='https_only';"
```

```
systemctl start co37-backend
```

Danach ist die Oberfläche wieder unverschlüsselt erreichbar. Proxy richten,
Zwang neu einschalten.

### Passwort zurücksetzen und Konto reaktivieren

Setzt das Passwort von `admin` neu und hebt eine Deaktivierung auf. Das
Passwort im Befehl ersetzen:

```
systemctl stop co37-backend
```

```
cd /opt/co37/backend && CO37_DB="sqlite:////opt/co37/data/co37.db" CO37_DATA=/opt/co37/data /opt/co37/venv/bin/python3 - <<'EOF'
import main
from models import User, Role
from sqlmodel import Session, select
with Session(main.engine) as s:
    u = s.exec(select(User).where(User.username == "admin")).first()
    u.password_hash = main.hash_password("NEUES-PASSWORT")
    u.disabled = False
    u.role = Role.admin
    s.add(u); s.commit()
    print("zurueckgesetzt")
EOF
```

```
chown co37:co37 /opt/co37/data/co37.db* && systemctl start co37-backend
```

Das `chown` am Ende, weil die Befehle als root laufen: legt SQLite dabei eine
Journaldatei an, gehörte sie sonst root, und das Backend läuft als `co37`.

Der Umweg über das Programm statt über `sqlite3` ist Absicht — das Passwort
wird mit scrypt gehasht, und der Hash entsteht hier mit derselben Funktion,
die auch die Anmeldung prüft. Von Hand gebaut wäre er ein zweiter Weg, der
irgendwann auseinanderläuft.

---

# Betrieb über HTTP: was das bedeutet

CO-37 läuft im lokalen Netz ohne TLS. Zwei Auswirkungen, die im Browser
sichtbar werden:

Die Zwischenablage-Schnittstelle `navigator.clipboard` steht nur in einem
sicheren Kontext bereit — HTTPS oder `localhost`. Über `http://<ip>:8080` gibt
es sie nicht. Die Kopierknöpfe nutzen daher einen Rückfallweg über ein
verstecktes Textfeld, der auch ohne TLS funktioniert.

Anmeldedaten und Sitzungscookie gehen unverschlüsselt über das Netz. Für ein
internes Netz vertretbar, aber sobald das System über WireGuard oder einen
Reverse Proxy erreichbar wird, gehört TLS davor — siehe `REVERSE-PROXY.md`.


---

# Tests

Für das Frontend liegt ein Testgerüst unter `tests/` bereit, das das echte
Skript in Node gegen ein laufendes Backend ausführt. Siehe `tests/README.md`.

---

# Best Practices

Sammlung von Empfehlungen für den Betrieb. Wächst mit der Zeit.

## Bereiche (Areas)

Bereiche gruppieren Hosts in der Übersicht — **freiwillig**, nichts zwingt
dazu. Ein Host gehört höchstens einem Bereich an.

**Wann sich ein Bereich lohnt:** wenn mehrere Hosts dasselbe Wartungsfenster
teilen sollen oder ohnehin zusammengehören — etwa Cluster-Knoten, bei denen
ein Ausfall dieselben Checkmk-Objekte betrifft. Für einzelne, unabhängige
Hosts reicht der Zeitplan am Host selbst.

**Eigener Zeitplan, keine Vererbung.** Ein Bereich bekommt einen eigenen
Sammel-Zeitplan und eine eigene Downtime, unabhängig vom Zeitplan seiner
Hosts. Hat ein enthaltener Host bereits einen eigenen Zeitplan, blockiert das
Speichern nicht — nur eine Warnung, dass sich beide überschneiden könnten.
Entweder den Host-Zeitplan entfernen oder bewusst beides parallel laufen
lassen.

**Check/Patch/Restart wirkt auf den ganzen Bereich.** Über den Bereichs-Kopf
lässt sich der gesamte Bereich auf einmal prüfen, patchen oder neu starten —
praktisch für Gruppen, die ohnehin gemeinsam behandelt werden sollen.

**Zuordnen per Drag & Drop** in der Hostliste: auf einen Bereichs-Kopf
gezogen tritt der Host bei, auf einen bereichslosen Host gezogen verlässt er
seinen Bereich wieder.

**Löschen nur, wenn leer.** Ein Bereich mit Hosts lässt sich nicht löschen —
erst die Hosts herausziehen.

---

# Offene Punkte

- Agent- und Update-Pakete sind unsigniert. Wer Schreibzugriff auf das
  Paketverzeichnis hat, kann Code unterschieben.
- Prüfen wird nicht nach Zeitplan ausgelöst, nur Patchen und Neustarts.
- Kein Rollback von Patches, kein Ausschluss einzelner Updates.
- Der Agent fragt beim Server nach — Abstand über `CO37_POLL_INTERVAL`
  einstellbar, Vorgabe 60 Sekunden, während laufender Aufträge 5 Sekunden.
  Der Server kann nichts von sich aus zustellen. Long Polling wäre der
  nächste Schritt für sofortige Zustellung; ein lauschender Dienst auf jedem
  Host wäre die schlechtere Lösung.
- Die Schema-Migration ergänzt fehlende Spalten, entfernt aber keine alten.
  Ungenutzte Felder aus früheren Fassungen bleiben in der Datenbank stehen.
- Aufträge von vor 0.6.0 haben kein Protokoll. Dort bleibt die Ansicht leer.
- Das Frontend greift an einigen Stellen über den Element-Namen auf
  Formularfelder zu (`cUrl.value`). Browser stellen das bereit, es ist aber
  empfindlich gegenüber Namensgleichheit mit Variablen.

## Bewusste Festlegungen

Keine Versäumnisse, sondern Entscheidungen — hier festgehalten, damit sie
nicht bei jedem Durchsehen neu diskutiert werden.

- **Downtimes werden nicht vorzeitig aufgehoben**, sie laufen ihre Dauer ab.
  Früher aufzuheben hieße, sich auf den Agent als Zeugen zu verlassen: der
  meldet sich Sekunden nach dem Start, während Checkmk den Host noch nicht
  neu geprüft hat und Dienste erst hochlaufen. Die Benachrichtigungen gingen
  dann trotzdem raus. Gesteuert wird über `downtime_minutes` je Host.
- **`style-src 'unsafe-inline'`** bleibt in der Content-Security-Policy. Das
  Markup ist voller `style`-Attribute; ein Umbau wäre umfangreich ohne
  echten Gewinn, da eingeschleustes CSS mit den übrigen Regeln wenig
  anrichtet. `script-src` kommt dagegen ohne aus.
- **Kein Hintergrunddienst.** Zeitpläne, Nachschläge und das Aufräumen
  hängender Aufträge werden beim Heartbeat ausgewertet. So kann nichts
  auseinanderlaufen.
- **Der HTTPS-Zwang schließt den Port nicht.** Er antwortet mit 403.
  Wirklich zu ist er erst mit einer Firewallregel — die gehört zum Rechner,
  nicht in diese Anwendung, und dafür bräuchte das Backend Rechte, die es
  bewusst nicht hat.

---

# Lizenz

CO-37 steht unter der **Business Source License 1.1**. Der vollständige Text
liegt in [`LICENSE`](LICENSE).

Das ist **kein Open Source** im Sinne der OSI-Definition — der Quelltext ist
einsehbar und veränderbar, die produktive Nutzung aber begrenzt.

## Kostenlos

Bis zu **10 Hosts je Installation**, ohne zeitliche Begrenzung, auch
geschäftlich. Gezählt werden freigegebene Hosts; wartende, abgelehnte und
gelöschte zählen nicht.

Damit sind Homelabs und kleine Umgebungen abgedeckt, und wer mehr Hosts hat,
kann CO-37 in Ruhe ausprobieren.

## Kommerzielle Lizenz

Ab 11 Hosts. Der Schlüssel legt fest, wie viele Hosts freigeschaltet sind und
wie lange er gilt — wahlweise ein, zwei oder drei Jahre.

Anfragen an **sales@itkub.de**.

Der Schlüssel wird unter *Einstellungen → Lizenz* eingetragen. Er enthält
Kunde, Hostzahl und Laufzeit im Klartext und ist signiert — verändern
lässt er sich nicht, einsehen schon. Geprüft wird lokal, ohne Rückruf und
ohne Internetverbindung.

Läuft ein Schlüssel ab, werden bereits freigegebene Hosts **weiterhin
gepatcht**. Lediglich neue Freigaben oberhalb von 10 sind dann nicht mehr
möglich. Ein Patch-Management-Werkzeug, das wegen einer Lizenzfrage Systeme
ungepatcht lässt, wäre das Gegenteil dessen, wofür es gekauft wurde.

## Umwandlung in eine freie Lizenz

Jede Fassung wird spätestens vier Jahre nach ihrer Veröffentlichung
automatisch unter der Apache License 2.0 verfügbar. Das ist Bestandteil der
Business Source License und nicht widerrufbar.

---

# Kontakt

**Michael Kuban — ITkub**
Aichacher Str. 9
86573 Obergriesbach
Germany

- Lizenzen und Vertrieb: sales@itkub.de
- Fehler und Vorschläge: über die Issues dieses Repositories

CO-37 wird von einer Person entwickelt. Antwortzeiten richten sich danach.
