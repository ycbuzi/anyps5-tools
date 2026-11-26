# AnyPS5 machines watchdog (11-08, Blu: background watchers instead of Claude checking). Runs every 5 minutes as the
# scheduled task "AnyPS5 machines watchdog", offline, no Claude. It keeps things working by itself:
#  - the AnyPS5 Machines app: restarted if it stops responding for two checks in a row;
#  - each machine: reachable, and its offline job running; a stopped job is started again;
#  - a Spot VM that stays unreachable (Azure evicted it) or a machine that is down gets a Windows notification once.
# Status: _tools\live\watchdog.md (the local assistant and the Machines app can read it). Log: _tools\live\watchdog.log.
$ErrorActionPreference = 'SilentlyContinue'
$live = 'C:\AnyPS5 Games\_tools\live'; New-Item -ItemType Directory -Force $live | Out-Null
$state = Join-Path $live 'watchdog-state.json'; $log = Join-Path $live 'watchdog.log'
$s = @{}; if (Test-Path $state) { (Get-Content $state -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $s[$_.Name] = $_.Value } }
function Log($t) { Add-Content $log ("{0} {1}" -f (Get-Date -Format 'MM-dd HH:mm'), $t) }
function Notify($title, $text) {
    try {
        [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $n = $x.GetElementsByTagName('text'); [void]$n.Item(0).AppendChild($x.CreateTextNode($title)); [void]$n.Item(1).AppendChild($x.CreateTextNode($text))
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AnyPS5').Show([Windows.UI.Notifications.ToastNotification]::new($x))
    } catch {}
    Log "NOTIFY $title - $text"
}
function Ssh($h, $cmd, $t = 25) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo 'ssh', "-o BatchMode=yes -o ConnectTimeout=10 $h `"$cmd`""
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $read = $p.StandardOutput.ReadToEndAsync(); [void]$p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($t * 1000)) { try { $p.Kill() } catch {}; return $null }
    if ($p.ExitCode -ne 0) { return $null }
    $read.Result
}
$lines = @("# Machines watchdog $(Get-Date -Format 'MM-dd HH:mm')", "")

# 1. The Machines app
$app = Get-Process -Name 'AnyPS5 Machines' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($app) {
    if (-not $app.Responding) {
        if ($s['appHung']) { Log 'Machines app not responding twice: restarting'; [void]$app.CloseMainWindow(); if (-not $app.WaitForExit(10000)) { $app.Kill() }; Start-Sleep 2; Start-Process 'D:\AnyPS5 Games\AnyPS5 Machines.exe' -WorkingDirectory 'D:\AnyPS5 Games'; $s['appHung'] = $false; $lines += '- Machines app: was frozen, restarted' }
        else { $s['appHung'] = $true; $lines += '- Machines app: not responding (restart next check if still frozen)' }
    } else { $s['appHung'] = $false; $lines += '- Machines app: ok' }
} else { $lines += '- Machines app: not open' }

# 2. Machines and their offline jobs: name, ssh host, check command (prints OK when the job runs), restart command
$checks = @(
    @('Azure FreeBSD probes', 'azbsd', 'crontab -l | grep -q probes/run.sh && echo OK', ''),
    @('MacBook', 'macbook', 'echo OK', ''),
    @('Quest pipeline', 'quest', 'tmux has-session -t qpipe-sup 2>/dev/null && echo OK', '')
)
foreach ($c in $checks) {
    $name, $h, $check, $fix, $mode = $c
    $out = Ssh $h $check
    $key = "down-$h"
    if ($null -eq $out -and $mode -eq 'ondemand') { $lines += "- ${name}: stopped (on-demand VM, started for tests)"; continue }
    if ($null -eq $out) {
        $since = if ($s[$key]) { [datetime]$s[$key] } else { $s[$key] = (Get-Date).ToString('o'); Get-Date }
        $mins = [int]((Get-Date) - $since).TotalMinutes
        $lines += "- ${name}: unreachable for $mins min"
        if ($mins -ge 15 -and -not $s["told-$h"]) {
            $hint = if ($h -in 'azlinux', 'azwin') { 'Azure probably evicted the Spot VM: press Start on it in the portal.' } else { 'Check that it is on and online.' }
            Notify "AnyPS5: $name is down" $hint; $s["told-$h"] = $true
        }
        continue
    }
    if ($s[$key]) { Log "$name back"; $s.Remove($key); $s.Remove("told-$h") }
    if ($out -match 'OK') { $lines += "- ${name}: ok" }
    elseif ($fix) { [void](Ssh $h $fix); Log "$name job was stopped: started it again"; $lines += "- ${name}: job was stopped, started again" }
    else { $lines += "- ${name}: job not running (no automatic fix)" }
}
# 3. The WSL Quest builder (took over from the Chromebook 11-16): its driver runs from the scheduled task
if (Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'qb-driver\.ps1' }) { $lines += '- WSL Quest builder: ok' }
else { Start-ScheduledTask -TaskName 'AnyPS5 WSL Quest builder'; Log 'WSL Quest builder driver was not running: started its task'; $lines += '- WSL Quest builder: driver was not running, started again' }
$cb = & python -I 'C:\AnyPS5-work\cbssh.py' '/root/idle-tests/watch-status.sh' 2>$null
if ($LASTEXITCODE -eq 0 -and $cb) {
    $s.Remove('down-chromebook')
    $cbLines = @($cb | Where-Object { $_ -match '^- ' })
    $lines += '- Chromebook: ok'
    $lines += @($cbLines | ForEach-Object { '  ' + $_ })
    foreach ($r in @($cb | Where-Object { $_ -match '^RESTARTED (.+)' })) { Log "Chromebook $($r.Substring(10)) timer was stopped: started it again" }
    $seen = @($s['cbSeen']); $new = @()
    foreach ($p in @($cbLines | Where-Object { $_ -match '^- \d\d-\d\d \d\d:\d\d ' })) { if ($seen -notcontains $p) { $new += $p } }
    foreach ($p in $new) { Notify 'AnyPS5: Chromebook game problem' $p.Substring(2) }
    $s['cbSeen'] = @($cbLines | Where-Object { $_ -match '^- \d\d-\d\d \d\d:\d\d ' })
} else {
    $since = if ($s['down-chromebook']) { [datetime]$s['down-chromebook'] } else { $s['down-chromebook'] = (Get-Date).ToString('o'); Get-Date }
    $lines += "- Chromebook: unreachable for $([int]((Get-Date) - $since).TotalMinutes) min"
}
$xboxHost = Get-Content 'C:\AnyPS5-work\xbox-probe\out\.host' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($xboxHost) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $ok = try { $tcp.ConnectAsync($xboxHost.Trim(), 11443).Wait(2000) -and $tcp.Connected } catch { $false }
    $tcp.Close()
    $lines += $(if ($ok) { '- Xbox: reachable' } else { '- Xbox: not reachable (off, asleep or a new address: run xbox-probe\xfind.py)' })
}
$lines | Set-Content (Join-Path $live 'watchdog.md') -Encoding utf8
$s | ConvertTo-Json | Set-Content $state -Encoding utf8
