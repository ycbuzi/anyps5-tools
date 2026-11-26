param([Parameter(Mandatory = $true)][string]$Tag, [Parameter(Mandatory = $true)][string]$Regex, [string]$Build = "C:\AnyPS5-work\build-prs", [string]$Gpus = "4070", [string]$ControlTest = "", [int]$Runs = 1)
$ErrorActionPreference = "Continue"
$H = "D:\AnyPS5\scratchpad\helpers"; $Out = "$H\reports\validation"
New-Item -ItemType Directory -Force $Out | Out-Null
$status = Join-Path $Out "$Tag.status"
Remove-Item $status -ErrorAction SilentlyContinue
$cmd = Join-Path $Out "run-$Tag.cmd"
$extra = if ($ControlTest) { "-ControlTest $ControlTest" } else { "" }
$line = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$H\validation_nonadmin.ps1`" -Tag $Tag -Regex `"$Regex`" $extra -Gpus $Gpus -Runs $Runs -Build `"$Build`" -Out `"$Out`""
Set-Content -Path $cmd -Value "@echo off`r`n$line`r`n" -Encoding ascii
explorer.exe $cmd
$deadline = (Get-Date).AddMinutes(45)
while (-not (Test-Path $status) -or -not (Select-String -Path $status -Pattern "^done=" -Quiet)) {
    if ((Get-Date) -gt $deadline) { "TIMEOUT waiting for $status"; break }
    Start-Sleep 15
}
if (Test-Path $status) { Get-Content $status }
"Read it with validation_results $Tag"
