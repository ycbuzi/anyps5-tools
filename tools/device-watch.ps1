# Blu 11-07: "a background task that watches all of the devices so you can always make sure they're up to date while
# I'm gone". Every 5 minutes (task "AnyPS5 device watch"): checks the PC pipeline, the GPU lanes, the Quest and the Mac,
# restarts what it safely can, writes live\device-watch.txt and appends changes to live\device-events.log (Claude's
# session watches that file). Restarts on the Quest use only the AnyPS5 app's two fixed actions.
$ErrorActionPreference = "Continue"
$Tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$Live = Join-Path $Tools "live"
$Adb = "C:\AnyPS5-tools\platform-tools\adb.exe"
$Quest = "192.0.2.1:5555"
$StatePath = Join-Path $Live "device-watch.json"
$EventsPath = Join-Path $Live "device-events.log"
$state = @{}
if (Test-Path -LiteralPath $StatePath) { try { (Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json).psobject.Properties | ForEach-Object { $state[$_.Name] = $_.Value } } catch { } }
$now = Get-Date

function Event([string]$text) { Add-Content -LiteralPath $EventsPath -Value ("{0}  {1}" -f $now.ToString("MM-dd HH:mm"), $text) -Encoding utf8 }
function Changed([string]$key, [string]$value, [string]$message) {
    if ("$($state[$key])" -ne $value) { $state[$key] = $value; Event $message }
}
function Cooldown([string]$key, [int]$minutes) {
    $last = $state[$key]
    if ($last -and ($now - [datetime]$last).TotalMinutes -lt $minutes) { return $false }
    $state[$key] = $now.ToString("o"); return $true
}
function Run([string]$exe, [string[]]$arguments, [int]$seconds = 20) {
    $out = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError ($out + ".err")
    if (-not $p.WaitForExit($seconds * 1000)) { try { $p.Kill() } catch { }; Remove-Item $out, ($out + ".err") -ErrorAction SilentlyContinue; return $null }
    $text = (Get-Content -LiteralPath $out -Raw -ErrorAction SilentlyContinue); Remove-Item $out, ($out + ".err") -ErrorAction SilentlyContinue
    if ($null -eq $text) { "" } else { $text.Trim() }
}
function Ssh([string]$hostAlias, [string]$command, [int]$seconds = 20) { Run "ssh" @("-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", $hostAlias, $command) $seconds }

$lines = @()

# PC pipeline and GPU lanes
# The pipeline runs elevated, so its command line is hidden from this task: a trace written in the last 15 minutes is the sign (a nightly build step can leave it quiet for 9 minutes).
$trace = Get-Item -LiteralPath (Join-Path $Tools "auto-games-trace.log") -ErrorAction SilentlyContinue
$pipeline = [int]($trace -and ($now - $trace.LastWriteTime).TotalMinutes -lt 15)
if (-not $pipeline) {
    $flag = Get-Content -LiteralPath (Join-Path $Tools "live\gpu-busy.flag") -TotalCount 1 -ErrorAction SilentlyContinue
    if ($flag -match '^(\d+) .*\bpipeline\b') { $owner = Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue; if ($owner -and $owner.ProcessName -eq 'powershell') { $pipeline = 1 } }
}
$lanes = @(Get-CimInstance Win32_Process -Filter "name='powershell.exe'" | Where-Object { $_.CommandLine -match 'gpu-lanes\.ps1' }).Count
$recentErrors = @(Get-Content -LiteralPath (Join-Path $Tools "auto-games.log") -Tail 200 -ErrorAction SilentlyContinue | Where-Object { $_ -match ' error: ' -and $_ -match ('^' + $now.ToString("MM-dd")) -and $_ -match '^\d\d-\d\d (\d\d:\d\d)' -and ($now - [datetime]::ParseExact($now.ToString("yyyy-") + $_.Substring(0, 11), "yyyy-MM-dd HH:mm", $null)).TotalMinutes -lt 30 } | Select-Object -Last 3)
$lastErr = if ($recentErrors) { ($recentErrors[-1] -split ' error: ', 2)[1] } else { "" }
$lines += "PC pipeline: $(if ($pipeline) { 'running' } else { 'NOT running' })$(if ($lastErr) { '; last error: ' + $lastErr.Substring(0, [Math]::Min(120, $lastErr.Length)) })"
Changed "pipeline" "$([bool]$pipeline)" "PC pipeline $(if ($pipeline) { 'running' } else { 'is NOT running' })"
if ($lastErr) { Changed "pipelineError" $lastErr.Substring(0, [Math]::Min(80, $lastErr.Length)) "PC pipeline error: $($lastErr.Substring(0, [Math]::Min(160, $lastErr.Length)))" }
$same = @(Get-Content -LiteralPath (Join-Path $Tools "auto-games-trace.log") -Tail 60 -ErrorAction SilentlyContinue | ForEach-Object { if ($_ -match '^\d\d:\d\d:\d\d\s+(.*)$') { $Matches[1] } })
$stuck = if ($same.Count -ge 60 -and @($same | Select-Object -Unique).Count -eq 1) { $same[0] } else { "" }
if ($stuck) { $lines += "PC pipeline STUCK: the same step for 30+ minutes: " + $stuck.Substring(0, [Math]::Min(140, $stuck.Length)) }
Changed "pipelineStuck" "$([bool]$stuck)" "PC pipeline $(if ($stuck) { 'is STUCK on: ' + $stuck.Substring(0, [Math]::Min(140, $stuck.Length)) } else { 'is moving again' })"
$lines += "GPU lanes: $(if ($lanes) { 'running' } else { 'not running (the watchdog restarts them)' })"
Changed "lanes" "$([bool]$lanes)" "GPU lanes $(if ($lanes) { 'running' } else { 'stopped' })"

# Quest: SSH, desktop, Claude, pipeline; heal with the AnyPS5 app's fixed actions
$q = Ssh "quest" 'echo ok; pgrep -x xfce4-session | wc -l; tmux has-session -t claude 2>/dev/null && echo claude || echo noclaude; pgrep -fc "[q]pipe.sh"'
if ($null -eq $q -or $q -notmatch '^ok') {
    $lines += "Quest: SSH not answering"
    Changed "questSsh" "down" "Quest SSH is down"
    if (Cooldown "questHealServices" 10) { & $Adb -s $Quest shell "am start -n com.anyps5.quest/.LaunchActivity --es action services" *> $null; if ($LASTEXITCODE -eq 0) { Event "Quest: started SSH and Claude through the AnyPS5 app" } else { Changed "questOffline" "adb-$LASTEXITCODE" "Quest: off the network (SSH and adb both fail; headset asleep, off or on another Wi-Fi)" } }
} else {
    $parts = $q -split "`r?`n"
    $xfce = [int]$parts[1]; $claude = $parts[2] -eq "claude"; $qpipe = [int]$parts[3]
    $lines += "Quest: SSH ok; desktop $(if ($xfce) { 'up' } else { 'DOWN' }); Claude $(if ($claude) { 'up' } else { 'DOWN' }); test pipeline $(if ($qpipe) { 'up' } else { 'DOWN' })"
    Changed "questSsh" "up" "Quest SSH is up"
    Changed "questDesktop" "$xfce" "Quest desktop $(if ($xfce) { 'up' } else { 'DOWN' })"
    Changed "questClaude" "$claude" "Quest Claude $(if ($claude) { 'up' } else { 'DOWN' })"
    Changed "questPipeline" "$qpipe" "Quest test pipeline $(if ($qpipe) { 'up' } else { 'DOWN' })"
    if (-not $claude -and (Cooldown "questHealServices" 10)) { & $Adb -s $Quest shell "am start -n com.anyps5.quest/.LaunchActivity --es action services" *> $null; Event "Quest: restarted Claude through the AnyPS5 app" }
    if (-not $xfce -and (Cooldown "questHealDesktop" 15)) { & $Adb -s $Quest shell "am start -n com.anyps5.quest/.LaunchActivity" *> $null; Event "Quest: restarted the desktop through the AnyPS5 app" }
}
$adbOut = Run $Adb @("connect", $Quest) 15
$adbOk = [bool]($adbOut -match 'connected to')
if (-not $adbOk) {
    # Wi-Fi adb is lost at every headset restart. When the Quest is on USB and already trusts this PC, switch it back on.
    $usb = Run $Adb @("devices") 10
    $serial = @(("$usb" -split "`r?`n") | Where-Object { $_ -match '^\S+\s+device$' -and $_ -notmatch ':\d+\s' } | ForEach-Object { ($_ -split '\s+')[0] }) | Select-Object -First 1
    if ($serial -and (Cooldown "questAdbTcpip" 10)) {
        $model = Run $Adb @("-s", $serial, "shell", "getprop", "ro.product.model") 10
        if ("$model" -match 'Quest') {
            Run $Adb @("-s", $serial, "tcpip", "5555") 15 | Out-Null
            Start-Sleep -Seconds 4
            $adbOut = Run $Adb @("connect", $Quest) 15
            $adbOk = [bool]($adbOut -match 'connected to')
            Event "Quest: on USB with Wi-Fi adb off; switched Wi-Fi adb on ($(if ($adbOk) { 'connected' } else { 'still not connected' }))"
        }
    }
}
$lines += "Quest adb (Wi-Fi): $(if ($adbOk) { 'connected' } else { 'OFF - installs and adb heals impossible until adb tcpip 5555 is run again over USB' })"
Changed "questAdb" "$adbOk" "Quest Wi-Fi adb $(if ($adbOk) { 'connected' } else { 'is OFF (needs USB: adb tcpip 5555)' })"
$status = Run "curl.exe" @("-s", "-m", "6", "http://192.0.2.1:9072/status.txt") 10
if ($status) {
    $lines += "Quest pipeline: " + (($status -split "`r?`n") | Select-Object -First 2) -join " | "
    Changed "questRequests" "up" "Quest request server (:9072) answers"
} elseif ($null -ne $q -and $q -match '^ok') {
    $lines += "Quest request server (:9072): NOT answering; the WSL builder gets no requests"
    Changed "questRequests" "down" "Quest request server (:9072) is not answering"
    if (Cooldown "questHealRequests" 10) {
        Ssh "quest" 'for p in $(pgrep -f "http.server 9072"); do kill -9 $p; done' | Out-Null
        Event "Quest: killed the hung request server; qpipe restarts it on its next loop"
    }
}

# Chromebook (AnyPS5 OS): console, host API, key and pad services; restart the session when the console is gone
$cbArgs = @("-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "UserKnownHostsFile=$env:USERPROFILE\.ssh\known_hosts_anyps5os", "-i", "$env:USERPROFILE\.ssh\id_ed25519_chromebook", "user@example.com",
    'pgrep -x anyps5-console >/dev/null && echo console || echo noconsole; curl -s -m 4 -o /dev/null http://127.0.0.1:8790/ && echo 200 || echo down; for s in anyps5-keys anyps5-pad-watch bluetooth; do systemctl is-active $s; done; systemctl --failed --no-legend | wc -l')
$cb = Run "ssh" $cbArgs 25
if ($null -eq $cb -or -not $cb) {
    $lines += "Chromebook: not answering on SSH (off, asleep or rebooting)"
    Changed "cbSsh" "down" "Chromebook is not answering on SSH"
} else {
    $p = $cb -split "`r?`n"
    $console = $p[0] -eq "console"; $hostOk = $p[1] -eq "200"; $svc = @($p[2..4] | Where-Object { $_ -ne "active" }).Count; $failed = [int]$p[5]
    $lines += "Chromebook: console $(if ($console) { 'up' } else { 'DOWN' }); host $(if ($hostOk) { 'ok' } else { 'DOWN' }); services $(if ($svc) { "$svc not active" } else { 'ok' }); failed units $failed"
    Changed "cbSsh" "up" "Chromebook is up"
    Changed "cbConsole" "$console/$hostOk" "Chromebook console $(if ($console) { 'up' } else { 'DOWN' }), host $(if ($hostOk) { 'ok' } else { 'DOWN' })"
    Changed "cbServices" "$svc/$failed" "Chromebook: $svc OS services not active, $failed failed units"
    if ((-not $console -or -not $hostOk) -and (Cooldown "cbHealSession" 10)) {
        Run "ssh" ($cbArgs[0..($cbArgs.Count - 2)] + @('pgrep -x game >/dev/null || systemctl restart getty@tty1')) 25 | Out-Null
        Event "Chromebook: console or host down; restarted the session (skipped if a game is running)"
    }
}

# Mac: which macOS, reachable or not
$m = Ssh "macbook" 'sw_vers -productVersion'
if ($null -eq $m -or -not $m) {
    $lines += "Mac: not answering on SSH (installing, in Recovery or off)"
    Changed "mac" "down" "Mac is not answering on SSH"
} else {
    $lines += "Mac: up, macOS $m"
    Changed "mac" $m "Mac is up on macOS $m"
}

Set-Content -LiteralPath (Join-Path $Live "device-watch.txt") -Value (@("updated $($now.ToString('HH:mm'))") + $lines) -Encoding utf8
$state | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
