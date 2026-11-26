# Lists leftovers of games the pipeline has already built (archives, unpacked dumps, set-aside copies) with sizes,
# and whether each fits in its drive's Recycle Bin. Lists only; moves nothing. Writes _tools\live\cleanup-inventory.json.
param([string]$Out = "D:\AnyPS5 Games\_tools\live\cleanup-inventory.json")
$ErrorActionPreference = "SilentlyContinue"
$state = Get-Content "D:\AnyPS5 Games\_tools\auto-state.json" -Raw | ConvertFrom-Json
$built = @{}
foreach ($p in $state.PSObject.Properties) { if ($p.Value.status -in @("done", "built", "tested")) { $built[$p.Name] = $p.Value.status } }
function Clean([string]$s) { ($s -replace '\[[A-Za-z0-9]+\.COM\]-?', '').Trim() }
function Size([string]$path) {
    $i = Get-Item -LiteralPath $path -Force
    if (-not $i.PSIsContainer) { return $i.Length }
    (Get-ChildItem -LiteralPath $path -Recurse -File -Force | Measure-Object Length -Sum).Sum
}
function Owner([string]$name) {
    $n = (Clean $name).ToLowerInvariant()
    foreach ($g in $built.Keys) {
        $k = ($g -replace '\s*\(PPSA\d+\)', '').ToLowerInvariant()
        if ($n -like "*$k*" -or $k -like "*$n*") { return $g }
    }
    $id = [regex]::Match($name, 'PPSA\d{5}').Value
    if ($id) { foreach ($g in $built.Keys) { if ($g -match $id -or ($state.$g.gameDir -match $id)) { return $g } } }
    ""
}
$bins = @{}
foreach ($v in Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume") {
    $props = Get-ItemProperty $v.PSPath
    $bins[$v.PSChildName] = $props.MaxCapacity
}
$volumeGuid = @{}
foreach ($vol in Get-CimInstance Win32_Volume | Where-Object DriveLetter) { $volumeGuid[$vol.DriveLetter] = ($vol.DeviceID -replace '.*Volume(\{[^}]+\}).*', '$1') }
$rows = @()
$roots = @(
    @{ path = "D:\ps5 games dumps from ps5\_finished archives (safe to delete)"; why = "finished archive" },
    @{ path = "D:\ps5 games dumps from ps5"; why = "unpacked dump" },
    @{ path = "G:\ps5 games dumps from ps5"; why = "unpacked dump" },
    @{ path = "G:\ps5 downloads"; why = "download" },
    @{ path = "D:\_set aside"; why = "set aside (broken copy)" },
    @{ path = "G:\_set aside"; why = "set aside (broken copy)" }
)
foreach ($r in $roots) {
    if (-not (Test-Path -LiteralPath $r.path)) { continue }
    foreach ($item in Get-ChildItem -LiteralPath $r.path -Force | Where-Object { $_.Name -notlike "_finished archives*" -and $_.Name -ne '$RECYCLE.BIN' -and $_.Name -ne 'System Volume Information' }) {
        $owner = if ($r.why -like "set aside*") { "(set aside)" } else { Owner $item.Name }
        if (-not $owner) { continue }
        $partial = @(Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -Filter *.crdownload).Count -gt 0 -or $item.Extension -eq ".crdownload"
        if ($partial) { continue }
        $bytes = Size $item.FullName
        $drive = $item.FullName.Substring(0, 2)
        $maxMb = $bins[$volumeGuid[$drive]]
        $rows += [pscustomobject]@{ path = $item.FullName; shown = (Clean $item.Name); kind = $r.why; game = $owner; status = $built[$owner]; gb = [math]::Round($bytes / 1GB, 2); drive = $drive; binMaxGb = $(if ($maxMb) { [math]::Round($maxMb / 1024, 1) } else { $null }) }
    }
}
$rows | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $Out -Encoding UTF8
$rows | Sort-Object drive, kind, gb | ForEach-Object { "{0} {1,-26} {2,7:N2} GB  {3}  [{4}]" -f $_.drive, $_.kind, $_.gb, $_.shown, $_.game }
"bins (GB max): " + (($bins.GetEnumerator() | ForEach-Object { $d = ($volumeGuid.GetEnumerator() | Where-Object Value -eq $_.Key).Name; "$d=$([math]::Round($_.Value/1024,1))" }) -join ", ")
