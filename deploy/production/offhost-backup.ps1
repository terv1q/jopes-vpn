param(
    [string]$RestoreFile,
    [string]$RestoreTo
)

$ErrorActionPreference = "Stop"

$backupDirectory = Join-Path $env:USERPROFILE "JopesVPN-Backups"
$keyPath = Join-Path $backupDirectory "backup-key.dpapi"
$logPath = Join-Path $backupDirectory "backup.log"
$runId = [Guid]::NewGuid().ToString("N")
$partialPath = Join-Path $backupDirectory "$runId.sqlite3.enc.partial"
$restorePath = Join-Path $backupDirectory "$runId.sqlite3.verify"
$keyTempPath = Join-Path $backupDirectory "$runId.key"
$finalPath = Join-Path $backupDirectory "bot-state-$((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')).sqlite3.enc"
$macPartialPath = "$partialPath.hmac"
$opensslPath = (Get-Command openssl.exe -ErrorAction Stop).Source
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$authScriptPath = Join-Path $PSScriptRoot "backup-auth.py"

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
        $restore = [Diagnostics.Process]::Start($restoreInfo)
        $restore.WaitForExit()
        if ($restore.ExitCode -ne 0) {
            throw "Backup decryption failed"
        }
        $restoreCheck = & $pythonPath -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); result=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); print(result); sys.exit(result != 'ok')" $restorePath
        if ($LASTEXITCODE -ne 0 -or $restoreCheck -ne "ok") {
            throw "Restored SQLite integrity check failed"
        }
        $restoreParent = Split-Path -Parent $RestoreTo
        New-Item -ItemType Directory -Path $restoreParent -Force | Out-Null
        Move-Item -LiteralPath $restorePath -Destination $RestoreTo
        Write-BackupLog "Encrypted backup restored and verified to a new path"
        return
    }
    $sqlitePath = "/tmp/jv-offhost-$runId.sqlite3"
    $pythonCode = 'import sqlite3; s=sqlite3.connect("/app/data/bot-state.sqlite3"); assert "recovery_retry_used" in {r[1] for r in s.execute("PRAGMA table_info(orders)")}; assert s.execute("PRAGMA table_info(support_ticket_rate_limits)").fetchone(); d=sqlite3.connect("__TARGET__"); s.backup(d); d.close(); c=sqlite3.connect("__TARGET__"); ok=c.execute("PRAGMA integrity_check").fetchone()[0]; c.close(); assert ok == "ok"'
    $pythonCode = $pythonCode.Replace("__TARGET__", $sqlitePath)
    $remoteCommand = "docker exec jopesvpn-production-bot python3 -c '$pythonCode' >/dev/null && docker exec jopesvpn-production-bot cat '$sqlitePath'; result=`$?; docker exec jopesvpn-production-bot rm -f '$sqlitePath' >/dev/null 2>&1; exit `$result"

    $sshInfo = New-Object Diagnostics.ProcessStartInfo
    $sshInfo.FileName = (Get-Command ssh.exe -ErrorAction Stop).Source
    $sshInfo.Arguments = "jopesvpn $(Quote-Argument $remoteCommand)"
    $sshInfo.UseShellExecute = $false
    $sshInfo.RedirectStandardOutput = $true
    $ssh = New-Object Diagnostics.Process
    $ssh.StartInfo = $sshInfo
    if (-not $ssh.Start()) {
        throw "Could not start SSH backup stream"
    }

    $opensslInfo = New-Object Diagnostics.ProcessStartInfo
    $opensslInfo.FileName = $opensslPath
    $opensslInfo.Arguments = "enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -pass file:$(Quote-Argument $keyTempPath) -out $(Quote-Argument $partialPath)"
    $opensslInfo.UseShellExecute = $false
    $opensslInfo.RedirectStandardInput = $true
    $openssl = New-Object Diagnostics.Process
    $openssl.StartInfo = $opensslInfo
    if (-not $openssl.Start()) {
        throw "Could not start local encryption"
    }
    $ssh.StandardOutput.BaseStream.CopyTo($openssl.StandardInput.BaseStream)
    $openssl.StandardInput.Close()
    $openssl.WaitForExit()
    $ssh.WaitForExit()
    if ($openssl.ExitCode -ne 0 -or $ssh.ExitCode -ne 0) {
        throw "Backup stream or encryption failed"
    }
    $mac = Get-BackupHmac $partialPath
    Set-Content -LiteralPath $macPartialPath -Value $mac -Encoding ASCII -NoNewline

    $decryptInfo = New-Object Diagnostics.ProcessStartInfo
    $decryptInfo.FileName = $opensslPath
    $decryptInfo.Arguments = "enc -d -aes-256-cbc -pbkdf2 -iter 600000 -pass file:$(Quote-Argument $keyTempPath) -in $(Quote-Argument $partialPath) -out $(Quote-Argument $restorePath)"
    $decryptInfo.UseShellExecute = $false
    $decrypt = [Diagnostics.Process]::Start($decryptInfo)
    $decrypt.WaitForExit()
    if ($decrypt.ExitCode -ne 0) {
        throw "Encrypted backup could not be decrypted for verification"
    }
    $verification = & $pythonPath -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); result=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); print(result); sys.exit(result != 'ok')" $restorePath
    if ($LASTEXITCODE -ne 0 -or $verification -ne "ok") {
        throw "Restored SQLite integrity check failed"
    }
    Move-Item -LiteralPath $partialPath -Destination $finalPath
    Move-Item -LiteralPath $macPartialPath -Destination "$finalPath.hmac"
    Write-BackupLog "Encrypted SQLite backup and restore verification succeeded: $(Split-Path $finalPath -Leaf)"
    Get-ChildItem -LiteralPath $backupDirectory -Filter "bot-state-*.sqlite3.enc" -File |
        Where-Object { $_.LastWriteTimeUtc -lt (Get-Date).ToUniversalTime().AddDays(-90) } |
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
