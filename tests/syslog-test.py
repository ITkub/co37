"""
CO-37 - Weiterleitung an eine zentrale Protokollierung (F-69).

Bis 0.37.22 schrieb CO-37 ausschliesslich in die eigene Datenbank. Zwei
BSI-Bausteine verlangen etwas anderes: OPS.1.1.5.A6 ("an einer zentralen
Stelle gespeichert") und OPS.1.1.7.A15, das Anmeldeversuche UND die
Nichterreichbarkeit verwalteter Systeme ausdruecklich aufzaehlt.

WAS HIER GEPRUEFT WIRD UND WARUM GENAU DAS

Eine Weiterleitung ist leicht zu bauen und leicht falsch zu bauen. Die
drei Arten, wie so etwas schiefgeht:

  1. Sie haelt eine Anfrage auf. Ein Logserver, der nicht antwortet, ist
     der Normalfall - und ein TCP-Verbindungsaufbau laeuft dann in die
     Zeitueberschreitung des Betriebssystems. Eine Anmeldung, die zwei
     Minuten braucht, weil ein Logserver weg ist, waere ein selbst
     gebautes Verfuegbarkeitsproblem. Deshalb wird hier gegen einen
     Server gemessen, der ABSICHTLICH nicht antwortet.

  2. Sie frisst den Arbeitsspeicher. Eine unbegrenzte Warteschlange
     waechst bei einem tagelang toten Logserver, bis das Backend stirbt.

  3. Das Format traegt nicht. Ein Benutzername mit einem
     Anfuehrungszeichen oder einem Zeilenumbruch reisst den Eintrag
     auseinander - und genau solche Namen kommen bei fehlgeschlagenen
     Anmeldungen vor, weil sie der Aufrufer bestimmt.

Es laeuft ein echter Empfaenger auf einem freien Port, kein Ersatzstueck.

    python3 tests/syslog-test.py
"""
import os
import re
import socket
import socketserver
import sys
import tempfile
import threading
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/syslog.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import syslogfwd  # noqa: E402

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ======================================================================
# Empfaenger
# ======================================================================
EMPFANGEN = []


class UDPEmpfaenger(socketserver.BaseRequestHandler):
    def handle(self):
        EMPFANGEN.append(self.request[0].decode("utf-8", "replace"))


def udp_starten():
    srv = socketserver.UDPServer(("127.0.0.1", 0), UDPEmpfaenger)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


class TCPEmpfaenger(socketserver.StreamRequestHandler):
    def handle(self):
        daten = b""
        while True:
            teil = self.request.recv(4096)
            if not teil:
                break
            daten += teil
            # RFC 6587: <Laenge> <Meldung>
            while b" " in daten:
                kopf, rest = daten.split(b" ", 1)
                if not kopf.isdigit():
                    daten = rest
                    break
                n = int(kopf)
                if len(rest) < n:
                    break
                EMPFANGEN.append(rest[:n].decode("utf-8", "replace"))
                daten = rest[n:]


def tcp_starten():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TCPEmpfaenger)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def warten_auf(anzahl, sekunden=5.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if len(EMPFANGEN) >= anzahl:
            return True
        time.sleep(0.05)
    return False


# ======================================================================
# Das Format
# ======================================================================
print("--- RFC 5424 ---")
roh = syslogfwd.bauen("local0", "error", "2026-09-05T12:00:00.000000Z",
                      "ops01", "login.failed",
                      {"actor": "mike", "src": "192.168.2.10"},
                      "Anmeldung fehlgeschlagen").decode()
check("faengt mit PRI und Fassung 1 an", roh.startswith("<131>1 "), roh[:12])
# 131 = local0 (16) * 8 + error (3)
check("PRI stimmt rechnerisch", roh.startswith(f"<{16 * 8 + 3}>"), roh[:6])
check("Zeitstempel mit Zeitzone", "2026-09-05T12:00:00.000000Z" in roh)
check("Anwendungsname steht drin", " co37 " in roh)
check("MSGID ist die Aktion", "login.failed" in roh)
check("strukturierte Daten mit eigener Kennung",
      '[co37@0 actor="mike" src="192.168.2.10"]' in roh, roh)
check("der Text steht am Ende", roh.endswith("Anmeldung fehlgeschlagen"))

# Leere Felder gehoeren weggelassen, nicht als leere Zeichenkette
# mitgeschleppt - sonst steht in jedem Eintrag src="".
ohne = syslogfwd.bauen("local0", "notice", "2026-09-05T12:00:00.000000Z",
                       "h", "test", {"actor": "x", "src": None}, "t").decode()
check("leere Felder werden weggelassen", "src=" not in ohne, ohne)

print()
print("--- Was ein Angreifer in den Benutzernamen schreibt ---")
# Der Name einer fehlgeschlagenen Anmeldung kommt vom Aufrufer. Wer hier
# ausbrechen kann, faelscht Eintraege im SIEM.
boes = syslogfwd.bauen(
    "local0", "error", "2026-09-05T12:00:00.000000Z", "h", "login.failed",
    {"actor": 'x" ]injected[co37@0 actor="admin'}, "test").decode()
check("Anfuehrungszeichen werden entschaerft", '\\"' in boes, boes)
check("die schliessende Klammer ebenfalls", "\\]" in boes, boes)
# Der eigentliche Punkt ist nicht, WELCHE Zeichen entschaerft werden,
# sondern dass der Block nicht vorzeitig endet. RFC 5424 verlangt nur
# fuer ", \ und ] einen Rueckstrich - ein '[' im Wert ist harmlos,
# solange kein unentschaerftes ']' davor steht. Genau das wird gezaehlt.
offen = len(re.findall(r"(?<!\\)\]", boes))
check("genau eine unentschaerfte Klammer, und die beendet den Block",
      offen == 1, f"{offen} unentschaerfte ]")
check("der Block endet dort, wo er soll",
      boes.index("] ") == boes.rindex("]"), boes)

umbruch = syslogfwd.bauen(
    "local0", "error", "2026-09-05T12:00:00.000000Z", "h", "login.failed",
    {"actor": "erste\nzweite"}, "text\nmit umbruch").decode()
check("Zeilenumbrueche fliegen raus - sonst zwei Meldungen aus einer",
      "\n" not in umbruch, repr(umbruch))


# ======================================================================
# Zustellung ueber UDP
# ======================================================================
print()
print("--- Zustellung ueber UDP ---")
srv_udp, port_udp = udp_starten()
EMPFANGEN.clear()
d = syslogfwd.Weiterleitung()
d.einstellen(True, "127.0.0.1", port_udp, "udp", "local1")
d.melden("login.failed", "Anmeldung fehlgeschlagen", severity="error",
         actor="mike", src="192.168.2.10")
check("die Meldung kommt an", warten_auf(1), EMPFANGEN)
if EMPFANGEN:
    check("mit der eingestellten facility (local1 = 17)",
          EMPFANGEN[0].startswith(f"<{17 * 8 + 3}>"), EMPFANGEN[0][:8])
d.beenden()


# ======================================================================
# Zustellung ueber TCP
# ======================================================================
print()
print("--- Zustellung ueber TCP ---")
srv_tcp, port_tcp = tcp_starten()
EMPFANGEN.clear()
d = syslogfwd.Weiterleitung()
d.einstellen(True, "127.0.0.1", port_tcp, "tcp", "local0")
for i in range(3):
    d.melden("job.done", f"Auftrag {i}", host="ag01", job=i)
check("alle drei kommen an", warten_auf(3), len(EMPFANGEN))
check("und lassen sich einzeln trennen",
      len(EMPFANGEN) == 3 and all("Auftrag" in m for m in EMPFANGEN),
      EMPFANGEN[:1])
d.beenden()


# ======================================================================
# Der Logserver ist tot - die wichtigste Pruefung
# ======================================================================
print()
print("--- Der Logserver antwortet nicht ---")
# 203.0.113.x ist per RFC 5737 fuer Dokumentation reserviert und wird
# nirgends geroutet. Ein Verbindungsversuch dorthin laeuft ins Leere -
# genau der Fall, den wir brauchen.
d = syslogfwd.Weiterleitung()
d.einstellen(True, "203.0.113.199", 514, "tcp", "local0")
t = time.time()
for i in range(50):
    d.melden("login.failed", f"Versuch {i}", severity="error")
dauer = time.time() - t
check("fuenfzig Meldungen kosten den Aufrufer fast nichts",
      dauer < 0.1, f"{dauer * 1000:.1f} ms")
check("und zwar auch einzeln unter einer Millisekunde",
      dauer / 50 < 0.001, f"{dauer / 50 * 1e6:.0f} us je Meldung")
d.beenden(wartezeit=0.5)


# ======================================================================
# Die Warteschlange waechst nicht ins Unendliche
# ======================================================================
print()
print("--- Begrenzte Warteschlange ---")
d = syslogfwd.Weiterleitung()
# Absichtlich OHNE starten(): niemand raeumt ab, die Schlange laeuft voll.
d.aktiv, d.ziel = True, "203.0.113.199"
for i in range(syslogfwd.MAX_WARTESCHLANGE + 500):
    d.melden("test", f"Meldung {i}")
check("die Schlange bleibt bei der Obergrenze stehen",
      d.warteschlange.qsize() <= syslogfwd.MAX_WARTESCHLANGE,
      d.warteschlange.qsize())
check("und was nicht hineinpasste, wird gezaehlt",
      d.verworfen == 500, d.verworfen)
check("gezaehlt wird, nicht verschwiegen", d.verworfen > 0)


# ======================================================================
# Ausgeschaltet heisst ausgeschaltet
# ======================================================================
print()
print("--- Ab Werk aus ---")
d = syslogfwd.Weiterleitung()
check("ohne Einstellung ist die Weiterleitung aus", not d.aktiv)
d.melden("test", "darf nirgends landen")
check("und eine Meldung landet nicht einmal in der Schlange",
      d.warteschlange.qsize() == 0, d.warteschlange.qsize())

# Eingeschaltet, aber ohne Ziel: ebenfalls nichts.
d.aktiv = True
d.melden("test", "auch nicht")
check("eingeschaltet ohne Zieladresse ebenfalls nicht",
      d.warteschlange.qsize() == 0, d.warteschlange.qsize())


# ======================================================================
# TLS prueft wirklich
# ======================================================================
print()
print("--- TLS ---")
quelle = (WURZEL / "backend" / "syslogfwd.py").read_text(encoding="utf-8")
check("TLS nimmt den vorgabemaessigen Zertifikatsspeicher",
      "ssl.create_default_context()" in quelle)
check("und prueft den Namen der Gegenstelle",
      "server_hostname=self.ziel" in quelle)
check("Abschalten der Pruefung ist ausdruecklich und nicht die Vorgabe",
      "if not self.pruefe_zertifikat:" in quelle
      and "pruefe_zertifikat: bool = True" in quelle)

# Gegen einen TLS-Port, der gar kein TLS spricht: muss scheitern, nicht
# stillschweigend im Klartext senden.
EMPFANGEN.clear()
d = syslogfwd.Weiterleitung()
d.einstellen(True, "127.0.0.1", port_tcp, "tls", "local0")
d.melden("test", "darf nicht im Klartext ankommen")
time.sleep(2.0)
check("TLS gegen einen Klartext-Port faellt aus, statt unverschluesselt "
      "zu senden", not EMPFANGEN, EMPFANGEN)
d.beenden(wartezeit=0.5)


# ======================================================================
# Die Einbindung im Backend
# ======================================================================
print()
print("--- Einbindung ---")
mq = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
check("audit() leitet weiter", "syslogfwd.DIENST.melden(" in mq)
check("und zwar NACH dem Eintrag in die eigene Datenbank",
      mq.index("session.add(AuditEntry(\n        actor=kurz(")
      < mq.index("syslogfwd.DIENST.melden("))
check("Auftragsereignisse gehen ebenfalls hinaus",
      'f"job.{payload.state.value}"' in mq)
check("es gibt eine Testmeldung fuer die Oberflaeche",
      '/api/v1/syslog-settings/test' in mq)
check("das Ein- und Ausschalten steht im Pruefprotokoll",
      '"syslog.on" if payload.enabled else "syslog.off"' in mq)
# Gemessen statt im Quelltext nachgesehen: eine frische Datenbank ohne
# jede Einstellung muss zu einem ausgeschalteten Dienst fuehren. Die
# Zeichenkettensuche von vorher hat eine Mutation auf "true" nicht
# bemerkt, weil dieselbe Zeichenfolge an einer zweiten Stelle stand.
import main  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

SQLModel.metadata.create_all(main.engine)
main.syslogfwd.DIENST.aktiv = True          # absichtlich verstellen
with Session(main.engine) as _s:
    main.load_syslog_config(_s)
check("eine Anlage ohne Einstellung hat die Weiterleitung aus",
      main.syslogfwd.DIENST.aktiv is False, main.syslogfwd.DIENST.aktiv)

srv_udp.shutdown()
srv_tcp.shutdown()
print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
