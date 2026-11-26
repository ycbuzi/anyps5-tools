# Saves a game window as you see it: its title bar (game name and AnyPS5's FPS) and the game picture, cut at the
# window's visible edges (DWM extended frame bounds). GetWindowRect also covers the invisible resize border, where
# whatever is behind the window shows through (a browser download bar once got into a showcase image that way).
# A game inside another window (AnyPS5 Machines takes the pipeline's games in, off screen or in its Games tab; the AnyPS5
# Games window with "Put it inside") has no window of its own on screen: its picture is drawn with PrintWindow under a
# title strip with the same text (11-08).
# usage: window-shot.ps1 -GameDir <dir> -Out <png>
param([Parameter(Mandatory = $true)][string]$GameDir, [Parameter(Mandatory = $true)][string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class WS { [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int a, out RECT r, int s);
public struct RECT { public int L, T, R, B; }
public delegate bool P(IntPtr h, IntPtr l);
[DllImport("user32.dll")] static extern bool EnumWindows(P cb, IntPtr l);
[DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, P cb, IntPtr l);
[DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
public static string Title(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
public static IntPtr Inside(uint pid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((top, l) => {
        EnumChildWindows(top, (h, l2) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid) { found = h; return false; } return true; }, IntPtr.Zero);
        return found == IntPtr.Zero;
    }, IntPtr.Zero);
    return found;
} }
"@
$games = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($GameDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) })
$p = $games | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($p) {
    $r = New-Object WS+RECT
    [void][WS]::DwmGetWindowAttribute($p.MainWindowHandle, 9, [ref]$r, 16)
    $b = New-Object Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
    [Drawing.Graphics]::FromImage($b).CopyFromScreen($r.L, $r.T, 0, 0, $b.Size)
    $b.Save($Out, [Drawing.Imaging.ImageFormat]::Png)
    "{0}x{1} '{2}' -> {3}" -f $b.Width, $b.Height, $p.MainWindowTitle, $Out
    exit 0
}
foreach ($g in $games) {
    $h = [WS]::Inside([uint32]$g.Id)
    if ($h -eq [IntPtr]::Zero) { continue }
    $c = New-Object WS+RECT; [void][WS]::GetClientRect($h, [ref]$c)
    $w = $c.R - $c.L; $hh = $c.B - $c.T
    if ($w -lt 50 -or $hh -lt 50) { continue }
    $title = [WS]::Title($h)
    $pic = New-Object Drawing.Bitmap $w, $hh
    $pg = [Drawing.Graphics]::FromImage($pic); $dc = $pg.GetHdc(); [void][WS]::PrintWindow($h, $dc, 2); $pg.ReleaseHdc($dc); $pg.Dispose()
    $bar = 30
    $b = New-Object Drawing.Bitmap $w, ($hh + $bar)
    $g2 = [Drawing.Graphics]::FromImage($b)
    $g2.Clear([Drawing.Color]::FromArgb(32, 32, 32))
    $g2.DrawString($title, (New-Object Drawing.Font("Segoe UI", 10)), [Drawing.Brushes]::White, 8, 6)
    $g2.DrawImage($pic, 0, $bar, $w, $hh)
    $b.Save($Out, [Drawing.Imaging.ImageFormat]::Png)
    "{0}x{1} '{2}' (inside another window) -> {3}" -f $b.Width, $b.Height, $title, $Out
    exit 0
}
"no game window"; exit 1
