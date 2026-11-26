# Daily cleanup of tool-made clutter on the PC, the MacBook and the Chromebook (Blu 11-06: "we need a cleanup process
# for everything ... files just cluttering"). It only removes what tools made and can make again; it never touches game
# dumps, downloads, saves (_sd), mods, game.json or game-env.txt. Deletion is permanent (the Recycle Bin is off), so
# every rule names exactly what it removes, -DryRun lists without deleting, and each run writes
# _reports\cleanup-<date>.md with what went and how much space it freed.
param([switch]$DryRun, [int]$KeepDays = 3, [int]$LogDays = 14)
$ErrorActionPreference = "Continue"
$Tools = $PSScriptRoot
$Games = "D:\AnyPS5 Games"
$Report = Join-Path $Games ("_reports\cleanup-{0}.md" -f (Get-Date -Format "yyyy-MM-dd"))
$lines = New-Object System.Collections.Generic.List[string]
$freed = @{}
function Size($p) { if (Test-Path -LiteralPath $p -PathType Leaf) { return (Get-Item -LiteralPath $p).Length }; (Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }
function Drop($what, $path) {
    $bytes = [double](Size $path)
    if (-not $DryRun) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $path) { $lines.Add("- could not remove $path"); return } }
    $freed[$what] = [double]$freed[$what] + $bytes
    $lines.Add(("- {0}: {1} ({2:N2} GB)" -f $what, $path, ($bytes / 1GB)))
}
$cut = (Get-Date).AddDays(-$KeepDays)
$logCut = (Get-Date).AddDays(-$LogDays)
$gameDirs = @(Get-ChildItem -LiteralPath $Games -Directory | Where-Object { -not $_.Name.StartsWith("_") -and (Test-Path -LiteralPath (Join-Path $_.FullName "game.json")) })

# 1. Shader cache folders of older AnyPS5 builds. Each build writes its own folder (the key is a hash of the compiler's
#    sources), so an older folder is never read again. Kept: the newest folder and any written in the last $KeepDays days
#    (a game pinned to an older build keeps using its folder, which is then its newest).
foreach ($g in $gameDirs + @(Get-ChildItem -LiteralPath (Join-Path $Games "_test") -Directory -ErrorAction SilentlyContinue | ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue })) {
    $sc = Join-Path $g.FullName "shader_cache"
    if (-not (Test-Path -LiteralPath $sc)) { continue }
    $versions = @(Get-ChildItem -LiteralPath $sc -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^[0-9a-f]{16}$' } | Sort-Object LastWriteTime -Descending)
    foreach ($v in $versions | Select-Object -Skip 1) { if ($v.LastWriteTime -lt $cut) { Drop "old shader cache" $v.FullName } }
}

# 2. AnyPS5 builds in _tools that no game uses: kept are the pipeline's current build, any build a game.json pins and
#    the two newest.
$pinned = @($gameDirs | ForEach-Object { try { "$((Get-Content -LiteralPath (Join-Path $_.FullName 'game.json') -Raw | ConvertFrom-Json).anyps5Build)" } catch { "" } } | Where-Object { $_ } | ForEach-Object { Split-Path $_ -Leaf })
$current = try { "anyps5-" + (Get-Content -LiteralPath (Join-Path $Tools "auto-state.json") -Raw | ConvertFrom-Json)."(base)" } catch { "" }
$builds = @(Get-ChildItem -LiteralPath $Tools -Directory -Filter "anyps5-*" | Sort-Object LastWriteTime -Descending)
foreach ($b in $builds | Select-Object -Skip 2) { if ($pinned -notcontains $b.Name -and $b.Name -ne $current -and $b.LastWriteTime -lt $cut) { Drop "unused AnyPS5 build" $b.FullName } }

# 3. Per-game test leftovers older than $LogDays days: crash logs, AI play runs, all but the newest 30 test shots.
foreach ($g in $gameDirs) {
    foreach ($sub in "crash-logs", "ai-runs") {
        Get-ChildItem -LiteralPath (Join-Path $g.FullName $sub) -Force -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old $sub" $_.FullName }
    }
    Get-ChildItem -LiteralPath (Join-Path $g.FullName "test-shots") -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Where-Object { $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old test shot" $_.FullName }
}

# 4. Tool scratch: GPU lane logs, repair-run prompts and reports, extraction logs, old app builds and script backups.
Get-ChildItem -LiteralPath (Join-Path $Tools "live\lanes") -Filter *.log -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cut } | ForEach-Object { Drop "old lane log" $_.FullName }
Get-ChildItem -LiteralPath (Join-Path $Tools "repair-runs") -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(usage\.jsonl|scoreboard\.json|team-notes\.md|world-state\.json)$' -and $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old repair-run file" $_.FullName }
Get-ChildItem -LiteralPath $Tools -Filter "extract-*.log" -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old extraction log" $_.FullName }
Get-ChildItem -LiteralPath (Join-Path $Tools "launcher") -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(exe|cs)\.(before|old|running)' -or $_.Name -match '\.(running-old|preview-running)' } | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | ForEach-Object { Drop "old app build or source backup" $_.FullName }
Get-ChildItem -LiteralPath $Tools -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(ps1|py)\.(before|bak|broken)' -and $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old script backup" $_.FullName }
Get-ChildItem -LiteralPath (Join-Path $Tools "assistant") -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(py|md)\.(before|bak)' -and $_.LastWriteTime -lt $logCut } | ForEach-Object { Drop "old script backup" $_.FullName }

# 5. Git: worktree records whose folder is gone, and the pipeline's automatic "claude-fix leftovers" stashes older
#    than $LogDays days (made by Claude-Fix when it switches branches; no person's work is in them).
if (-not $DryRun) { git -C D:\AnyPS5 worktree prune 2>$null }
$stashes = @(git -C D:\AnyPS5 stash list --format='%gd|%ct|%gs' 2>$null)
foreach ($s in ($stashes | Sort-Object { [int](($_ -split '\|')[0] -replace '\D', '') } -Descending)) {
    $ref, $when, $subject = $s -split '\|', 3
    if ($subject -notmatch 'claude-fix leftovers') { continue }
    if ([DateTimeOffset]::FromUnixTimeSeconds([int64]$when).LocalDateTime -gt $logCut) { continue }
    if (-not $DryRun) { git -C D:\AnyPS5 stash drop $ref 2>$null | Out-Null }
    $lines.Add("- old pipeline stash: $subject")
}

# 6. A known leak: Razer Game Manager Service 3 grew to 8 GB on 11-06 and left the GPU lanes no memory; over 2 GB it is
#    restarted (Windows' own service start, nothing in Razer's settings changes).
$svc = Get-CimInstance Win32_Service | Where-Object { $_.PathName -match 'GameManagerService3' }
if ($svc -and $svc.ProcessId) {
    $p = Get-Process -Id $svc.ProcessId -ErrorAction SilentlyContinue
    if ($p -and $p.PrivateMemorySize64 -gt 2GB) {
        $gb = [math]::Round($p.PrivateMemorySize64 / 1GB, 1)
        if (-not $DryRun) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; Start-Sleep 3; Start-Service -Name $svc.Name -ErrorAction SilentlyContinue }
        $lines.Add("- restarted Razer Game Manager Service 3 (it held $gb GB of memory)")
    }
}

# 7. The MacBook and the Chromebook: PR test worktrees and builds older than $KeepDays days (their reports and logs
#    stay), and logs older than $LogDays days.
$remote = @'
cd ~ 2>/dev/null || exit 0
for d in ~/AnyPS5-wt/pr-*; do [ -d "$d" ] || continue; if [ -n "$(find "$d" -maxdepth 0 -mtime +KEEP)" ]; then du -sk "$d" | awk '{print "SIZE " $1 " " $2}'; [ "DRY" = "1" ] || { git -C ~/AnyPS5-wt/job7-main worktree remove --force "$d" 2>/dev/null || rm -rf "$d"; }; fi; done
for d in ~/mac-work/logs ~/cb-work/logs; do [ -d "$d" ] && find "$d" -type f -mtime +LOGS -print -delete 2>/dev/null | sed 's/^/LOG /' ; done
'@ -replace 'KEEP', $KeepDays -replace 'LOGS', $LogDays -replace 'DRY', $(if ($DryRun) { "1" } else { "0" })
if ($DryRun) { $remote = $remote -replace '-print -delete', '-print' }
foreach ($host_ in "macbook", "chromebook") {
    $out = @($remote | ssh -o ConnectTimeout=15 -o BatchMode=yes $host_ "bash -s" 2>$null)
    foreach ($o in $out) {
        if ($o -match '^SIZE (\d+) (.+)$') { $freed["$host_ PR test folders"] = [double]$freed["$host_ PR test folders"] + [double]$Matches[1] * 1KB; $lines.Add("- ${host_}: PR test folder $($Matches[2]) ($([math]::Round([double]$Matches[1] / 1MB, 2)) GB)") }
        elseif ($o -like "LOG *") { $lines.Add("- ${host_}: old log $($o.Substring(4))") }
    }
}

$total = ($freed.Values | Measure-Object -Sum).Sum
$head = @("# Cleanup $(Get-Date -Format 'yyyy-MM-dd HH:mm')$(if ($DryRun) { ' (dry run: nothing deleted)' })", "", ("Freed {0:N1} GB in total." -f ($total / 1GB)), "")
$head += @($freed.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { "- {0}: {1:N2} GB" -f $_.Key, ($_.Value / 1GB) })
$head += @("", "## Each item")
Set-Content -LiteralPath $Report -Value ($head + $lines) -Encoding utf8
"{0}: {1:N1} GB {2} ({3} items) -> {4}" -f $(if ($DryRun) { "dry run" } else { "cleanup" }), ($total / 1GB), $(if ($DryRun) { "would be freed" } else { "freed" }), $lines.Count, $Report
