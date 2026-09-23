# CO-37 - Checkmk-Local-Check fuer den AGENT-Host (Windows).
#
# Liegt auf dem ueberwachten Host im local-Ordner des Checkmk-Agents
# (z. B. C:\ProgramData\checkmk\agent\local\) und prueft direkt, ob der
# CO-37-Agent laeuft - unabhaengig vom CO-37-Server.
#
# Der Agent ist bewusst KEIN Windows-Dienst, sondern eine geplante
# Aufgabe (CO37Agent), die beim Systemstart als SYSTEM einen dauerhaft
# laufenden Python-Prozess startet. "Laeuft" heisst deshalb: der Prozess
# ist da. Die geplante Aufgabe wird zusaetzlich als Kontext geprueft.

$ErrorActionPreference = 'SilentlyContinue'
$name = 'CO-37 Agent'

# 1) Laeuft der Agent-Prozess? (python mit agent.py in der Befehlszeile)
$proc = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*agent.py*' }

# 2) Existiert die geplante Aufgabe und in welchem Zustand?
$task = Get-ScheduledTask -TaskName 'CO37Agent'

if ($proc) {
    Write-Host ('0 "{0}" - laeuft (Prozess aktiv)' -f $name)
}
elseif ($null -eq $task) {
    Write-Host ('2 "{0}" - laeuft NICHT (geplante Aufgabe CO37Agent fehlt)' -f $name)
}
elseif ($task.State -eq 'Running') {
    Write-Host ('0 "{0}" - geplante Aufgabe laeuft' -f $name)
}
else {
    Write-Host ('2 "{0}" - laeuft NICHT (Aufgabe im Zustand {1}, kein Prozess)' -f $name, $task.State)
}
