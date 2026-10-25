$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE 'JopesVPN-Backups\backup-key.dpapi'
$secure = ConvertTo-SecureString ([IO.File]::ReadAllText($keyPath).Trim())
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $secret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
$pythonPath = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.venv\Scripts\python.exe'
$info = New-Object Diagnostics.ProcessStartInfo
$info.FileName = $pythonPath
$info.Arguments = '"' + (Join-Path $PSScriptRoot 'setup-cloud.py') + '"'
$info.WorkingDirectory = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($info)
$output = $process.StandardOutput.ReadToEndAsync()
$errors = $process.StandardError.ReadToEndAsync()
$process.StandardInput.Write((@{ backup_secret = $secret } | ConvertTo-Json -Compress))
$process.StandardInput.Close()
$secret = $null
$process.WaitForExit()
Write-Output $output.GetAwaiter().GetResult()
$null = $errors.GetAwaiter().GetResult()
exit $process.ExitCode
