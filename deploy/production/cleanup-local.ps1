$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Split-Path (Split-Path $PSScriptRoot -Parent) -Parent))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'JopesVPN-Backups'))
$temporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$targets = @(
    @{ Root = $backupRoot; Name = 'verified-restore.tar.gz'; Sensitive = $true },
    @{ Root = $workspaceRoot; Name = '.panel-index.tmp.js'; Sensitive = $false },
    @{ Root = $workspaceRoot; Name = 'docs\service-reliability\audit-error.txt'; Sensitive = $false }
)
foreach ($name in @('opencode-20261025T162346Z.tar','jopesvpn-health-fix.tar','jopesvpn-ops-docs.tar','jopesvpn-final-report.tar')) {
    $targets += @{ Root = $temporaryRoot; Name = $name; Sensitive = $false }
}
foreach ($target in $targets) {
    $root = [IO.Path]::GetFullPath($target.Root).TrimEnd('\') + '\'
    $path = [IO.Path]::GetFullPath((Join-Path $root $target.Name))
    if (-not $path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Cleanup target escaped its approved directory' }
    if (-not (Test-Path -LiteralPath $path)) { Write-Output "Absent: $($target.Name)"; continue }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Cleanup refuses directories and links' }
    if ($target.Sensitive) {
        $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $buffer = New-Object byte[] 1048576
            $remaining = $stream.Length
            while ($remaining -gt 0) {
                $count = [int][Math]::Min($remaining, $buffer.Length)
                $stream.Write($buffer, 0, $count)
                $remaining -= $count
            }
            $stream.Flush($true)
        }
        finally { $stream.Dispose() }
    }
    Remove-Item -LiteralPath $path -Force
    Write-Output "Removed: $($target.Name)"
}
