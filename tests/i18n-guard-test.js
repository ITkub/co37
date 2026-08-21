/**
 * CO-37 - Absicherung: toast() und confirm() bekommen keinen festen Text mehr.
 *
 * Direkter Nachfolger der Uebersetzung von app.js (0.35.4). Ein toast("...")
 * oder confirm("...") mit getipptem Satz faellt sonst durch jede andere
 * Pruefung - das Woerterbuch selbst bleibt vollstaendig, nur die Oberflaeche
 * zeigt an dieser Stelle wieder Deutsch, egal welche Sprache eingestellt ist.
 *
 * Ein Muster-Abgleich reicht nicht: der Text steckt oft nicht direkt im
 * Aufruf, sondern in einer Variablen, die vorher mit += zusammengesetzt
 * wurde (siehe scheduleReboot in app.js - msg waechst dort ueber vier
 * Zeilen). Deshalb ein echter Parser (acorn, vendored in tests/vendor/,
 * kein npm-Install noetig) statt Regex: jeder toast()/confirm()-Aufruf wird
 * bis zu seinen Argumenten verfolgt, und jeder Bezeichner darin bis zu
 * seinen Zuweisungen in der umschliessenden Funktion. t(...)-Aufrufe werden
 * dabei komplett uebersprungen - ihr Inhalt sind Schluessel und
 * Platzhalternamen, kein Menschentext.
 *
 * Ein Fund zaehlt nur, wenn die Zeichenkette einen Buchstaben enthaelt.
 * Trennzeichen und Zeilenumbrueche wie "\n\n" oder " - " sind kein
 * uebersetzungspflichtiger Text und wuerden sonst jeden Lauf verfaelschen.
 *
 * Grenzen, bewusst nicht geschlossen: Text, der ueber ein Funktions-
 * Argument, einen Rueckgabewert oder eine andere Datei hereinkommt, sieht
 * dieser Test nicht - nur Literale im Aufruf selbst und in Zuweisungen
 * innerhalb derselben Funktion. Das waere eine vollstaendige
 * Datenflussanalyse, keine mehr, die sich in einer Testreihe rechtfertigt.
 *
 * Aufruf:
 *
 *     node tests/i18n-guard-test.js frontend/app.js
 */
const fs = require("fs");
const path = require("path");
const acorn = require(path.join(__dirname, "vendor", "acorn.js"));

const JS_PATH = process.argv[2] || "frontend/app.js";
const source = fs.readFileSync(JS_PATH, "utf8");

let fails = 0;
const check = (label, ok, extra = "") => {
  if (!ok) fails++;
  console.log(`${ok ? "ok    " : "FEHLER"} ${label}${extra ? "  -> " + extra : ""}`);
};

let ast;
try {
  ast = acorn.parse(source, { ecmaVersion: 2022, sourceType: "script", locations: true });
} catch (e) {
  check("app.js laesst sich parsen", false, e.message);
  console.log(`\nFehler: ${fails}`);
  process.exit(1);
}
check("app.js laesst sich parsen", true);

// ------------------------------------------------------------ Baum-Helfer
function eachChild(node, fn) {
  for (const key in node) {
    const val = node[key];
    if (Array.isArray(val)) {
      for (const item of val) {
        if (item && typeof item.type === "string") fn(item);
      }
    } else if (val && typeof val.type === "string") {
      fn(val);
    }
  }
}
function walkAll(node, visit) {
  if (!node || typeof node !== "object") return;
  visit(node);
  eachChild(node, (child) => walkAll(child, visit));
}

// -------------------------------------------------- toast()/confirm()-Rufe
const ZIELE = new Set(["toast", "confirm"]);
const rufe = [];
(function sammeln(node, funkStack) {
  if (!node || typeof node !== "object") return;
  const istFunktion = node.type === "FunctionDeclaration"
    || node.type === "FunctionExpression"
    || node.type === "ArrowFunctionExpression";
  const naechsterStack = istFunktion ? funkStack.concat([node]) : funkStack;

  if (node.type === "CallExpression" && node.callee.type === "Identifier"
      && ZIELE.has(node.callee.name)) {
    rufe.push({
      name: node.callee.name,
      zeile: node.loc.start.line,
      args: node.arguments,
      bezirk: naechsterStack[naechsterStack.length - 1] || ast,
    });
  }
  eachChild(node, (child) => sammeln(child, naechsterStack));
})(ast, []);

check("toast()/confirm()-Aufrufe gefunden", rufe.length > 0, rufe.length);

// -------------------------------------------------- Zuweisungen im Bezirk
function findeZuweisungen(bezirk, name) {
  const treffer = [];
  walkAll(bezirk, (n) => {
    if (n.type === "VariableDeclarator" && n.id && n.id.type === "Identifier"
        && n.id.name === name && n.init) {
      treffer.push(n.init);
    }
    if (n.type === "AssignmentExpression" && n.left && n.left.type === "Identifier"
        && n.left.name === name && (n.operator === "=" || n.operator === "+=")) {
      treffer.push(n.right);
    }
  });
  return treffer;
}

const BUCHSTABE = /[A-Za-zÀ-ÖØ-öø-ÿ]/;

function pruefeText(node, bezirk, funde, besucht) {
  if (!node || typeof node !== "object") return;

  switch (node.type) {
    case "CallExpression":
      // Nur ein einfacher benannter Aufruf (mkMsg("...")) wird verfolgt.
      // Ein Methodenaufruf wie document.execCommand("copy") hat einen
      // MemberExpression als Ziel - dessen Argumente sind API-Namen, kein
      // Menschentext, und werden bewusst nicht angesehen.
      if (node.callee.type === "Identifier") {
        if (node.callee.name === "t") return;
        for (const a of node.arguments) pruefeText(a, bezirk, funde, besucht);
      }
      return;
    case "Literal":
      if (typeof node.value === "string" && BUCHSTABE.test(node.value)) {
        funde.push({ zeile: node.loc.start.line, text: node.value });
      }
      return;
    case "TemplateLiteral":
      for (const q of node.quasis) {
        const txt = q.value.cooked || "";
        if (BUCHSTABE.test(txt)) funde.push({ zeile: node.loc.start.line, text: txt });
      }
      for (const e of node.expressions) pruefeText(e, bezirk, funde, besucht);
      return;
    case "ConditionalExpression":
      pruefeText(node.test, bezirk, funde, besucht);
      pruefeText(node.consequent, bezirk, funde, besucht);
      pruefeText(node.alternate, bezirk, funde, besucht);
      return;
    case "LogicalExpression":
      pruefeText(node.left, bezirk, funde, besucht);
      pruefeText(node.right, bezirk, funde, besucht);
      return;
    case "BinaryExpression":
      // Nur "+" baut Text zusammen. Vergleiche wie r.mode === "staged"
      // pruefen einen Statuswert, zeigen ihn nicht an.
      if (node.operator === "+") {
        pruefeText(node.left, bezirk, funde, besucht);
        pruefeText(node.right, bezirk, funde, besucht);
      }
      return;
    case "UnaryExpression":
      pruefeText(node.argument, bezirk, funde, besucht);
      return;
    case "AwaitExpression":
      pruefeText(node.argument, bezirk, funde, besucht);
      return;
    case "ArrayExpression":
      for (const e of node.elements) if (e) pruefeText(e, bezirk, funde, besucht);
      return;
    case "SequenceExpression":
      for (const e of node.expressions) pruefeText(e, bezirk, funde, besucht);
      return;
    case "ObjectExpression":
      for (const p of node.properties) {
        if (p.type === "Property" && p.value) pruefeText(p.value, bezirk, funde, besucht);
      }
      return;
    case "Identifier": {
      const name = node.name;
      if (besucht.has(name)) return;
      besucht.add(name);
      for (const init of findeZuweisungen(bezirk, name)) {
        pruefeText(init, bezirk, funde, besucht);
      }
      return;
    }
    default:
      return;
  }
}

for (const ruf of rufe) {
  const funde = [];
  for (const arg of ruf.args) {
    pruefeText(arg, ruf.bezirk, funde, new Set());
  }
  const beispiel = funde.slice(0, 2)
    .map((f) => `Zeile ${f.zeile}: "${f.text.length > 40 ? f.text.slice(0, 40) + "…" : f.text}"`)
    .join(" | ");
  check(`${ruf.name}() ohne festen Text (Zeile ${ruf.zeile})`, funde.length === 0, beispiel);
}

console.log(`\nFehler: ${fails}`);
process.exit(fails ? 1 : 0);
