$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$EnvFile = Join-Path $Root ".env"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$TaskName = "CEREPATH Guardian"

if (-not (Test-Path $EnvFile -PathType Leaf)) {
    throw "Guardian .env not found. Copy .env.example to .env and configure CEREPATH_COMPOSE_DIR first."
}

if (Test-Path $VenvPython -PathType Leaf) {
    $Python = $VenvPython
} else {
    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw "Python 3.11+ was not found. Create the Guardian .venv first." }
    $Python = $PythonCommand.Source
}

$Arguments = '-m guardian --env-file "' + $EnvFile + '" run'
$Action = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $Root
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$TaskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $TaskSettings -Description "Runs the local CEREPATH Guardian supervisor after user logon." -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and requested start for '$TaskName'. It will also start automatically at each sign-in."
Write-Host "Task status: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Guardian logs: $(Join-Path $Root 'data\guardian.log')"
