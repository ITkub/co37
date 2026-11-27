# Neustart-Dialog: Downtime-Haken nur bei Checkmk-Verknuepfung vorbelegt

## Absicht
Im Neustart-Dialog eines Hosts ist "Zuerst eine Checkmk-Downtime setzen"
immer vorbelegt, auch wenn der Host mit keinem Checkmk-Host verknuepft ist.
Der Hinweis darunter sagt dann, dass sich keine Downtime setzen laesst - der
Haken widerspricht ihm.

## Ziel
Der Haken ist nur gesetzt, wenn es ein Ziel gibt: ein oder mehrere
verknuepfte Checkmk-Hosts, oder "alle Checkmk-Hosts" (`hasDt(h)`, dieselbe
Regel wie fuer den Hinweis und wie `has_downtime_target` im Backend).
Ohne Ziel ist er aus und bleibt von Hand setzbar.

## Nicht-Ziele
Die Bereichsaktion "Neustart fuer alle Hosts eines Bereichs" bleibt
unveraendert: sie nutzt die Verknuepfung des Bereichs, nicht die des
einzelnen Hosts. Keine Aenderung am Server.

## Pruefung
`tests/frontend-test.js`: Hosts ohne Verknuepfung, mit einem, mit mehreren,
mit "alle" und mit fehlendem Feld; Wechsel zwischen Hosts darf den Zustand
nicht mitschleppen.
