# Sizes of the folders on a drive, biggest first, two levels deep under the biggest ones. Reads only.
param([string]$Root = "C:\", [int]$Top = 12)
$ErrorActionPreference = "SilentlyContinue"
function Size([string]$p) {
    $total = 0L
    $stack = [System.Collections.Generic.Stack[string]]::new(); $stack.Push($p)
    while ($stack.Count) {
        $d = $stack.Pop()
        try {
            $info = [IO.DirectoryInfo]::new($d)
            foreach ($f in $info.EnumerateFiles()) { $total += $f.Length }
            foreach ($s in $info.EnumerateDirectories()) { if (-not ($s.Attributes -band [IO.FileAttributes]::ReparsePoint)) { $stack.Push($s.FullName) } }
        } catch {}
    }
    $total
}
$rows = foreach ($d in Get-ChildItem -LiteralPath $Root -Directory -Force) { if (-not ($d.Attributes -band [IO.FileAttributes]::ReparsePoint)) { [pscustomobject]@{ path = $d.FullName; gb = [math]::Round((Size $d.FullName) / 1GB, 1) } } }
$files = Get-ChildItem -LiteralPath $Root -File -Force | ForEach-Object { [pscustomobject]@{ path = $_.FullName; gb = [math]::Round($_.Length / 1GB, 1) } }
($rows + $files) | Sort-Object gb -Descending | Select-Object -First $Top | ForEach-Object { "{0,7:N1} GB  {1}" -f $_.gb, $_.path }
foreach ($big in $rows | Sort-Object gb -Descending | Select-Object -First 3) {
    "--- inside $($big.path)"
    Get-ChildItem -LiteralPath $big.path -Directory -Force | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | ForEach-Object { [pscustomobject]@{ path = $_.FullName; gb = [math]::Round((Size $_.FullName) / 1GB, 1) } } | Sort-Object gb -Descending | Select-Object -First 6 | ForEach-Object { "{0,7:N1} GB  {1}" -f $_.gb, $_.path }
}
