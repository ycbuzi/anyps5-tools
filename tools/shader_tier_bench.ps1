# Time to first present (anyps5-report.json run.milestones.firstPresent) of one game with its shader_cache on D: and
# then on the C: SSD tier (shader_tier.py), same build, -Runs runs each. The Windows file cache is dropped before every
# run so neither side reads from RAM. Waits up to -MaxWaitMinutes for no game to run, then takes the PC lock (80).
# Each run: start (start-game.ps1, game-env.txt applied), -Seconds later close the window (the report is written on
# close), kill after 15 s more. The game's tier state is put back as it was. Results: _reports\shader-tier-bench.md.
#   shader_tier_bench.ps1 -GameDir "D:\AnyPS5 Games\<game>" [-Runs 2] [-Seconds 40] [-MaxWaitMinutes 120]
param([Parameter(Mandatory)][string]$GameDir, [int]$Runs = 2, [int]$Seconds = 40, [int]$MaxWaitMinutes = 120, [string[]]$Sides = @("D", "C"))
$ErrorActionPreference = "Continue"
$Tools = $PSScriptRoot
. (Join-Path $Tools "assistant\skills\gpu_flag.ps1")
$GameDir = $GameDir.TrimEnd('\')
$name = Split-Path $GameDir -Leaf
$py = "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
$tier = Join-Path $Tools "shader_tier.py"
$out = "D:\AnyPS5 Games\_reports\shader-tier-bench.md"
$report = Join-Path $GameDir "anyps5-report.json"
function Say($t) { $line = "$(Get-Date -Format 'MM-dd HH:mm:ss') $t"; Write-Host $line; Add-Content -LiteralPath "D:\AnyPS5 Games\_reports\shader-tier.log" -Value "bench: $line" -Encoding utf8 }
function Tier([string[]]$a) { & $py -I $tier @a 2>&1 | ForEach-Object { Say "  $_" }; $LASTEXITCODE }
function IsHot { (Get-Item -LiteralPath (Join-Path $GameDir "shader_cache") -ErrorAction SilentlyContinue).LinkType -eq "Junction" }

$exe = Get-ChildItem -LiteralPath $GameDir -Filter *.exe | Where-Object { $_.Name -notlike "AnyPS5*" } | Select-Object -First 1
if (-not $exe) { Say "${name}: no game exe"; exit 1 }
$deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
while (Gpu-GameRunning) {
    if ((Get-Date) -gt $deadline) { Say "${name}: a game was still running after $MaxWaitMinutes min; not measured"; exit 4 }
    Start-Sleep -Seconds 30
}

function Run-Once([string]$where, [int]$i) {
    $before = try { (Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).run.id } catch { "" }
    $purge = & $py -I $tier purge-standby 2>&1
    if ($LASTEXITCODE -ne 0) { Say "  purge failed: $purge" }
    $saved = @{}
    $envFile = Join-Path $GameDir "game-env.txt"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($l in Get-Content -LiteralPath $envFile) { if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $saved[$matches[1]] = [Environment]::GetEnvironmentVariable($matches[1]); [Environment]::SetEnvironmentVariable($matches[1], $matches[2]) } }
    }
    $logDir = "D:\AnyPS5 Games\_reports\shader-tier-bench-logs"
    New-Item -ItemType Directory -Force $logDir | Out-Null
    $log = Join-Path $logDir ("{0}-{1}-{2}.log" -f ($name -replace '[^\w]+', '_'), $where.TrimEnd(':'), $i)
    $shell = & (Join-Path $Tools "start-game.ps1") -Exe $env:ComSpec -Dir $GameDir -Arguments ('/c ""' + $exe.FullName + '" 2>&1 | "' + $py + '" -I "' + $tier + '" stamp --game "' + $log + '""')
    $p = $null
    foreach ($t in 1..20) { Start-Sleep -Milliseconds 500; $p = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe.FullName } | Select-Object -First 1; if ($p) { break } }
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    if (-not $p) { $p = $shell }
    $end = (Get-Date).AddSeconds($Seconds)
    $intruder = $false
    while ((Get-Date) -lt $end -and -not $p.HasExited) {
        Start-Sleep -Seconds 2
        if (@(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith("D:\AnyPS5 Games\", [StringComparison]::OrdinalIgnoreCase) -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*" -and $_.Id -ne $p.Id }).Count) { $intruder = $true; break }
    }
    $ended = if ($p.HasExited) { "exited by itself" } else { "closed" }
    if (-not $p.HasExited) { [void]$p.CloseMainWindow(); [void]$p.WaitForExit(30000) }
    if (-not $p.HasExited) { $ended = "killed"; Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; [void]$p.WaitForExit(15000) }
    [void]$shell.WaitForExit(10000)
    Start-Sleep -Seconds 2
    $r = try { Get-Content -LiteralPath $report -Raw | ConvertFrom-Json } catch { $null }
    $fp = if ($r -and $r.run.id -ne $before) { $r.run.milestones.firstPresent } else { $null }
    $hits = if ($r -and $r.run.id -ne $before) { $r.shaders.cacheHits } else { $null }
    $firstWindow = ""
    $w = Select-String -LiteralPath $log -Pattern '^\s*[0-9.]+ \[shader-disk-cache\] \(10 s\): (\d+) hits.*? ([0-9.]+) MiB read in ([0-9.]+) ms' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($w) { $firstWindow = "$($w.Matches[0].Groups[1].Value) hits, $($w.Matches[0].Groups[2].Value) MiB in $($w.Matches[0].Groups[3].Value) ms" }
    $row = [pscustomobject]@{ where = $where; run = $i; firstWindow = $firstWindow; firstPresent = $fp; cacheHits = $hits; firstDraw = $(if ($fp) { $r.run.milestones.firstDraw }); gpu = $(if ($r) { $r.system.gpu.name }); commit = $(if ($r) { "$($r.anyps5.commit)".Substring(0, [Math]::Min(10, "$($r.anyps5.commit)".Length)) }); intruder = $intruder }
    Say "  $where run ${i}: first 10 s: $firstWindow; report firstPresent=$fp s, firstDraw=$($row.firstDraw) s, cache hits=$hits, $ended, log $log$(if ($intruder) { ' (another game started; run discarded)' })"
    $row
}

$rows = @()
Use-Pc "shader tier bench $name" 80 {
    $wasHot = IsHot
    Say "${name}: bench start (cache was on $(if ($wasHot) { 'C:' } else { 'D:' }))"
    if ($wasHot) { [void](Tier @("demote", "--game", $GameDir)) }
    if (-not (IsHot) -and $Sides -contains "D") { foreach ($i in 1..$Runs) { $script:rows += Run-Once "D:" $i } }
    if ($Sides -contains "C" -and (Tier @("promote", "--game", $GameDir)) -eq 0 -and (IsHot)) { foreach ($i in 1..$Runs) { $script:rows += Run-Once "C:" $i } } elseif ($Sides -contains "C") { Say "${name}: promotion failed; C: side not measured" }
    if (-not $wasHot) { [void](Tier @("demote", "--game", $GameDir)) }
} -Game

$lines = @("", "## $name ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))", "", "| cache on | run | first 10 s cache reads | report firstPresent s | firstDraw s | cache hits | GPU | build |", "|---|---|---|---|---|---|---|---|")
foreach ($r in $rows) { $lines += "| $($r.where) | $($r.run) | $($r.firstWindow) | $($r.firstPresent) | $($r.firstDraw) | $($r.cacheHits) | $($r.gpu) | $($r.commit) |$(if ($r.intruder) { ' discarded' })" }
Add-Content -LiteralPath $out -Value $lines -Encoding utf8
$rows | ConvertTo-Json -Compress | Write-Host
