# The job board for the three machines (Blu 11-06: every machine always has work, all of it planned so it works out).
# Runs every 30 minutes as the "AnyPS5 dispatcher" task. Each run:
#   - Chromebook (Linux, no AVX): when its PR runner is idle, gives it the next 3 recent PRs it can build.
#   - MacBook (macOS, AVX only): when its Claude worker is idle and today's cap is not reached, starts the next job,
#     alternating a PR test batch and a watch job (GitHub replies to draft, upstream merges that may fix our game
#     stops, new KytyPS5 commits). Workers never push or post; I review their results first.
#   - Every night: the cleanup (cleanup.ps1) and a backup of the pipeline's records, game settings and saves to the Mac.
#   - Copies every worker result to _reports\workers\ and writes _reports\status.html.
param([int]$MacJobsPerDay = 4)
$ErrorActionPreference = "Continue"
$Tools = $PSScriptRoot
$Games = "D:\AnyPS5 Games"
$Workers = Join-Path $Games "_reports\workers"
$Log = Join-Path $Tools "dispatcher.log"
$StateFile = Join-Path $Tools "live\dispatcher-state.json"
New-Item -ItemType Directory -Force $Workers, (Split-Path $StateFile) | Out-Null
function Say($m) { "$(Get-Date -Format 'MM-dd HH:mm:ss')  $m" | Add-Content -LiteralPath $Log -Encoding utf8 }
function On($h) { $r = ssh -o ConnectTimeout=12 -o BatchMode=yes $h "echo up" 2>$null; "$r" -eq "up" }
$state = if (Test-Path -LiteralPath $StateFile) { try { Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json } catch { $null } } else { $null }
if (-not $state) { $state = [pscustomobject]@{ day = ""; macJobs = 0; macNext = "prtest"; cleanupDay = ""; backupDay = ""; tested = @() } }
$today = Get-Date -Format "yyyy-MM-dd"
if ($state.day -ne $today) { $state.day = $today; $state.macJobs = 0 }

# Open PRs a CPU-only runner can build and test: non-draft, updated in the last 7 days, every changed file under the
# relinker, tools/ or root CMake (the relinker-only build skips the shader recompiler, so a recompiler change would not build). Cached for an hour.
function Candidates {
    $cache = Join-Path $Tools "live\pr-candidates.json"
    if ((Test-Path $cache) -and ((Get-Date) - (Get-Item $cache).LastWriteTime).TotalMinutes -lt 60) { return @((Get-Content $cache -Raw | ConvertFrom-Json) | ForEach-Object { $_ }) }
    $since = (Get-Date).AddDays(-7).ToString("yyyy-MM-dd")
    $json = gh pr list --repo boykopovar/AnyPS5 --state open --limit 300 --search "updated:>=$since draft:false" --json number,headRefOid,files 2>$null
    $prs = @(($json -join "`n" | ConvertFrom-Json) | ForEach-Object { $_ })
    $ok = @($prs | Where-Object { $f = @($_.files.path); $f.Count -and -not @($f | Where-Object { $_ -notmatch '^(core/relinker/|tools/|CMakeLists\.txt$|cmake/)' }).Count } | ForEach-Object { [pscustomobject]@{ number = $_.number; head = $_.headRefOid.Substring(0, 10) } })
    ConvertTo-Json @($ok) | Set-Content $cache -Encoding utf8
    $ok
}
function Untested($machine, $n) {
    $cands = Candidates
    $done = @($state.tested | Where-Object { $_ -like "${machine}:*" })
    @($cands | Where-Object { $done -notcontains "${machine}:$($_.number):$($_.head)" } | Select-Object -First $n)
}
function Mark($machine, $prs) { $state.tested = @($state.tested) + @($prs | ForEach-Object { "${machine}:$($_.number):$($_.head)" }) }

# Chromebook: the PR runner script.
if (On "chromebook") {
    $busy = ssh chromebook "pgrep -f '[c]hromebook_prtest.sh' >/dev/null && echo busy" 2>$null
    if ("$busy" -ne "busy") {
        $next = Untested "chromebook" 3
        if ($next.Count) {
            $nums = ($next.number) -join " "
            ssh chromebook "nohup bash ~/cb-work/chromebook_prtest.sh $nums > ~/cb-work/logs/prtest-run.log 2>&1 < /dev/null &" 2>$null
            Mark "chromebook" $next
            Say "chromebook: PR runner started on $nums"
        }
    }
    scp -q -o BatchMode=yes "chromebook:cb-work/prtests/*.md" "$Workers/" 2>$null
    Get-ChildItem $Workers -Filter "*.md" | Where-Object { $_.Name -match '^\d+\.md$' } | ForEach-Object { Move-Item $_.FullName (Join-Path $Workers ("chromebook-" + $_.Name)) -Force }
} else { Say "chromebook: not reachable (asleep, or its tunnel is down)" }

# MacBook (11-08): offline PR tests with ~/mac-work/mac_prtest.sh, no Claude (Blu: save usage). live\mac-paused.flag holds it.
if ((On "macbook") -and -not (Test-Path (Join-Path $Tools "live\mac-paused.flag"))) {
    $busy = ssh macbook "test -d ~/mac-work/prtest.lock && echo busy" 2>$null
    if ("$busy" -ne "busy") {
        $next = Untested "macbook" 3
        if ($next.Count) {
            $nums = ($next | ForEach-Object { "$($_.number)" }) -join " "
            scp -q (Join-Path $Tools "assistant\mac_prtest.sh") "macbook:mac-work/mac_prtest.sh" 2>$null
            ssh -f macbook "nohup bash ~/mac-work/mac_prtest.sh $nums > ~/mac-work/logs/prtest-last.log 2>&1 < /dev/null &" 2>$null
            Mark "macbook" $next
            Say "macbook: offline PR tests started for $nums"
        }
    }
    scp -q -o BatchMode=yes "macbook:mac-work/prtests/*.md" "$Workers/" 2>$null
    Get-ChildItem $Workers -Filter "*.md" | Where-Object { $_.Name -match '^\d+\.md$' } | ForEach-Object { Move-Item $_.FullName (Join-Path $Workers ("macbook-" + $_.Name)) -Force }
    scp -q -o BatchMode=yes "macbook:mac-work/watch-*.md" "$Workers/" 2>$null
} else { Say "macbook: not reachable" }

# Azure ARM lane (11-08, anyps5-arm): box64 + lavapipe Quest-path checks run on the VM's own hourly timer
# (assistant\azarm-lane\). Here: push the Chromebook's newest Quest build when the VM lacks it, fetch the summary.
if (On "azarm") {
    $newest = ssh -o BatchMode=yes chromebook "cd ~/cb-work/quest-dist 2>/dev/null && ls -1t | while read d; do ls `$d/relinker-v* >/dev/null 2>&1 && ls `$d/prx-linux-v*.tar.gz >/dev/null 2>&1 && { echo `$d; break; }; done" 2>$null
    if ("$newest" -match '^[A-Za-z0-9._-]+$') {
        $has = ssh -o BatchMode=yes azarm "test -d ~/alane/incoming/$newest && echo yes" 2>$null
        if ("$has" -ne "yes") {
            $rl = ssh -o BatchMode=yes chromebook "bash ~/cb-work/azarm/cb-relink.sh $newest 2>&1 | tr '\n' ' '" 2>$null
            cmd /c "ssh -o BatchMode=yes chromebook ""mkdir -p ~/cb-work/azarm-relinked/$newest && tar -cf - -C ~/cb-work/quest-dist $newest -C ~/cb-work/azarm-relinked --exclude=*.log --exclude=*.part $newest"" | ssh -o BatchMode=yes azarm ""mkdir -p ~/alane/incoming/.recv && tar -C ~/alane/incoming/.recv -xf - && mv ~/alane/incoming/.recv/$newest ~/alane/incoming/ ; rm -rf ~/alane/incoming/.recv""" 2>$null
            Say "azarm: pushed Chromebook build $newest (relink: $rl)"
        }
    }
    $cbTests = @(ssh -o BatchMode=yes chromebook "ls -1 ~/cb-work/quest-dist/tests 2>/dev/null" 2>$null)
    $vmTests = @(ssh -o BatchMode=yes azarm "ls -1 ~/alane/tests 2>/dev/null" 2>$null)
    $newTests = @($cbTests | Where-Object { $_ -match '^tests-[0-9a-f]+$' -and $vmTests -notcontains $_ } | Select-Object -First 3)
    if ($newTests.Count) {
        $list = $newTests -join " "
        cmd /c "ssh -o BatchMode=yes chromebook ""tar -C ~/cb-work/quest-dist/tests -cf - $list"" | ssh -o BatchMode=yes azarm ""mkdir -p ~/alane/tests && tar -C ~/alane/tests -xf -""" 2>$null
        Say "azarm: pushed test bundles $list"
    }
    scp -q -o BatchMode=yes "azarm:alane/results.txt" (Join-Path $Workers "azarm-lane.txt") 2>$null
} else { Say "azarm: not reachable" }

$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StateFile -Encoding utf8
# Nightly: cleanup, then a backup to the Mac (after 03:00, once a day).
if ((Get-Date).Hour -ge 3 -and $state.cleanupDay -ne $today) {
    $r = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "cleanup.ps1") 2>&1 | Select-Object -Last 1
    Say "cleanup: $r"; $state.cleanupDay = $today
}
if ((Get-Date).Hour -ge 3 -and $state.backupDay -ne $today -and (On "macbook")) {
    $stage = Join-Path $env:TEMP "anyps5-backup"
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force (Join-Path $stage "pipeline"), (Join-Path $stage "games") | Out-Null
    foreach ($f in "auto-state.json", "app-settings.json", "settings.json", "requests.txt", "archive-passwords.txt", "bug-links.json") { $p = Join-Path $Tools $f; if (Test-Path $p) { Copy-Item $p (Join-Path $stage "pipeline") } }
    foreach ($g in Get-ChildItem $Games -Directory | Where-Object { -not $_.Name.StartsWith("_") -and (Test-Path (Join-Path $_.FullName "game.json")) }) {
        $dst = Join-Path $stage "games\$($g.Name)"
        New-Item -ItemType Directory -Force $dst | Out-Null
        foreach ($f in "game.json", "game-env.txt") { $p = Join-Path $g.FullName $f; if (Test-Path $p) { Copy-Item $p $dst } }
        foreach ($d in "_sd", "_trophy") { $p = Join-Path $g.FullName $d; if (Test-Path $p) { Copy-Item $p $dst -Recurse -ErrorAction SilentlyContinue } }
    }
    $zip = Join-Path $env:TEMP "anyps5-backup-$today.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -CompressionLevel Optimal
    ssh macbook "mkdir -p ~/backups" 2>$null
    scp -q $zip "macbook:backups/" 2>$null
    ssh macbook "ls -t ~/backups/anyps5-backup-*.zip | tail -n +8 | xargs rm -f" 2>$null
    $mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item $zip -ErrorAction SilentlyContinue
    Say "backup: $mb MB to macbook:~/backups (7 kept)"; $state.backupDay = $today
}

# FreeBSD reference probes (Azure VM azbsd: run.sh hourly, repeat.sh loop in between): pull results.txt/.diff/.meta
# and flaky.txt when the VM has a newer hourly run. A changed diff or a newly flaky case is appended to the day's worker
# result for review.
$bsdDir = Join-Path $Games "_reports\freebsd-probes"
$bsdMeta = Join-Path $bsdDir "results.meta"
if (-not (Test-Path $bsdMeta) -or ((Get-Date) - (Get-Item $bsdMeta).LastWriteTime).TotalMinutes -gt 50) {
    $remoteRun = ssh -o ConnectTimeout=12 -o BatchMode=yes azbsd "head -1 ~/probes/results.meta" 2>$null
    $localRun = if (Test-Path $bsdMeta) { Get-Content $bsdMeta -TotalCount 1 } else { "" }
    if ("$remoteRun" -like "run *" -and "$remoteRun" -ne "$localRun") {
        New-Item -ItemType Directory -Force $bsdDir | Out-Null
        $flakyFile = Join-Path $bsdDir "flaky.txt"
        $oldFlaky = @(Get-Content $flakyFile -ErrorAction SilentlyContinue | Select-Object -Skip 1 | ForEach-Object { ($_ -split ' ')[0] })
        scp -q -o BatchMode=yes "azbsd:probes/results.txt" "azbsd:probes/results.diff" "azbsd:probes/results.meta" "azbsd:probes/flaky/flaky.txt" "$bsdDir/" 2>$null
        $diff = Get-Content (Join-Path $bsdDir "results.diff") -Raw -ErrorAction SilentlyContinue
        $newFlaky = @(Get-Content $flakyFile -ErrorAction SilentlyContinue | Select-Object -Skip 1 | Where-Object { $oldFlaky -notcontains ($_ -split ' ')[0] })
        $note = ""
        if ($diff -and $diff.Trim() -ne "no changes" -and $diff.Trim() -ne "first run") { $note += "## Changed results ($remoteRun)`n`n``````diff`n$diff`n```````n`n" }
        if ($newFlaky.Count) { $note += "## Newly flaky cases ($remoteRun)`n`n```````n$($newFlaky -join "`n")`n```````n`n" }
        if ($note) {
            $wf = Join-Path $Workers "freebsd-probes-$today.md"
            if (-not (Test-Path $wf)) { "# FreeBSD probe changes $today`n" | Set-Content $wf -Encoding utf8 }
            Add-Content $wf $note -Encoding utf8
        }
        Say "freebsd probes: fetched $remoteRun$(if ($note) { ' (changes for review)' })"
    }
}

$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StateFile -Encoding utf8


# Discord: when Blu has put a webhook URL for a channel he chose into _tools\discord-webhook.txt (he creates it in
# Discord himself; nothing here signs in to his account), new test results are posted there as short lines: pipeline
# test, recheck and PR test events, fixes and kept settings, and GPU lane results. At most 10 lines per run.
$hook = Join-Path $Tools "discord-webhook.txt"
if (Test-Path -LiteralPath $hook) {
    $url = (Get-Content -LiteralPath $hook -Raw).Trim()
    $mark = Join-Path $Tools "live\discord-posted.txt"
    $since = if (Test-Path $mark) { [datetime](Get-Content $mark -Raw).Trim() } else { (Get-Date).AddHours(-1) }
    $new = @(Select-String -LiteralPath (Join-Path $Tools "auto-games.log") -Pattern ' EVENT (TESTED|RECHECKED|PR TESTED|FIXED|SETTING KEPT|FIX PROVEN|BUILT) ' -ErrorAction SilentlyContinue | ForEach-Object { $_.Line } | Where-Object { $_ -match '^(\d\d-\d\d \d\d:\d\d:\d\d)' -and [datetime]::ParseExact("$((Get-Date).Year)-$($Matches[1])", "yyyy-MM-dd HH:mm:ss", $null) -gt $since } | ForEach-Object { ($_ -replace '^\S+ \S+\s+EVENT ', '') -replace '\s*\|\s*first frame.*$', '' })
    $new += @(Get-Content (Join-Path $Tools "gpu-lanes.log") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^(\d\d-\d\d \d\d:\d\d:\d\d)  (.+ lane: [^:]+: .+)$' -and $_ -notmatch 'testing |LANE PROBLEM|stopped ' -and [datetime]::ParseExact("$((Get-Date).Year)-$($Matches[1])", "yyyy-MM-dd HH:mm:ss", $null) -gt $since } | ForEach-Object { $_.Substring(16) })
    if ($new.Count -and $url -match '^https://(discord|discordapp)\.com/api/webhooks/') {
        $text = ($new | Select-Object -First 10 | ForEach-Object { "- " + $(if ($_.Length -gt 180) { $_.Substring(0, 180) + "..." } else { $_ }) }) -join "`n"
        try {
            Invoke-RestMethod -Uri $url -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes((@{ username = "AnyPS5 test bench"; content = "**AnyPS5 test results**`n$text" } | ConvertTo-Json))) | Out-Null
            Set-Content $mark (Get-Date -Format o)
            Say "discord: posted $([Math]::Min(10, $new.Count)) results"
        } catch { Say "discord: post failed: $($_.Exception.Message)" }
    }
}

# Status page: one look at every machine, lane and queue.
$trace = (Get-Content (Join-Path $Tools "auto-games-trace.log") -Tail 300 -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\d\d:\d\d:\d\d  [^ $]' } | Select-Object -Last 1)
$lanes = @(Get-Content (Join-Path $Tools "gpu-lanes.log") -Tail 6 -ErrorAction SilentlyContinue)
$disp = @(Get-Content $Log -Tail 8 -ErrorAction SilentlyContinue)
$free = (Get-PSDrive C, D, G | ForEach-Object { "{0}: {1} GB free" -f $_.Name, [math]::Round($_.Free / 1GB) }) -join " &middot; "
$results = @(Get-ChildItem $Workers -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -First 12 | ForEach-Object { "<li>$($_.Name) <small>$($_.LastWriteTime.ToString('MM-dd HH:mm'))</small></li>" })
$enc = { param($t) [System.Net.WebUtility]::HtmlEncode("$t") }
$html = @"
<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="60"><title>AnyPS5 machines</title>
<style>body{font:15px system-ui,sans-serif;background:#111;color:#ddd;margin:16px}h1{font-size:20px}h2{font-size:16px;margin-top:18px;color:#8bd}pre{white-space:pre-wrap;background:#1b1b1b;padding:8px;border-radius:6px}small{color:#888}</style></head><body>
<h1>AnyPS5 machines <small>$(Get-Date -Format 'yyyy-MM-dd HH:mm')</small></h1><p>$free</p>
<h2>Pipeline (RTX 4070 SUPER)</h2><pre>$(& $enc $trace)</pre>
<h2>GPU lanes (GTX 1650 SUPER, Radeon)</h2><pre>$(& $enc ($lanes -join "`n"))</pre>
<h2>Job board</h2><pre>$(& $enc ($disp -join "`n"))</pre>
<h2>Worker results waiting for review</h2><ul>$($results -join '')</ul>
</body></html>
"@
Set-Content -LiteralPath (Join-Path $Games "_reports\status.html") -Value $html -Encoding utf8
