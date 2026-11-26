# Cold-start A/B of two converted copies of one game (e.g. main and a shader-cache change), each with its own shader
# cache folder on a slow drive. Before every run the Windows file cache is dropped (shader_tier.py purge-standby), so
# both sides read the drive. Runs alternate main, change, main, change ... for -Rounds rounds of -Seconds each, with
# APS5_TRACE_SECONDS=0:<TraceSeconds> and a log per run; then tools\trace_digest.py digests every trace.
# Results: _reports\cache-prefetch-ab\<Tag>\ (summary.md, logs, traces, digests). Holds the PC lock (50).
#   cache_prefetch_ab.ps1 -MainCopy C:\AnyPS5-work\branch-runs\pf-main -ChangeCopy C:\AnyPS5-work\branch-runs\pf-change
#       -CacheRoot "D:\AnyPS5 Games\_shader-ab" -Tag luna [-Rounds 2] [-Seconds 120] [-TraceSeconds 60]
# Cache folders: <CacheRoot>\main and <CacheRoot>\change (warm them first with one run each, e.g. branch_game_run.ps1).
param(
    [Parameter(Mandatory)][string]$MainCopy,
    [Parameter(Mandatory)][string]$ChangeCopy,
    [Parameter(Mandatory)][string]$CacheRoot,
    [Parameter(Mandatory)][string]$Tag,
    [int]$Rounds = 2,
    [int]$Seconds = 120,
    [int]$TraceSeconds = 60,
    [string]$Tree = "C:\AnyPS5-work\wt-cachepf"
)
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
$py = "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
$tier = "C:\AnyPS5 Games\_tools\shader_tier.py"
$Out = "C:\AnyPS5 Games\_reports\cache-prefetch-ab\$Tag"
New-Item -ItemType Directory -Force $Out | Out-Null
$Summary = Join-Path $Out "summary.md"
function Say([string]$m) { $l = (Get-Date -Format "MM-dd HH:mm:ss ") + $m; $l; Add-Content -LiteralPath (Join-Path $Out "progress.txt") -Value $l -Encoding utf8 }

$PauseFlag = "C:\AnyPS5 Games\_tools\pause.flag"
function Wait-NoPause { while (Test-Path -LiteralPath $PauseFlag) { Start-Sleep -Seconds 30; Touch-GpuFlag } }

function Run-Once([string]$side, [string]$copy, [int]$i) {
    Wait-NoPause
    $exe = Get-ChildItem -LiteralPath $copy -Filter *.exe | Where-Object { $_.Name -notlike "AnyPS5*" } | Select-Object -First 1
    $report = Join-Path $copy "anyps5-report.json"
    $before = try { (Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).run.id } catch { "" }
    $purge = & $py -I $tier purge-standby 2>&1
    if ($LASTEXITCODE -ne 0) { Say "  purge failed: $purge" }
    $run = "$side-$i"
    $traceDir = Join-Path $Out "trace-$run"
    New-Item -ItemType Directory -Force $traceDir | Out-Null
    $log = Join-Path $Out "$run.log"
    $env:ANYPS5_SHADER_CACHE_DIR = Join-Path $CacheRoot $side
    $env:ANYPS5_LOG_FILE = $log
    $env:APS5_TRACE_SECONDS = "0:$TraceSeconds"
    $env:APS5_TRACE_DIR = $traceDir
    $p = & "C:\AnyPS5 Games\_tools\start-game.ps1" -Exe $exe.FullName -Dir $copy
    $end = (Get-Date).AddSeconds($Seconds)
    $intruder = $false
    while ((Get-Date) -lt $end -and -not $p.HasExited) {
        Start-Sleep -Seconds 2
        Touch-GpuFlag
        if (@(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith("D:\AnyPS5 Games\", [StringComparison]::OrdinalIgnoreCase) -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*" }).Count -or (Test-Path -LiteralPath $PauseFlag)) { $intruder = $true; break }
    }
    $ended = if ($p.HasExited) { "exited by itself" } else { "closed" }
    if (-not $p.HasExited) { [void]$p.CloseMainWindow(); [void]$p.WaitForExit(30000) }
    if (-not $p.HasExited) { $ended = "killed"; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; [void]$p.WaitForExit(15000) }
    Remove-Item Env:ANYPS5_SHADER_CACHE_DIR, Env:ANYPS5_LOG_FILE, Env:APS5_TRACE_SECONDS, Env:APS5_TRACE_DIR -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $r = try { Get-Content -LiteralPath $report -Raw | ConvertFrom-Json } catch { $null }
    $fresh = $r -and $r.run.id -ne $before
    $fp = if ($fresh) { $r.run.milestones.firstPresent } else { $null }
    $windows = @(Select-String -LiteralPath $log -Pattern '\[shader-disk-cache\] \(10 s\): (\d+) hits, (\d+) misses.*? ([0-9.]+) MiB read in ([0-9.]+) ms' -ErrorAction SilentlyContinue | Select-Object -First 3)
    $hits = 0; $mib = 0.0; $ms = 0.0
    foreach ($w in $windows) { $g = $w.Matches[0].Groups; $hits += [int]$g[1].Value; $mib += [double]$g[3].Value; $ms += [double]$g[4].Value }
    $pf = @(Select-String -LiteralPath $log -Pattern '\[shader-disk-cache\] prefetch \(10 s\).*totals: (\d+) entries, ([0-9.]+) MiB' -ErrorAction SilentlyContinue | Select-Object -Last 1)
    $prefetch = if ($pf.Count) { "$($pf[0].Matches[0].Groups[1].Value) entries, $($pf[0].Matches[0].Groups[2].Value) MiB" } else { "-" }
    $trace = Get-ChildItem -LiteralPath $traceDir -Filter "aps5-trace-*.json.gz" -ErrorAction SilentlyContinue | Select-Object -First 1
    $digest = Join-Path $Out "digest-$run.txt"
    if ($trace) { & $py (Join-Path $Tree "tools\trace_digest.py") $trace.FullName *> $digest } else { "no trace written" | Set-Content -LiteralPath $digest -Encoding utf8 }
    $line = (Get-Content -LiteralPath $digest -ErrorAction SilentlyContinue | Select-Object -First 3) -join " / "
    Say "  $run firstPresent=$fp s; first 30 s cache: $hits hits, $mib MiB in $ms ms; prefetch $prefetch; $ended$(if ($intruder) { ' (another game ran or Blu paused the PC: discarded)' })"
    [pscustomobject]@{ side = $side; run = $i; firstPresent = $fp; hits = $hits; mib = $mib; ms = $ms; prefetch = $prefetch; intruder = $intruder; digest = $line; commit = $(if ($fresh) { "$($r.anyps5.commit)".Substring(0, [Math]::Min(10, "$($r.anyps5.commit)".Length)) }) }
}

$rows = @()
Use-Pc "shader cache prefetch A/B $Tag" 50 {
    Say "start: $Rounds rounds x $Seconds s, cache root $CacheRoot"
    foreach ($i in 1..$Rounds) {
        foreach ($side in "main", "change") {
            $copy = if ($side -eq "main") { $MainCopy } else { $ChangeCopy }
            $script:rows += Run-Once $side $copy $i
        }
    }
}
$lines = @("## $Tag ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))", "", "| side | run | firstPresent s | first 30 s cache hits | MiB | load ms (summed) | prefetch | build | note |", "|---|---|---|---|---|---|---|---|---|")
foreach ($r in $rows) { $lines += "| $($r.side) | $($r.run) | $($r.firstPresent) | $($r.hits) | $($r.mib) | $($r.ms) | $($r.prefetch) | $($r.commit) | $(if ($r.intruder) { 'discarded' }) |" }
$lines += ""
foreach ($r in $rows) { $lines += "- $($r.side) $($r.run) digest: $($r.digest)" }
Add-Content -LiteralPath $Summary -Value ($lines -join "`n") -Encoding utf8
Say "RESULT written to $Summary"
