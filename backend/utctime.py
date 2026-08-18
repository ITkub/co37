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
from datetime import datetime, timezone, tzinfo
from typing import Optional

from sqlalchemy import DateTime, TypeDecorator

UTC = timezone.utc


# ----------------------------------------------------------------------
# Erzeugen
# ----------------------------------------------------------------------
def utcnow() -> datetime:
    """Jetzt, in UTC, mit Zeitzone. Ersetzt das veraltete datetime.utcnow()."""
    return datetime.now(UTC)


def localnow() -> datetime:
    """Jetzt, in der Zeitzone des Servers, mit Zeitzone."""
    return datetime.now().astimezone()


def local_tz() -> tzinfo:
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
