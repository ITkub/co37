"""
CO-37 - setup.sh legt keine Geheimnisse offen.

Der Anlass, aus der Sicherheitspruefung vom 2026-08-22 (F-04): setup.sh
schrieb den Admin-Token und CO37_SECRET_KEY als Environment=-Zeilen in
/etc/systemd/system/co37-backend.service. Die Datei entsteht mit den
Vorgaberechten und ist damit fuer jeden lokalen Benutzer lesbar, und
'systemctl show' gibt Environment=-Zeilen ohnehin im Klartext aus. Der
Admin-Token ist Vollzugriff auf die Schnittstelle.

Was diese Reihe leistet und was nicht - hier ehrlich, damit sie nicht
mehr zu versprechen scheint, als sie kann:

  LEISTET: eine statische Durchsicht von setup.sh. Steht in der erzeugten
  Unit wieder ein Geheimnis, fehlt die EnvironmentFile, oder entsteht die
  Datei ohne 600/root - dann meldet es sich hier.

  LEISTET NICHT: sie fuehrt setup.sh nicht aus. Das braucht apt, useradd
  und systemd; im Testcontainer ist davon nichts sinnvoll zu haben. Ob es
  auf dem Server tatsaechlich greift, zeigen dort
  'systemctl cat co37-backend' und 'ls -l /etc/co37/backend.env'.

Braucht kein Backend und kein Netz.

    python3 tests/setup-test.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
SETUP = WURZEL / "setup.sh"

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


check("setup.sh ist vorhanden", SETUP.is_file(), SETUP)
quelle = SETUP.read_text(encoding="utf-8")


def heredoc(nach: str) -> str:
    """
    Holt den Inhalt eines <<EOF-Blocks, der auf 'nach' folgt.

    Gezielt statt einer Suche ueber die ganze Datei: die Namen der
    Geheimnisse stehen auch in Kommentaren und in der env-Datei selbst -
    ein Treffer dort waere ein Fehlalarm. Geprueft werden muss, was in der
    UNIT landet.
    """
    i = quelle.find(nach)
    if i < 0:
        return ""
    i = quelle.find("<<EOF", i)
    if i < 0:
        return ""
    ende = quelle.find("\nEOF", i)
    return quelle[i + len("<<EOF"):ende if ende > 0 else len(quelle)]


unit = heredoc("cat > /etc/systemd/system/co37-backend.service")
check("die Backend-Unit wird im Skript erzeugt", len(unit) > 100, len(unit))

# ----------------------------------------------------------------------
# Kein Geheimnis in der Unit
# ----------------------------------------------------------------------
gefunden = [z.strip() for z in unit.splitlines()
            if re.match(r'\s*Environment=.*(CO37_ADMIN_TOKEN|CO37_SECRET_KEY)', z)]
check("kein Admin-Token und kein Schluessel als Environment= in der Unit",
      not gefunden, "; ".join(gefunden))

check("die Unit zieht die Geheimnisse aus einer EnvironmentFile",
      re.search(r"^\s*EnvironmentFile=\S+", unit, re.M) is not None)

# Die harmlosen Einstellungen bleiben sichtbar - sonst versteckt die
# Umstellung Pfade und Intervalle mit, und 'systemctl cat' taugt nicht
# mehr zum Nachsehen.
for name in ("CO37_DB", "CO37_DATA", "CO37_POLL_INTERVAL", "CO37_OFFLINE_SECONDS"):
    check(f"{name} steht weiter offen in der Unit",
          re.search(rf"^\s*Environment=.*{name}=", unit, re.M) is not None)

# ----------------------------------------------------------------------
# Die env-Datei entsteht eng und gehoert root
# ----------------------------------------------------------------------
m = re.search(r"^ENVFILE=(\S+)", quelle, re.M)
check("ein Pfad fuer die env-Datei ist gesetzt", m is not None)
pfad = m.group(1) if m else ""

check("die env-Datei liegt nicht unter $BASE",
      "$BASE" not in pfad and "/data" not in pfad, pfad)
check("chmod 600 auf die env-Datei",
      re.search(r'chmod 600 "\$ENVFILE"', quelle) is not None)
check("chown root:root auf die env-Datei",
      re.search(r'chown root:root "\$ENVFILE"', quelle) is not None)

# Reihenfolge: die Datei muss eng angelegt werden, bevor etwas hineinkommt.
# Sonst stuende der Token einen Moment lang mit 644 auf der Platte, und
# genau in dem Moment kann jeder mitlesen.
i_fuellen = quelle.find('cat > "$ENVFILE"')
i_leer = quelle.find(': > "$ENVFILE"')
# Nicht die ERSTE "umask 177" im Skript nehmen: seit secret.key genauso
# behandelt wird, steht sie weiter oben und haette diese Pruefung auch
# dann gruen gehalten, wenn die env-Datei ihre eigene verloren haette.
i_umask = quelle.rfind("umask 177", 0, i_leer if i_leer > 0 else i_fuellen)
check("die Datei wird eng angelegt, bevor sie gefuellt wird",
      0 < i_umask < i_leer < i_fuellen,
      f"umask@{i_umask} leer@{i_leer} fuellen@{i_fuellen}")

# Derselbe Fall fuenfzig Zeilen weiter oben: secret.key. Dort stand
# "schreiben, danach chmod 600" - dazwischen lag der Schluessel, mit dem
# alle Checkmk-Secrets verschluesselt sind, mit der Umask-Vorgabe auf der
# Platte (ueblich 644). Seit der vierten Pruefungsrunde als "niedrig,
# liegen gelassen" notiert, behoben am 2026-09-03.
#
# Ueber die Reihenfolge geprueft, nicht ueber das blosse Vorkommen von
# "umask 177": das steht wegen der env-Datei ohnehin in der Datei, eine
# Suche danach bliebe also auch ohne die Behebung gruen.
i_key_schreiben = quelle.find('echo "$SECRET_KEY" > "$BASE/data/secret.key"')
i_key_leer = quelle.find(': > "$BASE/data/secret.key"')
check("secret.key wird eng angelegt, bevor der Schluessel hineinkommt",
      0 < i_key_leer < i_key_schreiben,
      f"leer@{i_key_leer} schreiben@{i_key_schreiben}")
_umasks = [i for i in range(len(quelle))
           if quelle.startswith("umask 177", i)]
check("und davor steht eine enge umask",
      any(u < i_key_leer for u in _umasks), (_umasks, i_key_leer))
check("chmod 600 bleibt trotzdem stehen",
      'chmod 600 "$BASE/data/secret.key"' in quelle)

# ----------------------------------------------------------------------
# Eigentuemer des Installationsverzeichnisses (F-02)
# ----------------------------------------------------------------------
# Gehoert hierher, nicht zum Watcher-Test: setup.sh setzt den Anfangszustand.
check("$BASE gehoert root",
      re.search(r'chown -R root:root "\$BASE"', quelle) is not None)
check("nur $BASE/data gehoert co37",
      re.search(r'chown -R co37:co37 "\$BASE/data"', quelle) is not None)
check("kein chown von $BASE auf co37 mehr",
      re.search(r'chown -R co37:co37 "\$BASE"\s*$', quelle, re.M) is None)

# python3-cryptography braucht der Watcher fuer die Signaturpruefung.
check("python3-cryptography wird mitinstalliert",
      "python3-cryptography" in quelle)

# ----------------------------------------------------------------------
# Kein Admin-Token mehr
# ----------------------------------------------------------------------
# Bis 0.36.11 erzeugte setup.sh einen globalen Admin-Token, legte ihn in
# data/admin.token ab, schrieb ihn in die env-Datei und gab ihn aus. Der
# Token ist entfallen (siehe tests/apikey-test.py). Hier wird nur noch
# festgehalten, dass die Einrichtung ihn nicht wieder einfuehrt - und
# dass eine liegengebliebene Datei aus einer aelteren Fassung weggeraeumt
# wird, statt als totes Geheimnis auf der Platte zu bleiben.
check("setup.sh erzeugt keinen Admin-Token",
      "CO37_ADMIN_TOKEN" not in quelle and "openssl rand -base64 36" not in quelle)
check("und legt keine admin.token an",
      'echo "$TOKEN" >' not in quelle)
check("eine alte admin.token wird entfernt",
      'rm -f "$BASE/data/admin.token"' in quelle)
check("die env-Datei haelt nur noch den Verschluesselungsschluessel",
      "CO37_SECRET_KEY=$SECRET_KEY" in quelle and "CO37_ADMIN_TOKEN=" not in quelle)

# ----------------------------------------------------------------------
# Die dokumentierte Wiederherstellung passt noch zum Code
# ----------------------------------------------------------------------
# Wer sich aussperrt, kommt ueber die Datenbank zurueck - der Weg steht in
# der README unter "Wenn du dich aussperrst". Ein Befehl in der Doku, der
# nicht mehr laeuft, ist schlimmer als keiner: gebraucht wird er genau
# dann, wenn man ihn nicht in Ruhe ausprobieren kann.
#
# Geprueft wird beides gegeneinander - die Doku gegen den Code und
# umgekehrt. Wird hash_password umbenannt oder der Schluessel des
# HTTPS-Zwangs geaendert, faellt es hier auf.
print("--- Dokumentierte Wiederherstellung ---")
README = WURZEL / "README.md"
MAIN = WURZEL / "backend" / "main.py"
check("README ist lesbar", README.is_file(), README)
doku = README.read_text(encoding="utf-8")
backend = MAIN.read_text(encoding="utf-8")

check("die README beschreibt den Weg zurueck",
      "# Wenn du dich aussperrst" in doku)

# Der Python-Block muss syntaktisch gueltig sein.
block = re.search(r"<<'EOF'\n(.*?)\nEOF\n", doku, re.S)
check("der Block zum Zuruecksetzen steht in der README", block is not None)
if block:
    import ast  # noqa: E402
    try:
        ast.parse(block.group(1))
        ok, grund = True, ""
    except SyntaxError as exc:
        ok, grund = False, str(exc)
    check("und ist syntaktisch gueltig", ok, grund)

    # Gegen den Code: was der Block aufruft, muss es geben.
    check("er benutzt main.hash_password",
          "main.hash_password(" in block.group(1))
    check("und die Funktion gibt es im Backend",
          "def hash_password(" in backend)
    check("er hebt die Deaktivierung auf", "disabled = False" in block.group(1))
    check("das Feld heisst im Modell auch so",
          "disabled" in (WURZEL / "backend" / "models.py").read_text(encoding="utf-8"))

# Der sqlite3-Befehl muss den Schluessel treffen, den das Backend liest.
check("der dokumentierte sqlite3-Befehl setzt https_only",
      "where key='https_only'" in doku)
check("und das Backend liest genau diesen Schluessel",
      'SET_HTTPS_ONLY = "https_only"' in backend)

# ----------------------------------------------------------------------
# Und das Skript muss ueberhaupt laufen koennen
print("--- Was als root schreibt, schreibt nicht in co37-Gebiet ---")
# F-20, 2026-08-31: /etc/cron.daily/co37-backup laeuft als root und schrieb
# nach $BASE/data/backup - in das eine Verzeichnis, das co37 gehoert.
# sqlite3 ".backup" oeffnet das Ziel mit O_CREAT|O_RDWR und folgt einer
# Verknuepfung. Damit hatte co37 einmal taeglich einen Schreibzugriff als
# root an frei gewaehlter Stelle.
inhalt = SETUP.read_text(encoding="utf-8")
cron = re.search(r"cat > /etc/cron\.daily/co37-backup <<EOF(.*?)\nEOF",
                 inhalt, re.S)
check("die Sicherung wird eingerichtet", cron is not None)
if cron:
    rumpf = cron.group(1)
    # Die Datenbank wird aus data/ GELESEN, das ist richtig. Geschrieben
    # werden darf dort nichts.
    ziel = rumpf.split(".backup")[-1] if ".backup" in rumpf else rumpf
    check("ihr Ziel liegt nicht unter data/", "/data/" not in ziel,
          ziel.strip())
    check("sie schreibt nach db_backups", "db_backups" in rumpf)
    check("die Datenbank wird weiterhin von dort gelesen",
          "$BASE/data/co37.db" in rumpf)
check("db_backups wird angelegt und gehoert root",
      "db_backups" in inhalt and 'chmod 700 "$BASE/db_backups"' in inhalt)

print("--- Das Ausgangsverzeichnis des Watchers ---")
# F-18: der Watcher meldet seinen Stand nach state/. Das Verzeichnis muss
# root gehoeren und fuer co37 lesbar sein - beschreibbar nicht.
check("state/ wird angelegt", re.search(r"mkdir -p .*state", inhalt) is not None)
check("state/ ist lesbar, aber nicht fuer co37 beschreibbar",
      'chmod 755 "$BASE/state"' in inhalt)
check("state/ liegt nicht unter data/", "$BASE/data/state" not in inhalt)
check("co37 bekommt weiterhin nur data/",
      inhalt.count('chown -R co37:co37 "$BASE/data"') == 1
      and 'chown -R co37:co37 "$BASE/state"' not in inhalt)


# ----------------------------------------------------------------------
# Nur wenn bash da ist. Fehlt sie, ist das kein Fehler des Skripts, und
# eine Falschmeldung waere schlimmer als eine ausgelassene Pruefung -
# 'Port belegt' statt 'python3 fehlt' hat hier schon einmal zu einem
# ungetesteten Paket gefuehrt.
bash = shutil.which("bash")
if bash:
    res = subprocess.run([bash, "-n", str(SETUP)], capture_output=True, text=True)
    check("setup.sh ist syntaktisch in Ordnung", res.returncode == 0,
          res.stderr.strip()[:200])
else:
    print("       (bash nicht gefunden - Syntaxpruefung ausgelassen)")


# ======================================================================
# Die Agent-Pakete liegen im Ausgang, nicht im Eingang (F-29)
# ======================================================================
# build_packages.sh laeuft als root - der Watcher startet es - und
# build_deb.py schrieb mit open(ziel,"wb") hinein. Lag das Zielverzeichnis
# wie bis 0.37.9 unter data/, gehoerte es co37: eine Verknuepfung unter
# dem erwarteten Paketnamen genuegte, und root schrieb an eine frei
# gewaehlte Stelle. Ausgeloest wurde der Bau durch eine einzige Datei im
# Eingang, ohne Paket und ohne Signatur.
check("state/packages wird angelegt",
      re.search(r"mkdir -p .*state/packages", inhalt) is not None)
check("state/packages ist lesbar, aber nicht fuer co37 beschreibbar",
      'chmod 755 "$BASE/state/packages"' in inhalt)
check("die Pakete liegen nicht unter data/",
      "$BASE/data/packages" not in inhalt)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
