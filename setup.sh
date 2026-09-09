#!/bin/bash
# =====================================================================
# CO-37 - Erstinstallation (Debian 13 / Ubuntu)
#
#   bash setup.sh
#
# CO-37 ist fuer den Betrieb im lokalen Netz gedacht. Das Backend
# lauscht auf 0.0.0.0:8080. Kein nginx, kein certbot, keine Domain.
# Fuer Zugriff aus anderen Netzen: WireGuard ins Zielnetz, oder einen
# Reverse Proxy davorsetzen.
#
# Mehrfach ausfuehrbar. Bestehende Schluessel und Daten bleiben erhalten.
# =====================================================================
set -euo pipefail

BASE="${CO37_BASE:-/opt/co37}"
PORT="${CO37_PORT:-8080}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren."
  exit 1
fi

# ---------------------------------------------------------------------
# Projektdateien pruefen
# ---------------------------------------------------------------------
if [ ! -f "$BASE/backend/main.py" ]; then
  cat <<EOF
!!! Projektdateien fehlen unter $BASE

    Vorgehen:
        apt install -y unzip
        mkdir -p $BASE
        unzip -o co37_v0_2_0.zip -d $BASE
        bash $BASE/setup.sh
EOF
  exit 1
fi

# ---------------------------------------------------------------------
# Pakete
# ---------------------------------------------------------------------
echo ">>> Pakete installieren"
apt-get update -qq
# python3-cryptography braucht der Update-Watcher. Er laeuft mit dem
# System-Python, nicht mit dem venv - bewusst: er muss auch dann noch
# zurueckrollen koennen, wenn ein misslungenes Update das venv zerlegt
# hat. Damit steht ihm aber auch nichts aus requirements.txt zur
# Verfuegung, und die Signaturpruefung vor dem Auspacken braucht Ed25519.
# rpm liefert rpmbuild - gebraucht fuer das Agentenpaket der
# RPM-Anlagen (Red Hat, Oracle, Rocky, Alma, SUSE). Ohne das Paket
# baut build_packages.sh die uebrigen Pakete weiter und sagt, dass
# es das RPM auslaesst.
PKGS="python3 python3-venv python3-pip python3-cryptography openssl ca-certificates sqlite3 unzip curl rpm"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS

# ---------------------------------------------------------------------
# Benutzer und Verzeichnisse
# ---------------------------------------------------------------------
echo ">>> Benutzer und Verzeichnisse"
id -u co37 >/dev/null 2>&1 || \
  useradd -r -s /usr/sbin/nologin -d "$BASE" co37
mkdir -p "$BASE"/{data,data/update,update_backups,state,state/packages,db_backups}

# ---------------------------------------------------------------------
# Virtualenv
# ---------------------------------------------------------------------
echo ">>> Virtualenv"
[ -d "$BASE/venv" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
"$BASE/venv/bin/pip" install -q -r "$BASE/backend/requirements.txt"

# ---------------------------------------------------------------------
# Geheimnisse - bestehende werden nie ueberschrieben
# ---------------------------------------------------------------------
echo ">>> Schluessel"
# Den frueheren globalen Admin-Token gibt es nicht mehr. Er stammte aus
# der Zeit vor den Benutzerkonten, lief nie ab, galt auf jeder Route und
# liess sich - anders als das Passwort - unbegrenzt durchprobieren. Wer
# sich aussperrt, kommt ueber die Datenbank zurueck; der Weg steht in der
# README unter "Wenn du dich aussperrst".
#
# Eine liegengebliebene Datei aus einer aelteren Fassung wird entfernt:
# ein Geheimnis, das nichts mehr oeffnet, aber aussieht, als taete es das.
if [ -f "$BASE/data/admin.token" ]; then
  rm -f "$BASE/data/admin.token"
  echo "    Alter Admin-Token entfernt - wird nicht mehr verwendet."
fi

if [ -f "$BASE/data/secret.key" ]; then
  SECRET_KEY=$(cat "$BASE/data/secret.key")
  echo "    Vorhandener Verschluesselungsschluessel wird weiterverwendet."
else
  SECRET_KEY=$(openssl rand -base64 48)
  # Erst leer und eng anlegen, dann fuellen - genau wie fuenfzig Zeilen
  # weiter unten bei backend.env. Vorher stand hier "schreiben, danach
  # chmod 600": dazwischen lag der Schluessel, mit dem alle Checkmk-Secrets
  # verschluesselt sind, mit der Umask-Vorgabe auf der Platte, ueblich 644.
  # Steht seit der Pruefung vom 2026-08-31 unter "niedrig, liegen
  # gelassen"; derselbe Fall in den Signaturwerkzeugen ist als F-35
  # behoben. Kostet zwei Zeilen.
  umask 177
  : > "$BASE/data/secret.key"
  umask 022
  echo "$SECRET_KEY" > "$BASE/data/secret.key"
  chmod 600 "$BASE/data/secret.key"
fi

# Eigentuemer: alles root, nur die Daten gehoeren co37.
#
# Der Watcher laeuft als root und fuehrt Dateien aus diesem Verzeichnis
# aus - update_watcher.py selbst, build_packages.sh, pip aus dem venv.
# Gehoerten die co37, koennte jeder, der Code als co37 ausfuehrt, sie
# austauschen und damit root werden. Genau die Trennung, fuer die es den
# Watcher ueberhaupt gibt, waere dann keine.
#
# Der Backend-Prozess verliert dadurch nichts: er darf durch
# ProtectSystem=strict und ReadWritePaths ohnehin nur nach data/
# schreiben. Lesen und Ausfuehren bleibt ueber die Modusbits erhalten.
chown -R root:root "$BASE"
chown -R co37:co37 "$BASE/data"
chmod 700 "$BASE/data"

# state/ ist die Gegenrichtung zu data/: dorthin meldet der als root
# laufende Watcher seinen Stand, und das Backend liest ihn. Lesbar fuer
# alle, beschreibbar nur fuer root - das ist der ganze Zweck. Lag der
# Stand wie bis 0.37.5 unter data/, war jeder Schreibzugriff des Watchers
# ein Hebel nach root: co37 legt unter dem erwarteten Namen eine
# Verknuepfung ab, root schreibt hindurch und uebereignet anschliessend
# per chown das Ziel (Sicherheitspruefung 2026-08-31, F-18).
chmod 755 "$BASE/state"

# Die fertigen Agent-Pakete liegen seit 0.37.10 ebenfalls hier und nicht
# mehr unter data/. Grund ist derselbe: build_packages.sh laeuft als root
# und schreibt mit open(ziel,"wb") hinein - lag das Verzeichnis in
# co37-Gebiet, genuegte eine Verknuepfung unter dem erwarteten
# Paketnamen, um root an eine frei gewaehlte Stelle schreiben zu lassen,
# ausgeloest durch eine einzige Datei im Eingang (F-29).
# Lesbar fuer alle, damit das Backend ausliefern kann.
chmod 755 "$BASE/state/packages"

# Die taegliche Sicherung laeuft als root. Ihr Ziel darf deshalb nicht in
# data/ liegen - sqlite3 ".backup" folgt einer Verknuepfung und legt die
# Datei an, wohin sie zeigt. Damit haette co37 einmal taeglich einen
# Schreibzugriff als root an frei gewaehlter Stelle (F-20).
chmod 700 "$BASE/db_backups"

# ---------------------------------------------------------------------
# Geheimnisse fuer den Dienst
# ---------------------------------------------------------------------
# Nicht als Environment= in die Unit. Die Unit-Datei entsteht mit den
# Vorgaberechten und ist damit fuer JEDEN lokalen Benutzer lesbar - und
# 'systemctl show' gibt Environment=-Zeilen im Klartext aus. Der
# Admin-Token ist Vollzugriff auf die Schnittstelle, CO37_SECRET_KEY
# entschluesselt das Checkmk-Secret in der Datenbank.
#
# Den Inhalt einer EnvironmentFile zeigt systemctl dagegen nicht, und die
# Datei selbst gehoert root mit 600. Was bleibt, ist /proc/<pid>/environ
# fuer root und fuer co37 selbst - unvermeidbar, co37 haelt den
# Schluessel ohnehin.
#
# NICHT unter $BASE/data: das gehoert co37 und liegt in ReadWritePaths.
# Der Backend-Prozess koennte die Datei sonst ueberschreiben und sich beim
# naechsten Neustart einen eigenen Admin-Token setzen.
#
# Nur die beiden Geheimnisse. Pfade und Intervalle bleiben in der Unit,
# damit 'systemctl cat' sie weiter zeigt und auf einen Blick klar ist,
# WELCHE zwei Werte geheim sind.
#
# Ein fester Pfad, also eine Installation je Rechner. Die README setzt
# ohnehin einen Server je Netz voraus.
echo ">>> Geheimnisse"
ENVFILE=/etc/co37/backend.env
mkdir -p "$(dirname "$ENVFILE")"
# Erst leer und eng, dann fuellen: sonst stuende der Token einen Moment
# lang mit 644 auf der Platte.
umask 177
: > "$ENVFILE"
umask 022
cat > "$ENVFILE" <<EOF
CO37_SECRET_KEY=$SECRET_KEY
EOF
chown root:root "$ENVFILE"
chmod 600 "$ENVFILE"

# ---------------------------------------------------------------------
# Backend-Dienst
# ---------------------------------------------------------------------
echo ">>> Backend-Dienst"
cat > /etc/systemd/system/co37-backend.service <<EOF
[Unit]
Description=CO-37 Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=co37
Group=co37
WorkingDirectory=$BASE/backend
EnvironmentFile=$ENVFILE
Environment="CO37_DB=sqlite:///$BASE/data/co37.db"
Environment="CO37_DATA=$BASE/data"
Environment="CO37_POLL_INTERVAL=60"
Environment="CO37_OFFLINE_SECONDS=180"
# --no-proxy-headers ist Absicht und gehoert NICHT weggelassen
# (F-58 der Pruefung vom 2026-09-03).
#
# uvicorn schaltet ProxyHeadersMiddleware ab Werk EIN und glaubt
# X-Forwarded-For jedem Aufrufer aus 127.0.0.1 (forwarded_allow_ips,
# Vorgabe "127.0.0.1"). Diese Middleware schreibt scope["client"] um -
# also genau den Wert, auf dem die ganze Vertrauensentscheidung von CO-37
# aufsetzt: peer_ip() liest ihn, via_trusted_proxy() vergleicht ihn mit
# der eingetragenen Proxy-Liste, und client_ip() glaubt der Kopfzeile nur
# dann. Eine Schicht darunter war sie da schon geglaubt worden.
#
# Nachgemessen am 2026-09-03, sieben Anmeldeversuche mit je einem anderen
# erfundenen X-Forwarded-For:
#
#   ohne  --no-proxy-headers   401 401 401 401 401 401 401   (nie gesperrt)
#   mit   --no-proxy-headers   401 401 401 401 401 429 429
#
# Im Pruefprotokoll stand danach die erfundene Adresse statt 127.0.0.1.
# Das ist F-21 noch einmal, eine Ebene tiefer: dort wurde die Kopfzeile
# von der falschen Seite gelesen, hier wird sie an CO-37 vorbei geglaubt.
# Erreichbar fuer jeden, der vom Rechner selbst eine Verbindung zum
# Backend aufbauen kann.
#
# Die Entscheidung, welchem Proxy zu glauben ist, gehoert an EINE Stelle -
# und das ist die Einstellung "trusted_proxy" in CO-37, nicht die Vorgabe
# eines Servers.
ExecStart=$BASE/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT --no-proxy-headers
Restart=always
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$BASE/data
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now co37-backend
systemctl restart co37-backend

# ---------------------------------------------------------------------
# Update-Watcher
# ---------------------------------------------------------------------
if [ -f "$BASE/update_watcher.py" ]; then
  echo ">>> Update-Watcher"
  cat > /etc/systemd/system/co37-watcher.service <<EOF
[Unit]
Description=CO-37 Update-Watcher
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BASE
Environment="CO37_BASE=$BASE"
Environment="CO37_HEALTH_URL=http://127.0.0.1:$PORT/api/health"
ExecStart=/usr/bin/python3 $BASE/update_watcher.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now co37-watcher
  systemctl restart co37-watcher
fi

# ---------------------------------------------------------------------
# Datensicherung
# ---------------------------------------------------------------------
cat > /etc/cron.daily/co37-backup <<EOF
#!/bin/sh
# Ziel bewusst NICHT unter data/ - siehe die Begruendung oben bei
# db_backups. Das Verzeichnis gehoert root und ist 700.
mkdir -p $BASE/db_backups
chmod 700 $BASE/db_backups
sqlite3 $BASE/data/co37.db ".backup $BASE/db_backups/co37-\$(date +%u).db"
EOF
chmod +x /etc/cron.daily/co37-backup

# Alte Sicherungen aus der Zeit, als sie in co37-Gebiet lagen. Sie sind
# nicht falsch, stehen aber am gefaehrdeten Ort - und wer sie dort noch
# findet, haelt sie fuer den aktuellen Stand.
if [ -d "$BASE/data/backup" ]; then
  echo "    Hinweis: alte Datensicherungen liegen noch unter"
  echo "             $BASE/data/backup - neue entstehen unter"
  echo "             $BASE/db_backups. Die alten von Hand pruefen und entfernen."
fi

# ---------------------------------------------------------------------
# Abschluss
# ---------------------------------------------------------------------
sleep 3
echo
echo "======================================================================"
if systemctl is-active --quiet co37-backend; then
  echo " Dienst laeuft."
else
  echo " ACHTUNG: Dienst laeuft nicht. Pruefen mit:"
  echo "     journalctl -u co37-backend -n 40 --no-pager"
fi

IP=$(hostname -I | awk '{print $1}')
echo " Adresse:     http://$IP:$PORT"
echo
echo " Unverschluesselt - fuer den Betrieb im lokalen Netz vorgesehen."
echo " Zugriff aus anderen Netzen bitte ueber WireGuard."

echo
echo " Anmeldung beim ersten Mal mit  admin / admin"
echo " Passwort danach unter Einstellungen / Konto aendern."
echo
echo " Dateien, die in die Datensicherung gehoeren:"
echo "     $BASE/data/co37.db"
echo "     $BASE/data/secret.key   (NICHT am selben Ort wie die Datenbank)"
echo "======================================================================"
