# Offline speed test of open AnyPS5 pull requests on Red Dead Redemption (Blu 11-10: RDR is the closest stand-in for
# a big game; "set background tasks so you're not using usage"). No AI calls. For the base commit alone and then for each
# pull request merged onto it: builds libSceAgcDriver.prx (pr_libs.ps1, its own worktree and build folder), puts it in
# a test copy of the game, runs a warm-up and a measured run on the RTX 4070 SUPER without the game's speed switch,
# and appends FPS, frame time, the largest driver phases and a screenshot to
# D:\AnyPS5 Games\_reports\rdr-speed\report.md. The real game folder is never changed.
#   run it again:  powershell -File rdr_pr_speed.ps1 -Prs 12070,16648
# Blu 11-10: the game sits at "X to play" unless Cross is pressed twice (title menu, then the loading prompt). Each run
# presses Cross at $PressAt seconds through AnyPS5's pad file (tools\pad_input.py), the same times for every variant;
# the presses after the second cover a slower start.
param([int[]]$Prs = @(12070, 16648, 13796, 13251), [string]$Base = "9d2e737432", [int]$Seconds = 240, [int[]]$PressAt = @(40, 60, 80, 100))
$ErrorActionPreference = "Continue"
$Tools = "C:\AnyPS5 Games\_tools"
$Game = "D:\AnyPS5 Games\Red Dead Redemption (PPSA30528)"
$Test = "D:\AnyPS5 Games\_test\Red Dead Redemption (PPSA30528) speed"
$Reports = "D:\AnyPS5 Games\_reports\rdr-speed"
$Report = Join-Path $Reports "report.md"
New-Item -ItemType Directory -Force $Reports | Out-Null
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
function Note([string]$t) { $t; Add-Content -LiteralPath $Report -Value $t -Encoding utf8 }

function Measure-Variant([string]$label, [string]$libDir) {
    if (Test-Path -LiteralPath $Test) { cmd /c rd /s /q "$Test" }
    New-Item -ItemType Directory -Force $Test | Out-Null
    $cfg = Get-Content -LiteralPath (Join-Path $Game "game.json") -Raw | ConvertFrom-Json
    $cfg | Add-Member -NotePropertyName anyps5Build -NotePropertyValue (Join-Path $Tools "anyps5-$Base") -Force
    $cfg | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Test "game.json") -Encoding utf8
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $Test 2>&1 | Out-Null
    $exe = Join-Path $Test "Red Dead Redemption.exe"
    if (-not (Test-Path -LiteralPath $exe)) { Note "- **$label**: the test copy did not build"; return }
    if ($libDir) { Copy-Item -LiteralPath (Join-Path $libDir "libSceAgcDriver.prx") -Destination (Join-Path $Test "libs\libSceAgcDriver.prx") -Force }
    Enter-GpuFlag "Red Dead Redemption speed test $label"
    try {
        $env:ANYPS5_GPU = "4070"; $env:APS5_TRACE_SUMMARY_EVERY = "10"
        $pad = Join-Path $Test "pad.txt"; $env:APS5_PAD_INPUT_FILE = $pad
        Remove-Item Env:APS5_NO_LABEL_READBACK_STORE -ErrorAction SilentlyContinue
        $line = ""
        foreach ($run in "warm-up", "measured") {
            $sum = Join-Path $Test "aps5-summary.jsonl"
            Remove-Item -LiteralPath $sum -Force -ErrorAction SilentlyContinue
            "$label ${run}: $Seconds s"
            $p = & (Join-Path $Tools "start-game.ps1") -Exe $exe -Dir $Test
            $t0 = Get-Date; $shot = $false; $pressed = 0
            while (-not $p.HasExited -and ((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
                Start-Sleep 5; Touch-GpuFlag
                if ($pressed -lt $PressAt.Count -and ((Get-Date) - $t0).TotalSeconds -ge $PressAt[$pressed]) {
                    & python -I "D:\AnyPS5\tools\pad_input.py" $pad cross 2>&1 | Out-Null
                    $pressed++
                }
                if ($run -eq "measured" -and -not $shot -and ((Get-Date) - $t0).TotalSeconds -ge $Seconds * 0.8) {
                    try { & (Join-Path $Tools "window-shot.ps1") -GameDir $Test -Out (Join-Path $Reports "$label.png") | Out-Null } catch { }
                    $shot = $true
                }
            }
            $ran = [int]((Get-Date) - $t0).TotalSeconds
            $ended = if ($p.HasExited) { "exited by itself after $ran s with code 0x{0:X8}" -f ($p.ExitCode -band 0xFFFFFFFF) } else { Stop-Process -Id $p.Id -Force; "ran the full $ran s" }
            Start-Sleep 4
            if ($run -eq "measured") {
                $fps = if (Test-Path -LiteralPath $sum) { (& python -I (Join-Path $PSScriptRoot "rdr_fps.py") $sum $ran) -join "; " } else { "no summary written" }
                $line = "- **$label**: $ended; $fps"
            }
        }
        Note $line
    } finally {
        Remove-Item Env:ANYPS5_GPU, Env:APS5_TRACE_SUMMARY_EVERY, Env:APS5_PAD_INPUT_FILE -ErrorAction SilentlyContinue
        Exit-GpuFlag
    }
}

Note ""
Note "## Run of $(Get-Date -Format 'yyyy-MM-dd HH:mm') on base $Base ($Seconds s warm-up + $Seconds s measured each, RTX 4070 SUPER, speed switch off)"
$libs = Join-Path $Reports "libs"
$out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "pr_libs.ps1") -NoPr -Base $Base -Libs libSceAgcDriver -OutDir $libs 2>&1
if ($LASTEXITCODE -eq 0) { Measure-Variant "base-$Base" (Join-Path $libs "main-$Base") } else { Note "- **base-$Base**: did not build: $(($out | Select-Object -Last 2) -join ' ')" }
foreach ($n in $Prs) {
    $title = (& gh pr view $n --repo boykopovar/AnyPS5 --json title,state --jq '.state + ": " + .title' 2>$null)
    "PR #$n ($title): building"
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "pr_libs.ps1") -Number $n -Base $Base -Libs libSceAgcDriver -OutDir $libs 2>&1
    if ($LASTEXITCODE -ne 0) { Note "- **PR #$n** ($title): did not merge or build onto ${Base}: $((($out | Select-Object -Last 2) -join ' ').Substring(0, [Math]::Min(300, (($out | Select-Object -Last 2) -join ' ').Length)))"; continue }
    Measure-Variant "pr$n" (Join-Path $libs "pr$n-$Base")
    Add-Content -LiteralPath $Report -Value "  (#$n $title)" -Encoding utf8
}
if (Test-Path -LiteralPath $Test) { cmd /c rd /s /q "$Test" }
Note "finished $(Get-Date -Format 'HH:mm'); screenshots are next to this file"
