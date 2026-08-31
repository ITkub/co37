"""
CO-37 - Umgang mit Zeit

Grundregel: **Jeder Zeitstempel im Programm traegt eine Zeitzone.**

Warum so streng: Ein zeitzonenloser Wert sieht nicht anders aus, egal ob er
UTC oder Ortszeit enthaelt. Python vergleicht zwei solche Werte klaglos und
rechnet mit dem Ergebnis weiter. Genau daran ist der Update-Zeitplan
gescheitert - `last_patch_run` stand in UTC, der Termin wurde in Ortszeit
berechnet, und die Differenz von zwei Stunden fiel nirgends auf.

Mit Zeitzone wirft derselbe Vergleich einen TypeError. Der Fehler tritt dann
sofort und laut auf, statt still ein falsches Ergebnis zu liefern.

Ablage: SQLite verwirft die Zeitzone beim Schreiben, auch mit
`DateTime(timezone=True)`. Der Spaltentyp unten rechnet deshalb beim
Schreiben nach UTC um und heftet die Zeitzone beim Lesen wieder an. Das
Dateiformat bleibt dabei unveraendert - bestehende Datenbanken laufen ohne
Umschreiben weiter.
"""
import os
from datetime import datetime, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import DateTime, TypeDecorator

UTC = timezone.utc


def _system_zone() -> Optional[tzinfo]:
    """
    Die BENANNTE Zeitzone des Servers, oder None.

    Warum benannt und nicht der Versatz (F-40 der Pruefung vom
    2026-08-31): datetime.now().astimezone() heftet ein festes
    timezone(timedelta(...)) an - den Versatz, der JETZT gilt, keine
    Zonenregel. Die Terminrechnung in main.py blickt aber bis zu acht
    Tage zurueck und rechnet Termine dieser Tage mit dem heutigen Versatz
    nach UTC um. Ueber eine Zeitumstellung hinweg ist das Ergebnis eine
    Stunde daneben:

        jetzt   2026-03-25 12:00:00+01:00
        CO-37   2026-03-30 03:00:00+01:00 -> UTC 02:00
        richtig 2026-03-30 03:00:00+02:00 -> UTC 01:00

    Je nach Richtung wird ein Termin dadurch doppelt oder gar nicht als
    faellig erkannt - bei einem Zeitplan mit Neustart also unter
    Umstaenden ein zweiter Neustart. Zweimal im Jahr, und nur fuer
    Termine, die mit dem Nachlauf ueber die Umstellung reichen.

    Reihenfolge: TZ aus der Umgebung, sonst das Ziel von /etc/localtime,
    sonst /etc/timezone. Findet sich nichts, gibt die Funktion None
    zurueck und der Aufrufer bleibt beim festen Versatz - das ist der
    Stand von vorher und immer noch besser als abzustuerzen.
    """
    name = (os.environ.get("TZ") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass

    try:
        ziel = os.path.realpath("/etc/localtime")
        if "/zoneinfo/" in ziel:
            return ZoneInfo(ziel.split("/zoneinfo/", 1)[1])
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass

    try:
        with open("/etc/timezone", encoding="ascii") as fh:
            return ZoneInfo(fh.read().strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass

    return None


# ----------------------------------------------------------------------
# Erzeugen
# ----------------------------------------------------------------------
def utcnow() -> datetime:
    """Jetzt, in UTC, mit Zeitzone. Ersetzt das veraltete datetime.utcnow()."""
    return datetime.now(UTC)


def localnow() -> datetime:
    """
    Jetzt, in der Zeitzone des Servers, mit Zeitzone.

    Mit der BENANNTEN Zone, wenn sie sich ermitteln laesst - sonst
    rechnet jede Terminverschiebung ueber eine Zeitumstellung hinweg
    falsch. Siehe _system_zone().
    """
    zone = _system_zone()
    if zone is not None:
        return datetime.now(zone)
    return datetime.now().astimezone()


def local_tz() -> tzinfo:
    zone = _system_zone()
    if zone is not None:
        return zone
    return datetime.now().astimezone().tzinfo


def from_timestamp(ts: float) -> datetime:
    """Unix-Zeit -> UTC mit Zeitzone. Ersetzt datetime.utcfromtimestamp()."""
    return datetime.fromtimestamp(ts, UTC)


# ----------------------------------------------------------------------
# Umrechnen
# ----------------------------------------------------------------------
def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """
    Stellt sicher, dass ein Wert in UTC mit Zeitzone vorliegt.

    Zeitzonenlose Werte werden als UTC gedeutet - das ist die einzige
    Annahme, die bei Altbestaenden aus der Datenbank zutrifft.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_local(value: Optional[datetime]) -> Optional[datetime]:
    """UTC -> Ortszeit des Servers, mit Zeitzone."""
    if value is None:
        return None
    return ensure_utc(value).astimezone()


def local_wall_to_utc(naive_local: datetime) -> datetime:
    """
    Wanduhrzeit vor Ort -> UTC mit Zeitzone.

    Fuer Zeitplaene: gibt jemand 03:00 ein, ist drei Uhr nachts vor Ort
    gemeint. Der Wert kommt zeitzonenlos herein und wird hier verortet.
    """
    if naive_local.tzinfo is not None:
        return naive_local.astimezone(UTC)
    return naive_local.replace(tzinfo=local_tz()).astimezone(UTC)


# ----------------------------------------------------------------------
# Spaltentyp
# ----------------------------------------------------------------------
class UTCDateTime(TypeDecorator):
    """
    Zeitstempelspalte, die ausschliesslich Werte mit Zeitzone annimmt.

    Beim Schreiben wird nach UTC umgerechnet und zeitzonenlos abgelegt,
    beim Lesen die Zeitzone wieder angeheftet. Ein zeitzonenloser Wert
    fuehrt zu einem Fehler statt zu einer stillen Fehldeutung.
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"Zeitstempel erwartet, erhalten: {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError(
                f"Zeitzonenloser Zeitstempel {value!r}. Erwartet wird ein Wert "
                f"mit Zeitzone - etwa utcnow() aus utctime."
            )
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # Altbestaende sind zeitzonenloses UTC
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
