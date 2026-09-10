"""
CO-37 - Update-Bezug aus GitHub Releases.

KEIN zweiter Update-Mechanismus. Dieses Modul holt nur die Bytes: es fragt
die Releases-API ab, sucht das signierte Paket und laedt ZIP und .sig. Was
danach passiert, ist unveraendert - die Bytes gehen durch
update_manager.validate_and_store_update(), das die Signatur prueft, den
fremden Schluessel und den Rueckschritt abweist und das Paket im Eingang
ablegt. Eingespielt wird erst nach ausdruecklichem Ausloesen durch den
Watcher als root. Die Sicherheit haengt an der Signatur, nicht an der
Herkunft der Bytes.

Aufteilung, damit das Meiste ohne Netz pruefbar ist:

    release_auswerten(json)  - rein, ohne Netz: welche Version, welche
                               Asset-URLs. Der ganze Verstand steckt hier.
    _pruefe_https(url)       - rein: laesst nur https zu.
    _mit_grenze(chunks, max) - rein: bricht ab, wenn zu viel kommt.
    holen(url)               - duenne Transportschicht darum.

Das Repository ist fest eingebaut, nicht konfigurierbar. Eine freie URL
waere eine Einladung, den Bezug umzulenken - die Signatur faengt das zwar,
aber die Flaeche muss nicht sein. Kein GitHub-Token: das Repository ist
oeffentlich, die anonyme API reicht fuer einen Tagescheck.
"""
import json
from typing import Iterable, Optional

import httpx

# Fest eingebaut. Aendert sich der Ort je (Spiegel), ersetzt eine zweite
# Variable nur den Host - eine vom Nutzer setzbare URL gibt es bewusst nicht.
GITHUB_REPO = "ITkub/co37"
API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Dieselbe Grenze wie beim Upload (update_manager.MAX_ZIP_BYTES). Bewusst
# hier gespiegelt und nicht importiert, damit dieses Modul ohne
# update_manager pruefbar bleibt; der Testfall haelt beide Werte gleich.
MAX_BYTES = 50 * 1024 * 1024

TIMEOUT = 30.0
MAX_REDIRECTS = 5


class GithubUpdateError(Exception):
    """Der Bezug ist gescheitert. Der Aufrufer macht daraus eine Meldung."""


def _tag_zu_version(tag: str) -> str:
    """'v0.38.1' -> '0.38.1'. Fuehrendes v/V weg, sonst unveraendert."""
    tag = (tag or "").strip()
    if tag[:1] in ("v", "V"):
        tag = tag[1:]
    return tag


def release_auswerten(json_bytes: bytes) -> dict:
    """
    Wertet die Antwort von /releases/latest aus. Rein, ohne Netz.

    Liefert {version, tag, zip_url, sig_url, zip_name}. Wirft
    GithubUpdateError bei Draft/Prerelease, fehlenden oder mehrdeutigen
    Assets - mehrere Kandidaten bedeuten Abbruch, nicht Raten.
    """
    try:
        d = json.loads(json_bytes)
    except Exception as exc:  # noqa: BLE001
        raise GithubUpdateError(f"Antwort von GitHub ist kein JSON: {exc}")

    if not isinstance(d, dict):
        raise GithubUpdateError("Unerwartete Antwort von GitHub.")

    if d.get("draft"):
        raise GithubUpdateError("Neuestes Release ist ein Entwurf.")
    if d.get("prerelease"):
        raise GithubUpdateError("Neuestes Release ist eine Vorabversion.")

    tag = _tag_zu_version(d.get("tag_name", ""))
    if not tag:
        raise GithubUpdateError("Release ohne Tag.")

    assets = d.get("assets") or []
    # Das .sha256 zaehlt NICHT als ZIP - deshalb erst die Signatur und die
    # Pruefsumme aussortieren, dann bleibt genau das Paket uebrig.
    zips = [a for a in assets
            if str(a.get("name", "")).endswith(".zip")]
    sigs = [a for a in assets
            if str(a.get("name", "")).endswith(".zip.sig")]

    if len(zips) != 1:
        raise GithubUpdateError(
            f"Erwartet genau ein .zip-Asset, gefunden: {len(zips)}.")
    if len(sigs) != 1:
        raise GithubUpdateError(
            f"Erwartet genau ein .zip.sig-Asset, gefunden: {len(sigs)}.")

    zip_url = zips[0].get("browser_download_url")
    sig_url = sigs[0].get("browser_download_url")
    if not zip_url or not sig_url:
        raise GithubUpdateError("Asset ohne Download-Adresse.")

    return {
        "version": tag,
        "tag": d.get("tag_name", ""),
        "zip_url": zip_url,
        "sig_url": sig_url,
        "zip_name": zips[0].get("name"),
    }


def _pruefe_https(url: str) -> str:
    """Laesst nur https zu. Rein - fuer den Redirect-Zwang gebraucht."""
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise GithubUpdateError(
            f"Nur https ist erlaubt, nicht: {str(url)[:60]}")
    return url


def _mit_grenze(chunks: Iterable[bytes], max_bytes: int = MAX_BYTES) -> bytes:
    """
    Sammelt Bloecke und bricht ab, sobald die Grenze ueberschritten ist -
    WAEHREND des Empfangs, nicht danach. Ein Server, der endlos liefert,
    fuellt sonst den Speicher, bevor irgendeine Pruefung greift.
    """
    out = bytearray()
    for c in chunks:
        out += c
        if len(out) > max_bytes:
            raise GithubUpdateError(
                f"Download groesser als {max_bytes // (1024*1024)} MB - abgebrochen.")
    return bytes(out)


def holen(url: str, max_bytes: int = MAX_BYTES, timeout: float = TIMEOUT) -> bytes:
    """
    Laedt eine Datei ueber https, mit Groessengrenze beim Streamen.

    Redirects werden von Hand verfolgt, damit JEDER Sprung auf https
    geprueft wird - GitHub leitet den Asset-Download auf
    objects.githubusercontent.com um. httpx' eigenes follow_redirects
    wuerde einen Sprung auf http nicht abweisen.
    """
    _pruefe_https(url)
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            for _ in range(MAX_REDIRECTS + 1):
                with client.stream("GET", url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        ziel = resp.headers.get("location", "")
                        url = _pruefe_https(ziel)
                        continue
                    if resp.status_code != 200:
                        raise GithubUpdateError(
                            f"GitHub antwortete mit {resp.status_code}.")
                    return _mit_grenze(resp.iter_bytes(), max_bytes)
            raise GithubUpdateError("Zu viele Weiterleitungen.")
    except httpx.HTTPError as exc:
        raise GithubUpdateError(
            f"GitHub nicht erreichbar ({type(exc).__name__}): {exc}")


def neuestes_release() -> dict:
    """Fragt die Releases-API ab und wertet die Antwort aus. Mit Netz."""
    try:
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
            resp = client.get(
                API_LATEST,
                headers={"Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"})
    except httpx.HTTPError as exc:
        raise GithubUpdateError(
            f"GitHub nicht erreichbar ({type(exc).__name__}): {exc}")
    if resp.status_code == 404:
        raise GithubUpdateError("Keine Releases vorhanden.")
    if resp.status_code != 200:
        raise GithubUpdateError(f"GitHub antwortete mit {resp.status_code}.")
    return release_auswerten(resp.content)


def paket_holen(rel: dict) -> tuple:
    """
    Laedt Paket und Signatur zu einem ausgewerteten Release.

    Liefert (zip_bytes, zip_name, sig_text) - genau die drei Werte, die
    update_manager.validate_and_store_update() erwartet.
    """
    zip_bytes = holen(rel["zip_url"])
    sig_bytes = holen(rel["sig_url"], max_bytes=64 * 1024)
    sig_text = sig_bytes.decode("ascii", "replace").strip()
    return zip_bytes, rel.get("zip_name") or "co37-update.zip", sig_text
