# Schriften

Beide unter der SIL Open Font License 1.1 — Weitergabe ausdrücklich
erlaubt, auch eingebettet in andere Software. Die Lizenztexte liegen
daneben.

| Datei | Herkunft |
|---|---|
| `archivo-*.woff2` | [google/fonts](https://github.com/google/fonts/tree/main/ofl/archivo), variable Schrift `Archivo[wdth,wght].ttf` |
| `jetbrainsmono-*.woff2` | [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono), Release 2.304 |

## Erzeugt mit

Archivo liegt nur als variable Schrift vor und wurde bei `wdth=100` auf
die drei benötigten Gewichte festgelegt. Alle sechs Dateien sind
anschließend auf den lateinischen Zeichensatz gekürzt — dazu die Zeichen
`→` und `⚠`, die die Oberfläche verwendet.

Ohne diese beiden griffe der Browser für sie auf eine Ersatzschrift
zurück, sichtbar an einem Zeichen, das aus der Zeile fällt.

Gesamtgröße rund 115 KB statt mehrerer hundert.

## Warum mitgeliefert

Vorher kamen die Schriften von `fonts.googleapis.com`. Das hatte zwei
Nachteile: die Oberfläche sah ohne Internetzugang anders aus — auf einem
abgeschotteten Wartungsnetz also immer — und die Regel
`Content-Security-Policy` brauchte Ausnahmen für zwei fremde Adressen.
Beides entfällt.
