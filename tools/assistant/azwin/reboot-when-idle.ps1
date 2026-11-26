# One-shot (11-08): waits until the PR runner is idle, holds its lock, reboots (finishes the Containers feature),
# and registers a one-shot startup task that pulls the Server Core ltsc2025 image and tests process isolation.
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$a = New-ScheduledTaskAction -Execute $ps -Argument '-NoProfile -ExecutionPolicy Bypass -Command "& C:\x64win\containers.ps1 image *> C:\x64win\logs\containers-image.log; Unregister-ScheduledTask x64win-containers-image -Confirm:$false"'
Register-ScheduledTask -TaskName x64win-containers-image -Action $a -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal (New-ScheduledTaskPrincipal -UserId SYSTEM -RunLevel Highest) -Force | Out-Null
while ($true) {
    try { $lock = [IO.File]::Open('C:\x64win\.prtest.lock', 'OpenOrCreate', 'ReadWrite', 'None'); break } catch { Start-Sleep 60 }
}
Restart-Computer -Force
