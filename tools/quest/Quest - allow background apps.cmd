@echo off
rem Turns on the Quest's Developer option "Disable child process restrictions" (Android's limit of 32 background
rem processes per app kept killing the AnyPS5 desktop, the test pipeline and Claude inside Termux).
rem Undo: same command with true instead of false.
"C:\AnyPS5-tools\platform-tools\adb.exe" -s 192.0.2.1:5555 shell settings put global settings_enable_monitor_phantom_procs false
"C:\AnyPS5-tools\platform-tools\adb.exe" -s 192.0.2.1:5555 shell settings get global settings_enable_monitor_phantom_procs
echo.
echo If it says false above, it worked. You can close this window.
pause
