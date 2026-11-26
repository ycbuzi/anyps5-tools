# The offline half of the AnyPS5 app loop. Every 2 hours (scheduled task "AnyPS5 app loop") it checks the REAL running
# programs without opening anything, typing anything or starting a game:
#   - AnyPS5 Machines: loads its AnyPS5 screen in the background and reports (live-check.ps1 -NoStart)
#   - AnyPS5 Games work app: answers from its UI timer (live\workapp-check.flag -> live\workapp-check.txt)
#   - pipeline: auto-games.ps1 is running and its heartbeat is fresh, unless paused
#   - nothing left behind: no stale game marker, no pipeline pause left by the app with no game running
#   - source: Launcher.cs, MachinesView.cs and AnyPS5.cs still compile (test builds only, nothing deployed)
# Results: _tools\live\app-loop.log; failures also in D:\AnyPS5 Games\_reports\app-loop\NEEDS-FIX.txt, which Claude reads
# at the start of a session. Full interactive test (opens a test window ~30 s): _tools\console\selftest.cmd
$tools = "C:\AnyPS5 Games\_tools"
$live = Join-Path $tools "live"
$fails = @(); $notes = @()

$out = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\live-check.ps1" -NoStart 2>&1
$fails += @($out | Where-Object { $_ -match "^FAIL" })
$notes += @($out | Where-Object { $_ -match "^PASS" })

$work = Get-Process "AnyPS5 Games" -ErrorAction SilentlyContinue
if (-not $work) { $fails += "FAIL work app: AnyPS5 Games.exe is not running" }
else {
    $answer = Join-Path $live "workapp-check.txt"
    $asked = Get-Date
    Set-Content (Join-Path $live "workapp-check.flag") "check" -Encoding ascii
    $got = $null
    for ($i = 0; $i -lt 40 -and -not $got; $i++) {
        Start-Sleep -Milliseconds 500
        if ((Test-Path $answer) -and (Get-Item $answer).LastWriteTime -ge $asked.AddSeconds(-1)) { $got = Get-Content $answer }
    }
    if ($got) { $notes += "PASS work app answered (" + (($got | Where-Object { $_ -match "^(build|tabs)" }) -join ", ") + ")" }
    else { $fails += "FAIL work app did not answer in 20 s (window hung, or a build older than the check)"; Remove-Item (Join-Path $live "workapp-check.flag") -ErrorAction SilentlyContinue }
}

$paused = Test-Path (Join-Path $tools "pause.flag")
$pipe = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match "auto-games\.ps1" }
$alive = Join-Path $tools "auto-games.alive"
$age = if (Test-Path $alive) { ((Get-Date) - (Get-Item $alive).LastWriteTime).TotalMinutes } else { 9999 }
if ($paused) { $notes += "SKIP pipeline paused" }
elseif (-not $pipe) { $fails += "FAIL pipeline: auto-games.ps1 is not running and the pipeline is not paused" }
elseif ($age -gt 15) {
    # 11-15: a long copy or build (robocopy of a game to _test on the shared USB D: drive) does not touch the heartbeat.
    # Busy = one of the pipeline's child processes is still moving data; only that counts as alive.
    $all = Get-CimInstance Win32_Process
    $kids = @(); $queue = @($pipe | ForEach-Object { $_.ProcessId })
    while ($queue.Count) { $id = $queue[0]; $queue = @($queue | Select-Object -Skip 1); foreach ($c in $all | Where-Object { $_.ParentProcessId -eq $id }) { $kids += $c.ProcessId; $queue += $c.ProcessId } }
    $io = { param($ids) $sum = 0; foreach ($k in $ids) { $p = Get-CimInstance Win32_Process -Filter "ProcessId=$k" -ErrorAction SilentlyContinue; if ($p) { $sum += $p.ReadTransferCount + $p.WriteTransferCount } }; $sum }
    $before = & $io $kids; Start-Sleep -Seconds 4; $after = & $io $kids
    $trace = Join-Path $tools "auto-games-trace.log"
    $traceAge = if (Test-Path $trace) { ((Get-Date) - (Get-Item $trace).LastWriteTime).TotalMinutes } else { 9999 }
    if ($traceAge -lt 5) { $notes += "PASS pipeline busy for $([int]$age) min (its log was written $([int]($traceAge * 60)) s ago: " + ((Get-Content $trace -Tail 1) -replace '^\S+\s+', '').Substring(0, [Math]::Min(60, ((Get-Content $trace -Tail 1) -replace '^\S+\s+', '').Length)) + ")" }
    elseif ($kids.Count -and $after -gt $before) { $notes += "PASS pipeline busy for $([int]$age) min (" + (($all | Where-Object { $kids -contains $_.ProcessId } | ForEach-Object { $_.Name } | Select-Object -Unique) -join ", ") + " still moving data)" }
    else { $fails += "FAIL pipeline heartbeat is $([int]$age) min old and nothing it started is moving data" }
}
else { $notes += "PASS pipeline running (heartbeat $([int]$age) min)" }

$marker = Join-Path $live "anyps5-app-game.txt"
if (Test-Path $marker) {
    $id = (Get-Content $marker -Raw).Trim()
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { $fails += "FAIL stale game marker for pid $id (removed)"; Remove-Item $marker -ErrorAction SilentlyContinue }
}
$flag = Join-Path $tools "pause.flag"
if ($paused -and ((Get-Content $flag -Raw) -match "AnyPS5 app") -and -not (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like 'D:\AnyPS5 Games\*' -and $_.ExecutablePath -notlike '*\_tools\*' -and $_.Name -notlike 'AnyPS5*' })) {
    $fails += "FAIL the pipeline is still paused by the AnyPS5 app but no game is running"
}

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$w = Join-Path $tools "webview2"
$tmp = Join-Path $live "loop-build"
New-Item -ItemType Directory -Force $tmp | Out-Null
$common = @("-nologo", "-target:winexe", "-r:System.Management.dll", "-r:System.Web.Extensions.dll", "-r:System.Windows.Forms.dll", "-r:System.Drawing.dll", "-r:$w\Microsoft.Web.WebView2.Core.dll", "-r:$w\Microsoft.Web.WebView2.WinForms.dll")
$l = Join-Path $tools "launcher"
$builds = @(
    @{ name = "work app"; args = @("-out:$tmp\w.exe", "$l\Launcher.cs", "$l\TeamView.cs", "$l\TownView.cs", "$l\QuestView.cs") },
    @{ name = "Machines"; args = @("-out:$tmp\m.exe", "$l\MachinesView.cs") },
    @{ name = "AnyPS5.exe"; args = @("-out:$tmp\a.exe", "$tools\console\AnyPS5.cs", "$tools\console\host-*.cs") })
foreach ($b in $builds) {
    $r = & $csc @common @($b.args) 2>&1 | Where-Object { $_ -match "error" }
    if ($LASTEXITCODE -ne 0) { $fails += "FAIL $($b.name) source does not compile: " + (($r | Select-Object -First 2) -join " ; ") } else { $notes += "PASS $($b.name) source compiles" }
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
Add-Content (Join-Path $live "app-loop.log") ("$stamp " + $(if ($fails.Count) { "FAIL: " + ($fails -join " | ") } else { "ok: " + ($notes -join " | ") }))
$report = "D:\AnyPS5 Games\_reports\app-loop"
if ($fails.Count) { New-Item -ItemType Directory -Force $report | Out-Null; Set-Content (Join-Path $report "NEEDS-FIX.txt") (@("$stamp AnyPS5 app loop found:") + $fails) }
$fails; $notes
exit $fails.Count
