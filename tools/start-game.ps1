# Starts a relinked game without Windows loader pop-ups ("Entry Point Not Found", "System Error ... .dll was not
# found"). Start-Process goes through the shell, and those boxes then wait for a click and hang the pipeline (11-08,
# Spider-Man 2 on 9fb2d9bb83). Started directly with the error mode set, the game exits with 0xC0000135/0xC0000139
# instead and prints the module that failed. Returns the System.Diagnostics.Process.
param([Parameter(Mandatory)][string]$Exe, [string]$Dir = (Split-Path $Exe), [string]$Arguments = "")
if (-not ("QuietStart.Err" -as [type])) {
    Add-Type 'namespace QuietStart { using System.Runtime.InteropServices; public static class Err { [DllImport("kernel32.dll")] public static extern uint SetErrorMode(uint m); } }'
}
[void][QuietStart.Err]::SetErrorMode(0x8003)
$si = New-Object System.Diagnostics.ProcessStartInfo $Exe
$si.UseShellExecute = $false
$si.WorkingDirectory = $Dir
if ($Arguments) { $si.Arguments = $Arguments }
[System.Diagnostics.Process]::Start($si)
