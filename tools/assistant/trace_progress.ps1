# Shows a long step the pipeline waits on without printing (a copy from another drive inside a build) in the live
# console: one top line for the window's "Now:" line, then each file as it lands, with its size, and a total every minute.
param([Parameter(Mandatory = $true)][int]$ProcessId, [Parameter(Mandatory = $true)][string]$Folder, [double]$TotalGB = 0, [string]$Label = "copying", [string]$Verb = "copied")
$Trace = "D:\AnyPS5 Games\_tools\auto-games-trace.log"
# Download-site tags in folder names ("[SITE.COM]") never reach the console: Blu does not want site names shown.
function Out([string]$text) { $text = $text -replace '\s*\[[A-Za-z0-9-]+\.(COM|NET|ORG|IO)\]\s*-?', ''; [IO.File]::AppendAllText($Trace, ("{0}  {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text), [Text.Encoding]::UTF8) }
$Folder = $Folder.TrimEnd('\')
$seen = @{}
foreach ($f in Get-ChildItem -LiteralPath $Folder -Recurse -File -Force -ErrorAction SilentlyContinue) { $seen[$f.FullName] = $true }
Out $Label
Out ("  {0:N0} files already there" -f $seen.Count)
$lastTotal = Get-Date
while ($true) {
    $running = [bool](Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
    $all = @(Get-ChildItem -LiteralPath $Folder -Recurse -File -Force -ErrorAction SilentlyContinue)
    foreach ($f in $all | Where-Object { -not $seen.ContainsKey($_.FullName) } | Sort-Object LastWriteTime) {
        $seen[$f.FullName] = $true
        Out ("  $Verb {0} ({1:N1} MB)" -f $f.FullName.Substring($Folder.Length + 1), ($f.Length / 1MB))
    }
    if (((Get-Date) - $lastTotal).TotalSeconds -ge 60 -or -not $running) {
        $gb = ($all | Measure-Object Length -Sum).Sum / 1GB
        Out ("  {0:N1}{1} GB, {2:N0} files" -f $gb, $(if ($TotalGB -gt 0) { " of {0:N1}" -f $TotalGB } else { "" }), $all.Count)
        $lastTotal = Get-Date
    }
    if (-not $running) { break }
    Start-Sleep 5
}
Out "  copy finished; the build goes on"
