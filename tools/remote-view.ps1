# What the Chromebook and the MacBook are running, for the Discord stream (Blu 11-06: "they can't see the MacBook or
# Chromebook on stream"). Every 15 seconds it asks both over ssh and writes _tools\live\remote-view.txt, which the
# AnyPS5 Games window shows beside its Live console. The AnyPS5 Games window starts it (hidden) when it is not running.
# Read only.
param([int]$Seconds = 60)
$ErrorActionPreference = "Continue"
# One copy at a time: both the AnyPS5 Games window and AnyPS5 Quest View start it when its files go stale (11-07).
$created = $false
$single = New-Object System.Threading.Mutex($true, "Local\AnyPS5RemoteView", [ref]$created)
if (-not $created) { exit 0 }
$Out = Join-Path $PSScriptRoot "live\remote-view.txt"
$cb = @'
echo "load $(cut -d' ' -f1-3 /proc/loadavg)   RAM free $(free -m | awk '/Mem/{print $7}') MB   disk free $(df -h ~ | awk 'END{print $4}')"
if pgrep -f '[c]hromebook_prtest.sh' >/dev/null; then echo "PR tests: running"; else echo "PR tests: idle"; fi
tail -1 ~/cb-work/prtest.status 2>/dev/null | cut -c1-160
pgrep -f '[c]laude --dangerously' >/dev/null && echo "Claude Code: on (Remote Control)" || echo "Claude Code: off"
echo "last results:"; ls -t ~/cb-work/prtests 2>/dev/null | head -4 | while read f; do echo "  #${f%.md}: $(grep -m1 '^new failures' ~/cb-work/prtests/$f | cut -c1-70)"; done
'@
$mac = @'
echo "load $(sysctl -n vm.loadavg | tr -d '{}')   disk free $(df -h / | awk 'END{print $4}')"
if pgrep -f '[c]laude -p' >/dev/null; then echo "Claude job: running"; else echo "Claude job: idle"; fi
echo "latest:"; tail -3 ~/mac-work/status.md 2>/dev/null | cut -c1-160 | sed 's/^/  /'
'@
# Quest 2 (Blu 11-07: "when you test games on the Quest, we can see it"): its run scripts append START/EXIT/FRAME lines
# to /root/quest-status.txt inside proot Debian; this reads them from the Termux side, where box64 shows as a process.
$quest = @'
R=$PREFIX/var/lib/proot-distro/containers/debian/rootfs/root
echo "status: $(grep -E '^(START|EXIT) ' $R/quest-status.txt 2>/dev/null | tail -1 | cut -c1-150)"
pgrep -f '[b]ox64' >/dev/null && echo "game process: running" || echo "game process: none"
pgrep -f '[X]vnc :1' >/dev/null && echo "display: on" || echo "display: off"
pgrep -f '[c]laude' >/dev/null && echo "Claude Code: on (Remote Control)" || echo "Claude Code: off"
grep '^FRAME' $R/quest-status.txt 2>/dev/null | tail -1 | cut -c1-150 | sed 's/^/last frame: /'
'@
$QuestJson = Join-Path $PSScriptRoot "live\quest-view.json"
$QuestUrl = "http://192.0.2.1:6080/vnc.html?autoconnect=1&view_only=1&resize=scale&reconnect=1&reconnect_delay=2000"
while ($true) {
    $c = @(ssh -o ConnectTimeout=8 -o BatchMode=yes -o UserKnownHostsFile=~/.ssh/known_hosts_anyps5os -i ~/.ssh/id_ed25519_chromebook user@example.com 'pgrep -x anyps5-console >/dev/null && echo "AnyPS5 OS console: running" || echo "AnyPS5 OS console: not running"; g=$(pgrep -a -f "/games/.*/game" | head -1 | sed "s|.*/games/||;s|/game.*||"); [ -n "$g" ] && echo "playing: $g"' 2>$null)
    $m = ($mac -replace "`r", "") | ssh -o ConnectTimeout=8 -o BatchMode=yes macbook "bash -s" 2>$null
    $q = @(($quest -replace "`r", "") | ssh -o ConnectTimeout=8 -o BatchMode=yes quest "bash -s" 2>$null)
    $status = "$($q | Where-Object { $_ -like 'status: *' } | Select-Object -First 1)" -replace '^status: ', ''
    $running = $status -like 'START |*' -and ($q -contains 'game process: running')
    $game = if ($running) { ($status -split '\|')[1].Trim() } else { "" }
    try { @{ running = $running; game = $game; online = [bool]$q.Count; url = $QuestUrl; status = $status; updated = (Get-Date -Format o) } | ConvertTo-Json -Compress | Set-Content -LiteralPath $QuestJson -Encoding utf8 } catch { }
    $lines = @("CHROMEBOOK   Pentium N6000, AnyPS5 OS (no AVX)     updated $(Get-Date -Format 'HH:mm:ss')")
    $lines += if ($c) { $c | ForEach-Object { "  $_" } } else { "  offline" }
    $lines += ""
    $lines += "MACBOOK   2011 MacBook Pro, Intel, AVX only: CPU PR tests and watch jobs"
    $lines += if ($m) { $m | ForEach-Object { "  $_" } } else { "  offline" }
    $lines += ""
    $lines += "QUEST 2   Snapdragon XR2, Android 14: games through box64 + Turnip" + $(if ($running) { "   PLAYING $game" } else { "" })
    $lines += if ($q.Count) { $q | ForEach-Object { "  $_" } } else { "  offline (Termux sshd on 192.0.2.1:8022 not answering)" }
    $lines += "  live view: $QuestUrl"
    try { Set-Content -LiteralPath $Out -Value $lines -Encoding utf8 } catch { }
    # Blu 11-07: "everything should know what each thing is doing". One file for the pipeline, the app and the local
    # AI; a machine that comes back (the MacBook after its reinstall) shows up here and in machine-events.log at once.
    $now = @{
        chromebook = @{ online = [bool]$c; busy = [bool]($c -match 'PR tests: running'); detail = "$(@($c) | Select-Object -Skip 1 -First 2)" }
        macbook = @{ online = [bool]$m; busy = [bool]($m -match 'Claude job: running'); detail = "$(@($m) | Select-Object -Skip 1 -First 1)"; paused = (Test-Path (Join-Path $PSScriptRoot "live\mac-paused.flag")) }
        quest = @{ online = [bool]$q.Count; busy = $running; detail = $(if ($running) { "playing $game" } else { $status }) }
    }
    foreach ($name in @("chromebook", "macbook", "quest")) {
        if ($null -ne $script:was -and $script:was[$name] -ne $now[$name].online) {
            "$(Get-Date -Format 'MM-dd HH:mm:ss')  $name $(if ($now[$name].online) { 'came online' } else { 'went offline' })" | Add-Content -LiteralPath (Join-Path $PSScriptRoot "live\machine-events.log") -Encoding utf8
        }
    }
    $script:was = @{ chromebook = $now.chromebook.online; macbook = $now.macbook.online; quest = $now.quest.online }
    $now.updated = (Get-Date -Format o)
    try { $now | ConvertTo-Json -Compress -Depth 3 | Set-Content -LiteralPath (Join-Path $PSScriptRoot "live\machines.json") -Encoding utf8 } catch { }
    Start-Sleep $Seconds
}
