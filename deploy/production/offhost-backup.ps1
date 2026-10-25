param(
    [string]$RestoreFile,
    [string]$RestoreTo,
    [switch]$FullBackup,
    [switch]$VerifyPostgresRestore,
    [string]$SshHost = "jopesvpn",
    [string]$RemoteRoot = "/opt/jopesvpn",
    [string]$OpenSslPath,
    [int]$RetentionDays = 90
)

$ErrorActionPreference = "Stop"

$backupDirectory = Join-Path $env:USERPROFILE "JopesVPN-Backups"
$keyPath = Join-Path $backupDirectory "backup-key.dpapi"
$logPath = Join-Path $backupDirectory "backup.log"
$runId = [Guid]::NewGuid().ToString("N")
$isBundle = $FullBackup -or ($RestoreFile -and $RestoreFile.EndsWith('.tar.gz.enc'))
$extension = if ($isBundle) { "tar.gz" } else { "sqlite3" }
$partialPath = Join-Path $backupDirectory "$runId.$extension.enc.partial"
$restorePath = Join-Path $backupDirectory "$runId.$extension.verify"
$keyTempPath = Join-Path $backupDirectory "$runId.key"
$prefix = if ($isBundle) { "recovery" } else { "bot-state" }
$finalPath = Join-Path $backupDirectory "$prefix-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')).$extension.enc"
$macPartialPath = "$partialPath.hmac"
if (-not $OpenSslPath) {
    $opensslCommand = Get-Command openssl.exe -ErrorAction SilentlyContinue
    $OpenSslPath = if ($opensslCommand) { $opensslCommand.Source } else { Join-Path $env:ProgramFiles 'Git\usr\bin\openssl.exe' }
}
if (-not (Test-Path -LiteralPath $OpenSslPath)) { throw "OpenSSL was not found; supply -OpenSslPath" }
$opensslPath = $OpenSslPath
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$authScriptPath = Join-Path $PSScriptRoot "backup-auth.py"
$exportScriptPath = Join-Path $PSScriptRoot "backup-export.py"
$verifyScriptPath = Join-Path $PSScriptRoot "backup-verify.py"
if ($RetentionDays -lt 1 -or $RetentionDays -gt 3650 -or $SshHost -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$' -or $RemoteRoot -notmatch '^/[A-Za-z0-9/_-]+$') {
    throw "Invalid backup options"
}

function Test-Restore([string]$path) {
    if ($isBundle) {
        $verification = & $pythonPath $verifyScriptPath $path
        if ($LASTEXITCODE -ne 0) { throw "Recovery bundle verification failed" }
        return
    }
    $verification = & $pythonPath -c "import pathlib,sqlite3,sys; c=sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True); result=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); print(result); sys.exit(result != 'ok')" $path
    if ($LASTEXITCODE -ne 0 -or $verification -ne "ok") { throw "Restored SQLite integrity check failed" }
}

function Write-BackupLog([string]$message) {
    Add-Content -LiteralPath $logPath -Value "$((Get-Date).ToUniversalTime().ToString('o')) $message"
}

function Get-BackupSecret {
    if (-not (Test-Path -LiteralPath $keyPath)) {
        $bytes = New-Object byte[] 48
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        $generator.GetBytes($bytes)
        $generator.Dispose()
        $secret = [Convert]::ToBase64String($bytes)
        $protected = ConvertFrom-SecureString (ConvertTo-SecureString $secret -AsPlainText -Force)
        Set-Content -LiteralPath $keyPath -Value $protected -Encoding ASCII
    }
    $protected = (Get-Content -LiteralPath $keyPath -Raw).Trim()
    $secure = ConvertTo-SecureString -String $protected
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Quote-Argument([string]$value) {
    return '"' + $value.Replace('"', '\"') + '"'
}

function Get-BackupHmac([string]$cipherPath) {
    $digest = & $pythonPath $authScriptPath mac $keyTempPath $cipherPath
    if ($LASTEXITCODE -ne 0) {
        throw "Backup authentication code could not be generated"
    }
    return "$digest".Trim()
}

function Test-BackupHmac([string]$cipherPath, [string]$macPath) {
    $result = & $pythonPath $authScriptPath verify $keyTempPath $cipherPath
    return $LASTEXITCODE -eq 0
}

try {
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    $account = "$env:USERDOMAIN\$env:USERNAME"
    & icacls.exe $backupDirectory /inheritance:r /grant:r "$account`:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict backup directory permissions"
    }
    $secret = Get-BackupSecret
    Set-Content -LiteralPath $keyTempPath -Value $secret -Encoding ASCII
    if ($RestoreFile) {
        $restoreMacPath = "$RestoreFile.hmac"
        if (-not $RestoreTo -or -not (Test-Path -LiteralPath $RestoreFile) -or -not (Test-Path -LiteralPath $restoreMacPath)) {
            throw "A readable backup and a new restore path are required"
        }
        if (Test-Path -LiteralPath $RestoreTo) {
            throw "Restore destination already exists"
        }
        if (-not (Test-BackupHmac $RestoreFile $restoreMacPath)) {
            throw "Backup authentication failed"
        }
        $restoreInfo = New-Object Diagnostics.ProcessStartInfo
        $restoreInfo.FileName = $opensslPath
        $restoreInfo.Arguments = "enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:$(Quote-Argument $keyTempPath) -in $(Quote-Argument $RestoreFile) -out $(Quote-Argument $restorePath)"
        $restoreInfo.UseShellExecute = $false
        $restoreInfo.CreateNoWindow = $true
        $restore = [Diagnostics.Process]::Start($restoreInfo)
        $restore.WaitForExit()
        if ($restore.ExitCode -ne 0) {
            throw "Backup decryption failed"
        }
        Test-Restore $restorePath
        $restoreParent = Split-Path -Parent $RestoreTo
        New-Item -ItemType Directory -Path $restoreParent -Force | Out-Null
        Move-Item -LiteralPath $restorePath -Destination $RestoreTo
        Write-BackupLog "Encrypted backup restored and verified to a new path"
        return
    }
    $sqlitePath = "/tmp/jv-offhost-$runId.sqlite3"
    $pythonCode = 'import sqlite3; s=sqlite3.connect("file:/app/data/bot-state.sqlite3?mode=ro",uri=True); d=sqlite3.connect("__TARGET__"); s.backup(d); d.close(); c=sqlite3.connect("__TARGET__"); ok=c.execute("PRAGMA integrity_check").fetchone()[0]; c.close(); assert ok == "ok"'
    $pythonCode = $pythonCode.Replace("__TARGET__", $sqlitePath)
    $remoteCommand = "docker exec jopesvpn-production-bot python3 -c '$pythonCode' >/dev/null && docker exec jopesvpn-production-bot cat '$sqlitePath'; result=`$?; docker exec jopesvpn-production-bot rm -f '$sqlitePath' >/dev/null 2>&1; exit `$result"
    if ($isBundle) {
        $remoteCommand = "python3 - --root $RemoteRoot"
        if ($VerifyPostgresRestore) { $remoteCommand += ' --verify-postgres-restore' }
    }

    $sshInfo = New-Object Diagnostics.ProcessStartInfo
    $sshInfo.FileName = (Get-Command ssh.exe -ErrorAction Stop).Source
    $sshInfo.Arguments = "-o BatchMode=yes -o ConnectTimeout=15 $(Quote-Argument $SshHost) $(Quote-Argument $remoteCommand)"
    $sshInfo.UseShellExecute = $false
    $sshInfo.RedirectStandardOutput = $true
    $sshInfo.RedirectStandardError = $true
    $sshInfo.RedirectStandardInput = $isBundle
    $sshInfo.CreateNoWindow = $true
    $ssh = New-Object Diagnostics.Process
    $ssh.StartInfo = $sshInfo
    if (-not $ssh.Start()) {
        throw "Could not start SSH backup stream"
    }
    $sshErrorRead = $ssh.StandardError.ReadToEndAsync()
    if ($isBundle) {
        $ssh.StandardInput.Write([IO.File]::ReadAllText($exportScriptPath))
        $ssh.StandardInput.Close()
    }

    $opensslInfo = New-Object Diagnostics.ProcessStartInfo
    $opensslInfo.FileName = $opensslPath
    $opensslInfo.Arguments = "enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass file:$(Quote-Argument $keyTempPath) -out $(Quote-Argument $partialPath)"
    $opensslInfo.UseShellExecute = $false
    $opensslInfo.RedirectStandardInput = $true
    $opensslInfo.CreateNoWindow = $true
    $openssl = New-Object Diagnostics.Process
    $openssl.StartInfo = $opensslInfo
    if (-not $openssl.Start()) {
        throw "Could not start local encryption"
    }
    $ssh.StandardOutput.BaseStream.CopyTo($openssl.StandardInput.BaseStream)
    $openssl.StandardInput.Close()
    $openssl.WaitForExit()
    $ssh.WaitForExit()
    $sshErrorText = $sshErrorRead.GetAwaiter().GetResult()
    if ($openssl.ExitCode -ne 0 -or $ssh.ExitCode -ne 0) {
        $safeStage = [regex]::Match($sshErrorText, 'Recovery export step failed: [a-z_]+ \(exit [0-9]+\)').Value
        if ($safeStage) { Write-BackupLog $safeStage; Write-Output $safeStage }
        throw "Backup stream or encryption failed"
    }
    $mac = Get-BackupHmac $partialPath
    Set-Content -LiteralPath $macPartialPath -Value $mac -Encoding ASCII -NoNewline

    $decryptInfo = New-Object Diagnostics.ProcessStartInfo
    $decryptInfo.FileName = $opensslPath
    $decryptInfo.Arguments = "enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:$(Quote-Argument $keyTempPath) -in $(Quote-Argument $partialPath) -out $(Quote-Argument $restorePath)"
    $decryptInfo.UseShellExecute = $false
    $decryptInfo.CreateNoWindow = $true
    $decrypt = [Diagnostics.Process]::Start($decryptInfo)
    $decrypt.WaitForExit()
    if ($decrypt.ExitCode -ne 0) {
        throw "Encrypted backup could not be decrypted for verification"
    }
    Test-Restore $restorePath
    if (-not (Test-BackupHmac $partialPath $macPartialPath)) { throw "Backup authentication failed" }
    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Move-Item -LiteralPath $macPartialPath -Destination "$finalPath.hmac"
    Write-BackupLog "Encrypted $prefix backup and restore verification succeeded: $(Split-Path $finalPath -Leaf)"
    Get-ChildItem -LiteralPath $backupDirectory -Filter "$prefix-*.$extension.enc" -File |
        Where-Object { $_.LastWriteTimeUtc -lt (Get-Date).ToUniversalTime().AddDays(-$RetentionDays) } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force
            $oldMacPath = "$($_.FullName).hmac"
            if (Test-Path -LiteralPath $oldMacPath) {
                Remove-Item -LiteralPath $oldMacPath -Force
            }
        }
}
catch {
    Write-BackupLog "Backup failed: $($_.Exception.GetType().Name)"
    throw
}
finally {
    foreach ($path in @($partialPath, $macPartialPath, $restorePath, $keyTempPath)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}
