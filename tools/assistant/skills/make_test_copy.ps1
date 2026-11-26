# A separate copy of a game built on the C: build of AnyPS5 (main + #11580). The original game folder is only read.
param([Parameter(Mandatory = $true)][string]$Name, [string]$Build = "C:\AnyPS5-work\build", [string]$Into = "")
$ErrorActionPreference = "Continue"
(Get-Process -Id $PID).PriorityClass = "BelowNormal"
$Root = "D:\AnyPS5 Games"; $Orig = Join-Path $Root $Name; $Test = if ($Into) { $Into } else { Join-Path $Root "_test\$Name" }; $Src = Join-Path $Root "_test\_src\$Name"
$Unwrap = "C:\AnyPS5-work\src\tools\unwrap_self.py"
# 11-08: a Spider-Man 2 fix copy (260 GB) filled C: to 0.2 GB free and slowed Blu's PC. The copy needs about the size
# of the game's app0; refuse unless the target drive keeps that plus 40 GB free (C: is the SSD Windows runs from).
$need = (Get-ChildItem -LiteralPath (Join-Path $Orig "app0") -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
$drive = Get-PSDrive -Name $Test.Substring(0, 1)
if ($drive -and $drive.Free -lt $need + 40GB) { "STOP: not enough disk on $($drive.Name): for a test copy (needs {0:N0} GB + 40 GB spare, has {1:N0} GB)" -f ($need / 1GB), ($drive.Free / 1GB); exit 1 }
$old = Get-Content -LiteralPath "$Orig\game.json" -Raw | ConvertFrom-Json
Add-Type -AssemblyName Microsoft.VisualBasic
$lastBuild = if (Test-Path -LiteralPath "$Test\game.json") { (Get-Content -LiteralPath "$Test\game.json" -Raw | ConvertFrom-Json).anyps5Build } else { "" }
if ($lastBuild -ne $Build -and (Test-Path -LiteralPath "$Test\libs")) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("$Test\libs", "OnlyErrorDialogs", "SendToRecycleBin"); "old libs of this copy went to the Recycle Bin, so another build's libraries cannot mix in" }
New-Item -ItemType Directory -Force $Test, $Src | Out-Null
$eboot = "$Orig\app0\eboot.bin"
$b = New-Object byte[] 4; $s = [IO.File]::OpenRead($eboot); [void]$s.Read($b, 0, 4); $s.Close()
$magic = ($b | ForEach-Object { $_.ToString("x2") }) -join ""
"eboot magic: $magic"
if ($magic -in "4f153d1d", "5414f5ee") { python -I $Unwrap $eboot "$Src\eboot.elf"; "unwrap exit $LASTEXITCODE" }
elseif ($magic -eq "7f454c46") { Copy-Item -LiteralPath $eboot "$Src\eboot.elf" }
else { "STOP: eboot is $magic"; exit 1 }
foreach ($m in Get-ChildItem -LiteralPath "$Orig\app0" -Recurse -File -Force | Where-Object { $_.Extension -in ".prx", ".sprx" -and $_.FullName -notmatch '\\(sce_sys|fakelib)\\' }) {
    $rel = $m.FullName.Substring("$Orig\app0".Length + 1); $dst = Join-Path $Src $rel
    New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
    $s = [IO.File]::OpenRead($m.FullName); [void]$s.Read($b, 0, 4); $s.Close()
    if ((($b | ForEach-Object { $_.ToString("x2") }) -join "") -in "4f153d1d", "5414f5ee") { python -I $Unwrap $m.FullName $dst | Out-Null } else { Copy-Item -LiteralPath $m.FullName $dst }
}
$dirs = @(Get-ChildItem -LiteralPath $Src -Recurse -File | Where-Object Extension -in ".prx", ".sprx" | ForEach-Object { $r = $_.DirectoryName.Substring($Src.Length).TrimStart('\').Replace('\', '/'); if ($r) { $r } else { "." } } | Select-Object -Unique | Where-Object { $_ -notin "prx", "sce_module", "sce_modules" })
$cfg = [ordered]@{ title = $old.title; titleId = $old.titleId; dump = $Src; resources = "$Orig\app0"; eboot = "eboot.elf"; anyps5Build = $Build }
if ($old.exeName) { $cfg.exeName = $old.exeName }
if ($dirs) { $cfg.moduleDirs = $dirs }
$cfg | ConvertTo-Json | Set-Content -LiteralPath "$Test\game.json" -Encoding ascii
$env:PATH = "C:\AnyPS5-work\mingw64\bin;$env:PATH"
powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\_tools\build-game.ps1" -GameDir $Test 2>&1 | Select-Object -Last 14 | ForEach-Object { "$_".Substring(0, [Math]::Min(170, "$_".Length)) }
"build exit $LASTEXITCODE"
Get-ChildItem -LiteralPath $Test -Filter *.exe | ForEach-Object { "$($_.Name) $($_.Length)" }
"libs: " + @(Get-ChildItem -LiteralPath "$Test\libs" -File -ErrorAction SilentlyContinue).Count
