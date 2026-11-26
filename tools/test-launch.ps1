# Launch-tests one game folder: starts <exe>, waits up to -Seconds for a frame (FPS in the title) or an exit,
# screenshots the window, and prints the log's error lines. Leaves the game running unless -Close.
param([Parameter(Mandatory = $true)][string]$GameDir, [int]$Seconds = 600, [string]$Shot, [switch]$Close)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class TL { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
public struct RECT { public int L, T, R, B; } }
"@
$cfg = Get-Content -LiteralPath (Join-Path $GameDir "game.json") -Raw | ConvertFrom-Json
$exeName = if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }
$exe = Join-Path $GameDir "$exeName.exe"
# The assistant's 14B model holds the RTX 4070 SUPER; it switches to the 7B on the Radeon when its server goes.
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match '--port 8093\b' -and $_.CommandLine -match 'Vulkan0' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Start-Sleep 2 }
$p = & (Join-Path $PSScriptRoot "start-game.ps1") -Exe $exe -Dir $GameDir
# Inside the AnyPS5 Games window the game is not a main window; that window writes its title (with the FPS) to a file.
function Title { if ($p.MainWindowTitle) { $p.MainWindowTitle } else { $f = Join-Path $PSScriptRoot "live\title-$($p.Id).txt"; if (Test-Path -LiteralPath $f) { [string](Get-Content -LiteralPath $f -Raw) } else { "" } } }
$deadline = (Get-Date).AddSeconds($Seconds); $sawFrame = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep 2
    if ($p.HasExited) { break }
    $p.Refresh()
    if ((Title) -match 'FPS: [0-9.]+ \(([0-9]+)\)' -and [int]$Matches[1] -gt 120) { $sawFrame = $true; Start-Sleep 8; break }
}
if ($p.HasExited) { "EXITED code 0x{0:X8} after {1:N0}s" -f $p.ExitCode, ((Get-Date) - $p.StartTime).TotalSeconds }
else {
    $p.Refresh(); "running: '$(Title)', {0:N0} MB" -f ($p.WorkingSet64 / 1MB)
    if ($Shot -and $p.MainWindowHandle -ne 0) {
        [TL]::ShowWindow($p.MainWindowHandle, 6) | Out-Null; [TL]::ShowWindow($p.MainWindowHandle, 9) | Out-Null; [TL]::SetForegroundWindow($p.MainWindowHandle) | Out-Null; Start-Sleep 2
        $r = New-Object TL+RECT; [TL]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
        $b = New-Object Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T); [Drawing.Graphics]::FromImage($b).CopyFromScreen($r.L, $r.T, 0, 0, $b.Size); $b.Save($Shot); "screenshot: $Shot"
    }
    if ($Close) { Stop-Process -Id $p.Id }
}
$log = Join-Path $GameDir "anyps5.log"
if (Test-Path -LiteralPath $log) {
    Select-String -LiteralPath $log -Pattern "FATAL|unhandled exception|open failed|not implemented|unimplemented|abort" | Select-Object -First 12 | ForEach-Object { "  " + $_.Line.Substring(0, [Math]::Min(220, $_.Line.Length)) }
    $fatal = Select-String -LiteralPath $log -Pattern "^FATAL" | Select-Object -First 1
    if ($fatal) { Get-Content -LiteralPath $log | Select-Object -Skip ($fatal.LineNumber - 1) -First 14 | ForEach-Object { "  | " + $_.Substring(0, [Math]::Min(200, $_.Length)) } }
}
