# Start or finish a hand-played screenshot session for one game.
#   showcase-session.ps1 -GameDir <dir> -Start   backs up _sd, launches the game, waits for frames
#   showcase-session.ps1 -GameDir <dir> -Finish  closes the game and puts _sd back exactly as it was
param([Parameter(Mandatory = $true)][string]$GameDir, [switch]$Start, [switch]$Finish)
$GameDir = $GameDir.TrimEnd('\')
$cfg = Get-Content -LiteralPath (Join-Path $GameDir "game.json") -Raw | ConvertFrom-Json
$exe = Join-Path $GameDir ("{0}.exe" -f $(if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }))
$marker = Join-Path $GameDir "ai-runs\showcase-backup.txt"
if ($Start) {
    New-Item -ItemType Directory -Force (Join-Path $GameDir "ai-runs") | Out-Null
    $backup = Join-Path $GameDir ("ai-runs\showcase-" + (Get-Date -Format "yyyyMMdd-HHmm"))
    if (Test-Path -LiteralPath (Join-Path $GameDir "_sd")) { Copy-Item -LiteralPath (Join-Path $GameDir "_sd") -Destination (Join-Path $backup "_sd") -Recurse; "saves backed up" } else { New-Item -ItemType Directory -Force $backup | Out-Null; "no saves yet" }
    Set-Content -LiteralPath $marker -Value $backup
    $p = & (Join-Path $PSScriptRoot "start-game.ps1") -Exe $exe -Dir $GameDir
    for ($i = 0; $i -lt 100; $i++) { Start-Sleep 3; $p.Refresh(); if ($p.HasExited) { "game exited 0x{0:X}" -f $p.ExitCode; exit 1 }; if ($p.MainWindowTitle -match 'FPS: [0-9.]+ \(([0-9]+)\)' -and [int]$Matches[1] -gt 60) { "running after $($i*3) s: $($p.MainWindowTitle)"; exit 0 } }
    "no frames yet: '$($p.MainWindowTitle)'"
}
if ($Finish) {
    Get-Process | Where-Object { $_.Path -eq $exe } | Stop-Process -Force; Start-Sleep 2
    $backup = Get-Content -LiteralPath $marker -ErrorAction SilentlyContinue
    if ($backup -and (Test-Path -LiteralPath (Join-Path $backup "_sd"))) {
        Remove-Item -LiteralPath (Join-Path $GameDir "_sd") -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath (Join-Path $backup "_sd") -Destination (Join-Path $GameDir "_sd") -Recurse; "saves restored"
    } elseif ($backup) { Remove-Item -LiteralPath (Join-Path $GameDir "_sd") -Recurse -Force -ErrorAction SilentlyContinue; "no saves before: removed the session's new save" }
    Remove-Item -LiteralPath $marker -ErrorAction SilentlyContinue
}
