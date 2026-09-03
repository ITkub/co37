/**
 * Fuehrt das Frontend-Skript aus index.html in Node aus, gegen ein echtes
 * Backend. Statt eines Browsers eine minimale DOM-Attrappe - damit laesst
 * sich pruefen, ob die Funktionen wirklich durchlaufen und was sie in die
 * Elemente schreiben.
 */
const fs = require("fs");
const { execSync } = require("child_process");

const PORT = process.argv[2] || "8085";
// Sitzungstoken statt des frueheren globalen Admin-Tokens. run-tests.sh
// meldet sich einmal an und reicht es an alle Reihen weiter.
const SESSION = process.argv[3] || process.env.CO37_TEST_SESSION || "";

// Seit 0.13.0 arbeitet die Oberflaeche mit einer Anmeldesitzung statt mit
// dem API-Key. Die Sitzung muss vorliegen, bevor das Skript geladen wird -
// deshalb hier synchron per curl statt mit fetch.
// Das Geruest aendert das Anfangspasswort beim Start (erzwungener Wechsel,
// F-16) und reicht das neue als CO37_TEST_ADMIN_PW weiter. Der Rueckfall
// auf "admin" gilt fuer den Einzellauf gegen ein frisches Backend.
const ADMIN_PW = process.env.CO37_TEST_ADMIN_PW || "admin";
let SESSION_TOKEN = "";
try {
  const out = execSync(
    `curl -s -X POST http://127.0.0.1:${PORT}/api/v1/login `
    + `-H "Content-Type: application/json" `
    + `-d '{"username":"admin","password":"${ADMIN_PW}"}'`,
    { encoding: "utf8" });
  SESSION_TOKEN = JSON.parse(out).session || "";
} catch (e) {
  console.error("Anmeldung fehlgeschlagen. Laeuft das Backend, und passt das "
              + "Passwort von admin (CO37_TEST_ADMIN_PW)?");
}
if (!SESSION_TOKEN) process.exit(1);
const HTML_PATH = process.argv[4] || "/tmp/jt/frontend/index.html";
const html = fs.readFileSync(HTML_PATH, "utf8");
// Das Skript liegt seit 0.27.0 in einer eigenen Datei - noetig, damit die
// Regel script-src ohne 'unsafe-inline' auskommt.
const JS_PATH = require("path").join(require("path").dirname(HTML_PATH), "app.js");
const appjs = fs.readFileSync(JS_PATH, "utf8");
// i18n.js steht im Browser als zweites Skript daneben und teilt sich mit
// app.js den globalen Namensraum. Hier muss es davor stehen, sonst fehlen
// t() und setzeSprache - und was app.js davon benutzt, faellt erst im
// Betrieb auf.
const I18N_PATH = require("path").join(require("path").dirname(HTML_PATH), "i18n.js");
const i18njs = fs.readFileSync(I18N_PATH, "utf8");

// ---------------------------------------------------------------- DOM-Attrappe
const store = {};
function mkEl(id) {
  return {
    id,
    _text: "", _html: "", value: "", checked: false, placeholder: "",
    className: "", style: {}, dataset: {}, files: [],
    // Fuer die Scrollsperre (Rueckmeldung Schritt 5, Punkt 4): showModal()
    // und close() setzen 'open' wie im echten <dialog> und benachrichtigen
    // registrierte MutationObserver - siehe FakeMutationObserver unten.
    open: false,
    _observers: [],
    classList: {
      _c: new Set(),
      add(c) { this._c.add(c); }, remove(c) { this._c.delete(c); },
      toggle(c, on) { on ? this._c.add(c) : this._c.delete(c); },
      contains(c) { return this._c.has(c); },
    },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    addEventListener() {}, removeEventListener() {},
    appendChild() {}, remove() {}, select() {}, setSelectionRange() {},
    setAttribute() {},
    showModal() { this.open = true; this._observers.forEach(cb => cb()); },
    close() { this.open = false; this._observers.forEach(cb => cb()); },
    click() {}, focus() {}, blur() {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
  };
}
// Bildet nur genau das nach, was app.js tatsaechlich benutzt: eine einzige
// Beobachtung des 'open'-Attributs. Kein generischer DOM-Mutationsabgleich.
class FakeMutationObserver {
  constructor(cb) { this.cb = cb; }
  observe(el) { el._observers.push(this.cb); }
  disconnect() {}
}
function el(id) { return store[id] || (store[id] = mkEl(id)); }

// Elemente aus dem HTML vorab anlegen
for (const m of html.matchAll(/id="(\w+)"/g)) el(m[1]);

// Browser legen Elemente mit id automatisch als globale Namen an.
// Node nicht - hier nachbilden, sonst scheitert der Test an etwas,
// das im Browser funktioniert.
for (const m of html.matchAll(/id="(\w+)"/g)) {
  const id = m[1];
  if (!(id in global)) {
    Object.defineProperty(global, id, { get: () => el(id), configurable: true });
  }
}

// Reiter-Knoepfe mit dataset
const tabButtons = [];
for (const m of html.matchAll(/<button([^>]*?)data-(h?)tab="(\w+)"([^>]*)>/g)) {
  const b = mkEl("btn_" + m[3]);
  b.dataset[m[2] ? "htab" : "tab"] = m[3];
  b._group = m[2] ? "hTabs" : "sTabs";
  tabButtons.push(b);
}

// Alle <dialog id="..."> aus dem echten Markup - fuer
// document.querySelectorAll("dialog") (Scrollsperre, Rueckmeldung
// Schritt 5, Punkt 4). Liefert dieselben, ueber el() zwischengespeicherten
// Objekte wie document.getElementById(), damit ein showModal()/close() von
// app.js auf demselben Objekt landet, das hier beobachtet wird.
const dialogIds = [...html.matchAll(/<dialog id="(\w+)"/g)].map(m => m[1]);

// Die Oberflaeche haengt einen Klick-Verteiler ans Dokument. Hier
// mitgeschnitten, damit der Test ihn aufrufen kann - im Browser passiert
// das ueber ein echtes Klickereignis.
let CLICK_HANDLER = null;
// Offene Dialoge fuer document.querySelectorAll("dialog[open]") - eigens
// von Hand gefuellt in den Tests, die das brauchen (Meldungsposition),
// unabhaengig vom echten offen/geschlossen-Zustand ueber .open.
const DIALOGE_OFFEN = [];

global.document = {
  documentElement: { dataset: {} },
  cookie: "",
  body: { appendChild() {}, removeChild() {}, style: {} },
  addEventListener(typ, fn) { if (typ === "click") CLICK_HANDLER = fn; },
  removeEventListener() {},
  getElementById: el,
  createElement: () => {
    const e = mkEl("tmp");
    // Die Meldung wird als popover eingeblendet, wenn der Browser es
    // kann. Hier bewusst NICHT vorhanden, damit der Ersatzweg geprueft
    // wird: anhaengen an den offenen Dialog.
    e.setAttribute = (k, v) => { e.dataset[k] = v; };
    return e;
  },
  querySelector(sel) {
    // Kein Dialog offen in der Attrappe - der Kopier-Rueckfallweg faellt
    // dann auf document.body zurueck, wie im Browser ohne offenen Dialog.
    if (sel === "dialog[open]") return null;
    return null;
  },
  querySelectorAll(sel) {
    // Meldungen haengen sich an den obersten offenen Dialog.
    if (sel === "dialog[open]") return DIALOGE_OFFEN;
    if (sel === "dialog") return dialogIds.map(el);
    if (sel === "#sTabs button") return tabButtons.filter(b => b._group === "sTabs");
    if (sel === "#hTabs button") return tabButtons.filter(b => b._group === "hTabs");
    if (sel === "[data-close]") return [];
    if (sel === ".tabs button") throw new Error("Ungenauer Selektor .tabs button!");
    if (sel === ".pane") throw new Error("Ungenauer Selektor .pane!");
    return [];
  },
  execCommand: () => true,
};
global.window = { isSecureContext: false, storage: null };
global.location = { origin: `http://127.0.0.1:${PORT}` };
global.navigator = {};   // wie im unsicheren Kontext: kein clipboard
// localStorage bildet die Oberflaeche nicht mehr ab - das Sitzungstoken
// liegt in einem Cookie mit HttpOnly. Bleibt als leere Attrappe stehen,
// damit ein spaeterer Zugriff nicht unbemerkt am fehlenden Objekt
// scheitert.
global.localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

// Node hat keinen Cookie-Speicher. Der Browser schickt das Sitzungscookie
// von selbst mit; hier muss das nachgebildet werden, sonst laufen alle
// Aufrufe der Oberflaeche in 401.
const realFetch = global.fetch;
global.fetch = (url, opts = {}) => {
  const headers = { ...(opts.headers || {}) };
  if (!headers.Cookie && !headers.cookie) {
    headers.Cookie = `co37_session=${SESSION_TOKEN}`;
  }
  return realFetch(url, { ...opts, headers });
};
global.prompt = () => SESSION;
global.confirm = () => true;
global.matchMedia = () => ({ matches: false });
global.MutationObserver = FakeMutationObserver;
global.URL.createObjectURL = () => "blob:x";
global.URL.revokeObjectURL = () => {};

const TOASTS = [];
// Startroutine des Skripts abfangen, damit sie nicht sofort loslaeuft
const origSetInterval = global.setInterval;
const origSetTimeout = global.setTimeout;
global.setInterval = () => 0;
global.setTimeout = () => 0;

// -------------------------------------------------------------- Skript laden
const script = i18njs + "\n" + appjs;
const mod = { exports: {} };
const EXPORTS = "\nreturn { loadAgentsTab, loadCmk, loadCmkForm, copy, fmtSize, toast, "
  + "rebootHost: typeof rebootHost !== 'undefined' ? rebootHost : null, "
  + "setHosts: (h) => { HOSTS = h; }, "
  + "checkVersion, setPageVersion: (v) => { PAGE_VERSION = v; }, "
  + "apiCall: api, offlineSeit: () => OFFLINE_SEIT, "
  + "setOfflineSeit: (t) => { OFFLINE_SEIT = t; }, "
  + "spracheImDialogZeigen, setzeSprache, "
  + "setVorgabeSprache: (s) => { VORGABE_SPRACHE = s; }, "
  + "setUpdateLaeuft: (b) => { UPDATE_LAEUFT = b; }, "
  + "loadRollout, loadUsers, loadAudit, loadAccount, nuRechteAnzeigen, "
  + "setMe: (m) => { ME = m; }, getMe: () => ME, renderRackHead, makeInstallToken, forgetInstallToken: () => { INSTALL_TOKEN = null; renderLinuxCmd(); }, "
  + "render, setAreas: (a) => { AREAS = a; }, applyDrop, "
  + "loadAreasTab, editArea, moveArea, scanArea, patchArea, rebootArea, "
  + "getEditAreaId: () => EDIT_AREA_ID, getAreas: () => AREAS, renderAreaHead, renderUnit, "
  + "setLastAction: (o) => { LAST_ACTION = o; }, removeArea, t, fmtTime, "
  + "setAgentVer: (v) => { AGENT_VER = v; }, setActive: (a) => { ACTIVE = a; } };";
const wrapped = new Function(script + EXPORTS);
let api;
try {
  api = wrapped();
} catch (e) {
  console.error("Skript liess sich nicht laden:", e.message);
  process.exit(1);
}

global.setInterval = origSetInterval;
global.setTimeout = origSetTimeout;

// ------------------------------------------------------------------ Pruefungen
(async () => {
  let fails = 0;
  const check = (label, ok, extra = "") => {
    if (!ok) fails++;
    console.log(`${ok ? "ok    " : "FEHLER"} ${label}${extra ? "  -> " + extra : ""}`);
  };

  console.log("=== fmtSize ===");
  check("11200 Bytes -> KB", api.fmtSize(11200) === "11 KB", api.fmtSize(11200));
  check("2 MB", api.fmtSize(2 * 1024 * 1024) === "2.0 MB", api.fmtSize(2 * 1024 * 1024));
  check("500 Bytes", api.fmtSize(500) === "500 Bytes", api.fmtSize(500));

  console.log("\n=== Agents-Reiter aufbauen ===");
  // Die gebauten Pakete liegen nur vor, wenn der Watcher laeuft. Der ist
  // ein eigener, privilegierter Prozess und gehoert nicht zum Testaufbau.
  // Ohne ihn hat der Befehl keinen echten Dateinamen - das ist dann keine
  // Abweichung im Frontend, sondern eine leere Umgebung.
  let pkgs = null;
  try {
    const probe = await fetch(`http://127.0.0.1:${PORT}/api/v1/packages`,
                              {headers: {"X-Session": SESSION}});
    const body = await probe.text();
    console.log("Direkter Abruf:", probe.status, body.slice(0,80));
    try { pkgs = JSON.parse(body); } catch(e){ pkgs = null; }
  } catch(e){ console.log("Direkter Abruf scheiterte:", e.message); }

  // Der Reiter ist Administratoren vorbehalten.
  api.setMe({ username: "admin", is_admin: true });
  await api.loadAgentsTab();
  // Der Linux-Befehl entsteht erst mit einem Installations-Token. Ohne das
  // waere er hier leer, und die folgenden Pruefungen meldeten einen Fehler,
  // den es nicht gibt. Ob er ohne Token wirklich leer bleibt, prueft der
  // Abschnitt "Installationsbefehl" weiter unten.
  await api.makeInstallToken();

  const lin = el("cmdLinux").textContent;
  const win = el("cmdWin").textContent;
  const ver = el("agVer").textContent;

  check("Linux-Zeile gefuellt", el("infoLinux").textContent.length > 0,
        el("infoLinux").textContent);
  check("Windows-Zeile gefuellt", el("infoWin").textContent.length > 0,
        el("infoWin").textContent);
  check("Linux-Befehl enthaelt curl", lin.includes("curl"));
  check("Linux-Befehl enthaelt Paketnamen", lin.includes(".deb"));
  // Ohne eingetragene oeffentliche Adresse gilt die Adresse des Aufrufs.
  check("Linux-Befehl enthaelt Serveradresse", lin.includes(`127.0.0.1:${PORT}`));
  // Gesetzt wird innerHTML - die Attrappe haelt beide Felder getrennt,
  // textContent bliebe leer.
  check("Adresse wird ueber dem Befehl angezeigt",
        el("agBase").innerHTML.includes(`127.0.0.1:${PORT}`),
        el("agBase").innerHTML.slice(0, 60));
  check("Linux-Befehl enthaelt ein Token", /X-Install-Token: \S{20,}/.test(lin));
  // Auf den Kopfzeilennamen pruefen, nicht auf den Wert. Der API-Key ist
  // entfallen; die Pruefung bleibt, damit er nicht ueber einen alten
  // Codepfad zurueckkehrt.
  check("Linux-Befehl nutzt NICHT den API-Key", !lin.includes("X-API-Key"));
  check("und auch nicht die Sitzung",
        !SESSION || !lin.includes(SESSION));
  check("Windows-Befehl enthaelt msiexec", win.includes("msiexec"));
  check("Windows-Befehl enthaelt .msi", win.includes(".msi"));
  check("Windows-Befehl enthaelt CO37SERVER", win.includes("CO37SERVER"));
  check("kein Befehl leer", lin.trim().length > 20 && win.trim().length > 20);
  check("keine Fehlermeldung im Befehl", !lin.includes("Kein ") && !win.includes("Kein "));
  check("Agent-Version angezeigt", ver.length > 0 && ver !== "—", ver);

  if (pkgs && pkgs.linux) {
    check("Befehl nutzt echten Dateinamen",
          lin.includes(el("infoLinux").textContent.split(" ")[0]),
          el("infoLinux").textContent.split(" ")[0]);
  } else {
    console.log("      (kein Linux-Paket gebaut, uebersprungen)");
  }
  check("Warnhinweis bei Abweichung sichtbar",
        el("pkgWarn").innerHTML.length > 0 || !el("infoLinux").textContent.includes("⚠"));

  console.log("\n=== Paket-Routen ===");
  // 409 ist bei build gueltig: eine noch unbearbeitete Anforderung liegt vor.
  // Entscheidend ist, dass kein 405 kommt - das hiesse, die Route fehlt und
  // der Aufruf landet in der GET-Route /api/v1/packages/{name}.
  for (const [meth, path, want] of [
      ["POST", "/api/v1/packages/build", [200, 409]],
      ["GET",  "/api/v1/packages/build-status", [200]],
      ["GET",  "/api/v1/packages", [200]]]) {
    const r = await fetch(`http://127.0.0.1:${PORT}${path}`,
                          {method: meth, headers: {"X-Session": SESSION}});
    check(`${meth} ${path}`, want.includes(r.status), String(r.status));
  }

  console.log("\n=== Kopieren ohne HTTPS ===");
  let copied = null;
  document.execCommand = () => { copied = "aufgerufen"; return true; };
  await api.copy(lin, "Linux-Befehl");
  check("Rueckfallweg wurde genutzt", copied === "aufgerufen");
  check("Befehl mit && verkettet", lin.includes("&&"), lin.split("\n")[1] || "");
  await api.copy("", "Leer");
  check("leerer Text wird abgefangen", true);

  console.log("\n=== Neustart-Dialog ===");
  // Host vortaeuschen, damit rebootHost etwas findet
  const hostsResp = await fetch(`http://127.0.0.1:${PORT}/api/v1/hosts`,
                                {headers: {"X-Session": SESSION}});
  const hosts = await hostsResp.json();
  if (hosts.length) {
    api.setHosts ? api.setHosts(hosts) : null;
    try {
      api.rebootHost(hosts[0].id);
      check("Dialog gefuellt", el("rbTitle").textContent.length > 0, el("rbTitle").textContent);
      check("Nachlauf Vorgabe 10", el("rbGrace").value === 10 || el("rbGrace").value === "10",
            String(el("rbGrace").value));
      check("Downtime-Haken gesetzt", el("rbDowntime").checked === true);
      check("Downtime-Dauer vorbelegt", Number(el("rbDtMin").value) > 0, String(el("rbDtMin").value));
      check("Warnhinweis vorhanden", el("rbWarn").innerHTML.length > 0);
    } catch(e){ check("rebootHost laeuft durch", false, e.message); }
  } else {
    console.log("      (kein Host vorhanden, uebersprungen)");
  }

  console.log("\n=== Zeitzonen ===");
  {
    const r = await fetch(`http://127.0.0.1:${PORT}/api/v1/hosts`,
                          {headers: {"X-Session": SESSION}});
    const hosts = await r.json();
    const stamps = [];
    for (const h of hosts)
      for (const k of ["enrolled_at","last_seen","last_scan","next_patch_run","last_patch_run"])
        if (h[k]) stamps.push([k, h[k]]);
    const bad = stamps.filter(([, v]) => !/([+-]\d\d:\d\d|Z)$/.test(v));
    check("alle Zeitstempel mit Zeitzone", bad.length === 0,
          bad.length ? bad[0].join("=") : `${stamps.length} geprüft`);

    // Das Frontend darf kein "Z" mehr selbst anhaengen
    // Der Code steht in app.js, nicht mehr im HTML.
    const src2 = appjs;
    check("kein manuelles Z im Frontend", !src2.includes('+ "Z"'));
    // Zeitstempel duerfen nicht als Zeichenkette zerschnitten werden -
    // das zeigt UTC statt Ortszeit.
    check("keine roh zerschnittenen Zeitstempel",
          !/\.replace\("T"," "\)/.test(src2) && !/_at \|\| ""\)\.slice/.test(src2));
    check("fmtTime wird verwendet", (src2.match(/fmtTime\(/g) || []).length >= 8,
          (src2.match(/fmtTime\(/g) || []).length + " Aufrufe");

    if (stamps.length) {
      const d = new Date(stamps[0][1]);
      check("Zeitstempel lesbar", !isNaN(d.getTime()), d.toISOString());
    }
  }

  console.log("\n=== Zeitzonenstrenge (Backend) ===");
  {
    const fs3 = require("fs");
    const files = ["backend/main.py","backend/models.py","backend/update_manager.py"];
    let naive = [];
    for (const f of files) {
      let src;
      try { src = fs3.readFileSync(f, "utf8"); } catch(e){ continue; }
      // datetime.utcnow() ist veraltet und liefert zeitzonenlose Werte
      if (/datetime\.utcnow\(\)/.test(src)) naive.push(f + ": datetime.utcnow()");
      if (/datetime\.utcfromtimestamp\(/.test(src)) naive.push(f + ": utcfromtimestamp");
    }
    check("keine zeitzonenlosen Zeitfunktionen", naive.length === 0,
          naive.join(", ") || "geprüft: " + files.length + " Dateien");

    let models;
    try { models = fs3.readFileSync("backend/models.py","utf8"); } catch(e){ models = ""; }
    const stamps = (models.match(/(?:_at|_seen|_scan|_run):\s*(?:Optional\[)?datetime/g) || []).length;
    const typed = (models.match(/UTCDateTime/g) || []).length;
    check("alle Zeitfelder mit strengem Typ", typed >= stamps,
          `${typed} Spalten / ${stamps} Felder`);
  }

  console.log("\n=== Agent-Updates ===");
  try {
    await api.loadRollout();
    check("Pilot-Auswahl gefuellt", el("roPilot").innerHTML.includes("<option"),
          (el("roPilot").innerHTML.match(/<option/g) || []).length + " Einträge");
    check("Vorgehen gesetzt", ["staged","all"].includes(el("roMode").value),
          String(el("roMode").value));
    check("Zustand angezeigt", el("roState").textContent.length > 0,
          el("roState").textContent);
    check("Hinweistext vorhanden", el("roHint").innerHTML.length > 20);
  } catch(e){ check("loadRollout laeuft durch", false, e.message); }

  console.log("\n=== Neustart-Haekchen beim Anlegen ===");
  {
    // Ein Administrator hat das Recht ueber seine Rolle - das Haekchen
    // daneben taete nichts und behauptete das Gegenteil. Beim Umschalten
    // auf "Administrator" muss es also verschwinden.
    const rolle = el("nuRole"), zeile = el("nuRebootRow"),
          hinweis = el("nuRebootHint"), haken = el("nuReboot");

    rolle.value = "user";
    api.nuRechteAnzeigen();
    check("bei 'Benutzer' ist die Zeile sichtbar", zeile.style.display !== "none",
          zeile.style.display);
    check("und der Hinweis auch", hinweis.style.display !== "none",
          hinweis.style.display);

    haken.checked = true;
    rolle.value = "admin";
    api.nuRechteAnzeigen();
    check("bei 'Administrator' ist die Zeile weg", zeile.style.display === "none",
          zeile.style.display);
    check("und der Hinweis ebenso", hinweis.style.display === "none",
          hinweis.style.display);
    check("ein gesetztes Haekchen wird dabei zurueckgenommen",
          haken.checked === false, haken.checked);

    rolle.value = "user";
    api.nuRechteAnzeigen();
    check("zurueck auf 'Benutzer' ist sie wieder da",
          zeile.style.display !== "none", zeile.style.display);
  }

  console.log("\n=== Meldungen bei offenem Dialog ===");
  {
    // Ein mit showModal() geoeffneter Dialog liegt im obersten Fenster des
    // Browsers - darueber kommt kein z-index. Eine Meldung am body haengt
    // damit dahinter und ist nicht lesbar. Genau so ist die Begruendung
    // fuer ein abgewiesenes Update untergegangen.
    const angehaengt = [];
    const dialog = { appendChild: (e) => angehaengt.push(e), remove(){} };
    DIALOGE_OFFEN.length = 0;
    DIALOGE_OFFEN.push(dialog);

    api.toast("Testmeldung", true);
    check("Meldung landet im offenen Dialog", angehaengt.length === 1,
          angehaengt.length);
    check("Fehlermeldung ist als solche gekennzeichnet",
          (angehaengt[0] || {}).className === "toast err",
          (angehaengt[0] || {}).className);

    DIALOGE_OFFEN.length = 0;
    api.toast("Ohne Dialog");
    check("ohne offenen Dialog bleibt es beim body", angehaengt.length === 1,
          angehaengt.length);

    // Die Gestaltung des popover-Wegs muss zurueckgenommen sein, sonst
    // erscheint die Meldung mittig statt unten rechts.
    check("popover-Vorgaben ueberschrieben",
          /\.toast\[popover\]\{[^}]*inset:auto/.test(html));
  }

  console.log("\n=== Skripte werden auch wirklich eingebunden ===");
  {
    // Dieser Test haengt beide Dateien aneinander und fuehrt sie zusammen
    // aus. Fehlte im Markup die Zeile fuer i18n.js, liefe der Test trotzdem
    // durch und der Browser zeigte eine Oberflaeche ohne Uebersetzungen -
    // eine Abweichung, die genau hier verborgen bliebe.
    const skripte = [...html.matchAll(/<script src="([^"]+)"/g)].map(m => m[1]);
    check("i18n.js ist eingebunden", skripte.includes("i18n.js"), skripte.join(", "));
    check("app.js ist eingebunden", skripte.includes("app.js"), skripte.join(", "));
    check("i18n.js steht vor app.js",
          skripte.indexOf("i18n.js") < skripte.indexOf("app.js"),
          skripte.join(", "));
  }

  console.log("\n=== Sprachfelder zeigen den echten Stand ===");
  {
    // Ein Auswahlfeld, das nie gefuellt wird, zeigt immer den ersten
    // Eintrag - also 'English', egal was gespeichert ist. Wer es dann
    // anfasst, setzt still Englisch statt dessen, was er sieht.
    api.setzeSprache("de");
    api.setVorgabeSprache("en");
    api.spracheImDialogZeigen();
    check("eigene Sprache steht im Feld", el("spMeine").value === "de",
          el("spMeine").value);
    check("Vorgabe der Installation steht im Feld",
          el("spVorgabe").value === "en", el("spVorgabe").value);

    // Der Fall, der den Fehler ausgemacht hat: beide unterschiedlich, und
    // die Vorgabe ist nicht der erste Eintrag der Liste.
    api.setzeSprache("en");
    api.setVorgabeSprache("de");
    api.spracheImDialogZeigen();
    check("beide Felder folgen unabhaengig voneinander",
          el("spMeine").value === "en" && el("spVorgabe").value === "de",
          `${el("spMeine").value} / ${el("spVorgabe").value}`);
  }

  console.log("\n=== Server nicht erreichbar ===");
  {
    // Waehrend eines Systemupdates startet das Backend neu und der Proxy
    // antwortet 502. Mehrere Zeitgeber fragen gleichzeitig weiter; frueher
    // gab jede fehlgeschlagene Abfrage eine eigene Kurzmeldung. Der
    // wichtigste Fall hier ist der, der NICHT ausloesen darf.
    const echtesFetch = global.fetch;
    const bar = el("offlineBar");
    const meldungen = [];
    DIALOGE_OFFEN.length = 0;
    DIALOGE_OFFEN.push({ appendChild: (e) => meldungen.push(e), remove(){} });

    const antwortet = (status, koerper) => {
      global.fetch = async () => ({
        status, ok: status >= 200 && status < 300,
        text: async () => koerper || "",
        json: async () => (koerper ? JSON.parse(koerper) : {}),
      });
    };
    const ruf = () => api.apiCall("GET", "/api/v1/hosts").catch(() => {});

    // --- der Fall, der nicht ausloesen darf ---
    antwortet(502, "Bad Gateway");
    api.setOfflineSeit(null); api.setUpdateLaeuft(false);
    bar.style.display = "none";
    await ruf();
    check("502 gibt keine Kurzmeldung", meldungen.length === 0, meldungen.length);
    check("502 blendet nicht sofort die Leiste ein",
          bar.style.display === "none", bar.style.display);
    check("502 wird als Erreichbarkeitsfehler vermerkt",
          api.offlineSeit() !== null);

    // --- haelt es an, muss es sichtbar werden ---
    api.setOfflineSeit(Date.now() - 25000);
    await ruf();
    check("nach 25 s Ausfall erscheint die Leiste",
          bar.style.display === "flex", bar.style.display);
    check("auch dann keine Kurzmeldung", meldungen.length === 0, meldungen.length);

    // --- waehrend eines Updates laenger stillhalten ---
    api.setUpdateLaeuft(true);
    api.setOfflineSeit(Date.now() - 25000);
    bar.style.display = "none";
    await ruf();
    check("waehrend eines Updates bleibt es bei 25 s still",
          bar.style.display === "none", bar.style.display);
    api.setOfflineSeit(Date.now() - 200000);
    await ruf();
    check("waehrend eines Updates erscheint sie nach 200 s doch",
          bar.style.display === "flex", bar.style.display);

    // --- gar keine Antwort: fetch selbst scheitert ---
    global.fetch = async () => { throw new TypeError("Failed to fetch"); };
    api.setUpdateLaeuft(false); api.setOfflineSeit(null);
    bar.style.display = "none";
    await ruf();
    check("Totalausfall gibt ebenfalls keine Kurzmeldung",
          meldungen.length === 0, meldungen.length);
    check("Totalausfall zaehlt als Erreichbarkeitsfehler",
          api.offlineSeit() !== null);

    // --- Erholung ---
    antwortet(200, "{}");
    api.setOfflineSeit(Date.now() - 200000);
    bar.style.display = "flex";
    await api.apiCall("GET", "/api/v1/hosts");
    check("erste Antwort nimmt die Leiste wieder weg",
          bar.style.display === "none", bar.style.display);
    check("Zaehler ist zurueckgesetzt", api.offlineSeit() === null);

    // --- Anwendungsfehler muss weiterhin sofort melden ---
    antwortet(400, JSON.stringify({ detail: "Kaputte Eingabe" }));
    meldungen.length = 0;
    bar.style.display = "none";
    await ruf();
    check("Anwendungsfehler meldet weiterhin sofort",
          meldungen.length === 1, meldungen.length);
    check("Anwendungsfehler blendet keine Leiste ein",
          bar.style.display === "none", bar.style.display);
    check("Anwendungsfehler zaehlt nicht als Erreichbarkeitsfehler",
          api.offlineSeit() === null);

    global.fetch = echtesFetch;
    DIALOGE_OFFEN.length = 0;
    api.setOfflineSeit(null); api.setUpdateLaeuft(false);
    bar.style.display = "none";
  }

  console.log("\n=== Einstellungsdialog ===");
  {
    // Acht Reiter passen bei den 600px der uebrigen Dialoge nicht in eine
    // Zeile. Kommt ein neunter dazu, faellt es sonst erst im Betrieb auf.
    const anzahl = (html.match(/data-tab="\w+"/g) || []).length;
    const breite = html.match(/dialog#dlgSettings\{width:min\((\d+)px/);
    check("Einstellungsdialog hat eine eigene Breite", !!breite,
          breite && breite[1] + "px");

    // Ueberschlag: Beschriftungen bei 12px halbfett, plus Innenabstaende,
    // Abstaende und Rand.
    // Zwischen data-tab und dem Tag-Ende koennen weitere Attribute
    // stehen - bei zweien steht class dahinter statt davor.
    const labels = [...html.matchAll(/data-tab="\w+"[^>]*>([^<]+)</g)]
                   .map(m => m[1]);
    const geschaetzt = labels.join("").length * 6.8 + labels.length * 28
                     + (labels.length - 1) * 2 + 36;
    check("Reiter passen rechnerisch in eine Zeile",
          !!breite && geschaetzt < Number(breite[1]),
          `${Math.round(geschaetzt)}px bei ${breite && breite[1]}px`);
    check("alle Reiter gefunden", labels.length === anzahl, labels.length);

    check("Fliesstexte im Dialog sind begrenzt",
          /dialog#dlgSettings \.hint\{max-width:\d+ch\}/.test(html));
  }

  console.log("\n=== Schriften ===");
  {
    const fsf = require("fs"), pathf = require("path");
    const dir = pathf.join(pathf.dirname(HTML_PATH), "fonts");

    // Auf tatsaechliche Verweise pruefen, nicht auf blosse Erwaehnung: im
    // Kommentar daneben steht, warum die Schriften nicht mehr von dort
    // kommen. Ein Treffer im Fliesstext waere ein falscher Alarm.
    const verweise = html.match(
      /(?:src|href)\s*=\s*["'][^"']*fonts\.(?:googleapis|gstatic)\.com[^"']*["']/g)
      || html.match(/url\(\s*['"]?https?:\/\/[^)]*fonts\.(?:googleapis|gstatic)/g)
      || [];
    check("keine fremde Schriftquelle im Markup", verweise.length === 0, verweise);

    // Jede im Stylesheet verwendete Datei muss auch vorhanden sein. Fehlt
    // eine, faellt der Browser stillschweigend auf eine Ersatzschrift
    // zurueck - man sieht es, aber man sucht es nicht dort.
    const benutzt = [...html.matchAll(/url\('fonts\/([^']+)'\)/g)].map(m => m[1]);
    check("Schriften im Stylesheet eingebunden", benutzt.length === 6, benutzt.length);
    const fehlend = benutzt.filter(f => !fsf.existsSync(pathf.join(dir, f)));
    check("alle eingebundenen Dateien vorhanden", fehlend.length === 0, fehlend);

    // Und umgekehrt: eine Datei, die niemand einbindet, ist toter Ballast.
    let dabei = [];
    try { dabei = fsf.readdirSync(dir).filter(f => f.endsWith(".woff2")); } catch(e){}
    const unbenutzt = dabei.filter(f => !benutzt.includes(f));
    check("keine unbenutzte Schriftdatei", unbenutzt.length === 0, unbenutzt);

    check("Lizenztexte liegen bei",
          fsf.existsSync(pathf.join(dir, "OFL-Archivo.txt"))
          && fsf.existsSync(pathf.join(dir, "OFL-JetBrainsMono.txt")));
  }

  console.log("\n=== Klick-Verteiler ===");
  {
    // Seit die Klicks ueber data-act laufen, ist ein Tippfehler im Namen
    // nicht mehr sichtbar: der Knopf tut dann einfach nichts. Frueher waere
    // im onclick ein Funktionsname gestanden, den man beim Lesen bemerkt.
    // Markup und Skript liegen getrennt - beides einzeln pruefen.
    const src = appjs;
    const known = new Set(
      [...src.matchAll(/^\s{2}(\w+):\s*\(d\)\s*=>/gm)].map(m => m[1]));
    check("Aktionen gefunden", known.size >= 10, [...known].length);

    const used = [...src.matchAll(/data-act="(\w+)"/g)].map(m => m[1]);
    check("data-act im Markup vorhanden", used.length > 0, used.length);
    const fehlend = [...new Set(used)].filter(a => !known.has(a));
    check("jedes data-act hat eine Aktion", fehlend.length === 0, fehlend);

    // Und umgekehrt: eine Aktion ohne Verwendung ist toter Code.
    const unbenutzt = [...known].filter(a => !used.includes(a));
    check("keine Aktion ohne Verwendung", unbenutzt.length === 0, unbenutzt);

    // Weder im statischen Markup noch in den Vorlagen, die das Skript
    // erzeugt. Ein einziges onclick wuerde von der Regel blockiert - der
    // Knopf taete dann nichts, ohne sichtbaren Fehler.
    check("keine onclick-Attribute im Markup",
          !/onclick="/.test(html), (html.match(/onclick="/g) || []).length);
    check("keine onclick-Attribute im Skript",
          !/onclick="/.test(appjs), (appjs.match(/onclick="/g) || []).length);
    check("Skript liegt in einer eigenen Datei",
          /<script src="app\.js"/.test(html));
    check("kein Inline-Skriptblock mehr",
          !/<script>/.test(html));
  }

  console.log("\n=== Kopfzeile ===");
  {
    // Die Kopfzeile wird in der schmalen Ansicht nicht erzeugt. Fruehere
    // Fassungen blendeten sie nur per CSS aus - taucht sie dort trotzdem
    // auf, ueberdeckt sie den Hostnamen.
    global.matchMedia = () => ({ matches: true, addEventListener(){} });
    api.renderRackHead();
    check("schmale Ansicht ohne Kopfzeile", el("rackHead").innerHTML === "",
          el("rackHead").innerHTML.slice(0, 60));

    global.matchMedia = () => ({ matches: false, addEventListener(){} });
    // Nicht auf ein deutsches Wort festnageln: seit 0.35.x kommt die
    // Beschriftung aus dem Woerterbuch, und die Vorgabe ist Englisch. Ein
    // fester Anker haette hier nur gemeldet, dass sich die Sprache
    // geaendert hat - nicht, ob die Kopfzeile funktioniert. Also beide
    // Sprachen pruefen; das prueft zugleich den Weg durch t().
    api.setzeSprache("en");
    api.renderRackHead();
    check("breite Ansicht mit Kopfzeile, englisch",
          el("rackHead").innerHTML.includes("Security"),
          el("rackHead").innerHTML.slice(0, 80));
    api.setzeSprache("de");
    api.renderRackHead();
    check("breite Ansicht mit Kopfzeile, deutsch",
          el("rackHead").innerHTML.includes("Sicherheit"),
          el("rackHead").innerHTML.slice(0, 80));
    api.setzeSprache("en");
  }

  console.log("\n=== Spaltenausrichtung ===");
  {
    const fs = require("fs");
    // Das Stylesheet steht weiter im HTML, die Vorlagen im Skript.
    const cols = html.match(/\.rack-head,\.unit\{display:grid;[\s\S]*?grid-template-columns:([^;]+);/)[1].trim().split(/\s+/);
    const src = appjs;
    const countCells = (frag) => {
      let depth = 0, n = 0;
      for (const m of frag.matchAll(/<\/?div\b/g)) {
        if (m[0] === "<div") { if (depth === 0) n++; depth++; }
        else { depth--; if (depth < 0) break; }
      }
      return n;
    };
    // Die Kopfzeile steht nicht mehr im Markup: sie wird aus RACK_COLUMNS
    // aufgebaut und in der schmalen Ansicht gar nicht erst erzeugt. Also
    // gegen die Liste pruefen statt gegen festes HTML.
    const colsSrc = src.match(/const RACK_COLUMNS = \[([\s\S]*?)\];/);
    if (!colsSrc) throw new Error("RACK_COLUMNS nicht gefunden");
    const head = colsSrc[1].split(",").filter(x => x.trim()).length;
    // Anker ohne festes Tag-Ende: die Zeile traegt inzwischen Attribute
    // (data-id fuer das Verschieben per Drag and Drop). Ein Literal mit
    // '>' am Schluss faende die Stelle nicht mehr und zaehlte still null
    // Zellen - der Test meldete dann einen Fehler im Markup, obwohl nur
    // sein eigener Anker veraltet war.
    const rowMatch = src.match(/return `<div class="unit"[^>]*>/);
    if (!rowMatch) throw new Error("Datenzeile im Markup nicht gefunden");
    const rowStart = rowMatch.index;
    const rowHtml = src.slice(rowStart, src.indexOf("</div>`;", rowStart));
    // Hinter das oeffnende Tag der Zeile springen, damit dieses nicht als
    // eigene Zelle mitgezaehlt wird.
    const row = countCells(rowHtml.slice(rowMatch[0].length));

    check("Kopf- und Datenzeile gleich viele Zellen", head === row, `${head} / ${row}`);
    check("Zellen passen zur Spaltenzahl", head === cols.length, `${head} / ${cols.length}`);
    // Zwei Quellen fuer dieselbe Umbruchbreite - sie muessen zusammenpassen,
    // sonst erzeugt das JavaScript eine Kopfzeile, die das CSS als Karte
    // formatiert (oder umgekehrt).
    // Die Konstante steht im Skript, die Medienabfrage im Stylesheet -
    // seit der Trennung zwei Dateien. Genau deshalb bleibt die Pruefung
    // wichtig: auseinanderlaufen wuerde jetzt niemandem mehr auffallen.
    const q = appjs.match(/const RACK_NARROW_QUERY = "([^"]+)"/);
    check("Umbruchbreite in JS und CSS gleich",
          !!q && html.includes("@media" + q[1]), q && q[1]);

    check("keine auto-Spalte (verschiebt die Ausrichtung)",
          !cols.includes("auto"), cols.join(" "));
  }

  console.log("\n=== Bereiche in der Liste ===");
  {
    // Rein synthetische Hosts/Bereiche statt echter Daten vom Backend -
    // die Gruppierung soll unabhaengig vom sonstigen Bestand geprueft
    // werden, und ohne angelegte Bereiche wuerde dieser Test nichts zu
    // pruefen finden.
    const mkHost = (id, overrides) => Object.assign({
      id, hostname: `H${id}`, display_name: null,
      approval_state: "approved", status: "online",
      os_type: "linux", updates_available: 0, security_updates: 0,
      checkmk_downtime_all: false, checkmk_hosts: [], downtime_minutes: 30,
      reboot_required: false, patch_enabled: false, patch_followup_left: 0,
      updates_require_reboot: false, area_id: null,
    }, overrides);

    const areaA = { id: 90501, name: "Buero-A", sort_order: 1, host_count: 0 };
    const areaB = { id: 90502, name: "Leerer-Bereich", sort_order: 2, host_count: 0 };
    const h1 = mkHost(90001, { area_id: areaA.id });
    const h2 = mkHost(90002, { area_id: areaA.id });
    const h3 = mkHost(90003, {});   // ohne Bereich - bleibt wie bisher

    api.setAreas([areaA, areaB]);
    api.setHosts([h1, h2, h3]);
    el("filter").value = "";
    el("fState").value = "";
    api.render();

    const out = el("units").innerHTML;
    const posArea = out.indexOf(`data-area-id="${areaA.id}"`);
    const posEmptyArea = out.indexOf(`data-area-id="${areaB.id}"`);
    const posH1 = out.indexOf(`data-id="${h1.id}"`);
    const posH2 = out.indexOf(`data-id="${h2.id}"`);
    const posH3 = out.indexOf(`data-id="${h3.id}"`);

    check("Bereichs-Kopf gefunden", posArea >= 0, posArea);
    check("Bereichsname erscheint im Kopf",
          out.slice(posArea, posArea + 200).includes(areaA.name));
    check("Bereichs-Kopf steht vor seinen eigenen Hosts",
          posArea >= 0 && posArea < posH1 && posH1 < posH2, { posArea, posH1, posH2 });
    check("beide Hosts im Bereich sind als solche markiert",
          out.slice(Math.max(0, posH1 - 40), posH1).includes('data-in-area="1"')
          && out.slice(Math.max(0, posH2 - 40), posH2).includes('data-in-area="1"'));
    check("Host ohne Bereich bleibt unmarkiert",
          !out.slice(Math.max(0, posH3 - 40), posH3).includes('data-in-area="1"'));
    // Seit der Rueckmeldung zu Schritt 5: bereichslose Hosts stehen ganz
    // oben, vor jedem Bereichs-Kopf - nicht mehr danach.
    check("Host ohne Bereich steht VOR dem ersten Bereichs-Kopf",
          posH3 >= 0 && posH3 < posArea, { posH3, posArea });
    check("leerer Bereich bleibt sichtbar (Ablageziel) ohne aktiven Filter",
          posEmptyArea >= 0, posEmptyArea);

    // Aktiver Filter, der nur H1 trifft: der leere Bereich ist jetzt eine
    // Karteileiche und soll nicht erscheinen - sonst zeigt die Suche einen
    // Bereich, zu dem gerade kein Treffer gehoert.
    el("filter").value = "h" + h1.id;
    api.render();
    const outGefiltert = el("units").innerHTML;
    check("gefiltert: leerer Bereich verschwindet",
          !outGefiltert.includes(`data-area-id="${areaB.id}"`));
    check("gefiltert: der treffende Bereich mit seinem Treffer bleibt",
          outGefiltert.includes(`data-area-id="${areaA.id}"`)
          && outGefiltert.includes(`data-id="${h1.id}"`));
    check("gefiltert: der nicht treffende zweite Host im selben Bereich fehlt",
          !outGefiltert.includes(`data-id="${h2.id}"`));
    el("filter").value = "";

    // Ohne jeden Bereich muss die Anzeige unveraendert bleiben - das war
    // die ausdrueckliche Bedingung: wer keine Bereiche will, braucht auch
    // keine anzulegen.
    api.setAreas([]);
    api.render();
    const outOhne = el("units").innerHTML;
    check("ohne Bereiche: keine Kopfzeile im Markup", !outOhne.includes("areahead"));
    check("ohne Bereiche: kein Host als eingerueckt markiert",
          !outOhne.includes("data-in-area"));
  }

  console.log("\n=== Bereiche: Ablegen berechnen (applyDrop) ===");
  {
    // Reine Rechenfunktion, keine DOM - direkt gegen synthetische Listen
    // geprueft, wie patch_due() im Backend gegen synthetische Hosts.
    const liste = () => [
      { id: 1, area_id: null },
      { id: 2, area_id: 777 },
      { id: 3, area_id: 777 },
      { id: 4, area_id: null },
    ];

    {
      const r = api.applyDrop(liste(), 1, { kind: "unit", id: 2, after: false });
      check("Ablegen vor einem Host im Bereich uebernimmt dessen Bereich",
            r && r.hosts.find(h => h.id === 1).area_id === 777, r && r.newAreaId);
      check("Bereichswechsel wird gemeldet", r && r.areaChanged === true);
      check("Reihenfolge: Host 1 steht jetzt vor Host 2",
            r && r.hosts.findIndex(h => h.id === 1) < r.hosts.findIndex(h => h.id === 2),
            r && r.hosts.map(h => h.id));
    }
    {
      const r = api.applyDrop(liste(), 2, { kind: "unit", id: 4, after: true });
      check("Ablegen bei einem Host ohne Bereich entfernt aus dem Bereich",
            r && r.hosts.find(h => h.id === 2).area_id === null);
      check("Bereichswechsel wird auch beim Entfernen gemeldet", r && r.areaChanged === true);
    }
    {
      const r = api.applyDrop(liste(), 2, { kind: "unit", id: 3, after: true });
      check("reine Umsortierung im selben Bereich meldet KEINE Aenderung",
            r && r.areaChanged === false, r);
      check("Reihenfolge trotzdem angepasst",
            r && r.hosts.findIndex(h => h.id === 2) > r.hosts.findIndex(h => h.id === 3));
    }
    {
      const r = api.applyDrop(liste(), 4, { kind: "areahead", areaId: 777 });
      check("Ablegen auf dem Bereichs-Kopf setzt den Bereich",
            r && r.hosts.find(h => h.id === 4).area_id === 777);
      check("... und haengt hinter die vorhandenen Hosts des Bereichs an",
            r && r.hosts[r.hosts.length - 1].id === 4, r && r.hosts.map(h => h.id));
    }
    {
      // Bereich 888 hat in dieser Liste noch keinen einzigen Host - der
      // Zweig ohne "letzten Host des Bereichs" (lastIdx bleibt -1).
      const r = api.applyDrop(liste(), 1, { kind: "areahead", areaId: 888 });
      check("Ablegen auf dem Kopf eines LEEREN Bereichs setzt den Bereich trotzdem",
            r && r.hosts.find(h => h.id === 1).area_id === 888);
      check("... und haengt einfach hinten an, mangels vorhandener Hosts dort",
            r && r.hosts[r.hosts.length - 1].id === 1, r && r.hosts.map(h => h.id));
    }
    {
      const r = api.applyDrop(liste(), 9, { kind: "unit", id: 2, after: false });
      check("Ziehen eines nicht (mehr) vorhandenen Hosts liefert kein Ergebnis",
            r === null, "unbekannte dragId - kein Ergebnis erwartet");
    }
    {
      const r = api.applyDrop(liste(), 2, { kind: "unit", id: 2, after: false });
      check("Ablegen auf sich selbst liefert kein Ergebnis", r === null);
    }
    {
      // Ablagezone vor dem ersten Bereichs-Kopf (Rueckmeldung Schritt 5,
      // Punkt 1): setzt den Bereich auf "keiner", unabhaengig davon, wo der
      // Host vorher stand.
      const r = api.applyDrop(liste(), 2, { kind: "beforeFirstArea" });
      check("Ablegen vor dem ersten Bereich entfernt aus dem Bereich",
            r && r.hosts.find(h => h.id === 2).area_id === null);
      check("... und meldet die Aenderung", r && r.areaChanged === true);
      check("... Host landet ganz vorn in der Liste",
            r && r.hosts[0].id === 2, r && r.hosts.map(h => h.id));
    }
    {
      // Schon bereichslos: keine Bereichsaenderung zu melden, auch wenn
      // die Position sich verschiebt.
      const r = api.applyDrop(liste(), 1, { kind: "beforeFirstArea" });
      check("Ablegen eines schon bereichslosen Hosts meldet keine Aenderung",
            r && r.areaChanged === false, r);
    }
  }

  console.log("\n=== Versionswechsel bemerken ===");
  try {
    await api.checkVersion();           // erster Lauf: merkt sich die Version
    const before = el("reloadBar").style.display;
    check("kein Hinweis bei gleicher Version", before !== "flex", String(before));
    api.setPageVersion("0.0.1");        // Wechsel vortaeuschen
    await api.checkVersion();
    check("Hinweis bei Versionswechsel", el("reloadBar").style.display === "flex",
          el("reloadText").textContent.slice(0, 60));
  } catch(e){ check("checkVersion laeuft durch", false, e.message); }

  console.log("\n=== Checkmk-Formular ===");
  await api.loadCmk();
  await api.loadCmkForm();
  check("Statuszeile gefuellt", el("cmkInfo").innerHTML.length > 0,
        el("cmkInfo").innerHTML.replace(/<[^>]+>/g, "").slice(0, 60));

  console.log("\n=== Benutzerverwaltung ===");
  api.setMe({username: "admin", role: "admin", is_admin: true});
  api.loadAccount();
  check("Kontoname gesetzt", el("acName").textContent === "admin",
        el("acName").textContent);
  check("Rolle im Klartext", el("acRole").textContent === "Administrator",
        el("acRole").textContent);

  await api.loadUsers();
  check("Benutzerliste gefuellt", el("userList").innerHTML.includes("admin"),
        el("userList").innerHTML.replace(/<[^>]+>/g, " ").slice(0, 70));
  check("eigener Zugang nicht entfernbar",
        el("userList").innerHTML.includes("disabled"));

  await api.loadAudit();
  check("Pruefprotokoll gefuellt", el("auditList").innerHTML.includes("login"),
        el("auditList").innerHTML.replace(/<[^>]+>/g, " ").slice(0, 70));

  console.log("\n=== Rollentrennung im Markup ===");
  {
    const src = html;
    for (const id of ["tabUsers", "tabAudit"]) {
      const re = new RegExp(`data-tab="${id}"[^>]*class="admin-only"`);
      check(`${id} ist als admin-only gekennzeichnet`, re.test(src));
    }
    // Der Lizenzstand ist fuer jeden lesbar: wer am Limit scheitert, soll
    // den Grund nachvollziehen koennen. Der Schluessel selbst wird nur
    // Administratoren zum Eintragen angeboten.
    check("tabLizenz ist NICHT admin-only",
          !/data-tab="tabLizenz"[^>]*admin-only/.test(html));
    check("Eingabe des Schluessels ist admin-only",
          /admin-only[^>]*>\s*(?:<[^>]+>\s*)*Lizenzschlüssel/.test(html)
          || html.includes('class="section admin-only"'));
    check("tabAccount ist NICHT admin-only",
          !/data-tab="tabAccount"[^>]*admin-only/.test(src));
  }

  console.log("\n=== Installationsbefehl ===");
  // Ohne erzeugtes Token darf gar kein Befehl dastehen - sonst koennte man
  // einen unvollstaendigen kopieren und wuerde den Fehler erst auf dem
  // Zielsystem bemerken.
  // Zustand wie beim Oeffnen des Dialogs herstellen: Token vergessen.
  api.forgetInstallToken();
  check("ohne Token kein Befehl",
        !el("cmdLinux").textContent.includes("curl"),
        el("cmdLinux").textContent.slice(0, 50));
  check("Kopieren ohne Token gesperrt", el("copyLinux").disabled === true);

  await api.makeInstallToken();
  const cmd = el("cmdLinux").textContent;
  // X-Install-Token, nicht die Sitzung: der Befehl wird auf einem fremden
  // Rechner ausgefuehrt und bleibt dort in der Verlaufsdatei stehen.
  check("Befehl nutzt ein Installations-Token",
        cmd.includes("X-Install-Token:"), cmd.slice(0, 70));
  check("Befehl enthaelt nicht den API-Key",
        !cmd.includes("X-API-Key:"), cmd.slice(0, 70));
  check("Token ist nicht leer",
        !/X-Install-Token:\s+["']?\s*(http|$)/.test(cmd), cmd.slice(0, 70));
  check("Kopieren jetzt moeglich", el("copyLinux").disabled === false);

  console.log("\n=== Bereiche in den Einstellungen ===");
  {
    // Eigener HTTP-Helfer statt api.apiCall(): der laeuft ueber das Cookie
    // des Browsers, hier geht die Sitzung wie im Rest der Suite als
    // Kopfzeile X-Session mit.
    const adminCall = async (path, opts = {}) => {
      const r = await fetch(`http://127.0.0.1:${PORT}${path}`, {
        headers: {"X-Session": SESSION, "Content-Type": "application/json"},
        method: opts.method || (opts.body ? "POST" : "GET"),
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const text = await r.text();
      let json = {};
      try { json = text ? JSON.parse(text) : {}; } catch(e){}
      return [r.status, json];
    };
    // Eigener Suffix je Lauf: die Testdatenbank traegt zu diesem Zeitpunkt
    // schon Bereiche aus der eigenstaendigen 'area'-Reihe. Diese hier
    // muessen sich nur eindeutig finden lassen, nicht allein sein.
    const suffix = Date.now();

    const [c1, areaA] = await adminCall("/api/v1/areas", { body: { name: `ST-Buero-${suffix}` } });
    const [c2, areaB] = await adminCall("/api/v1/areas", { body: { name: `ST-Lager-${suffix}` } });
    check("zwei Testbereiche angelegt", c1 === 200 && c2 === 200, [c1, c2]);

    await api.loadAreasTab();
    const list = el("areaList").innerHTML;
    check("beide im Reiter sichtbar",
          list.includes(`ST-Buero-${suffix}`) && list.includes(`ST-Lager-${suffix}`));

    const areasNachLaden = api.getAreas();
    const idxA0 = areasNachLaden.findIndex(a => a.id === areaA.id);
    const idxB0 = areasNachLaden.findIndex(a => a.id === areaB.id);
    check("frisch angelegt: B steht direkt hinter A", idxB0 === idxA0 + 1, { idxA0, idxB0 });

    const ersteId = areasNachLaden[0].id, letzteId = areasNachLaden[areasNachLaden.length-1].id;
    check("Auf-Knopf der ersten Zeile gesperrt",
          new RegExp(`data-act="areaup" data-id="${ersteId}"[^>]*disabled`).test(list));
    check("Ab-Knopf der letzten Zeile gesperrt",
          new RegExp(`data-act="areadown" data-id="${letzteId}"[^>]*disabled`).test(list));

    // 'Nach oben' fuer B - vertauscht A und B, auch serverseitig, nicht
    // nur in der lokalen Anzeige.
    await api.moveArea(areaB.id, -1);
    const [, geladenNachVerschieben] = await adminCall("/api/v1/areas");
    const idxA1 = geladenNachVerschieben.findIndex(a => a.id === areaA.id);
    const idxB1 = geladenNachVerschieben.findIndex(a => a.id === areaB.id);
    check("nach 'nach oben' stehen A und B vertauscht - auch auf dem Server",
          idxB1 === idxA1 - 1, { idxA1, idxB1 });

    // Bearbeiten-Dialog: Vorbelegung aus dem echten Bereich.
    await adminCall(`/api/v1/areas/${areaA.id}`, { method: "PATCH", body: {
      patch_enabled: true, patch_days: ["MO","MI"], patch_time: "04:30",
      patch_grace_hours: 6, downtime_minutes: 45,
    }});
    await api.loadAreasTab();
    api.editArea(areaA.id);
    check("Name vorbelegt", el("aName").value === `ST-Buero-${suffix}`, el("aName").value);
    check("editArea() merkt sich die bearbeitete Id", api.getEditAreaId() === areaA.id);
    check("Zeitplan-Haken vorbelegt", el("aUpEnabled").checked === true);
    check("Uhrzeit vorbelegt", el("aUpTime").value === "04:30", el("aUpTime").value);
    check("Kulanz vorbelegt", Number(el("aUpGrace").value) === 6, el("aUpGrace").value);
    check("Downtime vorbelegt", Number(el("aDowntime").value) === 45, el("aDowntime").value);
    check("Wochentage gezeichnet (mindestens ein hervorgehobener Tag)",
          el("aUpDays").innerHTML.includes("var(--accent)"));

    // Manuell pruefen/updaten - ein echter Host im Bereich, ueber die
    // Agent-API auf offene Updates gebracht wie ein wirklicher Agent es
    // taete, nicht am Datensatz vorbei.
    const [ce, enrollRes] = await adminCall("/api/v1/agent/enroll", { body: {
      hostname: `ST-AREA-HOST-${suffix}`, os_type: "linux",
      os_version: "Debian 13", agent_version: "0.35.4",
    }});
    check("Testhost fuer den Reiter angemeldet", ce === 200, ce);
    const token = enrollRes.agent_token;
    const [, hostsNow] = await adminCall("/api/v1/hosts");
    const hid = hostsNow.find(h => h.hostname === `ST-AREA-HOST-${suffix}`).id;
    await adminCall(`/api/v1/hosts/${hid}/approve`, { body: {} });
    await adminCall(`/api/v1/hosts/${hid}/area`, { body: { area_id: areaA.id } });

    const holeHosts = async () => {
      const [, hs] = await adminCall("/api/v1/hosts");
      api.setHosts(hs);
    };
    await holeHosts();

    // Noch ohne offene Updates: der Updaten-Knopf darf nichts anlegen.
    const jobsVorher = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    await api.patchArea(areaA.id);
    const jobsOhneUpdates = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    check("Updaten ohne offene Updates legt keinen Auftrag an",
          jobsOhneUpdates.length === jobsVorher.length,
          { vorher: jobsVorher.length, nachher: jobsOhneUpdates.length });

    // Jetzt einen offenen Update melden - wie ein Agent nach einem Scan.
    await fetch(`http://127.0.0.1:${PORT}/api/v1/agent/scan-result`, {
      method: "POST",
      headers: {"X-Agent-Token": token, "Content-Type": "application/json"},
      body: JSON.stringify({ updates: [{id: "pkg1", title: "Testpaket"}] }),
    });
    await holeHosts();

    await api.scanArea(areaA.id);
    const jobsNachPruefen = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    check("Pruefen legt einen scan-Auftrag fuer den Host im Bereich an",
          jobsNachPruefen.some(j => j.job_type === "scan"),
          jobsNachPruefen.map(j => j.job_type));

    await api.patchArea(areaA.id);
    const jobsNachUpdaten = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    const patchAuftrag = jobsNachUpdaten.find(j => j.job_type === "patch");
    check("Updaten legt jetzt einen patch-Auftrag an", !!patchAuftrag,
          jobsNachUpdaten.map(j => j.job_type));
    check("... mit der Herkunft des Bereichs in den Parametern - fuer die "
          + "Downtime bei einem spaeteren Neustart (Schritt 2)",
          patchAuftrag && patchAuftrag.params
          && patchAuftrag.params.source_area_id === areaA.id,
          patchAuftrag && patchAuftrag.params);
  }

  console.log("\n=== Rueckmeldung nach der Vorschau (Schritt 5) ===");
  {
    const mkHost = (id, overrides) => Object.assign({
      id, hostname: `S5-${id}`, display_name: null,
      approval_state: "approved", status: "online",
      os_type: "linux", updates_available: 0, security_updates: 0,
      checkmk_downtime_all: false, checkmk_hosts: [], downtime_minutes: 30,
      reboot_required: false, patch_enabled: false, patch_followup_left: 0,
      updates_require_reboot: false, area_id: null,
    }, overrides);

    // --- Punkt 1: bereichslose Hosts ganz oben, eigene Ablagezone nur bei
    // Bedarf. Rein im Speicher, wie bei "Bereiche in der Liste" oben. ---
    const areaX = { id: 90601, name: "S5-Bereich", sort_order: 1, host_count: 0 };
    const hostFrei = mkHost(90011, {});
    const hostInArea = mkHost(90012, { area_id: areaX.id });

    api.setAreas([areaX]);
    api.setHosts([hostInArea, hostFrei]);   // absichtlich "falsch herum" im Array
    el("filter").value = ""; el("fState").value = "";
    api.render();
    let out = el("units").innerHTML;
    const posFrei = out.indexOf(`data-id="${hostFrei.id}"`);
    const posArea = out.indexOf(`data-area-id="${areaX.id}"`);
    check("bereichsloser Host steht vor dem Bereichs-Kopf",
          posFrei >= 0 && posArea >= 0 && posFrei < posArea, { posFrei, posArea });
    check("keine eigene Ablagezone noetig - es gibt ja schon einen bereichslosen Host",
          !out.includes("areadrop-top"));

    api.setHosts([hostInArea]);   // jetzt gehoert ausnahmslos jeder Host einem Bereich an
    api.render();
    check("Ablagezone vor dem ersten Bereich erscheint, sobald kein Host mehr bereichslos ist",
          el("units").innerHTML.includes("areadrop-top"));
    api.setHosts([hostInArea, hostFrei]);   // Ausgangslage fuer den Rest wiederherstellen

    // --- Punkt 2: Aktionen direkt am Bereichs-Kopf ---
    const headAdmin = api.renderAreaHead(areaX, 3, true);
    check("Check-Knopf am Bereichs-Kopf",
          headAdmin.includes(`data-act="areascan" data-id="${areaX.id}"`));
    check("Patch-Knopf am Bereichs-Kopf",
          headAdmin.includes(`data-act="areapatch" data-id="${areaX.id}"`));
    check("Restart-Knopf am Bereichs-Kopf",
          headAdmin.includes(`data-act="areareboot" data-id="${areaX.id}"`));
    check("kein History-Knopf am Bereichs-Kopf - den gibt es nur pro Host",
          !headAdmin.includes('data-act="detail"'));
    check("'...'-Knopf fuer einen Administrator vorhanden",
          headAdmin.includes(`data-act="areaedit" data-id="${areaX.id}"`));
    const headUser = api.renderAreaHead(areaX, 3, false);
    check("'...'-Knopf OHNE Administratorrechte nicht vorhanden",
          !headUser.includes('data-act="areaedit"'));
    check("Check und Patch auch ohne Administratorrechte da",
          headUser.includes('data-act="areascan"')
          && headUser.includes('data-act="areapatch"'));

    // --- Neustart haengt seit 0.37.7 am Recht des Kontos, nicht an der
    // Rolle (F-22). Bis dahin durfte jedes angemeldete Konto auf jedem
    // Host neu starten - unter Umgehung von Wartungsfenster und
    // Neustartrichtlinie, weil create_job() dabei manual=True setzt.
    const merkeMe = api.getMe();
    api.setMe({ username: "b", role: "user", is_admin: false, may_reboot: false });
    const ohneRecht = api.renderAreaHead(areaX, 3, false);
    check("ohne Neustart-Recht kein Restart-Knopf am Bereichs-Kopf",
          !ohneRecht.includes('data-act="areareboot"'));
    check("Check und Patch bleiben trotzdem",
          ohneRecht.includes('data-act="areascan"')
          && ohneRecht.includes('data-act="areapatch"'));
    const ohneRechtHost = api.renderUnit(
      { id: 4711, hostname: "h", approval_state: "approved", os_type: "linux" },
      0, false);
    check("und auch keiner an der Hostzeile",
          !ohneRechtHost.includes('data-act="reboot"'));

    api.setMe({ username: "b", role: "user", is_admin: false, may_reboot: true });
    check("mit Neustart-Recht ist er da",
          api.renderAreaHead(areaX, 3, false).includes('data-act="areareboot"'));
    check("auch an der Hostzeile",
          api.renderUnit(
            { id: 4711, hostname: "h", approval_state: "approved",
              os_type: "linux" }, 0, false).includes('data-act="reboot"'));
    api.setMe(merkeMe);

    // Der Edit-Knopf in Settings -> Bereiche ist weg - Bearbeiten laeuft
    // jetzt ausschliesslich ueber den Bereichs-Kopf auf der Hauptseite.
    await api.loadAreasTab();
    check("Settings-Liste der Bereiche hat KEINEN Edit-Knopf mehr",
          !el("areaList").innerHTML.includes('data-act="areaedit"'),
          el("areaList").innerHTML.replace(/<[^>]+>/g, " ").slice(0, 120));

    // --- Punkt 3: Bereichs-Kopf farblich abgehoben. Quelltextpruefung -
    // die DOM-Attrappe wertet kein CSS aus. ---
    check("Bereichs-Kopf nutzt die Akzentfarbe statt der Flaeche von Kopfleiste/Spaltenkopf",
          /\.areahead\{[^}]*background:var\(--accent-bg\)/.test(html));

    // --- rebootArea(): echter Host ueber den Server, wie beim scan/patch-
    // Test oben. Nur Hosts mit tatsaechlichem Neustartbedarf werden erfasst. ---
    const adminCall = async (path, opts = {}) => {
      const r = await fetch(`http://127.0.0.1:${PORT}${path}`, {
        headers: {"X-Session": SESSION, "Content-Type": "application/json"},
        method: opts.method || (opts.body ? "POST" : "GET"),
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const text = await r.text();
      let json = {};
      try { json = text ? JSON.parse(text) : {}; } catch(e){}
      return [r.status, json];
    };
    const suffix = Date.now();
    const [, areaR] = await adminCall("/api/v1/areas", { body: { name: `S5-Reboot-${suffix}` } });
    const [ce, enrollRes] = await adminCall("/api/v1/agent/enroll", { body: {
      hostname: `S5-REBOOT-HOST-${suffix}`, os_type: "linux",
      os_version: "Debian 13", agent_version: "0.36.0",
    }});
    check("Testhost fuer rebootArea() angemeldet", ce === 200, ce);
    const token = enrollRes.agent_token;
    const [, hostsNow] = await adminCall("/api/v1/hosts");
    const hid = hostsNow.find(h => h.hostname === `S5-REBOOT-HOST-${suffix}`).id;
    await adminCall(`/api/v1/hosts/${hid}/approve`, { body: {} });
    await adminCall(`/api/v1/hosts/${hid}/area`, { body: { area_id: areaR.id } });

    const holeHosts = async () => {
      const [, hs] = await adminCall("/api/v1/hosts");
      api.setHosts(hs);
    };
    await holeHosts();

    const jobsVorher = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    await api.rebootArea(areaR.id);
    const jobsOhneBedarf = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    check("Restart ohne Neustartbedarf legt keinen Auftrag an",
          jobsOhneBedarf.length === jobsVorher.length,
          { vorher: jobsVorher.length, nachher: jobsOhneBedarf.length });

    // Neustartbedarf wie ein echter Agent melden, ueber den Heartbeat.
    await fetch(`http://127.0.0.1:${PORT}/api/v1/agent/heartbeat`, {
      method: "POST",
      headers: {"X-Agent-Token": token, "Content-Type": "application/json"},
      body: JSON.stringify({
        hostname: `S5-REBOOT-HOST-${suffix}`, os_type: "linux",
        os_version: "Debian 13", agent_version: "0.36.0",
        reboot_required: true, reboot_reasons: ["kernel"],
      }),
    });
    await holeHosts();

    await api.rebootArea(areaR.id);
    const jobsNachReboot = (await adminCall(`/api/v1/jobs?host_id=${hid}`))[1];
    const rebootAuftrag = jobsNachReboot.find(j => j.job_type === "reboot");
    check("Restart legt jetzt einen reboot-Auftrag an", !!rebootAuftrag,
          jobsNachReboot.map(j => j.job_type));
    check("... mit der Herkunft des Bereichs in den Parametern, wie bei patchArea()",
          rebootAuftrag && rebootAuftrag.params
          && rebootAuftrag.params.source_area_id === areaR.id,
          rebootAuftrag && rebootAuftrag.params);
  }

  console.log("\n=== Rueckmeldung: 4 weitere Punkte ===");
  {
    const adminCall = async (path, opts = {}) => {
      const r = await fetch(`http://127.0.0.1:${PORT}${path}`, {
        headers: {"X-Session": SESSION, "Content-Type": "application/json"},
        method: opts.method || (opts.body ? "POST" : "GET"),
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
      const text = await r.text();
      let json = {};
      try { json = text ? JSON.parse(text) : {}; } catch(e){}
      return [r.status, json];
    };
    api.setzeSprache("de");   // deterministisch fuer die erwarteten Texte unten

    const mkHost = (id, overrides) => Object.assign({
      id, hostname: `S6-${id}`, display_name: null,
      approval_state: "approved", status: "online",
      os_type: "linux", updates_available: 0, security_updates: 0,
      checkmk_downtime_all: false, checkmk_hosts: [], downtime_minutes: 30,
      reboot_required: false, patch_enabled: false, patch_followup_left: 0,
      updates_require_reboot: false, area_id: null,
    }, overrides);

    // --- Punkt 1: Nummerierung faengt in jeder Gruppe wieder bei 1 an -
    // ausdruecklich auch im bereichslosen Bereich, nicht nur je Area. ---
    const areaN = { id: 90701, name: "S6-Bereich", sort_order: 1, host_count: 0 };
    const hFrei1 = mkHost(90101, {});
    const hFrei2 = mkHost(90102, {});
    const hIn1 = mkHost(90103, { area_id: areaN.id });
    const hIn2 = mkHost(90104, { area_id: areaN.id });

    api.setAreas([areaN]);
    api.setHosts([hFrei1, hFrei2, hIn1, hIn2]);
    api.setLastAction({});
    el("filter").value = ""; el("fState").value = "";
    api.render();
    let out = el("units").innerHTML;
    const slotVon = (id) => {
      const m = out.match(new RegExp(
        `<div class="unit"[^>]*data-id="${id}">\\s*<div class="slot[^>]*>(\\d+)<`));
      return m ? m[1] : null;
    };
    check("bereichsloser Host 1 startet bei 01", slotVon(hFrei1.id) === "01", slotVon(hFrei1.id));
    check("bereichsloser Host 2 zaehlt weiter auf 02", slotVon(hFrei2.id) === "02", slotVon(hFrei2.id));
    check("erster Host IM Bereich beginnt wieder bei 01, nicht bei 03 (durchgehend)",
          slotVon(hIn1.id) === "01", slotVon(hIn1.id));
    check("zweiter Host im Bereich zaehlt innerhalb des Bereichs weiter auf 02",
          slotVon(hIn2.id) === "02", slotVon(hIn2.id));

    // Gegenprobe: EINE gemeinsame Nummerierung ueber alles wuerde die
    // beiden letzten Pruefungen auf 03/04 statt 01/02 durchfallen lassen -
    // das war vor der Umsetzung tatsaechlich der Fall (durchgehendes i in
    // render() statt eines je-Gruppe-Index aus map()).

    // --- Punkt 2: dritte Zeile mit der letzten abgeschlossenen Aktion ---
    const hOhne = mkHost(90105, {});
    api.setHosts([hOhne]);
    api.setAreas([]);
    api.setLastAction({});
    api.render();
    check("ohne abgeschlossenen Auftrag erscheint keine dritte Zeile",
          !el("units").innerHTML.includes('class="lastaction"'));

    const faelle = [
      ["scan", "done", "note.last_scan_done"],
      ["scan", "failed", "note.last_scan_failed"],
      ["patch", "done", "note.last_patch_done"],
      ["patch", "failed", "note.last_patch_failed"],
      ["reboot", "done", "note.last_reboot_done"],
      ["reboot", "failed", "note.last_reboot_failed"],
    ];
    for (const [job_type, state, schluessel] of faelle){
      const zeitpunkt = "2026-08-20T13:34:00Z";
      api.setLastAction({ [hOhne.id]: { host_id: hOhne.id, job_type, state, finished_at: zeitpunkt } });
      api.render();
      const zeit = api.fmtTime(zeitpunkt, {weekday:"short", hour:"2-digit", minute:"2-digit"});
      const erwartet = api.t(schluessel, { zeit });
      check(`dritte Zeile fuer ${job_type}/${state}`,
            el("units").innerHTML.includes(erwartet), erwartet);
    }

    // Ein Auftragstyp ohne eigene Formulierung (z.B. Selbstaktualisierung)
    // bleibt bewusst ohne dritte Zeile.
    api.setLastAction({ [hOhne.id]: {
      host_id: hOhne.id, job_type: "selfupdate", state: "done",
      finished_at: "2026-08-20T13:34:00Z" } });
    api.render();
    check("Auftragsarten ohne Formulierung (z.B. Selbstaktualisierung) bleiben ohne dritte Zeile",
          !el("units").innerHTML.includes('class="lastaction"'));

    // --- Punkt 2, Server: /api/v1/jobs/last liefert je Host den zuletzt
    // abgeschlossenen Auftrag, aktuellsten zuerst, cancelled zaehlt nicht. ---
    const suffix6 = Date.now();
    const [ce6, enroll6] = await adminCall("/api/v1/agent/enroll", { body: {
      hostname: `S6-JOB-HOST-${suffix6}`, os_type: "linux",
      os_version: "Debian 13", agent_version: "0.36.0",
    }});
    check("Testhost fuer jobs/last angemeldet", ce6 === 200, ce6);
    const token6 = enroll6.agent_token;
    const [, hosts6] = await adminCall("/api/v1/hosts");
    const hid6 = hosts6.find(h => h.hostname === `S6-JOB-HOST-${suffix6}`).id;
    await adminCall(`/api/v1/hosts/${hid6}/approve`, { body: {} });

    const agentReport = async (job_id, state) => {
      const r = await fetch(`http://127.0.0.1:${PORT}/api/v1/agent/report`, {
        method: "POST",
        headers: {"X-Agent-Token": token6, "Content-Type": "application/json"},
        body: JSON.stringify({ job_id, state, result: {} }),
      });
      return r.status;
    };
    const letzterAuftrag = async () => {
      const [, liste] = await adminCall("/api/v1/jobs/last");
      return liste.find(j => j.host_id === hid6);
    };

    const [, scanJob] = await adminCall(`/api/v1/hosts/${hid6}/jobs`, { body: { job_type: "scan", params: {} } });
    check("scan-Auftrag angelegt", !!scanJob.id, scanJob);
    check("scan-Auftrag noch nicht in jobs/last, solange er nicht abgeschlossen ist",
          !(await letzterAuftrag()));
    check("agent/report scan done", (await agentReport(scanJob.id, "done")) === 200);
    let letzter = await letzterAuftrag();
    check("jobs/last zeigt den scan-Auftrag als erledigt",
          letzter && letzter.job_type === "scan" && letzter.state === "done", letzter);

    const [, patchJob] = await adminCall(`/api/v1/hosts/${hid6}/jobs`, { body: { job_type: "patch", params: {} } });
    check("agent/report patch failed", (await agentReport(patchJob.id, "failed")) === 200);
    letzter = await letzterAuftrag();
    check("jobs/last zeigt jetzt den neueren patch-Auftrag statt des aelteren scan-Auftrags",
          letzter && letzter.job_type === "patch" && letzter.state === "failed", letzter);

    const [, cancelJob] = await adminCall(`/api/v1/hosts/${hid6}/jobs`, { body: { job_type: "reboot", params: {} } });
    check("agent/report reboot cancelled", (await agentReport(cancelJob.id, "cancelled")) === 200);
    letzter = await letzterAuftrag();
    check("ein abgebrochener (cancelled) Auftrag zaehlt nicht als letzte Aktion",
          letzter && letzter.job_type === "patch", letzter);

    // finished_at fehlt in einem seltenen Pfad (agent/notice haengt an den
    // juengsten Selfupdate-Auftrag an, ohne finished_at zu setzen) - dann
    // muss created_at als Behelf einspringen, sonst faellt der Auftrag beim
    // Sortieren nach hinten und wuerde nie als der juengste erkannt.
    await adminCall(`/api/v1/hosts/${hid6}/jobs`, { body: { job_type: "selfupdate", params: {} } });
    const noticeStatus = await fetch(`http://127.0.0.1:${PORT}/api/v1/agent/notice`, {
      method: "POST",
      headers: {"X-Agent-Token": token6, "Content-Type": "application/json"},
      body: JSON.stringify({ message: "Testfehler", result: {} }),
    });
    check("agent/notice angenommen", noticeStatus.status === 200, noticeStatus.status);
    letzter = await letzterAuftrag();
    check("jobs/last erkennt den Selfupdate-Auftrag ohne finished_at ueber created_at als juengsten",
          letzter && letzter.job_type === "selfupdate" && letzter.state === "failed"
          && !!letzter.finished_at, letzter);

    // Aufraeumen: dieser Testhost wird nicht mehr gebraucht. Wichtig fuer
    // den vollstaendigen Lauf (alle Reihen zusammen) - roles-test.py laeuft
    // bewusst als letzte Reihe gegen denselben Bestand und prueft die
    // Freigabe bis zum Freibetrag (10 Hosts). Ein hier liegen gelassener,
    // freigegebener Host wuerde diesen Spielraum unbemerkt verkleinern.
    await adminCall(`/api/v1/hosts/${hid6}`, { method: "DELETE" });

    // --- Punkt 3: Entfernen-Knopf direkt in der Bereichsliste ---
    const [, areaS3] = await adminCall("/api/v1/areas", { body: { name: `S6-Remove-${suffix6}` } });
    await api.loadAreasTab();
    check("neuer Entfernen-Knopf in der Bereichsliste vorhanden",
          el("areaList").innerHTML.includes(`data-act="arearemove" data-id="${areaS3.id}"`));
    check("der Entfernen-Knopf im Bearbeiten-Dialog bleibt zusaetzlich bestehen (aDelete)",
          !!el("aDelete"));

    await api.removeArea(areaS3.id, false);
    await api.loadAreasTab();
    check("Bereich ist nach removeArea() wirklich weg",
          !api.getAreas().some(a => a.id === areaS3.id), api.getAreas().map(a => a.id));

    // --- Punkt 4: Scrollsperre haelt den Hintergrund fest, solange
    // mindestens ein Dialog offen ist - auch wenn zwei gleichzeitig offen
    // sind (z.B. Settings mit einem Bereichs-Dialog obendrauf). ---
    for (const id of ["dlgLogin","dlgHost","dlgArea","dlgSettings","dlgReboot","dlgLive","dlgDetail"])
      el(id).close();
    check("Hintergrund frei, solange kein Dialog offen ist",
          document.body.style.overflow === "", document.body.style.overflow);

    el("dlgSettings").showModal();
    check("Hintergrund gesperrt, sobald ein Dialog offen ist",
          document.body.style.overflow === "hidden");

    el("dlgArea").showModal();
    check("weiterhin gesperrt mit zwei gleichzeitig offenen Dialogen",
          document.body.style.overflow === "hidden");

    el("dlgArea").close();
    check("bleibt gesperrt, solange der aeussere Dialog (Settings) noch offen ist - "
          + "durfte NICHT durchs Schliessen des inneren Dialogs freigegeben werden",
          document.body.style.overflow === "hidden");

    el("dlgSettings").close();
    check("Hintergrund wieder frei, sobald auch der letzte Dialog geschlossen ist",
          document.body.style.overflow === "", document.body.style.overflow);
  }

  // ==================================================================
  console.log("\n=== Text vom Agent wird escaped ===");
  {
    // In die zweite Zeile einer Host-Zeile ("notes") laufen Werte, die
    // der Agent bestimmt. h.agent_version kommt aus /agent/enroll und
    // damit von JEDEM im Netz, ohne Anmeldung.
    //
    // Das ist kein Schoenheitsfehler: der Klick-Handler reagiert auf
    // JEDES [data-act]-Element. Eingeschleustes Markup kann also einen
    // echten Knopf erzeugen - data-act="approve" mit freier data-id -
    // und ihn per style-Attribut tarnen. Ein Klick des Administrators
    // genuegt. Die Regel script-src 'self' verhindert nur, dass Skript
    // laeuft, nicht dass Markup entsteht.
    const boese = '<img src=x data-act="deluser" data-id="1">';
    const mk = (id, overrides) => Object.assign({
      id, hostname: `X${id}`, display_name: null,
      approval_state: "approved", status: "online",
      os_type: "linux", updates_available: 0, security_updates: 0,
      checkmk_downtime_all: false, checkmk_hosts: [], downtime_minutes: 30,
      reboot_required: false, patch_enabled: false, patch_followup_left: 0,
      updates_require_reboot: false, area_id: null,
    }, overrides);

    el("filter").value = "";
    el("fState").value = "";
    api.setAreas([]);

    // 1. agent_version - der unauthentifizierte Weg. Damit die Meldung
    //    "Agent veraltet" ueberhaupt erscheint, muss AGENT_VER abweichen.
    api.setAgentVer("0.0.0");
    api.setHosts([mk(90701, { agent_version: boese })]);
    api.render();
    let out = el("units").innerHTML;
    check("agent_version erscheint nicht als Markup",
          !out.includes("<img"), out.slice(out.indexOf("class=\"sub\""), 260));
    check("und auch nicht als klickbares data-act",
          !out.includes('data-act="deluser"'));
    check("der Text ist aber sichtbar, escaped",
          out.includes("&lt;img"), out.includes("&lt;img"));

    // 2. reboot_reasons - unbekannte Gruende gibt rebootNote() bewusst im
    //    Klartext des Agents aus.
    api.setAgentVer("");
    api.setHosts([mk(90702, {
      reboot_required: true, reboot_reasons: [boese],
    })]);
    api.render();
    out = el("units").innerHTML;
    check("reboot_reasons erscheint nicht als Markup", !out.includes("<img"));
    check("reboot_reasons wird escaped angezeigt", out.includes("&lt;img"));

    // 3. progress aus einem laufenden Auftrag.
    api.setHosts([mk(90703, {})]);
    api.setActive({ 90703: { id: 5, job_type: "patch", progress: boese } });
    api.render();
    out = el("units").innerHTML;
    check("progress erscheint nicht als Markup", !out.includes("<img"));
    check("progress wird escaped angezeigt", out.includes("&lt;img"));
    api.setActive({});

    // Gegenprobe zur Aussagekraft: der Hostname selbst war schon vorher
    // escaped. Waere esc() an der falschen Stelle, fiele das hier auf.
    api.setHosts([mk(90704, { display_name: boese })]);
    api.render();
    out = el("units").innerHTML;
    check("display_name bleibt escaped", !out.includes("<img"));
  }

  // ------------------------------------------------------------------
  // "Alle Agents aktualisieren" geht ueber die Rollout-Route
  // ------------------------------------------------------------------
  // Der Knopf legte die selfupdate-Auftraege bis 0.37.15 selbst an, in
  // einer Schleife ueber /api/v1/hosts/{id}/jobs. Damit fehlten die
  // Staffelung (erst der Pilot) und die Doppel-Auftrag-Sperre - wer ihn
  // drueckte, schickte die ganze Flotte auf einmal los. Der zweite Knopf
  // in den Einstellungen machte es von Anfang an richtig; die beiden
  // waren auseinandergelaufen.
  //
  // Geprueft wird am Quelltext, weil der Knopf in dieser Attrappe nicht
  // klickbar ist. Der Block wird auf die Klammernebene abgegrenzt statt
  // mit einer Suche ueber die ganze Datei - sonst faende die Pruefung
  // irgendein anderes POST auf /jobs und bliebe gruen.
  {
    const start = appjs.indexOf('getElementById("agUpdateAll")');
    check("der Knopf agUpdateAll ist auffindbar", start > 0);
    const ende = appjs.indexOf("\n};", start);
    const block = appjs.slice(start, ende > 0 ? ende : start + 1200);
    check("agUpdateAll legt keine Auftraege mehr selbst an",
          !/\/jobs`/.test(block) && !block.includes("job_type:\"selfupdate\""),
          block.slice(0, 300));
    check("agUpdateAll geht ueber rolloutStarten()",
          block.includes("rolloutStarten("), block.slice(0, 300));
    check("rolloutStarten benutzt die Rollout-Route",
          /async function rolloutStarten\(\)\s*\{[^]*?agent-rollout\/start/.test(appjs));
    // Gegenprobe: der zweite Knopf zeigt auf dieselbe Funktion, damit die
    // beiden nicht ein zweites Mal auseinanderlaufen koennen.
    const ro = appjs.indexOf('getElementById("roStart")');
    check("roStart benutzt dieselbe Funktion",
          ro > 0 && appjs.slice(ro, ro + 200).includes("rolloutStarten("),
          appjs.slice(ro, ro + 120));
  }

  console.log(`\nFehler: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
