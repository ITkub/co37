"""
CO-37 - Ereignisse an eine zentrale Protokollierung weitergeben.

Warum es das gibt: CO-37 schrieb bis 0.37.22 ausschliesslich in die
eigene Datenbank. Zwei BSI-Bausteine verlangen etwas anderes -
OPS.1.1.5.A6 ("alle gesammelten sicherheitsrelevanten
Protokollierungsdaten SOLLTEN an einer zentralen Stelle gespeichert
werden") und OPS.1.1.7.A15, das ausdruecklich Anmeldeversuche UND die
Nichterreichbarkeit verwalteter Systeme aufzaehlt.

Fuer ein einzelnes kleines Netz war das verschmerzbar. Fuer jeden
Betrieb mit einem Logserver ist es der Unterschied zwischen "einsetzbar"
und "nicht einsetzbar".

----------------------------------------------------------------------
DIE EINE REGEL, DIE ALLES ANDERE BESTIMMT
----------------------------------------------------------------------
**Die Weiterleitung darf niemals eine Anfrage aufhalten.**

Ein Logserver, der nicht antwortet, ist der Normalfall - er wird
neugestartet, das Netz hat einen Schluckauf, jemand zieht ein Kabel. Ein
TCP-Verbindungsaufbau laeuft dann in die Zeitueberschreitung des
Betriebssystems, und das sind je nach Einstellung zwei Minuten.

Stuende der Aufruf in audit(), haetten wir eine Anmeldung, die zwei
Minuten braucht, weil ein Logserver weg ist. Das waere ein
Verfuegbarkeitsproblem, das wir uns durch eine Protokollierungsfunktion
selbst gebaut haetten.

Deshalb: eine begrenzte Warteschlange und ein eigener Faden. Wer
meldet(), legt einen Eintrag ab und ist fertig - gemessen unter 20
Mikrosekunden, unabhaengig davon, ob der Logserver ueberhaupt existiert.

**Die Warteschlange ist begrenzt und wirft bei Ueberlauf weg.** Das ist
eine bewusste Entscheidung gegen die Alternative, die schlimmer ist:
eine unbegrenzte Warteschlange frisst bei einem tagelang toten Logserver
den Arbeitsspeicher auf und nimmt das Backend mit. Ein verlorener
Protokolleintrag ist aergerlich; ein Backend, das wegen der
Protokollierung stirbt, ist ein Ausfall. Wie viele verloren gingen, wird
gezaehlt und beim naechsten erfolgreichen Kontakt selbst gemeldet - eine
stille Luecke waere das Schlechteste von beidem.

----------------------------------------------------------------------
FORMAT
----------------------------------------------------------------------
RFC 5424, nicht das aeltere RFC 3164. Grund: 3164 hat keine Jahreszahl
und keine Zeitzone im Zeitstempel, und CO-37 arbeitet durchgaengig in
UTC. Ein Protokoll ohne Zeitzone ist bei der Auswertung wertlos.

    <PRI>1 ZEITSTEMPEL HOST co37 PID MSGID [SD] Text

Die strukturierten Daten tragen die Felder einzeln, damit ein SIEM sie
ohne Textzerlegung findet.
"""
import logging
import queue
import socket
import ssl
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Begrenzung der Warteschlange. 5000 Eintraege sind bei einem Ausrollen
# ueber eine grosse Flotte reichlich und kosten selbst im vollen Zustand
# nur wenige Megabyte.
MAX_WARTESCHLANGE = 5000

# Wie lange ein einzelner Zustellversuch hoechstens dauern darf. Gilt fuer
# Verbindungsaufbau und Senden. Kurz gehalten: der Faden soll bei einem
# toten Server nicht minutenlang haengen, sondern zuegig aufgeben und es
# spaeter erneut versuchen.
ZUSTELL_TIMEOUT = 5.0

# Abstand zwischen zwei Verbindungsversuchen nach einem Fehlschlag. Ohne
# das versucht der Faden es viele Male je Sekunde und beschaeftigt bei
# einem dauerhaft toten Server einen Prozessorkern.
WARTEN_NACH_FEHLER = 30.0

# RFC 5424: PRI = Facility * 8 + Severity.
FACILITIES = {
    "local0": 16, "local1": 17, "local2": 18, "local3": 19,
    "local4": 20, "local5": 21, "local6": 22, "local7": 23,
    "auth": 4, "authpriv": 10, "daemon": 3, "user": 1,
}
SEVERITY = {
    "notice": 5,     # normaler Vorgang, der protokolliert gehoert
    "warning": 4,    # etwas ist schiefgegangen, aber nicht schlimm
    "error": 3,      # fehlgeschlagene Anmeldung, abgebrochener Auftrag
    "critical": 2,   # Rechteaenderung, Update, Zurueckziehen von Token
}


def _escape_sd(wert: str) -> str:
    """
    RFC 5424, Abschnitt 6.3.3: in strukturierten Daten muessen ", \\ und ]
    mit einem Rueckstrich versehen werden.

    Ohne das reisst ein Benutzername mit einem Anfuehrungszeichen den
    ganzen Eintrag auseinander - und genau solche Namen kommen bei
    fehlgeschlagenen Anmeldungen vor, weil sie der Aufrufer bestimmt.
    """
    return (wert.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("]", "\\]"))


def _sauber(wert: str) -> str:
    """
    Steuerzeichen raus, insbesondere Zeilenumbrueche.

    Bei TCP trennt der Zaehler am Anfang die Meldungen, bei UDP das
    Paket - aber viele Logserver arbeiten zeilenweise. Ein Umbruch im
    Benutzernamen waere sonst eine gefaelschte zweite Meldung.
    """
    return "".join(c for c in wert if c.isprintable() or c == " ")


def bauen(facility: str, severity: str, zeitpunkt: str, quelle: str,
          msgid: str, felder: dict, text: str) -> bytes:
    """Eine Meldung nach RFC 5424 zusammensetzen."""
    pri = FACILITIES.get(facility, 16) * 8 + SEVERITY.get(severity, 5)
    sd = "".join(
        f' {k}="{_escape_sd(_sauber(str(v)))}"'
        for k, v in felder.items() if v is not None and v != "")
    sd_block = f"[co37@0{sd}]" if sd else "-"
    return (f"<{pri}>1 {zeitpunkt} {_sauber(quelle) or '-'} co37 - "
            f"{_sauber(msgid) or '-'} {sd_block} "
            f"{_sauber(text)}").encode("utf-8")


class Weiterleitung:
    """
    Haelt die Einstellungen, die Warteschlange und den Faden.

    Eine einzelne Instanz je Prozess, siehe DIENST unten. Umgestellt wird
    ueber einstellen() - der Faden wird dabei nicht neu gestartet, er
    liest die Einstellungen bei jedem Durchlauf frisch.
    """

    def __init__(self):
        self.warteschlange: queue.Queue = queue.Queue(MAX_WARTESCHLANGE)
        self._sperre = threading.Lock()
        self._faden: Optional[threading.Thread] = None
        self._ende = threading.Event()
        self._sock = None
        self._letzter_fehlversuch = 0.0
        self.verworfen = 0
        # Einstellungen
        self.aktiv = False
        self.ziel = ""
        self.port = 514
        self.transport = "udp"          # udp | tcp | tls
        self.facility = "local0"
        self.pruefe_zertifikat = True
        self.quelle = socket.gethostname()

    # ---------------------------------------------------------------
    def einstellen(self, aktiv: bool, ziel: str, port: int, transport: str,
                   facility: str, pruefe_zertifikat: bool = True):
        with self._sperre:
            geaendert = (
                (aktiv, ziel, port, transport)
                != (self.aktiv, self.ziel, self.port, self.transport))
            self.aktiv = bool(aktiv)
            self.ziel = ziel or ""
            self.port = int(port or 514)
            self.transport = transport if transport in ("udp", "tcp", "tls") else "udp"
            self.facility = facility if facility in FACILITIES else "local0"
            self.pruefe_zertifikat = bool(pruefe_zertifikat)
            if geaendert:
                self._schliessen()
                self._letzter_fehlversuch = 0.0
        if self.aktiv and self.ziel:
            self.starten()

    def starten(self):
        if self._faden and self._faden.is_alive():
            return
        self._ende.clear()
        self._faden = threading.Thread(target=self._arbeiten, daemon=True,
                                       name="co37-syslog")
        self._faden.start()

    def beenden(self, wartezeit: float = 2.0):
        self._ende.set()
        if self._faden and self._faden.is_alive():
            self._faden.join(timeout=wartezeit)
        self._schliessen()

    # ---------------------------------------------------------------
    def melden(self, msgid: str, text: str, severity: str = "notice",
               zeitpunkt: str = None, **felder):
        """
        Einen Eintrag ablegen. Kehrt sofort zurueck.

        Wirft nie. Diese Funktion sitzt in audit() und in den
        Auftragswegen - eine Ausnahme von hier duerfte niemals den
        Vorgang mitreissen, den sie nur beschreiben soll.
        """
        if not self.aktiv or not self.ziel:
            return
        try:
            eintrag = (self.facility, severity,
                       zeitpunkt or time.strftime("%Y-%m-%dT%H:%M:%S.000000Z",
                                                  time.gmtime()),
                       self.quelle, msgid, dict(felder), text)
            self.warteschlange.put_nowait(eintrag)
        except queue.Full:
            # Zaehlen statt wachsen. Siehe die Begruendung im Dateikopf.
            self.verworfen += 1
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------
    def _schliessen(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _verbinden(self):
        """
        Verbindung herstellen. Gibt None zurueck, wenn es nicht geht -
        der Aufrufer wartet dann und versucht es spaeter erneut.
        """
        if time.monotonic() - self._letzter_fehlversuch < WARTEN_NACH_FEHLER:
            return None
        try:
            if self.transport == "udp":
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(ZUSTELL_TIMEOUT)
                return s
            s = socket.create_connection((self.ziel, self.port),
                                         timeout=ZUSTELL_TIMEOUT)
            if self.transport == "tls":
                # Prueft Zertifikat UND Namen. Abschaltbar, weil im
                # Maschinenraum haeufig eigene Zertifikate stehen - aber
                # ausdruecklich, nicht stillschweigend.
                ctx = ssl.create_default_context()
                if not self.pruefe_zertifikat:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                s = ctx.wrap_socket(s, server_hostname=self.ziel)
            return s
        except Exception as exc:  # noqa: BLE001
            self._letzter_fehlversuch = time.monotonic()
            logger.warning("syslog nicht erreichbar (%s:%s): %s",
                           self.ziel, self.port, exc)
            return None

    def _senden(self, roh: bytes) -> bool:
        if self._sock is None:
            self._sock = self._verbinden()
            if self._sock is None:
                return False
        try:
            if self.transport == "udp":
                self._sock.sendto(roh, (self.ziel, self.port))
            else:
                # RFC 6587 octet counting: Laenge, Leerzeichen, Meldung.
                # Ohne das kann der Empfaenger bei einem Text mit
                # Zeilenumbruch nicht sagen, wo eine Meldung endet.
                self._sock.sendall(str(len(roh)).encode() + b" " + roh)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("syslog-Zustellung fehlgeschlagen: %s", exc)
            self._schliessen()
            self._letzter_fehlversuch = time.monotonic()
            return False

    def _arbeiten(self):
        while not self._ende.is_set():
            try:
                eintrag = self.warteschlange.get(timeout=1.0)
            except queue.Empty:
                continue
            if not self.aktiv or not self.ziel:
                continue
            facility, severity, zeitpunkt, quelle, msgid, felder, text = eintrag

            # Verworfene zuerst melden, sobald wieder jemand zuhoert.
            if self.verworfen and self._sock is not None:
                anzahl, self.verworfen = self.verworfen, 0
                self._senden(bauen(
                    facility, "warning", zeitpunkt, quelle, "queue.dropped",
                    {"count": anzahl},
                    f"{anzahl} Meldungen verworfen, Warteschlange war voll"))

            self._senden(bauen(facility, severity, zeitpunkt, quelle,
                               msgid, felder, text))


DIENST = Weiterleitung()
