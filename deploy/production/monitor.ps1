param(
    [switch]$Setup,
    [switch]$EnableTelegramAlerts,
    [string]$SshHost = "jopesvpn",
    [int]$MinAvailableMemoryMb = 500,
    [int]$MaxSwapPercent = 75,
    [int]$FailureThreshold = 2
)

$ErrorActionPreference = 'Stop'
$monitorDirectory = Join-Path $env:USERPROFILE 'JopesVPN-Monitor'
$configPath = Join-Path $monitorDirectory 'config.dpapi'
$statePath = Join-Path $monitorDirectory 'state.json'
$logPath = Join-Path $monitorDirectory 'monitor.log'
New-Item -ItemType Directory -Path $monitorDirectory -Force | Out-Null
$account = "$env:USERDOMAIN\$env:USERNAME"
& icacls.exe $monitorDirectory /inheritance:r /grant:r "$account`:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect the monitoring directory' }
if ($SshHost -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$' -or $MinAvailableMemoryMb -lt 1 -or $MaxSwapPercent -lt 1 -or $MaxSwapPercent -gt 100 -or $FailureThreshold -lt 1 -or $FailureThreshold -gt 20) {
    throw 'Invalid monitoring options'
}

function Start-HiddenProcess([string]$executable, [string]$arguments) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $executable
    $info.Arguments = $arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'Could not start monitoring helper' }
    return $process
}

if ($Setup) {
    $remoteCode = @'
import json,subprocess
def env(container):
 return dict(item.split('=',1) for item in json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Env}}',container])))
bot=env('jopesvpn-production-bot'); proxy=env('jopesvpn-production-proxy')
config={'endpoints':{'Bot':{'url':'https://'+proxy['CONNECT_DOMAIN']+'/healthz','statuses':[200]},'Panel':{'url':bot['PANEL_API_URL']+'/health','statuses':[200]},'Support':{'url':'https://'+proxy['CONNECT_DOMAIN']+'/support/admin/posts','statuses':[401]}},'containers':['jopesvpn-production-bot','jopesvpn-production-panel','jopesvpn-production-node','jopesvpn-production-db','jopesvpn-production-valkey','jopesvpn-production-proxy','jopesvpn-production-subpage','jopesvpn-production-ticket-forum']}
if __TELEGRAM__: config['telegram']={'token':bot['BOT_TOKEN'],'admin_ids':[int(value) for value in bot['BOT_ADMIN_IDS'].split(',') if value.strip()]}
print(json.dumps(config))
'@
    $remoteCode = $remoteCode.Replace('__TELEGRAM__', $(if ($EnableTelegramAlerts) { 'True' } else { 'False' }))
    $ssh = Start-HiddenProcess (Get-Command ssh.exe).Source "-o BatchMode=yes -o ConnectTimeout=10 $SshHost `"python3 -`""
    $outputTask = $ssh.StandardOutput.ReadToEndAsync()
    $errorTask = $ssh.StandardError.ReadToEndAsync()
    $ssh.StandardInput.Write($remoteCode)
    $ssh.StandardInput.Close()
    $ssh.WaitForExit()
    $output = $outputTask.GetAwaiter().GetResult()
    $null = $errorTask.GetAwaiter().GetResult()
    if ($ssh.ExitCode -ne 0) { throw 'Monitoring bootstrap failed' }
    $config = $output | ConvertFrom-Json
    $config | Add-Member -NotePropertyName ssh_host -NotePropertyValue $SshHost
    $config | Add-Member -NotePropertyName min_available_mb -NotePropertyValue $MinAvailableMemoryMb
    $config | Add-Member -NotePropertyName max_swap_percent -NotePropertyValue $MaxSwapPercent
    $config | Add-Member -NotePropertyName failure_threshold -NotePropertyValue $FailureThreshold
    $configJson = $config | ConvertTo-Json -Depth 8 -Compress
    $encrypted = ConvertFrom-SecureString (ConvertTo-SecureString $configJson -AsPlainText -Force)
    Set-Content -LiteralPath $configPath -Value $encrypted -Encoding ASCII
    Write-Output "Monitoring configured; Telegram alerts enabled: $([bool]$EnableTelegramAlerts)"
    return
}

if (-not (Test-Path -LiteralPath $configPath)) { throw 'Run monitor.ps1 -Setup first' }
$secure = ConvertTo-SecureString (Get-Content -LiteralPath $configPath -Raw).Trim()
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $configJson = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
$mutex = New-Object Threading.Mutex($false, 'Local\JopesVPN-Monitor')
if (-not $mutex.WaitOne(0)) { $mutex.Dispose(); exit 0 }
try {
    $pythonPath = (Get-Command python.exe).Source
    $scriptPath = Join-Path $PSScriptRoot 'monitor.py'
    $process = Start-HiddenProcess $pythonPath "`"$scriptPath`" `"$statePath`""
    $outputTask = $process.StandardOutput.ReadToEndAsync()
    $errorTask = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write($configJson)
    $process.StandardInput.Close()
    $process.WaitForExit()
    $output = $outputTask.GetAwaiter().GetResult().Trim()
    $null = $errorTask.GetAwaiter().GetResult()
    if ($output) {
        if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 10MB) {
            Move-Item -LiteralPath $logPath -Destination "$logPath.previous" -Force
        }
        Add-Content -LiteralPath $logPath -Value $output
        Write-Output $output
    }
    else { Add-Content -LiteralPath $logPath -Value "$((Get-Date).ToUniversalTime().ToString('o')) Monitor helper failed" }
    $result = $process.ExitCode
}
finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
exit $result
