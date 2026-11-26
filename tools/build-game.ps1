# Builds (or rebuilds) one game folder from its dump with the AnyPS5 build named in _tools\settings.json.
# usage: build-game.ps1 -GameDir "D:\AnyPS5 Games\Coffee Talk (PPSA07057)"
#
# Reads <GameDir>\game.json:
#   title       display name; the executable is "<title>.exe" unless exeName is given
#   titleId     e.g. PPSA07057
#   dump        folder holding the clean decrypted eboot.bin (never modified)
#   resources   (optional) the dump's full app folder when "dump" holds only the decrypted executables; copied first
#   sceSys      (optional) an outer sce_sys folder whose files fill in what the dump's own sce_sys lacks
#   moduleDirs  extra module folders inside the dump to convert, e.g. ["Media/Modules", "Media/Plugins"]
#   exeName     (optional) executable name without .exe
#   eboot       (optional) the executable inside the dump to convert, default eboot.bin (it must be a plain ELF;
#               a signed SELF container is refused by the relinker)
#   skipSceModule (optional) true: leave the dump's sce_module\*.prx unconverted (homebrew that bundles its own
#               libc.prx runs on AnyPS5's libc instead)
#
# Result, in the game folder itself (double-click the .exe to play; Windows starts it in its own folder,
# so the game's saves (_sd) and trophies (_trophy) land beside it):
#   <exe>.exe, libs\, app0\      replaced by every rebuild, and only after the whole build succeeded
#   _sd\, _trophy\, anyps5.log   written by the game; never touched here
#   mods\patches\*.toml          AnyPS5 game patches; a leading "_" disables one
#   mods\files\...               files laid over app0\ (same relative paths)
param([Parameter(Mandatory = $true)][string]$GameDir)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$GameDir = (Resolve-Path $GameDir).Path
# A game folder moved to G: keeps a junction at its D: path; the real folder decides whether app0 can be hard links.
$gameItem = Get-Item -LiteralPath $GameDir
if ($gameItem.LinkType -eq "Junction") { $GameDir = @($gameItem.Target)[0] }
$settings = Get-Content (Join-Path $PSScriptRoot "settings.json") -Raw | ConvertFrom-Json
$cfg = Get-Content (Join-Path $GameDir "game.json") -Raw | ConvertFrom-Json
$exeName = if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }
# A game can pin an AnyPS5 build in its game.json ("anyps5Build"); the pipeline does that when a newer AnyPS5
# broke a game that worked.
$BuildRoot = if ($cfg.anyps5Build) { $cfg.anyps5Build } else { $settings.anyps5Build }
$Relinker = Join-Path $BuildRoot "core\relinker\relinker.exe"
$Libs = Join-Path $BuildRoot "core\libs\libs"
$Dump = $cfg.dump
$Eboot = if ($cfg.eboot) { $cfg.eboot } else { "eboot.bin" }
$Stage = Join-Path $GameDir ".build"
"Building $($cfg.title) ($($cfg.titleId))"
# When the dump or resources folder was deleted after a build, the game's own app0 (a whole copy, or hard links to
# the same data) stands in for it: its files are linked into the new build, and the old names go when it is replaced.
$OwnApp0 = Join-Path $GameDir "app0"
$ownWhole = (Test-Path -LiteralPath (Join-Path $OwnApp0 $Eboot)) -and (Test-Path -LiteralPath (Join-Path $OwnApp0 "sce_sys"))
$fromOwnApp0 = $false
if (-not (Test-Path -LiteralPath (Join-Path $Dump $Eboot)) -and $ownWhole) { $Dump = $OwnApp0; $fromOwnApp0 = $true; "  the dump is gone; rebuilding from this game's own app0" }
$Resources = $cfg.resources
if ($fromOwnApp0) { $Resources = $OwnApp0 }
elseif ($Resources -and -not (Test-Path -LiteralPath $Resources) -and $ownWhole) { $Resources = $OwnApp0; "  the resources folder is gone; using this game's own app0" }
foreach ($p in $Relinker, $Libs, (Join-Path $Dump $Eboot)) { if (-not (Test-Path -LiteralPath $p)) { throw "missing: $p" } }
if (Test-Path -LiteralPath $Stage) { & cmd.exe /d /c "rd /s /q `"$Stage`" 2>nul"; if (Test-Path -LiteralPath $Stage) { throw "an earlier .build folder is still in use; close the game and try again" } }
$s = New-Item -ItemType Directory -Force $Stage; $s.Attributes = $s.Attributes -bor [IO.FileAttributes]::Hidden
New-Item -ItemType Directory -Force (Join-Path $Stage "libs"), (Join-Path $Stage "app0") | Out-Null
foreach ($d in "mods\patches", "mods\files", "mods\libs") { New-Item -ItemType Directory -Force (Join-Path $GameDir $d) | Out-Null }

# 1. AnyPS5 libraries, pinned into this game (plus the MinGW runtime DLLs they need).
Copy-Item (Join-Path $Libs "*.prx") (Join-Path $Stage "libs")
foreach ($dll in "libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll") { Copy-Item (Join-Path $settings.mingwBin $dll) (Join-Path $Stage "libs") }
"  libs: " + @(Get-ChildItem (Join-Path $Stage "libs") -Filter *.prx).Count + " AnyPS5 libraries"
# mods\libs\*.prx replace AnyPS5 libraries of the same name (for example one built from an unmerged AnyPS5 PR).
$libOverrides = @(Get-ChildItem -LiteralPath (Join-Path $GameDir "mods\libs") -Filter *.prx -File -ErrorAction SilentlyContinue)
foreach ($o in $libOverrides) { Copy-Item -LiteralPath $o.FullName -Destination (Join-Path $Stage "libs") -Force }
if ($libOverrides.Count) { "  library overrides: " + ($libOverrides.Name -join ", ") }

# 2. The game's own files (a copy; the dump is never modified). Dumps that keep the decrypted executables in a
#    separate folder hold the game's data in "resources": copy that first (without the dumper's leftovers), then
#    lay the decrypted executables and modules over it.
#    On the same NTFS volume the game's files are hard-linked instead of copied: no second copy on disk and no
#    wait. Nothing writes through a link: files the dump or mods\files provide are never linked (they are fresh
#    copies), and a rebuild only deletes links, never the dump's files.
if ($Resources) {
    $res = (Resolve-Path -LiteralPath $Resources).Path.TrimEnd('\')
    $app0 = Join-Path $Stage "app0"
    $sameVolume = [IO.Path]::GetPathRoot($res) -eq [IO.Path]::GetPathRoot($Stage) -and (Get-Volume -DriveLetter $res[0] -ErrorAction SilentlyContinue).FileSystem -eq "NTFS"
    if ($sameVolume) {
        if (-not ("BuildGameLink" -as [type])) { Add-Type 'using System.Runtime.InteropServices; public static class BuildGameLink { [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] public static extern bool CreateHardLink(string link, string target, System.IntPtr reserved); }' }
        $provided = @{}
        if (-not $fromOwnApp0) { foreach ($f in Get-ChildItem -LiteralPath $Dump -Recurse -File) { $provided[$f.FullName.Substring($Dump.TrimEnd('\').Length + 1).ToLowerInvariant()] = $true } }
        foreach ($f in Get-ChildItem -LiteralPath (Join-Path $GameDir "mods\files") -Recurse -File -ErrorAction SilentlyContinue) { $provided[$f.FullName.Substring((Join-Path $GameDir "mods\files").Length + 1).ToLowerInvariant()] = $true }
        $linked = 0
        foreach ($f in Get-ChildItem -LiteralPath $res -Recurse -File -Force) {
            $rel = $f.FullName.Substring($res.Length + 1)
            $parts = $rel.Split('\')
            if ($parts[-1] -eq "ampr_emu.index" -or ($parts[0..($parts.Count - 2)] | Where-Object { $_ -in "decrypted", "fakelib" })) { continue }
            if ($provided[$rel.ToLowerInvariant()]) { continue }
            $dst = Join-Path $app0 $rel
            $dir = Split-Path $dst -Parent
            if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
            if (-not [BuildGameLink]::CreateHardLink($dst, $f.FullName, [IntPtr]::Zero)) { throw "linking $rel failed (error $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" }
            $linked++
        }
        "  game files linked: $linked"
    } else {
        # From another drive this is a real copy (21 GB for Maiden of Black Water took over 30 minutes): each file is
        # printed as it is copied, with a running total, so the live console shows it file by file (Blu asked for that).
        $total = (Get-ChildItem -LiteralPath $res -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        "  copying the game's files from another drive: {0:N1} GB" -f ($total / 1GB)
        $done = 0
        & robocopy $res $app0 /E /XD decrypted fakelib /XF ampr_emu.index /NDL /NJH /NJS /NP /NC /BYTES /FP | ForEach-Object {
            if ("$_" -match '^\s*(\d+)\s+(.+?)\s*$') {
                $done += [double]$Matches[1]
                [Console]::WriteLine(("    copying {0} ({1:N1} MB; {2:N1} of {3:N1} GB)" -f $Matches[2].Substring([Math]::Min($Matches[2].Length, $res.Length + 1)), ([double]$Matches[1] / 1MB), ($done / 1GB), ($total / 1GB)))
            }
        }
        if ($LASTEXITCODE -ge 8) { throw "copying the game's files from $($cfg.resources) failed (robocopy $LASTEXITCODE)" }
    }
}
if (-not $fromOwnApp0) {
    & robocopy $Dump (Join-Path $Stage "app0") /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the dump failed (robocopy $LASTEXITCODE)" }
}
"  game files: " + @(Get-ChildItem (Join-Path $Stage "app0") -Recurse -File).Count + " in app0"
# Dumps that split the app keep param.json, trophy2\ and uds\ in an outer sce_sys; fill in what the clean copy lacks.
if ($cfg.sceSys -and (Test-Path -LiteralPath $cfg.sceSys)) {
    & robocopy $cfg.sceSys (Join-Path $Stage "app0\sce_sys") /E /XC /XN /XO /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying sce_sys from $($cfg.sceSys) failed (robocopy $LASTEXITCODE)" }
}
if (-not (Test-Path (Join-Path $Stage "app0\sce_sys\param.json"))) { "  warning: no app0\sce_sys\param.json (some titles need it for their title ID)" }

# 3. Enabled code patches, merged into one file for the relinker.
$patches = @(Get-ChildItem (Join-Path $GameDir "mods\patches") -Filter *.toml -ErrorAction SilentlyContinue | Where-Object { -not $_.Name.StartsWith("_") } | Sort-Object Name)
$patchArgs = @("--no-patches")
if ($patches.Count) {
    $merged = Join-Path $Stage "mods.patches.toml"
    ($patches | ForEach-Object { "# from mods\patches\$($_.Name)"; Get-Content $_.FullName -Raw }) | Set-Content -Path $merged -Encoding utf8
    $patchArgs = @("--patches", $merged)
}
"  code patches: " + $(if ($patches.Count) { ($patches.Name -join ", ") } else { "none" })

# 4. Convert. Relative module folders resolve against the dump.
$exe = Join-Path $Stage "$exeName.exe"
# The relinker already searches sce_module(s) and prx next to the eboot; naming them again is an error.
$moduleArgs = @(); foreach ($m in @($cfg.moduleDirs)) { if ($m -and $m.Trim('/', '\') -notin "prx", "sce_module", "sce_modules") { $moduleArgs += @("--module-dir", $m) } }
if ($cfg.skipSceModule) { $moduleArgs += "--skip-sce-module" }
Push-Location -LiteralPath $Dump
try {
    # Each relinker line is also printed as it comes, so the AnyPS5 Games window can follow the conversion.
    [Console]::WriteLine("  relinker " + (@("--windows", "--windows-gui", "--registry") + $patchArgs + $moduleArgs + @($Eboot, $exe) -join ' '))
    $out = @(& $Relinker --windows --windows-gui --registry @patchArgs @moduleArgs $Eboot $exe 2>&1 | ForEach-Object { $l = "$_"; [Console]::WriteLine("    " + $l); $l })
    $code = $LASTEXITCODE
} finally { Pop-Location }
$out | Set-Content -Path (Join-Path $Stage "relinker-output.txt") -Encoding utf8
if ($code -ne 0) { $out | Select-Object -Last 8; throw "relinker failed ($code); full output in .build\relinker-output.txt" }
"  converted: $exeName.exe"

# 5. Replacement files over the game's files.
$files = Join-Path $GameDir "mods\files"
$fileMods = @(Get-ChildItem $files -Recurse -File -ErrorAction SilentlyContinue)
# Each one is a fresh copy (the linking above leaves these paths out, so no dump file is overwritten).
foreach ($m in $fileMods) {
    $dst = Join-Path (Join-Path $Stage "app0") $m.FullName.Substring($files.Length + 1)
    New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }
    Copy-Item -LiteralPath $m.FullName -Destination $dst
}
"  replacement files: $($fileMods.Count)"

# 6. Everything succeeded: swap in the new exe, libs\ and app0\. Saves, trophies, logs and mods stay.
# Full literal paths: PowerShell 5.1 turns a file object into its bare name, which then resolves against the
# current location (that removed nothing and stopped the first Coffee Talk rebuild).
# The old version is moved aside first (retrying while a scanner briefly holds a file), then the new one moved in;
# deleting the old one comes last and may be finished by the next build. Deleting it outright failed halfway once
# (a freshly written .prx held open) and left the game without an .exe.
function Move-WithRetry([string]$from, [string]$to) {
    for ($try = 1; ; $try++) {
        try { Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return }
        catch { if ($try -ge 20) { throw }; Start-Sleep -Milliseconds 500 }
    }
}
$old = Join-Path $GameDir (".old-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
$o = New-Item -ItemType Directory -Force $old; $o.Attributes = $o.Attributes -bor [IO.FileAttributes]::Hidden
foreach ($item in @(Get-ChildItem -LiteralPath $GameDir -Filter *.exe -File | ForEach-Object { $_.FullName }) + @("libs", "app0" | ForEach-Object { Join-Path $GameDir $_ } | Where-Object { Test-Path -LiteralPath $_ })) {
    Move-WithRetry $item $old
}
Move-WithRetry (Join-Path $Stage "$exeName.exe") $GameDir
Move-WithRetry (Join-Path $Stage "libs") $GameDir
Move-WithRetry (Join-Path $Stage "app0") $GameDir
foreach ($leftover in Get-ChildItem -LiteralPath $GameDir -Directory -Force -Filter ".old-*") {
    & cmd.exe /d /c "rd /s /q `"$($leftover.FullName)`" 2>nul"
    if (Test-Path -LiteralPath $leftover.FullName) { "  note: $($leftover.Name) is still in use; the next rebuild removes it" }
}
# A frozen build names its commit in COMMIT.txt; otherwise ask the source checkout.
$commitFile = Join-Path $BuildRoot "COMMIT.txt"
$commit = if (Test-Path -LiteralPath $commitFile) { (Get-Content -LiteralPath $commitFile -TotalCount 1).Trim() } else { (git -C $settings.anyps5Source log --format="%h %ad" --date=short -1) 2>$null }
# The relinker's import lists (--registry) stay in imports\ for predict_missing.py; stamp.json names the .exe they
# belong to. Older lists there are left as they are and simply no longer named.
$importLists = @(Get-ChildItem -LiteralPath $Stage -File | Where-Object { $_.Name -like "*.registry.json" -or $_.Name -like "*.needed.json" })
if ($importLists.Count) {
    $importsDir = Join-Path $GameDir "imports"
    New-Item -ItemType Directory -Force $importsDir | Out-Null
    foreach ($l in $importLists) { Copy-Item -LiteralPath $l.FullName -Destination $importsDir -Force }
    $exeItem = Get-Item -LiteralPath (Join-Path $GameDir "$exeName.exe")
    [ordered]@{ exe = $exeItem.Name; exeSize = $exeItem.Length; exeTime = [DateTimeOffset]::new($exeItem.LastWriteTimeUtc).ToUnixTimeSeconds(); files = @($importLists | ForEach-Object { $_.Name }); build = $BuildRoot; commit = $commit; madeBy = "build-game.ps1"; made = (Get-Date -Format "yyyy-MM-dd HH:mm") } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $importsDir "stamp.json") -Encoding utf8
}
@(
    "$($cfg.title) ($($cfg.titleId))"
    "Play:         double-click `"$exeName.exe`""
    "Built:        $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    "AnyPS5:       $commit  (from $BuildRoot)"
    "Lib overrides: $(if ($libOverrides.Count) { $libOverrides.Name -join ', ' } else { 'none' })"
    "Dump:         $Dump"
    "Code patches: $(if ($patches.Count) { $patches.Name -join ', ' } else { 'none' })"
    "File mods:    $($fileMods.Count) file(s)"
    ""
    "Relinker output:"
) + $out | Set-Content -Path (Join-Path $GameDir "info.txt") -Encoding utf8
& cmd.exe /d /c "rd /s /q `"$Stage`" 2>nul"
"Done. Double-click `"$exeName.exe`" to play."
