@echo off
rem Builds D:\AnyPS5 Games\AnyPS5.exe (the console-style app) from AnyPS5.cs. Run it again after editing AnyPS5.cs.
set W=C:\AnyPS5 Games\_tools\webview2
rem A running AnyPS5.exe can be renamed but not overwritten: move it aside first.
del /q "D:\AnyPS5 Games\AnyPS5.exe.old" 2>nul
if exist "D:\AnyPS5 Games\AnyPS5.exe" ren "D:\AnyPS5 Games\AnyPS5.exe" AnyPS5.exe.old
"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" -nologo -target:winexe -optimize -win32icon:"%~dp0anyps5.ico" -r:System.Web.Extensions.dll -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:"%W%\Microsoft.Web.WebView2.Core.dll" -r:"%W%\Microsoft.Web.WebView2.WinForms.dll" -out:"D:\AnyPS5 Games\AnyPS5.exe" "%~dp0AnyPS5.cs" "%~dp0host-*.cs"
