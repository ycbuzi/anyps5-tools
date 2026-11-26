# Hands a pipeline problem the watchdog could not fix to Claude Code, unattended (Blu 11-03: "use claude code
# unattended"). The watchdog calls this once an hour with the pipeline_checkup report; it starts Claude Code only when
# the same problems were there an hour earlier too, at most once every 6 hours and 3 times a day, and never while a
# run is still going. Each run's prompt and answer are kept in _tools\repair-runs\, and its first line goes to the
# live console as "EVENT REPAIR ...". Claude Code may change the pipeline's own scripts (with a backup) but never
# deletes, never changes Defender or other security settings, never downloads games, never posts or pushes.
param([string]$Checkup, [switch]$DryRun)
$ErrorActionPreference = "Stop"
$Tools = $PSScriptRoot
$Runs = Join-Path $Tools "repair-runs"
$Memory = Join-Path $Runs "repair-state.json"
$Trace = Join-Path $Tools "auto-games-trace.log"
$LogPath = Join-Path $Tools "auto-games.log"
New-Item -ItemType Directory -Force $Runs | Out-Null

function Note([string]$text) {
    if ($DryRun) { "DRY RUN: REPAIR $text"; return }
    try { [IO.File]::AppendAllText($Trace, ("{0}  EVENT REPAIR {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text), [Text.Encoding]::UTF8) } catch { }
    try { [IO.File]::AppendAllText($LogPath, ("{0}  EVENT REPAIR {1}`r`n" -f (Get-Date -Format "MM-dd HH:mm:ss"), $text), [Text.Encoding]::UTF8) } catch { }
}

# Problems only Blu can settle (his pause or tests-off switch, tests waiting for him to step away) are not sent.
$problems = @(($Checkup -split "`r?`n") | Where-Object { $_ -like "- *" -and $_ -notmatch 'none found|pause\.flag|tests-off\.flag|wait for a test' })
$signature = (($problems | ForEach-Object { $_ -replace '\d+', '#' }) | Sort-Object) -join "`n"
$m = if (Test-Path -LiteralPath $Memory) { Get-Content -LiteralPath $Memory -Raw | ConvertFrom-Json } else { [pscustomobject]@{ signature = ""; seen = ""; runs = @(); pid = 0 } }
$now = Get-Date
$previous = "$($m.signature)"
$m.signature = $signature; $m.seen = $now.ToString("s")
$recent = @(@($m.runs) | Where-Object { $_ -and ($now - [datetime]$_).TotalHours -lt 24 })
$busy = (Get-ScheduledTask -TaskName "AnyPS5 pipeline repair" -ErrorAction SilentlyContinue).State -eq "Running"
$why = if (-not $problems) { "no problems" }
       elseif ($signature -ne $previous) { "new problems; waiting an hour to see whether the watchdog or the pipeline clears them" }
       elseif ($busy) { "a repair run is still going" }
       elseif ($recent.Count -ge 3) { "3 runs in the last 24 hours already" }
       elseif ($recent | Where-Object { ($now - [datetime]$_).TotalHours -lt 6 }) { "a run in the last 6 hours already" }
       else { "" }
if ($why) {
    if (-not $DryRun) { $m | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Memory -Encoding utf8 }
    if ($DryRun) { "DRY RUN: not started: $why" }
    exit 0
}

$claude = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA "Claude-Data") -Recurse -Depth 5 -Filter claude.exe -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like "*\claude-code\*" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $claude) { Note "cannot start Claude Code: no claude.exe under the Claude app's folder"; exit 0 }
$status = & $claude.FullName auth status 2>&1 | Out-String
if ($status -match '"loggedIn":\s*false') {
    if ($m.loginNoted -ne $now.ToString("yyyy-MM-dd")) { Note "cannot start Claude Code: it is not signed in. Blu signs it in once: run '$($claude.FullName)' auth login"; $m | Add-Member -NotePropertyName loginNoted -NotePropertyValue $now.ToString("yyyy-MM-dd") -Force }
    if (-not $DryRun) { $m | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Memory -Encoding utf8 }
    exit 0
}

$stamp = $now.ToString("yyyy-MM-dd-HHmm")
$prompt = @"
You are the repair agent for Blu's AnyPS5 games pipeline, started unattended by its watchdog. Nobody is watching; Blu reads your report later.

The pipeline (D:\AnyPS5 Games\_tools\auto-games.ps1) extracts PS5 dumps, builds them with AnyPS5, tests them, and tests open AnyPS5 PRs on failed games. Its watchdog (pipeline-watchdog.ps1) already restarts it and stops frozen steps; these problems survived that for over an hour:

$($problems -join "`n")

Full checkup report:
$Checkup

Do this:
1. Read D:\AnyPS5 Games\_tools\assistant\playbooks.md, the section "the pipeline is not testing games or finding fixes", and the end of auto-games-trace.log and auto-games.log.
Tokens cost Blu's usage: read only the parts of files you need (search first, read line ranges), do not dump logs.
2. Find the cause and fix it in the pipeline itself so it does not need you next time. Before you change a script, copy it to <name>.bak-$stamp; after, check it parses (PowerShell: [System.Management.Automation.Language.Parser]::ParseFile; Python: python -I -m py_compile). auto-games.ps1 reloads itself after its current pass; do not kill it in the middle of a build, extraction or test.
3. If the fix is a check the local AI could run, add it to D:\AnyPS5 Games\_tools\assistant\work_skills.py pipeline_checkup and the playbook.
4. Run python -I -c "import sys; sys.path.insert(0, r'D:\AnyPS5 Games\_tools\assistant'); import work_skills as w; print(w.pipeline_checkup())" again and say what is left.

Rules, never broken:
- Never delete anything (no rm, del, Remove-Item, Recycle Bin: the Recycle Bins here delete at once). Move a file aside instead.
- Never change Windows Defender, firewall, UAC or any security or system setting; never restore quarantined files.
- Never download games or archives; never type or read passwords, tokens or keys; never print archive passwords or download site names.
- Never post anything: no GitHub comments, PRs, pushes or force-pushes, no Discord.
- Change only D:\AnyPS5 Games\_tools, D:\AnyPS5\scratchpad\helpers and the pipeline's worktrees D:\AnyPS5\wt-pr and D:\AnyPS5\wt-health (git checkout, fetch and submodule commands are fine there; never edit AnyPS5 source files).
- Do not start or stop games Blu runs. Work at most about 30 minutes.

End with a report: first line one sentence on what you found and did; then what changed (files), what you tested and what is still broken.
"@
$promptFile = Join-Path $Runs "$stamp-prompt.md"; $outFile = Join-Path $Runs "$stamp-report.md"
if ($DryRun) { "DRY RUN: would start $($claude.FullName) with $promptFile"; $prompt; exit 0 }
$prompt | Set-Content -LiteralPath $promptFile -Encoding utf8
# The run goes through its own scheduled task ("AnyPS5 pipeline repair", 50-minute limit), so it outlives this
# script and the watchdog's 4-minute limit, and can never run on for hours. claude_bridge.py picks the model
# (Sonnet), caps turns and runs per day, denies deleting, posting and downloading, and logs tokens and cost.
$runner = Join-Path $Runs "current-run.cmd"
"@echo off`r`ncd /d `"$Tools`"`r`npython -I `"$Tools\assistant\claude_bridge.py`" repair `"$promptFile`" `"$outFile`"" | Set-Content -LiteralPath $runner -Encoding ascii
Start-ScheduledTask -TaskName "AnyPS5 pipeline repair"
$m.runs = @($recent) + $now.ToString("s")
$m | Add-Member -NotePropertyName pending -NotePropertyValue $outFile -Force
$m | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Memory -Encoding utf8
Note "started Claude Code on: $(($problems | Select-Object -First 1) -replace '^- ', '') (report: repair-runs\$stamp-report.md)"
