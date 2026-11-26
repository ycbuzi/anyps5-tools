@echo off
rem Builds _tools\launcher\AnyPS5 Machines.exe (Machines / AnyPS5 Games / AnyPS5 tabs) from MachinesView.cs.
rem The AnyPS5 tab shows the separate AnyPS5.exe (docked over the tab, see AnyPS5.cs ConsoleApp). The running exe is moved aside to .old first.
set W=C:\AnyPS5 Games\_tools\webview2
set OUT=%~dp0AnyPS5 Machines.exe
if not "%~1"=="" set OUT=%~1
del /q "%OUT%.old" 2>nul
if exist "%OUT%" ren "%OUT%" "AnyPS5 Machines.exe.old"
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" -nologo -target:winexe -optimize -win32icon:"%~dp0..\console\anyps5.ico" -r:System.Management.dll -r:System.Web.Extensions.dll -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:"%W%\Microsoft.Web.WebView2.Core.dll" -r:"%W%\Microsoft.Web.WebView2.WinForms.dll" -out:"%OUT%" "%~dp0MachinesView.cs"
