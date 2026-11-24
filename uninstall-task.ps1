$ErrorActionPreference = "Stop"
$TaskName = "CEREPATH Guardian"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'. Guardian data and reports were left untouched."
} else {
    Write-Host "Task '$TaskName' is not installed."
}
