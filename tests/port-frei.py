"""
Prueft, ob ein Port frei ist - indem versucht wird, ihn selbst zu belegen.

Zuverlaessiger als 'ss' oder 'netstat': die sind nicht ueberall
installiert, und ein fehlendes Werkzeug hiesse sonst stillschweigend
"Port ist frei". Die Tests liefen dann gegen irgendetwas anderes und
meldeten irrefuehrende Fehler.

Rueckgabe 0, wenn frei.
"""
import socket
import sys

s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
