# Runs on each Windows test VM (11-09): stops Defender and background services from eating the 2 vCPUs during builds.
# Defender stays on; it skips the build folders and compiler processes, scans at low priority and has no scheduled full scan.
$ErrorActionPreference = 'Continue'
Add-MpPreference -ExclusionPath 'C:\x64win', 'C:\tools', "$env:USERPROFILE\.local", "$env:USERPROFILE\.claude"
Add-MpPreference -ExclusionProcess 'gcc.exe', 'g++.exe', 'cc1.exe', 'cc1plus.exe', 'as.exe', 'ld.exe', 'ninja.exe', 'cmake.exe', 'ctest.exe', 'git.exe', 'python.exe', 'clang.exe', 'clang++.exe', 'lld.exe', 'ld.lld.exe', 'claude.exe'
Set-MpPreference -EnableLowCpuPriority $true -ScanAvgCPULoadFactor 10 -ScanScheduleDay 8 -DisableCatchupFullScan $true -DisableCatchupQuickScan $true -SubmitSamplesConsent 2
foreach ($s in 'WSearch', 'SysMain', 'DiagTrack', 'dmwappushservice', 'MapsBroker', 'XblAuthManager', 'XblGameSave', 'XboxNetApiSvc', 'XboxGipSvc', 'WerSvc', 'Fax', 'RetailDemo', 'wisvc') {
    $svc = Get-Service $s -ErrorAction SilentlyContinue
    if ($svc) { Stop-Service $s -Force -ErrorAction SilentlyContinue; Set-Service $s -StartupType Disabled -ErrorAction SilentlyContinue }
}
Get-ScheduledTask -TaskPath '\Microsoft\Windows\Application Experience\', '\Microsoft\Windows\Customer Experience Improvement Program\', '\Microsoft\Windows\Defrag\', '\Microsoft\Windows\Maps\', '\Microsoft\Windows\Feedback\Siam\' -ErrorAction SilentlyContinue | Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null
& powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c 2>$null
Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Xbox|Bing|Solitaire|Clipchamp|Teams|OneDrive|ZuneMusic|ZuneVideo|GetHelp|Getstarted|YourPhone|People|WindowsFeedbackHub|Todos|PowerAutomate|MicrosoftOfficeHub|Copilot|OutlookForWindows|News|Weather' } | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
$p = Get-MpPreference
"$env:COMPUTERNAME tuned: $($p.ExclusionPath.Count) folder and $($p.ExclusionProcess.Count) process exclusions, low-priority scans, no scheduled full scan; services and apps trimmed"
