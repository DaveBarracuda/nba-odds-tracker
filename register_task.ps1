# Registra una tarea de Windows que ejecuta el tracker cada hora en punto.
# Uso:   powershell -ExecutionPolicy Bypass -File register_task.ps1
# Quitar: Unregister-ScheduledTask -TaskName "NBA Odds Tracker" -Confirm:$false
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"   # sin ventana de consola
if (Test-Path $pythonw) { $python = $pythonw }

$action = New-ScheduledTaskAction -Execute $python -Argument "-m tracker run" -WorkingDirectory $root
$nextHour = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
$trigger = New-ScheduledTaskTrigger -Once -At $nextHour -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "NBA Odds Tracker" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Snapshot horario de odds NBA y alertas de movimiento" -Force | Out-Null
Write-Host "Tarea 'NBA Odds Tracker' registrada. Primera corrida: $nextHour. Log: $root\data\tracker.log"
