const API = location.origin;

/* ---------- Anmeldung ----------
 * Statt eines API-Keys eine Sitzung. Der Key funktioniert weiterhin, wird
 * hier aber nicht mehr gebraucht - er bleibt fuer Skripte, etwa den
 * Installationsbefehl der Agents.
 */
/*
 * Das Sitzungstoken liegt nicht mehr im Browser, sondern in einem Cookie
 * mit HttpOnly - JavaScript kommt nicht mehr heran, auch eingeschleustes
 * nicht. Der Browser schickt es bei jeder Anfrage von selbst mit.
 *
 * Deshalb steht hier nur noch ein Merker, ob eine Sitzung besteht. Ob sie
 * wirklich gilt, entscheidet der Server: bei 401 ist sie weg.
 */
let SESSION = false;
let ME = null;

function setSession(aktiv){
  SESSION = !!aktiv;
}

/*
 * Zwischenstand der Anmeldung in zwei Schritten. Das ist NICHT das
 * Sitzungstoken - es gibt noch keine Sitzung. Es ist die kurzlebige
 * Kennung des angefangenen Vorgangs, die das Backend nach fuenf Minuten
 * oder fuenf Fehlversuchen verwirft.
 */
let MFA_TOKEN = null;

function showLogin(msg){
  ME = null;
  MFA_TOKEN = null;
  document.getElementById("loginError").textContent = msg || "";
  document.getElementById("loginUser").value = "";
  document.getElementById("loginPass").value = "";
  document.getElementById("loginCode").value = "";
  document.getElementById("loginSchritt1").style.display = "";
  document.getElementById("loginSchritt2").style.display = "none";
  document.getElementById("appShell").style.display = "none";
  const dlg = document.getElementById("dlgLogin");
  if (!dlg.open) dlg.showModal();
  document.getElementById("loginUser").focus();
}

async function doLogin(){
  // Steht der Vorgang schon im zweiten Schritt, geht der Klick dorthin.
  if (MFA_TOKEN) return doLoginCode();
  const username = document.getElementById("loginUser").value.trim();
  const password = document.getElementById("loginPass").value;
  if (!username || !password){
    document.getElementById("loginError").textContent = t("login.fill_both");
    return;
  }
  let res;
  try {
    res = await fetch(API + "/api/v1/login", {
      method: "POST", headers: {"Content-Type": "application/json"},
      // Damit der Browser das gesetzte Sitzungscookie annimmt.
      credentials: "same-origin",
      body: JSON.stringify({username, password})
    });
  } catch(e){
    document.getElementById("loginError").textContent = t("login.unreachable");
    return;
  }
  if (!res.ok){
    let msg = t("login.failed");
    try { msg = (await res.json()).detail || msg; } catch(e){}
    document.getElementById("loginError").textContent = msg;
    return;
  }
  const d = await res.json();

  // Zweiter Schritt verlangt. Es gibt jetzt KEIN Sitzungscookie - das
  // Backend setzt es erst, wenn der Code stimmt. Wer hier abbricht, ist
  // nicht angemeldet.
  if (d && d.mfa_required){
    MFA_TOKEN = d.mfa_token;
    PW_ALT = password;
    document.getElementById("loginSchritt1").style.display = "none";
    document.getElementById("loginSchritt2").style.display = "";
    document.getElementById("loginError").textContent = "";
    document.getElementById("loginCode").focus();
    return;
  }

  // Fuer einen etwaigen erzwungenen Wechsel gleich danach aufheben.
  PW_ALT = password;
  setSession(true);
  document.getElementById("dlgLogin").close();
  document.getElementById("appShell").style.display = "";
  await startApp();
}

async function doLoginCode(){
  const code = document.getElementById("loginCode").value.trim();
  const feld = document.getElementById("loginError");
  if (!code){ feld.textContent = t("login.mfa_missing"); return; }
  let res;
  try {
    res = await fetch(API + "/api/v1/login/totp", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({mfa_token: MFA_TOKEN, code})
    });
  } catch(e){ feld.textContent = t("login.unreachable"); return; }

  if (!res.ok){
    // Nach zu vielen Fehlversuchen oder nach Ablauf verwirft das Backend
    // den Vorgang und sagt das in einer Kopfzeile. Dann hilft nur von
    // vorn - und die Maske muss das zeigen, sonst tippt jemand weiter
    // Codes gegen ein Token, das es nicht mehr gibt.
    const neu = res.headers.get("X-CO37-MFA") === "restart";
    let msg = t("login.failed");
    try { msg = (await res.json()).detail || msg; } catch(e){}
    feld.textContent = msg;
    document.getElementById("loginCode").value = "";
    if (neu){
      MFA_TOKEN = null;
      document.getElementById("loginPass").value = "";
      document.getElementById("loginSchritt1").style.display = "";
      document.getElementById("loginSchritt2").style.display = "none";
      document.getElementById("loginUser").focus();
    }
    return;
  }
  await res.json();
  MFA_TOKEN = null;
  setSession(true);
  document.getElementById("dlgLogin").close();
  document.getElementById("appShell").style.display = "";
  await startApp();
}

/* ---------- Erzwungener Passwortwechsel ---------- */

function zeigePasswortZwang(){
  document.getElementById("appShell").style.display = "none";
  document.getElementById("pwzError").textContent = "";
  document.getElementById("pwzNeu").value = "";
  document.getElementById("pwzNeu2").value = "";
  const dlg = document.getElementById("dlgPwZwang");
  if (!dlg.open) dlg.showModal();
  document.getElementById("pwzNeu").focus();
}

async function passwortZwangSpeichern(){
  const neu = document.getElementById("pwzNeu").value;
  const neu2 = document.getElementById("pwzNeu2").value;
  const feld = document.getElementById("pwzError");
  if (neu.length < PW_MIN){
    feld.textContent = t("msg.pw_short", { anzahl: PW_MIN }); return;
  }
  if (neu !== neu2){ feld.textContent = t("msg.pw_mismatch"); return; }
  try {
    await api("POST", "/api/v1/me/password",
              { old_password: PW_ALT || "", new_password: neu });
  } catch(e){ return; }
  // Der Server verwirft dabei alle Sitzungen, auch die eigene - der
  // Benutzer meldet sich gleich mit dem neuen Passwort an.
  PW_ALT = null;
  document.getElementById("dlgPwZwang").close();
  setSession(false);
  showLogin(t("msg.pw_changed"));
}


async function doLogout(){
  try {
    await fetch(API + "/api/v1/logout", {
      method: "POST", credentials: "same-origin"
    });
  } catch(e){}
  setSession(false);
  location.reload();
}

// Muss zu MIN_PASSWORD_LEN im Backend passen. Steht hier als Konstante
// und nicht zweimal als Zahl im Code - dieselbe Fehlerart wie die fest
// eingetragene Python-Fassung in i18n.js, die nach einem Anheben auf die
// falsche Datei zeigte.
const PW_MIN = 12;
// Das gerade eingegebene Passwort, nur solange ein erzwungener Wechsel
// aussteht. Die Route /me/password verlangt das alte Passwort, und der
// Benutzer hat es zwei Sekunden vorher eingetippt - ihn erneut danach zu
// fragen waere Schikane. Wird unmittelbar nach dem Wechsel geleert.
let PW_ALT = null;

let HOSTS = [], AREAS = [], CMK_HOSTS = [], AGENT_VER = "";
// Verschieben der Uebersicht: nur bei ungefilterter Liste erlaubt.
let SORTABLE = true, DRAG_ID = null;
let EDIT_ID = null, PICKED = new Set();

/* ---------- Symbole ---------- */
const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

const root = document.documentElement;
function readCookie(n){
  const c = document.cookie.split(";").find(x => x.trim().startsWith(n+"="));
  return c ? c.split("=")[1].trim() : null;
}
function setTheme(t){
  root.dataset.theme = t;
  // Zeigt das Symbol, zu dem gewechselt wird
  document.getElementById("btnTheme").innerHTML = t === "dark" ? SUN : MOON;
  document.cookie = `pp_theme=${t};path=/;max-age=31536000;SameSite=Lax`;
}
setTheme(readCookie("pp_theme") || "dark");
document.getElementById("btnTheme").onclick = () =>
  setTheme(root.dataset.theme === "dark" ? "light" : "dark");

/* ---------- Versionswechsel bemerken ---------- */
let PAGE_VERSION = null;

/**
 * Die Oberflaeche wird beim Systemupdate ausgetauscht, eine bereits
 * geoeffnete Seite laeuft aber mit dem alten JavaScript weiter. Sie baut
 * dann etwa Installationsbefehle mit dem alten Paketnamen - unabhaengig
 * davon, wie frisch die Daten vom Server sind. Deshalb wird die Version
 * bei jedem Durchlauf verglichen und ein Neuladen angeboten.
 */
async function checkVersion(){
  let h;
  try { h = await (await fetch(API + "/api/health", {cache: "no-store"})).json(); }
  catch(e){ return; }

  // Muss VOR den beiden Ausstiegen darunter stehen. Sonst erfaehrt eine
  // Oberflaeche, die gerade erst geladen wurde oder deren Version
  // unveraendert ist, nie von einem laufenden Update - und genau in
  // diesen beiden Faellen wird die laengere Geduld gebraucht.
  //
  // Bis 0.37.31 setzte nur renderUpdate() diese Angabe, also nur, wenn
  // der Updatereiter vorher geladen war. Wer ihn nie geoeffnet hatte
  // oder wessen Kollege das Update in einem anderen Browser anstiess,
  // bekam nach zwanzig Sekunden die Offline-Leiste zu sehen, obwohl der
  // Ausfall erwartet war. Der Server weiss es; hier wird es geholt, wo
  // ohnehin bei jedem Durchlauf gefragt wird.
  if ("update_running" in h) UPDATE_LAEUFT = !!h.update_running;

  // Update-Band: steht auf GitHub eine neuere Fassung bereit? h.update_-
  // available kommt aus dem letzten Suchlauf und steht hinter derselben
  // F-09-Schranke wie die Versionen. Sichtbar nur fuer Administratoren -
  // ein Benutzer koennte am Band ohnehin nichts ausloesen. Vor den beiden
  // Ausstiegen darunter, wie update_running, damit es auch bei
  // unveraenderter Version erscheint.
  const uBar = document.getElementById("updateBar");
  if (uBar){
    if (h.update_available && ME && ME.is_admin){
      document.getElementById("updateBarText").textContent =
        t("app.update_available", { version: h.update_available });
      uBar.style.display = "flex";
    } else {
      uBar.style.display = "none";
    }
  }

  if (PAGE_VERSION === null){ PAGE_VERSION = h.version; return; }
  if (h.version === PAGE_VERSION) return;

  const bar = document.getElementById("reloadBar");
  document.getElementById("reloadText").textContent =
    t("app.new_version", { neu: h.version, alt: PAGE_VERSION });
  bar.style.display = "flex";
}
document.getElementById("reloadNow").onclick = () => location.reload();

// Das Update-Band verlinkt nur - es installiert nichts. Ein Klick oeffnet
// Einstellungen -> Update, den gewohnten Weg (suchen, holen, ausloesen).
function oeffneUpdateReiter(){
  if (!(ME && ME.is_admin)) return;
  document.querySelectorAll("#sTabs button").forEach(x =>
    x.classList.toggle("on", x.dataset.tab === "tabUpd"));
  STABS.forEach(id =>
    document.getElementById(id).classList.toggle("on", id === "tabUpd"));
  document.getElementById("dlgSettings").showModal();
  loadUpdate();
}
{
  const b = document.getElementById("updateBarBtn");
  if (b) b.onclick = oeffneUpdateReiter;
}

/* ---------- Erreichbarkeit ---------- */
/*
 * Ein nicht erreichbarer Server ist kein Anwendungsfehler.
 *
 * Waehrend eines Systemupdates startet das Backend neu, der Reverse Proxy
 * antwortet solange mit 502. Gleichzeitig fragen mehrere Zeitgeber - die
 * Hostliste alle drei bis fuenfzehn Sekunden, der Updatestand alle drei,
 * dazu Watcher, Proxy und Bauzustand. Jede fehlgeschlagene Abfrage ergab
 * eine eigene Kurzmeldung von neun Sekunden Standzeit; bei zwanzig
 * Sekunden Ausfall waren das leicht ein Dutzend.
 *
 * Umgekehrt war der ernstere Fall stumm: faellt alles aus, scheitert
 * schon fetch selbst, und das wurde hier gar nicht abgefangen - keine
 * Meldung. Der harmlose Fall laermte, der ernste schwieg.
 *
 * Darum getrennt: Anwendungsfehler sofort melden, Erreichbarkeitsfehler
 * erst, wenn sie anhalten - und dann einmal als ruhige Leiste statt als
 * Flut von Kurzmeldungen.
 */
const GATEWAY_CODES = [502, 503, 504];
const OFFLINE_GEDULD = 20000;
// Waehrend eines Updates ist der Ausfall erwartet. Die Oberflaeche weiss
// das aus dem zuletzt gesehenen Zustand - dafuer braucht sie den Server
// nicht, sie hat es sich vorher gemerkt.
const OFFLINE_GEDULD_UPDATE = 180000;
let OFFLINE_SEIT = null;
let UPDATE_LAEUFT = false;

function offlineLeiste(sichtbar){
  const bar = document.getElementById("offlineBar");
  if (bar) bar.style.display = sichtbar ? "flex" : "none";
}

function verbindungFehlt(){
  if (OFFLINE_SEIT === null) OFFLINE_SEIT = Date.now();
  const geduld = UPDATE_LAEUFT ? OFFLINE_GEDULD_UPDATE : OFFLINE_GEDULD;
  if (Date.now() - OFFLINE_SEIT >= geduld) offlineLeiste(true);
}

function verbindungDa(){
  if (OFFLINE_SEIT === null) return;
  OFFLINE_SEIT = null;
  offlineLeiste(false);
}

/* ---------- API ---------- */
async function api(method, path, body){
  let res;
  try {
    res = await fetch(API + path, {
      method, headers: {"Content-Type": "application/json"},
      // Das Cookie geht nur mit, wenn es ausdruecklich verlangt wird -
      // bei fetch ist same-origin zwar die Vorgabe, aber sie war es nicht
      // immer, und ein stiller Ausfall waere hier eine Abmeldung bei jedem
      // Klick.
      credentials: "same-origin",
      body: body ? JSON.stringify(body) : undefined
    });
  } catch(e){
    // fetch scheitert erst, wenn ueberhaupt niemand antwortet: Netz weg,
    // Proxy weg, Server weg. Immer Erreichbarkeit, nie Anwendung.
    verbindungFehlt();
    throw e;
  }
  if (GATEWAY_CODES.includes(res.status)){
    verbindungFehlt();
    throw new Error(t("msg.unreachable_code", { code: res.status }));
  }
  // Ab hier hat das Backend selbst geantwortet - auch ein 403 ist eine
  // Antwort. Also ist es wieder da.
  verbindungDa();
  // 401 heisst: Sitzung abgelaufen oder verworfen. Zurueck zur Anmeldung,
  // statt den Benutzer mit Fehlermeldungen zu bewerfen.
  if (res.status === 401){ setSession(false); showLogin(t("login.session_expired")); throw new Error("401"); }
  if (!res.ok){
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail || msg; } catch(e){}
    toast(String(msg).slice(0,220), true);
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}
/*
 * Kurzmeldung unten rechts.
 *
 * Ein Dialog, der mit showModal() geoeffnet wurde, liegt im obersten
 * Fenster des Browsers - darueber kommt kein z-index. Eine Meldung am
 * body haengt damit zwangslaeufig dahinter und ist nicht lesbar. Genau so
 * ist die Begruendung fuer ein abgewiesenes Update untergegangen.
 *
 * Zwei Wege, je nachdem was der Browser kann:
 *   1. popover - liegt ebenfalls im obersten Fenster, unabhaengig von
 *      Dialogen. Der saubere Weg.
 *   2. sonst in den offenen Dialog haengen. Dann liegt die Meldung mit
 *      ihm im obersten Fenster. Sie verschwindet, wenn er geschlossen
 *      wird - das ist der Preis, aber besser als unsichtbar.
 */
function toast(msg, err){
  const el = document.createElement("div");
  el.className = "toast" + (err ? " err" : "");
  el.textContent = msg;

  let ziel = document.body;
  let alsPopover = false;

  if (typeof el.showPopover === "function"){
    // 'manual' statt 'auto': eine 'auto'-Einblendung schliesst sich, sobald
    // woanders geklickt wird - und Fehlermeldungen sollen stehen bleiben.
    el.setAttribute("popover", "manual");
    alsPopover = true;
  } else {
    const dialoge = document.querySelectorAll("dialog[open]");
    if (dialoge.length) ziel = dialoge[dialoge.length - 1];
  }

  ziel.appendChild(el);
  if (alsPopover){
    try { el.showPopover(); } catch(e){}
  }
  setTimeout(() => el.remove(), err ? 9000 : 5000);
}
/*
 * Scrollsperre fuer den Hintergrund, solange irgendein Dialog offen ist.
 *
 * <dialog> mit showModal() sperrt Tastaturfokus und Klicks auf den
 * Hintergrund, aber nicht das Mausrad - ohne das hier scrollt die Seite
 * dahinter mit, wenn das Rad ueber dem Dialog oder seinem abgedunkelten
 * Hintergrund gedreht wird.
 *
 * Ueber einen MutationObserver auf dem 'open'-Attribut jedes Dialogs statt
 * an jeder showModal()/close()-Stelle einzeln gesetzt - davon gibt es weit
 * ueber ein Dutzend, verteilt ueber die ganze Datei, und eine davon zu
 * vergessen wuerde nur bei genau diesem einen Dialog auffallen. Der
 * Observer erfasst jeden Weg, wie sich ein Dialog oeffnet oder schliesst,
 * die Esc-Taste eingeschlossen.
 *
 * Gezaehlt wird die Anzahl offener Dialoge, nicht nur "ist irgendeiner
 * offen" umgeschaltet - zwei koennen gleichzeitig offen sein (etwa
 * Einstellungen, darueber ein Bereich bearbeiten), der Hintergrund darf
 * erst wieder frei sein, wenn wirklich keiner mehr offen ist.
 */
function aktualisiereHintergrundsperre(){
  const offen = Array.from(document.querySelectorAll("dialog")).some(d => d.open);
  document.body.style.overflow = offen ? "hidden" : "";
}
document.querySelectorAll("dialog").forEach(dlg => {
  new MutationObserver(aktualisiereHintergrundsperre)
    .observe(dlg, { attributes: true, attributeFilter: ["open"] });
});
/**
 * Maskiert Text fuer die Ausgabe in HTML.
 *
 * Das einfache Anfuehrungszeichen gehoert dazu, obwohl es fuer HTML nicht
 * noetig waere. Frueher landeten Werte in JavaScript-Zeichenketten
 * innerhalb von Attributen; ein Hostname mit einem Anfuehrungszeichen
 * konnte daraus ausbrechen und Code ausfuehren - mitsamt Zugriff auf das
 * Sitzungstoken. Seit die Klicks ueber data-Attribute verteilt werden,
 * gibt es diese Stellen nicht mehr. Die Maskierung bleibt trotzdem
 * vollstaendig: sie ist die letzte Schranke, nicht die einzige.
 */
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
/**
 * Setzt ein Protokoll aus Update oder Paketbau zu Text zusammen.
 *
 * Watcher und Backend liefern Schluessel, keine Saetze - uebersetzt wird
 * hier. Vorher standen dort deutsche Saetze, die auch bei englischer
 * Oberflaeche deutsch blieben.
 *
 * Drei Formen kommen vor, alle drei muessen durchlaufen:
 *   {t, k, p}   ein Schluessel mit Werten - wird uebersetzt
 *   {t, text}   Ausgabe von build_packages.sh, apt, pip - unveraendert
 *   "..."       reine Zeichenkette, wie sie ein Watcher vor 0.36.6
 *               geschrieben hat. Steht noch im Protokoll des Updates,
 *               das die neue Fassung gerade eingespielt hat.
 *
 * Ein unbekannter Schluessel liefert ueber t() den Schluessel selbst -
 * sichtbar kaputt statt leer, falls ein neuerer Watcher etwas meldet,
 * das diese Oberflaeche noch nicht kennt.
 */
function protokoll(eintraege){
  return (eintraege || []).map(e => {
    if (typeof e === "string") return e;
    const zeit = e.t ? `[${e.t}] ` : "";
    return zeit + (e.k ? t(e.k, e.p || {}) : (e.text ?? ""));
  }).join("\n");
}
/**
 * Zeigt einen Zeitstempel aus der API in Ortszeit.
 *
 * Die API liefert UTC mit Zeitzone. Vorher wurde an einer Stelle die
 * Zeichenkette schlicht abgeschnitten - das zeigte UTC an und lag damit um
 * den Zeitzonenversatz daneben.
 */
function fmtTime(iso, opts){
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(zeitSprache(), opts);
}

function fmtSize(b){
  if (!b && b !== 0) return "?";
  return b >= 1024*1024 ? (b/1024/1024).toFixed(1) + " MB"
       : b >= 1024      ? Math.round(b/1024) + " KB"
                        : b + " Bytes";
}

/**
 * Kopiert in die Zwischenablage.
 *
 * navigator.clipboard gibt es nur in einem sicheren Kontext (HTTPS oder
 * localhost). Im lokalen Netz laeuft CO-37 ueber HTTP, dort existiert
 * die Schnittstelle nicht. Deshalb der Rueckfallweg ueber ein verstecktes
 * Textfeld und execCommand, das auch ohne TLS funktioniert.
 */
async function copy(text, label){
  if (!text || !text.trim()){
    toast(t("msg.nothing_to_copy"), true);
    return;
  }

  if (navigator.clipboard && window.isSecureContext){
    try {
      await navigator.clipboard.writeText(text);
      toast(t("msg.copied", { was: label }));
      return;
    } catch(e){ /* Rueckfallweg unten */ }
  }

  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";

    // In den offenen Dialog einhaengen. Bei einem modalen <dialog> ist
    // alles ausserhalb inert - ein Textfeld an document.body liesse sich
    // nicht markieren, und das Kopieren schlaegt still fehl.
    const host = document.querySelector("dialog[open]") || document.body;
    host.appendChild(ta);

    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    ta.remove();
    toast(ok ? t("msg.copied", { was: label }) : t("msg.copy_failed_short"), !ok);
  } catch(e){
    toast(t("msg.copy_failed"), true);
  }
}

/* ---------- Zustand ---------- */
/* Klartext der Neustart-Gruende. Muss zu REBOOT_REASON_TEXT im Agent passen. */
const REBOOT_REASONS = {
  windows_update: "reason.windows_update",
  cbs_pending: "reason.cbs",
  cbs_inprogress: "reason.cbs",
  cbs_packages: "reason.cbs",
  pending_rename: "reason.pending_rename",
  netlogon: "reason.netlogon",
  package_manager: "reason.package_manager",
  kernel: "reason.kernel",
};
// Gruende, die einen Neustart nur anzeigen, ihn aber nicht rechtfertigen.
const WEAK_REASONS = ["pending_rename"];

function rebootNote(h){
  // reboot_required ist seit 0.15.1 nur noch bei belastbaren Gruenden
  // gesetzt. Nachrangige Eintraege wie vorgemerkte Dateireste stehen
  // zwar weiter in reboot_reasons, fuehren aber zu keiner Meldung.
  const rs = (h.reboot_reasons || []).filter(r => !WEAK_REASONS.includes(r));
  // t() gibt bei unbekanntem Schluessel den Schluessel zurueck - ein Grund,
  // den die Oberflaeche nicht kennt, erscheint damit weiterhin im Klartext
  // des Agents statt als leere Klammer.
  const why = rs.length ? ` (${rs.map(r => t(REBOOT_REASONS[r] || r)).join(", ")})` : "";
  return t(h.auto_reboot ? "host.reboot_needed_allowed" : "host.reboot_needed") + why;
}

function stateOf(h){
  // Erreichbarkeit hat Vorrang vor einem laufenden Auftrag. Vorher stand
  // die Pruefung auf ACTIVE ganz oben: ein Host mit haengendem Auftrag
  // leuchtete dadurch dauerhaft als "läuft", auch wenn er sich seit
  // Stunden nicht mehr gemeldet hatte - genau das hat einen toten Agent
  // verdeckt.
  if (h.approval_state === "pending") return "pending";
  if (h.approval_state === "rejected") return "offline";
  if (h.status !== "online") return "offline";
  if (ACTIVE[h.id]) return "run";
  if (h.reboot_required) return "reboot";
  if (h.updates_available > 0) return "pending";
  return "ok";
}
const LED = {ok:"ok", pending:"pending", reboot:"reboot", offline:"", run:"run"};

/* ---------- Übersicht ---------- */
function render(){
  renderRackHead();
  const q = document.getElementById("filter").value.toLowerCase();
  const fs = document.getElementById("fState").value;

  const list = HOSTS.filter(h => {
    const hay = [h.hostname, h.display_name, (h.tags||[]).join(" ")]
      .filter(Boolean).join(" ").toLowerCase();
    if (q && !hay.includes(q)) return false;
    if (fs === "waiting") return h.approval_state === "pending";
    if (fs && stateOf(h) !== fs) return false;
    return true;
  });

  // Verschieben nur bei vollstaendiger Liste. Bei aktivem Filter zeigt die
  // Uebersicht eine Teilmenge - eine dort abgelesene Reihenfolge liesse
  // sich nicht auf den Gesamtbestand uebertragen, ohne die ausgeblendeten
  // Hosts an falsche Plaetze zu schieben.
  SORTABLE = list.length === HOSTS.length;

  document.getElementById("sTotal").textContent = HOSTS.length;
  document.getElementById("sOnline").textContent = HOSTS.filter(h => h.status === "online").length;
  document.getElementById("sUpdates").textContent = HOSTS.reduce((a,h) => a+(h.updates_available||0), 0);
  document.getElementById("sReboot").textContent = HOSTS.filter(h => h.reboot_required).length;
  document.getElementById("sWait").textContent = HOSTS.filter(h => h.approval_state === "pending").length;
  document.getElementById("sPlan").textContent = Object.keys(PLANNED).length;

  const box = document.getElementById("units");
  if (!list.length){
    box.innerHTML = `<div class="empty">${
      t(HOSTS.length ? "list.no_match" : "list.empty")}</div>`;
    return;
  }

  // Einmal ausgewertet statt in jeder Zeile. Rein zur Anzeige - die
  // Berechtigung durchsetzen tut die API, nicht diese Abfrage.
  const admin = !!(ME && ME.is_admin);

  // Bereichslose Hosts zuerst, ganz oben - danach jeder Bereich mit seinen
  // zugeordneten Hosts direkt darunter (eingerueckt). Ganz ohne angelegte
  // Bereiche ist AREAS leer und hier passiert nichts anderes als vorher:
  // freiwillig, wie verlangt.
  const byArea = new Map();
  const rest = [];
  list.forEach(h => {
    if (hostAreaGiltig(h)){
      if (!byArea.has(h.area_id)) byArea.set(h.area_id, []);
      byArea.get(h.area_id).push(h);
    } else rest.push(h);
  });

  // Die laufende Nummer zaehlt seit der Rueckmeldung zu Schritt 5 je Gruppe
  // neu ab 1 - bereichslos genauso wie jeder einzelne Bereich. Sie hat sonst
  // keine Funktion (Drag & Drop haengt an der Host-ID, nicht an der Zahl),
  // deshalb einfach der Index aus map() statt eines gemeinsamen Zaehlers.
  let html = "";
  html += rest.map((h, idx) => renderUnit(h, idx, admin)).join("");
  // Ohne einen einzigen bereichslosen Host gibt es nichts, worauf man zum
  // Herausziehen ablegen koennte - dafuer diese schmale Zone direkt vor dem
  // ersten Bereich. Mit mindestens einem bereichslosen Host oben ist sie
  // ueberfluessig, der oberste Host uebernimmt dieselbe Rolle.
  if (!rest.length && AREAS.length) html += `<div class="areadrop-top"></div>`;
  // Erst die obersten Bereiche, darunter je ihre direkten Hosts und dann
  // ihre Unterbereiche (eine Stufe tiefer eingerueckt). Nur eine Ebene tief,
  // wie das Backend sie zulaesst.
  for (const area of AREAS.filter(a => !a.parent_id)){
    const inArea = byArea.get(area.id) || [];
    const kinder = AREAS.filter(a => a.parent_id === area.id);
    const kindHatHosts = kinder.some(k => (byArea.get(k.id) || []).length);
    // Ein leerer Bereich bleibt nur sichtbar (als Ablageziel zum
    // Hineinziehen), solange nichts herausgefiltert ist - sonst taucht
    // beim Suchen ein Bereich auf, zu dem gerade kein Treffer gehoert. Ein
    // oberster Bereich bleibt aber sichtbar, solange irgendein Unterbereich
    // Hosts zeigt, sonst haengen dessen Zeilen ohne Kopf in der Luft.
    if (!inArea.length && !kindHatHosts && !SORTABLE) continue;
    html += renderAreaHead(area, inArea.length, admin, 1);
    html += inArea.map((h, idx) => renderUnit(h, idx, admin, 1)).join("");
    for (const kind of kinder){
      const kindHosts = byArea.get(kind.id) || [];
      if (!kindHosts.length && !SORTABLE) continue;
      html += renderAreaHead(kind, kindHosts.length, admin, 2);
      html += kindHosts.map((h, idx) => renderUnit(h, idx, admin, 2)).join("");
    }
  }
  box.innerHTML = html;
}

// Ob dieser Host tatsaechlich einem noch vorhandenen Bereich angehoert.
// Eigene Funktion statt der blossen Pruefung auf area_id, damit render()
// und renderUnit() nicht auseinanderlaufen koennen: ein area_id, dessen
// Bereich es nicht mehr gibt (etwa der Rand eines gerade geloeschten
// Bereichs, bevor der naechste Abruf das nachzieht), soll weder gruppiert
// noch eingerueckt dargestellt werden.
function hostAreaGiltig(h){
  return !!(h.area_id && AREAS.some(a => a.id === h.area_id));
}

// Darf das angemeldete Konto Neustarts anlegen?
//
// Eine Funktion statt einer Variablen, weil renderUnit() und
// renderAreaHead() sie beide brauchen und in verschiedenen Bereichen
// liegen. Rein zur Anzeige: durchgesetzt wird das Recht in create_job(),
// hier geht es nur darum, keinen Knopf hinzustellen, der 403 liefert.
function darfNeustart(){
  // is_admin steht mit dabei, obwohl das Backend may_reboot fuer
  // Administratoren ohnehin auf true setzt: so bleibt der Knopf auch
  // dann richtig, wenn die Oberflaeche gegen ein Backend laeuft, das das
  // Feld noch nicht kennt.
  return !!(ME && (ME.is_admin || ME.may_reboot));
}


function renderAreaHead(area, count, admin, level = 1){
  // Dieselben Knoepfe wie eine Host-Zeile (Check/Patch/Restart), nur ohne
  // History - die gibt es nur pro einzelnem Host. "..." oeffnet denselben
  // Bearbeiten-Dialog, den auch Settings -> Bereiche benutzt (editArea()),
  // jetzt direkt von hier aus statt nur aus den Einstellungen.
  // level 2 = Unterbereich, wird per CSS eine Stufe tiefer eingerueckt.
  return `<div class="areahead" data-area-id="${area.id}" data-area-level="${level}">
    <div class="areahead-info">
      <span class="areahead-name">${esc(area.name)}</span>
      <span class="areahead-count">${t("area.host_count", { anzahl: count })}</span>
    </div>
    <div class="actions">
      <button data-act="areascan" data-id="${area.id}">${t("act.scan")}</button>
      <button data-act="areapatch" data-id="${area.id}">${t("act.patch")}</button>
      ${darfNeustart() ? `<button class="warn" data-act="areareboot" data-id="${area.id}">${t("act.reboot")}</button>` : ""}
      ${admin ? `<button data-act="areaedit" data-id="${area.id}">…</button>` : ""}
    </div>
  </div>`;
}

function renderUnit(h, i, admin, level = 0){
  const s = stateOf(h);
  const upd = h.updates_available||0, sec = h.security_updates||0;
  const waiting = h.approval_state === "pending";
  const stale = AGENT_VER && h.agent_version && h.agent_version !== AGENT_VER;

  const act = ACTIVE[h.id];
  // In notes landet Text, den der Agent bestimmt: h.agent_version (kommt
  // aus /agent/enroll und damit von JEDEM im Netz, ohne Anmeldung),
  // act.progress, act.job_type und die Gruende aus h.reboot_reasons.
  //
  // Beim Ausgeben deshalb esc() auf das zusammengesetzte Ganze - siehe
  // unten bei class="sub". Ohne das liess sich Markup in die Liste
  // schreiben, und weil der Klick-Handler auf JEDES [data-act] reagiert,
  // war das kein Schoenheitsfehler: ein eingeschleuster Knopf mit
  // data-act="approve" und freier data-id ist ein echter Knopf, per
  // style-Attribut als etwas Harmloses getarnt.
  const notes = [];
  if (act) notes.push(act.progress ? `${act.job_type}: ${act.progress}`
                                    : t("note.job_running", { auftrag: act.job_type }));
  // Auftrag steht auf laufend, der Host meldet aber nicht mehr. Ohne
  // diesen Hinweis sieht die Zeile aus wie normale Arbeit.
  if (act && h.status !== "online") notes.push(t("note.silent"));
  if (waiting) notes.push(t("note.pending_approval"));
  if (h.approval_state === "rejected") notes.push(t("note.rejected"));
  // Ein Host, der sich angemeldet, aber nie gemeldet hat. Kann ein
  // abgebrochenes Aufsetzen sein - oder jemand, der den Namen eines noch
  // nicht eingerichteten Systems belegt hat, damit dieses sich spaeter
  // nicht anmelden kann.
  if (h.approval_state === "pending" && !h.last_seen)
    notes.push(t("note.never_reported"));
  if (h.reboot_required) notes.push(rebootNote(h));
  // Vorschau, kein Zustand: die gefundenen Updates ziehen einen Neustart
  // nach sich. Nur zeigen, solange noch keiner aussteht - sonst stuenden
  // zwei Meldungen zum selben Thema nebeneinander.
  else if (h.updates_require_reboot && h.updates_available > 0)
    notes.push(t("note.updates_need_reboot"));
  // Nur Anzeige, kein Knopf mehr: das Ausrollen der Agents laeuft ueber die
  // Einstellungen (automatisch beim Heartbeat, oder von Hand ueber "Start").
  // Der frueher hier stehende Knopf "Agent" legte den Auftrag unmittelbar an
  // und umging dabei sowohl die Staffelung mit Pilot-Host als auch die Sperre
  // gegen doppelte Auftraege in _queue_selfupdate() (2026-08-29 entfernt).
  if (stale) notes.push(t("note.agent_outdated", { version: h.agent_version }));
  if (PLANNED[h.id]) notes.push(t("note.reboot_planned", {
    zeit: PLANNED[h.id].toLocaleString(zeitSprache(),
      {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) }));
  if (h.patch_enabled && h.next_patch_run) notes.push(t("note.updates_at", {
    zeit: fmtTime(h.next_patch_run,{weekday:"short",hour:"2-digit",minute:"2-digit"}) }));
  // Windows legt nach einem Neustart kumulativ nach. Der Nachschlag ist
  // vorgemerkt und laeuft an, sobald der Host wieder meldet.
  if (h.patch_followup_left > 0)
    notes.push(t("note.followup", { anzahl: h.patch_followup_left }));

  // Dritte Zeile: was der Host als letztes gemacht hat (Rueckmeldung
  // Schritt 5) - eigene Zeile statt in "notes" gemischt, weil es sich vom
  // aktuellen Zustand oben unterscheidet: eine abgeschlossene Vergangenheit,
  // kein laufender Hinweis. Nur scan/patch/reboot haben eine Formulierung -
  // andere Auftragsarten (etwa die Agent-Selbstaktualisierung) bleiben ohne
  // dritte Zeile, dafuer hat niemand danach gefragt.
  const zuletzt = LAST_ACTION[h.id];
  let letzteAktion = "";
  if (zuletzt){
    const zeit = fmtTime(zuletzt.finished_at, {weekday:"short", hour:"2-digit", minute:"2-digit"});
    const schluessel = {
      scan:   zuletzt.state === "done" ? "note.last_scan_done"   : "note.last_scan_failed",
      patch:  zuletzt.state === "done" ? "note.last_patch_done"  : "note.last_patch_failed",
      reboot: zuletzt.state === "done" ? "note.last_reboot_done" : "note.last_reboot_failed",
    }[zuletzt.job_type];
    if (schluessel) letzteAktion = t(schluessel, { zeit });
  }

  // level 0 = bereichslos (kein Einzug), 1 = in einem obersten Bereich,
  // 2 = in einem Unterbereich (doppelter Einzug). Die Hoehe kommt vom
  // Aufrufer, der die Schachtelung kennt - nicht mehr aus hostAreaGiltig().
  return `<div class="unit"${level ? ` data-area-level="${level}"` : ""} data-id="${h.id}">
      <div class="slot${SORTABLE ? " grab" : ""}"${SORTABLE ? ` draggable="true" title="${esc(t("list.drag"))}"` : ` title="${esc(t("list.drag_blocked"))}"`}>${String(i+1).padStart(2,"0")}</div>
      <div class="led ${LED[s]}"></div>
      <div class="hostcell">
        <div class="hostname">${esc(h.display_name || h.hostname)}</div>
        <div class="sub">${esc(h.hostname)}${notes.length ? " · " + esc(notes.join(" · ")) : ""}</div>
        ${letzteAktion ? `<div class="lastaction">${esc(letzteAktion)}</div>` : ""}
      </div>
      <div class="os">${h.os_type || "—"}</div>
      <div class="updates"><span class="chip ${upd?"warn":"zero"}">${upd}</span></div>
      <div class="reboot"><span class="chip ${sec?"crit":"zero"}">${sec}</span></div>
      <div class="cmk ${hasDt(h) ? "linked":""}">${
        h.checkmk_downtime_all
          ? `${t("list.all_hosts")} · ${h.downtime_minutes} min`
          : h.checkmk_hosts?.length
            ? esc(h.checkmk_hosts.join(", ")) + ` · ${h.downtime_minutes} min`
            : t("list.not_linked")}</div>
      <div class="actions">
        ${waiting
          ? (admin
             ? `<button class="primary" data-act="approve" data-id="${h.id}">${t("act.approve")}</button>
                <button class="danger" data-act="reject" data-id="${h.id}">${t("act.reject")}</button>`
             : `<span style="color:var(--muted-2);font-size:11px">${t("note.pending_approval")}</span>`)
          : `${act ? `<button class="primary" data-act="live" data-id="${act.id}" data-title="${esc(h.display_name||h.hostname)}">${t("act.live")}</button>` : ""}
             <button data-act="scan" data-id="${h.id}">${t("act.scan")}</button>
             <button data-act="patch" data-id="${h.id}">${t("act.patch")}</button>
             ${darfNeustart() ? `<button class="warn" data-act="reboot" data-id="${h.id}">${t("act.reboot")}</button>` : ""}
             <button data-act="detail" data-id="${h.id}">${t("act.history")}</button>`}
        ${admin ? `<button data-act="edithost" data-id="${h.id}">…</button>` : ""}
      </div>
    </div>`;
}

/* ---------- Reihenfolge per Drag and Drop ---------- */
/*
 * Verschiebt in erster Linie die Anzeige. Auf Zeitplaene, Wartungsfenster
 * oder die Ausfuehrung von Auftraegen hat die reine Reihenfolge keinen
 * Einfluss - es gibt keine Abarbeitung in Reihe, jeder Host wertet beim
 * eigenen Kontakt fuer sich aus. Landet ein Host dabei in einem anderen
 * Bereich (oder verlaesst seinen), ist das die Ausnahme: das speichert
 * moveHostArea() eigens, denn das WIRKT sich auf den Zeitplan aus - der
 * Bereich hat ja unter Umstaenden einen eigenen.
 *
 * Angefasst wird nur die Platznummer, nicht die ganze Zeile: sonst startet
 * jeder Zug an einer Schaltflaeche oder an markiertem Text einen Drag.
 */
function unitsBox(){ return document.getElementById("units"); }

function clearDropMarks(){
  unitsBox().querySelectorAll(".unit").forEach(u => {
    u.classList.remove("dropbefore", "dropafter");
  });
  unitsBox().querySelectorAll(".areahead").forEach(a => a.classList.remove("over"));
  unitsBox().querySelectorAll(".areadrop-top").forEach(a => a.classList.remove("over"));
}

async function saveOrder(){
  try {
    await api("POST", "/api/v1/hosts/order", { ids: HOSTS.map(h => h.id) });
  } catch(e){
    toast(t("msg.order_not_saved"), true);
    // Gespeicherten Stand zurueckholen, statt eine Anzeige stehen zu
    // lassen, die es auf dem Server nicht gibt.
    load().catch(() => {});
  }
}

async function moveHostArea(hostId, areaId){
  try {
    await api("POST", `/api/v1/hosts/${hostId}/area`, { area_id: areaId });
    toast(t("msg.area_saved"));
  } catch(e){
    toast(t("msg.area_not_saved"), true);
    // Wie bei saveOrder(): den echten Stand zurueckholen statt einer
    // Anzeige, die es auf dem Server so nicht gibt - etwa weil ein
    // nicht-administrativer Zugang das Verschieben zwischen Bereichen
    // nicht darf.
    load().catch(() => {});
  }
}

/**
 * Reine Rechenfunktion ohne DOM-Zugriff: aus der bisherigen Reihenfolge,
 * dem gezogenen Host und dem Ablageziel die neue Reihenfolge bestimmen -
 * und, falls das Ziel zu einem anderen Bereich gehoert (oder gar keinem),
 * das gleich mit.
 *
 * target ist eines von
 *   { kind: "unit", id, after }     - abgelegt auf einem anderen Host
 *   { kind: "areahead", areaId }    - abgelegt auf einem Bereichs-Kopf
 *   { kind: "beforeFirstArea" }     - abgelegt auf der Zone vor dem ersten
 *                                      Bereich (nur da im Markup, wenn kein
 *                                      bereichsloser Host als Alternative
 *                                      zur Verfuegung steht)
 *
 * Eigene Funktion statt Logik im Ereignis-Handler, damit sich das ohne
 * eine nachgebaute DOM prüfen laesst - wie _schedule_due() im Backend.
 */
function applyDrop(hosts, dragId, target){
  const from = hosts.findIndex(h => h.id === dragId);
  if (from < 0) return null;
  if (target.kind === "unit" && target.id === dragId) return null;

  const list = hosts.slice();
  const [moved] = list.splice(from, 1);
  const oldAreaId = moved.area_id || null;

  if (target.kind === "beforeFirstArea"){
    // Diese Zone gibt es nur, wenn schon kein bereichsloser Host da war -
    // der gezogene wird also automatisch der einzige, seine Position
    // innerhalb der "bereichslos"-Gruppe ist damit beliebig.
    list.unshift(moved);
    moved.area_id = null;
    return { hosts: list, areaChanged: oldAreaId !== null, newAreaId: null };
  }

  if (target.kind === "areahead"){
    const newAreaId = target.areaId;
    // Ans Ende der bisherigen Reihenfolge dieses Bereichs - oder, ist er
    // leer, einfach ganz hinten anhaengen.
    let lastIdx = -1;
    list.forEach((h, idx) => { if ((h.area_id || null) === newAreaId) lastIdx = idx; });
    list.splice(lastIdx >= 0 ? lastIdx + 1 : list.length, 0, moved);
    moved.area_id = newAreaId;
    return { hosts: list, areaChanged: oldAreaId !== newAreaId, newAreaId };
  }

  // Zielposition erst nach dem Herausnehmen bestimmen, sonst verschiebt
  // sich der Index um eins, wenn nach unten gezogen wird.
  const to = list.findIndex(h => h.id === target.id);
  if (to < 0){
    list.splice(from, 0, moved);
    return { hosts: list, areaChanged: false, newAreaId: oldAreaId };
  }
  // Der Bereich des Ziel-Hosts gilt auch fuer den gezogenen - so entsteht
  // "herausziehen" (Ziel ohne Bereich) und "hineinziehen" (Ziel in einem
  // Bereich) von selbst, ohne eigene Bedienelemente dafuer.
  const newAreaId = list[to].area_id || null;
  list.splice(target.after ? to + 1 : to, 0, moved);
  moved.area_id = newAreaId;
  return { hosts: list, areaChanged: oldAreaId !== newAreaId, newAreaId };
}

function initSorting(){
  const box = unitsBox();

  box.addEventListener("dragstart", e => {
    const handle = e.target.closest(".slot");
    if (!handle || !SORTABLE) return;
    const unit = handle.closest(".unit");
    DRAG_ID = parseInt(unit.dataset.id);
    unit.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    // Firefox startet ohne gesetzte Nutzdaten keinen Drag.
    e.dataTransfer.setData("text/plain", String(DRAG_ID));
  });

  box.addEventListener("dragover", e => {
    if (DRAG_ID === null) return;
    const dropTop = e.target.closest(".areadrop-top");
    if (dropTop){
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      clearDropMarks();
      dropTop.classList.add("over");
      return;
    }
    const areaHead = e.target.closest(".areahead");
    if (areaHead){
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      clearDropMarks();
      areaHead.classList.add("over");
      return;
    }
    const unit = e.target.closest(".unit");
    if (!unit || parseInt(unit.dataset.id) === DRAG_ID) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const box2 = unit.getBoundingClientRect();
    const after = e.clientY > box2.top + box2.height / 2;
    clearDropMarks();
    unit.classList.add(after ? "dropafter" : "dropbefore");
  });

  box.addEventListener("drop", e => {
    if (DRAG_ID === null) return;
    const dropTop = e.target.closest(".areadrop-top");
    const areaHead = e.target.closest(".areahead");
    const unit = e.target.closest(".unit");
    if (!dropTop && !areaHead && !unit) return;
    e.preventDefault();

    const draggedId = DRAG_ID;
    let target;
    if (dropTop){
      target = { kind: "beforeFirstArea" };
    } else if (areaHead){
      target = { kind: "areahead", areaId: parseInt(areaHead.dataset.areaId) };
    } else {
      const rect = unit.getBoundingClientRect();
      target = { kind: "unit", id: parseInt(unit.dataset.id),
                 after: e.clientY > rect.top + rect.height / 2 };
    }

    const result = applyDrop(HOSTS, draggedId, target);
    DRAG_ID = null;
    clearDropMarks();
    if (!result) return;

    HOSTS = result.hosts;
    render();
    saveOrder();
    if (result.areaChanged) moveHostArea(draggedId, result.newAreaId);
  });

  box.addEventListener("dragend", () => {
    DRAG_ID = null;
    clearDropMarks();
    unitsBox().querySelectorAll(".unit.dragging")
      .forEach(u => u.classList.remove("dragging"));
  });
}

/* ---------- Klicks verteilen ---------- */
/*
 * Statt onclick-Attributen im Markup: ein Handler am Dokument, der anhand
 * von data-act entscheidet.
 *
 * Zwei Gruende. Erstens laesst sich damit die Regel script-src ohne
 * 'unsafe-inline' setzen - Attribute im Markup gelten dem Browser als
 * eingebettetes Skript und waeren sonst blockiert. Zweitens fiel damit
 * eine ganze Fehlerklasse weg: in einem onclick landet ein Wert in einer
 * JavaScript-Zeichenkette innerhalb eines Attributs, und ein Hostname mit
 * einem Anfuehrungszeichen konnte daraus ausbrechen. In einem
 * data-Attribut ist ein Wert nur ein Wert.
 *
 * Handler, die im JavaScript gesetzt werden (el.onclick = fn), sind davon
 * nicht betroffen - die blockiert CSP nicht.
 */
const ACTIONS = {
  approve:    (d) => approve(+d.id),
  reject:     (d) => reject(+d.id),
  live:       (d) => showLive(+d.id, d.title),
  scan:       (d) => scan(+d.id),
  patch:      (d) => patch(+d.id),
  reboot:     (d) => rebootHost(+d.id),
  detail:     (d) => detail(+d.id),
  edithost:   (d) => editHost(+d.id),
  canceljob:  (d) => cancelJob(+d.id),
  pick:       (d) => togglePick(d.name),
  day:        (d) => toggleDay(d.day),
  togglereboot: (d) => toggleReboot(+d.id, d.username, !d.on),
  resetpw:    (d) => resetPw(+d.id, d.username),
  deluser:    (d) => delUser(+d.id, d.username),
  areaedit:   (d) => editArea(+d.id),
  areaup:     (d) => moveArea(+d.id, -1),
  areadown:   (d) => moveArea(+d.id, 1),
  areapick:   (d) => toggleAreaPick(d.name),
  areaday:    (d) => toggleAreaDay(d.day),
  areascan:   (d) => scanArea(+d.id),
  areapatch:  (d) => patchArea(+d.id),
  areareboot: (d) => rebootArea(+d.id),
  arearemove: (d) => removeArea(+d.id, false),
  areasubadd: (d) => addSubArea(+d.id),
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const fn = ACTIONS[el.dataset.act];
  if (!fn) return;
  // Ein Kontrollkaestchen im Zeileninneren schaltet sich selbst um und
  // wuerde durch das Weiterreichen ein zweites Mal umgeschaltet. Der
  // sichtbare Zustand wird ohnehin neu gezeichnet.
  if (el.tagName === "INPUT") e.preventDefault();
  fn(el.dataset);
});

/* ---------- Aktionen ---------- */
let PLANNED = {}, ACTIVE = {}, LAST_ACTION = {};
let REFRESH_TIMER = null, FOLLOWUPS = [];

/**
 * Aktualisierungstakt richtet sich nach der Lage: laufen Auftraege, wird
 * haeufiger geladen. Zusaetzlich werden nach dem Ende eines Auftrags noch
 * zwei Nachzuegler eingeplant - beim Selbstupdate meldet der Agent "fertig"
 * und startet erst danach neu, die neue Version steht also einige Sekunden
 * spaeter im Heartbeat.
 */
function scheduleRefresh(){
  clearTimeout(REFRESH_TIMER);
  const busy = Object.keys(ACTIVE).length > 0;
  REFRESH_TIMER = setTimeout(async () => {
    // Waehrend eines Drags nicht neu zeichnen: der Neuaufbau der Liste
    // wuerde die angefasste Zeile mitten in der Bewegung ersetzen und
    // den Vorgang abbrechen.
    if (!DRAG_ID) { try { await load(); } catch(e){} }
    scheduleRefresh();
  }, busy ? 3000 : 15000);
}

function followUp(){
  // Nachzuegler abbrechen, falls noch welche offen sind
  FOLLOWUPS.forEach(clearTimeout);
  FOLLOWUPS = [4000, 10000, 20000].map(ms =>
    setTimeout(() => load().catch(() => {}), ms));
}

async function load(){
  checkVersion();
  const wasBusy = Object.keys(ACTIVE);
  HOSTS = await api("GET", "/api/v1/hosts");
  // Freiwillig: ohne angelegte Bereiche bleibt die Liste wie bisher.
  // Scheitert der Abruf, wird schlicht so getan, als gaebe es keine -
  // wie bei CMK_HOSTS und PLANNED unten, kein Grund, die ganze
  // Uebersicht scheitern zu lassen.
  try { AREAS = await api("GET", "/api/v1/areas"); } catch(e){ AREAS = []; }
  try {
    ACTIVE = {};
    (await api("GET", "/api/v1/jobs/active")).forEach(j => { ACTIVE[j.host_id] = j; });
  } catch(e){ ACTIVE = {}; }
  // Fuer die dritte Zeile in der Uebersicht (Rueckmeldung Schritt 5) - was
  // der Host als letztes gemacht hat und ob es geklappt hat.
  try {
    LAST_ACTION = {};
    (await api("GET", "/api/v1/jobs/last")).forEach(j => { LAST_ACTION[j.host_id] = j; });
  } catch(e){ LAST_ACTION = {}; }

  // Ein Auftrag ist gerade fertig geworden
  if (wasBusy.length && !Object.keys(ACTIVE).length) followUp();
  // Eingeplante Neustarts einsammeln, damit sie in der Uebersicht stehen
  try {
    const jobs = await api("GET", "/api/v1/jobs?limit=300");
    PLANNED = {};
    jobs.filter(j => j.state === "pending" && j.scheduled_at && j.job_type === "reboot")
        .forEach(j => {
          const t = new Date(j.scheduled_at);
          if (!PLANNED[j.host_id] || t < PLANNED[j.host_id]) PLANNED[j.host_id] = t;
        });
  } catch(e){ PLANNED = {}; }
  render();
}

async function afterAction(){
  await load().catch(() => {});
  scheduleRefresh();
  followUp();
}

async function scan(id){
  await api("POST", `/api/v1/hosts/${id}/jobs`, {job_type:"scan", params:{}});
  toast(t("msg.scan_scheduled"));
  afterAction();
}

async function patch(id){
  const h = HOSTS.find(x => x.id === id);
  const policy = h.auto_reboot
    ? (h.maintenance_window
        ? t("ask.policy_window", { fenster: h.maintenance_window })
        : t("ask.policy_after"))
    : t("ask.policy_report_only");
  // Vor der Bestaetigung ansagen, dass ein Neustart faellig wird. Bisher
  // stand das erst im Bericht nach dem Scan, und dort auch nur
  // missverstaendlich.
  const willReboot = h.updates_require_reboot ? t("ask.will_reboot") + "\n" : "";
  // Ohne (aktuellen) Scan-Stand ist "{anzahl} Updates" nur eine Vermutung -
  // patch_windows()/patch_linux() ermitteln beim Patchen ohnehin live, was
  // ansteht. Knopf bleibt deshalb immer aktiv (frueher: gesperrt ohne
  // updates_available), die Frage nennt dann keine Zahl, die schon beim
  // Klicken veraltet sein kann.
  const frage = h.updates_available > 0
    ? t("ask.patch", { anzahl: h.updates_available, host: h.hostname })
    : t("ask.patch_unknown", { host: h.hostname });
  if (!confirm(frage + "\n\n" + willReboot + policy)) return;
  await api("POST", `/api/v1/hosts/${id}/jobs`, {job_type:"patch", params:{}});
  toast(t("msg.patch_scheduled"));
  afterAction();
}

let REBOOT_ID = null;

function rebootHost(id){
  const h = HOSTS.find(x => x.id === id);
  REBOOT_ID = id;
  const linked = hasDt(h);

  document.getElementById("rbTitle").textContent =
    t("reboot.dialog_title", { host: h.display_name || h.hostname });
  document.getElementById("rbInfo").innerHTML =
    t("reboot.dialog_info", { host: esc(h.hostname) });
  rbDowntime.checked = true;
  rbDtMin.value = h.downtime_minutes || 30;
  rbGrace.value = 10;

  document.getElementById("rbWarn").innerHTML = linked
    ? t("reboot.downtime_target", { ziel: esc(dtTargetText(h)) })
    : `<span style="color:var(--led-pending)">${t("reboot.no_link")}</span>`;

  document.getElementById("dlgReboot").showModal();
}

document.getElementById("rbGo").onclick = async () => {
  const grace = parseInt(rbGrace.value) || 10;
  const dtMin = parseInt(rbDtMin.value) || 30;
  await api("POST", `/api/v1/hosts/${REBOOT_ID}/jobs`, {
    job_type: "reboot",
    set_downtime: rbDowntime.checked,
    grace_minutes: grace,
    downtime_minutes: dtMin,
    params: {}
  });
  document.getElementById("dlgReboot").close();
  toast(t("msg.reboot_triggered", { minuten: grace }));
  afterAction();
};

async function approve(id){
  try {
    await api("POST", `/api/v1/hosts/${id}/approve`);
  } catch(e){
    // api() hat die Begruendung des Backends bereits angezeigt - beim
    // erreichten Host-Limit steht dort, wie viele belegt sind und wohin
    // man sich wendet. Hier nur den Ablauf sauber beenden.
    return;
  }
  await load();
  toast(t("msg.host_approved"));
}
async function reject(id){
  if (!confirm(t("ask.reject_host"))) return;
  await api("POST", `/api/v1/hosts/${id}/reject`); await load(); toast(t("msg.host_rejected"));
}

async function scanAll(){
  const ok = HOSTS.filter(h => h.approval_state === "approved");
  for (const h of ok){ try { await api("POST", `/api/v1/hosts/${h.id}/jobs`, {job_type:"scan", params:{}}); } catch(e){} }
  toast(t("msg.scan_many", { anzahl: ok.length }));
  afterAction();
}

async function detail(id){
  const h = HOSTS.find(x => x.id === id);
  document.getElementById("dTitle").textContent = h.display_name || h.hostname;
  document.getElementById("log").textContent = t("common.loading");
  document.getElementById("dlgDetail").showModal();
  const jobs = await api("GET", `/api/v1/jobs?host_id=${id}&limit=25`);
  const ups  = await api("GET", `/api/v1/hosts/${id}/updates`);
  const L = [];
  L.push(t("history.system", { os: h.os_version || "?", agent: h.agent_version || "?", ip: h.ip_address || "?" }));
  L.push(t("history.enrolled", { when: fmtTime(h.enrolled_at) }) + (h.enrolled_from_ip ? t("history.enrolled_from", { ip: h.enrolled_from_ip }) : ""));
  L.push("", t("history.open_updates", { anzahl: ups.length }));
  ups.slice(0,80).forEach(u => L.push(`  ${u.is_security?t("history.security_marker"):"     "} ${u.package_id}  ${u.new_version||""}`));
  document.getElementById("log").textContent = L.join("\n");

  // Auftragsliste anklickbar, damit das Protokoll abrufbar ist
  const rows = jobs.map(j => {
    const when = fmtTime(j.created_at);
    const cls = j.state === "failed" ? "err" : j.state === "running" ? "run"
              : j.state === "done" ? "ok" : "";
    return `<div class="vrow" style="margin-bottom:5px">
      <span>#${j.id} · ${esc(j.job_type)}<br>
        <span style="color:var(--muted-2)">${when}${j.scheduled_at ? t("history.scheduled_suffix") : ""}${j.error ? " · " + esc(j.error.slice(0,60)) : ""}</span></span>
      <span style="display:flex;gap:8px;align-items:center">
        <span class="state ${cls}">${esc(t((LV_STATE[j.state]||[j.state])[0]))}</span>
        <button data-act="live" data-id="${j.id}" data-title="#${j.id} ${esc(j.job_type)}">${t("act.log")}</button>
      </span></div>`;
  }).join("");
  document.getElementById("dJobs").innerHTML =
    `<div class="section-title" style="margin-bottom:8px">${esc(t("history.jobs_title", { anzahl: jobs.length }))}</div>`
    + (rows || `<span style="color:var(--muted-2)">${t("history.none")}</span>`);
}

/* ---------- Live-Ausgabe ---------- */
let LIVE = {jobId:null, offset:0, timer:null};

async function showLive(jobId, title){
  clearInterval(LIVE.timer);
  LIVE = {jobId, offset:0, timer:null};
  document.getElementById("lvTitle").textContent = title || t("job.number", { id: jobId });
  document.getElementById("lvLog").textContent = "";
  document.getElementById("lvProgress").textContent = t("common.loading");
  document.getElementById("lvMeta").textContent = "";
  document.getElementById("lvState").textContent = "";
  document.getElementById("dlgLive").showModal();
  await tickLive();
  LIVE.timer = setInterval(tickLive, 1000);
}

const LV_STATE = {
  pending:["job.pending",""], running:["job.running","run"], done:["job.done","ok"],
  failed:["job.failed","err"], cancelled:["job.cancelled","warn"]
};

async function tickLive(){
  if (LIVE.jobId === null) return;
  let d;
  try {
    d = await api("GET", `/api/v1/jobs/${LIVE.jobId}/log?offset=${LIVE.offset}`);
  } catch(e){ clearInterval(LIVE.timer); return; }

  const box = document.getElementById("lvLog");
  if (d.offset < LIVE.offset){ box.textContent = ""; LIVE.offset = 0; }
  if (d.text){
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
    box.textContent += d.text;
    LIVE.offset = d.offset;
    if (document.getElementById("lvFollow").checked || atBottom)
      box.scrollTop = box.scrollHeight;
  }

  const [schluessel, cls] = LV_STATE[d.state] || [d.state, ""];
  const st = document.getElementById("lvState");
  st.textContent = t(schluessel); st.className = "state " + cls;

  document.getElementById("lvProgress").textContent =
    d.progress || (d.running ? t("live.running") : d.error ? t("live.error", { fehler: d.error }) : "—");
  document.getElementById("lvMeta").textContent =
    `${(d.size/1024).toFixed(1)} KB` + (d.job_type ? ` · ${d.job_type}` : "");

  // Ohne Ausgabe je nach Zustand erklaeren, warum. Frueher stand hier
  // pauschal ein Hinweis auf Auftraege von vor 0.6.0 - bei einem noch
  // wartenden Auftrag war das schlicht falsch.
  if (!box.textContent && !d.exists && !d.running){
    box.textContent =
      d.state === "pending"
        ? t("live.pending_hint")
      : d.state === "cancelled"
        ? t("live.cancelled_hint")
          + (d.error ? `\n\n${d.error}` : "")
        : t("live.no_output");
  }

  // Nach Abschluss noch zwei Runden nachladen, dann aufhören
  if (!d.running && d.state !== "pending"){
    if (LIVE.done){
      clearInterval(LIVE.timer); LIVE.timer = null;
      // Uebersicht nachziehen. Beim Selbstupdate meldet der Agent "fertig"
      // und startet erst danach neu - die neue Version kommt verzoegert.
      afterAction();
    }
    LIVE.done = true;
  } else {
    LIVE.done = false;
  }
}

document.getElementById("dlgLive").addEventListener("close", () => {
  clearInterval(LIVE.timer); LIVE = {jobId:null, offset:0, timer:null};
});
document.getElementById("lvCopy").onclick = () =>
  copy(document.getElementById("lvLog").textContent, t("act.log"));

/* ---------- Host bearbeiten ---------- */
function renderPicker(){
  const q = document.getElementById("hCmkSearch").value.toLowerCase();
  const list = CMK_HOSTS.filter(c => !q || c.name.toLowerCase().includes(q));
  const box = document.getElementById("hCmkPicker");
  box.innerHTML = list.length
    ? list.slice(0,300).map(c => `<div data-act="pick" data-name="${esc(c.name)}">
        <input type="checkbox" ${PICKED.has(c.name)?"checked":""} data-act="pick" data-name="${esc(c.name)}">
        <span>${esc(c.name)}</span></div>`).join("")
    : `<div style="color:var(--muted-2)">${CMK_HOSTS.length ? t("host.cmk_no_match") : t("host.cmk_none_loaded")}</div>`;

  // Bereits verknüpfte Namen, die Checkmk nicht kennt, trotzdem zeigen
  const extra = [...PICKED].filter(n => !CMK_HOSTS.some(c => c.name === n));
  document.getElementById("hCmkPicked").innerHTML = [...PICKED].length
    ? [...PICKED].map(n => `<span>${esc(n)}${extra.includes(n) ? t("host.cmk_unknown_suffix") : ""}</span>`).join("")
    : `<span style="border-color:var(--rail);color:var(--muted-2)">${t("host.cmk_none_picked")}</span>`;
}
function togglePick(name){
  PICKED.has(name) ? PICKED.delete(name) : PICKED.add(name);
  renderPicker();
}
document.getElementById("hCmkSearch").oninput = renderPicker;

/* Reiter im Host-Dialog */
const HTABS = ["htAllg","htCmk","htUpd","htPlan","htDt"];
const WDAYS = [["MO","Mo"],["DI","Di"],["MI","Mi"],["DO","Do"],["FR","Fr"],["SA","Sa"],["SO","So"]];
let PATCH_DAYS = new Set();

function renderDays(){
  document.getElementById("upDays").innerHTML = WDAYS.map(([k,l]) =>
    `<span data-act="day" data-day="${k}" style="cursor:pointer;user-select:none;padding:5px 11px;
      ${PATCH_DAYS.has(k)
        ? "border-color:var(--accent);color:var(--accent);background:var(--accent-bg)"
        : "border-color:var(--rail);color:var(--muted-2)"}">${l}</span>`).join("");
}
function toggleDay(k){
  PATCH_DAYS.has(k) ? PATCH_DAYS.delete(k) : PATCH_DAYS.add(k);
  renderDays();
}
document.querySelectorAll("#hTabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#hTabs button").forEach(x => x.classList.toggle("on", x === b));
  HTABS.forEach(id =>
    document.getElementById(id).classList.toggle("on", id === b.dataset.htab));
});

/* ---------- Tabellenkopfzeile ---------- */
// Eine Quelle fuer die Beschriftungen. Die Reihenfolge muss zu den Zellen
// einer Datenzeile passen; die zweite Spalte ist der Zustandsbalken und
// bleibt ohne Text.
// Schluessel statt Text: die Liste wird beim Laden ausgewertet, da steht
// die Sprache noch nicht fest. Uebersetzt wird erst beim Zeichnen.
const RACK_COLUMNS = ["rack.no", "", "rack.host", "rack.system", "rack.updates",
                      "rack.security", "rack.checkmk", "rack.actions"];

// Muss mit der Medienabfrage im Stylesheet uebereinstimmen. Der
// Frontend-Test vergleicht beide, damit sie nicht auseinanderlaufen.
const RACK_NARROW_QUERY = "(max-width:1000px)";

function renderRackHead(){
  const head = document.getElementById("rackHead");
  if (!head) return;
  const narrow = matchMedia(RACK_NARROW_QUERY).matches;
  if (narrow){
    // Nicht nur unsichtbar, sondern gar nicht vorhanden.
    head.innerHTML = "";
    return;
  }
  head.innerHTML = RACK_COLUMNS.map((label, i) => {
    if (i === 0) return `<div class="slot">${esc(t(label))}</div>`;
    if (!label) return "<div></div>";
    const last = i === RACK_COLUMNS.length - 1;
    return `<div${last ? ' style="text-align:right"' : ""}>${esc(t(label))}</div>`;
  }).join("");
}

// Ob fuer diesen Host eine Downtime gesetzt werden kann. Das Flag
// "alle Checkmk-Hosts" ersetzt die Verknuepfung - ohne diese Pruefung
// warnt die Oberflaeche weiter vor einem fehlenden Checkmk-Host,
// obwohl das Backend die Downtime setzt.
function hasDt(h){
  return !!(h.checkmk_hosts?.length || h.checkmk_downtime_all);
}
function dtTargetText(h){
  return h.checkmk_downtime_all
    ? t("dt.all_cmk_hosts")
    : (h.checkmk_hosts || []).join(", ");
}

function localIso(d){
  const p = n => String(n).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function editHost(id){
  const h = HOSTS.find(x => x.id === id);
  EDIT_ID = id;
  document.getElementById("hTitle").textContent = h.hostname;
  hName.value = h.display_name || "";
  hAuto.checked = !!h.auto_reboot;
  hWindow.value = h.maintenance_window || "";
  hDowntime.value = h.downtime_minutes || 30;
  hDtAll.checked = !!h.checkmk_downtime_all;
  PICKED = new Set(h.checkmk_hosts || []);
  document.getElementById("hCmkSearch").value = "";
  dtMinutes.value = ""; dtStart.value = ""; dtEnd.value = "";
  dtComment.value = ""; document.getElementById("dtOut").textContent = "";

  document.getElementById("hInfo").innerHTML =
    t("host.info", {
      os: esc(h.os_version || "?"), agent: esc(h.agent_version || "?"),
      ip: esc(h.ip_address || "?"), when: fmtTime(h.enrolled_at)
    });

  // Planung vorbelegen
  plWhen.value = "";
  plDowntime.checked = true;
  plGrace.value = 120;
  const linked = hasDt(h);
  document.getElementById("plHint").innerHTML = linked
    ? t("plan.downtime_summary", { minuten: h.downtime_minutes, ziel: esc(dtTargetText(h)) })
    : `<span style="color:var(--led-pending)">${t("plan.no_link_hint")}</span>`;

  // Erster Reiter beim Öffnen
  document.querySelectorAll("#hTabs button").forEach((x,i) => x.classList.toggle("on", i === 0));
  HTABS.forEach((id,i) => document.getElementById(id).classList.toggle("on", i === 0));

  // Update-Zeitplan
  upEnabled.checked = !!h.patch_enabled;
  PATCH_DAYS = new Set(h.patch_days || []);
  upTime.value = h.patch_time || "03:00";
  upGrace.value = h.patch_grace_hours || 4;
  upReboot.checked = !!h.patch_auto_reboot;
  document.getElementById("upNext").textContent = h.next_patch_run
    ? fmtTime(h.next_patch_run) : "—";
  document.getElementById("upLast").textContent = h.last_patch_run
    ? fmtTime(h.last_patch_run) : t("plan.never");
  document.getElementById("upRebootHint").innerHTML = (linked
    ? t("plan.reboot_downtime_hint", { minuten: h.downtime_minutes })
    : `<span style="color:var(--led-pending)">${t("plan.reboot_no_link_hint")}</span>`)
    // Das Wartungsfenster aus dem Reiter Allgemein greift auch hier. Ohne
    // diesen Hinweis wundert man sich, warum der Neustart trotz Haken
    // ausbleibt.
    + (h.maintenance_window
      ? t("plan.reboot_window_hint", { fenster: esc(h.maintenance_window) })
      : "");
  renderDays();

  renderPicker();
  loadPlanned();
  document.getElementById("dlgHost").showModal();
}

/* ---------- Neustart planen ---------- */
async function loadPlanned(){
  const box = document.getElementById("plList");
  try {
    const jobs = await api("GET", `/api/v1/jobs?host_id=${EDIT_ID}&limit=50`);
    const open = jobs.filter(j => j.state === "pending" && j.scheduled_at);
    box.innerHTML = open.length
      ? open.map(j => {
          const when = fmtTime(j.scheduled_at);
          const dt = j.params?.skip_downtime ? t("plan.without_downtime") : t("plan.with_downtime");
          return `<div class="vrow" style="margin-bottom:5px">
            <span>${esc(t("plan.reboot_on", { when }))}<br><span style="color:var(--muted-2)">${dt}${esc(t("plan.job_ref", { id: j.id }))}</span></span>
            <button class="danger" data-act="canceljob" data-id="${j.id}">${t("common.cancel")}</button>
          </div>`;
        }).join("")
      : t("plan.none");
  } catch(e){ box.textContent = t("msg.load_failed"); }
}

async function cancelJob(id){
  if (!confirm(t("ask.cancel_scheduled_reboot"))) return;
  await api("DELETE", `/api/v1/jobs/${id}`);
  await loadPlanned();
  await load();
  toast(t("msg.cancelled"));
}

document.getElementById("plIn2h").onclick = () => {
  const d = new Date(Date.now() + 2*3600*1000);
  d.setSeconds(0,0);
  plWhen.value = localIso(d);
};
document.getElementById("plTonight").onclick = () => {
  const d = new Date();
  d.setHours(22,0,0,0);
  if (d <= new Date()) d.setDate(d.getDate()+1);
  plWhen.value = localIso(d);
};

document.getElementById("plSet").onclick = async () => {
  if (!plWhen.value){ toast(t("msg.need_datetime"), true); return; }
  const when = new Date(plWhen.value);
  if (when <= new Date()){ toast(t("msg.time_in_past"), true); return; }

  const h = HOSTS.find(x => x.id === EDIT_ID);
  const linked = hasDt(h);
  const wantDt = plDowntime.checked;

  let msg = t("ask.schedule_reboot",
               { host: h.hostname, zeit: when.toLocaleString(zeitSprache()) }) + "\n\n";
  if (wantDt && linked)
    msg += t("ask.downtime_first", {
      minuten: h.downtime_minutes,
      ziel: h.checkmk_downtime_all ? t("dt.all_cmk_hosts") : h.checkmk_hosts.join("\n"),
    });
  else if (wantDt && !linked)
    msg += t("ask.downtime_wanted_no_link");
  else
    msg += t("ask.no_downtime");
  msg += t("ask.grace_hint", { minuten: parseInt(plGrace.value)||120 });

  if (!confirm(msg)) return;

  await api("POST", `/api/v1/hosts/${EDIT_ID}/jobs`, {
    job_type: "reboot",
    scheduled_at: when.toISOString(),
    set_downtime: wantDt,
    grace_minutes: parseInt(plGrace.value) || 120,
    params: {}
  });
  plWhen.value = "";
  await loadPlanned();
  await load();
  toast(t("msg.reboot_scheduled"));
};

document.getElementById("hSave").onclick = async () => {
  if (upEnabled.checked && (!PATCH_DAYS.size || !upTime.value)){
    toast(t("msg.need_day_time"), true); return;
  }
  await api("PATCH", `/api/v1/hosts/${EDIT_ID}`, {
    display_name: hName.value.trim() || null,
    checkmk_hosts: [...PICKED],
    checkmk_downtime_all: hDtAll.checked,
    downtime_minutes: parseInt(hDowntime.value) || 30,
    auto_reboot: hAuto.checked,
    maintenance_window: hWindow.value.trim() || null,
    patch_enabled: upEnabled.checked,
    patch_days: [...PATCH_DAYS],
    patch_time: upTime.value || null,
    patch_auto_reboot: upReboot.checked,
    patch_grace_hours: parseInt(upGrace.value) || 4
  });
  document.getElementById("dlgHost").close();
  await load();
  toast(t("msg.saved"));
};

document.getElementById("hResetToken").onclick = async () => {
  const h = HOSTS.find(x => x.id === EDIT_ID);
  if (!confirm(t("ask.revoke_token", { host: h.hostname }) + t("ask.revoke_token_detail"))) return;
  await api("POST", `/api/v1/hosts/${EDIT_ID}/reset-token`);
  document.getElementById("dlgHost").close();
  await load();
  toast(t("msg.token_revoked"));
};

document.getElementById("hDelete").onclick = async () => {
  const h = HOSTS.find(x => x.id === EDIT_ID);
  if (!confirm(t("ask.remove_host", { host: h.hostname }))) return;
  await api("DELETE", `/api/v1/hosts/${EDIT_ID}`);
  document.getElementById("dlgHost").close();
  await load();
  toast(t("msg.host_removed"));
};

/* ---------- Downtime von Hand ---------- */
document.getElementById("dtSet").onclick = async () => {
  const body = {comment: dtComment.value.trim() || t("dt.comment_example")};
  if (dtStart.value && dtEnd.value){
    body.start = new Date(dtStart.value).toISOString();
    body.end = new Date(dtEnd.value).toISOString();
  } else if (dtMinutes.value){
    body.minutes = parseInt(dtMinutes.value);
  } else {
    toast(t("msg.need_minutes"), true); return;
  }
  const res = await api("POST", `/api/v1/hosts/${EDIT_ID}/downtime`, body);
  const out = document.getElementById("dtOut");
  out.textContent = t("dt.result_set", { minuten: res.minutes, liste: res.ok.join(", ") || "—" })
    + (Object.keys(res.failed||{}).length ? t("dt.result_failed", { liste: Object.keys(res.failed).join(", ") }) : "");
  toast(t("msg.downtime_set"));
};
document.getElementById("dtClear").onclick = async () => {
  if (!confirm(t("ask.cancel_downtimes"))) return;
  const res = await api("DELETE", `/api/v1/hosts/${EDIT_ID}/downtime`);
  document.getElementById("dtOut").textContent =
    t("dt.result_cleared") + Object.entries(res.removed||{}).map(([k,v]) => `${k} (${v})`).join(", ");
  toast(t("msg.downtime_cancelled"));
};
document.getElementById("dtList").onclick = async () => {
  const res = await api("GET", `/api/v1/hosts/${EDIT_ID}/downtime`);
  const out = document.getElementById("dtOut");
  if (res.reason){ out.textContent = res.reason; return; }
  out.textContent = res.downtimes.length
    ? res.downtimes.map(d => `${d.host_name}${d.service_description ? " / "+d.service_description : ""} — ${d.comment}`).join("\n")
    : t("dt.none_active");
};

/* ---------- Einstellungen ---------- */
document.querySelectorAll("[data-close]").forEach(b => b.onclick = e => e.target.closest("dialog").close());
// Eingegrenzt auf #sTabs und die eigenen Bereiche. Ein ungenauer Selektor
// hat hier vorher die Reiter des Host-Dialogs mit ueberschrieben.
//
// Aus dem Markup abgeleitet statt daneben gepflegt. Die fest verdrahtete
// Liste ist beim Hinzufuegen des Reiters 'Sprache' auseinandergelaufen:
// der Knopf war da, der Bereich auch, nur die Liste kannte ihn nicht -
// und der Dialog blieb beim Klick leer. Zwei Wahrheiten ueber dieselbe
// Sache, von denen eine nicht mitgepflegt wurde.
//
// Der enge Selektor bleibt: '#sTabs button' und nicht '.tabs button',
// sonst greift es in die Reiter des Host-Dialogs hinein.
const STABS = [...document.querySelectorAll("#sTabs button")]
  .map(b => b.dataset.tab).filter(Boolean);
document.querySelectorAll("#sTabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#sTabs button").forEach(x => x.classList.toggle("on", x === b));
  STABS.forEach(id => document.getElementById(id).classList.toggle("on", id === b.dataset.tab));
});
document.getElementById("btnSettings").onclick = () => {
  const isAdmin = !!(ME && ME.is_admin);
  // Fuer Benutzer ist Konto der erste sichtbare Reiter. Ohne das
  // stuende der Dialog auf Agents, das dort ausgeblendet ist - man saehe
  // eine leere Flaeche.
  const first = isAdmin ? "tabAgents" : "tabAccount";
  document.querySelectorAll("#sTabs button")
    .forEach(x => x.classList.toggle("on", x.dataset.tab === first));
  STABS.forEach(id => document.getElementById(id).classList.toggle("on", id === first));
  document.getElementById("dlgSettings").showModal();
  loadAccount();
  // Fuer jeden lesbar: wer am Limit scheitert, soll den Grund sehen.
  loadLizenz();
  // Die Routen dahinter sind ohnehin auf Administratoren beschraenkt -
  // aufrufen wuerde nur 403 erzeugen und den Dialog mit Fehlern fuellen.
  if (isAdmin){
    loadAgentsTab(); loadUpdate(); loadCmkForm(); loadRollout(); loadBuildStatus();
    loadUsers(); loadAudit(); loadProxy(); loadAreasTab(); loadSyslog();
    loadMfaPolicy();
  }
};
document.getElementById("dlgSettings").addEventListener("close", () => {
  clearInterval(UPD_TIMER); UPD_TIMER = null;
  clearInterval(BUILD_TIMER); BUILD_TIMER = null;
  // Token vergessen, sobald der Dialog zu ist. Es bleibt serverseitig
  // gueltig bis zum Ablauf - aber es soll nicht beim naechsten Oeffnen
  // wieder dastehen, als waere es frisch.
  clearInterval(TOKEN_TIMER); TOKEN_TIMER = null;
  INSTALL_TOKEN = null;
  clearInterval(PROXY_TIMER); PROXY_TIMER = null;
});

document.getElementById("cSave").onclick = async () => {
  await api("POST", "/api/v1/checkmk/config", {
    url: cUrl.value.trim(), site: cSite.value.trim(), user: cUser.value.trim(),
    secret: cSecret.value, verify_ssl: cVerify.checked
  });
  cSecret.value = "";
  CMK_STATUS = null;
  await loadCmk();
  await loadCmkForm();
  toast(t("msg.cmk_connected"));
};

/* ---------- Agents ---------- */
async function downloadPkg(name){
  try {
    const res = await fetch(`${API}/api/v1/packages/${encodeURIComponent(name)}`,
                            {credentials: "same-origin"});
    if (!res.ok){ toast(t("msg.download_failed"), true); return; }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  } catch(e){ toast(t("msg.download_failed"), true); }
}

async function loadAgentsTab(){
  let data = null, loadError = null;
  try {
    data = await api("GET", "/api/v1/packages");
  } catch(e){ loadError = e; }

  // Die oeffentliche Adresse mitholen. Scheitert das, bleibt es bei der
  // Adresse des Aufrufs - der Befehl ist dann immer noch brauchbar.
  try { PUBLIC_URL = (await api("GET", "/api/v1/proxy-settings")).public_url || ""; }
  catch(e){}

  try { renderAgentsTab(data, loadError); }
  catch(e){
    console.error(e);
    toast(t("msg.agents_display_failed", { fehler: e.message || e }), true);
  }
}

/**
 * Zeigt genau ein Linux- und ein Windows-Paket, dazu die beiden
 * Einrichtungsbefehle. Die Befehle stehen immer da - der Dateiname ergibt
 * sich aus der Agent-Version, auch wenn das Paket noch nicht gebaut ist.
 */
function renderAgentsTab(data, loadError){
  // Die Adresse aus den Einstellungen hat Vorrang vor der, unter der das
  // Dashboard gerade aufgerufen wurde. Sonst traegt ein Host, den man
  // ueber den internen Port einrichtet, dauerhaft die interne Adresse -
  // und spraeche unverschluesselt, ohne dass es auffaellt.
  const base = (PUBLIC_URL || location.origin).replace(/\/+$/, "");
  const want = data?.agent_version || "";
  AGENT_VER = want;
  document.getElementById("agVer").textContent =
    want || (loadError ? t("msg.unavailable") : "—");

  document.getElementById("agBase").innerHTML =
    t("settings.agents.cmd_base", { base: esc(base) })
    + (PUBLIC_URL
        ? t("settings.agents.cmd_base_fixed")
        : t("settings.agents.cmd_base_dynamic"));

  const debName = `co37-agent_${want}_all.deb`;
  const rpmName = `co37-agent-${want}-1.noarch.rpm`;
  const msiName = `co37-agent-${want}.msi`;

  // Abweichende Benennung melden statt stillschweigend zu beheben
  const warnBox = document.getElementById("pkgWarn");
  const problems = (data?.mismatch || []).slice();
  if (data?.older?.length)
    problems.push(t("settings.agents.older_files", { liste: data.older.join(", ") }));
  warnBox.innerHTML = problems.length
    ? problems.map(t => `<div style="color:var(--led-pending)">${esc(t)}</div>`).join("")
    : "";
  warnBox.style.display = problems.length ? "flex" : "none";

  // ---- Linux ----
  const lin = data?.linux;
  document.getElementById("infoLinux").textContent = lin
    ? `${lin.name} · ${fmtSize(lin.size)} · ${fmtTime(lin.built_at)}`
      + (lin.matches ? "" : t("settings.agents.version_mismatch"))
    : loadError ? t("msg.unavailable") : t("settings.agents.not_built");
  const dlL = document.getElementById("dlLinux");
  dlL.disabled = !lin;
  dlL.textContent = lin ? t("settings.agents.download_deb") : t("settings.agents.not_available");
  dlL.onclick = lin ? () => downloadPkg(lin.name) : null;

  // ---- Linux, RPM ----
  // Ein Paket fuer beide RPM-Familien: noarch, und die drei
  // Abhaengigkeiten heissen auf Red Hat wie auf SUSE gleich.
  const rpm = data?.rpm;
  document.getElementById("infoRpm").textContent = rpm
    ? `${rpm.name} · ${fmtSize(rpm.size)} · ${fmtTime(rpm.built_at)}`
      + (rpm.matches ? "" : t("settings.agents.version_mismatch"))
    : loadError ? t("msg.unavailable") : t("settings.agents.not_built");
  const dlR = document.getElementById("dlRpm");
  dlR.disabled = !rpm;
  dlR.textContent = rpm ? t("settings.agents.download_rpm") : t("settings.agents.not_available");
  dlR.onclick = rpm ? () => downloadPkg(rpm.name) : null;

  // ---- Windows ----
  const win = data?.windows;
  document.getElementById("infoWin").textContent = win
    ? `${win.name} · ${fmtSize(win.size)} · ${fmtTime(win.built_at)}`
      + (win.matches ? "" : t("settings.agents.version_mismatch"))
    : loadError ? t("msg.unavailable") : t("settings.agents.not_built");
  const dlW = document.getElementById("dlWin");
  dlW.disabled = !win;
  dlW.textContent = win ? t("settings.agents.download_msi") : t("settings.agents.not_available");
  dlW.onclick = win ? () => downloadPkg(win.name) : null;

  // Der Linux-Befehl braucht ein Token und wird erst auf Knopfdruck
  // gebaut. Namen des Pakets hier merken, damit das spaeter ohne erneuten
  // Abruf geht.
  LINUX_PKG = lin?.name || debName;
  LINUX_PKG_RPM = rpm?.name || rpmName;
  LINUX_BASE = base;
  renderLinuxCmd();

  document.getElementById("cmdWin").textContent =
    `msiexec /i ${win?.name || msiName} /qn CO37SERVER="${base}"`;
}

/* ---------- Installations-Token ---------- */
// Der Befehl steht nur im Browser, nie in der Datenbank: dort liegt vom
// Token ausschliesslich der Hash.
let LINUX_PKG = "", LINUX_PKG_RPM = "", LINUX_BASE = "",
    INSTALL_TOKEN = null, TOKEN_TIMER = null;

// Ein Paket, drei Aufrufe. Die Unterschiede sind nicht kosmetisch:
// apt-get gibt es auf Red Hat und SUSE nicht, und der Dateiname des
// Pakets ist ein anderer.
//
// Unser RPM ist nicht signiert. Die beiden Paketmanager gehen damit
// verschieden um - am 2026-09-09 auf zwei Anlagen gemessen:
//
//   dnf (Oracle Linux 10) nimmt die lokale Datei ohne Weiteres. Ein
//   --nogpgcheck stand hier zuerst drin und ist wieder raus: der
//   Schalter gilt fuer die GANZE Transaktion, also auch fuer
//   python3-requests und die uebrigen Abhaengigkeiten aus den
//   Distributionsquellen. Genau deren Signaturen prueft dnf sonst - und
//   importiert dafuer bei Bedarf den Schluessel der Distribution.
//
//   zypper (openSUSE Leap 16.0) bricht ab: "Paket-Kopfdaten sind nicht
//   signiert!". --allow-unsigned-rpm erlaubt gezielt diese eine lokale
//   Datei; die Quellen prueft zypper weiter (das waere --no-gpg-checks,
//   und das steht hier bewusst NICHT).
//
// Ein signiertes RPM waere die bessere Loesung und setzt einen
// GPG-Schluessel voraus, der auf jedem Zielsystem bekannt sein muss -
// eine eigene Entscheidung, keine Zugabe.
const LINUX_INSTALL = {
  deb:    { datei: "/tmp/co37-agent.deb", rpm: false,
            befehl: "apt-get install -y --allow-downgrades" },
  dnf:    { datei: "/tmp/co37-agent.rpm", rpm: true,
            befehl: "dnf install -y" },
  zypper: { datei: "/tmp/co37-agent.rpm", rpm: true,
            befehl: "zypper --non-interactive install --allow-unsigned-rpm" },
};
// Aus den Einstellungen; leer bedeutet "Adresse des Aufrufs verwenden".
let PUBLIC_URL = "";

function renderLinuxCmd(){
  const box = document.getElementById("cmdLinux");
  const copyBtn = document.getElementById("copyLinux");
  if (!INSTALL_TOKEN){
    box.textContent = t("settings.agents.no_token");
    copyBtn.disabled = true;
    document.getElementById("tokenState").textContent = "";
    return;
  }
  // Mit && verkettet: schlaegt der Download fehl, laeuft apt gar nicht erst
  // an. Sonst kommt eine irrefuehrende Meldung ueber eine nicht
  // unterstuetzte Datei, obwohl in Wahrheit der Download scheiterte.
  const art = LINUX_INSTALL[document.getElementById("linDistro").value]
              || LINUX_INSTALL.deb;
  const paket = art.rpm ? LINUX_PKG_RPM : LINUX_PKG;
  box.textContent =
    `curl -fsSL -H "X-Install-Token: ${INSTALL_TOKEN.token}" ${LINUX_BASE}/api/v1/packages/${paket} -o ${art.datei} \\\n`
  + `  && CO37_SERVER="${LINUX_BASE}" ${art.befehl} ${art.datei}`;
  copyBtn.disabled = false;
}

function tickToken(){
  const st = document.getElementById("tokenState");
  if (!INSTALL_TOKEN){ st.textContent = ""; return; }
  const left = Math.round((new Date(INSTALL_TOKEN.expires_at) - Date.now()) / 1000);
  if (left <= 0){
    INSTALL_TOKEN = null;
    clearInterval(TOKEN_TIMER); TOKEN_TIMER = null;
    renderLinuxCmd();
    st.textContent = t("settings.agents.token_expired");
    return;
  }
  const m = Math.floor(left / 60), sec = String(left % 60).padStart(2, "0");
  st.textContent = t("settings.agents.token_valid", { min: m, sec, max: INSTALL_TOKEN.max_uses });
}

async function makeInstallToken(){
  try {
    INSTALL_TOKEN = await api("POST", "/api/v1/install-token", {});
  } catch(e){
    toast(t("msg.token_failed"), true);
    return;
  }
  renderLinuxCmd();
  clearInterval(TOKEN_TIMER);
  TOKEN_TIMER = setInterval(tickToken, 1000);
  tickToken();
}

/* ---------- Pakete bauen ---------- */
let BUILD_TIMER = null;
const BUILD_TEXT = {
  idle:["",""], requested:["build.requested","run"], running:["build.running","run"],
  success:["build.success","ok"], error:["build.error","err"]
};

async function loadBuildStatus(){
  let st;
  try { st = await api("GET", "/api/v1/packages/build-status"); } catch(e){ return; }
  const [schluessel, cls] = BUILD_TEXT[st.state] || [st.state, ""];
  const el = document.getElementById("pkgBuildState");
  el.textContent = t(schluessel); el.className = "state " + cls;

  const box = document.getElementById("pkgBuildLog");
  const busy = st.state === "requested" || st.state === "running";
  const done = st.state === "success" || st.state === "error";
  box.style.display = (busy || done) ? "block" : "none";
  if (busy || done) box.textContent = protokoll(st.log);

  document.getElementById("pkgBuild").disabled = busy;

  if (busy && !BUILD_TIMER){
    BUILD_TIMER = setInterval(loadBuildStatus, 2000);
  }
  if (!busy && BUILD_TIMER){
    clearInterval(BUILD_TIMER); BUILD_TIMER = null;
    if (done) loadAgentsTab();     // Paketliste neu einlesen
  }
}

document.getElementById("pkgBuild").onclick = async () => {
  try { await api("POST", "/api/v1/packages/build"); }
  catch(e){ return; }
  toast(t("msg.build_requested"));
  loadBuildStatus();
};

/* ---------- Agent-Updates ---------- */
const RO_STATE = {
  idle:["settings.rollout.state_idle",""], pilot:["settings.rollout.state_pilot","run"], rest:["settings.rollout.state_rest","run"],
  done:["settings.rollout.state_done","ok"], failed:["settings.rollout.state_failed","err"]
};

async function loadRollout(){
  let r;
  try { r = await api("GET", "/api/v1/agent-rollout"); } catch(e){ return; }

  roAuto.checked = !!r.autoroll;
  roMode.value = r.mode || "staged";

  const sel = document.getElementById("roPilot");
  sel.innerHTML = `<option value="">${t("settings.rollout.none")}</option>`
    + (r.candidates || []).map(h =>
        `<option value="${h.id}" ${h.id === r.pilot_host_id ? "selected" : ""}>`
        + `${esc(h.hostname)}${h.agent_version ? " · " + esc(h.agent_version) : ""}</option>`).join("");

  document.getElementById("roPilotRow").style.display =
    roMode.value === "staged" ? "flex" : "none";

  document.getElementById("roHint").innerHTML = roMode.value === "staged"
    ? t("settings.rollout.hint_staged")
    : t("settings.rollout.hint_all");

  const [schluessel, cls] = RO_STATE[r.state] || [r.state, ""];
  const st = document.getElementById("roState");
  const txt = t(schluessel);
  st.textContent = r.stale_count ? txt + t("settings.rollout.stale_suffix", { anzahl: r.stale_count }) : txt;
  st.className = "state " + cls;
  document.getElementById("roNote").textContent = r.note || "";
}

document.getElementById("roMode").onchange = () => {
  document.getElementById("roPilotRow").style.display =
    roMode.value === "staged" ? "flex" : "none";
  document.getElementById("roHint").innerHTML = roMode.value === "staged"
    ? t("settings.rollout.hint_staged_short")
    : t("settings.rollout.hint_all");
};

document.getElementById("roSave").onclick = async () => {
  if (roAuto.checked && roMode.value === "staged" && !roPilot.value){
    if (!confirm(t("ask.no_pilot"))) return;
  }
  await api("POST", "/api/v1/agent-rollout", {
    autoroll: roAuto.checked,
    mode: roMode.value,
    pilot_host_id: roPilot.value ? parseInt(roPilot.value) : 0
  });
  await loadRollout();
  toast(t("msg.saved"));
};

/**
 * Startet das Ausrollen ueber die dafuer vorgesehene Route.
 *
 * Steht als Funktion da, weil ZWEI Knoepfe darauf zeigen: der in den
 * Einstellungen und der auf der Agents-Seite. Der zweite legte bis 0.37.15
 * die selfupdate-Auftraege selbst an, in einer Schleife ueber
 * /api/v1/hosts/{id}/jobs - und ging damit an allem vorbei, was
 * start_agent_rollout() leistet:
 *
 *   - der Staffelung (erst der Pilot-Host, die anderen erst, wenn er sich
 *     mit der neuen Fassung UND einem abgeschlossenen Auftrag meldet),
 *   - der Doppel-Auftrag-Sperre in _queue_selfupdate(),
 *   - dem Zustand, den die Einstellungsseite anzeigt.
 *
 * Wer den Knopf drueckte, schickte also die ganze Flotte auf einmal los -
 * genau das, wogegen die Staffelung gebaut wurde. Keine Rechteausweitung,
 * beide Wege sind Administratorsache; aber wenn ein Agent nach der
 * Aktualisierung nicht mehr hochkommt, kommt auf diesem Weg keiner mehr
 * hoch (Befund der Pruefung vom 2026-09-03).
 *
 * Eine Funktion statt zweier gleichlautender Stellen, damit sie nicht ein
 * zweites Mal auseinanderlaufen.
 */
async function rolloutStarten(){
  const r = await api("POST", "/api/v1/agent-rollout/start");
  await loadRollout();
  await load();
  toast(r.started
    ? (r.mode === "staged" ? t("settings.rollout.pilot_updating", { pilot: r.pilot }) : t("settings.rollout.agents_updating", { anzahl: r.count }))
    : r.reason);
  return r;
}

document.getElementById("roStart").onclick = async () => { await rolloutStarten(); };

document.getElementById("btnMakeToken").onclick = makeInstallToken;
// Die Wahl der Distribution aendert nur den angezeigten Befehl. Das
// Token bleibt gueltig - es haengt am Server, nicht am Paketformat.
document.getElementById("linDistro").onchange = renderLinuxCmd;
document.getElementById("copyLinux").onclick = () =>
  copy(document.getElementById("cmdLinux").textContent, t("settings.agents.linux_cmd_label"));
document.getElementById("copyWin").onclick = () =>
  copy(document.getElementById("cmdWin").textContent, t("settings.agents.win_cmd_label"));

document.getElementById("agUpdateAll").onclick = async () => {
  const stale = HOSTS.filter(h => h.approval_state === "approved"
    && h.agent_version && AGENT_VER && h.agent_version !== AGENT_VER);
  if (!stale.length){ toast(t("msg.agents_current")); return; }
  if (!confirm(t("ask.update_agents", { anzahl: stale.length, version: AGENT_VER }))) return;
  // Nicht mehr selbst Auftraege anlegen - siehe rolloutStarten().
  try { await rolloutStarten(); } catch(e){ return; }
  afterAction();
};

/* ---------- Checkmk laden ---------- */
let CMK_STATUS = null;

async function loadCmk(){
  // Ohne Administratorrechte gibt es hier nichts zu holen: die Route ist
  // require_admin, die Checkmk-Verknuepfung wird nur im Host-Dialog
  // gebraucht, und der steht ohnehin nur Administratoren offen.
  //
  // Der Grund fuer diese Zeile ist eine Meldung, die niemand ausgeloest
  // hat: startApp() rief loadCmk() bei JEDER Anmeldung auf, api() zeigt
  // die Begruendung eines abgewiesenen Aufrufs selbst an, und das catch
  // hier schluckt nur die Ausnahme, nicht den Toast. Wer sich als
  // Benutzer anmeldete, bekam also sofort "Nur fuer Administratoren"
  // eingeblendet, ohne etwas getan zu haben (2026-08-31 gemeldet).
  if (!(ME && ME.is_admin)){ CMK_STATUS = null; CMK_HOSTS = []; return; }
  let st;
  try { st = await api("GET", "/api/v1/checkmk/status"); } catch(e){ return; }
  CMK_STATUS = st;

  // Der Verbindungszustand steht nur noch in Einstellungen -> Checkmk.
  // Im Kopf der Startseite hat er nichts verloren.
  if (st.key_missing || !st.configured || st.ok === false){ CMK_HOSTS = []; return; }

  try { CMK_HOSTS = await api("GET", "/api/v1/checkmk/hosts"); } catch(e){ CMK_HOSTS = []; }
}

/**
 * Belegt das Checkmk-Formular mit den gespeicherten Werten. Das Secret wird
 * bewusst nicht ausgeliefert - das Feld bleibt leer, und ein leeres Feld
 * bedeutet beim Speichern "hinterlegtes Secret beibehalten".
 */
async function loadCmkForm(){
  if (!CMK_STATUS){ try { await loadCmk(); } catch(e){ return; } }
  const st = CMK_STATUS || {};
  const sd = st.stored || {};
  cUrl.value = sd.url || "";
  cSite.value = sd.site || "";
  cUser.value = sd.user || "";
  cVerify.checked = sd.verify_ssl !== false;
  cSecret.value = "";
  cSecret.placeholder = sd.secret_set
    ? t("settings.cmk.secret_placeholder")
    : "";

  const box = document.getElementById("cmkInfo");
  if (st.key_missing){
    box.innerHTML = `<span style="color:var(--led-reboot)">${t("settings.cmk.key_missing")}</span>`;
  } else if (st.ok === false){
    box.innerHTML = `<span style="color:var(--led-reboot)">${t("settings.cmk.conn_failed", { fehler: esc(st.error || "") })}</span>`;
  } else if (st.configured){
    const v = (st.versions || {}).checkmk || "";
    box.innerHTML = `<span style="color:var(--led-ok)">${t("settings.cmk.connected")}</span>` + t("settings.cmk.version_suffix", { version: esc(v) })
      + (st.edition ? ` ${esc(st.edition)}` : "")
      + t("settings.cmk.site_suffix", { site: esc(st.site || sd.site || "") })
      + t("settings.cmk.hosts_suffix", { anzahl: CMK_HOSTS.length });
  } else {
    box.innerHTML = `<span style="color:var(--muted-2)">${t("settings.cmk.not_connected")}</span>`;
  }
}

/* ---------- Systemupdate ---------- */
let UPD_TIMER = null;
const STATE_TEXT = {
  idle:["upd.idle",""], uploaded:["upd.uploaded","warn"], triggered:["upd.triggered","run"],
  running:["upd.running","run"], success:["upd.success","ok"],
  rolled_back:["upd.rolled_back","warn"], error:["upd.error","err"]
};
async function loadWatcher(){
  let w;
  try { w = await api("GET", "/api/v1/watcher"); } catch(e){ return; }
  const el = document.getElementById("wVer");
  const hint = document.getElementById("wHint");
  el.textContent = w.version || t("settings.watcher.silent");
  el.style.color = w.ok && w.current ? "var(--led-ok)"
                 : w.ok ? "var(--led-pending)" : "var(--led-reboot)";
  if (w.hint){
    hint.style.display = "block";
    hint.innerHTML = `<span style="color:${w.ok ? "var(--led-pending)" : "var(--led-reboot)"}">${esc(w.hint)}</span>`
      + (!w.ok ? t("settings.watcher.manual_hint") : "");
  } else {
    hint.style.display = "none";
  }
}

/* ---------- Lizenz ---------- */
async function loadLizenz(){
  let d;
  try { d = await api("GET", "/api/v1/license"); }
  catch(e){ return; }

  const box = document.getElementById("lizStand");
  const grenze = d.erlaubte_hosts;           // null bedeutet unbegrenzt
  const belegt = d.belegt;

  // Der Anteil ist der Wert, den man beim Öffnen sehen will: passt es
  // noch, oder ist gleich Schluss?
  const eng = grenze !== null && belegt >= grenze;
  const knapp = grenze !== null && belegt >= grenze - 2;
  const farbe = eng ? "var(--led-reboot)"
              : knapp ? "var(--led-pending)" : "var(--led-ok)";

  let zeilen = [];
  if (d.vorhanden){
    zeilen.push(t("settings.license.licensed_for", { kunde: esc(d.kunde) })
                + (d.nummer ? t("settings.license.key_number", { nummer: d.nummer }) : ""));
    zeilen.push(d.unbefristet
      ? t("settings.license.unlimited")
      : t("settings.license.valid_until", { datum: fmtTime(d.gueltig_bis, {year:"numeric",month:"2-digit",day:"2-digit"}) })
        + (d.abgelaufen ? t("settings.license.expired_suffix")
                        : t("settings.license.days_left", { tage: d.tage_uebrig })));
  } else {
    zeilen.push(t("settings.license.none"));
  }

  zeilen.push(`<b style="color:${farbe}">`
              + t("settings.license.hosts_approved",
                  { belegt, grenze: grenze === null ? t("settings.license.unlimited_count") : grenze })
              + `</b>`);

  if (d.hinweis)
    zeilen.push(`<b style="color:var(--led-pending)">${esc(d.hinweis)}</b>`);
  if (eng)
    zeilen.push(t("settings.license.limit_hint"));

  box.innerHTML = zeilen.join("<br>");
}

document.getElementById("lizSave").onclick = async () => {
  const k = document.getElementById("lizKey").value.trim();
  if (!k){ toast(t("msg.no_key"), true); return; }
  try {
    await api("POST", "/api/v1/license", { key: k });
  } catch(e){
    // Der bisherige Zustand bleibt bestehen - das Backend weist einen
    // ungültigen Schlüssel ab, ohne ihn zu speichern.
    toast(t("msg.key_rejected"), true);
    return;
  }
  document.getElementById("lizKey").value = "";
  await loadLizenz();
  await load();
  toast(t("msg.key_applied"));
};

document.getElementById("lizClear").onclick = async () => {
  if (!confirm(t("ask.remove_license")))
    return;
  await api("POST", "/api/v1/license", { key: "" });
  await loadLizenz();
  toast(t("msg.key_removed"));
};

/* ---------- Zugang: Proxy und HTTPS-Zwang ---------- */
let PROXY_TIMER = null;

// ======================================================================
// Weiterleitung an eine zentrale Protokollierung
// ======================================================================
async function loadSyslog(){
  let d;
  try { d = await api("GET", "/api/v1/syslog-settings"); }
  catch(e){ return; }

  document.getElementById("slHost").value = d.host || "";
  document.getElementById("slPort").value = d.port || 514;
  document.getElementById("slTransport").value = d.transport || "udp";
  document.getElementById("slFacility").value = d.facility || "local0";
  document.getElementById("slVerify").checked = d.verify_tls !== false;
  document.getElementById("slOn").checked = !!d.enabled;
  syslogTlsFelder();

  // Verworfene Meldungen sind ein Betriebszustand, kein Fehler - aber
  // einer, den man sehen muss. Eine stille Luecke im Protokoll waere das
  // Schlechteste von beidem.
  document.getElementById("slStats").innerHTML = d.dropped
    ? t("settings.syslog.dropped", { n: d.dropped, q: d.queue || 0 })
    : t("settings.syslog.queue_ok", { q: d.queue || 0 });
}

// Die Zertifikatspruefung gilt nur fuer TLS. Sie bei UDP anzubieten
// waere eine Frage, auf die es keine Antwort gibt.
function syslogTlsFelder(){
  const tls = document.getElementById("slTransport").value === "tls";
  document.getElementById("slVerifyRow").style.display = tls ? "" : "none";
  document.getElementById("slVerifyHint").style.display = tls ? "" : "none";
}
document.getElementById("slTransport").onchange = syslogTlsFelder;

document.getElementById("slSave").onclick = async () => {
  await api("POST", "/api/v1/syslog-settings", {
    host: document.getElementById("slHost").value.trim(),
    port: parseInt(document.getElementById("slPort").value, 10) || 514,
    transport: document.getElementById("slTransport").value,
    facility: document.getElementById("slFacility").value,
    verify_tls: document.getElementById("slVerify").checked,
  });
  toast(t("settings.syslog.saved"));
  loadSyslog();
};

document.getElementById("slTest").onclick = async () => {
  const ziel = document.getElementById("slTestResult");
  // Erst speichern, sonst testet man gegen den alten Stand und wundert
  // sich, warum die gerade eingetippte Adresse nicht gilt.
  document.getElementById("slSave").click();
  ziel.textContent = t("settings.syslog.testing");
  try {
    const r = await api("POST", "/api/v1/syslog-settings/test", {});
    ziel.innerHTML = t("settings.syslog.test_ok", { ziel: esc(r.sent || "") });
  } catch(e){
    ziel.innerHTML = t("settings.syslog.test_failed", { grund: esc(String(e.message || e)) });
  }
};

document.getElementById("slOn").onchange = async (ev) => {
  const an = ev.target.checked;
  try {
    await api("POST", "/api/v1/syslog-settings", { enabled: an });
    toast(an ? t("settings.syslog.on") : t("settings.syslog.off"));
  } catch(e){
    // Zuruecksetzen, sonst zeigt der Haken einen Zustand, den der Server
    // nicht hat - etwa weil keine Zieladresse eingetragen ist.
    ev.target.checked = !an;
    toast(String(e.message || e));
  }
  loadSyslog();
};


async function loadProxy(){
  let d;
  try { d = await api("GET", "/api/v1/proxy-settings"); }
  catch(e){ return; }

  document.getElementById("pxProxy").value = d.trusted_proxy || "";
  document.getElementById("pxPublic").value = d.public_url || "";
  document.getElementById("pxHttps").checked = !!d.https_only;
  PUBLIC_URL = d.public_url || "";

  document.getElementById("pxEffective").innerHTML =
    t("settings.proxy.effective", { url: esc(d.effective_url || "?") })
    + (d.public_url ? "" : t("settings.proxy.effective_derived"));

  // Womit die eigene Anfrage hereinkam. Ohne das raet man beim Eintragen
  // der Proxy-Adresse, statt es zu sehen.
  document.getElementById("pxNow").innerHTML =
    t("settings.proxy.session_from", { ip: esc(d.this_request_from || "?") })
    + (d.this_request_https
        ? t("settings.proxy.encrypted")
        : t("settings.proxy.unencrypted_hint"));

  // Bereitschaft der Agents - der Grund, warum es diese Anzeige gibt.
  const n = d.agents_secure, total = d.agents_total;
  const ready = total > 0 && n === total;
  document.getElementById("pxReady").innerHTML =
    t("settings.proxy.agents_ready", { farbe: ready ? "var(--led-ok)" : "var(--led-pending)", n, total })
    + (d.agents_insecure.length
        ? t("settings.proxy.agents_insecure_list", { liste: esc(d.agents_insecure.join(", ")) })
        : "");

  renderProxyState(d);
}

function renderProxyState(d){
  const box = document.getElementById("pxState");
  clearInterval(PROXY_TIMER); PROXY_TIMER = null;

  if (!d.https_only){ box.textContent = ""; return; }
  if (!d.confirm_deadline){
    box.innerHTML = t("settings.proxy.confirmed");
    return;
  }

  // Noch nicht bestätigt: bis zum Ablauf der Frist muss sich jemand über
  // HTTPS anmelden, sonst stellt sich der Zwang von selbst zurück.
  const tick = () => {
    const left = Math.round((new Date(d.confirm_deadline) - Date.now()) / 1000);
    if (left <= 0){
      clearInterval(PROXY_TIMER); PROXY_TIMER = null;
      box.textContent = t("settings.proxy.deadline_expired");
      loadProxy();
      return;
    }
    const m = Math.floor(left / 60), sec = String(left % 60).padStart(2, "0");
    box.innerHTML = t("settings.proxy.not_confirmed", { m, sec });
  };
  tick();
  PROXY_TIMER = setInterval(tick, 1000);
}

document.getElementById("pxSavePublic").onclick = async () => {
  const v = document.getElementById("pxPublic").value.trim();
  if (v && !/^https?:\/\//.test(v)){
    toast(t("msg.url_scheme"), true); return;
  }
  await api("POST", "/api/v1/proxy-settings", { public_url: v });
  await loadProxy();
  toast(t("msg.saved"));
};

document.getElementById("pxSaveProxy").onclick = async () => {
  await api("POST", "/api/v1/proxy-settings",
            { trusted_proxy: document.getElementById("pxProxy").value.trim() });
  await loadProxy();
  toast(t("msg.saved"));
};

document.getElementById("pxHttps").onchange = async (e) => {
  const on = e.target.checked;
  if (on){
    const d = await api("GET", "/api/v1/proxy-settings");
    const offen = d.agents_insecure.length;
    const warn = offen
      ? t("settings.proxy.confirm_warn_agents", { offen, liste: d.agents_insecure.join(", ") })
      : "";
    const self = d.this_request_https ? "" : t("settings.proxy.confirm_warn_self");
    if (!confirm(t("settings.proxy.confirm_restrict_intro") + self + warn
      + t("settings.proxy.confirm_restrict_outro", { minuten: d.confirm_minutes }))){
      e.target.checked = false; return;
    }
  }
  await api("POST", "/api/v1/proxy-settings", { https_only: on });
  await loadProxy();
  toast(on ? t("settings.proxy.restricted") : t("settings.proxy.unrestricted"));
};

async function loadUpdate(){
  loadWatcher();
  let st;
  try { st = await api("GET", "/api/v1/update/status"); } catch(e){ return; }
  document.getElementById("uCur").textContent = st.current_version || "—";
  const [schluessel, cls] = STATE_TEXT[st.state] || [st.state, ""];
  const text = t(schluessel);
  const el = document.getElementById("uState");
  el.textContent = text; el.className = "state " + cls;

  const pending = st.state === "uploaded";
  document.getElementById("uPending").style.display = pending ? "block" : "none";
  if (pending) document.getElementById("uNew").textContent = st.new_version || "—";

  const busy = st.state === "triggered" || st.state === "running";
  const done = ["success","rolled_back","error"].includes(st.state);
  // Gemerkt fuer den Fall, dass gleich niemand mehr antwortet: waehrend
  // eines Updates darf der Ausfall laenger dauern, ohne dass die
  // Oberflaeche Alarm schlaegt. Bleibt stehen, wenn die naechste Abfrage
  // scheitert - genau dann wird es gebraucht.
  UPDATE_LAEUFT = busy;
  document.getElementById("uDrop").style.display = (pending||busy) ? "none" : "block";
  document.getElementById("uLogBox").style.display = (busy||done) ? "block" : "none";
  if (busy || done){
    document.getElementById("upLog").textContent = protokoll(st.log) || "…";
    document.getElementById("uAck").style.display = done ? "inline-block" : "none";
  }
  if (busy && !UPD_TIMER) UPD_TIMER = setInterval(loadUpdate, 3000);
  if (!busy && UPD_TIMER){ clearInterval(UPD_TIMER); UPD_TIMER = null; }

  await loadRemoteUpdate(pending || busy);
}

/*
 * Der GitHub-Bezug: Haken, letzter Suchlauf, gefundene Version. Kein
 * Netzaufruf hier - nur der gespeicherte Stand aus der Oberflaeche. Der
 * "Holen"-Knopf erscheint nur, wenn etwas Neueres gefunden wurde und
 * gerade kein Paket schon wartet oder laeuft.
 */
async function loadRemoteUpdate(schonBeschaeftigt){
  let cfg;
  try { cfg = await api("GET", "/api/v1/update/remote-config"); }
  catch(e){ return; }
  document.getElementById("uAuto").checked = !!cfg.auto;
  const zeile = document.getElementById("uRemoteState");
  if (cfg.last_check){
    const wann = new Date(cfg.last_check).toLocaleString();
    zeile.textContent = cfg.available
      ? t("settings.update.remote_found", { version: cfg.available })
      : t("settings.update.remote_current", { wann });
  } else {
    zeile.textContent = t("settings.update.remote_never");
  }
  const zeigeHolen = !!cfg.available && !schonBeschaeftigt;
  document.getElementById("uFetchRow").style.display = zeigeHolen ? "block" : "none";
  if (zeigeHolen) document.getElementById("uAvail").textContent = cfg.available;
}
/*
 * Nimmt eine Auswahl entgegen und sortiert sie selbst: das Paket und die
 * zugehoerige .sig-Datei. Beide gemeinsam auswaehlen oder gemeinsam
 * hineinziehen - so gibt es keine Reihenfolge, die man falsch machen
 * kann, und kein zweites Feld, das man uebersieht.
 */
async function uploadZip(dateien){
  const liste = dateien instanceof FileList || Array.isArray(dateien)
              ? [...dateien] : (dateien ? [dateien] : []);
  if (!liste.length) return;

  const zip = liste.find(f => f.name.toLowerCase().endsWith(".zip"));
  const sig = liste.find(f => f.name.toLowerCase().endsWith(".sig"));

  if (!zip){ toast(t("msg.need_zip"), true); return; }

  const fd = new FormData();
  fd.append("file", zip);
  if (sig) fd.append("sigfile", sig);
  toast(sig ? t("msg.checking_pkg_sig") : t("msg.checking_pkg"));
  const res = await fetch(API + "/api/v1/update/upload",
    {method:"POST", credentials: "same-origin", body:fd});
  if (!res.ok){
    // Die Antwort ist JSON. Ohne Auspacken stand die Begruendung als
    // {"detail":"..."} in der Meldung - lesbar, aber unschoen, und der
    // Anfang wurde von der Klammer verbraucht.
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail || msg; } catch(e){}
    toast(String(msg).slice(0, 300), true);
    return;
  }
  await loadUpdate(); toast(t("msg.package_staged"));
}
const drop = document.getElementById("uDrop"), fileInput = document.getElementById("uFile");
drop.onclick = () => fileInput.click();
fileInput.onchange = e => uploadZip(e.target.files);
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => uploadZip(e.dataTransfer.files));

document.getElementById("uRun").onclick = async () => {
  if (!confirm(t("ask.run_update", { von: uCur.textContent, nach: uNew.textContent }))) return;
  await api("POST", "/api/v1/update/trigger"); await loadUpdate();
};
document.getElementById("uCancel").onclick = async () => {
  await api("POST", "/api/v1/update/cancel"); await loadUpdate(); toast(t("msg.package_discarded"));
};
document.getElementById("uAck").onclick = async () => {
  await api("POST", "/api/v1/update/acknowledge"); await loadUpdate();
};

document.getElementById("uCheck").onclick = async () => {
  const btn = document.getElementById("uCheck");
  btn.disabled = true;
  try {
    // force=true: der Knopf sucht immer, die 24-Stunden-Sperre gilt nur
    // fuer den automatischen Lauf. Fehler meldet api() selbst mit dem
    // Grund vom Server (z. B. "GitHub nicht erreichbar").
    const r = await api("POST", "/api/v1/update/check?force=true");
    toast(r.available
      ? t("msg.update_found", { version: r.available })
      : t("msg.update_none"));
  } catch(e){ /* api() hat den Grund bereits gemeldet */ }
  finally { btn.disabled = false; }
  await loadUpdate();
  await checkVersion();
};

document.getElementById("uAuto").onchange = async (e) => {
  try { await api("PUT", "/api/v1/update/remote-config", { auto: e.target.checked }); }
  catch(err){ e.target.checked = !e.target.checked; }
};

document.getElementById("uFetch").onclick = async () => {
  const btn = document.getElementById("uFetch");
  btn.disabled = true;
  toast(t("msg.update_fetching"));
  try {
    await api("POST", "/api/v1/update/fetch");
    await loadUpdate();
    toast(t("msg.package_staged"));
  } catch(e){ /* api() hat den Grund bereits gemeldet */ }
  finally { btn.disabled = false; }
};

/* ---------- Start ---------- */
document.getElementById("btnReload").onclick = () => load().then(() => toast(t("msg.refreshed")));
document.getElementById("btnScanAll").onclick = scanAll;
document.getElementById("filter").oninput = render;
document.getElementById("fState").onchange = render;

/* ---------- Konto, Benutzer, Protokoll ---------- */

function loadAccount(){
  document.getElementById("acName").textContent = ME ? ME.username : "—";
  document.getElementById("acRole").textContent =
    ME ? (ME.is_admin ? t("settings.users.role_admin") : t("settings.users.role_user")) : "—";
  const an = !!(ME && ME.mfa_enabled);
  document.getElementById("mfaAn").style.display = an ? "" : "none";
  document.getElementById("mfaAus").style.display = an ? "none" : "";
}


/* ---------- Anmeldung in zwei Schritten ---------- */
/*
 * Ein Dialog, zwei Wege hinein: der Knopf im Reiter Konto und der Zwang
 * aus startApp(), wenn die Anlage sie fuer Administratoren verlangt. Der
 * Unterschied ist genau einer - ob abgebrochen werden darf.
 */
let MFA_ZWANG = false;

async function mfaEinrichtenOeffnen(zwang){
  MFA_ZWANG = !!zwang;
  let d;
  try { d = await api("POST", "/api/v1/me/totp/start"); } catch(e){ return; }

  // Das Bild kommt fertig aus dem Backend. innerHTML ist hier vertretbar
  // und sonst nirgends: der Inhalt ist ein SVG, das der Server selbst
  // erzeugt hat, kein Text aus einem Eingabefeld.
  document.getElementById("mfaQr").innerHTML = d.qr_svg || "";
  // In Vierergruppen, sonst vertippt sich beim Abtippen jeder.
  document.getElementById("mfaSecret").textContent =
    (d.secret || "").replace(/(.{4})/g, "$1 ").trim();
  document.getElementById("mfaCode").value = "";
  document.getElementById("mfaError").textContent = "";
  document.getElementById("mfaEinrichtung").style.display = "";
  document.getElementById("mfaRettung").style.display = "none";
  document.getElementById("mfaConfirm").style.display = "";
  document.getElementById("mfaDone").style.display = "none";
  document.getElementById("mfaCancel").style.display = zwang ? "none" : "";
  document.getElementById("mfaZwangHint").style.display = zwang ? "" : "none";

  const dlg = document.getElementById("dlgMfaSetup");
  if (!dlg.open) dlg.showModal();
  document.getElementById("mfaCode").focus();
}

document.getElementById("mfaGo").onclick = () => mfaEinrichtenOeffnen(false);

document.getElementById("mfaCancel").onclick = () => {
  // Abgebrochen heisst abgebrochen: das angefangene Geheimnis steht zwar
  // noch am Konto, scharf ist es aber nicht - totp_confirmed_at bleibt
  // leer, und ein neuer Anlauf erzeugt ein neues. Anmelden laesst sich
  // weiterhin mit dem Passwort allein.
  document.getElementById("dlgMfaSetup").close();
};

document.getElementById("mfaConfirm").onclick = async () => {
  const code = document.getElementById("mfaCode").value.trim();
  const feld = document.getElementById("mfaError");
  if (!code){ feld.textContent = t("settings.mfa.need_code"); return; }
  let d;
  try {
    d = await api("POST", "/api/v1/me/totp/confirm", { code });
  } catch(e){
    feld.textContent = String(e.message || e);
    return;
  }
  // Ab hier gibt es die Codes genau einmal zu sehen. Deshalb wechselt der
  // Dialog in einen zweiten Schritt statt sich zu schliessen.
  document.getElementById("mfaCodes").textContent =
    (d.recovery_codes || []).join("\n");
  document.getElementById("mfaEinrichtung").style.display = "none";
  document.getElementById("mfaRettung").style.display = "";
  document.getElementById("mfaConfirm").style.display = "none";
  document.getElementById("mfaCancel").style.display = "none";
  document.getElementById("mfaDone").style.display = "";
};

document.getElementById("mfaCopy").onclick = async () => {
  const text = document.getElementById("mfaCodes").textContent;
  try {
    await navigator.clipboard.writeText(text);
    toast(t("settings.mfa.copied"));
  } catch(e){
    // Ohne sicheren Kontext gibt es die Zwischenablage nicht. Dann bleibt
    // das Markieren von Hand - die Codes stehen ja da.
    toast(t("settings.mfa.copy_failed"), true);
  }
};

document.getElementById("mfaDone").onclick = async () => {
  document.getElementById("dlgMfaSetup").close();
  const zwang = MFA_ZWANG;
  MFA_ZWANG = false;
  try { ME = await api("GET", "/api/v1/me"); } catch(e){}
  loadAccount();
  // Kam der Dialog aus dem Zwang, war die Oberflaeche bis eben gesperrt.
  // Jetzt darf sie starten.
  if (zwang){
    document.getElementById("appShell").style.display = "";
    await startApp();
  }
};

document.getElementById("mfaOff").onclick = async () => {
  const password = document.getElementById("mfaOffPw").value;
  const code = document.getElementById("mfaOffCode").value.trim();
  if (!password || !code){ toast(t("settings.mfa.need_both"), true); return; }
  try { await api("POST", "/api/v1/me/totp/off", { password, code }); }
  catch(e){ return; }
  document.getElementById("mfaOffPw").value = "";
  document.getElementById("mfaOffCode").value = "";
  try { ME = await api("GET", "/api/v1/me"); } catch(e){}
  loadAccount();
  toast(t("settings.mfa.turned_off"));
};


async function loadMfaPolicy(){
  let d;
  try { d = await api("GET", "/api/v1/mfa-policy"); } catch(e){ return; }
  document.getElementById("mfaPolicy").checked = !!d.required_for_admins;
  document.getElementById("mfaPolicyState").textContent =
    d.required_for_admins ? t("settings.mfa.policy_on")
                          : t("settings.mfa.policy_off");
}

document.getElementById("mfaPolicy").onchange = async (ev) => {
  const an = ev.target.checked;
  try {
    await api("POST", "/api/v1/mfa-policy", { required_for_admins: an });
  } catch(e){
    // Das Backend weist das Einschalten ab, solange das eigene Konto den
    // zweiten Faktor nicht hat. Der Haken muss dann zurueck, sonst zeigt
    // er einen Zustand, den der Server nicht hat.
    ev.target.checked = !an;
    toast(String(e.message || e), true);
    return;
  }
  loadMfaPolicy();
};

document.getElementById("acSave").onclick = async () => {
  const oldPw = document.getElementById("acOld").value;
  const nw = document.getElementById("acNew").value;
  const nw2 = document.getElementById("acNew2").value;
  if (nw !== nw2){ toast(t("msg.pw_mismatch"), true); return; }
  if (nw.length < PW_MIN){ toast(t("msg.pw_short", { anzahl: PW_MIN }), true); return; }
  try { await api("POST", "/api/v1/me/password", {old_password: oldPw, new_password: nw}); }
  catch(e){ return; }
  // Der Server verwirft dabei alle Sitzungen - auch die eigene.
  setSession(false);
  document.getElementById("dlgSettings").close();
  showLogin(t("msg.pw_changed"));
};

async function loadUsers(){
  let users;
  try { users = await api("GET", "/api/v1/users"); } catch(e){ return; }
  const me = ME ? ME.username : "";
  document.getElementById("userList").innerHTML = users.map(u => `
    <div class="vrow" style="margin-bottom:5px">
      <span>${esc(u.username)}${u.username === me ? t("settings.users.you_suffix") : ""}<br>
        <span style="color:var(--muted-2)">${u.role === "admin" ? t("settings.users.role_admin") : t("settings.users.role_user")}
        ${u.may_reboot ? "· " + t("settings.users.reboot_allowed") : ""}
        · ${u.last_login ? t("settings.users.last_login", { when: fmtTime(u.last_login) }) : t("settings.users.never_logged_in")}</span></span>
      <span style="display:flex;gap:8px">
        ${u.role === "admin" ? "" : `<button data-act="togglereboot" data-id="${u.id}"
          data-username="${esc(u.username)}" data-on="${u.may_reboot ? "1" : ""}"
          >${u.may_reboot ? t("settings.users.reboot_revoke") : t("settings.users.reboot_grant")}</button>`}
        <button data-act="resetpw" data-id="${u.id}" data-username="${esc(u.username)}">${t("settings.users.set_password")}</button>
        <button class="danger" data-act="deluser" data-id="${u.id}" data-username="${esc(u.username)}"
          ${u.username === me ? "disabled" : ""}>${t("settings.users.remove")}</button>
      </span></div>`).join("");
}

// Das Neustart-Recht ist eine Frage fuer die Rolle 'user'. Ein
// Administrator hat es ueber seine Rolle, immer - Principal.may_reboot im
// Backend liefert fuer ihn True, gleichgueltig was im Feld steht. Ein
// Haekchen stehen zu lassen, das nichts bewirkt, ist irrefuehrend.
function nuRechteAnzeigen(){
  const istAdmin = document.getElementById("nuRole").value === "admin";
  document.getElementById("nuRebootRow").style.display = istAdmin ? "none" : "";
  document.getElementById("nuRebootHint").style.display = istAdmin ? "none" : "";
  // Zuruecksetzen, damit ein vorher gesetztes Haekchen nicht unsichtbar
  // weiterlebt und beim Zurueckschalten auf 'user' ueberrascht.
  if (istAdmin) document.getElementById("nuReboot").checked = false;
}

document.getElementById("nuRole").addEventListener("change", nuRechteAnzeigen);
// Einmal beim Laden: Browser stellen den Zustand eines <select> nach
// einem Neuladen gerne wieder her, und dann stuende die Auswahl auf
// "Administrator", waehrend die Zeile noch sichtbar waere.
nuRechteAnzeigen();


async function toggleReboot(id, name, an){
  // Das Backend verwirft dabei die Sitzungen des Kontos - ein entzogenes
  // Recht, das erst bei der naechsten Anmeldung greift, waere keines.
  if (!confirm(t(an ? "ask.reboot_grant" : "ask.reboot_revoke", { name }))) return;
  try { await api("PATCH", `/api/v1/users/${id}/rechte`, { may_reboot: an }); }
  catch(e){ return; }
  toast(t(an ? "msg.reboot_granted" : "msg.reboot_revoked", { name }));
  loadUsers(); loadAudit();
}

async function resetPw(id, name){
  const pw = prompt(t("ask.new_password", { name }));
  if (!pw) return;
  if (pw.length < PW_MIN){ toast(t("msg.pw_short", { anzahl: PW_MIN }), true); return; }
  try { await api("POST", `/api/v1/users/${id}/password`, {new_password: pw}); }
  catch(e){ return; }
  toast(t("msg.pw_set", { name }));
  loadUsers(); loadAudit();
}

async function delUser(id, name){
  if (!confirm(t("ask.remove_user", { name }))) return;
  try { await api("DELETE", `/api/v1/users/${id}`); } catch(e){ return; }
  toast(t("msg.removed", { name }));
  loadUsers(); loadAudit();
}

document.getElementById("nuAdd").onclick = async () => {
  const username = document.getElementById("nuName").value.trim();
  const password = document.getElementById("nuPass").value;
  const role = document.getElementById("nuRole").value;
  const may_reboot = document.getElementById("nuReboot").checked;
  if (!username || !password){ toast(t("msg.need_name_pw"), true); return; }
  try { await api("POST", "/api/v1/users", {username, password, role, may_reboot}); }
  catch(e){ return; }
  document.getElementById("nuName").value = "";
  document.getElementById("nuPass").value = "";
  document.getElementById("nuReboot").checked = false;
  toast(t("msg.created", { name: username }));
  loadUsers(); loadAudit();
};

async function loadAudit(){
  let rows;
  try { rows = await api("GET", "/api/v1/audit?limit=200"); } catch(e){ return; }
  document.getElementById("auditList").innerHTML = rows.length
    ? rows.map(e => `<div class="vrow" style="margin-bottom:3px">
        <span>${esc(e.actor)} · <b>${esc(e.action)}</b>
          ${e.detail ? "<br><span style=\"color:var(--muted-2)\">" + esc(e.detail) + "</span>" : ""}</span>
        <span style="color:var(--muted-2);white-space:nowrap">${fmtTime(e.at)}${e.from_ip ? " · " + esc(e.from_ip) : ""}</span>
      </div>`).join("")
    : `<span style="color:var(--muted-2)">${t("settings.audit.none")}</span>`;
}

/* ---------- Bereiche (Einstellungen) ---------- */
/*
 * Eigene Bedienung statt der Hostliste zu erweitern - Anlegen, Reihenfolge
 * und die eigentliche Konfiguration eines Bereichs gehoeren hierher, nicht
 * in die Uebersicht, die nur noch zieht und gruppiert (Schritt 3).
 *
 * AREAS ist dasselbe globale Feld, das auch render() fuer die Gruppierung
 * in der Hostliste benutzt - ein Neuladen hier zieht die Hauptliste
 * automatisch mit nach, ohne eigene Abstimmung zwischen beiden Stellen.
 *
 * Reihenfolge per Auf/Ab statt Ziehen wie bei den Hosts: bei der
 * ueberschaubaren Zahl an Bereichen in einer Einstellungsliste waere ein
 * zweites Drag-Geruest neben dem der Hostliste unnoetiger Aufwand fuer
 * denselben Nutzen.
 */
async function loadAreasTab(){
  try { AREAS = await api("GET", "/api/v1/areas"); } catch(e){ return; }
  renderAreaList();
}

// Eine Zeile in der Bereichsliste. i/anzahl gelten INNERHALB der eigenen
// Gruppe (oberste untereinander, Unterbereiche je Elter) - so bewegen die
// Pfeile einen Bereich nie ueber seine Gruppengrenze hinaus.
function areaRow(a, i, anzahl, unterbereich){
  return `<div class="vrow" style="margin-bottom:5px${unterbereich ? ";margin-left:24px" : ""}">
    <span>${esc(a.name)}<br>
      <span style="color:var(--muted-2)">${t("area.host_count", { anzahl: a.host_count })}</span></span>
    <span style="display:flex;gap:6px">
      <button data-act="areaup" data-id="${a.id}" ${i===0?"disabled":""} title="${esc(t("area.move_up"))}">↑</button>
      <button data-act="areadown" data-id="${a.id}" ${i===anzahl-1?"disabled":""} title="${esc(t("area.move_down"))}">↓</button>
      <button class="danger" data-act="arearemove" data-id="${a.id}">${t("area.remove")}</button>
    </span></div>`;
}

function renderAreaList(){
  const box = document.getElementById("areaList");
  const tops = AREAS.filter(a => !a.parent_id);
  if (!tops.length){
    box.innerHTML = `<span style="color:var(--muted-2)">${t("area.none")}</span>`;
    return;
  }
  let html = "";
  tops.forEach((a, i) => {
    html += areaRow(a, i, tops.length, false);
    const kinder = AREAS.filter(k => k.parent_id === a.id);
    kinder.forEach((k, j) => { html += areaRow(k, j, kinder.length, true); });
    // Direkt unter jedem obersten Bereich: ein Feld zum Anlegen eines
    // Unterbereichs darin. Nur eine Ebene - Unterbereiche bekommen kein
    // solches Feld.
    html += `<div class="vrow" style="margin:0 0 10px 24px">
      <input data-sub-name="${a.id}" placeholder="${esc(t("area.sub_name_example"))}"
             style="flex:1;min-width:0">
      <button data-act="areasubadd" data-id="${a.id}">${t("area.add_sub")}</button>
    </div>`;
  });
  box.innerHTML = html;
}

async function addSubArea(parentId){
  const feld = document.querySelector(`input[data-sub-name="${parentId}"]`);
  const name = feld ? feld.value.trim() : "";
  if (!name){ toast(t("msg.need_area_name"), true); return; }
  try { await api("POST", "/api/v1/areas", { name, parent_id: parentId }); }
  catch(e){ return; }
  toast(t("msg.created", { name }));
  await loadAreasTab();
  render();
}

async function moveArea(id, richtung){
  const a = AREAS.find(x => x.id === id);
  if (!a) return;
  const pid = a.parent_id || null;
  // Nur innerhalb der eigenen Geschwister tauschen.
  const gruppe = AREAS.filter(x => (x.parent_id || null) === pid);
  const idx = gruppe.findIndex(x => x.id === id);
  const ziel = idx + richtung;
  if (ziel < 0 || ziel >= gruppe.length) return;
  [gruppe[idx], gruppe[ziel]] = [gruppe[ziel], gruppe[idx]];
  // AREAS gruppiert neu zusammensetzen (oberster Bereich, dann seine Kinder)
  // - nur die getauschte Gruppe aendert sich, die anderen bleiben, wie sie
  // waren. Die Reihenfolge-Route vergibt sort_order fortlaufend ueber diese
  // Liste; gezeichnet wird je Gruppe nach sort_order, das genuegt.
  const tops = pid === null ? gruppe : AREAS.filter(x => !x.parent_id);
  const neu = [];
  for (const top of tops){
    neu.push(top);
    const kinder = (pid !== null && pid === top.id)
      ? gruppe : AREAS.filter(x => x.parent_id === top.id);
    neu.push(...kinder);
  }
  AREAS = neu;
  renderAreaList();
  try {
    await api("POST", "/api/v1/areas/order", { ids: AREAS.map(a => a.id) });
  } catch(e){
    // Wie saveOrder() bei den Hosts: echten Stand zurueckholen statt einer
    // Anzeige, die es serverseitig so nicht gibt.
    loadAreasTab();
  }
  render();
}

document.getElementById("naAdd").onclick = async () => {
  const name = document.getElementById("naName").value.trim();
  if (!name){ toast(t("msg.need_area_name"), true); return; }
  try { await api("POST", "/api/v1/areas", { name }); } catch(e){ return; }
  document.getElementById("naName").value = "";
  toast(t("msg.created", { name }));
  await loadAreasTab();
  render();
};

/* ---------- Bereich bearbeiten ---------- */
let EDIT_AREA_ID = null;
let AREA_PICKED = new Set();
let AREA_PATCH_DAYS = new Set();

function editArea(id){
  const a = AREAS.find(x => x.id === id);
  if (!a) return;
  EDIT_AREA_ID = id;
  document.getElementById("aTitle").textContent = a.name;
  document.getElementById("aName").value = a.name;

  document.getElementById("aUpEnabled").checked = !!a.patch_enabled;
  AREA_PATCH_DAYS = new Set(a.patch_days || []);
  document.getElementById("aUpTime").value = a.patch_time || "03:00";
  document.getElementById("aUpGrace").value = a.patch_grace_hours || 4;
  document.getElementById("aUpReboot").checked = !!a.patch_auto_reboot;
  renderAreaDays();

  AREA_PICKED = new Set(a.checkmk_hosts || []);
  document.getElementById("aCmkSearch").value = "";
  renderAreaPicker();
  document.getElementById("aDtAll").checked = !!a.checkmk_downtime_all;
  document.getElementById("aDowntime").value = a.downtime_minutes || 30;

  document.getElementById("dlgArea").showModal();
}

function renderAreaPicker(){
  const q = document.getElementById("aCmkSearch").value.toLowerCase();
  const list = CMK_HOSTS.filter(c => !q || c.name.toLowerCase().includes(q));
  const box = document.getElementById("aCmkPicker");
  box.innerHTML = list.length
    ? list.slice(0,300).map(c => `<div data-act="areapick" data-name="${esc(c.name)}">
        <input type="checkbox" ${AREA_PICKED.has(c.name)?"checked":""} data-act="areapick" data-name="${esc(c.name)}">
        <span>${esc(c.name)}</span></div>`).join("")
    : `<div style="color:var(--muted-2)">${CMK_HOSTS.length ? t("host.cmk_no_match") : t("host.cmk_none_loaded")}</div>`;

  const extra = [...AREA_PICKED].filter(n => !CMK_HOSTS.some(c => c.name === n));
  document.getElementById("aCmkPicked").innerHTML = [...AREA_PICKED].length
    ? [...AREA_PICKED].map(n => `<span>${esc(n)}${extra.includes(n) ? t("host.cmk_unknown_suffix") : ""}</span>`).join("")
    : `<span style="border-color:var(--rail);color:var(--muted-2)">${t("host.cmk_none_picked")}</span>`;
}
function toggleAreaPick(name){
  AREA_PICKED.has(name) ? AREA_PICKED.delete(name) : AREA_PICKED.add(name);
  renderAreaPicker();
}
document.getElementById("aCmkSearch").oninput = renderAreaPicker;

function renderAreaDays(){
  document.getElementById("aUpDays").innerHTML = WDAYS.map(([k,l]) =>
    `<span data-act="areaday" data-day="${k}" style="cursor:pointer;user-select:none;padding:5px 11px;
      ${AREA_PATCH_DAYS.has(k)
        ? "border-color:var(--accent);color:var(--accent);background:var(--accent-bg)"
        : "border-color:var(--rail);color:var(--muted-2)"}">${l}</span>`).join("");
}
function toggleAreaDay(k){
  AREA_PATCH_DAYS.has(k) ? AREA_PATCH_DAYS.delete(k) : AREA_PATCH_DAYS.add(k);
  renderAreaDays();
}

document.getElementById("aSave").onclick = async () => {
  const name = document.getElementById("aName").value.trim();
  if (!name){ toast(t("msg.need_area_name"), true); return; }
  if (document.getElementById("aUpEnabled").checked
      && (!AREA_PATCH_DAYS.size || !document.getElementById("aUpTime").value)){
    toast(t("msg.need_day_time"), true); return;
  }
  let res;
  try {
    res = await api("PATCH", `/api/v1/areas/${EDIT_AREA_ID}`, {
      name,
      patch_enabled: document.getElementById("aUpEnabled").checked,
      patch_days: [...AREA_PATCH_DAYS],
      patch_time: document.getElementById("aUpTime").value || null,
      patch_grace_hours: parseInt(document.getElementById("aUpGrace").value) || 4,
      patch_auto_reboot: document.getElementById("aUpReboot").checked,
      checkmk_hosts: [...AREA_PICKED],
      checkmk_downtime_all: document.getElementById("aDtAll").checked,
      downtime_minutes: parseInt(document.getElementById("aDowntime").value) || 30,
    });
  } catch(e){ return; }
  document.getElementById("dlgArea").close();
  toast(t("msg.saved"));
  // Nicht blockierend, wie schon beim Speichern in der Route entschieden -
  // nur ein Hinweis, dass sich der Bereichs-Zeitplan mit dem eines
  // enthaltenen Hosts in die Quere kommen koennte.
  if (res.patch_conflicts && res.patch_conflicts.length)
    toast(t("area.conflict_toast", { hosts: res.patch_conflicts.join(", ") }), true);
  await loadAreasTab();
  render();
};

// Geteilt zwischen dem Loeschen-Knopf im Bearbeiten-Dialog und dem neuen
// Remove-Knopf direkt in der Bereichsliste (Rueckmeldung Schritt 5) - beide
// tun dasselbe, nur der Dialog muss sich zusaetzlich noch schliessen.
async function removeArea(id, dialogSchliessen){
  const a = AREAS.find(x => x.id === id);
  if (!confirm(t("ask.remove_area", { name: a ? a.name : "" }))) return;
  try { await api("DELETE", `/api/v1/areas/${id}`); } catch(e){ return; }
  if (dialogSchliessen) document.getElementById("dlgArea").close();
  toast(t("msg.removed", { name: a ? a.name : "" }));
  await loadAreasTab();
  render();
}

document.getElementById("aDelete").onclick = () => removeArea(EDIT_AREA_ID, true);

async function scanArea(areaId){
  const hosts = HOSTS.filter(h => h.area_id === areaId && h.approval_state === "approved");
  for (const h of hosts){
    try { await api("POST", `/api/v1/hosts/${h.id}/jobs`, {job_type:"scan", params:{}}); } catch(e){}
  }
  toast(t("msg.scan_many", { anzahl: hosts.length }));
  afterAction();
}

async function patchArea(areaId){
  const a = AREAS.find(x => x.id === areaId);
  const hosts = HOSTS.filter(h => h.area_id === areaId && h.approval_state === "approved"
                                    && (h.updates_available||0) > 0);
  if (!hosts.length){ toast(t("area.nothing_to_patch"), true); return; }
  if (!confirm(t("ask.patch_area", { anzahl: hosts.length, bereich: a ? a.name : "" }))) return;
  for (const h of hosts){
    try {
      // source_area_id genau wie beim automatisch ausgeloesten Auftrag
      // (Schritt 2) - ein spaeter daraus genehmigter Neustart nutzt dann
      // die Downtime des Bereichs, nicht die des einzelnen Hosts.
      await api("POST", `/api/v1/hosts/${h.id}/jobs`, {
        job_type: "patch", params: { source_area_id: areaId },
      });
    } catch(e){}
  }
  toast(t("msg.patch_many", { anzahl: hosts.length }));
  afterAction();
}

// Nur Hosts mit tatsaechlichem Neustartbedarf, wie bei scanArea()/patchArea()
// eine gemeinsame Sicherheitsabfrage statt eines Dialogs pro Host - ein
// einzelner Host-Neustart (rebootHost()) fragt Downtime/Kulanz individuell
// ab, das skaliert auf "alle betroffenen Hosts einer Area" nicht sinnvoll.
async function rebootArea(areaId){
  const a = AREAS.find(x => x.id === areaId);
  const hosts = HOSTS.filter(h => h.area_id === areaId && h.approval_state === "approved"
                                    && h.reboot_required);
  if (!hosts.length){ toast(t("area.nothing_to_reboot"), true); return; }
  if (!confirm(t("ask.reboot_area", { anzahl: hosts.length, bereich: a ? a.name : "" }))) return;
  for (const h of hosts){
    try {
      await api("POST", `/api/v1/hosts/${h.id}/jobs`, {
        job_type: "reboot",
        set_downtime: true,
        grace_minutes: 10,
        downtime_minutes: (a && a.downtime_minutes) || h.downtime_minutes || 30,
        // source_area_id wie bei patchArea() - die Downtime bei der
        // Freigabe (agent_pre_reboot) gilt dann der Checkmk-Verknuepfung
        // des Bereichs, nicht der des einzelnen Hosts.
        params: { source_area_id: areaId },
      });
    } catch(e){}
  }
  toast(t("msg.reboot_many", { anzahl: hosts.length }));
  afterAction();
}

document.getElementById("aScan").onclick = () => scanArea(EDIT_AREA_ID);
document.getElementById("aPatch").onclick = () => patchArea(EDIT_AREA_ID);

/* ---------- Start ---------- */
document.getElementById("loginGo").onclick = doLogin;
document.getElementById("loginPass").addEventListener("keydown", e => {
  if (e.key === "Enter") doLogin();
});
document.getElementById("loginCode").addEventListener("keydown", e => {
  if (e.key === "Enter") doLogin();
});
document.getElementById("pwzGo").onclick = passwortZwangSpeichern;
document.getElementById("pwzNeu2").addEventListener("keydown", e => {
  if (e.key === "Enter") passwortZwangSpeichern();
});
document.getElementById("mfaCode").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("mfaConfirm").click();
});
document.getElementById("btnLogout").onclick = doLogout;

// Escape darf diese drei Dialoge nicht schliessen. Hinter jedem steht
// ein ausgeblendetes appShell - wer sie wegdrueckt, sieht eine leere
// Seite und haelt sie fuer kaputt. Beim Einrichtungsdialog kommt hinzu,
// dass er im Zwangsfall gar nicht verlassen werden darf; freiwillig
// geoeffnet gibt es dafuer den Knopf Abbrechen.
["dlgLogin", "dlgPwZwang", "dlgMfaSetup"].forEach(id => {
  document.getElementById(id).addEventListener("cancel", e => {
    e.preventDefault();
  });
});

// Stoesst den taeglichen Suchlauf an, wenn der Haken sitzt. Ohne 'force',
// also mit der 24-Stunden-Sperre im Backend. Fehler bleiben stumm - ein
// nicht erreichbares GitHub ist hier kein Anwendungsfehler, nur kein Fund.
async function pruefeAutoUpdate(){
  // Bewusst rohes fetch statt api(): api() zeigt bei jedem Fehler eine
  // Meldung, und ein Server ohne Internet-Zugang bekaeme sonst bei jedem
  // Start eine Fehlermeldung fuer etwas, das er gar nicht tun soll. Hier
  // ist ein nicht erreichbares GitHub kein Fund, kein Fehler - stumm.
  try {
    const r = await fetch(API + "/api/v1/update/remote-config",
                          { credentials: "same-origin" });
    if (!r.ok) return;
    const cfg = await r.json();
    if (!cfg.auto) return;
    await fetch(API + "/api/v1/update/check",
                { method: "POST", credentials: "same-origin" });
  } catch(e){ /* stumm */ }
}

async function startApp(){
  try { ME = await api("GET", "/api/v1/me"); } catch(e){ return; }

  // Erzwungener Passwortwechsel. Das Backend laesst mit gesetztem Flag nur
  // noch /me, /me/password und die Abmeldung durch - hier geht es also
  // nicht darum, etwas zu verhindern, sondern darum, dem Benutzer zu
  // sagen, was er tun soll, statt ihn gegen lauter 403 laufen zu lassen.
  if (ME.must_change_password){ zeigePasswortZwang(); return; }

  // Dasselbe fuer die Pflicht zur Anmeldung in zwei Schritten: das
  // Backend laesst dieses Konto dann nur noch an MFA_FREI. Ohne diesen
  // Zweig saehe der Administrator ein Dashboard, das an jeder Ecke 403
  // liefert, und wuesste nicht warum.
  if (ME.mfa_setup_required){
    document.getElementById("appShell").style.display = "none";
    await mfaEinrichtenOeffnen(true);
    return;
  }
  document.getElementById("whoami").textContent =
    ME.username + (ME.is_admin ? " · " + t("settings.users.role_admin") : "");
  // Reiter, die nur Administratoren sehen sollen
  document.querySelectorAll(".admin-only").forEach(el => {
    el.style.display = ME.is_admin ? "" : "none";
  });

  // Automatischer Update-Suchlauf: nur fuer Administratoren, nur wenn der
  // Haken sitzt. Der Server drosselt auf einmal je 24 Stunden - fuenf
  // offene Browser loesen also nicht fuenf Abrufe aus. Bewusst nicht
  // abgewartet: ein langsames oder abgeschaltetes GitHub darf den Start
  // der Oberflaeche nicht aufhalten. Das Ergebnis holt checkVersion() aus
  // /api/health beim naechsten Durchlauf.
  if (ME.is_admin) pruefeAutoUpdate();

  try {
    const h = await (await fetch(API + "/api/health", {cache: "no-store"})).json();
    AGENT_VER = h.agent_version || "";
    PAGE_VERSION = h.version;
    // Nur die Versionszeile ersetzen - textContent auf dem footer wuerde
    // die Zeile mit dem Urheberhinweis mit loeschen.
    if (h.version) document.getElementById("footVersion").textContent = `CO-37 v${h.version}`;
  } catch(e){}
  try { await loadCmk(); } catch(e){}
  // Einmalig registrieren: die Zeilen werden bei jedem Zeichnen ersetzt,
  // die Handler haengen deshalb am Behaelter statt an den Zeilen.
  initSorting();
  renderRackHead();   // vor dem ersten Laden, sonst kurz eine leere Leiste
  // Auf Breitenwechsel reagieren - Drehen des Geraets oder Ziehen des
  // Fensters. Ohne das bliebe die Kopfzeile bis zum naechsten Zeichnen im
  // falschen Zustand.
  try {
    matchMedia(RACK_NARROW_QUERY).addEventListener("change", renderRackHead);
  } catch(e){
    // Aeltere Browser kennen addEventListener auf MediaQueryList nicht.
    if (typeof addEventListener === "function")
      addEventListener("resize", renderRackHead);
  }
  try { await load(); } catch(e){}
  scheduleRefresh();
}

/* ---------- Sprache ---------- */
/*
 * Reihenfolge: Wahl des Benutzers, Cookie, Vorgabe der Installation,
 * Englisch. Die Vorgabe kommt aus /api/health, weil die Anmeldeseite sie
 * braucht, bevor irgendein Benutzer bekannt ist.
 *
 * Laeuft mit dem, was gerade da ist, und ohne zu scheitern: Ist der Server
 * nicht erreichbar, bleibt es beim Cookie oder bei Englisch. Eine
 * Oberflaeche, die wegen einer Spracheinstellung nicht startet, waere der
 * schlechtere Tausch.
 */
let VORGABE_SPRACHE = SPRACHE_VORGABE;

async function spracheAnwenden(benutzer){
  try {
    const h = await (await fetch(API + "/api/health", {cache: "no-store"})).json();
    VORGABE_SPRACHE = pruefeSprache(h.default_language) || SPRACHE_VORGABE;
  } catch(e){}
  // Beim Ermitteln nicht ins Cookie schreiben: sonst wuerde die Vorgabe der
  // Installation beim ersten Besuch als eigene Wahl festgeschrieben und
  // eine spaetere Aenderung der Vorgabe erreichte niemanden mehr.
  setzeSprache(ermittleSprache({ benutzer, vorgabe: VORGABE_SPRACHE }),
               { merken: false });
  spracheImDialogZeigen();
}

/*
 * Setzt beide Auswahlfelder auf den tatsaechlichen Stand.
 *
 * Ohne das zeigte die Vorgabe der Installation immer den ersten Eintrag -
 * unabhaengig davon, was gespeichert ist. Wer sie dann anfasst, um etwas
 * einzustellen, setzte still Englisch. Ein Bedienelement, das ueber den
 * eigenen Zustand luegt, ist schlimmer als keins.
 */
function spracheImDialogZeigen(){
  const meine = document.getElementById("spMeine");
  const vorgabe = document.getElementById("spVorgabe");
  if (meine) meine.value = SPRACHE;
  if (vorgabe) vorgabe.value = VORGABE_SPRACHE;
}

/*
 * Was das Skript selbst zeichnet - Hostliste, Kopfzeile, offene Dialoge -
 * ruehrt uebersetzeDom() nicht an: dort stehen keine data-i18n-Marken,
 * sondern es wird bei jedem Zeichnen neu aufgebaut. Ohne diesen Aufruf
 * bliebe die Tabelle nach dem Umschalten in der alten Sprache stehen, bis
 * die naechste Aktualisierung von selbst kommt - je nach Lage bis zu
 * fuenfzehn Sekunden. Das saehe nach einem Fehler aus.
 */
function neuZeichnenNachSprachwechsel(){
  try { renderRackHead(); } catch(e){}
  try { render(); } catch(e){}
}

async function spracheWaehlen(code){
  if (!setzeSprache(code)) return;          // schreibt das Cookie
  neuZeichnenNachSprachwechsel();
  try {
    await api("POST", "/api/v1/me/language", { language: code });
    if (ME) ME.language = code;
    toast(t("language.own.saved"));
  } catch(e){
    // Die Oberflaeche steht bereits um. Dass der Server sie sich nicht
    // merken konnte, hat api() schon gemeldet.
  }
  spracheImDialogZeigen();
}

async function vorgabespracheWaehlen(code){
  try {
    await api("POST", "/api/v1/settings/language", { language: code });
    VORGABE_SPRACHE = pruefeSprache(code) || VORGABE_SPRACHE;
    toast(t("language.default.saved"));
  } catch(e){
    // Nicht uebernommen: das Feld wieder auf den Stand zurueckstellen, der
    // wirklich gespeichert ist. Sonst bliebe die Anzeige auf einem Wert
    // stehen, den der Server nie bekommen hat.
  }
  spracheImDialogZeigen();
}

document.getElementById("spMeine").onchange =
  (e) => spracheWaehlen(e.target.value);
document.getElementById("spVorgabe").onchange =
  (e) => vorgabespracheWaehlen(e.target.value);

(async () => {
  // Ob eine Sitzung besteht, weiss nur der Server - das Cookie ist fuer
  // JavaScript unsichtbar. Also einfach fragen: bei 401 schaltet api()
  // selbst auf die Anmeldemaske.
  try {
    ME = await api("GET", "/api/v1/me");
    setSession(true);
  } catch(e){
    // Auch die Anmeldemaske will in der richtigen Sprache erscheinen.
    await spracheAnwenden(null);
    return;
  }
  await spracheAnwenden(ME.language);
  document.getElementById("appShell").style.display = "";
  await startApp();
})();
