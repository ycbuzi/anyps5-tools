# The PC lock (11-12, Blu: "the pipeline has too many things that interfere with it ... sometimes just doesn't know
# what it's doing and gets stuck"). One heavy job at a time owns the PC: GPU PR tests, the GPU lanes, the nightly
# health run, manual helper runs (winrun, main-baseline-run) and the pipeline's builds and game tests. The owner is
# _tools\live\gpu-busy.flag ("<pid> <HH:mm:ss> <job>", the same file and format the pipeline and the app already read).
# Waiting jobs register in _tools\live\pc-wait\<pid>.txt ("<priority> <ticks> <job>"); the highest priority goes first,
# then the one waiting longest. Priorities: 90 Blu asked for it now, 80 a request from requests.txt, 60 the pipeline,
# 50 manual helper runs, 40 GPU PR tests, 30 nightly health, 20 GPU lanes.
# Nothing starts while _tools\pause.flag exists (Blu paused everything) or while Blu plays a game, unless the job's
# priority is 90 and it is not a game test. A lock whose owner process is gone, or that was not refreshed for 20
# minutes, is taken over. Programs started by the owner inherit ANYPS5_PC_LOCK and pass straight through, so a GPU PR
# test's winrun does not wait for the lock its own parent holds.
# Dot-sourced by gpu_prtest.ps1, crash_ab.ps1, pr_libs.ps1, gpu-lanes.ps1, health.ps1, winrun.ps1, main-baseline-run.ps1
# and auto-games.ps1. Enter-GpuFlag / Touch-GpuFlag / Exit-GpuFlag keep their old names and calls.
$GpuGames = "D:\AnyPS5 Games"
$GpuTools = Join-Path $GpuGames "_tools"
$GpuFlag = Join-Path $GpuTools "live\gpu-busy.flag"
$PcWaitDir = Join-Path $GpuTools "live\pc-wait"
$PcPauseFlag = Join-Path $GpuTools "pause.flag"
function Gpu-LanePids { @(Get-ChildItem -LiteralPath (Join-Path $GpuTools "live\lanes") -Filter *.json -ErrorAction SilentlyContinue | ForEach-Object { try { [int](Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).pid } catch { } }) }
function Gpu-GameRunning {
    $lanes = Gpu-LanePids
    [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and -not $(try { $_.HasExited } catch { $false }) -and $lanes -notcontains $_.Id -and (
        ($_.Path -match '^[A-Za-z]:\\AnyPS5 Games\\[^\\]+\\' -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*") -or
        ($_.Path -match '\\steamapps\\common\\' -and $_.ProcessName -notmatch '^(steam|steamwebhelper|UnityCrashHandler\d*|CrashReport.*)$') -or
        ($_.Path -match '\\Epic Games\\' -and $_.Path -notmatch '\\Launcher\\') -or
        ($_.Path -match '\\Riot Games\\.*\\(VALORANT|League of Legends)' )) })
}
function Gpu-FpsRunning { [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '--from-pipeline' }) }
function Pc-Owner {
    $text = try { [IO.File]::ReadAllText($GpuFlag).Trim() } catch { return $null }
    $parts = $text -split ' ', 3
    $ownerPid = 0; [void][int]::TryParse($parts[0], [ref]$ownerPid)
    $age = try { ((Get-Date) - (Get-Item -LiteralPath $GpuFlag).LastWriteTime).TotalMinutes } catch { 0 }
    [pscustomobject]@{ text = $text; pid = $ownerPid; job = $(if ($parts.Count -gt 2) { $parts[2] } else { "" }); minutes = $age
        alive = ($ownerPid -gt 0 -and [bool](Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) }
}
function Pc-ClearStale {
    $o = Pc-Owner
    if ($o -and (-not $o.alive -or $o.minutes -ge 20)) { Remove-Item -LiteralPath $GpuFlag -ErrorAction SilentlyContinue }
}
function Pc-TopWaiter {
    $rows = foreach ($f in @(Get-ChildItem -LiteralPath $PcWaitDir -Filter *.txt -ErrorAction SilentlyContinue)) {
        $waiterPid = 0; [void][int]::TryParse($f.BaseName, [ref]$waiterPid)
        if (-not (Get-Process -Id $waiterPid -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $f.FullName -ErrorAction SilentlyContinue; continue }
        $text = try { [IO.File]::ReadAllText($f.FullName) } catch { "" }
        $p = "$text" -split ' ', 3
        $priority = 0; $ticks = [long]::MaxValue
        [void][int]::TryParse($p[0], [ref]$priority); if ($p.Count -gt 1) { [void][long]::TryParse($p[1], [ref]$ticks) }
        [pscustomobject]@{ pid = $waiterPid; priority = $priority; ticks = $ticks }
    }
    @($rows | Sort-Object @{ e = { $_.priority }; Descending = $true }, ticks)[0]
}
function Enter-GpuFlag([string]$what, [int]$priority = 40, [scriptblock]$OnWait = $null, [switch]$Game) {
    $current = Pc-Owner
    if ($env:ANYPS5_PC_LOCK -and $current -and $current.text -eq $env:ANYPS5_PC_LOCK) { $script:GpuMine = $null; $script:GpuInherited = $true; return }
    if ($current -and $current.pid -eq $PID) { $script:GpuDepth = [int]$script:GpuDepth + 1; return }
    $script:GpuInherited = $false
    New-Item -ItemType Directory -Force $PcWaitDir | Out-Null
    $waitFile = Join-Path $PcWaitDir "$PID.txt"
    [IO.File]::WriteAllText($waitFile, "$priority $((Get-Date).Ticks) $what")
    try {
        while ($true) {
            $won = $false
            try {
                $paused = (Test-Path -LiteralPath $PcPauseFlag) -and -not ($priority -ge 90 -and -not $Game)
                $busy = (Gpu-GameRunning) -or (Gpu-FpsRunning)
                if (-not $paused -and -not ($busy -and ($Game -or $priority -lt 90))) {
                    Pc-ClearStale
                    $top = Pc-TopWaiter
                    if (-not $top -or $top.pid -eq $PID) {
                        $mine = "$PID $(Get-Date -Format HH:mm:ss) $what"
                        $stream = [IO.File]::Open($GpuFlag, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
                        $bytes = [Text.Encoding]::UTF8.GetBytes($mine)
                        $stream.Write($bytes, 0, $bytes.Length); $stream.Close()
                        $script:GpuMine = $mine
                        $script:GpuDepth = 1
                        $env:ANYPS5_PC_LOCK = $mine
                        $won = $true
                    }
                }
            } catch { }
            if ($won) { return }
            try { if ($OnWait) { & $OnWait } } catch { }
            Start-Sleep 15
        }
    } finally { Remove-Item -LiteralPath $waitFile -ErrorAction SilentlyContinue }
}
function Touch-GpuFlag { if (Test-Path -LiteralPath $GpuFlag) { (Get-Item -LiteralPath $GpuFlag).LastWriteTime = Get-Date } }
function Exit-GpuFlag {
    if ($script:GpuInherited) { $script:GpuInherited = $false; return }
    if ([int]$script:GpuDepth -gt 1) { $script:GpuDepth = [int]$script:GpuDepth - 1; return }
    $now = try { [IO.File]::ReadAllText($GpuFlag) } catch { "" }
    if ($script:GpuMine -and $now -eq $script:GpuMine) { Remove-Item -LiteralPath $GpuFlag -ErrorAction SilentlyContinue }
    $script:GpuDepth = 0; $script:GpuMine = $null
    Remove-Item Env:\ANYPS5_PC_LOCK -ErrorAction SilentlyContinue
}
function Use-Pc([string]$what, [int]$priority, [scriptblock]$work, [scriptblock]$OnWait = $null, [switch]$Game) {
    Enter-GpuFlag $what $priority $OnWait -Game:$Game
    try { & $work } finally { Exit-GpuFlag }
}
