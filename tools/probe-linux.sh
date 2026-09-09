#!/bin/sh
# CO-37 - Messung der Paketmanager-Umgebung eines Linux-Zielsystems.
#
# Liest nur, aendert nichts, installiert nichts. Gedacht fuer zwei Faelle:
#
#   1. Vor jeder Aenderung am RPM-Weg oder an den zypper-/dnf-Aufrufen auf
#      den Test-VMs laufen lassen und die Ausgabe mit der vorherigen
#      vergleichen. Fuenf Irrtuemer der Fassungen 0.37.25 bis 0.37.31 sind
#      nur so aufgefallen - drei davon haette keine Pruefreihe gefunden.
#   2. Wenn CO-37 auf einem System keine Updates oder keinen Neustartbedarf
#      erkennt: die Ausgabe zeigt, ob das Werkzeug fehlt, anders heisst oder
#      einen unerwarteten Rueckgabewert liefert.
#
# Warum die Rueckgabewerte einzeln ausgegeben werden: der Agent entscheidet
# an ihnen, nicht am Text. 'zypper needs-rebooting' meldet 0/100/102,
# 'needs-restarting -r' 0/1 - und auf einem System ohne das Werkzeug gibt es
# gar keinen. Genau dieser Unterschied war zweimal die Fehlerursache.
#
# Aufruf auf dem Zielsystem, als beliebiger Benutzer:
#     sh probe-linux.sh > probe-$(hostname).txt 2>&1
#
# Die Ausgabe enthaelt Systemnamen und Paketstaende, sonst nichts.
export LC_ALL=C LANG=C
echo "=== os-release ==="
grep -E '^(ID|VERSION_ID|PRETTY_NAME)=' /etc/os-release

echo
echo "=== Werkzeuge ==="
for w in apt-get zypper dnf yum needs-restarting rpm python3; do
  p=$(command -v "$w" 2>/dev/null)
  echo "$w: ${p:-FEHLT}$( [ -x "/usr/bin/$w" ] && echo '  (auch /usr/bin)' )"
done
python3 -c 'import sys;print("python:",sys.version.split()[0])' 2>/dev/null

echo
echo "=== Kernel und /boot ==="
uname -r
ls -la --time=ctime /boot/vmlinuz-* 2>/dev/null | head -10

echo
echo "=== Neustart noetig? (Rueckgabewerte) ==="
if command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive needs-rebooting >/dev/null 2>&1
  echo "zypper needs-rebooting -> $?"
fi
if command -v needs-restarting >/dev/null 2>&1; then
  needs-restarting -r >/dev/null 2>&1
  echo "needs-restarting -r -> $?"
else
  echo "needs-restarting -r -> WERKZEUG FEHLT"
fi
if command -v dnf >/dev/null 2>&1; then
  dnf needs-restarting -r >/dev/null 2>&1
  echo "dnf needs-restarting -r -> $?"
fi
[ -e /var/run/reboot-required ] && echo "/var/run/reboot-required existiert"

echo
echo "=== SELinux ==="
command -v getenforce >/dev/null 2>&1 && getenforce || echo "kein getenforce"

echo
echo "=== Update-Liste (erste 40 Zeilen, roh) ==="
if command -v dnf >/dev/null 2>&1; then
  dnf -q check-update > /tmp/co37-upd.txt 2>&1
  echo "dnf check-update -> $?  ($(wc -l < /tmp/co37-upd.txt) Zeilen)"
  cat -A /tmp/co37-upd.txt | head -40 | sed 's/\$$//'
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive refresh >/dev/null 2>&1
  zypper --non-interactive --quiet list-updates > /tmp/co37-upd.txt 2>&1
  echo "zypper list-updates -> $?  ($(wc -l < /tmp/co37-upd.txt) Zeilen)"
  head -40 /tmp/co37-upd.txt
fi

echo
echo "=== Sicherheitsupdates (erste 30 Zeilen, roh) ==="
if command -v dnf >/dev/null 2>&1; then
  dnf -q check-update --security > /tmp/co37-sec.txt 2>&1
  echo "dnf check-update --security -> $?  ($(wc -l < /tmp/co37-sec.txt) Zeilen)"
  head -30 /tmp/co37-sec.txt
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive patch --dry-run --category security > /tmp/co37-sec.txt 2>&1
  echo "zypper patch --dry-run --category security -> $?  ($(wc -l < /tmp/co37-sec.txt) Zeilen)"
  head -30 /tmp/co37-sec.txt
fi
echo
echo "=== ENDE ==="
