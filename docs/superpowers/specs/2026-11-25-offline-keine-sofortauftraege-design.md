# Keine Sofort-Auftraege fuer Hosts, die offline sind

## Absicht
Ein von Hand angelegter Scan- oder Patch-Auftrag ohne Termin soll nicht
entstehen, wenn der Host offline ist. Bisher blieb er bis zu 7 Tage auf
"pending" und lief nachtraeglich, sobald der Host wiederkam.

## Ziele
- `create_job` lehnt Scan und Patch ohne `scheduled_at` bei Offline-Host ab (HTTP 409).
- Eine einzige Definition von "online" fuer Statusbericht und Auftragsanlage.
- Sammelaktionen in der Oberflaeche melden uebersprungene Hosts, statt still zu schlucken.

## Nicht-Ziele
- Eingeplante Auftraege (`scheduled_at` gesetzt) bleiben erlaubt; ihr Verfall
  ueber `expires_at` / `JOB_MAX_WAIT` bleibt unveraendert.
- Neustart-Auftraege bleiben wie bisher (eigene kurze Frist).
- `JOB_MAX_WAIT` und `auftraege_aufraeumen` werden nicht angefasst.
- Der Schwellwert `AGENT_OFFLINE_AFTER` (180 s) bleibt.

## Anti-Ziel
Ein Host, der nur kurz haengt, darf nicht zu einer stillen Fehlbedienung
fuehren: die Ablehnung nennt die letzte Meldung, und die Sammelaktion
nennt die Anzahl der uebersprungenen Hosts.

## Ansatz
Die lokale Funktion `_online` aus dem Statusbericht wird zu einer
Modulfunktion `host_ist_online(host, now)`. `create_job` ruft sie fuer
Scan/Patch ohne Termin auf. Verworfen: Pruefung nur im Frontend (umgehbar,
API bliebe offen); Anheben des Schwellwerts (wuerde die Anzeige aendern).

## Oberflaeche
Einzelaktionen zeigen die Serverfehlermeldung. Sammelaktionen zaehlen
abgelehnte Hosts und melden "N uebersprungen, weil offline" (DE/EN in `i18n.js`).

## Dem Bauenden ueberlassen
Genaue Textfassung der Meldungen, Stelle der Zaehlung in den drei
Sammelaktionen.
