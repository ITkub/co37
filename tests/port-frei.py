"""
Prueft, ob ein Port frei ist - indem versucht wird, ihn selbst zu belegen.

Zuverlaessiger als 'ss' oder 'netstat': die sind nicht ueberall
installiert, und ein fehlendes Werkzeug hiesse sonst stillschweigend
"Port ist frei". Die Tests liefen dann gegen irgendetwas anderes und
meldeten irrefuehrende Fehler.

WARUM SO_REUSEADDR HIER NUR UNTER POSIX GESETZT WIRD

Die beiden Betriebssysteme meinen mit dieser Option nicht dasselbe.

  POSIX   erlaubt das Binden, waehrend ein alter Socket noch in
          TIME_WAIT haengt. Ohne sie meldete ein gerade beendeter
          Testlauf den Port faelschlich als belegt.
  Windows erlaubt das Binden, OBWOHL ein anderer Prozess dort schon
          lauscht. Der Port sah damit immer frei aus.

Nachgemessen am 2026-09-07 auf LENOVO: die Reihe 'harness' belegte
einen Port und liess run-tests.sh darauf los - das Backend startete
anstandslos. Der Schutz, der verhindern soll, dass die Tests gegen einen
fremden Dienst laufen, war unter Windows also gar keiner.

Unter Windows ist SO_EXCLUSIVEADDRUSE das Gegenstueck: es besteht auf
Alleinbesitz und scheitert genau dann, wenn dort schon jemand lauscht.

Rueckgabe 0, wenn frei.
"""
import socket
import sys

s = socket.socket()
if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):        # nur Windows
    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
else:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
