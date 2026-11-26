# Moves files or folders to the Recycle Bin, but only while the drive's bin has room for them, so Windows never has to
# purge older items (which would be a permanent delete). Items that do not fit are left in place and listed.
param([Parameter(Mandatory)][string[]]$Paths, [double]$KeepFreeGb = 1)
Add-Type -AssemblyName Microsoft.VisualBasic
$ErrorActionPreference = "Stop"
$caps = @{}
foreach ($v in Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume") { $caps[$v.PSChildName] = (Get-ItemProperty $v.PSPath).MaxCapacity * 1MB }
$guid = @{}
foreach ($vol in Get-CimInstance Win32_Volume | Where-Object DriveLetter) { $guid[$vol.DriveLetter] = ($vol.DeviceID -replace '.*Volume(\{[^}]+\}).*', '$1') }
function BinUsed([string]$drive) { (Get-ChildItem -LiteralPath "$drive\`$Recycle.Bin" -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }
function Size([string]$p) { $i = Get-Item -LiteralPath $p -Force; if ($i.PSIsContainer) { (Get-ChildItem -LiteralPath $p -Recurse -File -Force | Measure-Object Length -Sum).Sum } else { $i.Length } }
function Shown([string]$p) { $p -replace '\[[A-Za-z0-9]+\.COM\]-?', '' }
$nuke = @{}
foreach ($v in Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume") { $nuke[$v.PSChildName] = (Get-ItemProperty $v.PSPath).NukeOnDelete }
foreach ($p in $Paths) {
    if (-not (Test-Path -LiteralPath $p)) { "missing, skipped: $(Shown $p)"; continue }
    $drive = $p.Substring(0, 2)
    if ($nuke[$guid[$drive]] -eq 1) { "$drive deletes recycled files at once (NukeOnDelete=1), so recycling here is a permanent delete; left in place for Blu to delete: $(Shown $p)"; continue }
    $cap = $caps[$guid[$drive]]
    $size = [double](Size $p)
    $used = [double](BinUsed $drive)
    if (-not $cap -or $used + $size + $KeepFreeGb * 1GB -gt $cap) { "{0:N1} GB does not fit (bin {1:N1} of {2:N1} GB used), left in place: {3}" -f ($size/1GB), ($used/1GB), ($cap/1GB), (Shown $p); continue }
    $item = Get-Item -LiteralPath $p -Force
    if ($item.PSIsContainer) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }
    else { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }
    "{0:N1} GB to the Recycle Bin: {1}" -f ($size/1GB), (Shown $p)
}
