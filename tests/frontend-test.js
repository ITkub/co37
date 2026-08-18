/**
 * Fuehrt das Frontend-Skript aus index.html in Node aus, gegen ein echtes
 * Backend. Statt eines Browsers eine minimale DOM-Attrappe - damit laesst
 * sich pruefen, ob die Funktionen wirklich durchlaufen und was sie in die
 * Elemente schreiben.
 */
const fs = require("fs");
const { execSync } = require("child_process");

const PORT = process.argv[2] || "8085";
const KEY = process.argv[3] || "t";

// Seit 0.13.0 arbeitet die Oberflaeche mit einer Anmeldesitzung statt mit
// dem API-Key. Die Sitzung muss vorliegen, bevor das Skript geladen wird -
// deshalb hier synchron per curl statt mit fetch.
let SESSION_TOKEN = "";
try {
  const out = execSync(
    `curl -s -X POST http://127.0.0.1:${PORT}/api/v1/login `
    + `-H "Content-Type: application/json" `
    + `-d '{"username":"admin","password":"admin"}'`,
    { encoding: "utf8" });
  SESSION_TOKEN = JSON.parse(out).session || "";
} catch (e) {
  console.error("Anmeldung fehlgeschlagen. Laeuft das Backend, und ist das "
              + "Passwort von admin noch 'admin'?");
}
if (!SESSION_TOKEN) process.exit(1);
const HTML_PATH = process.argv[4] || "/tmp/jt/frontend/index.html";
const html = fs.readFileSync(HTML_PATH, "utf8");
// Das Skript liegt seit 0.27.0 in einer eigenen Datei - noetig, damit die
// Regel script-src ohne 'unsafe-inline' auskommt.
const JS_PATH = require("path").join(require("path").dirname(HTML_PATH), "app.js");
const appjs = fs.readFileSync(JS_PATH, "utf8");

// ---------------------------------------------------------------- DOM-Attrappe
const store = {};
function mkEl(id) {
  return {
    id,
    _text: "", _html: "", value: "", checked: false, placeholder: "",
    className: "", style: {}, dataset: {}, files: [],
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
    setAttribute() {}, showModal() {}, close() {}, click() {}, focus() {}, blur() {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
  };
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

// Die Oberflaeche haengt einen Klick-Verteiler ans Dokument. Hier
// mitgeschnitten, damit der Test ihn aufrufen kann - im Browser passiert
// das ueber ein echtes Klickereignis.
let CLICK_HANDLER = null;

global.document = {
  documentElement: { dataset: {} },
  cookie: "",
  body: { appendChild() {}, removeChild() {} },
  addEventListener(typ, fn) { if (typ === "click") CLICK_HANDLER = fn; },
  removeEventListener() {},
  getElementById: el,
  createElement: () => mkEl("tmp"),
  querySelector(sel) {
    // Kein Dialog offen in der Attrappe - der Kopier-Rueckfallweg faellt
    // dann auf document.body zurueck, wie im Browser ohne offenen Dialog.
    if (sel === "dialog[open]") return null;
    return null;
  },
  querySelectorAll(sel) {
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
global.prompt = () => KEY;
global.confirm = () => true;
global.matchMedia = () => ({ matches: false });
global.URL.createObjectURL = () => "blob:x";
global.URL.revokeObjectURL = () => {};

const TOASTS = [];
// Startroutine des Skripts abfangen, damit sie nicht sofort loslaeuft
const origSetInterval = global.setInterval;
const origSetTimeout = global.setTimeout;
global.setInterval = () => 0;
global.setTimeout = () => 0;

// -------------------------------------------------------------- Skript laden
const script = appjs;
const mod = { exports: {} };
const EXPORTS = "\nreturn { loadAgentsTab, loadCmk, loadCmkForm, copy, fmtSize, toast, "
  + "rebootHost: typeof rebootHost !== 'undefined' ? rebootHost : null, "
  + "setHosts: (h) => { HOSTS = h; }, "
  + "checkVersion, setPageVersion: (v) => { PAGE_VERSION = v; }, "
  + "loadRollout, loadUsers, loadAudit, loadAccount, "
  + "setMe: (m) => { ME = m; }, getMe: () => ME, renderRackHead, makeInstallToken, forgetInstallToken: () => { INSTALL_TOKEN = null; renderLinuxCmd(); } };";
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
                              {headers: {"X-API-Key": KEY}});
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
  // Auf den Kopfzeilennamen pruefen, nicht auf den Wert: der Test-Schluessel
  // ist oft ein einzelner Buchstabe und kommt in jedem Befehl vor.
  check("Linux-Befehl nutzt NICHT den API-Key", !lin.includes("X-API-Key"));
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
                          {method: meth, headers: {"X-API-Key": KEY}});
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
                                {headers: {"X-API-Key": KEY}});
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
                          {headers: {"X-API-Key": KEY}});
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
    api.renderRackHead();
    check("breite Ansicht mit Kopfzeile", el("rackHead").innerHTML.includes("Sicherheit"));
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
  // X-Install-Token, nicht X-API-Key: der Befehl wird auf einem fremden
  // Rechner ausgefuehrt und bleibt dort in der Verlaufsdatei stehen.
  check("Befehl nutzt ein Installations-Token",
        cmd.includes("X-Install-Token:"), cmd.slice(0, 70));
  check("Befehl enthaelt nicht den API-Key",
        !cmd.includes("X-API-Key:"), cmd.slice(0, 70));
  check("Token ist nicht leer",
        !/X-Install-Token:\s+["']?\s*(http|$)/.test(cmd), cmd.slice(0, 70));
  check("Kopieren jetzt moeglich", el("copyLinux").disabled === false);

  console.log(`\nFehler: ${fails}`);
  process.exit(fails ? 1 : 0);
})();
