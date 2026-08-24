/**
 * CO-37 - Sprachen der Oberflaeche.
 *
 * Zwei Dinge gehen bei Uebersetzungen still schief, und beide fallen ohne
 * Pruefung erst dem Kunden auf:
 *
 * Ein Schluessel fehlt in einer Sprache. Dann steht dort entweder nichts
 * oder der Schluessel selbst - und wer die andere Sprache benutzt, merkt
 * es nie.
 *
 * Die uebersetzten Beschriftungen sind laenger als die englischen. Die
 * Reiter des Einstellungsdialogs passen dann auf Englisch in eine Zeile
 * und auf Deutsch nicht. Geprueft wird hier deshalb fuer beide Sprachen.
 *
 * Laeuft ohne Backend: i18n.js wird eigenstaendig geladen, mit einer
 * winzigen DOM-Attrappe nur fuer das, was die Datei anfasst.
 *
 * Aufruf:
 *
 *     node tests/i18n-test.js frontend/index.html
 */
const fs = require("fs");
const path = require("path");

const HTML_PATH = process.argv[2] || "frontend/index.html";
const html = fs.readFileSync(HTML_PATH, "utf8");
const quelle = fs.readFileSync(
  path.join(path.dirname(HTML_PATH), "i18n.js"), "utf8");

let fails = 0;
const check = (label, ok, extra = "") => {
  if (!ok) fails++;
  console.log(`${ok ? "ok    " : "FEHLER"} ${label}${extra ? "  -> " + extra : ""}`);
};

// ------------------------------------------------------------- DOM-Attrappe
function mkEl(datensatz) {
  return { dataset: datensatz, textContent: "", placeholder: "", title: "" };
}
const ELEMENTE = [];
global.document = {
  documentElement: {},
  cookie: "",
  querySelectorAll(sel) {
    const feld = sel === "[data-i18n]" ? "i18n"
               : sel === "[data-i18n-placeholder]" ? "i18nPlaceholder"
               : sel === "[data-i18n-title]" ? "i18nTitle" : null;
    return feld ? ELEMENTE.filter(e => e.dataset[feld] !== undefined) : [];
  },
};
global.location = { protocol: "http:" };

const i18n = new Function(quelle + `
  return { I18N, t, setzeSprache, uebersetzeDom, ermittleSprache,
           spracheAusCookie, pruefeSprache, SPRACHEN, SPRACHE_VORGABE,
           aktuelle: () => SPRACHE };`)();

// ------------------------------------------------------- Vollstaendigkeit
console.log("=== Woerterbuch vollstaendig ===");
{
  const sprachen = i18n.SPRACHEN;
  const alle = new Set();
  for (const s of sprachen) Object.keys(i18n.I18N[s]).forEach(k => alle.add(k));

  for (const s of sprachen) {
    const fehlt = [...alle].filter(k => i18n.I18N[s][k] === undefined);
    check(`keine fehlenden Texte in '${s}'`, fehlt.length === 0, fehlt.join(", "));
  }
  for (const s of sprachen) {
    const leer = Object.keys(i18n.I18N[s]).filter(k => !String(i18n.I18N[s][k]).trim());
    check(`keine leeren Texte in '${s}'`, leer.length === 0, leer.join(", "));
  }
  check("Englisch ist die Vorgabe", i18n.SPRACHE_VORGABE === "en",
        i18n.SPRACHE_VORGABE);
}

// ------------------------------------------------------ Schluessel im Markup
console.log("\n=== Schluessel im Markup ===");
{
  // Jeder im Markup benutzte Schluessel muss im Woerterbuch stehen. Sonst
  // erscheint die Beschriftung als 'settings.tab.foo' - sichtbar kaputt,
  // aber eben erst im Betrieb.
  const benutzt = [...html.matchAll(/data-i18n(?:-placeholder|-title)?="([^"]+)"/g)]
                  .map(m => m[1]);
  const unbekannt = [...new Set(benutzt)]
                    .filter(k => i18n.I18N.en[k] === undefined);
  check("jeder Schluessel im Markup ist uebersetzt",
        unbekannt.length === 0, unbekannt.join(", "));
  check("das Markup benutzt ueberhaupt Schluessel", benutzt.length > 0,
        benutzt.length);
}

// ---------------------------------------------------------------- t()
console.log("\n=== Nachschlagen ===");
{
  i18n.setzeSprache("de");
  check("deutscher Text kommt auf Deutsch",
        i18n.t("settings.tab.audit") === "Protokoll", i18n.t("settings.tab.audit"));
  check("unbekannter Schluessel liefert den Schluessel",
        i18n.t("gibt.es.nicht") === "gibt.es.nicht", i18n.t("gibt.es.nicht"));
  i18n.setzeSprache("en");
  check("englischer Text kommt auf Englisch",
        i18n.t("settings.tab.audit") === "Log", i18n.t("settings.tab.audit"));
  check("unbekannte Sprache wird abgelehnt", i18n.setzeSprache("kl") === false);
  check("und die alte bleibt stehen", i18n.aktuelle() === "en", i18n.aktuelle());
}

// ------------------------------------------------------------ Platzhalter
console.log("\n=== Platzhalter ===");
{
  i18n.I18N.en["test.platz"] = "a {eins} b {zwei} c {eins}";
  i18n.setzeSprache("en");
  check("alle Vorkommen werden ersetzt",
        i18n.t("test.platz", { eins: "X", zwei: "Y" }) === "a X b Y c X",
        i18n.t("test.platz", { eins: "X", zwei: "Y" }));
  i18n.I18N.en["test.regex"] = "vor {a.b} nach";
  check("Sonderzeichen im Namen treffen nicht das Falsche",
        i18n.t("test.regex", { "a.b": "Z" }) === "vor Z nach",
        i18n.t("test.regex", { "a.b": "Z" }));
  delete i18n.I18N.en["test.platz"];
  delete i18n.I18N.en["test.regex"];
}

// ---------------------------------------------------------------- Cookie
console.log("\n=== Cookie ===");
{
  check("Sprache wird aus dem Cookie gelesen",
        i18n.spracheAusCookie("a=1; co37_lang=de; b=2") === "de");
  check("aehnlich benanntes Cookie trifft nicht",
        i18n.spracheAusCookie("x_co37_lang=de") === null);
  check("unbekannte Kennung im Cookie wird verworfen",
        i18n.spracheAusCookie("co37_lang=kl") === null);
  check("'de-DE' gilt nicht als 'de'", i18n.pruefeSprache("de-DE") === null);
  check("leeres Cookie ergibt null", i18n.spracheAusCookie("") === null);
}

// ------------------------------------------------------------ Reihenfolge
console.log("\n=== Reihenfolge beim Ermitteln ===");
{
  // Das Cookie steht in der Rangfolge zwischen Benutzer und Vorgabe. Die
  // Umschaltungen weiter oben haben es gesetzt - ohne Leeren pruefte man
  // hier nur, welchen Wert der letzte Testabschnitt hinterlassen hat.
  const vorher = global.document.cookie;
  global.document.cookie = "";

  check("Benutzer schlaegt Vorgabe",
        i18n.ermittleSprache({ benutzer: "de", vorgabe: "en" }) === "de");
  check("ohne Benutzer gilt die Vorgabe",
        i18n.ermittleSprache({ benutzer: null, vorgabe: "de" }) === "de");
  check("ohne alles gilt Englisch",
        i18n.ermittleSprache({}) === "en");
  check("unbekannte Wahl des Benutzers faellt durch",
        i18n.ermittleSprache({ benutzer: "kl", vorgabe: "de" }) === "de");

  // Wer bewusst umgeschaltet hat, soll das auf der Anmeldeseite
  // wiederfinden - auch wenn die Installation anders vorgibt.
  global.document.cookie = "co37_lang=de";
  check("Cookie schlaegt die Vorgabe der Installation",
        i18n.ermittleSprache({ benutzer: null, vorgabe: "en" }) === "de");
  check("aber nicht die Wahl des Benutzers",
        i18n.ermittleSprache({ benutzer: "en", vorgabe: "en" }) === "en");

  global.document.cookie = vorher;
}

// --------------------------------------------------------- Markup fuellen
console.log("\n=== Markup wird gefuellt ===");
{
  ELEMENTE.length = 0;
  const text = mkEl({ i18n: "settings.tab.audit" });
  const platz = mkEl({ i18nPlaceholder: "settings.tab.users" });
  const titel = mkEl({ i18nTitle: "settings.tab.license" });
  ELEMENTE.push(text, platz, titel);

  i18n.setzeSprache("de");
  check("Beschriftung wird gesetzt", text.textContent === "Protokoll",
        text.textContent);
  check("Platzhalter wird gesetzt", platz.placeholder === "Benutzer",
        platz.placeholder);
  check("Titel wird gesetzt", titel.title === "Lizenz", titel.title);

  i18n.setzeSprache("en");
  check("Umschalten zeichnet neu", text.textContent === "Log", text.textContent);
  check("lang am Dokument wird mitgefuehrt",
        global.document.documentElement.lang === "en",
        global.document.documentElement.lang);
  ELEMENTE.length = 0;
}

// -------------------------------------------------- Reiter in beiden Sprachen
console.log("\n=== Reiter passen in beiden Sprachen ===");
{
  // Dieselbe Ueberschlagsrechnung wie im Frontend-Test, aber einmal je
  // Sprache. Auf Englisch steht der Text im Markup, auf Deutsch kommt er
  // aus dem Woerterbuch - genau dort faellt eine zu lange Uebersetzung
  // sonst durch jede Pruefung.
  const breite = html.match(/dialog#dlgSettings\{width:min\((\d+)px/);
  check("Einstellungsdialog hat eine eigene Breite", !!breite,
        breite && breite[1] + "px");

  const reiter = [...html.matchAll(/data-tab="\w+"[^>]*>([^<]+)</g)].map(m => m[1]);
  const schluessel = [...html.matchAll(/data-tab="\w+"[^>]*data-i18n="([^"]+)"/g)]
                     .map(m => m[1]);
  check("jeder Reiter hat einen Schluessel",
        schluessel.length === reiter.length,
        `${schluessel.length} von ${reiter.length}`);

  const rechne = (labels) => Math.round(
    labels.join("").length * 6.8 + labels.length * 28
    + (labels.length - 1) * 2 + 36);

  for (const sprache of i18n.SPRACHEN) {
    i18n.setzeSprache(sprache);
    const labels = schluessel.map(k => i18n.t(k));
    const b = rechne(labels);
    check(`Reiter passen auf '${sprache}' in eine Zeile`,
          !!breite && b < Number(breite[1]),
          `${b}px bei ${breite && breite[1]}px`);
  }
}

// ------------------------------------------- Eingabefelder ueberleben
console.log("\n=== Umschalten laesst Kindelemente unangetastet ===");
{
  // Das Markup sieht an vielen Stellen so aus:
  //     <label><input type="checkbox" id="hAuto"> Neustart ausfuehren</label>
  // Ein schlichtes textContent haette das Kaestchen geloescht - und zwar
  // erst beim Umschalten der Sprache, also lange nach jeder Sichtpruefung.
  // Schlimmer noch: ein neu gebautes Kaestchen haette seinen Haken verloren.
  const kasten = { nodeType: 1, checked: true, id: "hAuto" };
  const text = { nodeType: 3, textContent: " Neustart eigenständig ausführen" };
  const label = {
    dataset: { i18n: "host.auto_reboot" },
    childNodes: [kasten, text],
    set textContent(v) { this._ersetzt = v; this.childNodes = []; },
    get textContent() { return this._ersetzt; },
  };
  ELEMENTE.length = 0;
  ELEMENTE.push(label);

  i18n.setzeSprache("en");
  check("Text wurde ersetzt",
        text.textContent === "Restart on its own when required", text.textContent);
  check("das Kaestchen ist noch da", label.childNodes.includes(kasten));
  check("und hat seinen Haken behalten", kasten.checked === true);
  ELEMENTE.length = 0;
}

// ------------------------------------------------- Nichts bleibt uebersetzt
console.log("\n=== Kein sichtbarer Text ohne Schluessel ===");
{
  // Die eigentliche Absicherung dieser Umstellung. Ohne sie bliebe ein
  // vergessener Text still deutsch - und faellt erst einem Kunden auf, der
  // die Oberflaeche auf Englisch benutzt. Gilt auch fuer jede kuenftige
  // Aenderung am Markup: wer etwas hinzufuegt, ohne es zu verschluesseln,
  // bricht hier ab.
  const maske = html.replace(/<style[\s\S]*?<\/style>/g, m => " ".repeat(m.length))
                    .replace(/<script[\s\S]*?<\/script>/g, m => " ".repeat(m.length))
                    .replace(/<!--[\s\S]*?-->/g, m => " ".repeat(m.length));
  const tags = "div|label|button|option|span|h1|h2|h3|td|th|p|summary|legend";
  const re = new RegExp("<(" + tags + ")\\b([^>]*)>((?:(?!<\\/?(?:" + tags
                        + ")\\b)[\\s\\S])*?)<\\/\\1>", "g");
  // Sprachneutral: Zahlen, Zeichen, Eigennamen, Dateinamen. Bewusst als
  // vollstaendiger Vergleich verankert und nicht als Teiltreffer - sonst
  // rutschte jeder Satz durch, in dem 'Agent' vorkommt.
  const neutral = /^(|[\s—–.,:;·-]*|CO-?37|Checkmk|Agent|Agents|TLS|HTTPS|MSI|SHA-256|JSON|\d+[\d\s.,:%]*|…)$/i;

  // Nicht nach uebersetzbaren Elementen suchen, sondern die bereits
  // verschluesselten samt Inhalt wegstreichen und ansehen, was uebrig
  // bleibt. Die erste Fassung dieser Pruefung suchte mit demselben
  // Ausdruck wie das Werkzeug, das die Texte herausgeholt hat - und hatte
  // damit denselben blinden Fleck: ein Element, das andere Elemente
  // enthaelt, uebersahen beide. Zwoelf Texte und acht Platzhalter blieben
  // deutsch, und die Pruefung meldete alles in Ordnung.
  // data-i18n-skip heisst: bleibt in jeder Sprache gleich, mit Absicht.
  // Firmierung etwa. Ausdruecklich im Markup und nicht als Ausnahmeliste
  // hier - so steht die Entscheidung dort, wo sie jemand sieht.
  let rest = maske, vorher, runden = 0;
  do {
    vorher = rest;
    // Der Wert ist bewusst freigestellt: data-i18n-skip steht ohne '=' im
    // Markup, und ein Ausdruck, der eines verlangt, uebersieht es.
    rest = rest.replace(
      /<(\w+)[^>]*\bdata-i18n(?:-html|-skip|-placeholder|-title)?(?:\s*=\s*"[^"]*")?[^>]*>[\s\S]*?<\/\1>/g, " ")
      .replace(/<(\w+)[^>]*\bdata-i18n[^>]*\/?>/g, " ");
  } while (rest !== vorher && ++runden < 40);

  const offen = [...rest.matchAll(/>([^<>]+)</g)]
    .map(m => m[1].replace(/\s+/g, " ").trim())
    .filter(t => t && !neutral.test(t));
  check("jeder sichtbare Text hat einen Schluessel", offen.length === 0,
        [...new Set(offen)].slice(0, 6).join(" | "));

  // Platzhalter und Titel waren beim ersten Anlauf gar nicht erfasst -
  // sie stehen in Attributen, und dort hat niemand hingesehen.
  const attrNeutral = /^(\d+|cmk|automation|CO37-…|https?:\/\/\S+)$/i;
  const attrOffen = [...html.matchAll(/<[^>]*?(placeholder|title)="([^"]+)"[^>]*>/g)]
    .filter(m => !attrNeutral.test(m[2]) && !/data-i18n-(placeholder|title)/.test(m[0]))
    .map(m => `${m[1]}="${m[2].slice(0, 40)}"`);
  check("jeder Platzhalter und Titel hat einen Schluessel",
        attrOffen.length === 0, attrOffen.slice(0, 4).join(" | "));

  const benutzt = [...html.matchAll(/data-i18n(?:-html|-placeholder|-title)?="([^"]+)"/g)]
                  .map(m => m[1]);
  check("es sind tatsaechlich viele", benutzt.length > 180, benutzt.length);
}

// --------------------------------------------- Reiter und Bereiche passen
console.log("\n=== Jeder Reiter hat seinen Bereich ===");
{
  // Der Reiter 'Sprache' war da, sein Bereich auch - aber app.js pflegte
  // die Liste der Bereiche daneben und kannte ihn nicht. Ergebnis: ein
  // leerer Dialog. Diese Pruefung haelt Markup und Code zusammen.
  const reiter = [...html.matchAll(/data-tab="(\w+)"/g)].map(m => m[1]);
  const fehlend = reiter.filter(id => !new RegExp(`id="${id}"`).test(html));
  check("zu jedem Reiter gibt es einen Bereich im Markup",
        fehlend.length === 0, fehlend.join(", "));
  check("es gibt ueberhaupt Reiter", reiter.length >= 9, reiter.length);

  // Und die Liste in app.js darf nicht fest verdrahtet sein - sonst
  // laeuft sie beim naechsten Reiter wieder auseinander.
  const appjs = require("fs").readFileSync(
    require("path").join(require("path").dirname(HTML_PATH), "app.js"), "utf8");
  check("app.js leitet die Bereiche aus dem Markup ab",
        /const STABS = \[\.\.\.document\.querySelectorAll/.test(appjs));
}

// ------------------------------------------------- Nur harmlose Auszeichnung
console.log("\n=== data-i18n-html enthaelt nur Auszeichnung ===");
{
  // data-i18n-html setzt innerHTML. Der Inhalt stammt aus dem eigenen
  // Woerterbuch, nicht von Benutzern - trotzdem festgenagelt, damit dort
  // nie etwas Ausfuehrbares hineinwaechst. Die Regel script-src ohne
  // 'unsafe-inline' waere sonst an dieser Stelle unterlaufen.
  const erlaubt = /^(b|\/b|code|\/code|br|i|\/i|strong|\/strong)$/i;
  const schluessel = [...html.matchAll(/data-i18n-html="([^"]+)"/g)].map(m => m[1]);
  const boese = [];
  for (const s of schluessel) {
    for (const sprache of i18n.SPRACHEN) {
      const wert = String(i18n.I18N[sprache][s] || "");
      for (const tag of wert.matchAll(/<\s*([^\s>\/]+|\/[^\s>]+)/g)) {
        if (!erlaubt.test(tag[1])) boese.push(`${sprache}/${s}: <${tag[1]}`);
      }
      if (/on\w+\s*=/i.test(wert)) boese.push(`${sprache}/${s}: Ereignis-Attribut`);
    }
  }
  check("keine unerlaubten Elemente in Uebersetzungen", boese.length === 0,
        boese.slice(0, 4).join(" | "));
  check("es gibt ueberhaupt welche mit Auszeichnung", schluessel.length > 0,
        schluessel.length);
}

// ------------------------------------- data-i18n-html verschluckt Nachbarn
console.log("\n=== data-i18n-html umschliesst keine benannten Elemente ===");
{
  // Der Anlass, aus 0.36.3: die Zeilen der Agent-Pakete standen auf
  // data-i18n-html, und in genau diesen Spannen lagen die Elemente
  // #infoLinux und #infoWin. data-i18n-html setzt innerHTML - beim
  // Sprachaufbau waren die beiden damit weg, und app.js scheiterte
  // danach an document.getElementById(...).textContent = ... mit
  // "Cannot set properties of null". Erst beim Oeffnen der
  // Einstellungen sichtbar, deshalb von 201 Frontend-Pruefungen nicht
  // erwischt.
  //
  // Das ist der Unterschied zu data-i18n: das ersetzt nur den ersten
  // Textknoten und laesst Kindelemente stehen. Wer Auszeichnung
  // braucht, gibt ihr eine eigene Spanne und legt das benannte Element
  // daneben, nicht hinein.
  const treffer = [];
  const re = /<(\w+)([^>]*\bdata-i18n-html="([^"]+)"[^>]*)>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const tag = m[1];
    const rest = html.slice(m.index + m[0].length);
    // Ende des Elements suchen, verschachtelte gleichnamige Tags mitzaehlen.
    const teile = rest.matchAll(new RegExp(`<(/?)${tag}\\b`, "g"));
    let tiefe = 1, ende = rest.length;
    for (const teil of teile) {
      tiefe += teil[1] === "/" ? -1 : 1;
      if (tiefe === 0) { ende = teil.index; break; }
    }
    const inhalt = rest.slice(0, ende);
    for (const id of inhalt.matchAll(/\bid="([^"]+)"/g)) {
      treffer.push(`${m[3]} umschliesst #${id[1]}`);
    }
  }
  check("kein data-i18n-html umschliesst ein Element mit id",
        treffer.length === 0, treffer.join(" | "));
}

// -------------------------------- Watcher schreibt Schluessel, keine Saetze
console.log("\n=== Watcher und Paketbau liefern Schluessel ===");
{
  // Der Anlass: das Update-Protokoll in der Oberflaeche stand auf
  // Deutsch, auch wenn die Oberflaeche auf Englisch lief. Der Watcher
  // schrieb fertige deutsche Saetze in status.json, und app.js gab sie
  // unveraendert aus. Backend und Agent halten sich seit 0.35.x an
  // "Schluessel, nie Saetze" - der Watcher war die letzte Ausnahme.
  const fsx = require("fs");
  const pfad = require("path").join(
    require("path").dirname(HTML_PATH), "..", "update_watcher.py");
  let watcher = "";
  try { watcher = fsx.readFileSync(pfad, "utf8"); } catch (e) { /* s.u. */ }
  check("update_watcher.py ist lesbar", watcher.length > 0, pfad);

  // Jedes Argument von append_log() und jeder eintrag()-Aufruf muss ein
  // Schluessel sein: nur Kleinbuchstaben, Ziffern, Punkt, Unterstrich.
  // Ein Satz enthaelt Leerzeichen und faellt damit auf.
  const schluesselform = /^[a-z][a-z0-9_.]*$/;
  const schlecht = [];
  const aufrufe = [
    ...watcher.matchAll(/append_log\(\s*status\s*,\s*"([^"]*)"/g),
    ...watcher.matchAll(/(?<!def )\beintrag\(\s*"([^"]*)"/g),
  ];
  for (const a of aufrufe) {
    if (!schluesselform.test(a[1])) schlecht.push(a[1].slice(0, 40));
  }
  check("append_log/eintrag bekommen nur Schluessel, keine Saetze",
        schlecht.length === 0, schlecht.join(" | "));
  check("es wurden ueberhaupt Aufrufe gefunden", aufrufe.length > 10,
        aufrufe.length);

  // Und jeder dieser Schluessel muss im Woerterbuch stehen, sonst zeigt
  // die Oberflaeche den Schluessel selbst an.
  const unbekannt = [...new Set(aufrufe.map(a => a[1]))]
                    .filter(k => schluesselform.test(k))
                    .filter(k => i18n.I18N.en[k] === undefined);
  check("jeder Schluessel des Watchers ist uebersetzt",
        unbekannt.length === 0, unbekannt.join(", "));

  // Das Backend setzt beim Anfordern eine erste Zeile - ebenfalls als
  // Schluessel.
  const um = fsx.readFileSync(require("path").join(
    require("path").dirname(HTML_PATH), "..", "backend", "update_manager.py"),
    "utf8");
  const backendZeile = um.match(/"k":\s*"([^"]+)"/);
  check("das Backend setzt einen Schluessel statt eines Satzes",
        backendZeile !== null, backendZeile ? backendZeile[1] : "keiner");
  if (backendZeile) {
    check("und der Schluessel ist uebersetzt",
          i18n.I18N.en[backendZeile[1]] !== undefined, backendZeile[1]);
  }

  // Das Frontend darf das Protokoll nicht mehr rohgejoint ausgeben.
  const appjs2 = fsx.readFileSync(require("path").join(
    require("path").dirname(HTML_PATH), "app.js"), "utf8");
  check("app.js setzt das Protokoll ueber protokoll() zusammen",
        /function protokoll\(/.test(appjs2)
        && (appjs2.match(/protokoll\(st\.log\)/g) || []).length === 2,
        (appjs2.match(/protokoll\(st\.log\)/g) || []).length);
  check("kein rohes join des Protokolls mehr",
        !/\(st\.log\s*\|\|\s*\[\]\)\.join/.test(appjs2));
}

console.log(`\nFehler: ${fails}`);
process.exit(fails ? 1 : 0);
