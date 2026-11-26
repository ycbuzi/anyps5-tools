# Prints new EVENT / error / start lines of auto-games.log every 20 s, without holding the file open.
$p = Join-Path $PSScriptRoot "auto-games.log"
$n = @(Get-Content -LiteralPath $p).Count
while ($true) {
    Start-Sleep 20
    $all = @(Get-Content -LiteralPath $p -ErrorAction SilentlyContinue)
    if ($all.Count -gt $n) {
        $all[$n..($all.Count - 1)] | Where-Object { $_ -match 'EVENT|error:|pipeline started' } | ForEach-Object { $_ }
        $n = $all.Count
        [Console]::Out.Flush()
    }
}
