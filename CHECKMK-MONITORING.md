# CO-37 in Checkmk überwachen

Ab Version 0.40.0. Damit sieht Checkmk den Zustand von CO-37 selbst und
ob die Agenten auf den Zielhosts laufen. Das ist die **Gegenrichtung** zur
schon vorhandenen Anbindung (die setzt beim Patchen Downtimes in Checkmk —
siehe README, Teil 2).

Drei Bausteine, unabhängig voneinander nutzbar:

1. **CO-37-Server** — ein Local-Check meldet Backend, Flotte und Lizenz.
2. **Agent-Hosts** — ein kleiner Check je Host meldet *direkt*, ob der
   CO-37-Agent läuft (unabhängig davon, ob der CO-37-Server gerade steht).
3. **Piggyback** *(optional)* — die Zusatzsicht aus CO-37 je Host
   (Kontakt, Updates, Neustart), automatisch über die in CO-37
   hinterlegte Checkmk-Verknüpfung.

Alle Skripte liegen im Release unter `checkmk/`.

---

## Wie es abgesichert ist

Der Server-Check fragt einen neuen, **rein lokalen** Endpunkt ab:
`GET /api/v1/monitoring`. Der verlangt **beides**:

- die Anfrage kommt über **Loopback** (127.0.0.1), **und**
- ein **lokales Token** (`X-Monitor-Token`) stimmt.

Das Token erzeugt CO-37 beim Setup selbst und legt es unter
`data/monitor.token` ab (Rechte `600`, gehört `co37`). **Du musst damit
nichts tun** — das Local-Check-Skript liest es direkt aus der Datei.

Warum beides? CO-37 bestimmt „Loopback" über die echte TCP-Gegenstelle
(uvicorn läuft mit `--no-proxy-headers`). Steht ein Reverse Proxy auf
**demselben** Host, erschiene der als `127.0.0.1` — eine reine
Loopback-Schranke wäre dann über den Proxy von außen offen. Das Token
schließt das: von außen kommt niemand an die lokale Datei. Schlägt die
Prüfung fehl, antwortet der Endpunkt mit **404** — nach außen existiert
die Route also nicht einmal.

In der Antwort steht **kein Geheimnis**: keine Agent-Tokens, kein
Lizenzschlüssel, kein Kundenname. Nur Zähler, Hostnamen, die
Checkmk-Zuordnung, Versionen und die Lizenz-Eckdaten.

---

## 1. CO-37-Server überwachen

Auf dem CO-37-Server (dort läuft schon der Checkmk-Agent):

```
install -m 0755 /opt/co37/checkmk/co37_monitoring \
  /usr/lib/check_mk_agent/local/co37_monitoring
```

Testen:

```
/usr/lib/check_mk_agent/local/co37_monitoring
```

Es müssen Zeilen wie `0 "CO-37 Backend" - laeuft …` erscheinen. Dann in
Checkmk auf dem CO-37-Host eine **Service-Discovery** — die neuen Dienste
erscheinen:

| Dienst | WARN | CRIT |
|---|---|---|
| `CO-37 Backend` | — | Backend/Schema-Fehler oder Endpunkt nicht erreichbar |
| `CO-37 Agents online` | ein Host offline | alle offline |
| `CO-37 Wartet auf Freigabe` | > 0 | — |
| `CO-37 Updates offen` | Updates offen | sicherheitsrelevante Updates |
| `CO-37 Neustart noetig` | > 0 | — |
| `CO-37 Agents veraltet` | > 0 | — |
| `CO-37 Lizenz` | < 30 Tage / am Hostlimit | < 14 Tage / abgelaufen |
| `CO-37 Eigenes Update` | neue CO-37-Version verfügbar | — |

Läuft das Backend hinter einem Port ≠ 8080 oder liegt `data/` woanders,
lässt sich das über Umgebungsvariablen setzen (`CO37_URL`,
`CO37_MONITOR_TOKEN_FILE`) — siehe Kopf des Skripts.

> Hinweis: Ein CO-37-Systemupdate tauscht nur `backend`, `frontend`,
> `agent` und `packaging` aus. Das Skript im Checkmk-Ordner bleibt also
> stehen. Ändert sich der Check in einer neuen Fassung, die Datei aus
> `/opt/co37/checkmk/` erneut hineinkopieren.

---

## 2. Agent-Hosts direkt überwachen

Das misst **auf dem Zielhost selbst**, ob der Agent läuft — und schlägt
auch dann an, wenn der CO-37-Server gerade aus ist. Kein Token nötig.

### Linux

Der Agent ist der systemd-Dienst `co37-agent.service`. Zwei Wege:

**a) Ohne Datei** — Checkmk kann systemd-Dienste nativ überwachen: Regel
*„Monitor state of systemd services"* auf `co37-agent.service` (bzw. in
der Discovery aufnehmen).

**b) Mit Local-Check** (überall gleich, robuster):

```
install -m 0755 /opt/co37/checkmk/co37_agent \
  /usr/lib/check_mk_agent/local/co37_agent
```

Ergebnis: Dienst `CO-37 Agent` — OK wenn `active`, sonst CRIT.

### Windows

Der Agent ist bewusst **kein** Windows-Dienst, sondern eine geplante
Aufgabe (`CO37Agent`), die beim Systemstart als `SYSTEM` einen dauerhaft
laufenden Python-Prozess startet. Checkmk überwacht so etwas nicht von
allein — deshalb den mitgelieferten Local-Check verwenden:

```
copy \opt\co37\checkmk\co37_agent.ps1 ^
  "C:\ProgramData\checkmk\agent\local\co37_agent.ps1"
```

(Die `.ps1` liegt im Release; auf den Windows-Host kopieren, z. B. per
Ablage neben dem MSI.) Ergebnis: Dienst `CO-37 Agent` — OK wenn der
Prozess läuft, sonst CRIT.

Danach auf dem jeweiligen Host eine Service-Discovery.

---

## 3. Zusatzsicht je Host (Piggyback, optional)

Der Server-Check aus Abschnitt 1 hängt für jeden Host, der in CO-37 mit
einem Checkmk-Host **verknüpft** ist (Host-Dialog → Reiter Checkmk),
zusätzliche Dienste an **genau diesen** Checkmk-Host:

- `CO-37 Kontakt` — hat der Agent sich zuletzt gemeldet (Server-Sicht)
- `CO-37 Updates` — offene/sicherheitsrelevante Updates laut CO-37
- `CO-37 Neustart` — Neustart nötig
- `CO-37 Agent-Version` — veraltet

Das braucht keine weitere Installation auf den Zielhosts — es kommt über
den Server-Check. Ohne Checkmk-Verknüpfung entfällt es für den Host
einfach.

> `CO-37 Kontakt` (Server-Sicht) und der direkte `CO-37 Agent`-Check aus
> Abschnitt 2 ergänzen sich: der eine sieht „hat sich beim Server
> gemeldet", der andere „läuft wirklich auf dem Host". Fällt nur der
> Kontakt aus, ist eher das Netz oder der Server dran; fällt der direkte
> Check aus, ist der Agent selbst weg.
