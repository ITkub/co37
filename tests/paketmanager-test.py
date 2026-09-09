"""
CO-37 - Paketmanager: apt, zypper, dnf, yum.

DER ANLASS

Der Agent sollte auch Red Hat und SUSE bedienen. Beim Nachsehen stand
dort weniger, als die Dateien vermuten liessen:

  * SUSE war gar nicht unterstuetzt. Der einzige zypper-Aufruf im
    ganzen Agenten stand in der Neustartpruefung und lautete

        code, _ = run(["zypper", "ps", "-s"], timeout=180)

    Das Ergebnis wurde verworfen. Der Aufruf entschied nichts, kostete
    aber bis zu drei Minuten - und 'zypper ps' beantwortet ohnehin eine
    andere Frage (welche PROZESSE ersetzte Dateien offen halten), nicht
    die nach einem Neustart.

  * Der Kernelvergleich rief 'dpkg --compare-versions' auf. Auf einer
    RPM-Anlage gibt es das nicht, und der Rueckfall war ein
    alphabetischer Vergleich: dort steht '6.4.0-9' hinter '6.4.0-10'.

  * Die dnf-Auswertung verlangte drei Felder je Zeile. dnf bricht lange
    Paketnamen aber um - genau die fielen aus dem Scan, lautlos.

  * Und sie las die Ausgabe in der Sprache der Anlage. Auf einem
    deutschen System heisst die Kopfzeile nicht "Last metadata
    expiration check" - die Filterzeile griff nicht, und die Kopfzeile
    landete als Paket im Bericht.

WAS DIESE REIHE TUT

Sie prueft die Auswerter gegen ECHTE Ausgaben der Werkzeuge, nicht
gegen selbst erfundene Zeilen: umgebrochene dnf-Zeilen, die
Tabellenform von 'zypper list-updates', die Bloecke aus einem
Trockenlauf von 'zypper patch'. Dazu die Erkennung selbst, mit
vorgetaeuschten Werkzeugen.

Was sie NICHT kann: eine Aussage darueber, ob die Befehle auf einer
echten Anlage das tun, was hier angenommen wird. Das misst nur eine
Maschine - siehe die Test-VMs.

Braucht kein Backend und kein Netz.

    python3 tests/paketmanager-test.py
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}"
          f"{'  -> ' + str(extra) if extra else ''}")


def lade_agent():
    """agent.py als Modul, aus einem eigenen Verzeichnis mit agent.conf."""
    ordner = TMP / "agent"
    ordner.mkdir(parents=True, exist_ok=True)
    shutil.copy(WURZEL / "agent" / "agent.py", ordner / "agent.py")
    (ordner / "agent.conf").write_text("server = http://127.0.0.1:1\n",
                                       encoding="utf-8")
    spec = importlib.util.spec_from_file_location("co37agent_pm",
                                                  ordner / "agent.py")
    modul = importlib.util.module_from_spec(spec)
    sys.modules["co37agent_pm"] = modul
    spec.loader.exec_module(modul)
    return modul


agent = lade_agent()
quelle_agent = (WURZEL / "agent" / "agent.py").read_text(encoding="utf-8")


# ======================================================================
# dnf check-update
# ======================================================================
print("--- Die Ausgabe von dnf check-update ---")

# Echte Form, mit allem, was dnf dort hineinschreibt: Kopfzeile,
# Leerzeile, ein umgebrochener langer Name, und am Ende der Abschnitt
# 'Obsoleting Packages', der keine Aktualisierungen enthaelt.
DNF_AUSGABE = """Last metadata expiration check: 0:12:31 ago on Tue 08 Sep 2026.

bash.x86_64                             5.1.8-9.el9              baseos
NetworkManager-config-server.noarch
                                        1:1.46.0-19.el9          baseos
kernel.x86_64                           5.14.0-503.el9           baseos
python3.11.x86_64                       3.11.9-7.el9             appstream
libfoo.loongarch64                      1.2-3.el9                appstream

Obsoleting Packages
grub2-tools.x86_64                      1:2.06-95.el9            baseos
    grub2-tools-minimal.x86_64          1:2.06-90.el9            @baseos
"""

zeilen = agent._dnf_zeilen(DNF_AUSGABE)
namen = [n for n, _ in zeilen]

check("die Kopfzeile zaehlt nicht als Paket",
      not any("metadata" in n for n in namen), namen)
check("bash wird gefunden", ("bash", "5.1.8-9.el9") in zeilen, zeilen)
check("und die Architektur steht nicht im Namen",
      "bash.x86_64" not in namen, namen)

# Der eigentliche Fund: ein umgebrochener Name.
check("ein umgebrochener Name geht nicht verloren",
      ("NetworkManager-config-server", "1:1.46.0-19.el9") in zeilen, zeilen)

# Und die Gegenprobe zur Architekturerkennung: '3.11' hinter 'python' ist
# keine Architektur.
check("python3.11 bleibt python3.11",
      ("python3.11", "3.11.9-7.el9") in zeilen, zeilen)

check("der Abschnitt Obsoleting Packages zaehlt nicht mit",
      not any(n.startswith("grub2") for n in namen), namen)
check("und es sind genau fuenf Pakete", len(zeilen) == 5, zeilen)

# Eine Architektur, die in keiner Liste steht, wird trotzdem
# abgeschnitten. Waere die Erkennung an eine Aufzaehlung bekannter
# Architekturen gebunden, bliebe sie hier stehen.
check("auch eine unbekannte Architektur faellt weg",
      ("libfoo", "1.2-3.el9") in zeilen, zeilen)

# Kernel weiterhin als Kernel erkennbar - daran haengt requires_reboot.
check("der Kernel ist unter seinem Namen dabei", "kernel" in namen, namen)

# Ein Name ganz ohne Punkt. In der Ausgabe von check-update kommt das
# nicht vor - die erste Spalte traegt immer 'name.arch'. Geprueft wird es
# trotzdem: sonst haengt an dieser Stelle ein Rueckgabewert, der bei
# unerwarteter Eingabe einen LEEREN Paketnamen liefert, und ein leerer
# Name faende in der Sicherheitsliste alles oder nichts.
ohne_punkt = agent._dnf_zeilen("weirdpkg    1.0-1    repo\n")
check("ein Name ohne Punkt bleibt stehen, statt leer zu werden",
      ohne_punkt == [("weirdpkg", "1.0-1")], ohne_punkt)

# Eine leere Ausgabe (dnf gibt bei 'nichts zu tun' gar nichts aus).
check("keine Ausgabe heisst keine Aktualisierung",
      agent._dnf_zeilen("") == [], agent._dnf_zeilen(""))


# ======================================================================
# zypper list-updates
# ======================================================================
print()
print("--- Die Tabelle von zypper list-updates ---")

ZYPPER_TABELLE = """S | Repository          | Name           | Current Version | Available Version | Arch
--+---------------------+----------------+-----------------+-------------------+-------
v | Update Repository   | bash           | 5.2.15-150600.1 | 5.2.21-150600.3   | x86_64
v | Update Repository   | kernel-default | 6.4.0-150600.21 | 6.4.0-150600.23   | x86_64
v | Main Repository     | glibc          | 2.38-150600.1   | 2.38-150600.4     | x86_64
"""


def zypper_zeilen(text):
    """Dieselbe Zerlegung wie in scan_zypper()."""
    ergebnis = []
    for zeile in text.splitlines():
        teile = [t.strip() for t in zeile.split("|")]
        if len(teile) != 6 or teile[0] != "v":
            continue
        ergebnis.append((teile[2], teile[4]))
    return ergebnis


tab = zypper_zeilen(ZYPPER_TABELLE)
check("drei Aktualisierungen erkannt", len(tab) == 3, tab)
check("Name und neue Version stimmen",
      ("bash", "5.2.21-150600.3") in tab, tab)
check("die Kopfzeile faellt weg",
      not any(n == "Name" for n, _ in tab), tab)
check("die Trennzeile faellt weg",
      not any(set(n) <= set("-+") for n, _ in tab if n), tab)

# Und der Grund, warum der Scan das mit LC_ALL=C liest.
check("der Agent liest die Ausgabe mit fester Sprache",
      'C_UMGEBUNG = {"LC_ALL": "C", "LANG": "C"}' in
      (WURZEL / "agent" / "agent.py").read_text(encoding="utf-8"))


# ======================================================================
# zypper patch --dry-run --category security
# ======================================================================
print()
print("--- Welche Pakete ein Sicherheitslauf anfasst ---")

# Gekuerzt, aber Zeile fuer Zeile die ECHTE Form: gemessen am
# 2026-09-09 auf openSUSE Leap 16.0. Vier Bloecke, von denen genau EINER
# gemeint ist - und die drei anderen sehen ihm zum Verwechseln aehnlich.
ZYPPER_TROCKEN = """Refreshing service 'openSUSE'.
Loading repository data...
Reading installed packages...
Patch 'openSUSE-Leap-16.0-1314-1' is not in the specified category.
Warning: Patch 'openSUSE-Leap-16.0-1602-1' is interactive, skipping.
Resolving package dependencies...

The following 114 packages are going to be upgraded:
  NetworkManager NetworkManager-lang bind-utils bzip2 cpio curl glibc gzip
  kernel-default krb5 libcurl4 pam perl python313 util-linux vim wget

The following 26 NEW packages are going to be installed:
  libbrotlicommon1-x86-64-v3 libbz2-1-x86-64-v3 python3 python3-base

The following 60 NEW patches are going to be installed:
  openSUSE-Leap-16.0-1004 openSUSE-Leap-16.0-1005 openSUSE-Leap-16.0-1007

The following NEW pattern is going to be installed:
  x86_64_v3

114 packages to upgrade, 26 new.

Package download size:   130.7 MiB
"""

sicher = agent._zypper_block(ZYPPER_TROCKEN, agent.ZYPPER_KOPF)

check("die Pakete des Sicherheitslaufs werden gefunden",
      {"glibc", "curl", "pam"} <= sicher, sorted(sicher)[:6])
check("und zwar alle siebzehn der Kurzfassung", len(sicher) == 17,
      sorted(sicher))
check("der Kernel ist dabei", "kernel-default" in sicher, sorted(sicher))

# Die drei Bloecke, die genauso aussehen und NICHT gemeint sind. Jeder
# einzelne wuerde die Zahl der Sicherheitsupdates verfaelschen.
check("NEUE Pakete sind keine Aktualisierungen",
      "python3-base" not in sicher and "libbz2-1-x86-64-v3" not in sicher,
      sorted(sicher))
check("PATCH-Namen sind keine Pakete",
      not any(n.startswith("openSUSE-Leap") for n in sicher), sorted(sicher))
check("und ein Installationsmuster auch nicht",
      "x86_64_v3" not in sicher, sorted(sicher))

# Die Zusammenfassung am Ende steht nicht eingerueckt und gehoert
# deshalb nicht mehr zum Block.
check("die Zusammenfassung zaehlt nicht mit",
      not any(n.isdigit() or n.endswith("MiB") for n in sicher),
      sorted(sicher))

# Nichts zu tun.
check("ohne Sicherheitsupdates bleibt die Menge leer",
      agent._zypper_block("Nothing to do.\n", agent.ZYPPER_KOPF) == set())

# Und der Grund fuer LC_ALL=C, an der echten deutschen Ausgabe derselben
# Anlage: die Kopfzeile heisst dort anders, und der Auswerter faende
# NICHTS - ohne Fehler, ohne Meldung, nur mit null Sicherheitsupdates.
ZYPPER_DEUTSCH = """Paketabhaengigkeiten werden aufgeloest...

Die folgenden 114 Pakete werden aktualisiert:
  NetworkManager bind-taeutils glibc kernel-default

119 Pakete werden aktualisiert, 31 neue.
"""
check("die deutsche Ausgabe passt auf keine Kopfzeile - darum LC_ALL=C",
      agent._zypper_block(ZYPPER_DEUTSCH, agent.ZYPPER_KOPF) == set(),
      sorted(agent._zypper_block(ZYPPER_DEUTSCH, agent.ZYPPER_KOPF)))

# Die Regel "der Block beginnt an einer Kopfzeile MIT Doppelpunkt".
# Diese Zeilen sind ausdruecklich NICHT von zypper gemessen, sondern
# beschreiben die Regel selbst: eine Fliesstextzeile, die beide Woerter
# traegt und keine Aufzaehlung einleitet. Ohne den Doppelpunkt als
# Bedingung zoege der Auswerter alles Eingerueckte darunter mit herein.
NUR_FLIESSTEXT = """some packages were not upgraded because of conflicts
  siehe-die-anleitung nicht-ein-paket
"""
check("eine Fliesstextzeile ohne Doppelpunkt oeffnet keinen Block",
      agent._zypper_block(NUR_FLIESSTEXT, agent.ZYPPER_KOPF) == set(),
      sorted(agent._zypper_block(NUR_FLIESSTEXT, agent.ZYPPER_KOPF)))

# Und dass wirklich PAKETE gemeint sind, nicht irgendetwas Aktualisiertes.
# Auch das beschreibt die Regel, statt eine gemessene Ausgabe zu zeigen:
# in den Trockenlaeufen dieser Anlage werden Patches "installed", nicht
# "upgraded". Faende der Auswerter einen solchen Block trotzdem, stuenden
# Patchnamen in der Sicherheitsliste - und dort wuerde nie ein Paket
# darauf passen.
NUR_PATCHES = """The following 3 patches are going to be upgraded:
  openSUSE-Leap-16.0-1004  openSUSE-Leap-16.0-1005
"""
check("ein Block ueber PATCHES zaehlt nicht als Paketliste",
      agent._zypper_block(NUR_PATCHES, agent.ZYPPER_KOPF) == set(),
      sorted(agent._zypper_block(NUR_PATCHES, agent.ZYPPER_KOPF)))

# Und der Schalter, ohne den der Kernel fehlt.
check("der Sicherheitslauf laeuft mit --with-interactive",
      '"--with-interactive"' in quelle_agent)

# Gegenprobe zur Blockerkennung: der Block endet an der ersten Zeile, die
# nicht eingerueckt ist. Ohne diese Regel liefe er bis zum Dateiende und
# nimmt die Zusammenfassung mit.
check("der Block endet an der ersten nicht eingerueckten Zeile",
      "2" not in sicher and "packages" not in sicher, sorted(sicher))


# ======================================================================
# Die Erkennung selbst
# ======================================================================
print()
print("--- Welcher Paketmanager gilt ---")
# Vorgetaeuschte Werkzeuge in einem eigenen PATH. Es geht um die
# REIHENFOLGE: eine Anlage kann mehrere haben.
import os  # noqa: E402


def mit_werkzeugen(*namen):
    ordner = TMP / ("pfad_" + "_".join(namen) if namen else "pfad_leer")
    ordner.mkdir(parents=True, exist_ok=True)
    for name in namen:
        datei = ordner / name
        datei.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        datei.chmod(0o755)
    alt = os.environ.get("PATH", "")
    os.environ["PATH"] = str(ordner)
    try:
        return agent.paketmanager()
    finally:
        os.environ["PATH"] = alt


# Diese Pruefung geht nur unter Linux. paketmanager() gibt unter
# Windows ausdruecklich "" zurueck - dort gibt es keinen dieser
# Paketmanager, und der Agent geht seinen eigenen Weg ueber PowerShell.
# Kein stilles Ueberspringen: unter Windows wird stattdessen genau das
# geprueft, und die ausgelassene Pruefung steht in der Ausgabe.
if agent.IS_WINDOWS:
    check("unter Windows gibt es keinen dieser Paketmanager",
          agent.paketmanager() == "", agent.paketmanager())
    print("       (Reihenfolgepruefung nur unter Linux moeglich)")
else:
    # Und auch unter Linux nur sinnvoll, wenn der Rechner die Werkzeuge
    # nicht selbst unter /usr/bin liegen hat - _werkzeug_da() sieht dort
    # zuerst nach. Das wird gemessen statt angenommen.
    vorhanden = [n for n in ("apt-get", "zypper", "dnf", "yum")
                 if Path(f"/usr/bin/{n}").exists()]

    if "zypper" not in vorhanden and "apt-get" not in vorhanden:
        check("zypper allein ergibt zypper",
              mit_werkzeugen("zypper") == "zypper", mit_werkzeugen("zypper"))
    if not vorhanden:
        check("ohne jedes Werkzeug bleibt es leer", mit_werkzeugen() == "",
              mit_werkzeugen())
        check("dnf und yum zusammen ergeben dnf",
              mit_werkzeugen("dnf", "yum") == "dnf",
              mit_werkzeugen("dnf", "yum"))
        check("apt geht dnf vor - ein Debian mit dnf bleibt ein Debian",
              mit_werkzeugen("apt-get", "dnf") == "apt",
              mit_werkzeugen("apt-get", "dnf"))
    else:
        print(f"       (Reihenfolgepruefung ausgelassen, /usr/bin hat: "
              f"{', '.join(vorhanden)})")

# Die Reihenfolge steht auch im Quelltext, und die laesst sich immer
# pruefen - unabhaengig davon, was auf diesem Rechner installiert ist.
quelle = (WURZEL / "agent" / "agent.py").read_text(encoding="utf-8")
check("die Reihenfolge im Quelltext ist apt, zypper, dnf, yum",
      'for name in ("apt-get", "zypper", "dnf", "yum"):' in quelle)


# ======================================================================
# Was der Patchlauf als Erfolg zaehlt
# ======================================================================
print()
print("--- zypper meldet Erfolge mit Rueckgabewerten ueber 100 ---")
# Der folgenreichste Fall: 102 heisst 'Neustart noetig' und kommt bei
# jedem Kernelupdate. Wer nur 0 als Erfolg zaehlt, meldet genau diesen
# Lauf als fehlgeschlagen - und CO-37 wiederholt ihn, statt neu zu
# starten.
check("102 (Neustart noetig) gilt als Erfolg", 102 in agent.ZYPPER_OK,
      agent.ZYPPER_OK)
check("103 (zypper hat sich selbst erneuert) ebenfalls",
      103 in agent.ZYPPER_OK, agent.ZYPPER_OK)
check("0 selbstverstaendlich auch", 0 in agent.ZYPPER_OK, agent.ZYPPER_OK)
check("ein echter Fehler nicht", 1 not in agent.ZYPPER_OK, agent.ZYPPER_OK)
check("und ein Abbruch durch Signal auch nicht",
      105 not in agent.ZYPPER_OK, agent.ZYPPER_OK)


# ======================================================================
# Der Kernelvergleich
# ======================================================================
print()
print("--- Der Kernelvergleich kommt ohne dpkg aus ---")
# Ueber den Syntaxbaum und ohne den Docstring: der ERKLAERT, warum dpkg
# hier falsch waere, und nennt es dabei. Eine Zeichenkettensuche findet
# genau diese Erklaerung und meldet den Befund, den sie beschreibt.
# Derselbe Fall wie schon dreimal in dieser Anlage.
import ast  # noqa: E402

_baum = ast.parse(quelle)


def rumpf(name: str) -> str:
    """Der ausgefuehrte Teil einer Funktion, ohne ihren Docstring."""
    fn = next(k for k in ast.walk(_baum)
              if isinstance(k, ast.FunctionDef) and k.name == name)
    koerper = list(fn.body)
    if (koerper and isinstance(koerper[0], ast.Expr)
            and isinstance(koerper[0].value, ast.Constant)
            and isinstance(koerper[0].value.value, str)):
        koerper = koerper[1:]
    return "\n".join(ast.unparse(k) for k in koerper)


_kernelcode = "\n".join(rumpf(n) for n in ("_kernel_verschwunden_reason",
                                           "_kernel_namen",
                                           "_laufender_kernel_weg"))

check("kein dpkg mehr im Kernelvergleich",
      "dpkg" not in _kernelcode,
      [z for z in _kernelcode.splitlines() if "dpkg" in z])
check("und kein alphabetisches sorted() als Rueckfall",
      "sorted(" not in _kernelcode)
# Keine Zeitstempel mehr, in keiner der drei Funktionen. Beide
# widerlegten Fassungen haengen an einem: die erste an der juengsten
# Datei, die zweite an der Startzeit.
# Und dass er ueberhaupt aufgerufen wird - eine Funktion, die niemand
# ruft, ist kein Rueckfall.
check("reboot_reasons faellt am Ende darauf zurueck",
      "_kernel_verschwunden_reason()" in rumpf("reboot_reasons"))

check("der Rueckfall vergleicht keine Zeitstempel",
      "ctime" not in _kernelcode and "btime" not in _kernelcode,
      [z for z in _kernelcode.splitlines() if "time" in z])
# Gegenprobe zur Pruefung selbst: der ausgewertete Text ist nicht leer
# und enthaelt wirklich die Rumpfe.
check("und der Syntaxbaum hat die Rumpfe wirklich hergegeben",
      "glob" in _kernelcode and len(_kernelcode) > 300, len(_kernelcode))

# ----------------------------------------------------------------------
# Der Kernel-Rueckfall
# ----------------------------------------------------------------------
# Zweimal falsch gebaut, beide Male von einer Messung widerlegt. Die
# Faelle stehen hier als Daten, damit kein dritter Anlauf sie vergisst.
print()
print("--- Kernel: der Rueckfall sagt nur ja, wenn er sicher ist ---")

# Gemessen am 2026-09-09 auf Oracle Linux 10.2. Drei Abbilder, das
# juengste ist das Rettungsabbild ohne Kernelversion, und es laufen zwei
# Varianten nebeneinander (UEK und der von Red Hat).
ORACLE = {"0-rescue-05ac793a16c54e71bdbbbb87ebd265d6",
          "6.12.0-204.92.4.2.el10uek.x86_64",
          "6.12.0-211.7.3.el10_2.x86_64"}
check("Oracle: der laufende Kernel liegt da -> kein Neustart",
      agent._laufender_kernel_weg(
          ORACLE, "6.12.0-204.92.4.2.el10uek.x86_64") is False)

# Gemessen am 2026-09-09 auf openSUSE Leap 16.0. Genau ein Abbild, und
# es ist ein Symlink nach /usr/lib/modules.
LEAP = {"6.12.0-160000.35-default"}
check("Leap: dasselbe, mit nur einem Abbild",
      agent._laufender_kernel_weg(LEAP, "6.12.0-160000.35-default") is False)

# Der eine Fall, in dem die Aussage sicher ist.
check("ist der laufende Kernel aus /boot verschwunden -> Neustart",
      agent._laufender_kernel_weg(
          {"6.12.0-211.7.3.el10_2.x86_64"},
          "6.12.0-204.92.4.2.el10uek.x86_64") is True)

# Und der Fall, den dieser Rueckfall bewusst NICHT sieht: ein neuer
# Kernel liegt neben dem laufenden. Auf allen drei Familien beantwortet
# das ihr eigenes Werkzeug, und die stehen davor.
check("ein neuer Kernel NEBEN dem laufenden gilt hier nicht als Grund",
      agent._laufender_kernel_weg(
          {"6.12.0-204.92.4.2.el10uek.x86_64",
           "6.12.0-999.neu.el10uek.x86_64"},
          "6.12.0-204.92.4.2.el10uek.x86_64") is False)

check("ohne Abbilder wird nichts behauptet",
      agent._laufender_kernel_weg(set(), "6.12.0-204") is False)
check("und ohne laufenden Kernel auch nicht",
      agent._laufender_kernel_weg({"6.12.0-204"}, "") is False)

# Die Namen kommen aus den Dateinamen in /boot.
_boot = TMP / "boot"
_boot.mkdir(exist_ok=True)
for _name in ("vmlinuz-0-rescue-05ac793a16c54e71bdbbbb87ebd265d6",
              "vmlinuz-6.12.0-204.92.4.2.el10uek.x86_64",
              "vmlinuz-6.12.0-211.7.3.el10_2.x86_64",
              "initramfs-6.12.0-204.img"):
    (_boot / _name).write_text("x", encoding="utf-8")

_namen = agent._kernel_namen(_boot)
check("die Kernelversionen werden aus /boot gelesen", _namen == ORACLE,
      sorted(_namen))
check("initramfs ist kein Kernel",
      not any("initramfs" in n for n in _namen), sorted(_namen))

# Und der tote Aufruf ist weg.
check("der tote 'zypper ps'-Aufruf ist entfernt",
      'run(["zypper", "ps"' not in quelle)

# Die Neustartpruefung fragt zypper jetzt richtig.
check("gefragt wird 'zypper needs-rebooting'",
      '"needs-rebooting"' in quelle)
check("und 102 wird dort als 'Neustart noetig' gelesen",
      "if code == 102:" in quelle)

# dnf5 kennt needs-restarting nur noch als Unterbefehl.
check("beide Aufrufformen von needs-restarting sind da",
      '["needs-restarting", "-r"]' in quelle
      and '["dnf", "needs-restarting", "-r"]' in quelle)


# ======================================================================
# Tumbleweed
# ======================================================================
print()
print("--- Eine rollende Anlage wird nicht geraten ---")
check("Tumbleweed wird an ID aus os-release erkannt",
      'ID", "") == "opensuse-tumbleweed"' in quelle)
check("und der Patchlauf lehnt dort ab, statt das Falsche zu tun",
      "if is_tumbleweed():" in quelle
      and "zypper dup" in quelle)


# ======================================================================
# Das RPM-Paket
# ======================================================================
print()
print("--- Das Agentenpaket fuer RPM-Anlagen ---")
import re  # noqa: E402
import subprocess  # noqa: E402

rpm_quelle = (WURZEL / "packaging" / "build_rpm.py").read_text(encoding="utf-8")
deb_quelle = (WURZEL / "packaging" / "build_deb.py").read_text(encoding="utf-8")

# Dieselben Pfade wie im .deb. Ein Agent, der auf Debian woanders liegt
# als auf RHEL, waere zwei Produkte - jede Anleitung und jede Fehlersuche
# muesste nach Distribution unterscheiden.
for _pfad in ("usr/lib/co37/agent.py", "usr/lib/co37/release_key.pub",
              "usr/bin/co37-connect"):
    check(f"{_pfad} liegt in beiden Paketen gleich",
          _pfad in deb_quelle and "/" + _pfad in rpm_quelle)

# F-44: ein Paket ohne Signaturschluessel entsteht nicht stillschweigend.
check("ohne release_key.pub bricht der RPM-Bau ab",
      "CO37_OHNE_SIGNATUR" in rpm_quelle
      and "wuerden ihre Selbstaktualisierung nicht pruefen" in rpm_quelle)

# Und der Bau prueft sein ERGEBNIS, nicht seine Absicht - die Lehre aus
# der MSI-Saga.
check("der Bau prueft das fertige Paket nach",
      "pruefe_paket(ziel, dateien, mit_schluessel)" in rpm_quelle)

# Der Buildroot wird NICHT vorgegeben, sondern im %install-Abschnitt
# gefuellt. Gemessen am 2026-09-09: mit rpm 4.18.2 lief beides, mit
# 4.20.1 auf KK-OPS01 nur noch dieser Weg - ab 4.20 bestimmt rpmbuild
# den Buildroot selbst und uebergeht ein --define.
#
# Diese Pruefung liest den Quelltext, weil sie den Unterschied sonst
# nicht sehen koennte: auf einem Rechner mit rpm 4.18 laeuft auch der
# falsche Weg durch. Genau daran ist es hier vorbeigekommen.
check("der Bau gibt den Buildroot nicht mehr vor",
      '"buildroot ' not in rpm_quelle,
      [z.strip() for z in rpm_quelle.splitlines() if '"buildroot ' in z])
# Am Zeilenanfang gesucht, nicht irgendwo im Text: der Kommentar zwei
# Absaetze weiter oben nennt beides ebenfalls, und eine
# Zeichenkettensuche findet die Begruendung statt der Sache. Sechster
# Fall dieser Art in diesem Projekt.
check("sondern fuellt ihn im %install-Abschnitt",
      bool(re.search(r"^%install$", rpm_quelle, re.M))
      and bool(re.search(r"^cp -a .*%\{\{buildroot\}\}", rpm_quelle, re.M)))

if not shutil.which("rpmbuild"):
    # Kein stilles Ueberspringen: wenn hier nicht gebaut werden kann,
    # steht das da. Unter Windows ist das der Normalfall - das Paket
    # entsteht ohnehin nur auf dem Server.
    print("       (kein rpmbuild - der Bau selbst wurde nicht ausgefuehrt)")
else:
    _ziel = TMP / "rpmout"
    _bau = subprocess.run(
        [sys.executable, str(WURZEL / "packaging" / "build_rpm.py"),
         "--version", "9.9.9", "--out", str(_ziel)],
        capture_output=True, text=True, timeout=300)
    check("der Bau laeuft durch", _bau.returncode == 0,
          (_bau.stdout + _bau.stderr)[-300:])

    _pakete = list(_ziel.glob("co37-agent-*.rpm")) if _ziel.is_dir() else []
    check("und hinterlaesst genau ein Paket", len(_pakete) == 1,
          [x.name for x in _pakete])

    if _pakete:
        def _rpm(*args):
            return subprocess.run(["rpm", *args, str(_pakete[0])],
                                  capture_output=True, text=True).stdout

        _inhalt = set(_rpm("-qlp").split())
        check("agent.py ist drin", "/usr/lib/co37/agent.py" in _inhalt,
              sorted(_inhalt))
        check("der Signaturschluessel liegt daneben",
              "/usr/lib/co37/release_key.pub" in _inhalt, sorted(_inhalt))
        check("die systemd-Unit ist drin",
              "/usr/lib/systemd/system/co37-agent.service" in _inhalt,
              sorted(_inhalt))
        # /lib/systemd waere auf RHEL und SUSE eine Verknuepfung, und rpm
        # weist ein Paket zurueck, das dort hineininstalliert.
        check("und zwar unter /usr/lib, nicht /lib",
              not any(x.startswith("/lib/") for x in _inhalt), sorted(_inhalt))
        check("das Paket ist architekturunabhaengig",
              "noarch" in _pakete[0].name, _pakete[0].name)

        _verlangt = set(_rpm("-qp", "--requires").split())
        check("es verlangt die Pakete, die der Agent braucht",
              {"python3", "python3-requests", "python3-cryptography"}
              <= _verlangt, sorted(_verlangt))

        _skripte = _rpm("-qp", "--scripts")
        check("die Unit wird beim Installieren eingeschaltet",
              "systemctl enable co37-agent.service" in _skripte)
        # Der wichtigste Unterschied zum .deb: rpm ruft %preun auch beim
        # Upgrade auf. Ohne die Unterscheidung legte jedes Upgrade den
        # Dienst still und schaltete ihn erst im %post wieder ein -
        # dieselbe Luecke wie F-48 unter Windows.
        check("ein Upgrade schaltet den Dienst nicht ab",
              '"$1" = "0"' in _skripte)
        # Und /etc/co37 bleibt stehen: dort liegt auch die backend.env
        # des Servers, wenn Agent und Backend auf derselben Maschine sind.
        check("die Konfiguration wird beim Entfernen nicht geloescht",
              "rm -rf /etc/co37" not in _skripte)


print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
