param([int]$MonitorIntervalMinutes = 5, [int]$BackupHour = 3)
$ErrorActionPreference = 'Stop'
if ($MonitorIntervalMinutes -lt 1 -or $MonitorIntervalMinutes -gt 60 -or $BackupHour -lt 0 -or $BackupHour -gt 23) { throw 'Invalid schedule' }
$powershellPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$backupPath = Join-Path $PSScriptRoot 'offhost-backup.ps1'
$monitorPath = Join-Path $PSScriptRoot 'monitor.ps1'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$backupAction = New-ScheduledTaskAction -Execute $powershellPath -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$backupPath`" -FullBackup"
$backupTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours($BackupHour))
Register-ScheduledTask -TaskName 'JopesVPN-OffhostBackup' -Action $backupAction -Trigger $backupTrigger -Settings $settings -Force | Out-Null
$monitorAction = New-ScheduledTaskAction -Execute $powershellPath -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$monitorPath`""
$monitorTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $MonitorIntervalMinutes)
Register-ScheduledTask -TaskName 'JopesVPN-Monitor' -Action $monitorAction -Trigger $monitorTrigger -Settings $settings -Force | Out-Null
Write-Output 'Daily full recovery backup and external monitoring tasks installed'
