# CO-37 hinter einem Reverse Proxy

Anleitung für den Betrieb mit TLS über einen vorgeschalteten Proxy
(getestet gegen die Vorbereitung in CO-37, nicht gegen NPMplus selbst).

Ab Version 0.25.0 ist CO-37 dafür vorbereitet. Alles wird in der
Oberfläche eingestellt — am Server selbst ist nichts zu ändern und kein
Neustart nötig.

---

## 1. Warum das nötig ist

Ohne Vorbereitung sieht das Backend hinter einem Proxy nur noch **eine**
Absenderadresse: die des Proxy. Das trifft drei Stellen:

- **Die Drosselung der Anmeldung** zählt pro Absender. Bei einer
  gemeinsamen Adresse sperrt der fünfte Fehlversuch von irgendwem
  **alle** aus.
- **Prüfprotokoll und `enrolled_from_ip`** zeigen nur noch den Proxy. Wer
  sich von wo angemeldet hat, ist nicht mehr nachvollziehbar.
- **Der Installationsbefehl** entsteht aus der aufgerufenen Adresse und
  stünde auf dem internen Port statt auf dem DNS-Namen.

Gelöst wird das über `--proxy-headers` und die Angabe, welcher Adresse
dabei geglaubt wird.

---

## 2. Proxy eintragen

**Einstellungen → Zugang → Vertrauenswürdiger Proxy.** Adresse eintragen,
speichern. Mehrere mit Komma trennen.

Die Änderung greift sofort, ein Neustart des Dienstes ist nicht nötig.

Darunter steht, von welcher Adresse die aktuelle Sitzung kommt und ob sie
als verschlüsselt gilt. Solange dort **unverschlüsselt** steht, ist der
Eintrag noch nicht wirksam — dann stimmt entweder die Adresse nicht, oder
der Proxy setzt `X-Forwarded-Proto` nicht.

**Die Einschränkung auf genau diese Adresse ist wesentlich.** Ohne sie
könnte jeder `X-Forwarded-For` selbst setzen und die Drosselung der
Anmeldung mit erfundenen Absendern umgehen. Ein zu weit gefasster Eintrag
ist deshalb ein Risiko und steht im Prüfprotokoll.

### Verschlüsselt nur von dieser Adresse

Unabhängig vom Schalter in Abschnitt 5 gilt: **eine Anfrage, die HTTPS
behauptet, aber nicht vom eingetragenen Proxy kommt, wird abgewiesen.**
CO-37 nimmt selbst kein TLS entgegen — die Behauptung wäre unbelegt, und
sie stillschweigend als unverschlüsselt zu behandeln, würde einen
falschen Eintrag verdecken.

Unverschlüsselt bleibt erreichbar, bis der Schalter gesetzt wird. Sonst
wäre das System nach einem Update für jeden unerreichbar, der noch keinen
Proxy eingetragen hat.

**Der Weg zurück** bei falschem Eintrag oder geänderter Proxy-Adresse ist
immer der unverschlüsselte Zugang direkt auf dem internen Port. Über den
Proxy kommt man dann nicht mehr hinein — auch nicht, um es zu
korrigieren. Vor dem Schließen von Port 8080 (Abschnitt 5) sollte der
Eintrag also nachweislich stimmen.

## 3. Proxy konfigurieren

**Ziel:** `http://<CO-37-Server>:8080`

Nötig ist, dass der Proxy diese Kopfzeilen setzt:

| Kopfzeile | Wert |
|---|---|
| `X-Forwarded-For` | Adresse des ursprünglichen Aufrufers |
| `X-Forwarded-Proto` | `https` |
| `Host` | der DNS-Name, unverändert |

Die meisten Proxys tun das von sich aus. Nach dem Einrichten prüfen —
siehe Abschnitt 6.

**Nicht nötig:** WebSocket-Unterstützung. CO-37 fragt zyklisch ab.

**Größenbegrenzung für Anfragen:** Systemupdates werden als ZIP
hochgeladen, derzeit rund 450 KB. Eine Grenze unterhalb von etwa 10 MB
sollte angehoben werden.

**Zeitüberschreitung:** Die längste Anfrage ist die Freigabe eines
Neustarts, die dabei eine Downtime in Checkmk setzt. Sechzig Sekunden
genügen, bei sehr vielen Checkmk-Hosts eher hundertzwanzig.

---

## 4. Zertifikat

Zeigt der DNS-Eintrag auf eine private Adresse, ist die HTTP-Challenge
nicht möglich — Let's Encrypt erreicht den Server von außen nicht. In dem
Fall die **DNS-Challenge** verwenden. Dafür braucht der Proxy die
API-Zugangsdaten des DNS-Anbieters.

Der Vorteil gegenüber einem selbstsignierten Zertifikat oder einer
eigenen CA: das Zertifikat ist überall vertraut. Auf den Agents ist
nichts zu verteilen, unter Windows wie Linux.

---

## 5. Zugang auf HTTPS beschränken

**Erst wenn alle Agents umgestellt sind** — siehe Abschnitt 7. Der
Schalter trifft nicht nur den Browser, sondern jeden Agent.

**Einstellungen → Zugang → Nur über HTTPS.** Darüber steht, wie viele
Agents sich bereits verschlüsselt melden und welche noch nicht. Erst
umschalten, wenn dort alle stehen.

Danach werden alle Anfragen abgewiesen, die nicht verschlüsselt über den
eingetragenen Proxy kamen. Der direkte Zugriff auf Port 8080 antwortet
nur noch mit 403.

**Rückfall:** Meldet sich innerhalb von 15 Minuten niemand über HTTPS an,
stellt sich die Einschränkung von selbst zurück. Das ist der Schutz gegen
einen falsch eingetragenen Proxy — sonst käme man nirgends mehr hinein,
auch nicht, um die Einschränkung wieder abzuschalten. Nach der ersten
erfolgreichen Anmeldung über HTTPS gilt sie als bestätigt und bleibt.

Ausgenommen bleibt eine einzige Route: `/api/health` über `127.0.0.1`.
Der Watcher prüft darüber nach einem Systemupdate, ob das Backend läuft;
ohne die Ausnahme hielte er ein eingespieltes Update für fehlgeschlagen
und spielte die Sicherung zurück.

### Notzugang, wenn der Proxy ausfällt

Ist der Haken gesetzt und der Proxy fällt aus, ist die Oberfläche über
den Browser nicht mehr erreichbar — auch nicht, um den Haken wieder zu
entfernen. **Das ist kein Aussperren, aber du musst wissen, wie du
zurückkommst.**

Auf dem CO-37-Server, mit Zugang zur Maschine (notfalls über die Konsole
des Virtualisierers):

```bash
/opt/co37/venv/bin/python <<'EOF'
import sqlite3
c = sqlite3.connect("/opt/co37/data/co37.db")
c.execute("UPDATE setting SET value='false' WHERE key='https_only'")
c.commit()
EOF

systemctl restart co37-backend
```

Der Neustart ist nötig: der Wert wird im Arbeitsspeicher gehalten, damit
er nicht bei jeder einzelnen Anfrage aus der Datenbank gelesen werden
muss.

Der Python aus dem venv ist immer vorhanden; `sqlite3` als eigenes
Programm ist es nicht überall.

Als Einzeiler, wenn kein Heredoc möglich ist:

```bash
/opt/co37/venv/bin/python -c 'import sqlite3;c=sqlite3.connect("/opt/co37/data/co37.db");c.execute("UPDATE setting SET value=? WHERE key=?",("false","https_only"));c.commit()'
```

Aussen nur einfache, innen nur doppelte Anführungszeichen, und die Werte
als Platzhalter statt in SQL-Anführungszeichen. Mischt man das, hängt das
Ergebnis von der Shell ab — und in SQLite steht `"false"` für einen
Spaltennamen, nicht für einen Text.

Danach ist der unverschlüsselte Zugang wieder offen.

**Deshalb ist es vertretbar, den Haken zu setzen, statt HTTP dauerhaft
offen zu lassen.** Ein dauerhaft offener unverschlüsselter Zugang ist ein
ständiges Risiko; dieser Handgriff kostet dreißig Sekunden und wird
vielleicht nie gebraucht.

Wer den Weg über die Datenbank nicht will, lässt den Haken aus. Der
Dauerverkehr der Agents ist dann trotzdem verschlüsselt — das ist der
größere Teil, zehn Hosts im Minutentakt, jeder mit seinem Token in der
Kopfzeile. Offen bleibt nur das eigene Sitzungstoken, und auch das nur,
wenn man tatsächlich über den unverschlüsselten Port aufruft. Ein
Lesezeichen auf die interne Adresse ist dabei der eigentliche
Risikofaktor.

### Der Port bleibt offen

Ein Portscanner sieht 8080 weiterhin, bekommt aber nur 403. Wirklich
geschlossen ist er erst mit einer Firewallregel. Die gehört zum Rechner
und nicht in diese Anwendung — dafür bräuchte das Backend Rechte, die es
bewusst nicht hat.

Wenn gewünscht, auf dem CO-37-Server:

```bash
apt-get install -y ufw
ufw allow 22/tcp                 # zuerst, sonst sperrt man sich aus
ufw allow from <Proxy-Adresse> to any port 8080 proto tcp
ufw allow from <Checkmk-Server> to any port 6556 proto tcp
ufw default deny incoming
ufw enable
```

Der Checkmk-Agent auf Port 6556 wird sonst mit ausgesperrt und der Host
geht im Monitoring auf rot.

Laufen auf dem Rechner Docker-Container mit veröffentlichten Ports:
Docker schreibt seine Regeln direkt in iptables und umgeht ufw dabei.
Für CO-37 spielt das keine Rolle, für die Container schon.

## 5b. Öffentliche Adresse eintragen

**Einstellungen → Zugang → Öffentliche Adresse**, z.B.
`https://co37.example.net`.

Diese Adresse landet in den Installationsbefehlen und damit in der
`agent.conf` jedes neu eingerichteten Hosts. Bleibt das Feld leer, wird
die Adresse verwendet, unter der das Dashboard gerade aufgerufen wurde —
dann hängt es davon ab, wie man die Seite öffnet. Richtet man nach der
Umstellung einen Host über den internen Port ein, spräche dessen Agent
dauerhaft unverschlüsselt, und auffallen würde es erst im Zähler der
Bereitschaftsanzeige.

Im Reiter **Agents** steht über den Befehlen, welche Adresse gerade
eingetragen wird.

**Bestehende Agents sind davon nicht betroffen.** Deren Server steht in
der `agent.conf` auf dem jeweiligen Host. Die Selbstaktualisierung tauscht
nur den Programmcode aus, nie die Konfiguration — umgestellt wird von
Hand, siehe Abschnitt 7.

---

## 6. Prüfen, bevor die Agents umgestellt werden

Im Reiter **Zugang** muss stehen, dass die eigene Sitzung als
verschlüsselt gilt und von der erwarteten Adresse kommt.

Im Reiter **Agents** einen Installationsbefehl erzeugen: er muss den
DNS-Namen mit `https://` enthalten, nicht die interne Adresse mit Port.

## 7. Agents umstellen

**Einzeln, nicht alle gleichzeitig.** Kommt ein Agent nicht mehr durch,
fällt er still aus — er meldet den Fehler nur in seine eigene
Protokolldatei.

Auf jedem Host in `agent.conf`:

```
server = https://<DNS-Name>
```

- **Linux:** `/etc/co37/agent.conf`, danach
  `systemctl restart co37-agent`
- **Windows:** `C:\ProgramData\CO37\agent.conf` — **nicht** unter
  `Program Files`. Der Ordner ist im Explorer ausgeblendet, über die
  Adresszeile aber erreichbar. Danach die geplante Aufgabe neu starten:
  `schtasks /End /TN CO37Agent` und `schtasks /Run /TN CO37Agent`

Nur die Zeile `server` ändern. Geht dabei das `token` verloren, muss sich
der Host neu anmelden und wartet wieder auf Freigabe.

Der Agent sucht die Konfiguration in dieser Reihenfolge: neben
`agent.py`, dann `/etc/co37/agent.conf`, dann
`C:\ProgramData\CO37\agent.conf`. Unter Windows liegt sie bewusst in
`ProgramData` — `Program Files` wird beim erneuten Einspielen der MSI
überschrieben.

`verify_ssl` bleibt auf `true` — mit einem Let's-Encrypt-Zertifikat ist
das der richtige Wert. Steht es auf `false`, wäre die Verschlüsselung
zwar da, aber ohne Prüfung des Gegenübers.

Nach jedem Host: erscheint er im Dashboard wieder als online? Erst dann
den nächsten.

**Der DNS-Name muss intern auflösen**, auch von den Windows-Hosts aus.

---

## 8. Was danach noch offen bleibt

Der Verkehr **zwischen Proxy und Backend** läuft weiter unverschlüsselt.
Im lokalen Netz ist das vertretbar; wer es nicht will, braucht TLS auch
auf der Strecke dahinter.
