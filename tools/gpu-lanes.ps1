# Extra GPU test lanes beside the pipeline (Blu 11-06: the whole PC, all three GPUs, is for AnyPS5). The pipeline keeps
# testing on the RTX 4070 SUPER; this runs a lane on the GTX 1650 SUPER and one on the Radeon iGPU (RDNA2, the PS5's GPU
# family). Each lane tests games that already run, one at a time, on its own GPU (ANYPS5_GPU) and records the result in
# _reports\gpu-matrix.json and gpu-matrix.md: which games run on which GPU, how fast, and where they break.
# It never writes the pipeline's state; its games' processes are listed in _tools\live\lanes\ so the pipeline does not
# wait for them. A lane starts a game only when enough memory is free, the pipeline is not paused, Rocket League is not
# running and the local AI's model is not on that GPU. Each lane's games get their own TMP/TEMP: AnyPS5 keeps its
# named shared memory under the temp folder and cannot secure that folder while another game has files open in it. Games on G: and C: go first so the D: drive stays quiet.
param([int]$Seconds = 600, [int]$RetestDays = 7, [int]$MinFreeGB = 6)
$ErrorActionPreference = "Continue"
$Tools = $PSScriptRoot
$Games = "D:\AnyPS5 Games"
$LaneDir = Join-Path $Tools "live\lanes"
$Matrix = Join-Path $Games "_reports\gpu-matrix.json"
$MatrixMd = Join-Path $Games "_reports\gpu-matrix.md"
$Log = Join-Path $Tools "gpu-lanes.log"
$Lanes = [ordered]@{ "GTX 1650 SUPER" = "1650"; "Radeon iGPU" = "Radeon" }
New-Item -ItemType Directory -Force $LaneDir | Out-Null
# 11-12 the PC lock (assistant\skills\gpu_flag.ps1): lanes are the lowest priority, so a lane starts no new game while
# another job holds the PC or waits for it; a lane game already running finishes as before.
. (Join-Path $Tools "assistant\skills\gpu_flag.ps1")
function Pc-Wanted { $o = Pc-Owner; [bool](($o -and $o.alive -and $o.minutes -lt 20) -or (Pc-TopWaiter)) }
# Blu streams the primary monitor on Discord (a Screen share) and wants every test on it (11-06): the pipeline's main
# test (RTX 4070) and each lane game in a 2x2 grid of equal 16:9 cells (Blu: "all proportional, even sized"): main
# top left, the 1650 top right, the Radeon bottom left; the AnyPS5 Games window shows in the fourth cell. Sizes follow the primary monitor's working area.
Add-Type -AssemblyName System.Windows.Forms
Add-Type 'using System; using System.Runtime.InteropServices; public static class LaneWin { [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool r); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); }'
# 11-08: a PR test whose libraries lack an entry point the game's build imports made Windows show an "Entry Point Not
# Found" box that held the run until someone clicked it. Games started from here inherit this error mode and exit instead.
Add-Type 'using System; using System.Runtime.InteropServices; public static class LaneErr { [DllImport("kernel32.dll")] public static extern uint SetErrorMode(uint m); }'
[void][LaneErr]::SetErrorMode(0x8003)
function Slot($index) {
    $a = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $w = [int]($a.Width / 2); $h = [int]($w * 9 / 16)
    $cell = $index + 1
    @(($a.X + ($cell % 2) * $w), ($a.Y + [int][math]::Floor($cell / 2) * $h), $w, $h)
}
function Put($handle, $rect) {
    [LaneWin]::ShowWindow($handle, 9) | Out-Null
    [LaneWin]::MoveWindow($handle, $rect[0], $rect[1], $rect[2], $rect[3], $true) | Out-Null
}
function Place($run) {
    $run.proc.Refresh()
    if ($run.placed -or $run.proc.MainWindowHandle -eq 0) { return }
    Put $run.proc.MainWindowHandle (Slot ([array]::IndexOf(@($Lanes.Values), $run.match)))
    $run.placed = $true
}
$script:MainPlaced = @{}
function Place-Main {
    $lanePids = @($runs.Values | ForEach-Object { $_.proc.Id })
    foreach ($p in Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and $_.Path -and $lanePids -notcontains $_.Id -and $_.ProcessName -notlike "AnyPS5*" -and $_.Path -notlike "*\_tools\*" -and ($_.Path -like "D:\AnyPS5 Games\*" -or $_.Path -like "G:\AnyPS5 Games\*" -or $_.Path -like "C:\AnyPS5 Games\*") }) {
        if ($script:MainPlaced.ContainsKey($p.Id)) { continue }
        Start-Sleep 3
        Put $p.MainWindowHandle (Slot -1)
        $script:MainPlaced[$p.Id] = $true
        Say "placed the main test window $($p.ProcessName) at the top left of the streamed monitor"
    }
}
function Say($m) { "$(Get-Date -Format 'MM-dd HH:mm:ss')  $m" | Add-Content -LiteralPath $Log -Encoding utf8 }
function Load-Matrix { try { $m = Get-Content -LiteralPath $Matrix -Raw -Encoding utf8 | ConvertFrom-Json } catch { $m = $null }; if (-not $m) { $m = [pscustomobject]@{} }; $m }
function Free-GB { try { [math]::Round((Get-Counter '\Memory\Available MBytes' -ErrorAction Stop).CounterSamples[0].CookedValue / 1024, 1) } catch { 0 } }
function Real-Drive($dir) { try { $t = (Get-Item -LiteralPath $dir).Target; if ($t) { return "$t".Substring(0, 1).ToUpper() } } catch { }; $dir.Substring(0, 1).ToUpper() }
function Running-In($dir) { [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dir + "\", [StringComparison]::OrdinalIgnoreCase) }) }
function Title-Of($p) { $p.Refresh(); if ($p.MainWindowTitle) { return $p.MainWindowTitle }; $f = Join-Path $Tools "live\title-$($p.Id).txt"; if (Test-Path -LiteralPath $f) { return [string](Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) }; "" }

function Write-Md($m) {
    $gpus = @("RTX 4070 SUPER") + @($Lanes.Keys)
    $lines = @("# Games by GPU", "", "Each lane tests a game that runs for $Seconds s on one GPU. The RTX 4070 SUPER column is the pipeline's own last result. Updated $(Get-Date -Format 'yyyy-MM-dd HH:mm').", "", "| Game | " + ($gpus -join " | ") + " |", "|---|" + (($gpus | ForEach-Object { "---" }) -join "|") + "|")
    $state = try { Get-Content -LiteralPath (Join-Path $Tools "auto-state.json") -Raw -Encoding utf8 | ConvertFrom-Json } catch { $null }
    foreach ($game in ($m.PSObject.Properties.Name | Sort-Object)) {
        $row = @($game)
        $main = if ($state -and $state.$game) { "$($state.$game.note)" } else { "" }
        $row += $(if ($main -match 'FPS: ([0-9.]+)') { "runs, $($Matches[1]) FPS" } elseif ($main) { ($main -split '\|')[0].Trim() } else { "" })
        foreach ($g in $Lanes.Keys) { $r = $m.$game.$g; $row += $(if ($r) { "$($r.result) ($($r.at.Substring(5, 5)))" } else { "" }) }
        $lines += "| " + (($row | ForEach-Object { "$_" -replace '\|', '/' }) -join " | ") + " |"
    }
    Set-Content -LiteralPath $MatrixMd -Value $lines -Encoding utf8
}

function Pick($gpuName, $m, $taken) {
    $state = try { Get-Content -LiteralPath (Join-Path $Tools "auto-state.json") -Raw -Encoding utf8 | ConvertFrom-Json } catch { return $null }
    $cands = foreach ($prop in $state.PSObject.Properties) {
        $e = $prop.Value
        if ($e.status -ne "done" -or "$($e.note)" -notlike "runs:*" -or -not $e.gameDir -or -not (Test-Path -LiteralPath (Join-Path $e.gameDir "game.json"))) { continue }
        if ($taken -contains $e.gameDir -or $script:LaneProblems.ContainsKey($e.gameDir) -or (Running-In $e.gameDir)) { continue }
        $last = $m.($prop.Name).$gpuName
        $changed = $last -and @(Get-ChildItem -LiteralPath $e.gameDir -File -ErrorAction SilentlyContinue | Where-Object { ($_.Extension -eq ".exe" -or $_.Name -eq "game-env.txt") -and $_.LastWriteTime -gt [datetime]$last.at }).Count
        if ($last -and -not $changed -and ((Get-Date) - [datetime]$last.at).TotalDays -lt $RetestDays) { continue }
        [pscustomobject]@{ name = $prop.Name; dir = $e.gameDir; drive = Real-Drive $e.gameDir; tested = [bool]$last }
    }
    $cands | Sort-Object @{ e = { $_.tested } }, @{ e = { if ($_.drive -eq "D") { 1 } else { 0 } } }, name | Select-Object -First 1
}

# 11-07 Blu: "give the GPU lanes PR tests". When no game is due, a lane reruns a PR test the main pipeline made
# (auto-games.ps1 keeps each one in live\pr-jobs\<game label time>\: the PR's .prx files + job.json). The PR's libraries
# go into the game's libs folder for the run and the game's own come back afterwards; the result goes into job.json
# and _reports\pr-tests.md. Jobs older than 3 days or already run on this GPU are skipped.
$PrJobs = Join-Path $Tools "live\pr-jobs"
function Pick-PrJob($gpuName, $taken) {
    foreach ($d in @(Get-ChildItem -LiteralPath $PrJobs -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {
        $jf = Join-Path $d.FullName "job.json"
        if (-not (Test-Path -LiteralPath $jf) -or ((Get-Date) - $d.LastWriteTime).TotalDays -gt 3) { continue }
        $job = try { Get-Content -LiteralPath $jf -Raw | ConvertFrom-Json } catch { continue }
        if ($job.results -and $job.results.PSObject.Properties[$gpuName]) { continue }
        if (-not (Test-Path -LiteralPath (Join-Path $job.gameDir "game.json")) -or $taken -contains $job.gameDir -or (Running-In $job.gameDir)) { continue }
        if (@(Get-ChildItem -LiteralPath (Join-Path $job.gameDir "libs") -Filter "*.lane-original" -ErrorAction SilentlyContinue).Count) { continue }
        return [pscustomobject]@{ dir = $d.FullName; job = $job; game = [pscustomobject]@{ name = "$(Split-Path $job.gameDir -Leaf) [$($job.label)]"; dir = $job.gameDir; drive = Real-Drive $job.gameDir } }
    }
    $null
}
function Swap-PrLibs($pick) {
    $swapped = @()
    foreach ($lib in @($pick.job.libs)) {
        $src = Join-Path $pick.dir "$lib.prx"; $dst = Join-Path $pick.job.gameDir "libs\$lib.prx"
        if (-not (Test-Path -LiteralPath $src)) { continue }
        $had = Test-Path -LiteralPath $dst
        if ($had) { Copy-Item -LiteralPath $dst -Destination "$dst.lane-original" -Force }
        Copy-Item -LiteralPath $src -Destination $dst -Force
        $swapped += [pscustomobject]@{ path = $dst; had = $had }
    }
    $swapped
}
function Restore-PrLibs($swapped) {
    # 11-11 Divinity kept eight PR #10492 libraries: a copy back failed (the game just killed still held them) and the
    # backup was deleted anyway. Each file now retries for 30 s and its backup goes only after it is back; game-guard.ps1
    # puts back whatever is still wrong.
    foreach ($s in $swapped) {
        for ($try = 0; $try -lt 10; $try++) {
            try {
                if ($s.had) { Copy-Item -LiteralPath "$($s.path).lane-original" -Destination $s.path -Force -ErrorAction Stop; Remove-Item -LiteralPath "$($s.path).lane-original" -Force -ErrorAction SilentlyContinue }
                else { Remove-Item -LiteralPath $s.path -Force -ErrorAction Stop }
                break
            } catch { Start-Sleep -Seconds 3 }
        }
    }
    $dirs = @($swapped | ForEach-Object { Split-Path (Split-Path $_.path) } | Select-Object -Unique)
    foreach ($d in $dirs) { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "game-guard.ps1") -GameDir $d | Out-Null }
}
# A lane stopped mid-run leaves the PR's libraries in a game: put the game's own back first.
foreach ($leftover in @(Get-ChildItem -LiteralPath $PrJobs -Filter job.json -Recurse -ErrorAction SilentlyContinue)) {
    $j = try { Get-Content -LiteralPath $leftover.FullName -Raw | ConvertFrom-Json } catch { continue }
    foreach ($o in @(Get-ChildItem -LiteralPath (Join-Path $j.gameDir "libs") -Filter "*.lane-original" -ErrorAction SilentlyContinue)) {
        Copy-Item -LiteralPath $o.FullName -Destination ($o.FullName -replace '\.lane-original$', '') -Force; Remove-Item -LiteralPath $o.FullName -Force
    }
}
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "game-guard.ps1") -All | Out-Null

function Start-Lane($gpuName, $match, $game) {
    $cfg = Get-Content -LiteralPath (Join-Path $game.dir "game.json") -Raw | ConvertFrom-Json
    $exe = Join-Path $game.dir ("{0}.exe" -f $(if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }))
    if (-not (Test-Path -LiteralPath $exe)) { return $null }
    $logFile = Join-Path $LaneDir ("{0}-{1}.log" -f $match, ($game.name -replace '[^A-Za-z0-9]+', '_'))
    Remove-Item -LiteralPath $logFile -ErrorAction SilentlyContinue
    $saved = @{}
    $tmp = Join-Path $LaneDir "tmp-$match"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $envs = @{ ANYPS5_GPU = $match; ANYPS5_LOG_FILE = $logFile; TMP = $tmp; TEMP = $tmp }
    # Blu 11-07: every test, the lanes too, gets its own Claude tester. Its buttons go through the game's controller
    # command file, so three games can be played at once while Blu uses the PC (ai-player.py --pad-file).
    $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
    $ai = [bool]($app -and $app.aiPlayerBrain -eq "claude")
    $pad = Join-Path $LaneDir "pad-$match.txt"
    if ($ai) { Remove-Item -LiteralPath $pad -ErrorAction SilentlyContinue; $envs["APS5_PAD_INPUT_FILE"] = $pad; $envs["ANYPS5_INPUT_CONFIG"] = Join-Path $Tools "ai-input.ini" }
    $own = Join-Path $game.dir "game-env.txt"
    if (Test-Path -LiteralPath $own) { foreach ($l in Get-Content -LiteralPath $own) { $i = $l.IndexOf('='); if ($i -gt 0) { $envs[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1).Trim() } } }
    # Blu 11-15: PR #9508 drew Octopath Traveler II's title upside down at 4 FPS on this lane and it said "runs". AnyPS5
    # itself saves 16 real frames (one every 900 presented); Finish-Lane has frame_check.py judge them.
    Get-ChildItem -LiteralPath $game.dir -Filter "frame_*.bmp" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    if (-not $envs.ContainsKey("APS5_DUMP_FRAMES")) { $envs["APS5_DUMP_FRAMES"] = "16"; $envs["APS5_DUMP_FRAMES_EVERY"] = "900" }
    foreach ($k in $envs.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $envs[$k]) }
    try { $p = & (Join-Path $PSScriptRoot "start-game.ps1") -Exe $exe -Dir $game.dir } catch { $p = $null }
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    if (-not $p) { return $null }
    $null = $p.Handle
    @{ pid = $p.Id; game = $game.name; dir = $game.dir; gpu = $gpuName; started = (Get-Date -Format o) } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $LaneDir "$match.json") -Encoding utf8
    Say "$gpuName lane: testing $($game.name) (process $($p.Id), from $($game.drive):)"
    @{ proc = $p; game = $game; gpu = $gpuName; match = $match; log = $logFile; start = Get-Date; best = ""; placed = $false; ai = $ai; pad = $pad; player = $null; fps = (New-Object System.Collections.ArrayList) }
}

function Finish-Lane($run, $m) {
    $p = $run.proc
    $title = Title-Of $p
    if ($title -match 'FPS: [0-9.]+ \([0-9]+\)') { $run.best = $title }
    $secs = [int]((Get-Date) - $run.start).TotalSeconds
    if ($p.HasExited) {
        $why = (Select-String -LiteralPath $run.log -Pattern '^FATAL|what\(\):|not implemented' -ErrorAction SilentlyContinue | Select-Object -First 1).Line
        $result = "exited 0x{0:X8} after {1} s{2}" -f $p.ExitCode, $secs, $(if ($why) { ": " + $why.Substring(0, [Math]::Min(120, $why.Length)) } else { "" })
        if ($p.ExitCode -eq -1073741511) { $result = "libraries do not match this build (entry point not found, 0xC0000139) after $secs s" }
    } elseif ($run.best -match 'FPS: ([0-9.]+) \(([0-9]+)\)') {
        $result = if ([int]$Matches[2] -ge 600) { "runs, $($Matches[1]) FPS" } else { "slow start, $($Matches[1]) FPS, $($Matches[2]) frames" }
    } else { $result = "no frame in $secs s" }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; try { [void]$p.WaitForExit(15000) } catch { } }
    # Blu 11-15: "runs" also needs a real picture. All saved frames one flat colour is "blank"; Claude (the bridge's
    # Haiku "look", 80 a day) looks at the newest real frames and BROKEN (upside down, garbled) is "looks broken".
    # Neither counts as runs. Falling under 12 FPS for the last minute after reaching 30 keeps "runs" with a note.
    $label = if ($run.game.name -match '\[([^\]]+)\]$') { $Matches[1] } else { "lane" }
    $fcArgs = @("-I", (Join-Path $Tools "assistant\frame_check.py"), $run.game.dir, (Join-Path $run.game.dir "test-shots"), ("{0:yyyy-MM-dd-HHmm}-{1}-{2}" -f $run.start, $label, $run.match))
    if ($result -like "runs,*") { $fcArgs += "--look" }
    $fc = try { (& python @fcArgs 2>$null | Select-Object -Last 1) | ConvertFrom-Json } catch { $null }
    if ($fc -and $result -like "runs,*") {
        if ($fc.all_flat -and $fc.frames -ge 3) { $result = "blank: every frame AnyPS5 saved was $(@($fc.colours) -join ' or ') ($($fc.frames) frames) | was $result" }
        elseif ($fc.look -eq "broken") { $result = "looks broken: $($fc.answer) (Claude looked at the frames AnyPS5 saved, $($fc.newest)) | was $result" }
        elseif ($fc.frames -ge 6 -and $fc.same_last -ge [math]::Max(6, $fc.frames * 3 / 4)) { $result = "$result (still screen: the last $($fc.same_last) of $($fc.frames) saved frames are the same picture, no input was sent)" }
    }
    if ($result -like "runs,*" -and $run.fps.Count -ge 5) {
        $end = $run.fps[$run.fps.Count - 1][0]
        $late = @($run.fps | Where-Object { $_[0] -ge $end.AddSeconds(-60) } | ForEach-Object { $_[1] } | Sort-Object)
        $peak = ($run.fps | ForEach-Object { $_[1] } | Measure-Object -Maximum).Maximum
        if ($late.Count -ge 5) { $median = $late[[int][Math]::Floor($late.Count / 2)]; if ($median -lt 12 -and $peak -ge 30) { $result += " | slow at the end: {0:N1} FPS for its last minute after {1:N0} FPS earlier" -f $median, $peak } }
    }
    if ($run.player) { try { [void]$run.player.WaitForExit(150000) } catch { }; if (-not $run.player.HasExited) { Stop-Process -Id $run.player.Id -Force -ErrorAction SilentlyContinue } }
    Remove-Item -LiteralPath (Join-Path $LaneDir "$($run.match).json") -ErrorAction SilentlyContinue
    if ($run.prJob) {
        Restore-PrLibs $run.prJob.swapped
        $jf = Join-Path $run.prJob.dir "job.json"
        $job = Get-Content -LiteralPath $jf -Raw | ConvertFrom-Json
        if (-not $job.results) { $job | Add-Member -NotePropertyName results -NotePropertyValue ([pscustomobject]@{}) -Force }
        $job.results | Add-Member -NotePropertyName $run.gpu -NotePropertyValue ([pscustomobject]@{ at = (Get-Date -Format "yyyy-MM-dd HH:mm"); result = $result }) -Force
        $job | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $jf -Encoding utf8
        Add-Content -LiteralPath (Join-Path (Split-Path $Tools) "_reports\pr-tests.md") -Value "- $(Get-Date -Format 'MM-dd HH:mm') $(Split-Path $job.gameDir -Leaf): $($job.label) on the $($run.gpu) lane: $result (main GPU: $($job.main))" -Encoding utf8
        Say "$($run.gpu) lane: PR test $($run.game.name): $result"
        return
    }
    if ($result -match 'named shared-memory directory|unsupported output extent') {
        $script:LaneProblems[$run.game.dir] = $true
        Say "$($run.gpu) lane: $($run.game.name): LANE PROBLEM, not recorded as the game's result: $result"
        return
    }
    $entry = [pscustomobject]@{ at = (Get-Date -Format "yyyy-MM-dd HH:mm"); result = $result; seconds = $secs }
    if (-not $m.PSObject.Properties[$run.game.name]) { $m | Add-Member -NotePropertyName $run.game.name -NotePropertyValue ([pscustomobject]@{}) }
    $m.($run.game.name) | Add-Member -NotePropertyName $run.gpu -NotePropertyValue $entry -Force
    Say "$($run.gpu) lane: $($run.game.name): $result"
}

Say "GPU lanes started (lanes: $(($Lanes.Keys) -join ', '); $Seconds s per test)"
$ScriptLoaded = (Get-Item -LiteralPath $PSCommandPath).LastWriteTime
$runs = @{}
$script:LaneProblems = @{}
while ($true) {
    $m = Load-Matrix
    foreach ($gpuName in @($runs.Keys)) {
        $run = $runs[$gpuName]
        $run.proc.Refresh()
        Place $run
        $t = Title-Of $run.proc
        if ($t -match 'FPS: ([0-9.]+) \([0-9]+\)') {
            [void]$run.fps.Add(@((Get-Date), [double]$Matches[1]))
            $run.best = $t
            if ($run.ai -and -not $run.player) {
                $run.player = Start-Process python -ArgumentList "-I", "`"$(Join-Path $Tools 'ai-player.py')`"", "`"$($run.game.dir)`"", "--attach", $run.proc.Id, "--brain", "claude", "--minutes", 0, "--pad-file", "`"$($run.pad)`"" -WindowStyle Hidden -PassThru
                Say "$gpuName lane: $($run.game.name): its own Claude plays it until it is done (ai-player, process $($run.player.Id))"
            }
        }
        $playing = $run.player -and -not $run.player.HasExited
        $age = ((Get-Date) - $run.start).TotalSeconds
        if ($run.proc.HasExited -or (-not $playing -and $age -ge $Seconds) -or $age -ge 7500) {
            Finish-Lane $run $m
            try { $run.proc.Dispose() } catch { }
            if ($run.player) { try { $run.player.Dispose() } catch { } }
            $runs.Remove($gpuName)
            $m | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Matrix -Encoding utf8
            Write-Md $m
        }
    }
    Place-Main
    # A changed gpu-lanes.ps1 takes effect between tests: with no lane running it exits and the pipeline watchdog starts
    # the new one (it only does if the new one parses).
    if ($runs.Count -eq 0 -and (Get-Item -LiteralPath $PSCommandPath).LastWriteTime -gt $ScriptLoaded) {
        $pe = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$null, [ref]$pe)
        if (-not $pe) { Say "gpu-lanes.ps1 changed; exiting between tests so the new version starts"; exit 0 }
    }
    $why = if (Test-Path -LiteralPath (Join-Path $Tools "pause.flag")) { "pause.flag: " + "$(Get-Content -LiteralPath (Join-Path $Tools 'pause.flag') -Raw -ErrorAction SilentlyContinue)".Trim() } elseif (Get-Process -Name "RocketLeague" -ErrorAction SilentlyContinue) { "Rocket League is running" } else { "" }
    $script:BlockedTicks = if ($why) { [int]$script:BlockedTicks + 1 } else { 0 }
    $blocked = [bool]$why
    $yield = Get-Item -LiteralPath (Join-Path $LaneDir "yield.flag") -ErrorAction SilentlyContinue
    $yielding = $yield -and ((Get-Date) - $yield.LastWriteTime).TotalMinutes -lt 10
    if (-not $blocked -and -not $yielding -and -not (Pc-Wanted)) {
        foreach ($gpuName in $Lanes.Keys) {
            if ($runs.ContainsKey($gpuName)) { continue }
            if ((Free-GB) -lt $MinFreeGB) { break }
            if ($Lanes[$gpuName] -eq "Radeon" -and (Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue)) { continue }
            $taken = @($runs.Values | ForEach-Object { $_.game.dir })
            $game = Pick $gpuName $m $taken
            $prPick = $null
            if (-not $game) {
                $prPick = Pick-PrJob $gpuName $taken
                if (-not $prPick) { continue }
                $game = $prPick.game
                $swapped = @(Swap-PrLibs $prPick)
            }
            $run = Start-Lane $gpuName $Lanes[$gpuName] $game
            if ($prPick) {
                if ($run) { $run.prJob = @{ dir = $prPick.dir; swapped = $swapped } } else { Restore-PrLibs $swapped }
            }
            if ($run) { $runs[$gpuName] = $run; Start-Sleep 20 }
        }
    } elseif ($runs.Count -and $script:BlockedTicks -ge 3) {
        foreach ($gpuName in @($runs.Keys)) {
            $r = $runs[$gpuName]
            if (-not $r.proc.HasExited) { Stop-Process -Id $r.proc.Id -Force -ErrorAction SilentlyContinue }
            Remove-Item -LiteralPath (Join-Path $LaneDir "$($r.match).json") -ErrorAction SilentlyContinue
            Say "$gpuName lane: stopped $($r.game.name) unrecorded ($why)"
            $runs.Remove($gpuName)
        }
    }
    Start-Sleep 15
}

