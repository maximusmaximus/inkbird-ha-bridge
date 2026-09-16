# Install Windows Scheduled Task to run inkbird-ha-bridge 24/7 on logon
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pythonw) {
        $pythonw = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    }
}

$taskName = "InkbirdHABridge"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$scriptDir\bridge.py`"" -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

# Register or update task
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Successfully registered Windows Scheduled Task '$taskName' to run at logon with $pythonw"

# Start task now
Start-ScheduledTask -TaskName $taskName
Write-Host "Started task '$taskName' in background"
