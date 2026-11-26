param([string]$Game = "RocketLeague", [int]$StartWaitMinutes = 45, [int]$QuietMinutes = 10)
$Tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Tools "live\game-mode.log"
$Snap = Join-Path $Tools "live\pc-monitor.json"
$GameMode = Join-Path $Tools "game-mode.ps1"
function Say($m) { Add-Content -LiteralPath $Log -Value "$(Get-Date -Format 'MM-dd HH:mm:ss') auto: $m" -Encoding utf8 }
function Snapshot { try { Get-Content -LiteralPath $Snap -Raw | ConvertFrom-Json } catch { $null } }
. $GameMode status | Out-Null
$frozenFile = Join-Path $Tools "live\game-mode-frozen.txt"
function IsFrozen { Test-Path -LiteralPath $frozenFile }
function FreezeNow { Freeze | Out-Null }
function ThawNow { Thaw | Out-Null }
$deadline = (Get-Date).AddMinutes($StartWaitMinutes)
while (-not (Get-Process -Name $Game -ErrorAction SilentlyContinue)) {
    if ((Get-Date) -gt $deadline) { Say "$Game never started within $StartWaitMinutes min; game mode stays ON until turned off"; exit 0 }
    Start-Sleep 15
}
Say "$Game started; builds run at Idle priority and freeze only when the game needs the room"
$calmSince = $null
$quietSince = $null
while ($true) {
    $running = [bool](Get-Process -Name $Game -ErrorAction SilentlyContinue)
    if ($running) {
        $quietSince = $null
        $s = Snapshot
        if ($s) {
            $ourGpu = @($s.top | Where-Object { $_.ours -and $_.gpu -ge 10 }).Count
            $tight = $s.ram_pct -ge 85 -or $s.cpu -ge 80 -or $ourGpu -gt 0
            $calm = $s.ram_pct -lt 75 -and $s.cpu -lt 50 -and $ourGpu -eq 0
            if ($s.ram_pct -ge 82) {
                $k = 0
                Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(cc1plus|cc1|ninja|ld|g\+\+|gcc|ccache|cmake|ranlib|ar|ctest)\.exe$' -or ($_.Name -eq 'git.exe' -and $_.CommandLine -match ' gc|repack|pack-objects') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $k++ }
                if ($k) { Remove-Item -LiteralPath $frozenFile -ErrorAction SilentlyContinue; Say "RAM $($s.ram_pct)%: stopped $k build processes to free memory (they rebuild incrementally later)" }
                $calmSince = $null
            }
            elseif ($tight -and -not (IsFrozen)) { FreezeNow; Say "froze builds: RAM $($s.ram_pct)% CPU $($s.cpu)% our GPU users $ourGpu"; $calmSince = $null }
            elseif ($calm -and (IsFrozen)) {
                if (-not $calmSince) { $calmSince = Get-Date }
                elseif (((Get-Date) - $calmSince).TotalSeconds -ge 60) { ThawNow; Say "resumed builds (Idle priority): RAM $($s.ram_pct)% CPU $($s.cpu)%"; $calmSince = $null }
            } elseif (-not $calm) { $calmSince = $null }
        }
    } else {
        if (-not $quietSince) { $quietSince = Get-Date }
        elseif (((Get-Date) - $quietSince).TotalMinutes -ge $QuietMinutes) { break }
    }
    Start-Sleep 10
}
Say "$Game closed $QuietMinutes min ago; turning game mode OFF"
& $GameMode off | Out-Null
