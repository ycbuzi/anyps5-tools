@echo off
rem Quest 2: act as if worn, so it keeps drawing when set down (fixed view for capture). Undo: "Quest - normal sensor.cmd" or reboot.
"C:\AnyPS5-tools\platform-tools\adb.exe" connect 192.0.2.1:5555
"C:\AnyPS5-tools\platform-tools\adb.exe" -s 192.0.2.1:5555 shell am broadcast -a com.oculus.vrpowermanager.prox_close
echo.
echo The Quest now stays on when you set it down. Point it at your windows and leave it still.
pause
