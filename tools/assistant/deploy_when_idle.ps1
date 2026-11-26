# Deploys a rebuilt AnyPS5 Games window only while Blu is away: no keyboard or mouse input for -IdleMinutes, no game
# running, and the assistant not working. Then it compiles Launcher.cs, swaps the exe (the old one is kept) and reopens
# the window. Progress goes to _tools\assistant\app-updates.log, not the live console (Blu: the console is for games
# and code only).
# A Discord stream (Go Live) also blocks it: friends may watch the window while Blu is away, and reopening it ends
# the share. -What says what the new window brings (for the log).
param([int]$IdleMinutes = 20, [int]$GiveUpHours = 24, [string]$What = "the changes")
$ErrorActionPreference = "Stop"
$Games = "D:\AnyPS5 Games"
$Tools = Join-Path $Games "_tools"
$Log = Join-Path $Tools "assistant\app-updates.log"
function Say([string]$text) {
    [IO.File]::AppendAllText($Log, ("{0}  {1}`r`n" -f (Get-Date -Format "MM-dd HH:mm:ss"), $text), [Text.Encoding]::UTF8)
}
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class Idle {
    [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO info);
    public static double Minutes() { var i = new LASTINPUTINFO(); i.cbSize = (uint)Marshal.SizeOf(i); GetLastInputInfo(ref i); return ((uint)Environment.TickCount - i.dwTime) / 60000.0; }
}
'@
function Game-Running {
    [bool](Get-CimInstance Win32_Process -Filter "Name LIKE '%.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Games + "\", "OrdinalIgnoreCase") -and $_.ExecutablePath -notlike "*\_tools\*" -and $_.Name -ne "AnyPS5 Games.exe" })
}
function Assistant-Busy {
    $f = Join-Path $Tools "live\ai-busy.flag"
    (Test-Path -LiteralPath $f) -and ((Get-Date) - (Get-Item -LiteralPath $f).LastWriteTime).TotalMinutes -lt 15
}
function Discord-Streaming {
    if (-not (Get-Process -Name "Discord" -ErrorAction SilentlyContinue)) { return $false }
    foreach ($cap in "graphicsCaptureProgrammatic", "graphicsCaptureWithoutBorder") {
        $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\$cap\NonPackaged"
        if (-not (Test-Path $key)) { continue }
        foreach ($app in Get-ChildItem $key | Where-Object { $_.PSChildName -match 'discord' -and (Test-Path -LiteralPath ($_.PSChildName -replace '#', '\')) }) {
            $start = $app.GetValue("LastUsedTimeStart"); $stop = $app.GetValue("LastUsedTimeStop")
            if ($start -is [long] -and $start -gt 0 -and $stop -is [long] -and $stop -eq 0) { return $true }
        }
    }
    $false
}
# 11-04 the exe is set to "Run as administrator" (Compatibility, HKCU AppCompatFlags RUNASADMIN; not set by our tools).
# explorer.exe then needs a UAC prompt nobody is there to answer, so the 05:14 and 12:19 reopens never started. This
# script already runs elevated, so the window is started directly then; otherwise through explorer (not elevated).
function Open-Window {
    $exe = Join-Path $Games 'AnyPS5 Games.exe'
    $layers = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers' -ErrorAction SilentlyContinue
    $admin = $layers -and "$($layers.$exe)" -match 'RUNASADMIN'
    if ($admin) { Start-Process -FilePath $exe -WorkingDirectory $Games }
    else { Start-Process -FilePath "explorer.exe" -ArgumentList "`"$exe`"" }
}
Say "EVENT APP UPDATE WAITING: the new AnyPS5 Games window ($What) goes in once nobody has used the PC for $IdleMinutes minutes and Discord is not streaming"
$until = (Get-Date).AddHours($GiveUpHours)
while ((Get-Date) -lt $until) {
    if ([Idle]::Minutes() -ge $IdleMinutes -and -not (Game-Running) -and -not (Assistant-Busy) -and -not (Discord-Streaming)) { break }
    Start-Sleep 60
}
if ((Get-Date) -ge $until) { Say "EVENT APP UPDATE NOT DONE: the PC was never idle for $IdleMinutes minutes in $GiveUpHours hours"; exit 1 }
# The window closes before the swap: since 11-03 _tools (where the old exe is kept) is on C: and the exe on D:, so the
# swap is a copy and a delete, and a running exe cannot be deleted (two updates failed with an empty message).
$open = @(Get-Process -Name "AnyPS5 Games" -ErrorAction SilentlyContinue)
foreach ($p in $open) { [void]$p.CloseMainWindow() }
foreach ($p in $open) { if (-not $p.WaitForExit(20000)) { Stop-Process -Id $p.Id -Force } }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'assistant\.py"? --serve' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$result = & python -I -c "import sys; sys.path.insert(0, r'$Tools\assistant'); import work_skills as w; print(w.rebuild_app(True))" 2>&1 | Out-String
Say "  app update: $($result.Trim())"
if ("$result" -notmatch 'Compiled and deployed') {
    Say "EVENT APP UPDATE FAILED: $($result.Trim()); the old window is reopened"
    Open-Window
    exit 1
}
Open-Window
# 11-04 the 05:14 update was reported failed after 20 s, yet the same build opened fine by hand at 10:24: give a slow
# first start (Defender scanning a new exe) a minute.
$up = $false
for ($i = 0; $i -lt 12 -and -not $up; $i++) { Start-Sleep 5; $up = [bool](Get-Process -Name "AnyPS5 Games" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }) }
if ($up) { Say "EVENT APP UPDATED: the AnyPS5 Games window was reopened with $What (old build kept in _tools\launcher)" }
else { Say "EVENT APP UPDATE FAILED: the new window did not stay open; the old build is in _tools\launcher\AnyPS5 Games.exe.before-*" }
