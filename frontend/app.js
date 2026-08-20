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

function showLogin(msg){
  ME = null;
  document.getElementById("loginError").textContent = msg || "";
  document.getElementById("loginUser").value = "";
  document.getElementById("loginPass").value = "";
  document.getElementById("appShell").style.display = "none";
  const dlg = document.getElementById("dlgLogin");
  if (!dlg.open) dlg.showModal();
  document.getElementById("loginUser").focus();
}

async function doLogin(){
  const username = document.getElementById("loginUser").value.trim();
  const password = document.getElementById("loginPass").value;
  if (!username || !password){
    document.getElementById("loginError").textContent = "Bitte beides ausfüllen.";
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
    document.getElementById("loginError").textContent = "Server nicht erreichbar.";
    return;
  }
  if (!res.ok){
    let msg = "Anmeldung fehlgeschlagen.";
    try { msg = (await res.json()).detail || msg; } catch(e){}
    document.getElementById("loginError").textContent = msg;
    return;
  }
  await res.json();   // Inhalt wird nicht mehr gebraucht - das Cookie zaehlt
  setSession(true);
  document.getElementById("dlgLogin").close();
  document.getElementById("appShell").style.display = "";
  await startApp();
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

let HOSTS = [], CMK_HOSTS = [], AGENT_VER = "";
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

  if (PAGE_VERSION === null){ PAGE_VERSION = h.version; return; }
  if (h.version === PAGE_VERSION) return;

  const bar = document.getElementById("reloadBar");
  document.getElementById("reloadText").textContent =
    `CO-37 wurde auf ${h.version} aktualisiert — diese Seite läuft noch mit ${PAGE_VERSION}.`;
  bar.style.display = "flex";
}
document.getElementById("reloadNow").onclick = () => location.reload();

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
    throw new Error("Server nicht erreichbar (" + res.status + ")");
  }
  // Ab hier hat das Backend selbst geantwortet - auch ein 403 ist eine
  // Antwort. Also ist es wieder da.
  verbindungDa();
  // 401 heisst: Sitzung abgelaufen oder verworfen. Zurueck zur Anmeldung,
  // statt den Benutzer mit Fehlermeldungen zu bewerfen.
  if (res.status === 401){ setSession(false); showLogin("Sitzung abgelaufen. Bitte erneut anmelden."); throw new Error("401"); }
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
  return d.toLocaleString("de-DE", opts);
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
    toast("Nichts zu kopieren", true);
    return;
  }

  if (navigator.clipboard && window.isSecureContext){
    try {
      await navigator.clipboard.writeText(text);
      toast(label + " kopiert");
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
    toast(ok ? label + " kopiert" : "Kopieren nicht möglich — bitte markieren", !ok);
  } catch(e){
    toast("Kopieren nicht möglich — bitte markieren und Strg+C", true);
  }
}

/* ---------- Zustand ---------- */
/* Klartext der Neustart-Gruende. Muss zu REBOOT_REASON_TEXT im Agent passen. */
const REBOOT_REASONS = {
  windows_update: "Windows Update",
  cbs_pending: "Komponentenspeicher",
  cbs_inprogress: "Komponentenspeicher",
  cbs_packages: "Komponentenspeicher",
  pending_rename: "Dateireste",
  netlogon: "Domänenbeitritt",
  package_manager: "Paketverwaltung",
  kernel: "neuer Kernel",
};
// Gruende, die einen Neustart nur anzeigen, ihn aber nicht rechtfertigen.
const WEAK_REASONS = ["pending_rename"];

function rebootNote(h){
  // reboot_required ist seit 0.15.1 nur noch bei belastbaren Gruenden
  // gesetzt. Nachrangige Eintraege wie vorgemerkte Dateireste stehen
  // zwar weiter in reboot_reasons, fuehren aber zu keiner Meldung.
  const rs = (h.reboot_reasons || []).filter(r => !WEAK_REASONS.includes(r));
  const why = rs.length ? ` (${rs.map(r => REBOOT_REASONS[r] || r).join(", ")})` : "";
  return (h.auto_reboot ? "Neustart nötig, freigegeben" : "Neustart nötig") + why;
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
    box.innerHTML = `<div class="empty">${HOSTS.length
      ? "Kein Host passt zum Filter."
      : "Noch kein Host angemeldet.<br><br>Agent auf einem Zielsystem installieren — siehe Einstellungen → Agents."}</div>`;
    return;
  }

  // Einmal ausgewertet statt in jeder Zeile. Rein zur Anzeige - die
  // Berechtigung durchsetzen tut die API, nicht diese Abfrage.
  const admin = !!(ME && ME.is_admin);
  box.innerHTML = list.map((h,i) => {
    const s = stateOf(h);
    const upd = h.updates_available||0, sec = h.security_updates||0;
    const waiting = h.approval_state === "pending";
    const stale = AGENT_VER && h.agent_version && h.agent_version !== AGENT_VER;

    const act = ACTIVE[h.id];
    const notes = [];
    if (act) notes.push(act.progress ? `${act.job_type}: ${act.progress}` : `${act.job_type} läuft`);
    // Auftrag steht auf laufend, der Host meldet aber nicht mehr. Ohne
    // diesen Hinweis sieht die Zeile aus wie normale Arbeit.
    if (act && h.status !== "online") notes.push("Host meldet sich nicht");
    if (waiting) notes.push("wartet auf Freigabe");
    if (h.approval_state === "rejected") notes.push("abgelehnt");
    // Ein Host, der sich angemeldet, aber nie gemeldet hat. Kann ein
    // abgebrochenes Aufsetzen sein - oder jemand, der den Namen eines noch
    // nicht eingerichteten Systems belegt hat, damit dieses sich spaeter
    // nicht anmelden kann.
    if (h.approval_state === "pending" && !h.last_seen)
      notes.push("noch nie gemeldet");
    if (h.reboot_required) notes.push(rebootNote(h));
    // Vorschau, kein Zustand: die gefundenen Updates ziehen einen Neustart
    // nach sich. Nur zeigen, solange noch keiner aussteht - sonst stuenden
    // zwei Meldungen zum selben Thema nebeneinander.
    else if (h.updates_require_reboot && h.updates_available > 0)
      notes.push("Updates erfordern Neustart");
    if (stale) notes.push(`Agent ${h.agent_version} veraltet`);
    if (PLANNED[h.id]) notes.push(`Neustart geplant: ${PLANNED[h.id].toLocaleString("de-DE",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}`);
    if (h.patch_enabled && h.next_patch_run) notes.push(`Updates: ${fmtTime(h.next_patch_run,{weekday:"short",hour:"2-digit",minute:"2-digit"})}`);
    // Windows legt nach einem Neustart kumulativ nach. Der Nachschlag ist
    // vorgemerkt und laeuft an, sobald der Host wieder meldet.
    if (h.patch_followup_left > 0) notes.push(`Nachschlag vorgemerkt (${h.patch_followup_left})`);

    return `<div class="unit" data-id="${h.id}">
      <div class="slot${SORTABLE ? " grab" : ""}"${SORTABLE ? ' draggable="true" title="Ziehen, um die Reihenfolge zu ändern"' : ' title="Reihenfolge ändern nur ohne aktiven Filter"'}>${String(i+1).padStart(2,"0")}</div>
      <div class="led ${LED[s]}"></div>
      <div class="hostcell">
        <div class="hostname">${esc(h.display_name || h.hostname)}</div>
        <div class="sub">${esc(h.hostname)}${notes.length ? " · " + notes.join(" · ") : ""}</div>
      </div>
      <div class="os">${h.os_type || "—"}</div>
      <div class="updates"><span class="chip ${upd?"warn":"zero"}">${upd}</span></div>
      <div class="reboot"><span class="chip ${sec?"crit":"zero"}">${sec}</span></div>
      <div class="cmk ${hasDt(h) ? "linked":""}">${
        h.checkmk_downtime_all
          ? `alle Hosts · ${h.downtime_minutes} min`
          : h.checkmk_hosts?.length
            ? esc(h.checkmk_hosts.join(", ")) + ` · ${h.downtime_minutes} min`
            : "nicht verknüpft"}</div>
      <div class="actions">
        ${waiting
          ? (admin
             ? `<button class="primary" data-act="approve" data-id="${h.id}">Freigeben</button>
                <button class="danger" data-act="reject" data-id="${h.id}">Ablehnen</button>`
             : `<span style="color:var(--muted-2);font-size:11px">wartet auf Freigabe</span>`)
          : `${act ? `<button class="primary" data-act="live" data-id="${act.id}" data-title="${esc(h.display_name||h.hostname)}">Live</button>` : ""}
             <button data-act="scan" data-id="${h.id}">Prüfen</button>
             <button data-act="patch" data-id="${h.id}" ${upd?"":"disabled"}>Patchen</button>
             <button class="warn" data-act="reboot" data-id="${h.id}">Neustart</button>
             ${stale && admin ? `<button data-act="agentupd" data-id="${h.id}">Agent</button>` : ""}
             <button data-act="detail" data-id="${h.id}">Verlauf</button>`}
        ${admin ? `<button data-act="edithost" data-id="${h.id}">…</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

/* ---------- Reihenfolge per Drag and Drop ---------- */
/*
 * Verschiebt ausschliesslich die Anzeige. Auf Zeitplaene, Wartungsfenster
 * oder die Ausfuehrung von Auftraegen hat die Folge keinen Einfluss - es
 * gibt keine Abarbeitung in Reihe, jeder Host wertet beim eigenen Kontakt
 * fuer sich aus.
 *
 * Angefasst wird nur die Platznummer, nicht die ganze Zeile: sonst startet
 * jeder Zug an einer Schaltflaeche oder an markiertem Text einen Drag.
 */
function unitsBox(){ return document.getElementById("units"); }

function clearDropMarks(){
  unitsBox().querySelectorAll(".unit").forEach(u => {
    u.classList.remove("dropbefore", "dropafter");
  });
}

async function saveOrder(){
  try {
    await api("POST", "/api/v1/hosts/order", { ids: HOSTS.map(h => h.id) });
  } catch(e){
    toast("Reihenfolge konnte nicht gespeichert werden", true);
    // Gespeicherten Stand zurueckholen, statt eine Anzeige stehen zu
    // lassen, die es auf dem Server nicht gibt.
    load().catch(() => {});
  }
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
    const unit = e.target.closest(".unit");
    if (!unit) return;
    e.preventDefault();
    const targetId = parseInt(unit.dataset.id);
    if (targetId === DRAG_ID) return;

    const rect = unit.getBoundingClientRect();
    const after = e.clientY > rect.top + rect.height / 2;

    const from = HOSTS.findIndex(h => h.id === DRAG_ID);
    if (from < 0) return;
    const [moved] = HOSTS.splice(from, 1);
    // Zielposition erst nach dem Herausnehmen bestimmen, sonst verschiebt
    // sich der Index um eins, wenn nach unten gezogen wird.
    let to = HOSTS.findIndex(h => h.id === targetId);
    if (to < 0){ HOSTS.splice(from, 0, moved); return; }
    HOSTS.splice(after ? to + 1 : to, 0, moved);

    DRAG_ID = null;
    clearDropMarks();
    render();
    saveOrder();
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
  agentupd:   (d) => updateAgent(+d.id),
  detail:     (d) => detail(+d.id),
  edithost:   (d) => editHost(+d.id),
  canceljob:  (d) => cancelJob(+d.id),
  pick:       (d) => togglePick(d.name),
  day:        (d) => toggleDay(d.day),
  resetpw:    (d) => resetPw(+d.id, d.username),
  deluser:    (d) => delUser(+d.id, d.username),
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
let PLANNED = {}, ACTIVE = {};
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
  try {
    ACTIVE = {};
    (await api("GET", "/api/v1/jobs/active")).forEach(j => { ACTIVE[j.host_id] = j; });
  } catch(e){ ACTIVE = {}; }

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
  toast("Prüfung eingeplant — wird beim nächsten Agent-Kontakt ausgeführt");
  afterAction();
}

async function patch(id){
  const h = HOSTS.find(x => x.id === id);
  const policy = h.auto_reboot
    ? (h.maintenance_window
        ? `Ist ein Neustart nötig, erfolgt er im Fenster ${h.maintenance_window}.`
        : "Ist ein Neustart nötig, erfolgt er anschließend.")
    : "Ist ein Neustart nötig, wird er gemeldet, aber nicht ausgeführt.";
  // Vor der Bestaetigung ansagen, dass ein Neustart faellig wird. Bisher
  // stand das erst im Bericht nach dem Scan, und dort auch nur
  // missverstaendlich.
  const willReboot = h.updates_require_reboot
    ? "Unter den Updates sind Pakete, die einen Neustart erfordern (z.B. Kernel).\n"
    : "";
  if (!confirm(`${h.updates_available} Updates auf ${h.hostname} installieren.\n\n${willReboot}${policy}`)) return;
  await api("POST", `/api/v1/hosts/${id}/jobs`, {job_type:"patch", params:{}});
  toast("Patch-Auftrag eingeplant");
  afterAction();
}

let REBOOT_ID = null;

function rebootHost(id){
  const h = HOSTS.find(x => x.id === id);
  REBOOT_ID = id;
  const linked = hasDt(h);

  document.getElementById("rbTitle").textContent = `${h.display_name || h.hostname} neu starten`;
  document.getElementById("rbInfo").innerHTML =
    `Startet <b>${esc(h.hostname)}</b> neu. Es werden keine Updates installiert.`;
  rbDowntime.checked = true;
  rbDtMin.value = h.downtime_minutes || 30;
  rbGrace.value = 10;

  document.getElementById("rbWarn").innerHTML = linked
    ? `Downtime wird gesetzt auf: <b>${esc(dtTargetText(h))}</b>. Scheitert sie, unterbleibt der Neustart.`
    : '<span style="color:var(--led-pending)">Kein Checkmk-Host verknüpft — es kann keine Downtime gesetzt werden. Das Monitoring wird beim Neustart Alarm schlagen.</span>';

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
  toast(`Neustart ausgelöst — verfällt nach ${grace} Minuten ohne Kontakt`);
  afterAction();
};

async function updateAgent(id){
  await api("POST", `/api/v1/hosts/${id}/jobs`, {job_type:"selfupdate", params:{}});
  toast("Agent-Aktualisierung eingeplant");
  afterAction();
}

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
  toast("Host freigegeben");
}
async function reject(id){
  if (!confirm("Host ablehnen? Er bekommt keine Aufträge und bleibt in der Liste.")) return;
  await api("POST", `/api/v1/hosts/${id}/reject`); await load(); toast("Host abgelehnt");
}

async function scanAll(){
  const ok = HOSTS.filter(h => h.approval_state === "approved");
  for (const h of ok){ try { await api("POST", `/api/v1/hosts/${h.id}/jobs`, {job_type:"scan", params:{}}); } catch(e){} }
  toast(`Prüfung für ${ok.length} Hosts eingeplant`);
  afterAction();
}

async function detail(id){
  const h = HOSTS.find(x => x.id === id);
  document.getElementById("dTitle").textContent = h.display_name || h.hostname;
  document.getElementById("log").textContent = "Wird geladen…";
  document.getElementById("dlgDetail").showModal();
  const jobs = await api("GET", `/api/v1/jobs?host_id=${id}&limit=25`);
  const ups  = await api("GET", `/api/v1/hosts/${id}/updates`);
  const L = [];
  L.push(`SYSTEM: ${h.os_version || "?"} · Agent ${h.agent_version || "?"} · ${h.ip_address || "?"}`);
  L.push(`ANGEMELDET: ${fmtTime(h.enrolled_at)}${h.enrolled_from_ip ? " von " + h.enrolled_from_ip : ""}`);
  L.push("", `OFFENE UPDATES (${ups.length})`);
  ups.slice(0,80).forEach(u => L.push(`  ${u.is_security?"[SIC]":"     "} ${u.package_id}  ${u.new_version||""}`));
  document.getElementById("log").textContent = L.join("\n");

  // Auftragsliste anklickbar, damit das Protokoll abrufbar ist
  const rows = jobs.map(j => {
    const when = fmtTime(j.created_at);
    const cls = j.state === "failed" ? "err" : j.state === "running" ? "run"
              : j.state === "done" ? "ok" : "";
    return `<div class="vrow" style="margin-bottom:5px">
      <span>#${j.id} · ${esc(j.job_type)}<br>
        <span style="color:var(--muted-2)">${when}${j.scheduled_at ? " · geplant" : ""}${j.error ? " · " + esc(j.error.slice(0,60)) : ""}</span></span>
      <span style="display:flex;gap:8px;align-items:center">
        <span class="state ${cls}">${(LV_STATE[j.state]||[j.state])[0]}</span>
        <button data-act="live" data-id="${j.id}" data-title="#${j.id} ${esc(j.job_type)}">Protokoll</button>
      </span></div>`;
  }).join("");
  document.getElementById("dJobs").innerHTML =
    `<div class="section-title" style="margin-bottom:8px">Aufträge (${jobs.length})</div>`
    + (rows || '<span style="color:var(--muted-2)">keine</span>');
}

/* ---------- Live-Ausgabe ---------- */
let LIVE = {jobId:null, offset:0, timer:null};

async function showLive(jobId, title){
  clearInterval(LIVE.timer);
  LIVE = {jobId, offset:0, timer:null};
  document.getElementById("lvTitle").textContent = title || `Auftrag #${jobId}`;
  document.getElementById("lvLog").textContent = "";
  document.getElementById("lvProgress").textContent = "Wird geladen…";
  document.getElementById("lvMeta").textContent = "";
  document.getElementById("lvState").textContent = "";
  document.getElementById("dlgLive").showModal();
  await tickLive();
  LIVE.timer = setInterval(tickLive, 1000);
}

const LV_STATE = {
  pending:["wartet",""], running:["läuft","run"], done:["fertig","ok"],
  failed:["fehlgeschlagen","err"], cancelled:["abgebrochen","warn"]
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

  const [txt, cls] = LV_STATE[d.state] || [d.state, ""];
  const st = document.getElementById("lvState");
  st.textContent = txt; st.className = "state " + cls;

  document.getElementById("lvProgress").textContent =
    d.progress || (d.running ? "läuft…" : d.error ? `Fehler: ${d.error}` : "—");
  document.getElementById("lvMeta").textContent =
    `${(d.size/1024).toFixed(1)} KB` + (d.job_type ? ` · ${d.job_type}` : "");

  // Ohne Ausgabe je nach Zustand erklaeren, warum. Frueher stand hier
  // pauschal ein Hinweis auf Auftraege von vor 0.6.0 - bei einem noch
  // wartenden Auftrag war das schlicht falsch.
  if (!box.textContent && !d.exists && !d.running){
    box.textContent =
      d.state === "pending"
        ? "Der Auftrag wartet auf den nächsten Kontakt des Agents.\n\n"
          + "Eine laufende Ausgabe entsteht erst, wenn er ihn abholt."
      : d.state === "cancelled"
        ? "Der Auftrag wurde verworfen, bevor er lief."
          + (d.error ? `\n\n${d.error}` : "")
        : "Für diesen Auftrag liegt keine Ausgabe vor.";
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
  copy(document.getElementById("lvLog").textContent, "Protokoll");

/* ---------- Host bearbeiten ---------- */
function renderPicker(){
  const q = document.getElementById("hCmkSearch").value.toLowerCase();
  const list = CMK_HOSTS.filter(c => !q || c.name.toLowerCase().includes(q));
  const box = document.getElementById("hCmkPicker");
  box.innerHTML = list.length
    ? list.slice(0,300).map(c => `<div data-act="pick" data-name="${esc(c.name)}">
        <input type="checkbox" ${PICKED.has(c.name)?"checked":""} data-act="pick" data-name="${esc(c.name)}">
        <span>${esc(c.name)}</span></div>`).join("")
    : `<div style="color:var(--muted-2)">${CMK_HOSTS.length ? "Kein Treffer." : "Keine Checkmk-Hosts geladen. Verbindung prüfen."}</div>`;

  // Bereits verknüpfte Namen, die Checkmk nicht kennt, trotzdem zeigen
  const extra = [...PICKED].filter(n => !CMK_HOSTS.some(c => c.name === n));
  document.getElementById("hCmkPicked").innerHTML = [...PICKED].length
    ? [...PICKED].map(n => `<span>${esc(n)}${extra.includes(n) ? " (unbekannt)" : ""}</span>`).join("")
    : '<span style="border-color:var(--rail);color:var(--muted-2)">keine ausgewählt</span>';
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
const RACK_COLUMNS = ["Nr", "", "Host", "System", "Updates", "Sicherheit",
                      "Checkmk", "Aktionen"];

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
    if (i === 0) return `<div class="slot">${esc(label)}</div>`;
    if (!label) return "<div></div>";
    const last = i === RACK_COLUMNS.length - 1;
    return `<div${last ? ' style="text-align:right"' : ""}>${esc(label)}</div>`;
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
    ? "alle in Checkmk konfigurierten Hosts"
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
    `${esc(h.os_version || "?")}<br>Agent ${esc(h.agent_version || "?")} · ${esc(h.ip_address || "?")}<br>`
    + `Angemeldet ${fmtTime(h.enrolled_at)}`;

  // Planung vorbelegen
  plWhen.value = "";
  plDowntime.checked = true;
  plGrace.value = 120;
  const linked = hasDt(h);
  document.getElementById("plHint").innerHTML = linked
    ? `Downtime von ${h.downtime_minutes} Minuten auf: ${esc(dtTargetText(h))}`
    : '<span style="color:var(--led-pending)">Kein Checkmk-Host verknüpft — es kann keine Downtime gesetzt werden. Das Monitoring wird beim Neustart Alarm schlagen.</span>';

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
    ? fmtTime(h.last_patch_run) : "noch nie";
  document.getElementById("upRebootHint").innerHTML = (linked
    ? `Vor dem Neustart wird eine Downtime von ${h.downtime_minutes} Minuten gesetzt. Scheitert sie, unterbleibt der Neustart.`
    : '<span style="color:var(--led-pending)">Ohne Checkmk-Verknüpfung wird keine Downtime gesetzt und der Neustart unterbleibt. Erst unter Checkmk verknüpfen.</span>')
    // Das Wartungsfenster aus dem Reiter Allgemein greift auch hier. Ohne
    // diesen Hinweis wundert man sich, warum der Neustart trotz Haken
    // ausbleibt.
    + (h.maintenance_window
      ? `<br>Zusätzlich gilt das Wartungsfenster <b>${esc(h.maintenance_window)}</b> aus dem Reiter Allgemein. Fällt der Termin nicht hinein, unterbleibt der Neustart.`
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
          const dt = j.params?.skip_downtime ? "ohne Downtime" : "mit Downtime";
          return `<div class="vrow" style="margin-bottom:5px">
            <span>Neustart am ${when}<br><span style="color:var(--muted-2)">${dt} · Auftrag #${j.id}</span></span>
            <button class="danger" data-act="canceljob" data-id="${j.id}">Abbrechen</button>
          </div>`;
        }).join("")
      : "Nichts eingeplant.";
  } catch(e){ box.textContent = "Konnte nicht geladen werden."; }
}

async function cancelJob(id){
  if (!confirm("Eingeplanten Neustart abbrechen?")) return;
  await api("DELETE", `/api/v1/jobs/${id}`);
  await loadPlanned();
  await load();
  toast("Abgebrochen");
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
  if (!plWhen.value){ toast("Bitte Datum und Uhrzeit angeben", true); return; }
  const when = new Date(plWhen.value);
  if (when <= new Date()){ toast("Der Zeitpunkt liegt in der Vergangenheit", true); return; }

  const h = HOSTS.find(x => x.id === EDIT_ID);
  const linked = hasDt(h);
  const wantDt = plDowntime.checked;

  let msg = `Neustart von ${h.hostname} am ${when.toLocaleString("de-DE")} einplanen?\n\n`;
  if (wantDt && linked)
    msg += `Vorher wird eine Downtime von ${h.downtime_minutes} Minuten gesetzt auf:\n${h.checkmk_downtime_all ? "alle in Checkmk konfigurierten Hosts" : h.checkmk_hosts.join("\n")}\n\nDer Neustart erfolgt nur, wenn die Downtime steht.`;
  else if (wantDt && !linked)
    msg += "WARNUNG: Downtime gewünscht, aber kein Checkmk-Host verknüpft. Es wird KEINE Downtime gesetzt — das Monitoring wird Alarm schlagen.";
  else
    msg += "Ohne Downtime. Das Monitoring wird Alarm schlagen.";
  msg += `\n\nNachlaufzeit ${parseInt(plGrace.value)||120} Minuten: meldet sich der Host bis dahin nicht, wird der Neustart verworfen statt nachgeholt.`;

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
  toast("Neustart eingeplant");
};

document.getElementById("hSave").onclick = async () => {
  if (upEnabled.checked && (!PATCH_DAYS.size || !upTime.value)){
    toast("Für den Update-Zeitplan Wochentag und Uhrzeit angeben", true); return;
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
  toast("Gespeichert");
};

document.getElementById("hResetToken").onclick = async () => {
  const h = HOSTS.find(x => x.id === EDIT_ID);
  if (!confirm(`Agent-Token von ${h.hostname} zurückziehen?\n\n`
    + `Der Agent meldet sich beim nächsten Kontakt neu an und wartet dann `
    + `auf Freigabe. Zeitplan und Verknüpfungen bleiben erhalten.\n\n`
    + `Bis zur erneuten Anmeldung kann sich jedes Gerät im Netz unter `
    + `diesem Namen melden — die Freigabe also nicht blind erteilen.`)) return;
  await api("POST", `/api/v1/hosts/${EDIT_ID}/reset-token`);
  document.getElementById("dlgHost").close();
  await load();
  toast("Token zurückgezogen — Host wartet auf erneute Anmeldung");
};

document.getElementById("hDelete").onclick = async () => {
  const h = HOSTS.find(x => x.id === EDIT_ID);
  if (!confirm(`${h.hostname} entfernen?\n\nAufträge und Update-Liste werden gelöscht. Der Agent kann sich danach neu anmelden.`)) return;
  await api("DELETE", `/api/v1/hosts/${EDIT_ID}`);
  document.getElementById("dlgHost").close();
  await load();
  toast("Host entfernt");
};

/* ---------- Downtime von Hand ---------- */
document.getElementById("dtSet").onclick = async () => {
  const body = {comment: dtComment.value.trim() || "CO-37: Manuelle Wartung"};
  if (dtStart.value && dtEnd.value){
    body.start = new Date(dtStart.value).toISOString();
    body.end = new Date(dtEnd.value).toISOString();
  } else if (dtMinutes.value){
    body.minutes = parseInt(dtMinutes.value);
  } else {
    toast("Bitte Minuten oder eine Zeitspanne angeben", true); return;
  }
  const res = await api("POST", `/api/v1/hosts/${EDIT_ID}/downtime`, body);
  const out = document.getElementById("dtOut");
  out.textContent = `Gesetzt (${res.minutes} min): ${res.ok.join(", ") || "—"}`
    + (Object.keys(res.failed||{}).length ? `\nFehlgeschlagen: ${Object.keys(res.failed).join(", ")}` : "");
  toast("Downtime gesetzt");
};
document.getElementById("dtClear").onclick = async () => {
  if (!confirm("Alle von CO-37 gesetzten Downtimes dieses Hosts aufheben?")) return;
  const res = await api("DELETE", `/api/v1/hosts/${EDIT_ID}/downtime`);
  document.getElementById("dtOut").textContent =
    "Aufgehoben: " + Object.entries(res.removed||{}).map(([k,v]) => `${k} (${v})`).join(", ");
  toast("Downtimes aufgehoben");
};
document.getElementById("dtList").onclick = async () => {
  const res = await api("GET", `/api/v1/hosts/${EDIT_ID}/downtime`);
  const out = document.getElementById("dtOut");
  if (res.reason){ out.textContent = res.reason; return; }
  out.textContent = res.downtimes.length
    ? res.downtimes.map(d => `${d.host_name}${d.service_description ? " / "+d.service_description : ""} — ${d.comment}`).join("\n")
    : "Keine aktiven Downtimes.";
};

/* ---------- Einstellungen ---------- */
document.querySelectorAll("[data-close]").forEach(b => b.onclick = e => e.target.closest("dialog").close());
// Eingegrenzt auf #sTabs und die eigenen Bereiche. Ein ungenauer Selektor
// hat hier vorher die Reiter des Host-Dialogs mit ueberschrieben.
const STABS = ["tabAgents","tabCmk","tabUpd","tabProxy","tabLizenz",
               "tabAccount","tabUsers","tabAudit"];
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
    loadUsers(); loadAudit(); loadProxy();
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
  toast("Checkmk verbunden");
};

/* ---------- Agents ---------- */
async function downloadPkg(name){
  try {
    const res = await fetch(`${API}/api/v1/packages/${encodeURIComponent(name)}`,
                            {credentials: "same-origin"});
    if (!res.ok){ toast("Download fehlgeschlagen", true); return; }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  } catch(e){ toast("Download fehlgeschlagen", true); }
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
    toast("Anzeige der Agents fehlgeschlagen: " + (e.message || e), true);
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
    want || (loadError ? "nicht abrufbar" : "—");

  document.getElementById("agBase").innerHTML =
    `Die Befehle tragen <b>${esc(base)}</b> ein.`
    + (PUBLIC_URL
        ? ` Fest eingetragen unter Einstellungen / Zugang.`
        : ` Das ist die Adresse, unter der du dieses Dashboard gerade aufgerufen `
          + `hast. Unter <b>Zugang</b> lässt sich eine feste Adresse hinterlegen — `
          + `sonst hängt es davon ab, wie du die Seite öffnest.`);

  const debName = `co37-agent_${want}_all.deb`;
  const msiName = `co37-agent-${want}.msi`;

  // Abweichende Benennung melden statt stillschweigend zu beheben
  const warnBox = document.getElementById("pkgWarn");
  const problems = (data?.mismatch || []).slice();
  if (data?.older?.length)
    problems.push(`Ältere Dateien liegen noch im Ordner: ${data.older.join(", ")}. `
      + `Sie stören nicht — verwendet wird jeweils das zuletzt gebaute.`);
  warnBox.innerHTML = problems.length
    ? problems.map(t => `<div style="color:var(--led-pending)">${esc(t)}</div>`).join("")
    : "";
  warnBox.style.display = problems.length ? "flex" : "none";

  // ---- Linux ----
  const lin = data?.linux;
  document.getElementById("infoLinux").textContent = lin
    ? `${lin.name} · ${fmtSize(lin.size)} · ${fmtTime(lin.built_at)}`
      + (lin.matches ? "" : "  ⚠ Version weicht ab")
    : loadError ? "nicht abrufbar" : "noch nicht gebaut";
  const dlL = document.getElementById("dlLinux");
  dlL.disabled = !lin;
  dlL.textContent = lin ? "Herunterladen" : "nicht vorhanden";
  dlL.onclick = lin ? () => downloadPkg(lin.name) : null;

  // ---- Windows ----
  const win = data?.windows;
  document.getElementById("infoWin").textContent = win
    ? `${win.name} · ${fmtSize(win.size)} · ${fmtTime(win.built_at)}`
      + (win.matches ? "" : "  ⚠ Version weicht ab")
    : loadError ? "nicht abrufbar" : "noch nicht gebaut";
  const dlW = document.getElementById("dlWin");
  dlW.disabled = !win;
  dlW.textContent = win ? "Herunterladen" : "nicht vorhanden";
  dlW.onclick = win ? () => downloadPkg(win.name) : null;

  // Der Linux-Befehl braucht ein Token und wird erst auf Knopfdruck
  // gebaut. Namen des Pakets hier merken, damit das spaeter ohne erneuten
  // Abruf geht.
  LINUX_PKG = lin?.name || debName;
  LINUX_BASE = base;
  renderLinuxCmd();

  document.getElementById("cmdWin").textContent =
    `msiexec /i ${win?.name || msiName} /qn CO37SERVER="${base}"`;
}

/* ---------- Installations-Token ---------- */
// Der Befehl steht nur im Browser, nie in der Datenbank: dort liegt vom
// Token ausschliesslich der Hash.
let LINUX_PKG = "", LINUX_BASE = "", INSTALL_TOKEN = null, TOKEN_TIMER = null;
// Aus den Einstellungen; leer bedeutet "Adresse des Aufrufs verwenden".
let PUBLIC_URL = "";

function renderLinuxCmd(){
  const box = document.getElementById("cmdLinux");
  const copyBtn = document.getElementById("copyLinux");
  if (!INSTALL_TOKEN){
    box.textContent = "Noch kein Token erzeugt.";
    copyBtn.disabled = true;
    document.getElementById("tokenState").textContent = "";
    return;
  }
  // Mit && verkettet: schlaegt der Download fehl, laeuft apt gar nicht erst
  // an. Sonst kommt eine irrefuehrende Meldung ueber eine nicht
  // unterstuetzte Datei, obwohl in Wahrheit der Download scheiterte.
  box.textContent =
    `curl -fsSL -H "X-Install-Token: ${INSTALL_TOKEN.token}" ${LINUX_BASE}/api/v1/packages/${LINUX_PKG} -o /tmp/co37-agent.deb \\\n`
  + `  && CO37_SERVER="${LINUX_BASE}" apt-get install -y --allow-downgrades /tmp/co37-agent.deb`;
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
    st.textContent = "Token abgelaufen. Für die nächste Einrichtung neu erzeugen.";
    return;
  }
  const m = Math.floor(left / 60), sec = String(left % 60).padStart(2, "0");
  st.textContent = `Gültig noch ${m}:${sec} · bis zu ${INSTALL_TOKEN.max_uses} Abrufe`;
}

async function makeInstallToken(){
  try {
    INSTALL_TOKEN = await api("POST", "/api/v1/install-token", {});
  } catch(e){
    toast("Token konnte nicht erzeugt werden", true);
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
  idle:["",""], requested:["angefordert","run"], running:["wird gebaut","run"],
  success:["fertig","ok"], error:["fehlgeschlagen","err"]
};

async function loadBuildStatus(){
  let st;
  try { st = await api("GET", "/api/v1/packages/build-status"); } catch(e){ return; }
  const [txt, cls] = BUILD_TEXT[st.state] || [st.state, ""];
  const el = document.getElementById("pkgBuildState");
  el.textContent = txt; el.className = "state " + cls;

  const box = document.getElementById("pkgBuildLog");
  const busy = st.state === "requested" || st.state === "running";
  const done = st.state === "success" || st.state === "error";
  box.style.display = (busy || done) ? "block" : "none";
  if (busy || done) box.textContent = (st.log || []).join("\n");

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
  toast("Paketbau angefordert — läuft auf dem Server");
  loadBuildStatus();
};

/* ---------- Agent-Updates ---------- */
const RO_STATE = {
  idle:["Bereit",""], pilot:["Pilot läuft","run"], rest:["Wird ausgerollt","run"],
  done:["Abgeschlossen","ok"], failed:["Abgebrochen","err"]
};

async function loadRollout(){
  let r;
  try { r = await api("GET", "/api/v1/agent-rollout"); } catch(e){ return; }

  roAuto.checked = !!r.autoroll;
  roMode.value = r.mode || "staged";

  const sel = document.getElementById("roPilot");
  sel.innerHTML = '<option value="">— keiner gewählt —</option>'
    + (r.candidates || []).map(h =>
        `<option value="${h.id}" ${h.id === r.pilot_host_id ? "selected" : ""}>`
        + `${esc(h.hostname)}${h.agent_version ? " · " + esc(h.agent_version) : ""}</option>`).join("");

  document.getElementById("roPilotRow").style.display =
    roMode.value === "staged" ? "flex" : "none";

  document.getElementById("roHint").innerHTML = roMode.value === "staged"
    ? "Der Pilot wird zuerst aktualisiert. Erst wenn er sich mit der neuen Version zurückmeldet, folgen die übrigen. Wähl dafür ein unkritisches System. Meldet er sich 30 Minuten nicht oder schlägt fehl, wird nicht weiter ausgerollt."
    : "Alle Hosts werden gleichzeitig aktualisiert. Ein fehlerhafter Agent trifft damit die gesamte Flotte auf einmal.";

  const [txt, cls] = RO_STATE[r.state] || [r.state, ""];
  const st = document.getElementById("roState");
  st.textContent = r.stale_count ? `${txt} · ${r.stale_count} veraltet` : txt;
  st.className = "state " + cls;
  document.getElementById("roNote").textContent = r.note || "";
}

document.getElementById("roMode").onchange = () => {
  document.getElementById("roPilotRow").style.display =
    roMode.value === "staged" ? "flex" : "none";
  document.getElementById("roHint").innerHTML = roMode.value === "staged"
    ? "Der Pilot wird zuerst aktualisiert. Erst wenn er sich mit der neuen Version zurückmeldet, folgen die übrigen. Wähl dafür ein unkritisches System."
    : "Alle Hosts werden gleichzeitig aktualisiert. Ein fehlerhafter Agent trifft damit die gesamte Flotte auf einmal.";
};

document.getElementById("roSave").onclick = async () => {
  if (roAuto.checked && roMode.value === "staged" && !roPilot.value){
    if (!confirm("Kein Pilot-Host gewählt. Ohne Pilot werden alle Hosts gleichzeitig aktualisiert.\n\nTrotzdem speichern?")) return;
  }
  await api("POST", "/api/v1/agent-rollout", {
    autoroll: roAuto.checked,
    mode: roMode.value,
    pilot_host_id: roPilot.value ? parseInt(roPilot.value) : 0
  });
  await loadRollout();
  toast("Gespeichert");
};

document.getElementById("roStart").onclick = async () => {
  const r = await api("POST", "/api/v1/agent-rollout/start");
  await loadRollout();
  await load();
  toast(r.started
    ? (r.mode === "staged" ? `Pilot ${r.pilot} wird aktualisiert` : `${r.count} Agents werden aktualisiert`)
    : r.reason);
};

document.getElementById("btnMakeToken").onclick = makeInstallToken;
document.getElementById("copyLinux").onclick = () =>
  copy(document.getElementById("cmdLinux").textContent, "Linux-Befehl");
document.getElementById("copyWin").onclick = () =>
  copy(document.getElementById("cmdWin").textContent, "Windows-Befehl");

document.getElementById("agUpdateAll").onclick = async () => {
  const stale = HOSTS.filter(h => h.approval_state === "approved"
    && h.agent_version && AGENT_VER && h.agent_version !== AGENT_VER);
  if (!stale.length){ toast("Alle Agents sind aktuell"); return; }
  if (!confirm(`${stale.length} Agents auf ${AGENT_VER} aktualisieren?\n\nJeder Agent startet danach neu. Laufende Aufträge werden nicht unterbrochen.`)) return;
  for (const h of stale){ try { await api("POST", `/api/v1/hosts/${h.id}/jobs`, {job_type:"selfupdate", params:{}}); } catch(e){} }
  toast(`Aktualisierung für ${stale.length} Agents eingeplant`);
  afterAction();
};

/* ---------- Checkmk laden ---------- */
let CMK_STATUS = null;

async function loadCmk(){
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
    ? "hinterlegt — leer lassen, um es zu behalten"
    : "";

  const box = document.getElementById("cmkInfo");
  if (st.key_missing){
    box.innerHTML = '<span style="color:var(--led-reboot)">CO37_SECRET_KEY ist nicht gesetzt. Ohne den Schlüssel wird kein Secret gespeichert.</span>';
  } else if (st.ok === false){
    box.innerHTML = `<span style="color:var(--led-reboot)">Verbindung fehlgeschlagen: ${esc(st.error || "")}</span>`;
  } else if (st.configured){
    const v = (st.versions || {}).checkmk || "";
    box.innerHTML = `<span style="color:var(--led-ok)">Verbunden</span> · Checkmk ${esc(v)}`
      + (st.edition ? ` ${esc(st.edition)}` : "")
      + ` · Site ${esc(st.site || sd.site || "")}`
      + ` · ${CMK_HOSTS.length} Hosts abrufbar`;
  } else {
    box.innerHTML = '<span style="color:var(--muted-2)">Noch nicht verbunden.</span>';
  }
}

/* ---------- Systemupdate ---------- */
let UPD_TIMER = null;
const STATE_TEXT = {
  idle:["Bereit",""], uploaded:["Paket wartet","warn"], triggered:["Angefordert","run"],
  running:["Wird ausgeführt","run"], success:["Erfolgreich","ok"],
  rolled_back:["Zurückgerollt","warn"], error:["Fehler","err"]
};
async function loadWatcher(){
  let w;
  try { w = await api("GET", "/api/v1/watcher"); } catch(e){ return; }
  const el = document.getElementById("wVer");
  const hint = document.getElementById("wHint");
  el.textContent = w.version || "meldet sich nicht";
  el.style.color = w.ok && w.current ? "var(--led-ok)"
                 : w.ok ? "var(--led-pending)" : "var(--led-reboot)";
  if (w.hint){
    hint.style.display = "block";
    hint.innerHTML = `<span style="color:${w.ok ? "var(--led-pending)" : "var(--led-reboot)"}">${esc(w.hint)}</span>`
      + (!w.ok ? `<br><br>Einmalig auf dem Server:<br>
        <code>systemctl stop co37-watcher</code><br>
        <code>cd /opt/co37 &amp;&amp; unzip -o -j /tmp/co37_vX_Y_Z.zip update_watcher.py build_packages.sh build_release.sh setup.sh -d /opt/co37</code><br>
        <code>chmod +x /opt/co37/*.sh &amp;&amp; systemctl start co37-watcher</code>` : "");
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
    zeilen.push(`Lizenziert für <b>${esc(d.kunde)}</b>`
                + (d.nummer ? ` · Schlüssel Nr. ${d.nummer}` : ""));
    zeilen.push(d.unbefristet
      ? "Unbefristet"
      : `Gültig bis <b>${fmtTime(d.gueltig_bis, {year:"numeric",month:"2-digit",day:"2-digit"})}</b>`
        + (d.abgelaufen ? " — <b>abgelaufen</b>"
                        : ` (noch ${d.tage_uebrig} Tage)`));
  } else {
    zeilen.push("Kein Lizenzschlüssel eingetragen — freie Nutzung.");
  }

  zeilen.push(`<b style="color:${farbe}">${belegt} von `
              + `${grenze === null ? "unbegrenzt" : grenze}</b> Hosts freigegeben`);

  if (d.hinweis)
    zeilen.push(`<b style="color:var(--led-pending)">${esc(d.hinweis)}</b>`);
  if (eng)
    zeilen.push("Weitere Hosts lassen sich erst freigeben, wenn Platz frei "
                + "wird oder ein größerer Schlüssel eingetragen ist.");

  box.innerHTML = zeilen.join("<br>");
}

document.getElementById("lizSave").onclick = async () => {
  const k = document.getElementById("lizKey").value.trim();
  if (!k){ toast("Kein Schlüssel eingegeben", true); return; }
  try {
    await api("POST", "/api/v1/license", { key: k });
  } catch(e){
    // Der bisherige Zustand bleibt bestehen - das Backend weist einen
    // ungültigen Schlüssel ab, ohne ihn zu speichern.
    toast("Schlüssel abgelehnt — bisheriger Stand bleibt", true);
    return;
  }
  document.getElementById("lizKey").value = "";
  await loadLizenz();
  await load();
  toast("Lizenzschlüssel eingetragen");
};

document.getElementById("lizClear").onclick = async () => {
  if (!confirm("Lizenzschlüssel entfernen?\n\n"
    + "Danach gelten wieder 10 Hosts für neue Freigaben. Bereits "
    + "freigegebene Hosts bleiben unberührt und werden weiter gepatcht."))
    return;
  await api("POST", "/api/v1/license", { key: "" });
  await loadLizenz();
  toast("Lizenzschlüssel entfernt");
};

/* ---------- Zugang: Proxy und HTTPS-Zwang ---------- */
let PROXY_TIMER = null;

async function loadProxy(){
  let d;
  try { d = await api("GET", "/api/v1/proxy-settings"); }
  catch(e){ return; }

  document.getElementById("pxProxy").value = d.trusted_proxy || "";
  document.getElementById("pxPublic").value = d.public_url || "";
  document.getElementById("pxHttps").checked = !!d.https_only;
  PUBLIC_URL = d.public_url || "";

  document.getElementById("pxEffective").innerHTML =
    `Erzeugte Befehle tragen derzeit <b>${esc(d.effective_url || "?")}</b> ein.`
    + (d.public_url ? "" : " (aus dem aktuellen Aufruf abgeleitet)");

  // Womit die eigene Anfrage hereinkam. Ohne das raet man beim Eintragen
  // der Proxy-Adresse, statt es zu sehen.
  document.getElementById("pxNow").innerHTML =
    `Diese Sitzung kommt von <b>${esc(d.this_request_from || "?")}</b> und gilt als `
    + (d.this_request_https
        ? `<b style="color:var(--led-ok)">verschlüsselt</b>.`
        : `<b style="color:var(--led-reboot)">unverschlüsselt</b>. Rufst du über den `
          + `Proxy auf und steht hier trotzdem unverschlüsselt, ist entweder die `
          + `Adresse oben falsch oder der Proxy setzt <code>X-Forwarded-Proto</code> `
          + `nicht. Solange das so ist, würde der Haken unten diese Sitzung aussperren.`);

  // Bereitschaft der Agents - der Grund, warum es diese Anzeige gibt.
  const n = d.agents_secure, total = d.agents_total;
  const ready = total > 0 && n === total;
  document.getElementById("pxReady").innerHTML =
    `<b style="color:${ready ? "var(--led-ok)" : "var(--led-pending)"}">`
    + `${n} von ${total} Agents</b> melden sich verschlüsselt.`
    + (d.agents_insecure.length
        ? `<br>Noch unverschlüsselt: ${esc(d.agents_insecure.join(", "))}`
        : "");

  renderProxyState(d);
}

function renderProxyState(d){
  const box = document.getElementById("pxState");
  clearInterval(PROXY_TIMER); PROXY_TIMER = null;

  if (!d.https_only){ box.textContent = ""; return; }
  if (!d.confirm_deadline){
    box.innerHTML = `<b style="color:var(--led-ok)">Bestätigt.</b> Der Zugang `
      + `bleibt auf HTTPS beschränkt.`;
    return;
  }

  // Noch nicht bestätigt: bis zum Ablauf der Frist muss sich jemand über
  // HTTPS anmelden, sonst stellt sich der Zwang von selbst zurück.
  const tick = () => {
    const left = Math.round((new Date(d.confirm_deadline) - Date.now()) / 1000);
    if (left <= 0){
      clearInterval(PROXY_TIMER); PROXY_TIMER = null;
      box.textContent = "Frist abgelaufen — wird beim nächsten Zugriff zurückgestellt.";
      loadProxy();
      return;
    }
    const m = Math.floor(left / 60), sec = String(left % 60).padStart(2, "0");
    box.innerHTML = `<b style="color:var(--led-pending)">Noch nicht bestätigt.</b> `
      + `Innerhalb von <b>${m}:${sec}</b> über HTTPS neu anmelden, sonst wird der `
      + `Zwang automatisch zurückgenommen.`;
  };
  tick();
  PROXY_TIMER = setInterval(tick, 1000);
}

document.getElementById("pxSavePublic").onclick = async () => {
  const v = document.getElementById("pxPublic").value.trim();
  if (v && !/^https?:\/\//.test(v)){
    toast("Die Adresse muss mit http:// oder https:// beginnen", true); return;
  }
  await api("POST", "/api/v1/proxy-settings", { public_url: v });
  await loadProxy();
  toast("Gespeichert");
};

document.getElementById("pxSaveProxy").onclick = async () => {
  await api("POST", "/api/v1/proxy-settings",
            { trusted_proxy: document.getElementById("pxProxy").value.trim() });
  await loadProxy();
  toast("Gespeichert");
};

document.getElementById("pxHttps").onchange = async (e) => {
  const on = e.target.checked;
  if (on){
    const d = await api("GET", "/api/v1/proxy-settings");
    const offen = d.agents_insecure.length;
    const warn = offen
      ? `\n\nAchtung: ${offen} Agent(s) sprechen noch unverschlüsselt und `
        + `fallen sofort aus:\n${d.agents_insecure.join(", ")}`
      : "";
    const self = d.this_request_https ? "" :
      `\n\nAchtung: diese Sitzung gilt als unverschlüsselt und wird `
      + `ausgesperrt. Prüfe zuerst den Eintrag oben.`;
    if (!confirm(`Zugang auf HTTPS beschränken?${self}${warn}\n\n`
      + `Meldet sich innerhalb von ${d.confirm_minutes} Minuten niemand über `
      + `HTTPS an, wird die Einschränkung automatisch zurückgenommen.`)){
      e.target.checked = false; return;
    }
  }
  await api("POST", "/api/v1/proxy-settings", { https_only: on });
  await loadProxy();
  toast(on ? "Eingeschränkt — jetzt über HTTPS neu anmelden" : "Einschränkung aufgehoben");
};

async function loadUpdate(){
  loadWatcher();
  let st;
  try { st = await api("GET", "/api/v1/update/status"); } catch(e){ return; }
  document.getElementById("uCur").textContent = st.current_version || "—";
  const [text, cls] = STATE_TEXT[st.state] || [st.state, ""];
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
    document.getElementById("upLog").textContent = (st.log||[]).join("\n") || "…";
    document.getElementById("uAck").style.display = done ? "inline-block" : "none";
  }
  if (busy && !UPD_TIMER) UPD_TIMER = setInterval(loadUpdate, 3000);
  if (!busy && UPD_TIMER){ clearInterval(UPD_TIMER); UPD_TIMER = null; }
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

  if (!zip){ toast("Bitte die ZIP-Datei des Pakets wählen", true); return; }

  const fd = new FormData();
  fd.append("file", zip);
  if (sig) fd.append("sigfile", sig);
  toast(sig ? "Paket und Signatur werden geprüft…" : "Paket wird geprüft…");
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
  await loadUpdate(); toast("Paket geprüft und bereitgestellt");
}
const drop = document.getElementById("uDrop"), fileInput = document.getElementById("uFile");
drop.onclick = () => fileInput.click();
fileInput.onchange = e => uploadZip(e.target.files);
["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => uploadZip(e.dataTransfer.files));

document.getElementById("uRun").onclick = async () => {
  if (!confirm(`Update von ${uCur.textContent} auf ${uNew.textContent} ausführen?\n\nDer Dienst wird kurz neu gestartet.`)) return;
  await api("POST", "/api/v1/update/trigger"); await loadUpdate();
};
document.getElementById("uCancel").onclick = async () => {
  await api("POST", "/api/v1/update/cancel"); await loadUpdate(); toast("Paket verworfen");
};
document.getElementById("uAck").onclick = async () => {
  await api("POST", "/api/v1/update/acknowledge"); await loadUpdate();
};

/* ---------- Start ---------- */
document.getElementById("btnReload").onclick = () => load().then(() => toast("Aktualisiert"));
document.getElementById("btnScanAll").onclick = scanAll;
document.getElementById("filter").oninput = render;
document.getElementById("fState").onchange = render;

/* ---------- Konto, Benutzer, Protokoll ---------- */

function loadAccount(){
  document.getElementById("acName").textContent = ME ? ME.username : "—";
  document.getElementById("acRole").textContent =
    ME ? (ME.is_admin ? "Administrator" : "Benutzer") : "—";
}

document.getElementById("acSave").onclick = async () => {
  const oldPw = document.getElementById("acOld").value;
  const nw = document.getElementById("acNew").value;
  const nw2 = document.getElementById("acNew2").value;
  if (nw !== nw2){ toast("Die beiden neuen Passwörter stimmen nicht überein", true); return; }
  if (nw.length < 6){ toast("Passwort muss mindestens 6 Zeichen haben", true); return; }
  try { await api("POST", "/api/v1/me/password", {old_password: oldPw, new_password: nw}); }
  catch(e){ return; }
  // Der Server verwirft dabei alle Sitzungen - auch die eigene.
  setSession(false);
  document.getElementById("dlgSettings").close();
  showLogin("Passwort geändert. Bitte neu anmelden.");
};

async function loadUsers(){
  let users;
  try { users = await api("GET", "/api/v1/users"); } catch(e){ return; }
  const me = ME ? ME.username : "";
  document.getElementById("userList").innerHTML = users.map(u => `
    <div class="vrow" style="margin-bottom:5px">
      <span>${esc(u.username)}${u.username === me ? " (Sie)" : ""}<br>
        <span style="color:var(--muted-2)">${u.role === "admin" ? "Administrator" : "Benutzer"}
        · ${u.last_login ? "zuletzt " + fmtTime(u.last_login) : "noch nie angemeldet"}</span></span>
      <span style="display:flex;gap:8px">
        <button data-act="resetpw" data-id="${u.id}" data-username="${esc(u.username)}">Passwort setzen</button>
        <button class="danger" data-act="deluser" data-id="${u.id}" data-username="${esc(u.username)}"
          ${u.username === me ? "disabled" : ""}>Entfernen</button>
      </span></div>`).join("");
}

async function resetPw(id, name){
  const pw = prompt(`Neues Passwort für ${name}:`);
  if (!pw) return;
  if (pw.length < 6){ toast("Passwort muss mindestens 6 Zeichen haben", true); return; }
  try { await api("POST", `/api/v1/users/${id}/password`, {new_password: pw}); }
  catch(e){ return; }
  toast(`Passwort für ${name} gesetzt, laufende Sitzungen beendet`);
  loadUsers(); loadAudit();
}

async function delUser(id, name){
  if (!confirm(`Zugang ${name} entfernen?`)) return;
  try { await api("DELETE", `/api/v1/users/${id}`); } catch(e){ return; }
  toast(`${name} entfernt`);
  loadUsers(); loadAudit();
}

document.getElementById("nuAdd").onclick = async () => {
  const username = document.getElementById("nuName").value.trim();
  const password = document.getElementById("nuPass").value;
  const role = document.getElementById("nuRole").value;
  if (!username || !password){ toast("Name und Passwort ausfüllen", true); return; }
  try { await api("POST", "/api/v1/users", {username, password, role}); }
  catch(e){ return; }
  document.getElementById("nuName").value = "";
  document.getElementById("nuPass").value = "";
  toast(`${username} angelegt`);
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
    : '<span style="color:var(--muted-2)">noch keine Einträge</span>';
}

/* ---------- Start ---------- */
document.getElementById("loginGo").onclick = doLogin;
document.getElementById("loginPass").addEventListener("keydown", e => {
  if (e.key === "Enter") doLogin();
});
document.getElementById("btnLogout").onclick = doLogout;

async function startApp(){
  try { ME = await api("GET", "/api/v1/me"); } catch(e){ return; }
  document.getElementById("whoami").textContent =
    ME.username + (ME.is_admin ? " · Administrator" : "");
  // Reiter, die nur Administratoren sehen sollen
  document.querySelectorAll(".admin-only").forEach(el => {
    el.style.display = ME.is_admin ? "" : "none";
  });

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
async function spracheAnwenden(benutzer){
  let vorgabe = null;
  try {
    const h = await (await fetch(API + "/api/health", {cache: "no-store"})).json();
    vorgabe = h.default_language || null;
  } catch(e){}
  // Beim Ermitteln nicht ins Cookie schreiben: sonst wuerde die Vorgabe der
  // Installation beim ersten Besuch als eigene Wahl festgeschrieben und
  // eine spaetere Aenderung der Vorgabe erreichte niemanden mehr.
  setzeSprache(ermittleSprache({ benutzer, vorgabe }), { merken: false });
}

async function spracheWaehlen(code){
  if (!setzeSprache(code)) return;          // schreibt das Cookie
  try {
    await api("POST", "/api/v1/me/language", { language: code });
    if (ME) ME.language = code;
    toast(t("language.own.saved"));
  } catch(e){
    // Die Oberflaeche steht bereits um. Dass der Server sie sich nicht
    // merken konnte, hat api() schon gemeldet.
  }
}

async function vorgabespracheWaehlen(code){
  try {
    await api("POST", "/api/v1/settings/language", { language: code });
    toast(t("language.default.saved"));
  } catch(e){}
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
  document.getElementById("spMeine").value = SPRACHE;
  document.getElementById("appShell").style.display = "";
  await startApp();
})();
