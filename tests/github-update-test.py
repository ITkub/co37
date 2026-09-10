"""
CO-37 - Update-Bezug aus GitHub Releases (0.38.2).

Prueft die reinen Bausteine ohne Netz (release_auswerten, _pruefe_https,
_mit_grenze) und die Routen im Prozess mit gefaelschtem GitHub. Kein
echter Netzzugriff - github_update.neuestes_release/paket_holen werden
ersetzt.

    python3 tests/github-update-test.py
"""
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp())
os.environ["CO37_DB"] = f"sqlite:///{TMP}/gh.db"
os.environ["CO37_DATA"] = str(TMP)
os.environ["CO37_SECRET_KEY"] = "test"

sys.path.insert(0, str(WURZEL / "backend"))
import json  # noqa: E402
import main  # noqa: E402
import github_update as g  # noqa: E402
import update_manager as um  # noqa: E402
from starlette.requests import Request  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402
from models import Role  # noqa: E402
from fastapi import HTTPException  # noqa: E402

SQLModel.metadata.create_all(main.engine)

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


# ======================================================================
# release_auswerten - rein, ohne Netz
# ======================================================================
print("--- release_auswerten ---")


def rel_json(**kw):
    base = dict(tag_name="v0.38.2", draft=False, prerelease=False, assets=[
        {"name": "co37_v0_38_2.zip", "browser_download_url": "https://x/z.zip"},
        {"name": "co37_v0_38_2.zip.sig", "browser_download_url": "https://x/z.sig"},
        {"name": "co37_v0_38_2.zip.sha256", "browser_download_url": "https://x/z.sha"},
    ])
    base.update(kw)
    return json.dumps(base).encode()


r = g.release_auswerten(rel_json())
check("gueltiges Release wird ausgewertet",
      r["version"] == "0.38.2" and r["zip_url"].endswith(".zip")
      and r["sig_url"].endswith(".sig"), r)
check("das .sha256 wird nicht fuer das Paket gehalten",
      r["zip_name"] == "co37_v0_38_2.zip", r["zip_name"])
check("fuehrendes v im Tag wird abgezogen",
      g.release_auswerten(rel_json(tag_name="v1.2.3"))["version"] == "1.2.3")
check("Tag ohne v geht auch",
      g.release_auswerten(rel_json(tag_name="1.2.3"))["version"] == "1.2.3")


def wird_abgewiesen(label, payload):
    try:
        g.release_auswerten(payload)
        check(label, False, "nicht abgewiesen")
    except g.GithubUpdateError:
        check(label, True)


wird_abgewiesen("Entwurf abgewiesen", rel_json(draft=True))
wird_abgewiesen("Vorabversion abgewiesen", rel_json(prerelease=True))
wird_abgewiesen("kein .zip abgewiesen", rel_json(assets=[
    {"name": "co37.zip.sig", "browser_download_url": "https://x/s"}]))
wird_abgewiesen("zwei .zip abgewiesen", rel_json(assets=[
    {"name": "a.zip", "browser_download_url": "https://x/a"},
    {"name": "b.zip", "browser_download_url": "https://x/b"},
    {"name": "a.zip.sig", "browser_download_url": "https://x/s"}]))
wird_abgewiesen("kein .sig abgewiesen", rel_json(assets=[
    {"name": "co37.zip", "browser_download_url": "https://x/z"}]))
wird_abgewiesen("zwei .sig abgewiesen", rel_json(assets=[
    {"name": "a.zip", "browser_download_url": "https://x/a"},
    {"name": "a.zip.sig", "browser_download_url": "https://x/s1"},
    {"name": "b.zip.sig", "browser_download_url": "https://x/s2"}]))
wird_abgewiesen("Asset ohne URL abgewiesen", rel_json(assets=[
    {"name": "a.zip"}, {"name": "a.zip.sig", "browser_download_url": "https://x/s"}]))
wird_abgewiesen("kaputtes JSON abgewiesen", b"{nope")
wird_abgewiesen("leerer Tag abgewiesen", rel_json(tag_name=""))

# ======================================================================
# _pruefe_https und _mit_grenze - rein
# ======================================================================
print("\n--- https-Zwang und Groessengrenze ---")
for url, soll_ok in [("https://x/y", True), ("http://x/y", False),
                     ("ftp://x/y", False), ("", False), (None, False)]:
    try:
        g._pruefe_https(url)
        ist_ok = True
    except g.GithubUpdateError:
        ist_ok = False
    check(f"_pruefe_https({url!r}) -> {'ok' if soll_ok else 'abgewiesen'}",
          ist_ok == soll_ok)

check("_mit_grenze gibt die Bytes zurueck",
      g._mit_grenze([b"aa", b"bb"], 10) == b"aabb")
try:
    g._mit_grenze([b"x" * 6, b"x" * 6], 10)
    check("_mit_grenze bricht ueber der Grenze ab", False, "kein Abbruch")
except g.GithubUpdateError:
    check("_mit_grenze bricht ueber der Grenze ab (WAEHREND des Empfangs)", True)

check("MAX_BYTES entspricht update_manager.MAX_ZIP_BYTES",
      g.MAX_BYTES == um.MAX_ZIP_BYTES, (g.MAX_BYTES, um.MAX_ZIP_BYTES))

# ======================================================================
# update_manager.ist_neuer - eine Stelle fuer den Vergleich
# ======================================================================
print("\n--- ist_neuer ---")
check("0.38.2 > 0.38.1", um.ist_neuer("0.38.2", "0.38.1") is True)
check("0.38.1 nicht > 0.38.1", um.ist_neuer("0.38.1", "0.38.1") is False)
check("0.37.33 nicht > 0.38.0", um.ist_neuer("0.37.33", "0.38.0") is False)
check("leer ist nie neuer", um.ist_neuer("", "0.38.1") is False)
# Zahlenvergleich, nicht Zeichenkette - 0.38.10 > 0.38.9
check("0.38.10 > 0.38.9 (Zahl, nicht Text)", um.ist_neuer("0.38.10", "0.38.9") is True)

# ======================================================================
# Routen im Prozess - GitHub gefaelscht, kein Netz
# ======================================================================
print("\n--- Routen (check / fetch / health) ---")
who = main.Principal("adm", Role.admin)


def S():
    return Session(main.engine)


def loopback_req():
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/api/health", "raw_path": b"/api/health", "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("t", 80),
        "client": ("127.0.0.1", 40000), "headers": [],
    })


def fake_release(version):
    return lambda: {"version": version, "tag": "v" + version,
                    "zip_url": "https://x/z.zip", "sig_url": "https://x/z.sig",
                    "zip_name": f"co37_v{version.replace('.', '_')}.zip"}


aktuell = um.get_current_version()

# check findet etwas Neueres
g.neuestes_release = fake_release("99.9.9")
with S() as s:
    r = main.update_check(request=None, force=True, who=who, session=s)
check("check meldet neueres Release", r.get("available") == "99.9.9" and r["checked"], r)
with S() as s:
    check("Fund wird gespeichert",
          main._setting(s, main.SET_UPDATE_AVAILABLE) == "99.9.9")

# 24-Stunden-Sperre: nicht-force geht NICHT ins Netz
def boom():
    raise AssertionError("haette nicht ins Netz gehen duerfen")


g.neuestes_release = boom
with S() as s:
    r = main.update_check(request=None, force=False, who=who, session=s)
check("nicht-force respektiert die 24-Stunden-Sperre", r.get("checked") is False)

# aelteres Release -> kein Fund, gespeicherter Wert geleert
g.neuestes_release = fake_release("0.0.1")
with S() as s:
    r = main.update_check(request=None, force=True, who=who, session=s)
check("aelteres Release ist kein Fund", r.get("available") is None)
with S() as s:
    check("gespeicherter Fund wird geleert",
          main._setting(s, main.SET_UPDATE_AVAILABLE) == "")

# health meldet update_available nur, solange es wirklich neuer ist
with S() as s:
    main.set_setting(s, main.SET_UPDATE_AVAILABLE, "99.9.9")
    s.commit()
with S() as s:
    h = main.health(loopback_req(), x_session="", session=s)
check("health meldet update_available (neuer)", h.get("update_available") == "99.9.9")
with S() as s:
    main.set_setting(s, main.SET_UPDATE_AVAILABLE, "0.0.1")
    s.commit()
with S() as s:
    h = main.health(loopback_req(), x_session="", session=s)
check("health meldet nichts, wenn der Wert nicht neuer ist",
      "update_available" not in h)

# fetch reicht die Bytes an dieselbe Pruefkette und leert das Band
g.neuestes_release = fake_release("99.9.9")
g.paket_holen = lambda rel: (b"ZIPBYTES", rel["zip_name"], "SIGTEXT")
_orig_store = um.validate_and_store_update
_orig_status = um.get_status
gesehen = {}


def fake_store(b, n, sig):
    gesehen["args"] = (b, n, sig)
    return {"state": "uploaded", "new_version": "99.9.9"}


um.validate_and_store_update = fake_store
um.get_status = lambda: {"state": "idle"}
with S() as s:
    main.set_setting(s, main.SET_UPDATE_AVAILABLE, "99.9.9")
    s.commit()
with S() as s:
    r = main.update_fetch(request=None, who=who, session=s)
check("fetch liefert den uploaded-Zustand", r.get("state") == "uploaded", r)
check("fetch reicht genau die geholten Bytes an validate_and_store_update",
      gesehen.get("args") == (b"ZIPBYTES", "co37_v99_9_9.zip", "SIGTEXT"),
      gesehen.get("args"))
with S() as s:
    check("nach fetch ist das Band geleert",
          main._setting(s, main.SET_UPDATE_AVAILABLE) == "")

# fetch bei laufendem Update wird abgewiesen
um.get_status = lambda: {"state": "running"}
try:
    with S() as s:
        main.update_fetch(request=None, who=who, session=s)
    check("fetch bei laufendem Update abgewiesen", False, "kein 400")
except HTTPException as exc:
    check("fetch bei laufendem Update abgewiesen", exc.status_code == 400, exc.status_code)

um.validate_and_store_update = _orig_store
um.get_status = _orig_status

# remote-config: Haken setzen und lesen
with S() as s:
    main.update_remote_config_set(main.RemoteConfig(auto=True), request=None,
                                  who=who, session=s)
with S() as s:
    cfg = main.update_remote_config(session=s)
check("Haken 'automatisch suchen' wird gespeichert und gelesen",
      cfg.get("auto") is True, cfg)

print(f"\nFehler: {fails}")
sys.exit(1 if fails else 0)
