# One-time setup of anyps5-win (run over ssh as blu, an administrator). Re-runnable.
# Expects C:\x64win\in\*.tgz (toolchain archives from the PC) and the scripts in C:\x64win.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$W = 'C:\x64win'
New-Item -ItemType Directory -Force C:\tools, "$W\prtests", "$W\logs", "$W\tmp" | Out-Null
$z = "$W\in\tools.zip"
if (Test-Path $z) {
    if (Get-Command tar.exe -ErrorAction SilentlyContinue) { tar -xf $z -C C:\tools } else { Expand-Archive $z C:\tools -Force }
    if (Test-Path C:\tools\mingw64\bin\gcc.exe) { Remove-Item $z }
}
foreach ($a in 'mingw64', 'cmake', 'git', 'python') {
    $f = "$W\in\$a.tgz"
    if (Test-Path $f) { tar -xzf $f -C C:\tools; if ($LASTEXITCODE -eq 0) { Remove-Item $f } }
}
if (Test-Path C:\tools\cmake-4.4.4-windows-x86_64) { Rename-Item C:\tools\cmake-4.4.4-windows-x86_64 cmake }
$env:PATH = 'C:\tools\mingw64\bin;C:\tools\cmake\bin;C:\tools\Git\cmd;C:\tools\Python314;' + $env:PATH
if (-not (Test-Path "$W\AnyPS5\.git")) {
    git clone -q https://github.com/boykopovar/AnyPS5.git "$W\AnyPS5"
    git -C "$W\AnyPS5" remote add fork https://github.com/ycbuzi/AnyPS5.git
}
git -C "$W\AnyPS5" config core.longpaths true
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$act = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -File $W\prtest.ps1 -Auto" -WorkingDirectory $W
$trg = @((New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(40) -RepetitionInterval (New-TimeSpan -Hours 1)), (New-ScheduledTaskTrigger -AtStartup))
$trg[1].Delay = 'PT10M'
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12) -MultipleInstances IgnoreNew -Priority 7 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$pri = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\blu" -LogonType S4U -RunLevel Highest
Register-ScheduledTask -TaskName 'x64win-prtest' -Action $act -Trigger $trg -Settings $set -Principal $pri -Force | Out-Null
$sh = New-Object -ComObject WScript.Shell
$desk = [Environment]::GetFolderPath('Desktop'); $startup = [Environment]::GetFolderPath('Startup')
function Link($path, $target, $arguments) { $l = $sh.CreateShortcut($path); $l.TargetPath = $target; $l.Arguments = $arguments; $l.WorkingDirectory = $W; $l.Save() }
Link "$startup\AnyPS5 live status.lnk" $ps "-NoProfile -ExecutionPolicy Bypass -File $W\live.ps1"
Link "$desk\Live PR test status.lnk" $ps "-NoProfile -ExecutionPolicy Bypass -File $W\live.ps1"
Link "$desk\Run PR tests now.lnk" $ps "-NoProfile -Command `"Start-ScheduledTask x64win-prtest; Write-Host 'Started the PR test run (new open PRs). Watch it in the live status window.'; Start-Sleep 8`""
Link "$desk\PR test reports.lnk" "$env:SystemRoot\explorer.exe" "$W\prtests"
Get-ScheduledTask x64win-prtest | Select-Object TaskName, State
& gcc --version | Select-Object -First 1
& cmake --version | Select-Object -First 1
& python --version
'setup done'
