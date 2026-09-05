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
| `X-Forwarded-For` | Adresse des ursprünglichen Aufrufers, **am Ende angehängt** |
| `X-Forwarded-Proto` | `https` — **gesetzt, nicht durchgereicht** |
| `Host` | der DNS-Name, unverändert |

Die meisten Proxys tun das von sich aus. Nach dem Einrichten prüfen —
siehe Abschnitt 6.

**Beide Kopfzeilen müssen wirklich vom Proxy kommen.** Ein Proxy, der
`X-Forwarded-Proto` gar nicht setzt, reicht durch, was der Aufrufer
geschickt hat — und der behauptet dann `https`, obwohl die Verbindung
unverschlüsselt ist. Daran hängt der HTTPS-Zwang. Das lässt sich in CO-37
nicht auffangen: ohne eigenen Wert des Proxys steht in der Kopfzeile
nichts als die Behauptung des Aufrufers.

### Warum „am Ende angehängt" hier steht

Proxys gehen mit `X-Forwarded-For` unterschiedlich um: manche **ersetzen**
die Kopfzeile, manche **hängen an**. nginx tut mit der verbreiteten
Schreibweise `$proxy_add_x_forwarded_for` das Zweite, Traefik ebenfalls.
Schickt der Aufrufer dann selbst ein `X-Forwarded-For: 1.2.3.4`, kommt bei
CO-37 `1.2.3.4, <echte Adresse>` an.

CO-37 liest deshalb seit 0.37.7 **von rechts** und überspringt dabei die
unter „Zugang" eingetragenen Proxy-Adressen. Was übrig bleibt, hat der
letzte Proxy selbst gesehen und stammt nicht vom Aufrufer.

Bis 0.37.6 wurde der **erste** Eintrag genommen. Hinter einem anhängenden
Proxy war das der frei erfundene Wert — und damit ließ sich die Drosselung
der Anmeldung vollständig umgehen: je Versuch eine neue Fantasieadresse,
und der Zähler fing immer wieder bei null an. Im Prüfprotokoll stand
dieselbe Erfindung.

Beide Verhaltensweisen funktionieren jetzt. Wer die Wahl hat, lässt den
Proxy anhängen (`$proxy_add_x_forwarded_for`) — dann bleibt die Kette
nachvollziehbar.

Für `X-Forwarded-Proto` gilt seit 0.37.10 dasselbe: auch dort wird **von
rechts** gelesen. Die Korrektur von 0.37.7 hatte diese Schwesterfunktion
nicht mitgenommen — bis dahin gewann bei einer angehängten Kopfzeile der
vom Aufrufer geschickte Wert, und `https` ließ sich damit behaupten
(F-32 der Prüfung vom 31.08.2026). Anders als bei `X-Forwarded-For` gibt
es bei nginx keine übliche anhängende Schreibweise; der praktisch
wichtigere Fall ist der Proxy, der die Kopfzeile schlicht vergisst.

**Wer der Kopfzeile glaubt, steht in CO-37 — nicht im Webserver.** Seit
0.37.15 startet die systemd-Unit uvicorn mit `--no-proxy-headers`. Das ist
Absicht: uvicorn wertet `X-Forwarded-For` sonst **selbst** aus und glaubt sie
jedem Aufrufer aus `127.0.0.1`. Es überschreibt dabei die Adresse, an der
CO-37 anschließend erkennt, ob die Anfrage überhaupt vom eingetragenen Proxy
kommt — die Prüfung lief also gegen einen bereits gefälschten Wert. Sieben
Anmeldeversuche mit je einer erfundenen Adresse blieben ohne Sperre, und im
Prüfprotokoll stand die Erfindung (F-58 der Prüfung vom 03.09.2026).

Wer die Unit von Hand ändert: den Schalter stehen lassen. Die Liste der
Proxys, denen zu glauben ist, gehört in **Einstellungen → Proxy**.

**Nicht nötig:** WebSocket-Unterstützung. CO-37 fragt zyklisch ab.

**Größenbegrenzung für Anfragen:** Systemupdates werden als ZIP
hochgeladen, derzeit rund 600 KB. Eine Grenze unterhalb von etwa 10 MB
sollte angehoben werden.

Seit 0.37.15 weist **CO-37 selbst** zu große Anfragen mit `413` ab. Bis
dahin gab es gar keine Grenze: 200 MB an die absichtlich offene
Anmelderoute `/api/v1/agent/enroll` wurden vollständig in den Speicher
gelesen, bevor irgendeine Prüfung griff (F-56 der Prüfung vom
03.09.2026).

Seit 0.37.19 ist es **eine Grenze je Route** statt einer für alle
(F-62 der Prüfung vom 04.09.2026):

| Route | Grenze |
|---|---|
| alles Übrige | **1 MiB** (`CO37_MAX_BODY` in `/etc/co37/backend.env`, Angabe in Bytes) |
| `/api/v1/agent/scan-result` | 8 MiB |
| `/api/v1/update/upload` | **64 MiB** |

Die einheitlichen 32 MiB waren zu großzügig: der Körper wird vollständig
gelesen, bevor irgendeine Route etwas prüft — vor der Anmeldung, vor dem
Agent-Token, sogar vor der Drosselung. Eine bereits gesperrte Adresse
kostete den Server damit weiterhin 320 ms je Anfrage, beliebig oft.

Die Grenze im Proxy ersetzt das nicht und wird davon nicht ersetzt —
**beide gehören gesetzt**. Der Proxy hält die Last vom Server fern, bevor
sie überhaupt ankommt; die Grenze im Backend gilt auch dann, wenn jemand
den Port direkt erreicht. Bei nginx:

```nginx
client_max_body_size 64m;
```

**64m, nicht 32m** — der Wert muss zur *höchsten* Grenze im Backend
passen, sonst ist der Proxy die engere Schranke und ein Systemupdate über
32 MB käme nicht mehr durch. Heute sind die Pakete rund 600 KB, das fällt
also nicht auf; es fällt erst in dem Augenblick auf, in dem ein Paket
wächst, und dann sucht man an der falschen Stelle.

Bei **Nginx Proxy Manager / NPMplus** steht der Wert nicht in einer
Datei, sondern in der Oberfläche: *Hosts → Proxy Hosts → den Eintrag
bearbeiten → Reiter „Advanced" → Custom Nginx Configuration*. Der Block
landet im `server`-Abschnitt genau dieses Hosts; andere Proxy Hosts
bleiben unberührt. Ab Werk setzt NPM global `client_max_body_size 0;`,
also unbegrenzt — ohne diesen Eintrag gibt es im Proxy gar keine Grenze.

---

## Der Proxy darf `X-Forwarded-For` nicht von jedem glauben

**Das ist die gefährlichste Einstellung auf dieser Seite.** Am 2026-09-05
auf der eigenen Anlage nachgemessen und in drei Zeilen belegt (F-66).

Nginx kann die Absenderadresse einer Anfrage durch den Inhalt von
`X-Forwarded-For` ersetzen. Ob es das tut, hängt an `set_real_ip_from` —
der Liste der Quellen, denen dabei geglaubt wird. **Nginx Proxy Manager
und NPMplus liefern diese Liste ab Werk mit allen privaten Netzen:**

```nginx
real_ip_recursive on;
real_ip_header X-Forwarded-For;
set_real_ip_from 127.0.0.0/8;
set_real_ip_from 10.0.0.0/8;
set_real_ip_from 172.16.0.0/12;
set_real_ip_from 192.168.0.0/16;
```

Damit darf **jedes Gerät in irgendeinem privaten Netz**, das den Proxy
erreicht, seine eigene Adresse frei bestimmen. Was daran hängt:

- **Jede IP-Beschränkung am Proxy** (`allow 192.168.2.0/24; deny all;`)
  wird gegen die ERSETZTE Adresse geprüft — also gegen die, die der
  Aufrufer selbst geschickt hat.
- **Die Drosselung der Anmeldung in CO-37.** Sie zählt je Adresse. Wer
  die Adresse wechseln kann, hat keine Grenze mehr: statt fünf Versuchen
  je Viertelstunde sind es fünf **je erfundener Adresse**.
- **Das Prüfprotokoll.** Es hält fest, was der Aufrufer behauptet hat.

Nachgemessen, alle drei Aufrufe aus demselben Container, alle drei
tatsächlich von `127.0.0.1`:

```
ohne Kopfzeile                        -> 403   (allow-Liste greift)
X-Forwarded-For: 192.168.2.99         -> 200   (allow-Liste umgangen)
X-Forwarded-For: 8.8.8.8              -> 403   (die erfundene wird geprüft)
```

Und im Prüfprotokoll von CO-37 standen anschließend zwei fehlgeschlagene
Anmeldungen von `192.168.2.99` und `192.168.2.123` — zwei Adressen, die
es nicht gibt.

**Das ist derselbe Fehler wie F-58, eine Schicht höher.** Dort glaubte
uvicorn die Kopfzeile an CO-37 vorbei; das ist seit 0.37.15 mit
`--no-proxy-headers` abgestellt. Hier glaubt sie der Proxy — und CO-37
kann nichts dagegen tun: es vertraut dem Proxy zu Recht, der Proxy ist
belogen worden.

### Was zu tun ist

`set_real_ip_from` darf **nur** die Adressen enthalten, von denen
tatsächlich ein vorgeschalteter Proxy kommt. Steht nginx selbst am Rand
des Netzes — der Normalfall — gehört die Liste **leer**. Dann ist
`$remote_addr` die echte Socket-Adresse, und keine Kopfzeile kann daran
etwas ändern.

Bei NPMplus steht die Liste in der globalen `nginx.conf` des Containers
und lässt sich nicht in der Oberfläche ändern; sie gehört über einen
eigenen Konfigurationsschnipsel überschrieben oder beim Anbieter
angesprochen.

### Gegenprobe

Vom Proxy selbst, mit einer Adresse aus der erlaubten Liste:

```
curl -sk -o /dev/null -w "%{http_code}\n" \
  -H "X-Forwarded-For: <eine erlaubte Adresse>" \
  https://<dein-host>/api/health
```

**403 ist richtig.** Kommt 200, glaubt der Proxy die Kopfzeile, und jede
IP-Beschränkung davor ist Zierde.

---

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
