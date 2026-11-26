@echo off
rem Quest 2: back to the normal proximity sensor (sleeps when taken off).
"C:\AnyPS5-tools\platform-tools\adb.exe" connect 192.0.2.1:5555
"C:\AnyPS5-tools\platform-tools\adb.exe" -s 192.0.2.1:5555 shell am broadcast -a com.oculus.vrpowermanager.automation_disable
echo.
echo The Quest is back to normal: it sleeps when you take it off.
pause
