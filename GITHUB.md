# CO-37 auf den eigenen Rechner holen

Anleitung für Windows mit Git Bash. Ziel: der Quellstand liegt bei dir
statt nur als fertiges ZIP, und du kannst ihn zu GitHub hochladen.

---

## 1. Entpacken

Ordner anlegen und das aktuelle ZIP dort hinein entpacken — mit dem
Explorer oder einem Packprogramm. Beispiel:

```
C:\Projekte\co37\
```

Darin müssen anschließend `backend`, `frontend`, `agent`, `tests`,
`packaging` und die Dateien `setup.sh`, `run-tests.sh`, `README.md`
liegen. **Nicht** ein einzelner Unterordner mit allem darin — dann eine
Ebene hochziehen.

In Git Bash dorthin wechseln:

```bash
cd /c/Projekte/co37
ls
```

Git Bash schreibt Pfade mit Schrägstrichen und `/c/` statt `C:\`.

---

## 2. Prüfen, was hochgehen würde

**Vor** dem ersten Hochladen. Später ist das Zurücknehmen aufwendig.

```bash
git init
git add -A
git status --short
```

Die Liste durchsehen. Es dürfen **nicht** auftauchen:

- `*.db` — Datenbanken
- `admin.token`, `secret.key` — Zugangsdaten
- `*.pem` — private Schlüssel
- `co37_v*.zip` — gebaute Pakete
- `__pycache__` — Zwischenstände von Python

Erscheint eines davon, stimmt etwas mit `.gitignore` nicht. Dann:

```bash
git rm -r --cached .
git add -A
git status --short
```

Erst weitermachen, wenn die Liste sauber ist.

---

## 3. Ersten Stand festhalten

```bash
git commit -m "CO-37 0.28.0"
```

Beim ersten Mal fragt Git nach Name und Mailadresse, falls noch nicht
gesetzt:

```bash
git config --global user.name "Michael Kuban"
git config --global user.email "…@users.noreply.github.com"
```

Die Adresse steht dauerhaft in der Historie und ist bei einem
öffentlichen Repository einsehbar. GitHub bietet unter *Settings →
Emails* eine Weiterleitungsadresse an — die ist hier die bessere Wahl.

---

## 4. Repository auf GitHub anlegen

Auf github.com: **New repository**.

- Name: `co37`
- **Private** auswählen
- **Keine** Häkchen bei README, .gitignore oder License — die Dateien
  gibt es schon, sonst gibt es beim ersten Hochladen einen Konflikt

Danach die angezeigten Befehle verwenden, oder:

```bash
git remote add origin https://github.com/<dein-konto>/co37.git
git branch -M main
git push -u origin main
```

Beim ersten Hochladen fragt Git nach Zugangsdaten. Statt des Passworts
einen **Personal Access Token** verwenden (*Settings → Developer settings
→ Personal access tokens*), Bereich `repo`. Das Passwort selbst
funktioniert seit Jahren nicht mehr.

---

## 5. Warum zuerst privat

- Der Lizenztext fehlt noch. Ohne ihn gilt das strengste Urheberrecht —
  unklar für dich wie für Interessenten.
- Die Lizenztechnik ist noch nicht gebaut. Eine jetzt veröffentlichte
  Fassung hätte kein Host-Limit — und bliebe in der Historie dauerhaft
  abrufbar.

Auf öffentlich umstellen geht jederzeit unter *Settings → General →
Change repository visibility*. Umgekehrt bekommt man nichts mehr
eingefangen.

---

## 6. Ab jetzt

Nach jeder Änderung:

```bash
git add -A
git commit -m "kurze Beschreibung"
git push
```

`git status` zeigt jederzeit, was sich geändert hat. `git diff`, was
genau.

---

## Noch offen: Pakete bauen

`build_release.sh` braucht `zip` und `unzip`. **Beides bringt Git Bash
nicht mit** — das Skript läuft dort also noch nicht.

Drei Wege, wenn es soweit ist:

1. Die beiden Programme für Git Bash nachrüsten
2. Das Bauen in Python umschreiben, das kann ZIP-Dateien von Haus aus
3. Auf einem Linux-Rechner bauen — etwa KK-AG01

Muss jetzt nicht entschieden werden. Zum Hochladen und Bearbeiten des
Quellstands braucht es nichts davon.
