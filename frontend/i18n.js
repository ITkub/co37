/*
 * CO-37 - Sprachen.
 *
 * Nur die Oberflaeche uebersetzt. Backend und Agent liefern Schluessel und
 * Werte, niemals fertige Saetze - sonst braeuchte es drei Woerterbuecher an
 * drei Orten und drei Stellen, an denen ein Text fehlen kann.
 *
 * Englisch ist die Vorgabe und zugleich der Rueckfall: fehlt ein Schluessel
 * in der eingestellten Sprache, wird der englische genommen; fehlt er auch
 * dort, erscheint der Schluessel selbst. Sichtbar falsch ist besser als
 * unsichtbar leer - eine leere Beschriftung faellt niemandem auf.
 *
 * Im Markup steht der englische Text ausgeschrieben, nicht nur der
 * Schluessel. Zwei Gruende: die Seite zeigt schon vor dem ersten Skriptlauf
 * etwas Lesbares, und die Pruefung, ob die Reiter in eine Zeile passen,
 * kann weiterhin am Markup rechnen.
 *
 * Wird vor app.js geladen und stellt seine Namen global bereit - die
 * Oberflaeche kommt ohne Module aus, und script-src bleibt bei 'self'.
 */

const SPRACHEN = ["en", "de"];
const SPRACHE_VORGABE = "en";
const SPRACH_COOKIE = "co37_lang";

/* Reihenfolge im Woerterbuch: Schluessel alphabetisch, damit Luecken beim
   Lesen auffallen. Die Pruefreihe vergleicht beide Sprachen ohnehin. */
const I18N = {
  en: {
    "settings.tab.access": "Access",
    "settings.tab.account": "Account",
    "settings.tab.agents": "Agents",
    "settings.tab.audit": "Log",
    "settings.tab.checkmk": "Checkmk",
    "settings.tab.language": "Language",
    "settings.tab.license": "License",
    "settings.tab.update": "Update",
    "settings.tab.users": "Users",

    "language.hint": "The language applies to this interface only. Messages "
      + "from older agents keep the wording they were recorded with.",
    "language.own": "My language",
    "language.own.saved": "Language saved.",
    "language.default": "Default for this installation",
    "language.default.hint": "Applies to users who have not chosen a language "
      + "themselves, and to the sign-in page.",
    "language.default.saved": "Default language saved.",
    "language.name.de": "Deutsch",
    "language.name.en": "English",
  },
  de: {
    "settings.tab.access": "Zugang",
    "settings.tab.account": "Konto",
    "settings.tab.agents": "Agents",
    "settings.tab.audit": "Protokoll",
    "settings.tab.checkmk": "Checkmk",
    "settings.tab.language": "Sprache",
    "settings.tab.license": "Lizenz",
    "settings.tab.update": "Update",
    "settings.tab.users": "Benutzer",

    "language.hint": "Die Sprache gilt nur für diese Oberfläche. Meldungen "
      + "älterer Agenten behalten den Wortlaut, mit dem sie aufgezeichnet wurden.",
    "language.own": "Meine Sprache",
    "language.own.saved": "Sprache gespeichert.",
    "language.default": "Vorgabe für diese Installation",
    "language.default.hint": "Gilt für Benutzer, die selbst keine Sprache "
      + "gewählt haben, und für die Anmeldeseite.",
    "language.default.saved": "Vorgabesprache gespeichert.",
    "language.name.de": "Deutsch",
    "language.name.en": "English",
  },
};

let SPRACHE = SPRACHE_VORGABE;

/**
 * Uebersetzt einen Schluessel.
 *
 * werte fuellt Platzhalter der Form {name}. Ersetzt wird mit split/join
 * statt mit einem regulaeren Ausdruck: der Name koennte Sonderzeichen
 * enthalten, und eine unmaskierte Ersetzung traefe dann das Falsche.
 */
function t(schluessel, werte) {
  let text = (I18N[SPRACHE] || {})[schluessel];
  if (text === undefined) text = I18N[SPRACHE_VORGABE][schluessel];
  if (text === undefined) return schluessel;
  if (werte) {
    for (const name of Object.keys(werte)) {
      text = text.split("{" + name + "}").join(String(werte[name]));
    }
  }
  return text;
}

/** Gueltige Sprache oder null. Kein Rateschluss auf Teilzeichenketten. */
function pruefeSprache(code) {
  return SPRACHEN.includes(code) ? code : null;
}

/**
 * Liest die Sprache aus dem Cookie.
 *
 * roh ist nur fuer die Pruefreihe da: dort gibt es keinen echten
 * Cookie-Speicher, und ohne diesen Weg liesse sich das Auslesen nicht
 * pruefen.
 */
function spracheAusCookie(roh) {
  const text = roh === undefined ? document.cookie : roh;
  for (const teil of String(text || "").split(";")) {
    const [name, ...rest] = teil.trim().split("=");
    // Genauer Vergleich: ein Cookie 'x_co37_lang' darf nicht treffen.
    if (name === SPRACH_COOKIE) return pruefeSprache(rest.join("="));
  }
  return null;
}

function merkeSpracheImCookie(code) {
  if (!pruefeSprache(code)) return;
  const sicher = location.protocol === "https:" ? "; Secure" : "";
  // Ein Jahr. Kein HttpOnly - die Oberflaeche muss den Wert selbst lesen,
  // und eine Spracheinstellung ist kein Geheimnis.
  document.cookie = `${SPRACH_COOKIE}=${code}; path=/; max-age=31536000`
                  + `; SameSite=Lax${sicher}`;
}

/**
 * Setzt die Sprache und zeichnet die Oberflaeche neu.
 *
 * Gibt zurueck, ob etwas Gueltiges gesetzt wurde - der Aufrufer soll nicht
 * raten muessen, ob eine unbekannte Kennung stillschweigend verworfen wurde.
 */
function setzeSprache(code, { merken = true } = {}) {
  const gueltig = pruefeSprache(code);
  if (!gueltig) return false;
  SPRACHE = gueltig;
  if (merken) merkeSpracheImCookie(gueltig);
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.lang = gueltig;
  }
  uebersetzeDom();
  return true;
}

/**
 * Traegt alle Uebersetzungen ins Markup ein.
 *
 * Laeuft ueber das ganze Dokument, nicht nur ueber den sichtbaren Teil:
 * Dialoge sind geschlossen, ihr Inhalt existiert aber schon, und beim
 * Oeffnen soll nichts nachflackern.
 */
function uebersetzeDom(wurzel) {
  const w = wurzel || (typeof document !== "undefined" ? document : null);
  if (!w || !w.querySelectorAll) return;
  for (const el of w.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  for (const el of w.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  }
  for (const el of w.querySelectorAll("[data-i18n-title]")) {
    el.title = t(el.dataset.i18nTitle);
  }
}

/**
 * Ermittelt die Sprache in der vereinbarten Reihenfolge:
 * Einstellung des Benutzers, Vorgabe der Installation, Englisch.
 *
 * Das Cookie steht vor der Vorgabe der Installation, aber hinter dem
 * Benutzer: wer bewusst umgeschaltet hat, soll das auf der Anmeldeseite
 * wiederfinden, ohne dass es eine Wahl am Konto ueberschreibt.
 */
function ermittleSprache({ benutzer = null, vorgabe = null } = {}) {
  return pruefeSprache(benutzer)
      || spracheAusCookie()
      || pruefeSprache(vorgabe)
      || SPRACHE_VORGABE;
}
