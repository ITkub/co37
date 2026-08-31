"""
CO-37 - Die Anmeldung eines Agents ist in der Menge begrenzt.

Aus der Sicherheitspruefung vom 2026-08-22:

  F-08: /api/v1/agent/enroll ist bewusst ohne Enrollment-Token - das
  System ist fuer den Betrieb im lokalen Netz gedacht, und der Schutz
  liegt in der Freigabe durch einen Menschen. Das bleibt so. Ungeschuetzt
  war aber die MENGE: wer die Route erreicht, konnte in einer Schleife
  beliebig viele Hostzeilen anlegen. Jede erscheint im Dashboard, und in
  genuegender Zahl geht der eine echte neue Host in der Freigabeliste
  unter.

  F-10: enrolled_from_ip wurde in den beiden Zweigen unterschiedlich
  ermittelt - bei der Erstanmeldung aus der Verbindung, bei der
  Neuanmeldung ueber client_ip(). Hinter einem Reverse Proxy war die
  Verbindung immer die des Proxys, und im Dashboard sah dann jeder neu
  angemeldete Host so aus, als kaeme er vom selben Rechner.

WARUM DIESE REIHE NICHT UEBER DAS NETZ PRUEFT: sie muesste dafuer die
Grenze tatsaechlich erreichen, also fuenfzig Anmeldungen von 127.0.0.1
absetzen. Das Testgeruest teilt sich EIN Backend ueber alle Reihen - die
Sperre bliebe danach eine Viertelstunde stehen und liesse jede spaeter
laufende Reihe scheitern, die einen Host anmeldet. Genau dieses Muster
hat die Anmeldedrosselung schon einmal ausgeloest (siehe run-tests.sh,
Abschnitt "Reihenfolge"). Deshalb hier ein eigenes Backend im selben
Prozess, mit eigener Datenbank.

Braucht kein laufendes Backend und kein Netz.

    python3 tests/enroll-test.py
"""
import os
import sys
import tempfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/enroll.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import main  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402
from sqlmodel import Session, SQLModel, select  # noqa: E402
from models import ApprovalState, Host  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def anfrage(ip, kopfzeilen=None):
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "path": "/api/v1/agent/enroll", "raw_path": b"/api/v1/agent/enroll",
        "query_string": b"", "root_path": "", "scheme": "http",
        "server": ("test", 80),
        "client": (ip, 40000) if ip else None,
        "headers": [(k.lower().encode(), v.encode())
                    for k, v in (kopfzeilen or {}).items()],
    })


def enroll(hostname, ip="192.0.2.10", kopfzeilen=None):
    """Ruft die Route so auf, wie FastAPI es tut. Gibt (Code, Antwort)."""
    nutzlast = main.AgentEnroll(hostname=hostname, os_type="linux",
                                os_version="Debian 13", agent_version="0.36.14")
    with Session(main.engine) as s:
        try:
            return 200, main.agent_enroll(nutzlast, anfrage(ip, kopfzeilen), s)
        except HTTPException as exc:
            return exc.status_code, exc.detail


def zurueckgesetzt():
    """Zaehler leeren - jeder Abschnitt beginnt bei null."""
    main._ENROLLS.clear()


# ======================================================================
print("--- Die Drosselung greift, aber nicht zu frueh ---")
# Beide Richtungen, sonst sagt die Reihe nichts: eine Grenze, die immer
# sperrt, bestuende die Pruefung "der 51. wird abgewiesen" ebenfalls.
zurueckgesetzt()
grenze = main.ENROLL_MAX_PER_SOURCE
letzter = None
for i in range(grenze):
    letzter = enroll(f"grenze-{i:03d}")[0]
check(f"die ersten {grenze} Anmeldungen gehen durch", letzter == 200, letzter)

code, detail = enroll("einer-zuviel")
check("die naechste wird abgewiesen", code == 429, code)
check("und nennt einen Grund", isinstance(detail, str) and "Adresse" in detail,
      detail)

# Gegenprobe: es sperrt die QUELLE, nicht die Route.
code, _ = enroll("andere-quelle", ip="192.0.2.99")
check("eine andere Adresse kommt weiterhin durch", code == 200, code)

# Gegenprobe: eine abgewiesene Anmeldung darf nicht mitzaehlen. Sonst
# genuegten fuenfzig unsinnige Hostnamen, um eine Quelle auszusperren -
# und ein Agent mit kaputtem Namen sperrte sich selbst aus.
zurueckgesetzt()
for _ in range(grenze):
    enroll("hat leerzeichen")          # scheitert an check_hostname
code, _ = enroll("sauber-danach")
check("abgewiesene Hostnamen zaehlen nicht mit", code == 200, code)


# ======================================================================
print("--- Die Obergrenze wartender Hosts ---")
zurueckgesetzt()
merke = main.PENDING_MAX
try:
    # Klein gesetzt statt hundert Hosts anzulegen: geprueft wird, DASS die
    # Zahl aus pending_count() die Entscheidung traegt, nicht welche Zahl
    # darin steht.
    with Session(main.engine) as s:
        stand = main.pending_count(s)
    main.PENDING_MAX = stand + 1

    code, _ = enroll("noch-platz", ip="198.51.100.1")
    check("solange Platz ist, geht es durch", code == 200, code)

    code, detail = enroll("kein-platz-mehr", ip="198.51.100.1")
    check("ist die Grenze erreicht, wird abgewiesen", code == 429, code)
    check("und der Grund nennt den Weg heraus",
          isinstance(detail, str) and "freigeben" in detail, detail)

    with Session(main.engine) as s:
        check("der abgewiesene Host wurde nicht angelegt",
              s.exec(select(Host).where(Host.hostname == "kein-platz-mehr")
                     ).first() is None)

    # Gegenprobe: eine Freigabe macht wieder Platz. Ohne das waere die
    # Grenze eine Sackgasse statt einer Bremse.
    with Session(main.engine) as s:
        einer = s.exec(select(Host).where(
            Host.approval_state == ApprovalState.pending)).first()
        einer.approval_state = ApprovalState.approved
        s.add(einer)
        s.commit()
    code, _ = enroll("nach-der-freigabe", ip="198.51.100.1")
    check("nach einer Freigabe geht es weiter", code == 200, code)

    # Gegenprobe: ein BEKANNTER Host darf nicht an der Grenze scheitern.
    # Er legt nichts Neues an - ihn auszusperren, weil andere warten,
    # waere ein Betriebsschaden ohne Sicherheitsgewinn.
    with Session(main.engine) as s:
        h = s.exec(select(Host).where(
            Host.hostname == "nach-der-freigabe")).first()
        h.agent_token_hash = None      # Token im Dashboard zurueckgezogen
        s.add(h)
        s.commit()
    with Session(main.engine) as s:
        main.PENDING_MAX = main.pending_count(s)   # voll
    code, _ = enroll("nach-der-freigabe", ip="198.51.100.1")
    check("ein bekannter Host meldet sich auch bei voller Liste neu an",
          code == 200, code)
finally:
    main.PENDING_MAX = merke


# ======================================================================
print("--- Woher der Host gekommen ist (F-10) ---")
zurueckgesetzt()


def herkunft(hostname):
    with Session(main.engine) as s:
        h = s.exec(select(Host).where(Host.hostname == hostname)).first()
        return h.enrolled_from_ip if h else None


# Ohne eingetragenen Proxy zaehlt die Verbindung, egal was in der
# Kopfzeile steht - sonst koennte sie jeder selbst setzen.
main._PROXY_CFG["trusted"] = []
enroll("herkunft-direkt", ip="203.0.113.7",
       kopfzeilen={"X-Forwarded-For": "10.9.9.9"})
check("ohne Proxy zaehlt die Verbindung",
      herkunft("herkunft-direkt") == "203.0.113.7", herkunft("herkunft-direkt"))

# Mit eingetragenem Proxy zaehlt der urspruengliche Aufrufer.
main._PROXY_CFG["trusted"] = ["203.0.113.8"]
enroll("herkunft-proxy", ip="203.0.113.8",
       kopfzeilen={"X-Forwarded-For": "10.9.9.9"})
check("hinter dem eingetragenen Proxy zaehlt der Aufrufer",
      herkunft("herkunft-proxy") == "10.9.9.9", herkunft("herkunft-proxy"))

# Und in beiden Zweigen gleich: Erstanmeldung oben, Neuanmeldung hier.
with Session(main.engine) as s:
    h = s.exec(select(Host).where(Host.hostname == "herkunft-proxy")).first()
    h.agent_token_hash = None
    s.add(h)
    s.commit()
enroll("herkunft-proxy", ip="203.0.113.8",
       kopfzeilen={"X-Forwarded-For": "10.9.9.10"})
check("die Neuanmeldung ermittelt sie genauso",
      herkunft("herkunft-proxy") == "10.9.9.10", herkunft("herkunft-proxy"))

main._PROXY_CFG["trusted"] = []

# Gegenprobe im Quelltext: die alte Fassung darf nicht zurueckkommen.
# Sie sah harmlos aus - request.client.host - und war es hinter einem
# Proxy nicht.
quelle = (WURZEL / "backend" / "main.py").read_text(encoding="utf-8")
check("enrolled_from_ip wird nirgends mehr aus request.client gefuellt",
      "enrolled_from_ip=request.client" not in quelle
      and "enrolled_from_ip = request.client" not in quelle)



# ======================================================================
# Hostnamen sind eindeutig, auch ueber die Schreibweise (F-33)
# ======================================================================
# F-23 hat die Umbenennung per Heartbeat auf einen belegten Namen
# geschlossen - aber nur bytegleich. Der Vergleich war ein SQL-'=' auf
# einer Spalte ohne COLLATE NOCASE: 'DC01' und 'dc01' galten als zwei
# verschiedene Hosts, obwohl DNS und Windows sie nicht unterscheiden. In
# der Freigabeliste standen damit wieder zwei nicht unterscheidbare
# Eintraege, und die menschliche Freigabe - nach F-08 der GESAMTE Schutz
# der offenen Anmelderoute - war wieder ein Muenzwurf.
check("der Vergleich laeuft ueber hostname_belegt()",
      "def hostname_belegt" in quelle)
check("hostname_belegt vergleicht kleingeschrieben",
      "func.lower(Host.hostname)" in quelle)
check("kein blanker Vergleich Host.hostname == mehr",
      "Host.hostname == " not in quelle)
check("ein abschliessender Punkt wird abgewiesen",
      'name.endswith(".")' in quelle)

# Die Datenbank faengt das Rennen ab - die Pruefung in der Route allein
# kann es nicht: zwischen dem Nachsehen und dem Schreiben liegt kein
# Schloss, und beide Routen laufen nebenlaeufig im Threadpool.
mig = (WURZEL / "backend" / "migrate.py").read_text(encoding="utf-8")
check("es gibt einen eindeutigen Index ueber lower(hostname)",
      "ux_host_hostname_nocase" in mig and "lower(hostname)" in mig)
check("ein vorhandenes Doppelpaar bricht die Migration nicht ab",
      "def _hostname_index" in mig and "report[\"notes\"].append" in mig)

# Und die Wirkung, nicht nur der Wortlaut.
import sqlite3 as _s3  # noqa: E402
_c = _s3.connect(":memory:")
_c.execute("create table host(id integer primary key, hostname text)")
_c.execute("create unique index ux_host_hostname_nocase on host(lower(hostname))")
_c.execute("insert into host(hostname) values('dc01')")


def _geht_durch(name):
    try:
        _c.execute("insert into host(hostname) values(?)", (name,))
        _c.execute("delete from host where hostname=?", (name,))
        return True
    except _s3.IntegrityError:
        return False


check("DC01 kommt nicht neben dc01", not _geht_durch("DC01"))
check("Dc01 kommt nicht neben dc01", not _geht_durch("Dc01"))
check("ein anderer Name geht weiterhin durch", _geht_durch("dc02"))

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
