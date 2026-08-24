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
i_umask = quelle.find("umask 177")
i_fuellen = quelle.find('cat > "$ENVFILE"')
check("die Datei wird eng angelegt, bevor sie gefuellt wird",
      0 < i_umask < i_fuellen, f"umask@{i_umask} fuellen@{i_fuellen}")

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
# Der Admin-Token steht nur bei der Erstinstallation im Klartext
# ----------------------------------------------------------------------
# Der Anlass: setup.sh gab ihn am Ende JEDES Laufs aus, auch wenn er
# gerade eben als "Vorhandener Admin-Token wird weiterverwendet" gemeldet
# worden war. Bei einem Wiederholungslauf ist das nur eine Gelegenheit,
# ihn irgendwohin zu kopieren, wo er nicht hingehoert - genau so ist er
# einmal in einem Chatprotokoll gelandet und musste getauscht werden.
zeilen = quelle.splitlines()
klartext = [i for i, z in enumerate(zeilen) if "Admin-Token: $TOKEN" in z]
check("der Token wird genau einmal im Klartext ausgegeben",
      len(klartext) == 1, len(klartext))

check("TOKEN_NEU wird bei der Erstinstallation gesetzt",
      re.search(r"TOKEN_NEU=1", quelle) is not None)
check("und vorher auf 0", re.search(r"^TOKEN_NEU=0", quelle, re.M) is not None)

if len(klartext) == 1:
    # Rueckwaerts bis zum umschliessenden if laufen. Steht dazwischen ein
    # 'fi', ist die Ausgabe nicht mehr in dem Zweig - dann greift die
    # Bedingung nicht, auch wenn sie irgendwo darueber steht.
    i = klartext[0]
    umschliessend = None
    for j in range(i - 1, -1, -1):
        z = zeilen[j].strip()
        if z == "fi":
            break
        if z.startswith("if "):
            umschliessend = z
            break
    check("die Klartextausgabe haengt an TOKEN_NEU",
          umschliessend is not None and "TOKEN_NEU" in umschliessend,
          umschliessend or "kein umschliessendes if gefunden")

check("beim Wiederholungslauf wird nur der Fundort genannt",
      "steht in $BASE/data/admin.token" in quelle)

# ----------------------------------------------------------------------
# Und das Skript muss ueberhaupt laufen koennen
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

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
