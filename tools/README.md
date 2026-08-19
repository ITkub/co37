# Werkzeuge für den Lizenzgeber

Dieses Verzeichnis gehört **nicht** ins Auslieferungspaket —
`build_release.sh` nimmt `tools/` bewusst nicht mit.

---

## Einmalig: Schlüsselpaar erzeugen

Auf dem Rechner, auf dem das Projekt liegt — **nicht** auf dem
CO-37-Server. Der Server ist erreichbar, betreibt ein Webportal und wird
gepatcht; der Schlüssel, der das Geschäftsmodell trägt, gehört an einen
Ort, der möglichst wenig tut.

```
python tools\make-license.py --init
```

Ergebnis:

| Datei | Ort | Zweck |
|---|---|---|
| `license-private.pem` | `%USERPROFILE%\.co37\` | bleibt bei dir, erzeugt Schlüssel |
| `license_key.pub` | `backend\` | wird ausgeliefert, prüft Schlüssel |

Der öffentliche Teil gehört ins Repository. Mit ihm lassen sich
Schlüssel prüfen, aber nicht erzeugen — auch nicht von jemandem, der den
gesamten Quelltext hat.

Ein zweiter Aufruf von `--init` wird abgelehnt. Ein versehentliches
Überschreiben wäre der Totalverlust.

---

## Den privaten Schlüssel sichern

**Vor dem ersten Verkauf.** Geht er verloren, können keine neuen
Schlüssel mehr ausgestellt werden. Bereits verkaufte laufen weiter — aber
jeder Neukunde und jede Verlängerung wäre unmöglich, bis ein neues Paar
existiert. Dann müssten **alle** bestehenden Kunden neue Schlüssel
bekommen und eine neue Fassung veröffentlicht werden.

Mindestens drei Kopien, in unterschiedlicher Form:

1. **Passwortmanager**, als sicherer Notiz-Eintrag
2. **Ausgedruckt** — `--sicherung` gibt ihn druckfreundlich aus, mit
   Prüfsumme zum Abgleich beim Abtippen. Drei Zeilen Text überleben jeden
   Datenträgerausfall und jede Verschlüsselung durch Erpressungssoftware.
3. **Verschlüsselt außer Haus**

**Nicht** auf dem CO-37-Server, **nicht** im Homelab-Backup — beides
fällt bei demselben Ereignis mit aus, gegen das die Sicherung schützen
soll. **Niemals** in ein Git-Repository, auch nicht in ein privates.

### Die Sicherung prüfen

```
python tools\make-license.py --pruefen sicherung.txt
```

Liest eine Sicherung ein und bestätigt, dass sie zum ausgelieferten
öffentlichen Teil passt. Eine Sicherung, die nie geprüft wurde, ist keine
Sicherung.

---

## Schlüssel ausstellen

```
python tools\make-license.py --kunde "Firma Meier" --hosts 100 --jahre 3
```

`--jahre` nimmt 1, 2 oder 3. Ausgegeben wird eine Zeile, die der Kunde in
den Einstellungen einfügt:

```
CO37-eyJleHAiOiIyMDI5LTA4LTE3IiwiaCI6MTAwLC...
```

Jeder Schlüssel wird in `%USERPROFILE%\.co37\lizenzen.csv` vermerkt — mit
laufender Nummer, Kunde, Umfang und Datum. Ohne dieses Register weiß man
nach zwei Jahren nicht mehr, wem was verkauft wurde, und kann einem
Kunden, der seinen Schlüssel verlegt hat, nicht helfen.

### Verlängern

Verlängert ein Kunde **vor** dem Ablauf, würde die Restlaufzeit sonst
verfallen — dann verlängert niemand mehr vorzeitig. Mit `--verlaengert`
beginnt die neue Laufzeit am Ablaufdatum des bisherigen Schlüssels:

```
python tools\make-license.py --kunde "Firma Meier GmbH" --hosts 100 --jahre 3 ^
  --verlaengert CO37-...
```

Bequemer über die laufende Nummer aus dem Register:

```
python tools\make-license.py --kunde "Firma Meier GmbH" --hosts 100 --jahre 3 ^
  --verlaengert-nr 7
```

Vor dem Ausstellen wird angezeigt, was herauskommt:

```
Verlaengerung:
  bisher:  Firma Meier GmbH · 50 Hosts · bis 18.08.2027
  neu:     Firma Meier GmbH · 100 Hosts · bis 17.08.2030
  Aus der bisherigen Laufzeit werden 365 Tage uebernommen.
  Die Hostzahl aendert sich von 50 auf 100. Die Restlaufzeit wird
  unveraendert uebernommen.
```

Geprüft wird dabei:

- **Signatur des Vorgängers** — sonst würde auf eine Fälschung
  aufgerechnet
- **Kundenname** — weicht er ab, kommt eine Rückfrage
- **Bereits abgelaufen** — dann beginnt die neue Laufzeit heute.
  Rückwirkend zu rechnen wäre ein Verlust ohne Gegenwert
- **Unbefristeter Vorgänger** — wird abgelehnt. Es gibt nichts
  aufzuschlagen, und eine stillschweigende Umwandlung in eine feste
  Laufzeit wäre eine unangenehme Überraschung

**Eine geänderte Hostzahl wird nicht umgerechnet.** Die Restlaufzeit
bleibt, die neue Hostzahl gilt. Alles andere wäre eine Rechenaufgabe, die
kein Kunde nachvollziehen kann.

### Warum das Aufrechnen hier passiert und nicht beim Einspielen

Würde CO-37 beim Eintragen die Restlaufzeit selbst aufschlagen, könnte
ein Kunde denselben Schlüssel mehrfach eintragen und sich die Laufzeit
verlängern. Die Rechnung gehört auf die Seite, auf der der private
Schlüssel liegt.

### Ohne Begrenzung

```
python tools\make-license.py --kunde "ITkub intern" --hosts unbegrenzt --unbefristet
```

Beides ist auch einzeln möglich — etwa 50 Hosts unbefristet.

Ein Schlüssel, der **unbegrenzt und unbefristet** ist, hebelt das
Geschäftsmodell vollständig aus, wenn er abhanden kommt: er läuft nie ab,
es gibt also keine natürliche Schadensbegrenzung. Das Werkzeug fragt
deshalb nach. Für die eigene Installation in Ordnung — an Kunden nur
bewusst vergeben, nicht aus Bequemlichkeit.

---

## Einen Schlüssel ansehen

```
python tools\make-license.py --zeigen CO37-...
```

Zeigt Kunde, Umfang, Laufzeit und ob die Signatur stimmt. Nützlich bei
Rückfragen: „Mein Schlüssel wird nicht angenommen."

---

## Was der Schlüssel enthält

Kunde, Hostzahl, Ausstellungs- und Ablaufdatum, laufende Nummer — alles
im Klartext lesbar. Die Signatur schützt vor **Veränderung**, nicht vor
Einsicht. Etwas Geheimes steht nicht darin.

Fälschen kann niemand einen Schlüssel. Wer die Prüfung im Quelltext
entfernt, umgeht sie — das gilt für jede selbstgehostete Software und ist
dann eine bewusste Lizenzverletzung, kein Versehen.


---

# Update-Pakete signieren

**Zweites Schlüsselpaar**, getrennt vom Lizenzschlüssel. Der
Lizenzschlüssel erlaubt, Lizenzen auszustellen. Dieser hier erlaubt, Code
als root auf jedem Kundensystem auszuführen — der Watcher packt ein
Update-Paket als root aus.

## Einmalig

```
python tools\sign-release.py --init
```

Legt `release-private.pem` unter `%USERPROFILE%\.co37\` an und
`backend\release_key.pub` im Projekt. **Genauso sichern wie den
Lizenzschlüssel.**

`--sicherung` gibt ihn druckfreundlich aus.

## Beim Bauen

`build_release.py` signiert von selbst, wenn der Schlüssel vorliegt:

```
python build_release.py 0.33.0
```

Erzeugt neben dem Paket zwei Dateien:

| Datei | Zweck |
|---|---|
| `.zip.sig` | wird beim Einspielen gebraucht |
| `.zip.sha256` | nur zum Abgleich von Hand |

Beide zusammen mit dem Paket veröffentlichen. Nachträglich signieren geht
auch:

```
python tools\sign-release.py co37_v0_33_0.zip
python tools\sign-release.py --pruefen co37_v0_33_0.zip
```

## Warum nicht nur eine Prüfsumme

Wer die Datei austauschen kann, kann auch die Prüfsummendatei daneben
austauschen. Sie schützt gegen einen abgebrochenen Download, nicht gegen
Manipulation.

## Der Übergang

**Das Paket, das die Prüfung einführt, muss noch von der bisherigen
Fassung eingespielt werden können** — die prüft nichts. Ab dann ist jedes
Paket signaturpflichtig.

Liegt kein `release_key.pub` im Paket, läuft CO-37 ohne Prüfung weiter.
Das ist der Zustand eines Quelltextes, aus dem sich jemand selbst baut —
wer das tut, hat den Code ohnehin in der Hand. Liegt der Schlüssel
dagegen vor, ist die Signatur Pflicht; sonst könnte ein Angreifer sie
einfach weglassen.
