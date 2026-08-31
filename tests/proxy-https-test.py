"""
CO-37 - Reverse Proxy und HTTPS-Zwang.

Der Zwang weist alles ab, was nicht ueber den eingetragenen Proxy
verschluesselt ankam. Zwei Dinge muessen dabei sitzen, sonst ist das
System nicht mehr erreichbar oder repariert sich selbst kaputt:

  * Der Rueckfall. Meldet sich binnen der Frist niemand ueber HTTPS,
    schaltet sich der Zwang von selbst ab. Ohne das sperrt ein falsch
    eingetragener Proxy dauerhaft aus.
  * Loopback bleibt frei. Der Watcher prueft die Gesundheit des Backends
    ueber 127.0.0.1; bekaeme er 403, hielte er ein eingespieltes Update
    fuer fehlgeschlagen und spielte die Sicherung zurueck.

Braucht ein laufendes Backend mit eigener Testdatenbank. Die Anfragen
kommen alle von 127.0.0.1 - der Proxy wird deshalb auf diese Adresse
eingetragen, um die Kopfzeilen wirken zu lassen.

    python3 tests/proxy-https-test.py
"""
import json
import os
import urllib.error
import urllib.request

B = os.getenv("CO37_TEST_URL", "http://127.0.0.1:8085")
# Das Geruest aendert das Anfangspasswort beim Start (erzwungener
# Wechsel, F-16). Wer diese Reihe einzeln gegen ein frisches Backend
# laufen laesst, hat noch das Anfangspasswort - daher der Rueckfall.
ADMIN_PW = os.getenv("CO37_TEST_ADMIN_PW", "admin")
ADMIN = {"X-Session": os.getenv("CO37_TEST_SESSION", "")}

fails = 0


def check(label, ok, extra=""):
    global fails
    if not ok:
        fails += 1
    print(f"{'ok    ' if ok else 'FEHLER'} {label}{'  -> ' + str(extra) if extra else ''}")


def call(path, data=None, method=None, hdr=None):
    h = {"Content-Type": "application/json"}
    h.update(hdr or {})
    r = urllib.request.Request(
        B + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        body = urllib.request.urlopen(r, timeout=30).read()
        return 200, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


HTTPS = {"X-Forwarded-Proto": "https"}
HTTP = {"X-Forwarded-Proto": "http"}

# ------------------------------------------------ Ausgangslage
code, res = call("/api/v1/proxy-settings", hdr=ADMIN)
check("Einstellungen abrufbar", code == 200, code)
check("Zwang ist zunaechst aus", res.get("https_only") is False, res.get("https_only"))
check("Bereitschaft wird mitgeliefert", "agents_total" in res)

# ------------------------------------------------ ohne eingetragenen Proxy
code, res = call("/api/v1/proxy-settings", {"trusted_proxy": ""}, hdr=ADMIN)
check("ohne Proxy speicherbar", code == 200, code)

# Regel 1: gilt unabhaengig vom Schalter. Eine Anfrage, die 'https'
# behauptet, ohne vom eingetragenen Proxy zu kommen, wird abgewiesen -
# nicht stillschweigend als unverschluesselt behandelt.
code, _ = call("/api/v1/hosts", hdr=dict(ADMIN, **HTTPS))
check("ohne Proxy: HTTPS wird abgewiesen", code == 403, code)
code, _ = call("/api/v1/hosts", hdr=ADMIN)
check("ohne Proxy: HTTP kommt durch", code == 200, code)
code, _ = call("/api/v1/hosts", hdr=dict(ADMIN, **HTTP))
check("ohne Proxy: HTTP mit Kopfzeile kommt durch", code == 200, code)

# ------------------------------------------------ mit eingetragenem Proxy
code, res = call("/api/v1/proxy-settings", {"trusted_proxy": "127.0.0.1"},
                 hdr=ADMIN)
check("Proxy eingetragen", res.get("trusted_proxy") == "127.0.0.1",
      res.get("trusted_proxy"))
code, res = call("/api/v1/proxy-settings", hdr=dict(ADMIN, **HTTPS))
check("mit Proxy zaehlt die Kopfzeile",
      res.get("this_request_https") is True, res.get("this_request_https"))
check("Adresse der Anfrage wird gemeldet",
      res.get("this_request_from") == "127.0.0.1", res.get("this_request_from"))

# ------------------------------------------------ Zwang einschalten
code, res = call("/api/v1/proxy-settings", {"https_only": True},
                 hdr=dict(ADMIN, **HTTPS))
check("Zwang einschaltbar", res.get("https_only") is True, code)
check("Frist wird gesetzt", bool(res.get("confirm_deadline")),
      res.get("confirm_deadline"))

code, _ = call("/api/v1/hosts", hdr=dict(ADMIN, **HTTPS))
check("ueber HTTPS weiterhin erreichbar", code == 200, code)

code, _ = call("/api/v1/hosts", hdr=dict(ADMIN, **HTTP))
check("ueber HTTP abgewiesen", code == 403, code)

# Bei aktivem Zwang traegt das Sitzungscookie den Secure-Merker. Ohne den
# Zwang darf er NICHT gesetzt sein - sonst naehme der Browser das Cookie
# ueber den unverschluesselten Zugang nicht an und niemand kaeme hinein.
r = urllib.request.Request(
    B + "/api/v1/login",
    data=json.dumps({"username": "admin", "password": ADMIN_PW}).encode(),
    headers={"Content-Type": "application/json", **HTTPS})
roh = urllib.request.urlopen(r, timeout=30).headers.get("Set-Cookie") or ""
check("mit HTTPS-Zwang traegt das Cookie Secure",
      "secure" in roh.lower(), roh)

code, _ = call("/api/v1/hosts", hdr=ADMIN)
check("ohne Kopfzeile abgewiesen", code == 403, code)

# Die Gesundheitspruefung bleibt frei - sonst haelt der Watcher ein
# eingespieltes Update fuer fehlgeschlagen und rollt zurueck.
code, _ = call("/api/health")
check("Gesundheitspruefung bleibt frei", code == 200, code)

# ------------------------------------------------ Zwang wieder aus
code, res = call("/api/v1/proxy-settings", {"https_only": False},
                 hdr=dict(ADMIN, **HTTPS))
check("Zwang abschaltbar", res.get("https_only") is False, code)
code, _ = call("/api/v1/hosts", hdr=dict(ADMIN, **HTTP))
check("danach wieder ueber HTTP erreichbar", code == 200, code)

# ------------------------------------------------ Oeffentliche Adresse
code, res = call("/api/v1/proxy-settings", {"public_url": ""}, hdr=ADMIN)
check("ohne Eintrag wird die Adresse abgeleitet",
      (res.get("effective_url") or "").startswith("http://127.0.0.1:"),
      res.get("effective_url"))

code, res = call("/api/v1/proxy-settings",
                 {"public_url": "https://co37.example.net/"}, hdr=ADMIN)
check("Adresse speicherbar",
      res.get("public_url") == "https://co37.example.net", res.get("public_url"))
check("Schraegstrich am Ende wird entfernt",
      not (res.get("public_url") or "").endswith("/"), res.get("public_url"))
check("Adresse hat Vorrang vor dem Aufruf",
      res.get("effective_url") == "https://co37.example.net",
      res.get("effective_url"))

code, res = call("/api/v1/proxy-settings", {"public_url": "co37.example.net"},
                 hdr=ADMIN)
check("Adresse ohne Schema wird abgelehnt", code == 400, code)

# Diese Route liefert reinen Text, kein JSON - deshalb direkt abrufen.
r = urllib.request.Request(B + "/api/v1/install-script", headers=dict(ADMIN))
skript = urllib.request.urlopen(r, timeout=30).read().decode()
check("Installationsskript nutzt die Adresse",
      "https://co37.example.net" in skript,
      [l for l in skript.splitlines() if "curl" in l][:1])
check("Installationsskript nutzt nicht die Aufrufadresse",
      "127.0.0.1:8085" not in skript)

# Ausgangszustand wiederherstellen. Ein stehengebliebener Proxy-Eintrag
# wirkt auf jede spaeter gegen dasselbe Backend laufende Reihe.
call("/api/v1/proxy-settings",
     {"public_url": "", "trusted_proxy": "", "https_only": False},
     hdr=ADMIN)

# Bewusst NICHT geprueft: Zwang an und gleichzeitig ein fremder Proxy
# eingetragen. Dann ist auch der Weg zum Abschalten zu - genau die
# Aussperrung, fuer die es den Rueckfall gibt. Das laesst sich ueber HTTP
# nicht pruefen, ohne den Dienst neu zu starten; dafuer gibt es
# tests/proxy-fallback-test.py.

print(f"\nFehler: {fails}")
raise SystemExit(1 if fails else 0)
