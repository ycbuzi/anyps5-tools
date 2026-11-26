# Screenshots the window of the running game from one game folder: shot.ps1 -GameDir <dir> -Out <png>
param([Parameter(Mandatory = $true)][string]$GameDir, [Parameter(Mandatory = $true)][string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class SH { [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
public struct RECT { public int L, T, R, B; } }
"@
$p = Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($GameDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
if (-not $p) { "not running"; exit 1 }
[SH]::ShowWindow($p.MainWindowHandle, 6) | Out-Null; [SH]::ShowWindow($p.MainWindowHandle, 9) | Out-Null; [SH]::SetForegroundWindow($p.MainWindowHandle) | Out-Null; Start-Sleep 2
$r = New-Object SH+RECT; [SH]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$b = New-Object Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T); [Drawing.Graphics]::FromImage($b).CopyFromScreen($r.L, $r.T, 0, 0, $b.Size); $b.Save($Out)
$p.Refresh(); "'$($p.MainWindowTitle)' -> $Out"
