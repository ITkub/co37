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

  const offen = [];
  for (const m of maske.matchAll(re)) {
    const inner = m[3].trim();
    const nurText = inner.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    if (!nurText || neutral.test(nurText)) continue;
    if (/data-i18n/.test(m[2])) continue;
    // Ein Element, dessen Text vollstaendig in verschluesselten Kindern
    // steckt, ist selbst in Ordnung.
    if (/data-i18n/.test(inner)
        && !inner.replace(/<[^>]*data-i18n[^>]*>[\s\S]*?<\/\w+>/g, " ")
                 .replace(/<[^>]*>/g, " ").trim()) continue;
    offen.push(`${m[1]}: ${nurText.slice(0, 55)}`);
  }
  check("jeder sichtbare Text hat einen Schluessel", offen.length === 0,
        offen.slice(0, 5).join(" | "));

  const benutzt = [...html.matchAll(/data-i18n(?:-html)?="([^"]+)"/g)].map(m => m[1]);
  check("es sind tatsaechlich viele", benutzt.length > 150, benutzt.length);
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

console.log(`\nFehler: ${fails}`);
process.exit(fails ? 1 : 0);
