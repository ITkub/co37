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
PKGS="python3 python3-venv python3-pip python3-cryptography openssl ca-certificates sqlite3 unzip curl"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $PKGS

# ---------------------------------------------------------------------
# Benutzer und Verzeichnisse
# ---------------------------------------------------------------------
echo ">>> Benutzer und Verzeichnisse"
id -u co37 >/dev/null 2>&1 || \
  useradd -r -s /usr/sbin/nologin -d "$BASE" co37
mkdir -p "$BASE"/{data,data/update,update_backups}

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
if [ -f "$BASE/data/admin.token" ]; then
  TOKEN=$(cat "$BASE/data/admin.token")
  echo "    Vorhandener Admin-Token wird weiterverwendet."
else
  TOKEN=$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-40)
  echo "$TOKEN" > "$BASE/data/admin.token"
  chmod 600 "$BASE/data/admin.token"
fi

if [ -f "$BASE/data/secret.key" ]; then
  SECRET_KEY=$(cat "$BASE/data/secret.key")
  echo "    Vorhandener Verschluesselungsschluessel wird weiterverwendet."
else
  SECRET_KEY=$(openssl rand -base64 48)
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
Environment="CO37_ADMIN_TOKEN=$TOKEN"
Environment="CO37_SECRET_KEY=$SECRET_KEY"
Environment="CO37_DB=sqlite:///$BASE/data/co37.db"
Environment="CO37_DATA=$BASE/data"
Environment="CO37_POLL_INTERVAL=60"
Environment="CO37_OFFLINE_SECONDS=180"
ExecStart=$BASE/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
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
mkdir -p $BASE/data/backup
sqlite3 $BASE/data/co37.db ".backup $BASE/data/backup/co37-\$(date +%u).db"
EOF
chmod +x /etc/cron.daily/co37-backup

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
echo " Admin-Token: $TOKEN"
echo
echo " Dateien, die in die Datensicherung gehoeren:"
echo "     $BASE/data/co37.db"
echo "     $BASE/data/secret.key   (NICHT am selben Ort wie die Datenbank)"
echo "======================================================================"
