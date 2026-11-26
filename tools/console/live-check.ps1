# Checks the REAL deployed AnyPS5.exe, not a test copy. Since 11-15 AnyPS5.exe is its own app (the screen for AnyPS5 OS)
# and the pipeline window only opens it. If AnyPS5.exe is running: it must have loaded its screen since it started
# (live\anyps5-app.log "screen ready ... in AnyPS5") and its window must be responding; it is not touched. If it is not
# running and -NoStart is not given: start it, wait for "screen ready", close it again. Prints PASS/FAIL; exit = failures.
param([switch]$NoStart)
$live = "C:\AnyPS5 Games\_tools\live"
$log = Join-Path $live "anyps5-app.log"
$fails = 0
Add-Type -Namespace LiveCheck -Name W -MemberDefinition '[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool IsHungAppWindow(IntPtr h);'
function ReadySince($t) {
    if (-not (Test-Path $log)) { return $null }
    foreach ($l in (Get-Content $log | Where-Object { $_ -match " in AnyPS5(:|$| \()" } | Select-Object -Last 20)) {
        $at = [datetime]::ParseExact((Get-Date).Year.ToString() + "-" + $l.Substring(0, 14), "yyyy-MM-dd HH:mm:ss", $null)
        if ($at -ge $t.AddSeconds(-2)) { $last = $l }
    }
    return $last
}
$app = Get-Process AnyPS5 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($app) {
    $l = ReadySince $app.StartTime
    $hw = 0; try { $hw = [long](Get-Content (Join-Path $live "anyps5-window.txt") -Raw).Trim() } catch { }
    $win = $hw -and [LiveCheck.W]::IsWindow([IntPtr]$hw)
    if (-not $l -or $l -notmatch "screen ready") { $fails++; "FAIL live AnyPS5.exe has not loaded its screen since it started at " + $app.StartTime.ToString("HH:mm:ss") + $(if ($l) { ": $l" } else { "" }) }
    elseif (-not $win) { $fails++; "FAIL live AnyPS5.exe window not found" }
    elseif ([LiveCheck.W]::IsHungAppWindow([IntPtr]$hw)) { $fails++; "FAIL live AnyPS5.exe window is not responding" }
    else { "PASS live AnyPS5 running on its own, screen loaded, responding" }
}
elseif (-not $NoStart) {
    $since = Get-Date
    $started = Start-Process "D:\AnyPS5 Games\AnyPS5.exe" -PassThru
    $l = $null; $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline -and -not $l) { Start-Sleep -Milliseconds 500; $l = ReadySince $since }
    if ($l -and $l -match "screen ready") { "PASS live AnyPS5 screen ready" } else { $fails++; "FAIL live AnyPS5 : " + $(if ($l) { $l } else { "no answer in 40 s" }) }
    try { $started.CloseMainWindow() | Out-Null; if (-not $started.WaitForExit(5000)) { $started.Kill() } } catch { }
}
else { "SKIP live AnyPS5.exe is not running" }
exit $fails
