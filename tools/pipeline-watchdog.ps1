# Watchdog for auto-games.ps1, run every 5 minutes by its own scheduled task so it still works when the pipeline is
# frozen. It never touches a game Blu runs himself and never deletes anything.
#  - pipeline not running: starts it.
#  - a program the pipeline started (git, gh, ninja, the relinker, an extraction, a test launch) that has used no CPU
#    and done no disk or network I/O for 30 minutes is frozen: it is stopped, and the pipeline's own error handling
#    records that step as failed and moves on.
#  - the pipeline wrote nothing to its trace for 60 minutes and runs nothing: its script is stuck; it is restarted
#    (on start it puts back libraries a stopped PR test left in a game).
#  - the nightly AnyPS5 build: a state file without a commit is restored from the newest good copy, and a failed
#    run is reported once per run, so a stale AnyPS5 never goes unnoticed again (10-28 to 11-03 it did).
# Everything it does is written to the pipeline's trace and log as "EVENT WATCHDOG ...", which the live console shows.
# -DryRun only reports (tests use it with small limits).
param([switch]$DryRun, [double]$FrozenMinutes = 30, [double]$SilentMinutes = 60)
$ErrorActionPreference = "Stop"
$Tools = $PSScriptRoot
$Trace = Join-Path $Tools "auto-games-trace.log"
$LogPath = Join-Path $Tools "auto-games.log"
$Memory = Join-Path $Tools "watchdog-state.json"
$Task = "AnyPS5 Games pipeline"
$Health = "D:\AnyPS5\scratchpad\helpers\health"

function Note([string]$text) {
    $line = "WATCHDOG $text"
    if ($DryRun) { "DRY RUN: $line"; return }
    try { [IO.File]::AppendAllText($Trace, ("{0}  EVENT {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $line), [Text.Encoding]::UTF8) } catch { }
    try { [IO.File]::AppendAllText($LogPath, ("{0}  EVENT {1}`r`n" -f (Get-Date -Format "MM-dd HH:mm:ss"), $line), [Text.Encoding]::UTF8) } catch { }
}

$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\AnyPS5PipelineWatchdog", [ref]$created)
if (-not $created) { exit 0 }

$now = Get-Date
$seen = @{}
if (Test-Path -LiteralPath $Memory) { try { (Get-Content -LiteralPath $Memory -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $seen[$_.Name] = $_.Value } } catch { } }
$keep = @{}

$all = @(Get-CimInstance Win32_Process)
$pipeline = @($all | Where-Object { $_.Name -eq "powershell.exe" -and $_.CommandLine -match 'auto-games\.ps1' })
# Under heavy load the process list can come back without command lines (11-03 06:48 it reported a running pipeline
# as stopped); a trace written in the last 5 minutes means it runs.
$traceAge = if (Test-Path -LiteralPath $Trace) { ($now - (Get-Item -LiteralPath $Trace).LastWriteTime).TotalMinutes } else { 999 }

if (-not $pipeline -and $traceAge -lt 5) {
    $keep["down"] = $null
} elseif (-not $pipeline) {
    # pipeline_doctor.py puts back a script that no longer parses and a lost state file before it starts it. A
    # script that exits by itself to load a new version is started by its scheduled task, so give that 10 minutes.
    $exited = $seen["down"]; $since = if ($exited -and $exited.since) { [datetime]$exited.since } else { $now }
    $keep["down"] = [pscustomobject]@{ since = $since.ToString("s") }
    if (($now - $since).TotalMinutes -ge 10) {
        if ($DryRun) { Note "the pipeline is not running; pipeline_doctor.py would start it" }
        else {
            $doctor = & python -I (Join-Path $Tools "pipeline_doctor.py") 2>&1 | Select-Object -Last 1
            $fixed = try { ("$doctor" | ConvertFrom-Json).fixed } catch { @("pipeline_doctor.py said: $doctor") }
            foreach ($f in @($fixed)) { if ($f) { Note $f } }
        }
    }
} else {
    $children = @{}
    foreach ($p in $all) { if (-not $children.ContainsKey([int]$p.ParentProcessId)) { $children[[int]$p.ParentProcessId] = @() }; $children[[int]$p.ParentProcessId] += $p }
    $tree = @(); $queue = [Collections.Queue]::new(); $pipeline | ForEach-Object { $queue.Enqueue([int]$_.ProcessId) }
    while ($queue.Count) {
        $id = $queue.Dequeue()
        foreach ($c in @($children[$id])) { if ($c -and $c.Name -ne "conhost.exe" -and $c.CreationDate -ge ($all | Where-Object ProcessId -eq $id).CreationDate) { $tree += $c; $queue.Enqueue([int]$c.ProcessId) } }
    }
    $frozen = @()
    foreach ($c in $tree) {
        $key = "$($c.ProcessId)|$($c.CreationDate.ToString('s'))"
        $work = "$([uint64]$c.KernelModeTime + [uint64]$c.UserModeTime)/$([uint64]$c.ReadTransferCount + [uint64]$c.WriteTransferCount + [uint64]$c.OtherTransferCount)"
        $since = if ($seen.ContainsKey($key) -and $seen[$key].work -eq $work) { [datetime]$seen[$key].since } else { $now }
        $keep[$key] = [pscustomobject]@{ work = $work; since = $since.ToString("s"); name = $c.Name }
        if (($now - $since).TotalMinutes -ge $FrozenMinutes) { $frozen += [pscustomobject]@{ p = $c; minutes = [int]($now - $since).TotalMinutes } }
    }
    # Stop the innermost frozen programs: a frozen parent whose child still works is only waiting for it.
    $working = @($tree | Where-Object { $k = "$($_.ProcessId)|$($_.CreationDate.ToString('s'))"; ($now - [datetime]$keep[$k].since).TotalMinutes -lt $FrozenMinutes } | ForEach-Object { [int]$_.ParentProcessId })
    # 11-12: while Blu has paused everything (_tools\pause.flag) the pipeline's processes are frozen on purpose and its
    # trace is quiet; none of that is stuck, so nothing is stopped or restarted.
    $pausedNow = Test-Path -LiteralPath (Join-Path $Tools "pause.flag")
    if ($pausedNow) { $frozen = @() }
    foreach ($f in $frozen) {
        if ([int]$f.p.ProcessId -in $working) { continue }
        $cmd = "$($f.p.CommandLine)" -replace '(?<=\s)-p\S*', '-p***'
        Note ("stopped frozen {0} (pid {1}): no CPU or I/O for {2} min: {3}" -f $f.p.Name, $f.p.ProcessId, $f.minutes, $cmd.Substring(0, [Math]::Min(160, $cmd.Length)))
        if (-not $DryRun) { Stop-Process -Id $f.p.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    $quiet = if (Test-Path -LiteralPath $Trace) { ($now - (Get-Item -LiteralPath $Trace).LastWriteTime).TotalMinutes } else { 0 }
    if ($quiet -ge $SilentMinutes -and -not $tree -and -not $pausedNow) {
        Note ("restarted the pipeline: nothing in its trace for {0:N0} min and no program running under it" -f $quiet)
        if (-not $DryRun) {
            $pipeline | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep 5
            Start-ScheduledTask -TaskName $Task
        }
    }
}

# Nightly AnyPS5 build.
$state = Join-Path $Health "state-latest.json"
$sha = try { "$((Get-Content -LiteralPath $state -Raw | ConvertFrom-Json).sha)" } catch { "" }
if ((Test-Path -LiteralPath $state) -and -not $sha) {
    $good = Get-ChildItem -LiteralPath $Health -Filter "state-20*.json" | Sort-Object Name -Descending | Where-Object { try { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).sha } catch { $false } } | Select-Object -First 1
    if ($good) {
        Note "the nightly build's state-latest.json had no commit (overwritten?); restored it from $($good.Name)"
        if (-not $DryRun) { Copy-Item -LiteralPath $state -Destination "$state.bad-$($now.ToString('MMdd-HHmm'))"; Copy-Item -LiteralPath $good.FullName -Destination $state -Force }
    }
}
$info = Get-ScheduledTaskInfo -TaskName "AnyPS5 nightly health" -ErrorAction SilentlyContinue
$run = if ($info) { $info.LastRunTime.ToString("s") } else { "" }
if ($info -and $info.LastTaskResult -notin 0, 267009 -and $seen["nightly"].run -ne $run) {
    $why = Get-Content -LiteralPath (Join-Path $Health "last-run-output.txt") -Tail 40 -ErrorAction SilentlyContinue | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*(\+|At |CategoryInfo|FullyQualifiedErrorId|~)' } | Select-Object -Last 1
    Note "the nightly AnyPS5 build of $run failed (result $($info.LastTaskResult)): $("$why".Trim()); games stay on the AnyPS5 they have until a run succeeds"
}
$keep["nightly"] = [pscustomobject]@{ run = $run }

# Once an hour: the local AI's pipeline_checkup (free). Problems it still finds an hour later go to Claude Code
# through pipeline-repair-agent.ps1, which keeps that to a few runs a day.
$lastCheck = if ($seen["checkup"]) { [datetime]$seen["checkup"].at } else { [datetime]::MinValue }
$keep["checkup"] = [pscustomobject]@{ at = $lastCheck.ToString("s") }
if (($now - $lastCheck).TotalMinutes -ge 60) {
    $keep["checkup"].at = $now.ToString("s")
    $checkup = & python -I -c "import sys; sys.path.insert(0, r'$Tools\assistant'); import work_skills as w; print(w.pipeline_checkup())" 2>&1 | Out-String
    & (Join-Path $Tools "pipeline-repair-agent.ps1") -Checkup $checkup -DryRun:$DryRun
}
# Once a day after 20:00, when Settings has "Claude Code teaches the local AI" on: Sonnet reviews the day's chats and
# writes lessons into playbooks.md (claude_bridge.py coach keeps a copy of playbooks.md first).
$coachDay = if ($seen["coach"]) { "$($seen["coach"].day)" } else { "" }
$keep["coach"] = [pscustomobject]@{ day = $coachDay }
$appSettings = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
if ($appSettings.claudeCoach -eq $true -and $now.Hour -ge 20 -and $coachDay -ne $now.ToString("yyyy-MM-dd")) {
    $keep["coach"].day = $now.ToString("yyyy-MM-dd")
    Note "Claude Code is teaching the local AI from today's chats (lessons go into playbooks.md)"
    if (-not $DryRun) { Start-Process -FilePath python -ArgumentList '-I', ('"' + (Join-Path $Tools "assistant\claude_bridge.py") + '"'), 'coach' -WindowStyle Hidden }
}
# Blu 11-04: the members work on their own, then come back together. Once a day after 09:00 the Ambassador (Opus)
# goes through Blu's open pull requests and drafts replies (it never pushes or posts); once a day after 21:00 the
# Round Table: the boss reads everyone's results and the whole team works the one problem best solved together.
# Settings can turn either off (claudePr / claudeCouncil = false).
foreach ($daily in @(@{ key = "pr"; hour = 9; on = ($appSettings.claudePr -ne $false); say = "The Ambassador is going through Blu's open pull requests (drafts only)" },
                     @{ key = "council"; hour = 21; on = ($appSettings.claudeCouncil -ne $false); say = "The AI team's Round Table: the boss picks one problem for the whole team" },
                     @{ key = "stops"; hour = 20; on = ($appSettings.claudeBoss -ne $false); say = "The boss judges today's Watcher stops (right +2, wrong -1)" })) {
    $last = if ($seen[$daily.key]) { "$($seen[$daily.key].day)" } else { "" }
    $keep[$daily.key] = [pscustomobject]@{ day = $last }
    if ($daily.on -and $now.Hour -ge $daily.hour -and $last -ne $now.ToString("yyyy-MM-dd")) {
        $keep[$daily.key].day = $now.ToString("yyyy-MM-dd")
        Note $daily.say
        if (-not $DryRun) { Start-Process -FilePath python -ArgumentList '-I', ('"' + (Join-Path $Tools "assistant\claude_bridge.py") + '"'), $daily.key -WindowStyle Hidden }
    }
}
# 11-07: the GPU lanes exit after yielding to the pipeline and nothing started them again (the 1650 and the Radeon sat
# idle 02:00-13:22 and 14:08-17:55). gpu-lanes.ps1 checks yield.flag itself, so starting it while the pipeline has the
# GPU only makes it wait.
# 11-12: while Blu plays, the lanes task is disabled; Start-ScheduledTask then fails silently and this logged a false
# "started them again" every 5 minutes. A disabled task means paused on purpose: leave it.
$lanesTask = Get-ScheduledTask -TaskName "AnyPS5 GPU lanes" -ErrorAction SilentlyContinue
if (-not $DryRun -and $lanesTask -and $lanesTask.State -ne 'Disabled' -and -not (Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -match 'gpu-lanes\.ps1' })) {
    Start-ScheduledTask -TaskName "AnyPS5 GPU lanes" -ErrorAction SilentlyContinue
    Note "GPU lanes were not running; started them again"
}
# The living realm: every run moves the AI team through the kingdom from what they really do (realm_sim.py writes
# repair-runs\realm-sim.json; free, no model call).
# 11-09: a crash in it (two damaged brain files since 11-08 13:47) stopped this whole script here under
# ErrorActionPreference Stop, before the state below was saved, so the daily jobs started again every 5 minutes.
if (-not $DryRun) { try { & python -I (Join-Path $Tools "assistant\realm_sim.py") *> $null } catch { } }
# 11-11 Blu: a game started from its own .exe must also be on its own build: every pass puts back libraries a PR test
# left in a game (game-guard.ps1 skips games that are running or in a test).
if (-not $DryRun) { try { & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "game-guard.ps1") -All *> $null } catch { } }
# Blu 11-04: the team really talks to each other (talk.py): every pass up to three teammates answer the messages that
# have waited longest, each on its own model with read-only look-ups. Uncapped spending (Blu), Settings claudeTalk=false
# stops it. Hidden and not waited for, so the watchdog's pass stays short.
if (-not $DryRun -and $appSettings.claudeTalk -ne $false) { Start-Process -FilePath python -ArgumentList '-I', ('"' + (Join-Path $Tools "assistant\claude_bridge.py") + '"'), 'talk', '3' -WindowStyle Hidden }
# A finished repair run: its first line goes to the live console.
$announced = @(if ($seen["reports"]) { $seen["reports"].names })
$reports = @(Get-ChildItem -LiteralPath (Join-Path $Tools "repair-runs") -Filter "*-report.md" -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 0 -and $_.Name -notin $announced })
if ((Get-ScheduledTask -TaskName "AnyPS5 pipeline repair" -ErrorAction SilentlyContinue).State -ne "Running") {
    foreach ($r in $reports) {
        $job = if ($r.Name -match '-raid-') { "team raid" } elseif ($r.Name -match '-exp-') { "experiment" } elseif ($r.Name -match '-fix-') { "fix" } elseif ($r.Name -match '-build-') { "build" } elseif ($r.Name -match '-coach') { "teaching" } else { "repair" }
        $lines = @(Get-Content -LiteralPath $r.FullName | Where-Object { $_ -match '\S' })
        $answer = @($lines | Where-Object { $_ -match '^\s*\{|Boss verdict' }) | Select-Object -Last 2
        Note "Claude Code $job finished ($($r.Name)): $(if ($answer) { ($answer -join ' ') } else { $lines | Select-Object -Last 1 })"
        $announced += $r.Name
    }
}
$keep["reports"] = [pscustomobject]@{ names = @($announced | Select-Object -Last 50) }

if (-not $DryRun) { $keep | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Memory -Encoding utf8 }
