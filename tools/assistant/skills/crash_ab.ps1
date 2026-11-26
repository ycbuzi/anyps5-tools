# A/B crash test of one game copy: -Runs launches as it is and -Runs changed (-Env NAME=value;NAME=value, and/or the
# .prx files of -LibDir swapped into its libs, put back after every run). -BaseLibDir swaps the same way on the base
# side, so both sides run one AnyPS5 commit when the copy was built from an older one. Each run lasts up to -Seconds;
# the sides alternate so time of day and disk load hit both alike. Counts crashes (FATAL in anyps5.log) and keeps
# every log in -OutDir\<game>-<stamp>\. Takes _tools\live\gpu-busy.flag through gpu_flag.ps1 (no other game, FPS run or
# job meanwhile), and starts each run only while no other game runs.
# 11-04: five games crashed reading 0xffffffffffffffff while storage-texture write-backs were pending.
param([Parameter(Mandatory)][string]$Copy, [string]$Env = "", [string]$LibDir = "", [string]$BaseLibDir = "", [int]$Runs = 4, [int]$Seconds = 60, [string]$OutDir = "D:\AnyPS5 Games\_reports\crash-ab")
$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
$cfg = Get-Content -LiteralPath (Join-Path $Copy "game.json") -Raw | ConvertFrom-Json
$exe = Join-Path $Copy ("{0}.exe" -f $(if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }))
if (-not (Test-Path -LiteralPath $exe)) { "no $exe"; exit 1 }
$swap = @(if ($LibDir) { Get-ChildItem -LiteralPath $LibDir -Filter *.prx -File })
$baseSwap = @(if ($BaseLibDir) { Get-ChildItem -LiteralPath $BaseLibDir -Filter *.prx -File })
if (-not $Env -and -not $swap) { "nothing to change: give -Env or a -LibDir with .prx files"; exit 1 }
if (@(@($swap) + @($baseSwap) | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Copy "libs\$($_.Name)")) })) { "the copy has no libs\ for every -LibDir file"; exit 1 }
$what = (@($(if ($Env) { $Env })) + @($swap | ForEach-Object { $_.Name })) -join " + "
$dir = Join-Path $OutDir ("{0}-{1}" -f (($cfg.title -replace '[^\w]+', '-').Trim('-')), (Get-Date -Format "MMdd-HHmm"))
New-Item -ItemType Directory -Force $dir | Out-Null
$pairs = @($Env -split ';' | Where-Object { $_ -match '=' } | ForEach-Object { , ($_ -split '=', 2) })
Enter-GpuFlag "crash A/B of $($cfg.title)"
$tally = @{ base = 0; changed = 0 }
$stalled = @{ base = 0; changed = 0 }
try {
    for ($i = 1; $i -le $Runs; $i++) {
        foreach ($side in "base", "changed") {
            foreach ($p in $pairs) { if ($side -eq "changed") { Set-Item -Path "Env:$($p[0])" -Value $p[1] } else { Remove-Item -Path "Env:$($p[0])" -ErrorAction SilentlyContinue } }
            foreach ($l in $(if ($side -eq "changed") { $swap } else { $baseSwap })) { $dst = Join-Path $Copy "libs\$($l.Name)"; Copy-Item -LiteralPath $dst "$dst.before-ab" -Force; Copy-Item -LiteralPath $l.FullName $dst -Force }
            while (Gpu-GameRunning) { Start-Sleep 5 }
            $start = Get-Date
            $proc = & (Join-Path $PSScriptRoot "..\..\start-game.ps1") -Exe $exe -Dir $Copy
            $exited = $proc.WaitForExit($Seconds * 1000)
            if (-not $exited) { Stop-Process -Id $proc.Id -Force; Start-Sleep 3 }
            foreach ($l in @($swap) + @($baseSwap)) { $dst = Join-Path $Copy "libs\$($l.Name)"; if (Test-Path -LiteralPath "$dst.before-ab") { Move-Item -LiteralPath "$dst.before-ab" $dst -Force } }
            $log = Join-Path $dir "$side-$i.log"
            Copy-Item -LiteralPath (Join-Path $Copy "anyps5.log") -Destination $log
            $crash = Select-String -LiteralPath $log -Pattern "^FATAL" -Quiet
            # 11-04 a run that hung at start (two log lines in 60 s) had read as "no crash".
            $lines = @(Get-Content -LiteralPath $log -ErrorAction SilentlyContinue).Count
            $stall = -not $crash -and $exited -eq $false -and $lines -lt 50
            if ($crash) { $tally[$side]++ }
            if ($stall) { $stalled[$side]++ }
            $rip = if ($crash) { ((Select-String -LiteralPath $log -Pattern "^\s+rip " | Select-Object -First 1).Line).Trim() } else { "" }
            "$side $i`: " + $(if ($crash) { "CRASH after $([int]((Get-Date) - $start).TotalSeconds) s ($rip)" } elseif ($stall) { "STALLED: only $lines log lines in $Seconds s, the game did not get going" } else { "no crash in $Seconds s ($lines log lines)" })
            Touch-GpuFlag
        }
    }
} finally {
    foreach ($p in $pairs) { Remove-Item -Path "Env:$($p[0])" -ErrorAction SilentlyContinue }
    foreach ($l in @($swap) + @($baseSwap)) { $dst = Join-Path $Copy "libs\$($l.Name)"; if (Test-Path -LiteralPath "$dst.before-ab") { Move-Item -LiteralPath "$dst.before-ab" $dst -Force } }
    Exit-GpuFlag
}
"RESULT $($cfg.title): crashes as it is$(if ($baseSwap) { " (with " + (($baseSwap | ForEach-Object { $_.Name }) -join "+") + " from $BaseLibDir)" }) $($tally.base)/$Runs, with $what $($tally.changed)/$Runs; logs in $dir" + $(if ($stalled.base + $stalled.changed) { "`nNOT A VALID TEST: $($stalled.base) base and $($stalled.changed) changed run(s) stalled at start, so their no-crash means nothing" } else { "" })
