$ErrorActionPreference = 'Stop'
$dir = 'C:\AnyPS5 Games\_tools\stream'
$pyw = '%USERPROFILE%\AppData\Local\Programs\Python\Python314\pythonw.exe'
$py = '%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

$a = New-ScheduledTaskAction -Execute $pyw -Argument "`"$dir\start.py`"" -WorkingDirectory $dir
Register-ScheduledTask -TaskName 'AnyPS5 Discord stream' -Action $a -Principal $principal -Settings $settings -Description 'Starts OBS on the Discord Monitors profile with the program projector for Discord Go Live' -Force | Out-Null

$a = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless `"$py`" `"$dir\control.py`"" -WorkingDirectory $dir
$t = New-ScheduledTaskTrigger -AtLogOn -User $user
Register-ScheduledTask -TaskName 'AnyPS5 stream control page' -Action $a -Trigger $t -Principal $principal -Settings $settings -Description 'Phone page on port 8795 to switch the Discord stream layout' -Force | Out-Null
'tasks registered'
