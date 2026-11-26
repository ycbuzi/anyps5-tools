# Live view of anyps5-win's PR tests for Blu's Remote Desktop pane (starts from the Startup folder).
$W = 'C:\x64win'
$Host.UI.RawUI.WindowTitle = 'AnyPS5 Azure Windows - live'
while ($true) {
    Clear-Host
    $cpu = [int](Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average
    $mem = [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1024)
    Write-Host "=== AnyPS5 Azure Windows (PR tests, Spot) === $(Get-Date -Format 'HH:mm:ss')   cpu: $cpu%   mem free: $mem MB" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '--- testing now ---' -ForegroundColor Yellow
    if (Test-Path "$W\current") { "PR $(Get-Content "$W\current")" } else { 'idle' }
    Get-Process ninja, ctest, cc1plus, 'x86_64-w64-mingw32-g++' -ErrorAction SilentlyContinue | Select-Object -First 4 | ForEach-Object { "  $($_.ProcessName) (pid $($_.Id))" }
    Write-Host ''
    Write-Host '--- next automatic run ---' -ForegroundColor Yellow
    $i = Get-ScheduledTaskInfo -TaskName 'x64win-prtest' -ErrorAction SilentlyContinue
    if ($i) { "next $($i.NextRunTime)   last $($i.LastRunTime) (result $($i.LastTaskResult))" } else { 'scheduled task missing' }
    Write-Host ''
    Write-Host '--- results (status.md) ---' -ForegroundColor Yellow
    Get-Content "$W\status.md" -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { if ($_.Length -gt 170) { $_.Substring(0, 170) } else { $_ } }
    Write-Host ''
    Write-Host "--- toolchain matrix: $(if (Test-Path "$W\tc-now.txt") { Get-Content "$W\tc-now.txt" } else { 'idle' }) ---" -ForegroundColor Yellow
    Get-Content "$W\tc-status.txt" -Tail 6 -ErrorAction SilentlyContinue | ForEach-Object { if ($_.Length -gt 170) { $_.Substring(0, 170) } else { $_ } }
    Write-Host ''
    $L = Get-ChildItem "$W\logs\*build.log", "$W\logs\*ctest*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    Write-Host "--- live log: $($L.Name) ---" -ForegroundColor Yellow
    if ($L) { Get-Content $L.FullName -Tail 12 | ForEach-Object { if ($_.Length -gt 160) { $_.Substring(0, 160) } else { $_ } } }
    Start-Sleep 5
}
