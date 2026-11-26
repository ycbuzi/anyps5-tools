@echo off
rem Builds and runs the AnyPS5 view self-test (selftest.cs): a test window hosts the view like the Machines tab, types a
rem PIN and walks the menus with real key presses, then plays a stand-in game (fakegame.cs) inside the view, types into
rem it and closes it. Needs the desktop for about 25 seconds. Prints PASS/FAIL; exit code = number of failures.
setlocal
set W=C:\AnyPS5 Games\_tools\webview2
set CSC=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe
set T=%~dp0..\live\selftest
if not exist "%T%" mkdir "%T%"
"%CSC%" -nologo -target:exe -main:SelfTest -r:System.Web.Extensions.dll -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:"%W%\Microsoft.Web.WebView2.Core.dll" -r:"%W%\Microsoft.Web.WebView2.WinForms.dll" -out:"%T%\anyps5-selftest.exe" "%~dp0selftest.cs" "%~dp0AnyPS5.cs" "%~dp0host-*.cs" || exit /b 99
"%CSC%" -nologo -target:winexe -out:"%T%\FakeGame.exe" "%~dp0fakegame.cs" || exit /b 98
set ANYPS5_FAKE_GAME=%T%\FakeGame.exe
"%T%\anyps5-selftest.exe" %*
set RC=%ERRORLEVEL%
rmdir /s /q "%T%" 2>nul
rem The test window above is a copy; the live check runs the real deployed programs (11-15: the copy passed while the
rem real AnyPS5 Machines.exe could not start the screen at all).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0live-check.ps1"
set /a RC=%RC%+%ERRORLEVEL%
exit /b %RC%
