"""
CO-37 - Backend

Betriebsart: lokales Netz. Agents melden sich frei an und werden im
Dashboard freigegeben. Es gibt keine Enrollment-Tokens und kein manuelles
Anlegen von Hosts - Hosts entstehen ausschliesslich durch Anmeldung.

Start:
    uvicorn main:app --host 0.0.0.0 --port 8080
"""
import hashlib
import os
import re
import shutil
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Query, Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, PlainSerializer
from typing_extensions import Annotated
from sqlalchemy import or_
from sqlmodel import Session, SQLModel, create_engine, select

import joblog
import license
import migrate
import update_manager
from checkmk import CheckmkClient, CheckmkError
from crypto import CryptoNotConfigured, decrypt, encrypt, hash_token, is_configured
from utctime import (
    UTC, as_local, ensure_utc, from_timestamp, local_wall_to_utc, localnow, utcnow,
)
from models import (
    Area, ApprovalState, AuditEntry, Host, HostStatus, InstallToken, Job,
    JobState, JobType, LoginSession, OSType, Role, Setting, UpdatePackage,
    User,
)

DB_URL = os.getenv("CO37_DB", "sqlite:///./co37.db")
AGENT_OFFLINE_AFTER = int(os.getenv("CO37_OFFLINE_SECONDS", "180"))
DATA_DIR = Path(os.getenv("CO37_DATA", "/opt/co37/data"))
PKG_DIR = DATA_DIR / "packages"
AGENT_SRC = Path(__file__).resolve().parent.parent / "agent" / "agent.py"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session


SCHEMA_REPORT: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)

    # Schema abgleichen, bevor irgendetwas darauf zugreift. Ohne diesen
    # Schritt liefert eine Datenbank aus einer aelteren Fassung 500er
    # bei jeder Host-Abfrage.
    global SCHEMA_REPORT
    try:
        SCHEMA_REPORT = migrate.migrate(engine)
        if SCHEMA_REPORT.get("added") or SCHEMA_REPORT.get("migrated"):
            print(f"Schema angepasst: {SCHEMA_REPORT}", flush=True)
    except Exception as exc:  # noqa: BLE001
        SCHEMA_REPORT = {"error": str(exc)}
        print(f"Schema-Migration fehlgeschlagen: {exc}", flush=True)

    # Ohne mindestens einen Benutzer kaeme niemand mehr hinein. Legt beim
    # allerersten Start admin/admin an, danach passiert hier nichts mehr.
    try:
        with Session(engine) as session:
            bootstrap_admin(session)
            # Proxy-Einstellungen in den Zwischenspeicher: sie werden bei
            # jeder einzelnen Anfrage gebraucht.
            load_proxy_config(session)
            load_license(session)
    except Exception as exc:  # noqa: BLE001
        print(f"Startbenutzer konnte nicht angelegt werden: {exc}", flush=True)

    # Nach einem Systemupdate steht eine neue agent.py bereit. Ist das
    # automatische Ausrollen eingeschaltet, wird es hier angestossen.
    try:
        with Session(engine) as session:
            version = agent_source_version()
            last = _setting(session, "agent_roll_version")
            if version != "unbekannt" and version != last:
                res = start_agent_rollout(session)
                print(f"Agent-Ausrollen nach Update: {res}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Agent-Ausrollen konnte nicht gestartet werden: {exc}", flush=True)

    try:
        removed = joblog.prune()
        if removed:
            print(f"{removed} alte Auftragsprotokolle entfernt", flush=True)
    except Exception:  # noqa: BLE001
        pass

    yield


app = FastAPI(
    title="CO-37",
    version=update_manager.get_current_version(),
    lifespan=lifespan,
)

# CORS: standardmaessig gar keine fremde Herkunft.
#
# Vorher stand hier allow_origins=["*"]. Die Anmeldung laeuft ueber eine
# eigene Kopfzeile und nicht ueber Cookies - echtes CSRF war damit
# weitgehend ausgeschlossen. Erreichbar waren aber die offenen Routen von
# jeder beliebigen Webseite aus: eine Seite im Browser eines Mitarbeiters
# konnte Anmeldeversuche gegen den internen Server fahren oder Hosts
# anmelden.
#
# Die Oberflaeche wird vom selben Ursprung ausgeliefert wie die API und
# braucht CORS nicht. Wer eine eigene Anwendung dagegen baut, traegt ihre
# Adresse in CO37_CORS_ORIGINS ein, mehrere mit Komma getrennt.
_cors_origins = [
    o.strip() for o in os.getenv("CO37_CORS_ORIGINS", "").split(",") if o.strip()
]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Session",
                       "X-Agent-Token", "X-Install-Token"],
    )


# Regeln fuer den Browser. Zusammengesetzt einmal beim Start, damit nicht
# bei jeder Antwort neu gebaut wird.
#
# script-src kommt ohne 'unsafe-inline' aus: das Skript liegt in app.js,
# und die Klicks laufen ueber data-Attribute statt ueber onclick. Damit
# wird eingeschleustes Skript gar nicht erst ausgefuehrt - vorher war
# genau das nicht der Fall, und die Regel schuetzte nur gegen das
# Nachladen fremder Dateien.
#
# Bei style-src bleibt 'unsafe-inline' stehen: das Markup ist voller
# style-Attribute. Eingeschleustes CSS richtet mit den uebrigen Regeln
# wenig an, und ein Umbau waere umfangreich ohne echten Gewinn.
#
# Weiter von Bedeutung:
#   connect-src 'self'   - eingeschleustes Skript koennte das
#                          Sitzungstoken nirgendwohin senden
#   frame-ancestors none - die Oberflaeche laesst sich nicht in eine
#                          fremde Seite einbetten (Clickjacking)
#
# Keine fremden Adressen mehr: die Schriften liegen unter frontend/fonts
# und werden von hier ausgeliefert.
CSP = "; ".join([
    "default-src 'self'",
    # Ohne 'unsafe-inline': das Skript liegt in einer eigenen Datei, und
    # es gibt keine onclick-Attribute mehr im Markup. Damit greift der
    # Schutz wirklich - eingeschleustes Skript wird nicht ausgefuehrt.
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    # frame-ancestors deckt das ab; die aeltere Kopfzeile bleibt fuer
    # Browser, die sie noch brauchen.
    "X-Frame-Options": "DENY",
    # Verhindert, dass der Browser den Inhaltstyp erraet - eine
    # hochgeladene Datei soll nicht als Skript ausgefuehrt werden.
    "X-Content-Type-Options": "nosniff",
    # Die Adresse enthaelt zwar keine Geheimnisse, aber es gibt keinen
    # Grund, den internen Namen des Servers nach aussen zu tragen.
    "Referrer-Policy": "no-referrer",
}


def disable_https_only(reason: str):
    """Schaltet den Zwang ab und vermerkt es im Pruefprotokoll."""
    with Session(engine) as session:
        set_setting(session, SET_HTTPS_ONLY, "false")
        set_setting(session, SET_HTTPS_DEADLINE, "")
        audit(session, "system", "https-only.auto-off", reason, None)
        session.commit()
        load_proxy_config(session)


def claims_https(request: Optional[Request]) -> bool:
    """
    Behauptet die Anfrage, verschluesselt hereingekommen zu sein?

    Ohne Ruecksicht darauf, ob das belegt ist - genau diese Unterscheidung
    braucht die Pruefung unten.
    """
    if not request:
        return False
    proto = request.headers.get("x-forwarded-proto", "")
    if proto.split(",")[0].strip().lower() == "https":
        return True
    return request.url.scheme == "https"


def https_only_check(request: Request):
    """
    Prueft, ob eine Anfrage hereindarf. Gibt eine fertige Antwort zurueck,
    wenn abgewiesen wird, sonst None.

    Zwei Regeln, die unabhaengig voneinander gelten:

    1. Verschluesselt nur ueber den eingetragenen Proxy. Eine Anfrage, die
       'https' behauptet, aber woanders herkommt, ist eine unbelegte
       Behauptung - CO-37 selbst nimmt kein TLS entgegen. Sie wird
       abgewiesen statt stillschweigend als unverschluesselt behandelt.
       Gilt immer, auch ohne den Schalter.

    2. Ist der Schalter an, wird alles Unverschluesselte abgewiesen.

    Unverschluesselt bleibt also erreichbar, bis der Schalter gesetzt
    wird - sonst waere das System nach einem Update fuer jeden
    unerreichbar, der noch keinen Proxy eingetragen hat.
    """
    # Rueckfall zuerst, vor jeder Abweisung: ist die Frist verstrichen,
    # ohne dass sich jemand ueber HTTPS angemeldet hat, schaltet sich der
    # Zwang selbst ab. Ohne das sperrt ein falsch eingetragener Proxy
    # dauerhaft aus.
    deadline = _PROXY_CFG["deadline"]
    if _PROXY_CFG["https_only"] and deadline and utcnow() >= deadline:
        disable_https_only(
            f"binnen {HTTPS_CONFIRM_MINUTES} Minuten keine Anmeldung ueber "
            f"HTTPS - zurueck auf unverschluesselten Zugang"
        )

    # Die Gesundheitspruefung bleibt ueber Loopback frei. Der Watcher
    # ruft sie nach einem Update ueber 127.0.0.1 auf; bekaeme er hier 403,
    # hielte er das eingespielte Update fuer fehlgeschlagen und spielte
    # die Sicherung zurueck.
    #
    # Bewusst nur diese eine Route und nicht Loopback insgesamt: sonst
    # stuende die gesamte Schnittstelle jedem offen, der auf dem Rechner
    # eine Shell hat.
    if request.url.path == "/api/health" and is_loopback(request):
        return None

    # Regel 1
    if claims_https(request) and not via_trusted_proxy(request):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Verschluesselte Anfragen werden nur vom "
                          "eingetragenen Proxy angenommen. Dessen Adresse "
                          "unter Einstellungen / Zugang eintragen - "
                          "erreichbar ueber den unverschluesselten Port."
            },
        )

    # Regel 2
    if not _PROXY_CFG["https_only"]:
        return None

    if request_is_https(request):
        return None

    return JSONResponse(
        status_code=403,
        content={
            "detail": "Dieser Zugang ist auf HTTPS beschraenkt. Bitte den "
                      "eingerichteten Namen mit https:// verwenden."
        },
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    denied = https_only_check(request)
    if denied is not None:
        response = denied
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# ======================================================================
# Auth
# ======================================================================
SESSION_HOURS = int(os.getenv("CO37_SESSION_HOURS", "12"))

# scrypt aus der Standardbibliothek. Kein argon2, kein bcrypt - das waeren
# zusaetzliche Abhaengigkeiten fuer keinen Gewinn, den man hier merkt.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt,
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt_hex, hash_hex = stored.split("$")
        if kind != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=32)
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(dk.hex(), hash_hex)


def _setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


# ======================================================================
# Reverse Proxy und HTTPS-Zwang
# ======================================================================
# Bewusst hier ausgewertet und nicht ueber uvicorns --proxy-headers: die
# Option liest ihre Einstellung beim Prozessstart. Ein Wert, den man in
# der Oberflaeche aendert, kaeme dort nie an, ohne den Dienst neu zu
# starten - und das Backend laeuft unprivilegiert und kann sich nicht
# selbst neu starten. So gibt es nur eine Stelle, und eine Aenderung
# greift sofort.
SET_TRUSTED_PROXY = "trusted_proxy"
SET_HTTPS_ONLY = "https_only"
SET_HTTPS_DEADLINE = "https_only_deadline"

# Adresse, unter der CO-37 von den Zielsystemen aus erreichbar ist.
#
# Ohne sie richtet sich der Installationsbefehl danach, wie das Dashboard
# gerade aufgerufen wurde. Das geht so lange gut, bis jemand nach der
# Umstellung auf HTTPS einen neuen Host ueber den internen Port
# einrichtet - der Agent spraeche dann dauerhaft unverschluesselt, und
# auffallen wuerde es erst im Zaehler der Bereitschaftsanzeige.
#
# Bewusst nicht aus dem ersten Aufruf gelernt: ein einziger Aufruf ueber
# eine falsche Adresse wuerde sie dauerhaft setzen.
SET_PUBLIC_URL = "public_url"

# Lizenzschluessel. Steht im Klartext in der Datenbank - er ist kein
# Geheimnis, sondern ein signierter Nachweis. Wer ihn liest, kann damit
# nichts anfangen, was er nicht ohnehin duerfte.
SET_LICENSE = "license_key"

# Vorgabesprache der Installation. Gilt fuer Benutzer, die selbst nichts
# gewaehlt haben, und fuer die Anmeldeseite - dort ist noch kein Benutzer
# bekannt. Englisch ist die Vorgabe der Vorgabe.
SET_LANGUAGE = "default_language"
SPRACHEN = ("en", "de")


def pruefe_sprache(code) -> Optional[str]:
    """
    Gibt die Sprachkennung zurueck, wenn sie bekannt ist, sonst None.

    Genauer Vergleich gegen die Liste, keine Teilzeichenkette und kein
    Praefix: 'de-DE' ist nicht 'de', und was hier durchrutscht, landet
    unuebersetzt in der Oberflaeche.
    """
    return code if code in SPRACHEN else None

# Im Arbeitsspeicher gehalten: die Pruefung laeuft bei jeder Freigabe und
# bei jedem Abruf der Hostliste.
_LIZENZ = license.Lizenz()


def load_license(session: Session):
    """Liest den gespeicherten Schluessel in den Zwischenspeicher."""
    global _LIZENZ
    roh = _setting(session, SET_LICENSE, "")
    if not roh:
        _LIZENZ = license.Lizenz()
        return
    try:
        _LIZENZ = license.pruefen(roh)
    except license.LizenzFehler:
        # Ein gespeicherter Schluessel, der nicht mehr prueft - etwa weil
        # der oeffentliche Teil ausgetauscht wurde. Nicht loeschen, nur
        # nicht anwenden: der Betreiber soll in der Oberflaeche sehen,
        # dass etwas nicht stimmt.
        _LIZENZ = license.Lizenz()


def approved_hosts(session: Session) -> int:
    """Gezaehlt werden freigegebene Hosts - so steht es in der Lizenz."""
    return len(session.exec(
        select(Host).where(Host.approval_state == ApprovalState.approved)
    ).all())


def check_host_limit(session: Session):
    """
    Wirft, wenn kein Platz mehr frei ist.

    Aufgerufen ausschliesslich bei der Freigabe. Alles andere laeuft
    unveraendert weiter - auch bei abgelaufenem Schluessel. Ein
    Patch-Management-Werkzeug, das wegen einer Lizenzfrage Systeme
    ungepatcht laesst, schafft genau die Luecke, gegen die es angeschafft
    wurde.
    """
    grenze = _LIZENZ.erlaubte_hosts
    if grenze is None:
        return
    belegt = approved_hosts(session)
    if belegt < grenze:
        return

    if _LIZENZ.vorhanden and not _LIZENZ.abgelaufen:
        text = (f"Der Lizenzschluessel umfasst {grenze} Hosts, es sind "
                f"bereits {belegt} freigegeben. Fuer weitere Hosts wird ein "
                f"groesserer Schluessel benoetigt: sales@itkub.de")
    elif _LIZENZ.abgelaufen:
        text = (f"Der Lizenzschluessel ist abgelaufen. Ohne gueltigen "
                f"Schluessel sind {grenze} Hosts moeglich, es sind bereits "
                f"{belegt} freigegeben. Bereits freigegebene Hosts werden "
                f"weiter gepatcht. Verlaengerung: sales@itkub.de")
    else:
        text = (f"Ohne Lizenzschluessel sind {grenze} Hosts moeglich, es "
                f"sind bereits {belegt} freigegeben. Fuer mehr Hosts: "
                f"sales@itkub.de")
    raise HTTPException(403, text)

# Frist, innerhalb derer nach dem Einschalten eine Anmeldung ueber HTTPS
# erfolgen muss. Sonst schaltet sich der Zwang von selbst wieder ab.
#
# Fuenfzehn Minuten und nicht fuenf: der Schalter trifft nicht nur den
# eigenen Browser, sondern alle Agents. Ob die noch durchkommen, sieht man
# erst nach ein, zwei Heartbeat-Runden, und dazwischen kann ein Patch-Lauf
# liegen.
HTTPS_CONFIRM_MINUTES = 15

# Im Arbeitsspeicher gehalten, weil bei jeder einzelnen Anfrage gebraucht.
# Wird beim Start geladen und bei jeder Aenderung neu gesetzt.
_PROXY_CFG: dict = {"trusted": [], "https_only": False, "deadline": None}


def public_base_url(request: Request, session: Session) -> str:
    """
    Adresse fuer erzeugte Befehle.

    Ist die oeffentliche Adresse eingetragen, gilt sie - unabhaengig
    davon, wie das Dashboard gerade aufgerufen wurde. Sonst wird sie aus
    der Anfrage abgeleitet, wobei das Schema vom eingetragenen Proxy
    kommt: die Host-Kopfzeile traegt den richtigen Namen, aber die
    Verbindung zum Backend ist unverschluesselt.
    """
    configured = _setting(session, SET_PUBLIC_URL, "").strip().rstrip("/")
    if configured:
        return configured

    base = str(request.base_url).rstrip("/")
    if request_is_https(request) and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


def load_proxy_config(session: Session):
    """Liest die Einstellungen in den Zwischenspeicher."""
    raw = _setting(session, SET_TRUSTED_PROXY, "")
    _PROXY_CFG["trusted"] = [p.strip() for p in raw.split(",") if p.strip()]
    _PROXY_CFG["https_only"] = _setting(session, SET_HTTPS_ONLY, "false") == "true"
    deadline = _setting(session, SET_HTTPS_DEADLINE, "")
    try:
        _PROXY_CFG["deadline"] = ensure_utc(datetime.fromisoformat(deadline)) \
            if deadline else None
    except ValueError:
        _PROXY_CFG["deadline"] = None


def set_setting(session: Session, key: str, value: str):
    session.merge(Setting(key=key, value=value))


def peer_ip(request: Optional[Request]) -> Optional[str]:
    """Die Adresse, von der die Verbindung tatsaechlich kommt."""
    return request.client.host if request and request.client else None


def via_trusted_proxy(request: Optional[Request]) -> bool:
    ip = peer_ip(request)
    return bool(ip) and ip in _PROXY_CFG["trusted"]


def client_ip(request: Optional[Request]) -> Optional[str]:
    """
    Adresse des urspruenglichen Aufrufers.

    Der Kopfzeile X-Forwarded-For wird nur geglaubt, wenn die Verbindung
    vom eingetragenen Proxy kommt. Ohne diese Einschraenkung koennte jeder
    sie selbst setzen und damit die Drosselung der Anmeldung mit
    erfundenen Absendern umgehen.
    """
    if not request:
        return None
    if via_trusted_proxy(request):
        fwd = request.headers.get("x-forwarded-for", "")
        # Erster Eintrag ist der urspruengliche Aufrufer; die weiteren sind
        # zwischengeschaltete Proxys.
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return peer_ip(request)


def request_is_https(request: Optional[Request]) -> bool:
    """
    Kam die Anfrage verschluesselt an?

    Auch hier zaehlt die Kopfzeile nur vom eingetragenen Proxy. Direkt
    ueber TLS betreibt CO-37 nichts - das uebernimmt der Proxy.
    """
    if not request:
        return False
    if via_trusted_proxy(request):
        proto = request.headers.get("x-forwarded-proto", "")
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def is_loopback(request: Optional[Request]) -> bool:
    return peer_ip(request) in ("127.0.0.1", "::1", "localhost")


# ======================================================================
# Drosselung der Anmeldung
# ======================================================================
# Gezaehlt wird pro Absender-IP, nicht pro Konto.
#
# Eine Kontosperre koennte jeder ausloesen, der weiss, dass das Konto
# 'admin' heisst - dafuer braucht es keinen Angreifer, ein Skript mit
# altem Passwort in einer Schleife genuegt. Der einzige Administrator
# staende dann vor seinem eigenen System, moeglicherweise mitten in einem
# Wartungsfenster. Die Sperre waere das groessere Risiko als das, wovor
# sie schuetzt.
#
# Pro Quelle bleibt der Schaden begrenzt: wer von einem anderen Rechner
# kommt, ist nicht betroffen, und nach Ablauf des Fensters loest es sich
# von selbst.
#
# Nicht abgedeckt: wer viele Adressen hat, umgeht das. Dagegen hilft nur
# eine Kontosperre, und die will ich aus dem obigen Grund nicht.
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = timedelta(minutes=15)

# Obergrenze fuer die Zahl gemerkter Quellen. Ohne sie koennte jemand mit
# gefaelschten Absendern den Speicher volllaufen lassen.
LOGIN_MAX_SOURCES = 5000

# Im Arbeitsspeicher, nicht in der Datenbank: sonst schreibt jeder
# Fehlversuch in die SQLite-Datei - genau das, was ein Angreifer gern
# ausloest. Preis dafuer ist, dass ein Neustart des Dienstes die Zaehler
# zuruecksetzt. Vertretbar, denn einen Neustart kann niemand von aussen
# erzwingen.
_LOGIN_FAILS: dict[str, list[datetime]] = {}


def _recent_fails(ip: str, now: datetime) -> list[datetime]:
    """Fehlversuche im laufenden Fenster; raeumt dabei auf."""
    tries = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < LOGIN_WINDOW]
    if tries:
        _LOGIN_FAILS[ip] = tries
    else:
        _LOGIN_FAILS.pop(ip, None)
    return tries


def login_retry_after(ip: Optional[str]) -> Optional[int]:
    """
    Wartezeit in Sekunden, wenn diese Quelle gerade gesperrt ist, sonst
    None.
    """
    if not ip:
        return None
    now = utcnow()
    tries = _recent_fails(ip, now)
    if len(tries) < LOGIN_MAX_FAILS:
        return None
    # Frei wird es, sobald der aelteste Versuch aus dem Fenster laeuft.
    return max(1, int((tries[0] + LOGIN_WINDOW - now).total_seconds()))


def note_login_failure(ip: Optional[str]) -> int:
    """Zaehlt einen Fehlversuch und gibt die Zahl im Fenster zurueck."""
    if not ip:
        return 0
    now = utcnow()
    tries = _recent_fails(ip, now)

    if ip not in _LOGIN_FAILS and len(_LOGIN_FAILS) >= LOGIN_MAX_SOURCES:
        # Voll: die Quelle mit dem aeltesten Versuch weicht. Sie ist am
        # ehesten ohnehin gleich aus dem Fenster gelaufen.
        oldest = min(_LOGIN_FAILS, key=lambda k: _LOGIN_FAILS[k][0])
        _LOGIN_FAILS.pop(oldest, None)

    tries.append(now)
    _LOGIN_FAILS[ip] = tries
    return len(tries)


def note_login_success(ip: Optional[str]):
    """Ein erfolgreicher Anmeldevorgang loescht den Zaehler der Quelle."""
    if ip:
        _LOGIN_FAILS.pop(ip, None)


# ======================================================================
# Drosselung der Agent-Anmeldung
# ======================================================================
# Aus der Sicherheitspruefung vom 2026-08-22 (F-08).
#
# /api/v1/agent/enroll ist bewusst ohne Enrollment-Token - der Schutz
# liegt in der Freigabe durch einen Menschen. Das bleibt so. Ungeschuetzt
# war aber die MENGE: wer die Route erreicht, konnte in einer Schleife
# beliebig viele Hostzeilen anlegen. Jede davon kostet Speicherplatz,
# jede erscheint im Dashboard, und in genuegender Zahl macht sie die
# Freigabeliste unbenutzbar - der eine echte neue Host geht darin unter.
#
# Zwei Schranken, weil sie Unterschiedliches abdecken:
#
#   Die Drosselung pro Quelle begrenzt das Tempo. Sie ist grosszuegig
#   bemessen: ein Rollout, das dreissig Hosts gleichzeitig ausbringt,
#   kommt aus einem NAT-Netz mit einer einzigen Adresse an, und ein
#   Anmeldevorgang, der daran scheitert, ist ein Betriebsvorfall - genau
#   die Sorte Schaden, die eine Sicherheitsmassnahme nicht anrichten darf.
#
#   Die Obergrenze wartender Hosts ist die eigentliche Schranke. Sie
#   greift unabhaengig davon, aus wie vielen Quellen die Anmeldungen
#   kommen, und sie loest sich von selbst: wer freigibt oder entfernt,
#   macht wieder Platz. Ein zweiter Absender hilft dem Angreifer hier
#   nichts.
#
# Nicht abgedeckt: wer bereits freigegebene Hosts hat, kann sie nicht auf
# diesem Weg vermehren - dafuer braucht es ein gueltiges Agent-Token.
ENROLL_MAX_PER_SOURCE = 50
ENROLL_WINDOW = timedelta(minutes=15)
ENROLL_MAX_SOURCES = 5000

# Wie viele Hosts gleichzeitig auf Freigabe warten duerfen. Wer so viele
# auf einmal ausbringt, gibt zwischendurch frei.
PENDING_MAX = 100

_ENROLLS: dict[str, list[datetime]] = {}


def _recent_enrolls(ip: str, now: datetime) -> list[datetime]:
    """Anmeldungen im laufenden Fenster; raeumt dabei auf."""
    tries = [t for t in _ENROLLS.get(ip, []) if now - t < ENROLL_WINDOW]
    if tries:
        _ENROLLS[ip] = tries
    else:
        _ENROLLS.pop(ip, None)
    return tries


def enroll_retry_after(ip: Optional[str]) -> Optional[int]:
    """Wartezeit in Sekunden, wenn diese Quelle gerade gesperrt ist."""
    if not ip:
        return None
    now = utcnow()
    tries = _recent_enrolls(ip, now)
    if len(tries) < ENROLL_MAX_PER_SOURCE:
        return None
    return max(1, int((tries[0] + ENROLL_WINDOW - now).total_seconds()))


def note_enroll(ip: Optional[str]):
    """
    Zaehlt eine Anmeldung.

    Gezaehlt wird die gelungene, nicht die abgewiesene: anders als bei der
    Anmeldung eines Benutzers gibt es hier keinen Fehlversuch, den man
    zaehlen koennte - jede Anfrage, die durchkommt, legt eine Zeile an.
    """
    if not ip:
        return
    now = utcnow()
    tries = _recent_enrolls(ip, now)
    if ip not in _ENROLLS and len(_ENROLLS) >= ENROLL_MAX_SOURCES:
        oldest = min(_ENROLLS, key=lambda k: _ENROLLS[k][0])
        _ENROLLS.pop(oldest, None)
    tries.append(now)
    _ENROLLS[ip] = tries


def pending_count(session: Session) -> int:
    """Wie viele Hosts warten auf Freigabe."""
    return len(session.exec(
        select(Host.id).where(Host.approval_state == ApprovalState.pending)
    ).all())


def audit(session: Session, actor: str, action: str,
          detail: str = None, request: Request = None):
    """
    Schreibt einen Eintrag ins Pruefprotokoll.

    Absichtlich ohne commit: der Eintrag gehoert in dieselbe Transaktion
    wie die Aenderung, die er beschreibt. Sonst kann das eine ohne das
    andere ueberleben.
    """
    session.add(AuditEntry(actor=actor, action=action, detail=detail,
                           from_ip=client_ip(request)))


class Principal:
    """Wer gerade handelt - ein angemeldeter Benutzer oder der API-Key."""

    def __init__(self, name: str, role: Role, user: Optional[User] = None):
        self.name = name
        self.role = role
        self.user = user

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin


def _session_by_token(token: str, session: Session) -> Optional[LoginSession]:
    if not token:
        return None
    row = session.exec(
        select(LoginSession).where(LoginSession.token_hash == hash_token(token))
    ).first()
    if not row:
        return None
    if ensure_utc(row.expires_at) <= utcnow():
        session.delete(row)
        session.commit()
        return None
    return row


# Name des Sitzungscookies.
#
# Das Token lag frueher im localStorage und war damit fuer jedes Skript in
# der Seite lesbar. Als Cookie mit HttpOnly kommt kein JavaScript mehr
# heran - auch eingeschleustes nicht.
#
# Was das NICHT verhindert: eingeschleustes Skript kann die
# Schnittstelle weiterhin in der Seite aufrufen, das Cookie geht dabei
# automatisch mit. Der Gewinn ist, dass das Token nicht mehr gestohlen
# und spaeter von woanders verwendet werden kann. Die eigentliche
# Schranke bleibt die Regel script-src.
SESSION_COOKIE = "co37_session"


def session_token(request: Optional[Request], x_session: str = "") -> str:
    """
    Sitzungstoken aus dem Cookie, ersatzweise aus der Kopfzeile.

    Die Kopfzeile bleibt zugelassen: sie kostet nichts, seit an das Token
    niemand mehr herankommt, und sie ist der Rueckweg, falls mit dem
    Cookie etwas klemmt - etwa hinter einem Proxy, der es abschneidet.
    """
    if request is not None:
        aus_cookie = request.cookies.get(SESSION_COOKIE, "")
        if aus_cookie:
            return aus_cookie
    return x_session


def authenticate(x_session: str, session: Session,
                 request: Optional[Request] = None) -> Principal:
    """
    Stellt fest, WER da ist. Ein Weg: die Anmeldesitzung.

    Als eigene Funktion und nicht nur als Abhaengigkeit, damit sie auch
    dort aufgerufen werden kann, wo die Pruefung von einer Bedingung
    abhaengt - beim Paketdownload etwa, der alternativ das
    Installations-Token akzeptiert.

    Frueher gab es hier einen zweiten Weg: einen globalen Admin-Token als
    Kopfzeile X-API-Key. Er ist entfallen. Warum:

    Er stammte aus der Zeit vor den Benutzerkonten, als er der einzige Weg
    hinein war. Danach war er dauerhaft gueltig, lief nie ab, galt auf
    jeder Route und liess sich - anders als das Passwort - unbegrenzt oft
    durchprobieren; die Drosselung sitzt nur an der Anmelderoute. Ab
    0.36.10 galt er nur noch ueber Loopback, und damit war er auf einer
    Installation mit HTTPS-Zwang ohnehin unbenutzbar: der Zwang laesst
    ueber Loopback nur /api/health durch.

    Uebrig blieb eine Berechtigung ohne Nutzen, aber mit Angriffsflaeche.
    Der Weg zurueck nach einem Aussperren fuehrt jetzt ausdruecklich ueber
    die Datenbank - siehe README, "Wenn du dich aussperrst". Das braucht
    root auf dem Server, und genau das ist angemessen.

    Wer die Schnittstelle aus einem Skript anspricht, meldet sich an und
    haelt die Sitzung. Sollte einmal eine nicht-interaktive Berechtigung
    gebraucht werden, gehoert sie je Konto und widerruflich - nicht als
    ein globaler Schluessel zurueck.
    """
    row = _session_by_token(session_token(request, x_session), session)
    if not row:
        raise HTTPException(401, "Nicht angemeldet")

    user = session.get(User, row.user_id)
    if not user or user.disabled:
        raise HTTPException(401, "Benutzer nicht mehr gueltig")

    row.last_seen = utcnow()
    session.add(row)
    session.commit()
    return Principal(user.username, user.role, user)


def require_login(
    request: Request,
    x_session: str = Header(default=""),
    session: Session = Depends(get_session),
) -> Principal:
    """
    Prueft nur, DASS jemand angemeldet ist - nicht, als was.

    Hiess frueher require_admin und war damit irrefuehrend benannt: an ueber
    dreissig Routen las sich das wie eine Rechtepruefung, obwohl jeder
    angemeldete Benutzer durchkam. Ein Konto mit der Rolle 'user' konnte
    dadurch Hosts freigeben, die Checkmk-Zugangsdaten aendern und - ueber
    das Hochladen eines Systemupdates, das der Watcher als root auspackt -
    Code als root ausfuehren.
    """
    return authenticate(x_session, session, request)


# Gueltigkeitsdauer eines Installations-Tokens. Kurz genug, dass der Wert
# in der Verlaufsdatei des Zielsystems von vornherein wertlos ist.
INSTALL_TOKEN_MINUTES = 15

# Nicht streng einmalig: der haeufigste Fall ist, dass der Download klappt
# und erst 'apt install' scheitert - dann fuehrt man den ganzen Einzeiler
# erneut aus. Bei genau einem Abruf staende man ohne Paket da. Der Schutz
# liegt in der Frist, die Zahl der Abrufe ist die zusaetzliche Schranke.
INSTALL_TOKEN_MAX_USES = 3


def new_install_token(session: Session, created_by: str) -> tuple[str, InstallToken]:
    """
    Erzeugt ein neues Installations-Token und raeumt abgelaufene weg.

    Aufgeraeumt wird hier statt in einem Hintergrunddienst - dieselbe
    Entscheidung wie ueberall sonst im System.
    """
    now = utcnow()
    for old in session.exec(
        select(InstallToken).where(InstallToken.expires_at <= now)
    ).all():
        session.delete(old)

    plain = secrets.token_urlsafe(32)
    row = InstallToken(
        token_hash=hash_token(plain),
        expires_at=now + timedelta(minutes=INSTALL_TOKEN_MINUTES),
        max_uses=INSTALL_TOKEN_MAX_USES,
        created_by=created_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return plain, row


def consume_install_token(candidate: str, session: Session,
                          request: Optional[Request] = None) -> bool:
    """
    Prueft ein Installations-Token und zaehlt einen Abruf hoch.

    Gibt False zurueck, wenn es unbekannt, abgelaufen oder aufgebraucht
    ist. Aufgebrauchte werden geloescht, damit die Tabelle nicht waechst.
    """
    if not candidate:
        return False
    row = session.exec(
        select(InstallToken).where(InstallToken.token_hash == hash_token(candidate))
    ).first()
    if not row:
        return False

    now = utcnow()
    if ensure_utc(row.expires_at) <= now or row.uses >= row.max_uses:
        session.delete(row)
        session.commit()
        return False

    row.uses += 1
    row.last_used_at = now
    row.last_used_ip = client_ip(request)
    session.add(row)
    session.commit()
    return True


def require_admin(who: Principal = Depends(require_login)) -> Principal:
    if not who.is_admin:
        raise HTTPException(403, "Nur fuer Administratoren")
    return who


def bootstrap_admin(session: Session):
    """
    Legt beim ersten Start den Benutzer admin/admin an.

    Bewusst ohne erzwungene Aenderung: im lokalen Testbetrieb steht dem
    nichts entgegen. Das Feld must_change_password ist vorhanden und wird
    hier nur nicht gesetzt - die Erzwingung laesst sich spaeter ohne
    Schemaaenderung nachruesten. Ohne TLS ist ein bekanntes Passwort im
    Netz mitlesbar; vor einem Einsatz ausserhalb des Testnetzes gehoert
    das eingeschaltet.
    """
    if session.exec(select(User)).first():
        return
    session.add(User(
        username="admin",
        role=Role.admin,
        password_hash=hash_password("admin"),
        must_change_password=False,
    ))
    session.commit()


def host_by_token(token: str, session: Session) -> Host:
    # Ein leeres Token darf niemals passen. Bei einem zurueckgezogenen
    # Token steht in der Datenbank NULL - ohne diese Pruefung koennte eine
    # leere Kopfzeile darauf treffen und den Host uebernehmen.
    if not token:
        raise HTTPException(401, "Unbekannter Agent-Token")
    host = session.exec(
        select(Host).where(Host.agent_token_hash == hash_token(token))
    ).first()
    if not host:
        raise HTTPException(401, "Unbekannter Agent-Token")
    return host


def require_approved(host: Host) -> Host:
    if host.approval_state != ApprovalState.approved:
        raise HTTPException(403, "Host ist nicht freigegeben")
    return host


# ======================================================================
# Checkmk-Zugang
# ======================================================================
def get_checkmk(session: Session) -> Optional[CheckmkClient]:
    url = _setting(session, "cmk_url")
    site = _setting(session, "cmk_site")
    user = _setting(session, "cmk_user")
    secret = decrypt(_setting(session, "cmk_secret"))
    if not all([url, site, user, secret]):
        return None
    return CheckmkClient(
        server_url=url, site=site, username=user, secret=secret,
        verify_ssl=_setting(session, "cmk_verify_ssl", "true") == "true",
    )


async def downtime_targets(quelle: "Host | Area", cmk: CheckmkClient) -> list[str]:
    """
    Welche Checkmk-Hosts eine Downtime bekommen.

    quelle ist meist der betroffene Host, bei einem aus dem Zeitplan eines
    Bereichs ausgeloesten Neustart dessen Bereich - beide tragen dieselben
    Feldnamen (checkmk_hosts, checkmk_downtime_all), gelesen wird hier nur
    das.

    Normalfall sind die verknuepften Namen. Ist checkmk_downtime_all
    gesetzt, sind es alle in Checkmk konfigurierten Hosts: die Quelle
    traegt dann das Monitoring selbst, und ein Neustart nimmt jedem
    anderen Host die Ueberwachung, ohne dass diesen etwas fehlt.

    Die verknuepften Namen bleiben in jedem Fall enthalten. Ein Name kann
    in der Auflistung fehlen, etwa weil er als Cluster oder in einem nicht
    lesbaren Ordner liegt - er waere dann trotz gesetztem Flag ohne
    Downtime, was das Gegenteil der Absicht ist.
    """
    linked = list(quelle.checkmk_hosts or [])
    if not quelle.checkmk_downtime_all:
        return linked

    names = [h["name"] for h in await cmk.list_hosts() if h.get("name")]
    for name in linked:
        if name not in names:
            names.append(name)
    return names


def has_downtime_target(quelle: "Host | Area") -> bool:
    """
    Ob ueberhaupt eine Downtime gesetzt werden kann. Bei gesetztem Flag
    reicht die Checkmk-Verbindung, eine Verknuepfung ist nicht noetig.

    quelle: siehe downtime_targets().
    """
    return bool(quelle.checkmk_hosts) or bool(quelle.checkmk_downtime_all)


# ======================================================================
# Schemas
# ======================================================================
# Zeitstempel werden immer mit Zeitzone ausgeliefert. Ohne das liest der
# Browser einen UTC-Wert als Ortszeit.
UtcDT = Annotated[
    datetime,
    PlainSerializer(
        lambda d: (d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d).isoformat(),
        return_type=str,
    ),
]


class HostPatch(BaseModel):
    display_name: Optional[str] = None
    tags: Optional[list[str]] = None
    checkmk_hosts: Optional[list[str]] = None
    checkmk_downtime_all: Optional[bool] = None
    downtime_minutes: Optional[int] = None
    auto_reboot: Optional[bool] = None
    maintenance_window: Optional[str] = None
    patch_enabled: Optional[bool] = None
    patch_days: Optional[list[str]] = None
    patch_time: Optional[str] = None
    patch_auto_reboot: Optional[bool] = None
    patch_grace_hours: Optional[int] = None


class HostOrder(BaseModel):
    ids: list[int]


class HostArea(BaseModel):
    area_id: Optional[int] = None


class AreaCreate(BaseModel):
    name: str


class AreaPatch(BaseModel):
    name: Optional[str] = None
    checkmk_hosts: Optional[list[str]] = None
    checkmk_downtime_all: Optional[bool] = None
    downtime_minutes: Optional[int] = None
    patch_enabled: Optional[bool] = None
    patch_days: Optional[list[str]] = None
    patch_time: Optional[str] = None
    patch_auto_reboot: Optional[bool] = None
    patch_grace_hours: Optional[int] = None


class AreaOrder(BaseModel):
    ids: list[int]


class AreaRead(BaseModel):
    id: int
    name: str
    sort_order: int = 0
    checkmk_hosts: list[str] = []
    checkmk_downtime_all: bool = False
    downtime_minutes: int = 30
    patch_enabled: bool = False
    patch_days: list[str] = []
    patch_time: Optional[str] = None
    patch_auto_reboot: bool = False
    patch_grace_hours: int = 4
    host_count: int = 0
    # Hostnamen, die beim Speichern schon einen eigenen Zeitplan hatten -
    # nicht blockierend, nur ein Hinweis, dass sich beide Zeitplaene fuer
    # denselben Host in die Quere kommen koennen.
    patch_conflicts: list[str] = []

    model_config = {"from_attributes": True}


class HostRead(BaseModel):
    """Ausgabeschema. Enthaelt bewusst kein Token."""
    id: int
    hostname: str
    display_name: Optional[str] = None
    tags: list[str] = []
    approval_state: ApprovalState
    enrolled_at: Optional[UtcDT] = None
    enrolled_from_ip: Optional[str] = None
    os_type: Optional[OSType] = None
    os_version: Optional[str] = None
    ip_address: Optional[str] = None
    agent_version: Optional[str] = None
    status: HostStatus
    last_seen: Optional[UtcDT] = None
    last_scan: Optional[UtcDT] = None
    updates_available: int = 0
    security_updates: int = 0
    reboot_required: bool = False
    reboot_reasons: list = []
    updates_require_reboot: bool = False
    checkmk_hosts: list[str] = []
    checkmk_downtime_all: bool = False
    downtime_minutes: int = 30
    auto_reboot: bool = False
    maintenance_window: Optional[str] = None
    patch_enabled: bool = False
    patch_days: list[str] = []
    patch_time: Optional[str] = None
    patch_auto_reboot: bool = False
    patch_grace_hours: int = 4
    last_patch_run: Optional[UtcDT] = None
    next_patch_run: Optional[UtcDT] = None
    patch_followup_left: int = 0
    sort_order: int = 0
    area_id: Optional[int] = None

    model_config = {"from_attributes": True}


class JobRead(BaseModel):
    id: int
    host_id: int
    job_type: JobType
    state: JobState
    params: dict = {}
    result: Optional[dict] = None
    log: Optional[str] = None
    error: Optional[str] = None
    scheduled_at: Optional[UtcDT] = None
    expires_at: Optional[UtcDT] = None
    delivered: int = 0
    last_delivered_at: Optional[UtcDT] = None
    created_at: UtcDT
    started_at: Optional[UtcDT] = None
    finished_at: Optional[UtcDT] = None

    model_config = {"from_attributes": True}


class UpdateRead(BaseModel):
    id: int
    package_id: str
    title: str
    new_version: Optional[str] = None
    is_security: bool
    requires_reboot: bool

    model_config = {"from_attributes": True}


class JobCreate(BaseModel):
    job_type: JobType
    params: dict = {}
    # Zeitpunkt in lokaler Zeit des Browsers, als ISO mit Zeitzone gesendet
    scheduled_at: Optional[datetime] = None
    # Downtime vorher setzen. Vorgabe ja.
    set_downtime: bool = True
    # Nur fuer Patch-Auftraege: anschliessend neu starten, falls noetig.
    reboot_after: bool = False
    # Nachlauf in Minuten. Meldet sich der Host erst danach, wird der Auftrag
    # verworfen. Vorgabe: 120 bei geplanten, 10 bei sofortigen Neustarts.
    grace_minutes: Optional[int] = None
    # Downtime-Dauer abweichend vom Vorgabewert des Hosts
    downtime_minutes: Optional[int] = None


class CheckmkConfig(BaseModel):
    url: str
    site: str
    user: str
    # Leer lassen, um das hinterlegte Secret unveraendert zu uebernehmen.
    # So kann man die URL aendern, ohne das Secret erneut eintippen zu muessen.
    secret: str = ""
    verify_ssl: bool = True


class DowntimeRequest(BaseModel):
    """Entweder minutes ab jetzt, oder start/end als ISO-Zeitstempel."""
    minutes: Optional[int] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    comment: str = "CO-37: Manuelle Wartung"


# Zweite Verteidigungslinie neben der Maskierung in der Oberflaeche. Die
# Anmeldung ist offen - jedes Geraet im Netz waehlt seinen Namen selbst.
# Erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich;
# das deckt gueltige Namen unter Linux wie Windows ab und schliesst
# Anfuehrungszeichen, spitze Klammern und Steuerzeichen aus.
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


def check_hostname(name: str) -> str:
    name = (name or "").strip()
    if not HOSTNAME_RE.match(name):
        raise HTTPException(
            400,
            "Ungueltiger Hostname. Erlaubt sind Buchstaben, Ziffern, Punkt, "
            "Bindestrich und Unterstrich, hoechstens 63 Zeichen.",
        )
    return name


# ----------------------------------------------------------------------
# Auskunftsfelder des Agents
# ----------------------------------------------------------------------
# os_version, agent_version, ip_address und reboot_reasons sind reine
# Auskunft: sie werden angezeigt, es haengt keine Entscheidung an ihrem
# genauen Wert. Der Agent bestimmt sie frei, und bei der Anmeldung tut
# das JEDER im Netz - dieser Endpunkt ist bewusst offen.
#
# Gekuerzt statt abgewiesen. Ein Heartbeat, der mit 422 scheitert, nimmt
# einen Host dauerhaft aus dem Betrieb, und platform.platform() kann auf
# einem System, das ich nicht kenne, mehr liefern als hier angesetzt.
# Kuerzen kann das nicht passieren.
#
# Das ist eine Groessenschranke, keine Sicherheitsschranke: die liegt im
# Frontend, das diese Werte escaped ausgibt. Hier geht es darum, dass
# niemand die Datenbank und jede Host-Abfrage mit Megabyte volllaufen
# laesst.
FELD_MAX = 120           # os_version: "Windows-10-10.0.19045-SP0" u.ae.
VERSION_MAX = 40         # agent_version: "0.36.6"
IP_MAX = 45              # laenger wird auch eine IPv6-Adresse nicht
GRUENDE_MAX = 20         # so viele Neustartgruende zeigt niemand mehr an
GRUND_MAX = 80


def kurz(wert, grenze: int) -> str:
    """
    Auskunftstext auf ein anzeigbares Mass bringen.

    Steuerzeichen fliegen raus - sie wuerden das Protokoll und die Liste
    zerschiessen, ohne je etwas zu bedeuten. Ein Zeichensatzfilter
    darueber hinaus waere falsch: die Versionszeichenkette eines fremden
    Systems darf aussehen, wie sie will.
    """
    text = "" if wert is None else str(wert)
    text = "".join(c for c in text if c == " " or c.isprintable())
    return text.strip()[:grenze]


def kurze_gruende(werte) -> list:
    """Neustartgruende: Anzahl und Laenge je Eintrag begrenzt."""
    if not isinstance(werte, list):
        return []
    return [kurz(w, GRUND_MAX) for w in werte[:GRUENDE_MAX] if kurz(w, GRUND_MAX)]


class AgentEnroll(BaseModel):
    hostname: str
    os_type: OSType
    os_version: str = ""
    ip_address: Optional[str] = None
    agent_version: str = ""


class AgentHeartbeat(BaseModel):
    hostname: str
    os_type: OSType
    os_version: str
    ip_address: Optional[str] = None
    agent_version: str
    reboot_required: bool = False
    reboot_reasons: list = []
    # Unix-Zeit des letzten Systemstarts. 0.0 heisst 'nicht ermittelbar' -
    # dann wird nichts aufgeraeumt. Aeltere Agents senden das Feld nicht.
    boot_time: float = 0.0
    # Der Agent hat bereits shutdown abgesetzt und wartet auf das
    # Herunterfahren. Es werden keine Auftraege mehr ausgeliefert.
    reboot_pending: bool = False


class AgentNotice(BaseModel):
    message: str
    result: dict = {}


class AgentJobReport(BaseModel):
    job_id: int
    state: JobState
    log: Optional[str] = None
    error: Optional[str] = None
    result: dict = {}
    reboot_required: Optional[bool] = None


class AgentScanResult(BaseModel):
    job_id: Optional[int] = None
    reboot_required: bool = False
    reboot_reasons: list = []
    updates: list[dict] = []


# ======================================================================
# Wartungsfenster
# ======================================================================
DAYS = {"MO": 0, "DI": 1, "MI": 2, "DO": 3, "FR": 4, "SA": 5, "SO": 6,
        "TU": 1, "WE": 2, "TH": 3, "SU": 6}


def in_maintenance_window(
    window: Optional[str], now: Optional[datetime] = None
) -> tuple[bool, str]:
    """
    Format: "SA,SO 02:00-05:00" oder "MO-FR 22:00-06:00" (ueber Mitternacht).
    Leer = jederzeit erlaubt.
    """
    if not window or not window.strip():
        return True, "Kein Fenster gesetzt"

    now = now or localnow()
    try:
        day_part, time_part = window.strip().split(None, 1)
        start_s, end_s = time_part.split("-")
        sh, sm = (int(x) for x in start_s.strip().split(":"))
        eh, em = (int(x) for x in end_s.strip().split(":"))
    except (ValueError, KeyError):
        return False, f"Wartungsfenster nicht lesbar: {window}"

    allowed = set()
    for token in day_part.upper().split(","):
        if "-" in token:
            a, b = token.split("-")
            if a not in DAYS or b not in DAYS:
                return False, f"Unbekannter Wochentag in {window}"
            i, j = DAYS[a], DAYS[b]
            allowed.update(
                range(i, j + 1) if i <= j
                else list(range(i, 7)) + list(range(0, j + 1))
            )
        else:
            if token not in DAYS:
                return False, f"Unbekannter Wochentag: {token}"
            allowed.add(DAYS[token])

    start_min, end_min = sh * 60 + sm, eh * 60 + em
    now_min = now.hour * 60 + now.minute

    if start_min <= end_min:
        if now.weekday() in allowed and start_min <= now_min < end_min:
            return True, "Im Wartungsfenster"
    else:
        if now.weekday() in allowed and now_min >= start_min:
            return True, "Im Wartungsfenster"
        if (now.weekday() - 1) % 7 in allowed and now_min < end_min:
            return True, "Im Wartungsfenster"

    return False, f"Ausserhalb des Wartungsfensters ({window})"


def next_patch_run(host: Host, now: Optional[datetime] = None) -> Optional[datetime]:
    """Naechster geplanter Patch-Zeitpunkt, oder None wenn nicht eingeplant."""
    if not host.patch_enabled or not host.patch_days or not host.patch_time:
        return None
    try:
        hh, mm = (int(x) for x in host.patch_time.split(":"))
    except ValueError:
        return None
    wanted = {DAYS[d] for d in host.patch_days if d in DAYS}
    if not wanted:
        return None

    # Termine sind Ortszeit. Zurueckgegeben wird UTC, damit die Oberflaeche
    # sie wie alle anderen Zeitstempel behandeln kann.
    now = now or localnow()
    for offset in range(8):
        day = now + timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        cand = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand > now:
            return cand.astimezone(UTC)
    return None


PATCH_MAX_RUNS_PER_SLOT = 3

# Hoechstzahl der Nachschlaege, die eine Patch-Kette ueber Neustarts hinweg
# erzeugen darf. Windows braucht kumulativ selten mehr als zwei Runden.
# Die Schranke verhindert, dass ein Update, das sich nicht installieren
# laesst, aber weiter als verfuegbar gemeldet wird, endlos Neustarts
# ausloest.
PATCH_MAX_FOLLOWUPS = 3


def _followup_due(
    host: Host, slot_utc: datetime, session: Optional[Session]
) -> tuple[bool, str]:
    """
    Entscheidet, ob nach einem bereits gelaufenen Termin noch einmal
    gepatcht wird.

    Hintergrund: Windows legt nach einem Patch-Lauf mit Neustart haeufig
    weitere Updates nach - der kumulative Teil bringt Nachfolger mit, die
    vorher gar nicht sichtbar waren. Ohne das hier bleiben die bis zum
    naechsten Termin liegen, obwohl das Wartungsfenster noch laeuft.

    Drei Bremsen, damit daraus keine Endlosschleife wird:
      - nur solange der Nachlauf des Termins laeuft (Pruefung beim Aufrufer)
      - nur wenn der vorige Lauf sauber beendet wurde. Ein fehlgeschlagenes
        Update wuerde sonst alle 15 Sekunden neu versucht.
      - hoechstens PATCH_MAX_RUNS_PER_SLOT Laeufe je Termin.
    """
    if host.updates_available <= 0:
        return False, "fuer diesen Termin bereits gelaufen"
    # Steht ein Neustart aus, wird nicht nachgelegt. Windows nimmt in
    # diesem Zustand keine weiteren Updates an, und der Lauf wuerde ohnehin
    # vom Herunterfahren abgeschnitten. Genau das ist passiert: der Agent
    # meldet den Patch-Auftrag als fertig und startet erst 30 Sekunden
    # spaeter neu - in dieser Luecke wurde der Nachschlag angelegt,
    # angenommen und mitten in der Installation gekappt.
    # Der Nachschlag laeuft stattdessen nach dem Neustart, gesteuert ueber
    # host.patch_followup_left.
    if host.reboot_required:
        return False, "Neustart steht aus - Nachschlag erst danach"
    if session is None:
        return False, "fuer diesen Termin bereits gelaufen"

    runs = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.job_type == JobType.patch,
            Job.created_at >= slot_utc,
        ).order_by(Job.created_at)
    ).all()

    if len(runs) >= PATCH_MAX_RUNS_PER_SLOT:
        return False, "Hoechstzahl der Laeufe fuer diesen Termin erreicht"
    if runs and runs[-1].state is not JobState.done:
        return False, f"voriger Lauf endete mit {runs[-1].state.value}"

    # Woher stammen die gemeldeten Updates?
    #
    # Der Nachlauf-Scan eines Patch-Laufs erzeugt keinen eigenen Auftrag -
    # er laeuft im Patch-Auftrag mit. Ein von Hand ausgeloester Scan ist
    # dagegen ein eigener Auftrag vom Typ scan. Steht ein solcher nach dem
    # letzten Patch-Lauf, kommen die Zahlen von dort.
    #
    # Ohne diese Pruefung genuegte ein Druck auf "Pruefen" im Nachlauf
    # eines bereits gelaufenen Termins, um sofort einen Patch-Lauf
    # auszuloesen. Der Nachschlag ist dafuer da, dass Windows nach einem
    # Patch-Lauf kumulativ nachlegt - nicht dafuer, auf einen Blick ins
    # System hin zu patchen. Wer prueft, will erst sehen.
    if runs:
        manual_scan = session.exec(
            select(Job).where(
                Job.host_id == host.id,
                Job.job_type == JobType.scan,
                Job.created_at > runs[-1].created_at,
            ).limit(1)
        ).first()
        if manual_scan:
            return False, "Zahlen stammen aus einem eigenen Scan - kein Nachschlag"

    return True, f"Nachschlag: {host.updates_available} weitere Updates im Nachlauf"


# Hoechstlaufzeit je Auftragstyp, bevor ein haengender Auftrag verworfen
# wird. Grosszuegig angesetzt: der Agent laesst PowerShell und apt jeweils
# bis zu zwei Stunden laufen, und ein Neustart-Auftrag ueberdauert den
# Neustart selbst. Zu knapp waere schlimmer als zu weit - ein Abbruch
# mitten im Patchen kostet mehr als ein spaeter aufgeraeumter Auftrag.
# Wie oft ein Auftrag ausgeliefert werden darf, ohne dass der Agent ihn
# begonnen hat. Zwei Versuche: einer fuer den Normalfall, einer fuer einen
# Neustart dazwischen. Danach ist von einem Auftrag auszugehen, der den
# Agent zuverlaessig umbringt.
MAX_JOB_DELIVERIES = 2

# Mindestabstand vor einer erneuten Auslieferung. Der Agent arbeitet
# Auftraege einzeln ab und meldet sich waehrenddessen nicht, doppelte
# Ausfuehrung ist also ohnehin unwahrscheinlich - dieser Abstand ist die
# Absicherung fuer den Fall, dass doch zwei Instanzen laufen.
REDELIVER_AFTER = timedelta(seconds=60)

JOB_MAX_RUNTIME = {
    JobType.scan: timedelta(minutes=30),
    JobType.patch: timedelta(hours=4),
    JobType.reboot: timedelta(hours=1),
    JobType.selfupdate: timedelta(minutes=30),
}


def _schedule_due(
    enabled: bool,
    days: list[str],
    time_str: Optional[str],
    grace_hours: int,
    last_run: Optional[datetime],
    host: Host,
    session: Optional[Session],
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """
    Kern von patch_due() und area_patch_due(): prueft, ob ein durch Tage,
    Uhrzeit und Kulanz beschriebener Termin jetzt faellig ist - unabhaengig
    davon, ob der Zeitplan vom Host selbst oder von seinem Bereich stammt.

    Wird beim Heartbeat aufgerufen, es gibt also keinen Hintergrunddienst.
    War der Host zum Termin ausgeschaltet, greift der Nachlauf
    (grace_hours) - danach wird der Termin uebersprungen, damit nicht
    Montagmorgen im Betrieb gepatcht wird.

    last_run ist die Bezugsgroesse fuer genau diesen Zeitplan:
    host.last_patch_run fuer den eigenen, host.area_patch_last_run fuer den
    des Bereichs. Getrennte Felder, weil beide Zeitplaene unabhaengig
    nebeneinander laufen koennen. host bleibt in jedem Fall der Host, fuer
    den geprueft wird - der Nachschlag (_followup_due) ist grundsaetzlich
    host-eigen, unabhaengig davon, welcher Zeitplan den urspruenglichen
    Lauf ausgeloest hat.
    """
    if not enabled or not days or not time_str:
        return False, "nicht eingeplant"
    try:
        hh, mm = (int(x) for x in time_str.split(":"))
    except ValueError:
        return False, f"Uhrzeit nicht lesbar: {time_str}"

    wanted = {DAYS[d] for d in days if d in DAYS}
    if not wanted:
        return False, "kein gueltiger Wochentag"

    now = now or localnow()
    grace = timedelta(hours=max(1, grace_hours or 4))

    # Den zuletzt vergangenen passenden Termin suchen
    for offset in range(8):
        day = now - timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        slot = day.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot > now:
            continue
        if now - slot > grace:
            return False, "Termin liegt zu lange zurueck (Nachlauf abgelaufen)"

        # last_run steht in UTC, der Termin in Ortszeit. Ohne Umrechnung
        # waere der Vergleich um den Zeitzonenversatz daneben - der Lauf
        # koennte doppelt ausloesen oder ganz ausfallen.
        slot_utc = slot.astimezone(UTC)
        if last_run and last_run >= slot_utc:
            return _followup_due(host, slot_utc, session)
        return True, f"Termin {slot:%d.%m. %H:%M} faellig"

    return False, "kein vergangener Termin gefunden"


def patch_due(
    host: Host,
    now: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> tuple[bool, str]:
    """Prueft, ob jetzt der eigene Patch-Zeitplan des Hosts faellig ist."""
    return _schedule_due(
        host.patch_enabled, host.patch_days, host.patch_time,
        host.patch_grace_hours, host.last_patch_run, host, session, now,
    )


def area_patch_due(
    host: Host,
    area: Area,
    now: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> tuple[bool, str]:
    """
    Prueft, ob jetzt der Patch-Zeitplan des Bereichs faellig ist, fuer
    genau diesen Host.

    Absichtlich pro Host statt einmal pro Bereich: Hosts melden sich
    zeitversetzt per Heartbeat. Ein einzelnes last_run am Bereich wuerde
    beim ersten meldenden Host schon auf "erledigt" stehen, und jeder
    andere Host im selben Bereich bekaeme seinen Auftrag fuer diesen
    Termin nie - er wurde ja nie gefragt.
    """
    return _schedule_due(
        area.patch_enabled, area.patch_days, area.patch_time,
        area.patch_grace_hours, host.area_patch_last_run, host, session, now,
    )


# ======================================================================
# Agent-API
# ======================================================================
@app.post("/api/v1/agent/enroll")
def agent_enroll(
    payload: AgentEnroll, request: Request, session: Session = Depends(get_session)
):
    """
    Freie Anmeldung. Der Agent erhaelt ein Dauertoken, bekommt aber erst
    Auftraege, wenn der Host im Dashboard freigegeben wurde.

    Bewusst ohne Enrollment-Token: das System ist fuer den Betrieb im
    lokalen Netz gedacht. Der Schutz liegt allein in der Freigabe.

    Begrenzt ist dagegen die Menge - siehe "Drosselung der
    Agent-Anmeldung" weiter oben.
    """
    quelle = client_ip(request)
    warte = enroll_retry_after(quelle)
    if warte:
        raise HTTPException(
            429,
            "Zu viele Anmeldungen von dieser Adresse. Bitte spaeter erneut "
            "versuchen.",
            headers={"Retry-After": str(warte)},
        )

    hostname = check_hostname(payload.hostname)
    existing = session.exec(
        select(Host).where(Host.hostname == hostname)
    ).first()
    if existing and existing.agent_token_hash:
        raise HTTPException(
            409,
            f"'{hostname}' ist bereits angemeldet. Fuer eine "
            f"Neuanmeldung den Host zuerst im Dashboard entfernen.",
        )

    if existing:
        # Das Token wurde im Dashboard zurueckgezogen - der Host meldet
        # sich neu an und behaelt dabei Zeitplan, Checkmk-Verknuepfung und
        # seinen Verlauf. Ein Loeschen und Neuanlegen wuerde all das
        # verwerfen.
        #
        # Die Freigabe wird zurueckgesetzt: waehrend das Token
        # zurueckgezogen ist, kann sich jedes Geraet im Netz unter diesem
        # Namen melden. Wer es tatsaechlich war, entscheidet ein Mensch.
        token = secrets.token_urlsafe(32)
        existing.agent_token_hash = hash_token(token)
        existing.approval_state = ApprovalState.pending
        existing.os_type = payload.os_type
        existing.os_version = kurz(payload.os_version, FELD_MAX)
        existing.ip_address = kurz(payload.ip_address, IP_MAX)
        existing.agent_version = kurz(payload.agent_version, VERSION_MAX)
        existing.enrolled_at = utcnow()
        existing.enrolled_from_ip = quelle
        existing.last_seen = None
        session.add(existing)
        audit(session, f"agent:{hostname}", "agent.reenroll",
              "nach zurueckgezogenem Token", request)
        session.commit()
        note_enroll(quelle)
        # Gleiche Form wie bei der Erstanmeldung - der Agent wertet nur
        # agent_token aus, aber abweichende Antworten auf denselben
        # Endpunkt sind eine Falle fuer spaeter.
        return {
            "agent_token": token,
            "approval_state": "pending",
            "message": "Neu angemeldet. Wartet auf Freigabe im Dashboard.",
        }

    # Die Obergrenze gilt nur fuer NEUE Zeilen. Die Neuanmeldung oben
    # betrifft einen Host, den es schon gibt - sie legt nichts an, und sie
    # abzuweisen wuerde einen bekannten Host aussperren, weil andere
    # warten.
    if pending_count(session) >= PENDING_MAX:
        raise HTTPException(
            429,
            f"Es warten bereits {PENDING_MAX} Hosts auf Freigabe. Erst im "
            f"Dashboard freigeben oder entfernen, dann meldet sich dieser "
            f"Host beim naechsten Versuch an.",
        )

    token = secrets.token_urlsafe(32)
    # Neuer Host ans Ende. Mit dem Vorgabewert 0 stuende er sonst vor
    # allen bereits einsortierten.
    last = session.exec(
        select(Host.sort_order).order_by(Host.sort_order.desc()).limit(1)
    ).first()
    host = Host(
        sort_order=(last or 0) + 1,
        hostname=hostname,
        agent_token_hash=hash_token(token),
        os_type=payload.os_type,
        os_version=kurz(payload.os_version, FELD_MAX),
        ip_address=kurz(payload.ip_address, IP_MAX),
        agent_version=kurz(payload.agent_version, VERSION_MAX),
        approval_state=ApprovalState.pending,
        enrolled_at=utcnow(),
        # Dieselbe Ermittlung wie in der Neuanmeldung oben und wie im
        # Pruefprotokoll (F-10). Vorher stand hier die Adresse der
        # Verbindung: hinter einem Reverse Proxy war das immer die des
        # Proxys, und im Dashboard sah dann jeder Host so aus, als kaeme er
        # vom selben Rechner.
        enrolled_from_ip=quelle,
    )
    session.add(host)
    session.commit()
    note_enroll(quelle)

    return {
        "agent_token": token,
        "approval_state": "pending",
        "message": "Angemeldet. Wartet auf Freigabe im Dashboard.",
    }


@app.post("/api/v1/agent/heartbeat")
def agent_heartbeat(
    payload: AgentHeartbeat,
    request: Request,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    host = host_by_token(x_agent_token, session)

    # Fuer die Bereitschaftsanzeige: welche Agents sprechen schon
    # verschluesselt? Wer beim Umschalten noch auf HTTP steht, faellt
    # sofort aus - und still, weil der Agent den Fehler nur in seine
    # eigene Protokolldatei schreibt.
    host.last_seen_secure = request_is_https(request)

    # Auch hier pruefen: der Heartbeat darf den Namen aendern, und ohne
    # diese Zeile liesse sich die Pruefung bei der Anmeldung umgehen -
    # erst sauber anmelden, dann im naechsten Heartbeat umbenennen.
    host.hostname = check_hostname(payload.hostname)
    host.os_type = payload.os_type
    host.os_version = kurz(payload.os_version, FELD_MAX)
    host.ip_address = kurz(payload.ip_address, IP_MAX)
    host.agent_version = kurz(payload.agent_version, VERSION_MAX)
    host.reboot_required = payload.reboot_required
    host.reboot_reasons = kurze_gruende(payload.reboot_reasons)
    host.status = HostStatus.online
    host.last_seen = utcnow()
    session.add(host)

    if host.approval_state == ApprovalState.approved:
        try:
            advance_agent_rollout(session)
        except Exception:  # noqa: BLE001
            pass

    if host.approval_state != ApprovalState.approved:
        session.commit()
        return {
            "poll_interval": 300,
            "jobs": [],
            "approval_state": host.approval_state.value,
            "agent_version": agent_source_version(),
        }

    def patch_job_open() -> bool:
        """Laeuft oder wartet bereits ein Patch-Auftrag fuer diesen Host?"""
        return session.exec(
            select(Job).where(
                Job.host_id == host.id,
                Job.job_type == JobType.patch,
                Job.state.in_([JobState.pending, JobState.running]),
            )
        ).first() is not None

    # Vorgemerkter Nachschlag nach einem Neustart. Hat Vorrang vor dem
    # Zeitplan und laeuft bewusst ohne Ruecksicht auf den Nachlauf
    # (patch_grace_hours): ein Windows-Server kann laenger neu starten, als
    # der Nachlauf reicht, und die nachgelegten Updates blieben sonst bis
    # zum naechsten Termin liegen.
    if host.patch_followup_left > 0 and not payload.reboot_pending:
        if payload.reboot_required:
            # Neustart steht weiter aus - der Nachschlag wartet.
            pass
        elif host.updates_available <= 0:
            # Nichts mehr offen, Kette beenden.
            host.patch_followup_left = 0
            session.add(host)
        elif not patch_job_open():
            host.patch_followup_left -= 1
            session.add(Job(
                host_id=host.id,
                job_type=JobType.patch,
                params={
                    "scheduled": True,
                    "followup": True,
                    "reboot_if_needed": host.patch_auto_reboot,
                    "downtime_minutes": host.downtime_minutes,
                },
            ))
            host.last_patch_run = utcnow()
            session.add(host)

    # Zeitplan pruefen und bei Faelligkeit einen Patch-Auftrag anlegen.
    # Bewusst hier statt in einem Hintergrunddienst: der Agent meldet sich
    # ohnehin regelmaessig, und so kann nichts auseinanderlaufen.
    due, reason = patch_due(host, session=session)
    if due and not payload.reboot_pending:
        if not patch_job_open():
            session.add(Job(
                host_id=host.id,
                job_type=JobType.patch,
                params={
                    "scheduled": True,
                    "reboot_if_needed": host.patch_auto_reboot,
                    "downtime_minutes": host.downtime_minutes,
                },
            ))
            host.last_patch_run = utcnow()
            session.add(host)

    # Zeitplan des Bereichs pruefen, unabhaengig vom eigenen Zeitplan oben -
    # beide koennen nebeneinander laufen, das wird beim Speichern des
    # Bereichs nur angezeigt, nicht verhindert. patch_job_open() sorgt
    # dafuer, dass nie zwei Patch-Auftraege gleichzeitig fuer denselben
    # Host entstehen: trifft der Bereichs-Termin auf einen schon laufenden
    # Lauf (gleich welcher Herkunft), wird er uebersprungen und beim
    # naechsten Termin neu geprueft - kein doppeltes Patchen, aber auch
    # keine Sonderbehandlung noetig.
    if host.area_id and not payload.reboot_pending:
        area = session.get(Area, host.area_id)
        if area:
            area_due, area_reason = area_patch_due(host, area, session=session)
            if area_due and not patch_job_open():
                session.add(Job(
                    host_id=host.id,
                    job_type=JobType.patch,
                    params={
                        "scheduled": True,
                        "source_area_id": area.id,
                        "reboot_if_needed": area.patch_auto_reboot,
                        "downtime_minutes": area.downtime_minutes,
                    },
                ))
                host.area_patch_last_run = utcnow()
                session.add(host)

    now = utcnow()

    # Abgelaufene Auftraege verwerfen, bevor irgendetwas ausgeliefert wird.
    # Sonst wuerde ein fuer Samstag nachts geplanter Neustart Montag frueh
    # ausgefuehrt, wenn der Host ueber das Wochenende aus war.
    expired = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.state == JobState.pending,
            Job.expires_at != None,          # noqa: E711
            Job.expires_at <= now,
        )
    ).all()
    for job in expired:
        job.state = JobState.cancelled
        job.finished_at = now
        grace = (job.params or {}).get("grace_minutes")
        when = job.scheduled_at or job.created_at
        job.error = (
            f"Nicht ausgefuehrt: Nachlauf abgelaufen. Geplant war "
            f"{when:%d.%m.%Y %H:%M} UTC, Nachlauf {grace} Minuten. "
            f"Der Host hat sich in diesem Zeitraum nicht gemeldet."
        )
        session.add(job)

    # Laufende Auftraege ohne Abschluss einsammeln. Ein Auftrag, dessen
    # Agent mitten im Lauf verschwindet - Absturz, Neustart, misslungene
    # Selbstaktualisierung - bleibt sonst dauerhaft auf running stehen und
    # blockiert jeden weiteren Auftrag desselben Typs fuer diesen Host,
    # ohne dass irgendwo ein Grund sichtbar waere.
    #
    # Zwei Wege. Der erste ist die Boot-Zeit: hat der Host seit dem Beginn
    # des Auftrags neu gestartet, kann der Agent ihn nicht mehr fortsetzen -
    # er hat kein Gedaechtnis ueber einen Neustart hinweg. Das steht sofort
    # fest, statt vier Stunden auf die Hoechstlaufzeit zu warten, waehrend
    # der Auftrag jeden weiteren desselben Typs blockiert.
    #
    # Der zweite ist die Hoechstlaufzeit als Auffangnetz - fuer Abstuerze
    # ohne Neustart und fuer Agents, die keine Boot-Zeit liefern.
    # from_timestamp liefert UTC *mit* Zeitzone. Vorher stand hier ein
    # .replace(tzinfo=None): der Vergleich mit job.started_at warf dann den
    # TypeError, fuer den utctime.py gerade gebaut ist - beim Agent kam er
    # als 500 an, und der Heartbeat schlug dauerhaft fehl.
    booted_at = (
        from_timestamp(payload.boot_time)
        if payload.boot_time and payload.boot_time > 0
        else None
    )

    for job in session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.state == JobState.running,
            Job.started_at != None,          # noqa: E711
        )
    ).all():
        limit = JOB_MAX_RUNTIME.get(job.job_type, timedelta(hours=1))

        # Kleiner Vorlauf, weil die Uhren von Host und Server nicht exakt
        # gleich gehen. Ohne ihn koennte ein Auftrag, der Sekunden nach dem
        # Start begonnen hat, faelschlich als abgeschnitten gelten.
        if booted_at and job.started_at < booted_at - timedelta(minutes=2):
            job.state = JobState.cancelled
            job.finished_at = now
            job.error = (
                "Abgebrochen: der Host hat neu gestartet, waehrend der Auftrag "
                "lief. Der Agent kann ihn nach einem Neustart nicht "
                "fortsetzen."
            )
            session.add(job)
            continue

        if now - job.started_at <= limit:
            continue
        job.state = JobState.cancelled
        job.finished_at = now
        job.error = (
            f"Abgebrochen: seit {int((now - job.started_at).total_seconds() // 60)} "
            f"Minuten ohne Rueckmeldung (Grenze "
            f"{int(limit.total_seconds() // 60)} Minuten). Der Agent hat den "
            f"Auftrag nie abgeschlossen."
        )
        session.add(job)

    # Waehrend eines angestossenen Neustarts nichts ausliefern. Der Agent
    # lehnt Auftraege in diesem Zustand ohnehin ab - wuerden sie trotzdem
    # zugestellt, liefe der Zaehler MAX_JOB_DELIVERIES hoch und der Auftrag
    # waere nach zwei Runden verworfen, obwohl ihn nie jemand begonnen hat.
    if payload.reboot_pending:
        session.commit()
        return {
            "poll_interval": int(os.getenv("CO37_POLL_INTERVAL", "15")),
            "jobs": [],
            "approval_state": host.approval_state.value,
            "agent_version": agent_source_version(),
        }

    # Geplante Auftraege erst herausgeben, wenn der Zeitpunkt erreicht ist
    jobs = session.exec(
        select(Job)
        .where(
            Job.host_id == host.id,
            Job.state == JobState.pending,
            or_(Job.scheduled_at == None, Job.scheduled_at <= now),  # noqa: E711
            or_(Job.last_delivered_at == None,                       # noqa: E711
                Job.last_delivered_at <= now - REDELIVER_AFTER),
        )
        .order_by(Job.created_at)
    ).all()

    out = []
    for job in jobs:
        # Nicht mehr sofort auf running setzen. Startet der Agent zwischen
        # Abholen und Ausfuehren neu - beim Selfupdate der Normalfall -,
        # blieb der Auftrag sonst als Leiche auf running stehen, bis die
        # Hoechstlaufzeit ihn verwarf. Bei einem Patch-Auftrag blockiert
        # das jeden weiteren Auftrag desselben Typs.
        if job.delivered >= MAX_JOB_DELIVERIES:
            job.state = JobState.cancelled
            job.finished_at = now
            job.error = (
                f"Abgebrochen: {job.delivered} mal an den Agent ausgeliefert, "
                f"ohne dass er ihn begonnen hat."
            )
            session.add(job)
            continue

        job.delivered += 1
        job.last_delivered_at = now
        session.add(job)
        out.append({"id": job.id, "type": job.job_type.value, "params": job.params or {}})

    # Solange Auftraege anstehen, haeufiger nachfragen. Ein Auftrag zieht
    # meist weitere nach (Patch -> Nachlauf-Scan -> Neustart).
    base = int(os.getenv("CO37_POLL_INTERVAL", "15"))
    busy = int(os.getenv("CO37_POLL_BUSY", "5"))

    session.commit()
    return {
        "poll_interval": busy if out else base,
        "jobs": out,
        "approval_state": "approved",
        "agent_version": agent_source_version(),
    }


@app.post("/api/v1/agent/scan-result")
def agent_scan_result(
    payload: AgentScanResult,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    host = require_approved(host_by_token(x_agent_token, session))

    for row in session.exec(
        select(UpdatePackage).where(UpdatePackage.host_id == host.id)
    ).all():
        session.delete(row)

    security = 0
    will_need_reboot = False
    for upd in payload.updates:
        pkg = UpdatePackage(
            host_id=host.id,
            package_id=str(upd.get("id", ""))[:255],
            title=str(upd.get("title", ""))[:500],
            current_version=upd.get("current_version"),
            new_version=upd.get("new_version"),
            is_security=bool(upd.get("is_security", False)),
            requires_reboot=bool(upd.get("requires_reboot", False)),
            size_bytes=upd.get("size_bytes"),
        )
        security += int(pkg.is_security)
        will_need_reboot = will_need_reboot or pkg.requires_reboot
        session.add(pkg)

    host.updates_available = len(payload.updates)
    host.security_updates = security
    host.reboot_required = payload.reboot_required
    host.reboot_reasons = kurze_gruende(payload.reboot_reasons)
    # Aus den gemeldeten Paketen abgeleitet, nicht als eigenes Feld
    # uebertragen: so gibt es nur eine Quelle. Verschwindet ein
    # Kernel-Update aus der Liste, faellt die Vorschau von selbst weg -
    # ein separat uebertragener Wert koennte hier stehenbleiben.
    host.updates_require_reboot = will_need_reboot
    host.last_scan = utcnow()
    session.add(host)
    session.commit()
    return {"ok": True, "stored": len(payload.updates)}


class AgentJobLog(BaseModel):
    job_id: int
    text: str
    # Kurzer Fortschrittstext fuer die Uebersicht, z.B. "Installiere 3 von 12"
    progress: Optional[str] = None


@app.post("/api/v1/agent/job-log")
def agent_job_log(
    payload: AgentJobLog,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    """
    Nimmt laufende Ausgabe eines Auftrags an. Der Agent schickt alle ein bis
    zwei Sekunden die neu angefallenen Zeilen.
    """
    host = require_approved(host_by_token(x_agent_token, session))
    job = session.get(Job, payload.job_id)
    if not job or job.host_id != host.id:
        raise HTTPException(404, "Auftrag nicht gefunden")

    # Erste Rueckmeldung des Agenten: jetzt laeuft der Auftrag wirklich.
    # Vorher stand er nur als ausgeliefert vermerkt.
    if job.state == JobState.pending:
        job.state = JobState.running
        job.started_at = utcnow()
        session.add(job)
        session.commit()

    res = joblog.append(payload.job_id, payload.text)

    if payload.progress:
        params = dict(job.params or {})
        params["progress"] = payload.progress[:200]
        job.params = params
        session.add(job)
        session.commit()

    # Bei erreichter Obergrenze hoert der Agent auf zu senden
    return {"ok": True, "truncated": res["truncated"]}


@app.post("/api/v1/agent/pre-reboot")
async def agent_pre_reboot(
    job_id: int,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    """
    Freigabestelle fuer Neustarts.

    Der Agent hat selbst festgestellt, DASS ein Neustart aussteht. Hier wird
    entschieden, OB er jetzt erfolgen darf, und erst bei Freigabe wird die
    Checkmk-Downtime gesetzt - auf allen verknuepften Hosts.
    """
    host = require_approved(host_by_token(x_agent_token, session))
    job = session.get(Job, job_id)
    if not job or job.host_id != host.id:
        raise HTTPException(404, "Job nicht gefunden")

    # Kam der Patch-Lauf aus dem Zeitplan eines Bereichs, gilt fuer die
    # Downtime dessen Checkmk-Verknuepfung statt der des Hosts - deshalb
    # hat der Bereich seine eigene. Area und Host tragen dieselben
    # Feldnamen (checkmk_hosts, checkmk_downtime_all), has_downtime_target()
    # und downtime_targets() lesen nur diese, unabhaengig vom Typ.
    dt_source = host
    area_id = job.params.get("source_area_id")
    if area_id:
        area = session.get(Area, area_id)
        if area:
            dt_source = area

    def deny(reason: str):
        job.log = (job.log or "") + f"\nNeustart nicht freigegeben: {reason}"
        session.add(job)
        session.commit()
        return {"approved": False, "downtime_set": False, "reason": reason}

    # Manuell ausgeloester Neustart umgeht Richtlinie und Fenster,
    # nicht aber die Downtime.
    manual = bool(job.params.get("manual", False))

    if not manual:
        if not job.params.get("reboot_if_needed", host.auto_reboot):
            return deny("Eigenstaendiger Neustart fuer diesen Host nicht freigegeben")
        ok, reason = in_maintenance_window(host.maintenance_window)
        if not ok:
            return deny(reason)

    if job.params.get("skip_downtime"):
        job.log = (job.log or "") + "\nNeustart ohne Downtime (so geplant)"
        session.add(job)
        session.commit()
        return {"approved": True, "downtime_set": False,
                "reason": "Downtime wurde bewusst uebersprungen"}

    minutes = int(job.params.get("downtime_minutes", host.downtime_minutes))

    if not has_downtime_target(dt_source):
        if not job.params.get("allow_without_downtime", False):
            return deny("Keine Checkmk-Hosts verknuepft - Neustart unterbunden")
        job.log = (job.log or "") + "\nNeustart ohne Downtime (ausdruecklich erlaubt)"
        session.add(job)
        session.commit()
        return {"approved": True, "downtime_set": False,
                "reason": "Keine Checkmk-Verknuepfung"}

    cmk = get_checkmk(session)
    if not cmk:
        if not job.params.get("allow_without_downtime", False):
            return deny("Checkmk nicht konfiguriert - Neustart unterbunden")
        return {"approved": True, "downtime_set": False,
                "reason": "Checkmk nicht konfiguriert"}

    # Die Auflistung kann scheitern - dann steht keine Zielliste fest und
    # es wird nicht neu gestartet. Ohne diesen Zweig endet der Aufruf in
    # einem 500, und der Agent bekaeme gar keine Antwort.
    try:
        targets = await downtime_targets(dt_source, cmk)
    except CheckmkError as exc:
        if not job.params.get("allow_without_downtime", False):
            return deny(f"Checkmk-Hosts nicht abrufbar - Neustart unterbunden: {exc}")
        return {"approved": True, "downtime_set": False,
                "reason": "Checkmk-Hosts nicht abrufbar"}

    if not targets:
        if not job.params.get("allow_without_downtime", False):
            return deny("Kein Checkmk-Host ermittelt - Neustart unterbunden")
        return {"approved": True, "downtime_set": False,
                "reason": "Kein Checkmk-Host ermittelt"}

    res = await cmk.set_downtime_multi(
        targets, minutes=minutes,
        comment=f"CO-37: Neustart {host.hostname} (Job {job.id})",
    )

    # Schlaegt eine Downtime fehl, wird nicht neu gestartet - sonst schlaegt
    # das Monitoring beim Kunden Alarm.
    if res["failed"] and not job.params.get("allow_partial_downtime", False):
        return deny(f"Downtime fehlgeschlagen fuer: {', '.join(res['failed'])}")

    job.downtime_ref = ",".join(res["ok"])
    # Bei gesetztem Flag koennen das hunderte Namen sein - die wuerden das
    # Auftragsprotokoll unlesbar machen.
    if len(res["ok"]) > 8:
        names_text = f"{', '.join(res['ok'][:8])} und {len(res['ok']) - 8} weitere"
    else:
        names_text = ", ".join(res["ok"])
    job.log = (job.log or "") + (
        f"\n[{localnow():%H:%M:%S}] Downtime gesetzt ({minutes} min) auf: "
        f"{names_text} - Neustart freigegeben"
    )
    session.add(job)
    session.commit()
    return {"approved": True, "downtime_set": True,
            "minutes": minutes, "hosts": res["ok"]}


@app.post("/api/v1/agent/report")
async def agent_report(
    payload: AgentJobReport,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    host = require_approved(host_by_token(x_agent_token, session))
    job = session.get(Job, payload.job_id)
    if not job or job.host_id != host.id:
        raise HTTPException(404, "Job nicht gefunden")

    # Auch hier den Startzeitpunkt nachtragen. Gesetzt wird er sonst nur
    # bei der ersten Protokollzeile - meldet ein Auftrag ohne Ausgabe, ist
    # started_at leer, und beide Aufraeumwege uebergehen ihn, weil sie
    # 'started_at != NULL' voraussetzen. Ein solcher Auftrag bliebe fuer
    # immer auf running stehen.
    if payload.state == JobState.running and job.started_at is None:
        job.started_at = utcnow()

    job.state = payload.state
    job.log = payload.log
    job.error = payload.error
    job.result = payload.result
    if payload.state in (JobState.done, JobState.failed, JobState.cancelled):
        job.finished_at = utcnow()
    session.add(job)

    if payload.reboot_required is not None:
        host.reboot_required = payload.reboot_required
        session.add(host)

    # Patch-Lauf, der in einen Neustart muendet: Nachschlag vormerken.
    # Erst nach dem Start liegen die kumulativ nachgelegten Updates
    # installierbar bereit - vorher weist Windows sie ab.
    #
    # Nur ein Lauf, der die Kette eroeffnet, setzt den Zaehler. Ein
    # Nachschlag selbst darf ihn nicht wieder auffuellen - sonst startet
    # die Kette bei jedem Erreichen von null von vorn und laeuft endlos.
    # Erkennbar am Parameter 'followup', den nur der Heartbeat setzt.
    if (
        job.job_type == JobType.patch
        and (payload.result or {}).get("rebooting")
        and not (job.params or {}).get("followup")
    ):
        host.patch_followup_left = PATCH_MAX_FOLLOWUPS
        session.add(host)

    session.commit()

    # Downtime aufheben - greift NUR, wenn kein Neustart im Spiel war.
    #
    # Im Neustart-Ablauf meldet der Agent den Auftrag als fertig, bevor er
    # herunterfaehrt; reboot_required steht zu diesem Zeitpunkt noch. Die
    # Bedingung trifft also nicht zu, und nach dem Neustart kommt fuer
    # diesen Auftrag keine Meldung mehr. Die Downtime laeuft dann bis zum
    # Ende ihrer Dauer.
    #
    # Das ist eine bewusste Festlegung, kein Versehen: frueh aufzuheben
    # hiesse, sich auf den Agent als Zeugen zu verlassen. Der meldet sich
    # Sekunden nach dem Start, waehrend Checkmk den Host noch nicht neu
    # geprueft hat und Dienste erst hochlaufen - die Benachrichtigungen
    # gingen dann trotzdem raus. Gesteuert wird ueber downtime_minutes.
    if (
        job.state == JobState.done
        and job.downtime_ref
        and not host.reboot_required
    ):
        cmk = get_checkmk(session)
        if cmk:
            names = [n for n in job.downtime_ref.split(",") if n]
            try:
                await cmk.remove_downtime_multi(names)
                job.log = (job.log or "") + "\nDowntime aufgehoben"
                session.add(job)
                session.commit()
            except CheckmkError:
                pass

    return {"ok": True}


@app.post("/api/v1/agent/notice")
def agent_notice(
    payload: AgentNotice,
    x_agent_token: str = Header(...),
    session: Session = Depends(get_session),
):
    """
    Meldung ausserhalb eines laufenden Auftrags.

    Gebraucht wird das beim Rueckrollen einer fehlgeschlagenen
    Selbstaktualisierung: der Agent stellt das beim Start fest, also lange
    nachdem der zugehoerige Auftrag als erledigt gemeldet wurde. Ohne diesen
    Weg stuende der Rueckfall nur im Journal des Zielsystems, waehrend die
    Oberflaeche weiter erfolgreiche Auftraege bei unveraenderter Version
    zeigt - genau die Kombination, die das Aufspueren so lange gekostet hat.

    Angehaengt wird an den juengsten Selfupdate-Auftrag des Hosts, damit die
    Meldung im Verlauf an der Stelle auftaucht, an der man sie sucht.
    """
    host = require_approved(host_by_token(x_agent_token, session))

    job = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.job_type == JobType.selfupdate,
        ).order_by(Job.created_at.desc())
    ).first()
    if not job:
        return {"ok": True, "attached": False}

    job.state = JobState.failed
    job.error = payload.message
    job.log = (job.log or "") + "\n" + payload.message
    job.result = {**(job.result or {}), **payload.result}
    session.add(job)
    session.commit()
    return {"ok": True, "attached": True, "job_id": job.id}


# ----------------------------------------------------------------------
# Agent-Selbstaktualisierung
# ----------------------------------------------------------------------
def agent_source_version() -> str:
    """Liest AGENT_VERSION aus der ausgelieferten agent.py."""
    try:
        for line in AGENT_SRC.read_text(encoding="utf-8").splitlines():
            if line.startswith("AGENT_VERSION"):
                return line.split("=")[1].strip().strip('"\'')
    except Exception:  # noqa: BLE001
        pass
    return "unbekannt"


@app.get("/api/v1/agent/code")
def agent_code(x_agent_token: str = Header(...), session: Session = Depends(get_session)):
    """
    Liefert den aktuellen Agent-Quellcode samt Pruefsumme und Signatur.
    Nur fuer freigegebene Hosts.

    Zur Signatur (F-07 der Sicherheitspruefung vom 2026-08-22): die
    Pruefsumme kommt aus derselben Antwort wie der Code. Wer die Antwort
    faelschen kann, faelscht beide - sie schuetzt gegen einen
    abgebrochenen Download, nicht gegen Manipulation. Genau die
    Ueberlegung, die beim Update-Paket zur Signatur gefuehrt hat, gilt
    hier: der Agent laeuft als root beziehungsweise als SYSTEM.

    Die .sig-Datei entsteht beim Bauen des Pakets (build_release.py) und
    liegt neben agent.py. Fehlt sie, wird die Antwort trotzdem
    ausgeliefert - was daraus folgt, entscheidet der Agent: er kennt
    seinen eigenen ausgelieferten Schluessel und weiss, ob er pruefen
    muss.
    """
    require_approved(host_by_token(x_agent_token, session))
    if not AGENT_SRC.is_file():
        raise HTTPException(404, "agent.py nicht vorhanden")
    code = AGENT_SRC.read_bytes()
    sig_datei = AGENT_SRC.parent / (AGENT_SRC.name + ".sig")
    signatur = ""
    if sig_datei.is_file():
        try:
            signatur = sig_datei.read_text(encoding="ascii").strip()
        except OSError:
            signatur = ""
    return {
        "version": agent_source_version(),
        "sha256": hashlib.sha256(code).hexdigest(),
        "signature": signatur,
        "code": code.decode("utf-8"),
    }


# ======================================================================
# Automatisches Ausrollen des Agents
# ======================================================================
#
# Nach einem Systemupdate koennen die Agents selbsttaetig nachgezogen werden.
# Abschaltbar, weil es bedeutet, dass Code ohne weiteres Zutun auf fremde
# Server geschoben wird - das will man auf Kundensystemen vielleicht bewusst
# entscheiden.
#
# Gestaffelt beginnt mit einem waehlbaren Pilot-Host. Erst wenn der sich mit
# der neuen Version zurueckgemeldet hat, folgen die uebrigen. Damit trifft
# ein fehlerhaftes Rollout zunaechst nur ein System.

ROLL_DEFAULTS = {
    "agent_autoroll": "true",
    "agent_roll_mode": "staged",     # "staged" oder "all"
    "agent_pilot_host": "",
    "agent_roll_state": "idle",      # idle | pilot | rest | done | failed
    "agent_roll_version": "",
    "agent_roll_started": "",
    "agent_roll_note": "",
}

PILOT_TIMEOUT_MINUTES = 30


def _set(session: Session, key: str, value: str):
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        row = Setting(key=key, value=value)
    session.add(row)


def roll_settings(session: Session) -> dict:
    return {k: _setting(session, k, v) for k, v in ROLL_DEFAULTS.items()}


def stale_agents(session: Session, version: str) -> list[Host]:
    """Freigegebene Hosts, deren Agent nicht auf der gewuenschten Version ist."""
    return [
        h for h in session.exec(
            select(Host).where(Host.approval_state == ApprovalState.approved)
        ).all()
        if h.agent_version and h.agent_version != version
    ]


def _queue_selfupdate(session: Session, host: Host) -> bool:
    """Legt einen Aktualisierungsauftrag an, falls nicht schon einer offen ist."""
    existing = session.exec(
        select(Job).where(
            Job.host_id == host.id,
            Job.job_type == JobType.selfupdate,
            Job.state.in_([JobState.pending, JobState.running]),
        )
    ).first()
    if existing:
        return False
    session.add(Job(host_id=host.id, job_type=JobType.selfupdate,
                    params={"auto": True}))
    return True


def start_agent_rollout(session: Session, manual: bool = False) -> dict:
    """Startet das Ausrollen, sofern noetig."""
    cfg = roll_settings(session)
    version = agent_source_version()

    if not manual and cfg["agent_autoroll"] != "true":
        return {"started": False, "reason": "Automatisches Ausrollen ist abgeschaltet"}

    targets = stale_agents(session, version)
    if not targets:
        _set(session, "agent_roll_state", "done")
        _set(session, "agent_roll_version", version)
        _set(session, "agent_roll_note", "Alle Agents sind aktuell")
        session.commit()
        return {"started": False, "reason": "Alle Agents sind bereits aktuell"}

    _set(session, "agent_roll_version", version)
    _set(session, "agent_roll_started", utcnow().isoformat())

    pilot_id = cfg["agent_pilot_host"]
    pilot = None
    if cfg["agent_roll_mode"] == "staged" and pilot_id:
        pilot = next((h for h in targets if str(h.id) == str(pilot_id)), None)

    if cfg["agent_roll_mode"] == "staged" and pilot:
        _queue_selfupdate(session, pilot)
        _set(session, "agent_roll_state", "pilot")
        _set(session, "agent_roll_note",
             f"Pilot {pilot.hostname} wird aktualisiert, danach folgen "
             f"{len(targets) - 1} weitere")
        session.commit()
        return {"started": True, "mode": "staged", "pilot": pilot.hostname,
                "remaining": len(targets) - 1}

    count = sum(_queue_selfupdate(session, h) for h in targets)
    _set(session, "agent_roll_state", "rest")
    note = f"{count} Agents werden aktualisiert"
    if cfg["agent_roll_mode"] == "staged" and not pilot:
        note += " (kein Pilot-Host gesetzt oder bereits aktuell)"
    _set(session, "agent_roll_note", note)
    session.commit()
    return {"started": True, "mode": "all", "count": count}


def advance_agent_rollout(session: Session):
    """
    Bringt ein laufendes Ausrollen weiter. Wird beim Heartbeat aufgerufen,
    es laeuft also kein Hintergrunddienst.
    """
    state = _setting(session, "agent_roll_state", "idle")
    if state not in ("pilot", "rest"):
        return

    version = _setting(session, "agent_roll_version") or agent_source_version()

    if state == "pilot":
        pilot_id = _setting(session, "agent_pilot_host")
        pilot = session.get(Host, int(pilot_id)) if pilot_id.isdigit() else None
        if not pilot:
            _set(session, "agent_roll_state", "failed")
            _set(session, "agent_roll_note", "Pilot-Host nicht mehr vorhanden")
            session.commit()
            return

        if pilot.agent_version == version:
            targets = [h for h in stale_agents(session, version) if h.id != pilot.id]
            count = sum(_queue_selfupdate(session, h) for h in targets)
            _set(session, "agent_roll_state", "rest" if count else "done")
            _set(session, "agent_roll_note",
                 f"Pilot {pilot.hostname} erfolgreich, {count} weitere folgen"
                 if count else
                 f"Pilot {pilot.hostname} erfolgreich, keine weiteren offen")
            session.commit()
            return

        started = _setting(session, "agent_roll_started")
        begin = utcnow()
        if started:
            try:
                begin = datetime.fromisoformat(started)
            except ValueError:
                pass

        # Nur Fehlschlaege aus DIESEM Durchlauf zaehlen. Ohne den Zeitbezug
        # wuerde ein alter fehlgeschlagener Auftrag jedes spaetere Ausrollen
        # sofort als gescheitert abstempeln.
        failed = session.exec(
            select(Job).where(
                Job.host_id == pilot.id,
                Job.job_type == JobType.selfupdate,
                Job.state == JobState.failed,
                Job.created_at >= begin,
            ).order_by(Job.created_at.desc())
        ).first()
        if failed:
            _set(session, "agent_roll_state", "failed")
            _set(session, "agent_roll_note",
                 f"Pilot {pilot.hostname} fehlgeschlagen: {failed.error or 'unbekannt'}. "
                 f"Es wurde nicht weiter ausgerollt.")
            session.commit()
            return

        if utcnow() - begin > timedelta(minutes=PILOT_TIMEOUT_MINUTES):
            _set(session, "agent_roll_state", "failed")
            _set(session, "agent_roll_note",
                 f"Pilot {pilot.hostname} hat sich seit "
                 f"{PILOT_TIMEOUT_MINUTES} Minuten nicht zurueckgemeldet. "
                 f"Es wurde nicht weiter ausgerollt.")
            session.commit()
        return

    if state == "rest" and not stale_agents(session, version):
        _set(session, "agent_roll_state", "done")
        _set(session, "agent_roll_note", "Alle Agents sind aktuell")
        session.commit()


class RollConfig(BaseModel):
    autoroll: Optional[bool] = None
    mode: Optional[str] = None          # "staged" oder "all"
    pilot_host_id: Optional[int] = None


@app.get("/api/v1/agent-rollout", dependencies=[Depends(require_admin)])
def get_rollout(session: Session = Depends(get_session)):
    cfg = roll_settings(session)
    version = agent_source_version()
    stale = stale_agents(session, version)
    pilot_id = cfg["agent_pilot_host"]
    pilot = session.get(Host, int(pilot_id)) if pilot_id.isdigit() else None

    # 'Abgeschlossen' neben '3 veraltet' widerspricht sich. Kommen nach einem
    # abgeschlossenen Durchlauf neue oder aeltere Hosts dazu, ist der Zustand
    # wieder 'bereit'.
    state = cfg["agent_roll_state"]
    note = cfg["agent_roll_note"]
    if state == "done" and stale:
        state = "idle"
        note = f"{len(stale)} Host(s) sind nicht auf {version}"

    return {
        "autoroll": cfg["agent_autoroll"] == "true",
        "mode": cfg["agent_roll_mode"],
        "pilot_host_id": pilot.id if pilot else None,
        "pilot_hostname": pilot.hostname if pilot else None,
        "state": state,
        "note": note,
        "version": version,
        "stale_count": len(stale),
        "stale": [{"id": h.id, "hostname": h.hostname,
                   "agent_version": h.agent_version} for h in stale],
        "candidates": [
            {"id": h.id, "hostname": h.hostname, "agent_version": h.agent_version}
            for h in session.exec(
                select(Host).where(Host.approval_state == ApprovalState.approved)
                .order_by(Host.hostname)
            ).all()
        ],
    }


@app.post("/api/v1/agent-rollout", dependencies=[Depends(require_admin)])
def set_rollout(payload: RollConfig, session: Session = Depends(get_session)):
    if payload.autoroll is not None:
        _set(session, "agent_autoroll", "true" if payload.autoroll else "false")
    if payload.mode in ("staged", "all"):
        _set(session, "agent_roll_mode", payload.mode)
    if payload.pilot_host_id is not None:
        _set(session, "agent_pilot_host", str(payload.pilot_host_id or ""))
    session.commit()
    return get_rollout(session)


@app.post("/api/v1/agent-rollout/start", dependencies=[Depends(require_admin)])
def start_rollout(session: Session = Depends(get_session)):
    return start_agent_rollout(session, manual=True)


# ======================================================================
# Hosts
# ======================================================================
@app.get("/api/v1/hosts", response_model=list[HostRead],
         dependencies=[Depends(require_login)])
def list_hosts(session: Session = Depends(get_session)):
    # Zweites Kriterium noetig: ohne es stehen alle Hosts mit gleichem
    # sort_order - im Neuzustand also saemtliche - in beliebiger Folge,
    # die sich bei jeder Abfrage aendern kann.
    hosts = session.exec(
        select(Host).order_by(Host.sort_order, Host.hostname)
    ).all()
    cutoff = utcnow() - timedelta(seconds=AGENT_OFFLINE_AFTER)
    for h in hosts:
        if h.last_seen and h.last_seen < cutoff and h.status == HostStatus.online:
            h.status = HostStatus.offline
            session.add(h)
    session.commit()

    out = []
    for h in hosts:
        data = HostRead.model_validate(h)
        data.next_patch_run = next_patch_run(h)
        out.append(data)
    return out


@app.post("/api/v1/hosts/order", dependencies=[Depends(require_login)])
def set_host_order(payload: HostOrder, session: Session = Depends(get_session)):
    """
    Setzt die Anzeigereihenfolge. Erwartet die vollstaendige Liste der IDs
    in der gewuenschten Folge.

    Die Zaehlung beginnt bei 1: 0 ist der Vorgabewert eines noch nie
    einsortierten Hosts, und der soll nicht zufaellig auf dem ersten
    Platz landen.

    Unbekannte IDs werden uebergangen statt abgelehnt - die Liste im
    Browser kann aelter sein als der Bestand, etwa wenn nebenher ein Host
    entfernt wurde. Ein Fehlschlag wuerde die gesamte Sortierung
    verwerfen, obwohl der Rest gueltig ist.
    """
    known = {h.id: h for h in session.exec(select(Host)).all()}
    pos = 0
    for host_id in payload.ids:
        host = known.pop(host_id, None)
        if not host:
            continue
        pos += 1
        host.sort_order = pos
        session.add(host)

    # Nicht mitgeschickte Hosts hinten anhaengen, in stabiler Folge.
    # Sonst behalten sie ihren alten Wert und mischen sich unter die neu
    # vergebenen Plaetze.
    for host in sorted(known.values(), key=lambda h: (h.sort_order, h.hostname)):
        pos += 1
        host.sort_order = pos
        session.add(host)

    session.commit()
    return {"ok": True, "count": pos}


@app.patch("/api/v1/hosts/{host_id}", response_model=HostRead,
           dependencies=[Depends(require_admin)])
def update_host(
    host_id: int, payload: HostPatch, session: Session = Depends(get_session)
):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(host, field, value)
    if host.downtime_minutes < 1:
        host.downtime_minutes = 30
    # "and" statt "is not None" wertete bei genau 0 nicht aus - 0 ist in
    # Python selbst schon falsch, der Vergleich "< 1" kam nie zum Zug. Eine
    # Kulanzzeit von 0 Stunden blieb dadurch stehen, statt auf die Vorgabe
    # zurueckgesetzt zu werden. Gefunden bei derselben Zeile im Bereichs-Code.
    if host.patch_grace_hours is not None and host.patch_grace_hours < 1:
        host.patch_grace_hours = 4
    session.add(host)
    session.commit()
    session.refresh(host)
    data = HostRead.model_validate(host)
    data.next_patch_run = next_patch_run(host)
    return data


@app.delete("/api/v1/hosts/{host_id}")
def delete_host(host_id: int, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    for job in session.exec(select(Job).where(Job.host_id == host_id)).all():
        joblog.delete(job.id)
        session.delete(job)
    for pkg in session.exec(
        select(UpdatePackage).where(UpdatePackage.host_id == host_id)
    ).all():
        session.delete(pkg)
    name = host.hostname
    session.delete(host)
    audit(session, who.name, "host.delete", name, request)
    session.commit()
    return {"ok": True}


@app.post("/api/v1/hosts/{host_id}/reset-token",
          dependencies=[Depends(require_admin)])
def reset_agent_token(host_id: int, request: Request,
                      who: Principal = Depends(require_admin),
                      session: Session = Depends(get_session)):
    """
    Zieht das Agent-Token eines Hosts zurueck.

    Agent-Token gelten sonst unbegrenzt und lassen sich nicht erneuern -
    einmal abgeflossen, bleiben sie gueltig. Hier wird das Token verworfen;
    der Agent bekommt beim naechsten Kontakt 401 und meldet sich von selbst
    neu an. Zeitplan, Checkmk-Verknuepfung und Verlauf des Hosts bleiben
    dabei erhalten, das Loeschen und Neuanlegen wuerde sie verwerfen.

    Bis zur erneuten Anmeldung steht der Host offen: jedes Geraet im Netz
    kann sich unter diesem Namen melden. Deshalb landet er wieder in der
    Freigabe - wer es tatsaechlich war, entscheidet ein Mensch.
    """
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")

    host.agent_token_hash = None
    host.approval_state = ApprovalState.pending
    session.add(host)
    audit(session, who.name, "host.reset-token", host.hostname, request)
    session.commit()
    return {"ok": True, "hostname": host.hostname}


@app.post("/api/v1/hosts/{host_id}/approve", response_model=HostRead)
def approve_host(host_id: int, request: Request,
                 who: Principal = Depends(require_admin),
                 session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")

    # Nur beim Wechsel nach freigegeben pruefen. Ein bereits freigegebener
    # Host, der erneut freigegeben wird, darf nicht am Limit scheitern.
    if host.approval_state != ApprovalState.approved:
        check_host_limit(session)

    host.approval_state = ApprovalState.approved
    session.add(host)
    audit(session, who.name, "host.approve", host.hostname, request)
    session.commit()
    session.refresh(host)
    return host


@app.post("/api/v1/hosts/{host_id}/reject", response_model=HostRead)
def reject_host(host_id: int, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    host.approval_state = ApprovalState.rejected
    session.add(host)
    audit(session, who.name, "host.reject", host.hostname, request)
    session.commit()
    session.refresh(host)
    return host


def _area_conflicts(session: Session, area_id: int) -> list[str]:
    """
    Hostnamen im Bereich, die schon einen eigenen Update-Zeitplan haben.

    Nur ein Hinweis fuer die Anzeige beim Speichern - der Bereichs-Zeitplan
    laeuft unabhaengig neben einem etwaigen eigenen Zeitplan des Hosts her,
    beide koennen sich also ueberschneiden. Verhindert wird das bewusst
    nicht, nur sichtbar gemacht.
    """
    hosts = session.exec(
        select(Host).where(Host.area_id == area_id, Host.patch_enabled == True)  # noqa: E712
    ).all()
    return [h.display_name or h.hostname for h in hosts]


@app.get("/api/v1/areas", response_model=list[AreaRead],
         dependencies=[Depends(require_login)])
def list_areas(session: Session = Depends(get_session)):
    areas = session.exec(select(Area).order_by(Area.sort_order, Area.name)).all()
    counts: dict[int, int] = {}
    for area_id in session.exec(select(Host.area_id)).all():
        if area_id is not None:
            counts[area_id] = counts.get(area_id, 0) + 1
    out = []
    for a in areas:
        data = AreaRead.model_validate(a)
        data.host_count = counts.get(a.id, 0)
        out.append(data)
    return out


@app.post("/api/v1/areas", response_model=AreaRead)
def create_area(payload: AreaCreate, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name fehlt")
    # Neuer Bereich ans Ende, wie ein neu angemeldeter Host.
    last = session.exec(
        select(Area.sort_order).order_by(Area.sort_order.desc()).limit(1)
    ).first()
    area = Area(name=name, sort_order=(last or 0) + 1)
    session.add(area)
    audit(session, who.name, "area.create", name, request)
    session.commit()
    session.refresh(area)
    data = AreaRead.model_validate(area)
    data.host_count = 0
    return data


@app.patch("/api/v1/areas/{area_id}", response_model=AreaRead)
def update_area(area_id: int, payload: AreaPatch, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    area = session.get(Area, area_id)
    if not area:
        raise HTTPException(404, "Bereich nicht gefunden")
    if payload.name is not None and not payload.name.strip():
        raise HTTPException(400, "Name fehlt")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(area, field, value.strip() if field == "name" else value)
    if area.downtime_minutes < 1:
        area.downtime_minutes = 30
    if area.patch_grace_hours is not None and area.patch_grace_hours < 1:
        area.patch_grace_hours = 4
    session.add(area)
    audit(session, who.name, "area.update", area.name, request)
    session.commit()
    session.refresh(area)

    data = AreaRead.model_validate(area)
    data.host_count = len(session.exec(
        select(Host.id).where(Host.area_id == area.id)
    ).all())
    # Nur pruefen, wenn der Bereich selbst gerade einen Zeitplan hat -
    # sonst gibt es nichts, das sich mit dem Host-eigenen ueberschneiden
    # koennte.
    if area.patch_enabled:
        data.patch_conflicts = _area_conflicts(session, area.id)
    return data


@app.delete("/api/v1/areas/{area_id}")
def delete_area(area_id: int, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    area = session.get(Area, area_id)
    if not area:
        raise HTTPException(404, "Bereich nicht gefunden")
    in_use = session.exec(
        select(Host.id).where(Host.area_id == area_id)
    ).first()
    if in_use:
        raise HTTPException(409, "Bereich enthaelt noch Hosts - erst herausziehen")
    name = area.name
    session.delete(area)
    audit(session, who.name, "area.delete", name, request)
    session.commit()
    return {"ok": True}


@app.post("/api/v1/areas/order", dependencies=[Depends(require_admin)])
def set_area_order(payload: AreaOrder, session: Session = Depends(get_session)):
    known = {a.id: a for a in session.exec(select(Area)).all()}
    pos = 0
    for area_id in payload.ids:
        area = known.pop(area_id, None)
        if not area:
            continue
        pos += 1
        area.sort_order = pos
        session.add(area)
    for area in sorted(known.values(), key=lambda a: (a.sort_order, a.name)):
        pos += 1
        area.sort_order = pos
        session.add(area)
    session.commit()
    return {"ok": True, "count": pos}


@app.post("/api/v1/hosts/{host_id}/area", response_model=HostRead)
def set_host_area(host_id: int, payload: HostArea, request: Request,
                  who: Principal = Depends(require_admin),
                  session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    if payload.area_id is not None and not session.get(Area, payload.area_id):
        raise HTTPException(404, "Bereich nicht gefunden")
    host.area_id = payload.area_id
    session.add(host)
    audit(session, who.name, "host.area",
          f"{host.hostname} -> {payload.area_id if payload.area_id else 'ohne Bereich'}",
          request)
    session.commit()
    session.refresh(host)
    data = HostRead.model_validate(host)
    data.next_patch_run = next_patch_run(host)
    return data


@app.get("/api/v1/hosts/{host_id}/updates", response_model=list[UpdateRead],
         dependencies=[Depends(require_login)])
def host_updates(host_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(UpdatePackage).where(UpdatePackage.host_id == host_id)
    ).all()


# ======================================================================
# Jobs
# ======================================================================
@app.post("/api/v1/hosts/{host_id}/jobs", response_model=JobRead,
          dependencies=[Depends(require_login)])
def create_job(
    host_id: int, payload: JobCreate, session: Session = Depends(get_session)
):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    if host.approval_state != ApprovalState.approved:
        raise HTTPException(400, "Host ist nicht freigegeben")

    params = dict(payload.params)

    # Manueller oder geplanter Neustart: Richtlinie und Wartungsfenster
    # werden umgangen, die Downtime nur auf ausdruecklichen Wunsch.
    if payload.job_type == JobType.reboot:
        params.setdefault("manual", True)
        params.setdefault("downtime_minutes", host.downtime_minutes)
        if not payload.set_downtime:
            params["skip_downtime"] = True
        if not has_downtime_target(host):
            params.setdefault("allow_without_downtime", True)

    # Patch-Auftrag mit ausdruecklichem Neustartwunsch. 'manual' uebergeht
    # Richtlinie und Wartungsfenster - beides waere hier falsch, weil der
    # Zeitpunkt bewusst gewaehlt wurde.
    if payload.job_type == JobType.patch and payload.reboot_after:
        params["reboot_if_needed"] = True
        params.setdefault("manual", True)
        params.setdefault("downtime_minutes", host.downtime_minutes)
        if not payload.set_downtime:
            params["skip_downtime"] = True
        if not has_downtime_target(host):
            params.setdefault("allow_without_downtime", True)

    scheduled = payload.scheduled_at
    if scheduled is not None:
        # Mit Zeitzone weiterrechnen, wie ueberall sonst. Frueher wurde sie
        # hier abgestreift; der Vergleich mit utcnow() warf dann einen
        # TypeError und jeder eingeplante Neustart endete in einem 500.
        # Das Frontend sendet toISOString(), also immer mit Zeitzone -
        # der Fehler trat damit bei jedem Versuch auf.
        scheduled = ensure_utc(scheduled)
        if scheduled <= utcnow() - timedelta(minutes=1):
            raise HTTPException(400, "Der Zeitpunkt liegt in der Vergangenheit")

    if payload.downtime_minutes is not None:
        params["downtime_minutes"] = max(1, payload.downtime_minutes)

    # Verfallszeitpunkt nur fuer Neustarts. Ein Scan oder Patch schadet auch
    # spaeter nicht, ein Neustart im laufenden Betrieb schon.
    expires = None
    if payload.job_type == JobType.reboot:
        grace = payload.grace_minutes
        if grace is None:
            grace = 120 if scheduled else 10
        grace = max(1, min(grace, 7 * 24 * 60))
        base = scheduled or utcnow()
        expires = base + timedelta(minutes=grace)
        params["grace_minutes"] = grace

    job = Job(
        host_id=host_id,
        job_type=payload.job_type,
        params=params,
        scheduled_at=scheduled,
        expires_at=expires,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@app.get("/api/v1/jobs/{job_id}/log", dependencies=[Depends(require_login)])
def job_log(
    job_id: int,
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    """
    Liest das Auftragsprotokoll ab 'offset'. Die Oberflaeche fragt im
    Sekundentakt und haengt nur das Neue an.
    """
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Auftrag nicht gefunden")
    data = joblog.read(job_id, offset)
    return {
        **data,
        "state": job.state.value,
        "job_type": job.job_type.value,
        "progress": (job.params or {}).get("progress"),
        "running": job.state == JobState.running,
        "error": job.error,
        "started_at": ensure_utc(job.started_at),
        "finished_at": ensure_utc(job.finished_at),
    }


@app.get("/api/v1/jobs/active", dependencies=[Depends(require_login)])
def active_jobs(session: Session = Depends(get_session)):
    """Laufende Auftraege, damit die Uebersicht sie kennzeichnen kann."""
    jobs = session.exec(
        select(Job).where(Job.state == JobState.running).order_by(Job.started_at)
    ).all()
    return [
        {
            "id": j.id,
            "host_id": j.host_id,
            "job_type": j.job_type.value,
            "progress": (j.params or {}).get("progress"),
            "started_at": ensure_utc(j.started_at),
        }
        for j in jobs
    ]


@app.get("/api/v1/jobs/last", dependencies=[Depends(require_login)])
def last_jobs(session: Session = Depends(get_session)):
    """
    Der zuletzt abgeschlossene Auftrag je Host, fuer die dritte Zeile in der
    Uebersicht ("Letzte Pruefung/Letztes Update/Letzter Neustart um ...").

    Nur done/failed zaehlen als tatsaechliche Aktion - ein abgebrochener
    (cancelled) Auftrag ist keine. finished_at fehlt in einem seltenen Pfad
    (eine Selbstaktualisierung, die per Notiz nachtraeglich als
    fehlgeschlagen markiert wird, ohne den Zeitstempel zu setzen) - dann
    created_at als Behelf, sonst faellt der Auftrag beim Sortieren ans Ende
    und wuerde nie als der juengste erkannt.
    """
    jobs = session.exec(
        select(Job).where(Job.state.in_([JobState.done, JobState.failed]))
    ).all()
    jobs.sort(key=lambda j: j.finished_at or j.created_at, reverse=True)
    gesehen = set()
    ergebnis = []
    for j in jobs:
        if j.host_id in gesehen:
            continue
        gesehen.add(j.host_id)
        ergebnis.append({
            "host_id": j.host_id,
            "job_type": j.job_type.value,
            "state": j.state.value,
            "finished_at": ensure_utc(j.finished_at or j.created_at),
        })
    return ergebnis


@app.delete("/api/v1/jobs/{job_id}", dependencies=[Depends(require_login)])
def cancel_job(job_id: int, session: Session = Depends(get_session)):
    """Bricht einen wartenden Auftrag ab. Laufende bleiben unberuehrt."""
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if job.state != JobState.pending:
        raise HTTPException(
            400,
            f"Auftrag ist im Zustand '{job.state.value}' und laesst sich nicht "
            f"mehr abbrechen.",
        )
    job.state = JobState.cancelled
    job.finished_at = utcnow()
    session.add(job)
    session.commit()
    return {"ok": True}


@app.get("/api/v1/jobs", response_model=list[JobRead],
         dependencies=[Depends(require_login)])
def list_jobs(
    host_id: Optional[int] = None,
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_session),
):
    # Abgelaufene Auftraege auch dann kennzeichnen, wenn sich der Host gar
    # nicht mehr meldet - sonst stuenden sie ewig als "wartet" in der Liste.
    now = utcnow()
    stale = session.exec(
        select(Job).where(
            Job.state == JobState.pending,
            Job.expires_at != None,          # noqa: E711
            Job.expires_at <= now,
        )
    ).all()
    for job in stale:
        job.state = JobState.cancelled
        job.finished_at = now
        grace = (job.params or {}).get("grace_minutes")
        when = job.scheduled_at or job.created_at
        job.error = (
            f"Nicht ausgefuehrt: Nachlauf abgelaufen. Geplant war "
            f"{when:%d.%m.%Y %H:%M} UTC, Nachlauf {grace} Minuten. "
            f"Der Host hat sich in diesem Zeitraum nicht gemeldet."
        )
        session.add(job)
    if stale:
        session.commit()

    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if host_id:
        stmt = stmt.where(Job.host_id == host_id)
    return session.exec(stmt).all()


# ======================================================================
# Checkmk
# ======================================================================
@app.post("/api/v1/checkmk/config")
async def set_checkmk_config(
    payload: CheckmkConfig, request: Request,
    who: Principal = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if not is_configured():
        raise HTTPException(
            500,
            "CO37_SECRET_KEY ist nicht gesetzt. Das Automation-Secret "
            "wird nicht im Klartext gespeichert.",
        )

    secret = payload.secret
    if not secret:
        secret = decrypt(_setting(session, "cmk_secret"))
        if not secret:
            raise HTTPException(400, "Kein Automation-Secret hinterlegt")

    probe = CheckmkClient(
        payload.url, payload.site, payload.user, secret, payload.verify_ssl
    )
    try:
        info = await probe.test_connection()
    except CheckmkError as exc:
        raise HTTPException(400, f"Verbindung fehlgeschlagen: {exc}")

    try:
        stored = encrypt(secret)
    except CryptoNotConfigured as exc:
        raise HTTPException(500, str(exc))

    for key, value in {
        "cmk_url": payload.url,
        "cmk_site": payload.site,
        "cmk_user": payload.user,
        "cmk_secret": stored,
        "cmk_verify_ssl": "true" if payload.verify_ssl else "false",
    }.items():
        row = session.get(Setting, key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
        session.add(row)
    audit(session, who.name, "checkmk.config",
          f"{payload.url} Site {payload.site} als {payload.user}", request)
    session.commit()
    return info


@app.get("/api/v1/checkmk/status", dependencies=[Depends(require_admin)])
async def checkmk_status(session: Session = Depends(get_session)):
    """
    Zustand und gespeicherte Zugangsdaten - ohne das Secret. Damit kann die
    Oberflaeche die Felder vorbelegen, statt leer zu erscheinen.
    """
    stored = {
        "url": _setting(session, "cmk_url"),
        "site": _setting(session, "cmk_site"),
        "user": _setting(session, "cmk_user"),
        "verify_ssl": _setting(session, "cmk_verify_ssl", "true") == "true",
        # Nur ob eines hinterlegt ist, nie der Wert selbst
        "secret_set": bool(_setting(session, "cmk_secret")),
    }

    if not is_configured():
        return {"configured": False, "key_missing": True, "stored": stored}
    cmk = get_checkmk(session)
    if not cmk:
        return {"configured": False, "stored": stored}
    try:
        return {"configured": True, "stored": stored, **await cmk.test_connection()}
    except CheckmkError as exc:
        return {"configured": True, "ok": False, "error": str(exc), "stored": stored}


@app.get("/api/v1/checkmk/hosts", dependencies=[Depends(require_admin)])
async def checkmk_hosts(session: Session = Depends(get_session)):
    cmk = get_checkmk(session)
    if not cmk:
        raise HTTPException(400, "Checkmk nicht konfiguriert")
    try:
        return await cmk.list_hosts()
    except CheckmkError as exc:
        raise HTTPException(502, str(exc))


# ----------------------------------------------------------------------
# Downtime von Hand
# ----------------------------------------------------------------------
@app.get("/api/v1/hosts/{host_id}/downtime", dependencies=[Depends(require_login)])
async def get_downtimes(host_id: int, session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    if not has_downtime_target(host):
        return {"downtimes": [], "reason": "Keine Checkmk-Hosts verknuepft"}
    cmk = get_checkmk(session)
    if not cmk:
        return {"downtimes": [], "reason": "Checkmk nicht konfiguriert"}
    # Bewusst nur die verknuepften Hosts abfragen, auch bei gesetztem Flag:
    # eine Abfrage pro Host waere sonst ein Aufruf je Checkmk-Host, nur um
    # eine Anzeige zu fuellen.
    out = []
    for name in (host.checkmk_hosts or []):
        try:
            out.extend(await cmk.list_downtimes(host_name=name))
        except CheckmkError:
            continue
    return {"downtimes": out}


@app.post("/api/v1/hosts/{host_id}/downtime", dependencies=[Depends(require_login)])
async def set_downtime(
    host_id: int, payload: DowntimeRequest, session: Session = Depends(get_session)
):
    """
    Setzt eine Downtime auf allen verknuepften Checkmk-Hosts.
    Entweder 'minutes' ab jetzt oder 'start'/'end' als Zeitspanne.
    """
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    if not has_downtime_target(host):
        raise HTTPException(400, "Keine Checkmk-Hosts verknuepft")
    cmk = get_checkmk(session)
    if not cmk:
        raise HTTPException(400, "Checkmk nicht konfiguriert")

    if payload.start and payload.end:
        if payload.end <= payload.start:
            raise HTTPException(400, "Ende liegt vor dem Beginn")
        minutes = int((payload.end - payload.start).total_seconds() // 60)
        if minutes < 1:
            raise HTTPException(400, "Zeitspanne ist kuerzer als eine Minute")
    else:
        minutes = payload.minutes or host.downtime_minutes
        if minutes < 1:
            raise HTTPException(400, "Dauer muss mindestens eine Minute betragen")

    try:
        targets = await downtime_targets(host, cmk)
    except CheckmkError as exc:
        raise HTTPException(502, f"Checkmk-Hosts nicht abrufbar: {exc}")
    if not targets:
        raise HTTPException(400, "Kein Checkmk-Host ermittelt")

    res = await cmk.set_downtime_multi(
        targets, minutes=minutes, comment=payload.comment
    )
    if res["failed"] and not res["ok"]:
        raise HTTPException(502, f"Downtime fehlgeschlagen: {res['failed']}")
    return res


@app.delete("/api/v1/hosts/{host_id}/downtime", dependencies=[Depends(require_login)])
async def clear_downtime(host_id: int, session: Session = Depends(get_session)):
    host = session.get(Host, host_id)
    if not host:
        raise HTTPException(404, "Host nicht gefunden")
    if not has_downtime_target(host):
        raise HTTPException(400, "Keine Checkmk-Hosts verknuepft")
    cmk = get_checkmk(session)
    if not cmk:
        raise HTTPException(400, "Checkmk nicht konfiguriert")
    try:
        targets = await downtime_targets(host, cmk)
    except CheckmkError as exc:
        raise HTTPException(502, f"Checkmk-Hosts nicht abrufbar: {exc}")
    return await cmk.remove_downtime_multi(targets)


# ======================================================================
# Agent-Pakete
# ======================================================================
@app.get("/api/v1/packages", dependencies=[Depends(require_admin)])
def list_packages():
    """
    Zeigt je ein Linux- und ein Windows-Paket: das jeweils zuletzt gebaute.

    Es wird bewusst nichts geloescht. Eine frueherer Entwurf entfernte alle
    Pakete, deren Version nicht zur ausgelieferten agent.py passte. Weichen
    Benennung und erwartete Version aus irgendeinem Grund voneinander ab -
    etwa weil build_packages.sh aus einer aelteren Fassung stammt -, wurden
    damit frisch gebaute Pakete geloescht. Aufraeumen ist Sache von
    build_packages.sh, das seine eigenen Altbestaende entfernt.

    Passt die Version eines Pakets nicht zur agent.py, wird das gemeldet
    statt stillschweigend behoben.
    """
    want = agent_source_version()
    result = {"agent_version": want, "linux": None, "windows": None, "mismatch": []}

    if not PKG_DIR.is_dir():
        return result

    def info(f):
        stem = f.stem.replace("_all", "")
        ver = stem.replace("co37-agent", "").strip("-_")
        return {
            "name": f.name,
            "version": ver,
            "matches": ver == want,
            "size": f.stat().st_size,
            "built_at": ensure_utc(from_timestamp(f.stat().st_mtime)),
        }

    for suffix, key in ((".deb", "linux"), (".msi", "windows")):
        found = [f for f in PKG_DIR.glob(f"co37-agent*{suffix}") if f.is_file()]
        if not found:
            continue
        # Das zuletzt gebaute gewinnt - unabhaengig von der Benennung
        newest = max(found, key=lambda f: f.stat().st_mtime)
        data = info(newest)
        result[key] = data
        if not data["matches"]:
            result["mismatch"].append(
                f"{data['name']} traegt Version {data['version']}, "
                f"die ausgelieferte agent.py meldet {want}. "
                f"Vermutlich stammt build_packages.sh aus einer aelteren Fassung."
            )
        # Aeltere Dateien nur melden, nicht anfassen
        older = [f.name for f in found if f != newest]
        if older:
            result.setdefault("older", []).extend(older)

    return result


BUILD_REQUEST = DATA_DIR / "update" / "build_request.json"
BUILD_STATUS = DATA_DIR / "update" / "build_status.json"


# Muss VOR der Route /api/v1/packages/{name} stehen. Sonst faengt deren
# Pfadmuster diesen Aufruf ab und liefert 405, weil sie nur GET erlaubt.
@app.post("/api/v1/packages/build", dependencies=[Depends(require_admin)])
def request_package_build():
    """
    Fordert einen Neubau der Agent-Pakete an.

    Ausgefuehrt wird er vom Watcher als root - das Backend laeuft
    unprivilegiert und darf weder in sein Programmverzeichnis schreiben noch
    Fremdprogramme starten. Gleiche Trennung wie beim Systemupdate.
    """
    import json as _json

    if BUILD_REQUEST.exists():
        raise HTTPException(409, "Es laeuft bereits ein Paketbau.")

    BUILD_REQUEST.parent.mkdir(parents=True, exist_ok=True)
    BUILD_REQUEST.write_text(_json.dumps({
        "requested_at": utcnow().isoformat(),
    }))
    BUILD_STATUS.write_text(_json.dumps({
        "state": "requested",
        "log": ["Angefordert, warte auf Verarbeitung durch den Host..."],
    }))
    return {"ok": True, "state": "requested"}


@app.get("/api/v1/packages/build-status", dependencies=[Depends(require_admin)])
def package_build_status():
    import json as _json
    if not BUILD_STATUS.exists():
        return {"state": "idle", "log": []}
    try:
        return _json.loads(BUILD_STATUS.read_text())
    except Exception:  # noqa: BLE001
        return {"state": "idle", "log": []}


@app.get("/api/v1/packages/{name}")
def download_package(name: str, request: Request,
                     x_install_token: str = Header(default=""),
                     x_session: str = Header(default=""),
                     session: Session = Depends(get_session)):
    """
    Einzige Route, die ein Installations-Token akzeptiert.

    Ein frisch aufgesetzter Host kann sich nicht anmelden - er holt sein
    Paket mit einem Token ab, das ein Administrator kurz vorher erzeugt
    hat. Das Token laeuft nach Minuten ab und gilt fuer wenige Abrufe.

    Angemeldete Administratoren kommen weiterhin ohne Token durch, damit
    die Oberflaeche die Pakete anzeigen kann.
    """
    if not consume_install_token(x_install_token, session, request):
        # Kein gueltiges Token: dann muss es eine Anmeldung sein.
        require_admin(authenticate(x_session, session, request))

    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "Ungueltiger Name")
    path = PKG_DIR / name
    if not path.is_file():
        raise HTTPException(404, "Paket nicht vorhanden. Mit build_packages.sh erzeugen.")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.get("/api/v1/install-script", response_class=PlainTextResponse)
def install_script(request: Request, who: Principal = Depends(require_admin),
                   session: Session = Depends(get_session)):
    """
    Ein Shell-Skript, das den Agent auf einem Linux-Host einrichtet.

    Jeder Abruf erzeugt ein eigenes, kurzlebiges Installations-Token. Das
    Skript wird auf einem fremden Rechner ausgefuehrt und bleibt dort in
    der Verlaufsdatei stehen - was zurueckbleibt, soll nach Minuten
    wertlos sein.
    """
    base = public_base_url(request, session)
    key, _row = new_install_token(session, who.name)
    audit(session, who.name, "install-token.create", "ueber install-script", request)
    session.commit()
    return f"""#!/bin/sh
# CO-37 Agent - Einrichtung
set -e

if [ "$(id -u)" -ne 0 ]; then echo "Bitte als root ausfuehren."; exit 1; fi

SERVER="{base}"
TMP=$(mktemp -d)
cd "$TMP"

echo ">>> Lade Paket"
curl -fsSL "$SERVER/api/v1/packages/co37-agent_{update_manager.get_current_version()}_all.deb" \\
     -o agent.deb -H "X-Install-Token: {key}" || {{
  echo "Download fehlgeschlagen. Ist das Paket gebaut? (bash build_packages.sh)"
  exit 1
}}

echo ">>> Installiere"
CO37_SERVER="$SERVER" apt-get install -y ./agent.deb

cd / && rm -rf "$TMP"
echo
echo "Fertig. Der Host erscheint im Dashboard und wartet auf Freigabe."
"""


# ======================================================================
# Selbstpruefung
# ======================================================================
BASE_DIR = Path(__file__).resolve().parent.parent


def _read(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:  # noqa: BLE001
        return ""


@app.get("/api/v1/diagnostics", dependencies=[Depends(require_admin)])
def diagnostics():
    """
    Prueft, ob die Teile zusammenpassen.

    Hintergrund: Backend, Oberflaeche, Watcher, Bauskript und die
    Agent-Pakete werden zu unterschiedlichen Zeitpunkten ausgetauscht.
    Laufen sie auseinander, aeussert sich das in verwirrenden Symptomen -
    veraltete Pakete, alte Oberflaeche, ausbleibende Neubauten. Diese
    Pruefung nennt die Ursache statt der Wirkung.
    """
    version = update_manager.get_current_version()
    checks = []

    def add(name, ok, detail, fix=None):
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    # Oberflaeche
    fe = _read(BASE_DIR / "frontend" / "index.html", 2000)
    m = re.search(r"CO37_FRONTEND_VERSION:\s*([\w.]+)", fe)
    fe_ver = m.group(1) if m else None
    add("Oberflaeche", fe_ver == version,
        f"Oberflaeche {fe_ver or 'ohne Kennung'}, Backend {version}",
        None if fe_ver == version else
        "Die Oberflaeche wurde nicht mit ausgetauscht. Watcher-Protokoll pruefen: "
        "journalctl -u co37-watcher -n 40")

    # Watcher
    wt = _read(BASE_DIR / "update_watcher.py", 6000)
    wt_ok = "MANAGED_FILES" in wt
    add("Update-Watcher", wt_ok,
        "tauscht auch Dateien auf oberster Ebene aus" if wt_ok
        else "alte Fassung - tauscht nur backend/, frontend/ und agent/ aus",
        None if wt_ok else
        "Einmalig von Hand ersetzen: systemctl stop co37-watcher && "
        "unzip -o -j <paket>.zip update_watcher.py build_packages.sh "
        "build_release.sh setup.sh -d /opt/co37 && "
        "systemctl start co37-watcher")

    # Bauskript
    bp = _read(BASE_DIR / "build_packages.sh", 3000)
    bp_ok = "AGENT_VERSION" in bp
    add("Paket-Bauskript", bp_ok,
        "benennt Pakete nach der Agent-Version" if bp_ok
        else "alte Fassung - benennt Pakete nach backend/VERSION",
        None if bp_ok else "Wird mit dem Watcher zusammen ersetzt, siehe oben.")

    # wixl
    wixl = shutil.which("wixl") is not None
    add("wixl (Windows-Pakete)", wixl,
        "vorhanden" if wixl else "nicht installiert - es entsteht kein .msi",
        None if wixl else "apt install -y wixl")

    # Pakete
    want = agent_source_version()
    pkgs = sorted(PKG_DIR.glob("co37-agent*")) if PKG_DIR.is_dir() else []
    names = [p.name for p in pkgs]
    has_deb = any(f"_{want}_" in n for n in names)
    has_msi = any(n.endswith(".msi") and want in n for n in names)
    stale = [n for n in names if want not in n]
    add("Linux-Paket", has_deb,
        f"vorhanden fuer {want}" if has_deb else f"kein Paket fuer Agent {want}",
        None if has_deb else "cd /opt/co37 && bash build_packages.sh")
    add("Windows-Paket", has_msi,
        f"vorhanden fuer {want}" if has_msi
        else ("kein Paket - wixl fehlt" if not wixl else f"kein Paket fuer Agent {want}"),
        None if has_msi else
        ("apt install -y wixl, danach: bash build_packages.sh" if not wixl
         else "cd /opt/co37 && bash build_packages.sh"))
    add("Keine veralteten Pakete", not stale,
        "aufgeraeumt" if not stale else f"veraltet: {', '.join(stale)}",
        None if not stale else "cd /opt/co37 && bash build_packages.sh")

    # Schluessel
    add("Verschluesselungsschluessel", is_configured(),
        "gesetzt" if is_configured() else "CO37_SECRET_KEY fehlt",
        None if is_configured() else
        "openssl rand -base64 48, in die systemd-Unit eintragen")

    return {
        "version": version,
        "agent_version": want,
        "frontend_version": fe_ver,
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
    }


# ======================================================================
# Health
# ======================================================================
def watcher_info() -> dict:
    """
    Liest die vom Watcher hinterlegte Kennung.

    Fehlt die Datei, laeuft eine Fassung vor 0.8.4 - oder gar keiner. Das ist
    kein Randfall: ein Watcher vor 0.4.3 tauscht nur backend/, frontend/ und
    agent/ aus. Er erneuert weder die Hilfsskripte noch sich selbst und kann
    daher aus eigener Kraft nie aktuell werden. Ohne diese Anzeige bleibt das
    unbemerkt, waehrend Korrekturen scheinbar wirkungslos verpuffen.
    """
    import json as _json
    path = DATA_DIR / "update" / "watcher.json"
    if not path.exists():
        return {
            "version": None,
            "ok": False,
            "hint": "Der Watcher meldet sich nicht. Entweder laeuft er nicht, "
                    "oder er stammt aus einer Fassung vor 0.8.4. Ein Watcher "
                    "vor 0.4.3 kann sich nicht selbst erneuern und muss "
                    "einmalig von Hand ersetzt werden.",
        }
    try:
        data = _json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {"version": None, "ok": False, "hint": "watcher.json nicht lesbar"}

    expected = update_manager.get_current_version()
    data["ok"] = True
    data["current"] = data.get("version") == expected
    if not data["current"]:
        data["hint"] = (
            f"Der Watcher meldet {data.get('version')}, das System steht auf "
            f"{expected}. Beim naechsten Update wird er mit erneuert."
        )
    return data


@app.get("/api/v1/watcher", dependencies=[Depends(require_admin)])
def watcher_status():
    return watcher_info()


# ======================================================================
# Anmeldung
# ======================================================================
class LoginIn(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.user


class PasswordReset(BaseModel):
    new_password: str


MIN_PASSWORD_LEN = 6


def _check_password(pw: str):
    if len(pw) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Passwort muss mindestens "
                                 f"{MIN_PASSWORD_LEN} Zeichen haben")


@app.post("/api/v1/login")
def login(payload: LoginIn, request: Request,
          session: Session = Depends(get_session)):
    ip = client_ip(request)

    # Ablehnen statt hinhalten. Naheliegend waere, eine fehlgeschlagene
    # Anmeldung ein paar Sekunden zu verzoegern - das ist hier schaedlich:
    # diese Route ist synchron, ein sleep blockiert einen Arbeiter aus dem
    # Threadpool. Mit ein paar Dutzend offenen Anfragen laege das Backend
    # lahm.
    wait = login_retry_after(ip)
    if wait is not None:
        raise HTTPException(
            429,
            f"Zu viele Fehlversuche von dieser Adresse. Erneut moeglich in "
            f"{wait // 60 + 1} Minuten.",
            headers={"Retry-After": str(wait)},
        )

    user = session.exec(
        select(User).where(User.username == payload.username)
    ).first()

    # Auch bei unbekanntem Benutzer das Passwort pruefen, damit sich aus
    # der Antwortzeit nicht ablesen laesst, welche Namen es gibt.
    stored = user.password_hash if user else hash_password("x")
    ok = verify_password(payload.password, stored)

    if not user or not ok or user.disabled:
        fails = note_login_failure(ip)
        audit(session, payload.username or "?", "login.failed",
              "Benutzer unbekannt, gesperrt oder Passwort falsch", request)
        # Nur beim Ueberschreiten einmal vermerken, nicht bei jedem
        # weiteren abgewiesenen Versuch - sonst schreibt genau der Vorgang
        # ins Protokoll, den die Drosselung eindaemmen soll.
        if fails == LOGIN_MAX_FAILS:
            audit(session, payload.username or "?", "login.throttled",
                  f"{fails} Fehlversuche - Adresse fuer "
                  f"{int(LOGIN_WINDOW.total_seconds() // 60)} Minuten gesperrt",
                  request)
        session.commit()
        raise HTTPException(401, "Benutzername oder Passwort falsch")

    note_login_success(ip)

    # Der Zwang gilt als bestaetigt, sobald sich jemand ueber HTTPS
    # angemeldet hat. Damit entfaellt der Rueckfall.
    if _PROXY_CFG["https_only"] and _PROXY_CFG["deadline"] \
            and request_is_https(request):
        set_setting(session, SET_HTTPS_DEADLINE, "")
        audit(session, user.username, "https-only.confirmed", None, request)
        session.commit()
        load_proxy_config(session)

    token = secrets.token_urlsafe(32)
    session.add(LoginSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=utcnow() + timedelta(hours=SESSION_HOURS),
        from_ip=client_ip(request),
    ))
    user.last_login = utcnow()
    session.add(user)
    audit(session, user.username, "login.ok", None, request)
    session.commit()

    body = {
        # Weiterhin mitgeliefert: aeltere Oberflaechen und Skripte nutzen
        # die Kopfzeile. Die aktuelle Oberflaeche braucht den Wert nicht
        # mehr - sie verlaesst sich auf das Cookie.
        "session": token,
        "username": user.username,
        "role": user.role.value,
        "must_change_password": user.must_change_password,
        "expires_in_hours": SESSION_HOURS,
    }
    resp = JSONResponse(content=jsonable_encoder(body))
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        # Strict statt Lax: der Browser schickt das Cookie bei Aufrufen von
        # fremden Seiten gar nicht erst mit. Damit ist CSRF erledigt, ohne
        # dass es dafuer ein eigenes Verfahren braucht.
        samesite="strict",
        # Nur wenn der Zugang ohnehin auf HTTPS beschraenkt ist. Sonst
        # naehme der Browser das Cookie ueber den unverschluesselten
        # Zugang nicht an - und niemand koennte sich mehr anmelden.
        secure=_PROXY_CFG["https_only"],
        path="/",
    )
    return resp


@app.post("/api/v1/logout")
def logout(request: Request, x_session: str = Header(default=""),
           session: Session = Depends(get_session)):
    row = _session_by_token(session_token(request, x_session), session)
    if row:
        user = session.get(User, row.user_id)
        session.delete(row)
        audit(session, user.username if user else "?", "logout", None, request)
        session.commit()
    resp = JSONResponse(content={"ok": True})
    # Auch loeschen, wenn die Sitzung schon abgelaufen war - sonst bleibt
    # ein totes Cookie im Browser stehen.
    # Gleiche Merkmale wie beim Setzen. Browser vergleichen zwar nur Name,
    # Domain und Pfad, aber zwei Stellen mit unterschiedlichen Angaben sind
    # eine Einladung fuer spaetere Verwirrung.
    resp.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="strict",
        secure=_PROXY_CFG["https_only"],
    )
    return resp


@app.get("/api/v1/me")
def whoami(who: Principal = Depends(require_login)):
    return {
        "username": who.name,
        "role": who.role.value,
        "is_admin": who.is_admin,
        "must_change_password": bool(
            who.user.must_change_password if who.user else False),
        # None heisst "nicht gewaehlt". Die Oberflaeche faellt dann auf die
        # Vorgabe der Installation zurueck, nicht auf einen Wert von hier -
        # sonst waere die Vorgabe fuer bestehende Konten wirkungslos.
        "language": (who.user.language if who.user else None),
    }


class SpracheWahl(BaseModel):
    language: str


@app.post("/api/v1/me/language")
def set_own_language(payload: SpracheWahl,
                     who: Principal = Depends(require_login),
                     session: Session = Depends(get_session)):
    """
    Speichert die Sprache am eigenen Konto. Keine Rolle noetig - das ist
    eine Anzeigeeinstellung, kein Eingriff.

    Der API-Key hat kein Konto und damit nichts zu speichern. Das ist kein
    Fehlerfall, den jemand beheben muesste: die Oberflaeche merkt sich die
    Wahl dann nur im Cookie.
    """
    code = pruefe_sprache(payload.language)
    if not code:
        raise HTTPException(400, f"Unbekannte Sprache: {payload.language}")
    if not who.user:
        return {"language": code, "gespeichert": False}
    who.user.language = code
    session.add(who.user)
    session.commit()
    return {"language": code, "gespeichert": True}


@app.post("/api/v1/settings/language", dependencies=[Depends(require_admin)])
def set_default_language(payload: SpracheWahl,
                         session: Session = Depends(get_session)):
    """Vorgabesprache der Installation. Nur Administratoren."""
    code = pruefe_sprache(payload.language)
    if not code:
        raise HTTPException(400, f"Unbekannte Sprache: {payload.language}")
    set_setting(session, SET_LANGUAGE, code)
    session.commit()
    return {"language": code}


@app.post("/api/v1/me/password")
def change_own_password(payload: PasswordChange, request: Request,
                        who: Principal = Depends(require_login),
                        session: Session = Depends(get_session)):
    """Jeder aendert sein eigenes Passwort - dafuer braucht es keine Rolle."""
    if not who.user:
        raise HTTPException(400, "Der API-Key hat kein Passwort")

    user = session.get(User, who.user.id)
    if not verify_password(payload.old_password, user.password_hash):
        audit(session, user.username, "password.change.failed",
              "altes Passwort falsch", request)
        session.commit()
        raise HTTPException(401, "Altes Passwort falsch")

    _check_password(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    session.add(user)

    # Alle anderen Sitzungen dieses Benutzers verwerfen. Wer das Passwort
    # aendert, will in der Regel genau das erreichen.
    for row in session.exec(
        select(LoginSession).where(LoginSession.user_id == user.id)
    ).all():
        session.delete(row)

    audit(session, user.username, "password.change", None, request)
    session.commit()
    return {"ok": True, "relogin": True}


# ======================================================================
# Benutzerverwaltung - nur fuer Administratoren
# ======================================================================
@app.get("/api/v1/users")
def list_users(who: Principal = Depends(require_admin),
               session: Session = Depends(get_session)):
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role.value,
            "disabled": u.disabled,
            "created_at": u.created_at,
            "last_login": u.last_login,
        }
        for u in session.exec(select(User).order_by(User.username)).all()
    ]


@app.post("/api/v1/users")
def create_user(payload: UserCreate, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    name = payload.username.strip()
    if not name:
        raise HTTPException(400, "Benutzername fehlt")
    if session.exec(select(User).where(User.username == name)).first():
        raise HTTPException(409, "Benutzername ist bereits vergeben")
    _check_password(payload.password)

    user = User(username=name, role=payload.role,
                password_hash=hash_password(payload.password))
    session.add(user)
    audit(session, who.name, "user.create",
          f"{name} als {payload.role.value}", request)
    session.commit()
    session.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role.value}


@app.post("/api/v1/users/{user_id}/password")
def reset_password(user_id: int, payload: PasswordReset, request: Request,
                   who: Principal = Depends(require_admin),
                   session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden")
    _check_password(payload.new_password)

    user.password_hash = hash_password(payload.new_password)
    session.add(user)
    # Laufende Sitzungen beenden - sonst bleibt der alte Zugang bestehen
    for row in session.exec(
        select(LoginSession).where(LoginSession.user_id == user.id)
    ).all():
        session.delete(row)

    audit(session, who.name, "user.password.reset", user.username, request)
    session.commit()
    return {"ok": True}


@app.delete("/api/v1/users/{user_id}")
def delete_user(user_id: int, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if who.user and who.user.id == user.id:
        raise HTTPException(400, "Der eigene Zugang kann nicht entfernt werden")

    admins = session.exec(select(User).where(User.role == Role.admin)).all()
    if user.role == Role.admin and len(admins) <= 1:
        raise HTTPException(400, "Der letzte Administrator kann nicht "
                                 "entfernt werden")

    for row in session.exec(
        select(LoginSession).where(LoginSession.user_id == user.id)
    ).all():
        session.delete(row)
    name = user.username
    session.delete(user)
    audit(session, who.name, "user.delete", name, request)
    session.commit()
    return {"ok": True}


@app.get("/api/v1/audit")
def list_audit(limit: int = Query(200, ge=1, le=1000),
               who: Principal = Depends(require_admin),
               session: Session = Depends(get_session)):
    """
    Nur lesen. Es gibt bewusst keine Route zum Aendern oder Loeschen -
    wer sich selbst herausschreiben kann, macht das Protokoll wertlos.
    """
    return [
        {"id": e.id, "at": e.at, "actor": e.actor, "action": e.action,
         "detail": e.detail, "from_ip": e.from_ip}
        for e in session.exec(
            select(AuditEntry).order_by(AuditEntry.at.desc()).limit(limit)
        ).all()
    ]


@app.post("/api/v1/install-token")
def create_install_token(request: Request,
                         who: Principal = Depends(require_admin),
                         session: Session = Depends(get_session)):
    """
    Erzeugt ein Installations-Token fuer genau einen Einrichtungsvorgang.

    Der Klartext wird nur hier einmal herausgegeben - danach steht in der
    Datenbank nur der Hash. Die Erzeugung steht im Pruefprotokoll.
    """
    plain, row = new_install_token(session, who.name)
    audit(session, who.name, "install-token.create",
          f"gueltig bis {row.expires_at:%H:%M:%S}, {row.max_uses} Abrufe", request)
    session.commit()
    return {
        # Kennung mitgeben, damit sich genau dieses Token wieder
        # zuruecknehmen laesst. Ueber die Liste zu gehen und das erste zu
        # nehmen waere nicht eindeutig - es koennen mehrere offen sein.
        "id": row.id,
        "token": plain,
        "expires_at": row.expires_at,
        "valid_minutes": INSTALL_TOKEN_MINUTES,
        "max_uses": row.max_uses,
    }


class LicenseIn(BaseModel):
    key: str


@app.get("/api/v1/license", dependencies=[Depends(require_login)])
def get_license(session: Session = Depends(get_session)):
    """
    Stand der Lizenz samt Belegung.

    Fuer jeden angemeldeten Benutzer lesbar, nicht nur fuer
    Administratoren: wer eine Freigabe versucht und am Limit scheitert,
    soll den Grund nachvollziehen koennen.
    """
    d = _LIZENZ.als_dict()
    d["belegt"] = approved_hosts(session)
    d["frei_ohne_schluessel"] = license.FREIE_HOSTS
    return d


@app.post("/api/v1/license", dependencies=[Depends(require_admin)])
def set_license(payload: LicenseIn, request: Request,
                who: Principal = Depends(require_admin),
                session: Session = Depends(get_session)):
    """
    Traegt einen Schluessel ein oder entfernt ihn.

    Ein ungueltiger Schluessel wird abgewiesen, ohne den bisherigen
    Zustand anzutasten - sonst kostet ein Tippfehler beim Einfuegen die
    halbe Installation.
    """
    roh = (payload.key or "").strip()

    if not roh:
        set_setting(session, SET_LICENSE, "")
        audit(session, who.name, "license.remove", None, request)
        session.commit()
        load_license(session)
        return get_license(session)

    try:
        geprueft = license.pruefen(roh)
    except license.LizenzFehler as exc:
        # Bewusst kein Speichern und kein Zuruecksetzen.
        raise HTTPException(400, str(exc))

    set_setting(session, SET_LICENSE, roh)
    audit(session, who.name, "license.set",
          f"Nr. {geprueft.nummer}, {geprueft.kunde}, "
          f"{'unbegrenzt' if geprueft.unbegrenzt else geprueft.daten.get('h')} Hosts, "
          f"bis {geprueft.daten.get('exp') or 'unbefristet'}", request)
    session.commit()
    load_license(session)
    return get_license(session)


class ProxySettings(BaseModel):
    trusted_proxy: Optional[str] = None
    https_only: Optional[bool] = None
    public_url: Optional[str] = None


@app.get("/api/v1/proxy-settings", dependencies=[Depends(require_admin)])
def get_proxy_settings(request: Request,
                       session: Session = Depends(get_session)):
    """
    Aktuelle Einstellung samt Bereitschaftsanzeige.

    Die Zahlen sind der Grund, warum es diese Route gibt: der Zwang trifft
    nicht nur den eigenen Browser, sondern jeden Agent. Vor dem Umschalten
    soll sichtbar sein, wer noch unverschluesselt spricht.
    """
    hosts = session.exec(
        select(Host).where(Host.approval_state == ApprovalState.approved)
    ).all()
    secure = [h.hostname for h in hosts if h.last_seen_secure]
    insecure = [h.hostname for h in hosts if not h.last_seen_secure]

    deadline = _PROXY_CFG["deadline"]
    return {
        "trusted_proxy": _setting(session, SET_TRUSTED_PROXY, ""),
        "public_url": _setting(session, SET_PUBLIC_URL, ""),
        # Die Adresse, die ein jetzt erzeugter Installationsbefehl trueg.
        "effective_url": public_base_url(request, session),
        "https_only": _PROXY_CFG["https_only"],
        # Steht ein Zeitpunkt, ist der Zwang noch nicht bestaetigt und
        # faellt bis dahin von selbst zurueck.
        "confirm_deadline": deadline,
        "confirm_minutes": HTTPS_CONFIRM_MINUTES,
        "agents_secure": len(secure),
        "agents_total": len(hosts),
        "agents_insecure": insecure,
        # Womit die aufrufende Anfrage selbst hereinkam - damit die
        # Oberflaeche sagen kann, ob der Proxy richtig eingetragen ist.
        "this_request_https": request_is_https(request),
        "this_request_from": peer_ip(request),
    }


@app.post("/api/v1/proxy-settings", dependencies=[Depends(require_admin)])
def set_proxy_settings(payload: ProxySettings, request: Request,
                       who: Principal = Depends(require_admin),
                       session: Session = Depends(get_session)):
    if payload.trusted_proxy is not None:
        value = ",".join(
            p.strip() for p in payload.trusted_proxy.split(",") if p.strip()
        )
        set_setting(session, SET_TRUSTED_PROXY, value)
        # Sicherheitsrelevant: wer hier eine breite Adresse eintraegt, kann
        # danach X-Forwarded-For faelschen und die Drosselung der Anmeldung
        # umgehen. Gehoert deshalb ins Pruefprotokoll.
        audit(session, who.name, "proxy.trusted-set", value or "(leer)", request)

    if payload.public_url is not None:
        value = payload.public_url.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise HTTPException(
                400, "Die Adresse muss mit http:// oder https:// beginnen.")
        set_setting(session, SET_PUBLIC_URL, value)
        audit(session, who.name, "public-url.set", value or "(leer)", request)

    if payload.https_only is not None:
        if payload.https_only:
            set_setting(session, SET_HTTPS_ONLY, "true")
            deadline = utcnow() + timedelta(minutes=HTTPS_CONFIRM_MINUTES)
            set_setting(session, SET_HTTPS_DEADLINE, deadline.isoformat())
            audit(session, who.name, "https-only.on",
                  f"Bestaetigung bis {deadline:%H:%M:%S} noetig", request)
        else:
            set_setting(session, SET_HTTPS_ONLY, "false")
            set_setting(session, SET_HTTPS_DEADLINE, "")
            audit(session, who.name, "https-only.off", None, request)

    session.commit()
    load_proxy_config(session)
    return get_proxy_settings(request, session)


@app.get("/api/v1/install-token")
def list_install_tokens(who: Principal = Depends(require_admin),
                        session: Session = Depends(get_session)):
    """
    Zeigt die noch gueltigen Token - ohne Klartext. Dient dazu, ein
    versehentlich erzeugtes Token wieder zurueckzunehmen.
    """
    now = utcnow()
    rows = session.exec(
        select(InstallToken).where(InstallToken.expires_at > now)
        .order_by(InstallToken.expires_at)
    ).all()
    return [
        {"id": r.id, "created_at": r.created_at, "expires_at": r.expires_at,
         "uses": r.uses, "max_uses": r.max_uses, "created_by": r.created_by,
         "last_used_at": r.last_used_at, "last_used_ip": r.last_used_ip}
        for r in rows if r.uses < r.max_uses
    ]


@app.delete("/api/v1/install-token/{token_id}")
def revoke_install_token(token_id: int, request: Request,
                         who: Principal = Depends(require_admin),
                         session: Session = Depends(get_session)):
    row = session.get(InstallToken, token_id)
    if not row:
        raise HTTPException(404, "Token nicht gefunden")
    session.delete(row)
    audit(session, who.name, "install-token.revoke", str(token_id), request)
    session.commit()
    return {"ok": True}


def _darf_versionen_sehen(request: Optional[Request], x_session: str,
                          session: Session) -> bool:
    """
    Darf dieser Aufrufer die Versionsnummern in /api/health sehen?

    Aus der Sicherheitspruefung vom 2026-08-22 (F-09). Die Route muss
    offen bleiben - der Watcher fragt sie nach jedem Update ab, und die
    Anmeldeseite braucht die Vorgabesprache, bevor ein Benutzer bekannt
    ist. Was sie NICHT muss, ist jedem Unangemeldeten sagen, welche
    Fassung hier laeuft. Diese Auskunft ist fuer den Betrieb nutzlos und
    fuer die Auswahl eines passenden Angriffs nuetzlich.

    Zwei Aufrufer bekommen sie weiterhin:

      Loopback - das ist der Watcher. Er laeuft auf demselben Rechner und
      vergleicht die Version, um ein misslungenes Update zu erkennen.

      Angemeldete Benutzer - die Oberflaeche zeigt die Version in der
      Fusszeile und vergleicht sie, um ein veraltetes JavaScript zu
      bemerken. Der Cookie geht bei fetch von selbst mit.

    Das ist kein starker Schutz und soll keiner sein: wer sich anmelden
    kann, sieht die Version ohnehin. Es nimmt nur die Auskunft aus der
    offenen Antwort heraus.
    """
    if is_loopback(request):
        return True
    try:
        return _session_by_token(session_token(request, x_session),
                                 session) is not None
    except Exception:  # noqa: BLE001
        return False


@app.get("/api/health")
def health(request: Request, x_session: str = Header(default=""),
           session: Session = Depends(get_session)):
    """
    Wird nach jedem Update vom Watcher abgefragt. Prueft ausdruecklich das
    Schema - ein Bruch muss hier auffallen und zum Rueckrollen fuehren,
    nicht erst beim ersten Nutzerzugriff.
    """
    ok, missing = migrate.verify(engine)
    if not ok:
        raise HTTPException(
            503,
            {"status": "schema_mismatch", "missing": missing,
             "hint": "Datenbank passt nicht zum Programmstand."},
        )
    # Echte Abfrage, damit auch Lesefehler auffallen
    session.exec(select(Host).limit(1)).first()
    antwort = {
        "status": "ok",
        # Bewusst hier und nicht in einem eigenen Endpunkt: die Anmeldeseite
        # braucht die Vorgabesprache, bevor irgendein Benutzer bekannt ist,
        # und /api/health fragt die Oberflaeche ohnehin bei jedem Durchlauf
        # ab. Ein zweiter offener Endpunkt waere zusaetzliche Angriffsflaeche
        # fuer eine Auskunft, die kein Geheimnis ist.
        "default_language": _setting(session, SET_LANGUAGE, "en"),
    }
    if _darf_versionen_sehen(request, x_session, session):
        antwort["version"] = update_manager.get_current_version()
        antwort["agent_version"] = agent_source_version()
        antwort["schema_version"] = migrate.get_version(engine)
    return antwort


@app.get("/api/v1/schema", dependencies=[Depends(require_admin)])
def schema_info():
    """Zeigt, was die Migration beim letzten Start getan hat."""
    ok, missing = migrate.verify(engine)
    return {
        "ok": ok,
        "missing": missing,
        "version": migrate.get_version(engine),
        "last_run": SCHEMA_REPORT,
    }


# ======================================================================
# Updates des Systems selbst
# ======================================================================
@app.get("/api/v1/update/status", dependencies=[Depends(require_admin)])
def update_status():
    return update_manager.get_status()


@app.post("/api/v1/update/upload")
async def update_upload(request: Request, file: UploadFile = File(...),
                        signature: str = Form(default=""),
                        sigfile: Optional[UploadFile] = File(default=None),
                        who: Principal = Depends(require_admin),
                        session: Session = Depends(get_session)):
    """
    Nimmt ein Update-Paket entgegen.

    Die Signatur kann als Text mitgeschickt oder als .sig-Datei angehaengt
    werden - je nachdem, was der Oberflaeche gerade vorliegt.
    """
    name = file.filename or "update.zip"
    sig = signature.strip()
    if not sig and sigfile is not None:
        sig = (await sigfile.read()).decode("ascii", "replace").strip()
    try:
        res = update_manager.validate_and_store_update(
            await file.read(), name, sig)
    except ValueError as exc:
        audit(session, who.name, "update.upload.failed", f"{name}: {exc}", request)
        session.commit()
        raise HTTPException(400, str(exc))
    audit(session, who.name, "update.upload", name, request)
    session.commit()
    return res


@app.post("/api/v1/update/trigger")
def update_trigger(request: Request, who: Principal = Depends(require_admin),
                   session: Session = Depends(get_session)):
    try:
        res = update_manager.trigger_update()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(session, who.name, "update.trigger", None, request)
    session.commit()
    return res


@app.post("/api/v1/update/cancel", dependencies=[Depends(require_admin)])
def update_cancel():
    try:
        return update_manager.cancel_update()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/update/acknowledge", dependencies=[Depends(require_admin)])
def update_acknowledge():
    try:
        return update_manager.acknowledge()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ======================================================================
# Frontend
# ======================================================================
FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend"
)


class NoCacheStatic(StaticFiles):
    """
    Liefert die Oberflaeche ohne Zwischenspeicherung aus.

    Grund: index.html und app.js tragen keine Versionskennung im Namen.
    Ohne diese Kopfzeilen zeigt der Browser nach einem Update weiterhin die
    alte Fassung, bis der Nutzer von Hand neu laedt - und die Fehlersuche
    geht dann in die falsche Richtung.

    Seit das Skript in einer eigenen Datei liegt, ist das gefaehrlicher als
    zuvor: der Browser koennte ein frisches index.html mit einem alten
    app.js mischen. Beide muessen deshalb ungespeichert bleiben.
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".html", ".js", "/")) or path in ("", "."):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", NoCacheStatic(directory=FRONTEND_DIR, html=True), name="frontend")
