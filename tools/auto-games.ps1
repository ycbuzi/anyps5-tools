# Watches the dumps folder and turns each finished download into a playable game folder, unattended:
#   1. waits until a game's folder is settled (no browser download in progress, no WinRAR/7-Zip job on it, files
#      unchanged between two checks, every .exfat image complete),
#   2. extracts its archives: .rar (multi-part sets from part 1), .7z, .zip with WinRAR, .exfat with exfat_extract.py,
#   3. picks the game's code (a plain ELF, or an already-decrypted fSELF it unwraps with AnyPS5's unwrap_self.py;
#      encrypted files are reported and left alone) and its data folder,
#   4. writes <Title> (<ID>)\game.json and builds it with build-game.ps1,
#   5. test-launches it (only while no other AnyPS5 game is running) and records whether it draws frames or crashes,
#   6. after a successful build, moves the downloaded archives to "_finished archives (safe to delete)",
#   7. when a game's test fails and nothing else is waiting: reads the failure from its log, looks for open AnyPS5
#      PRs that implement the missing function, merges them onto the frozen AnyPS5 commit in the PR worktree, builds
#      only the libraries they change, swaps those into the game, relaunches, and puts the original libraries back.
#      If the game then stops at another missing function it repeats that, up to three PRs deep. Nothing is posted:
#      the results go to _reports\ (one file per game, SUMMARY.md, most-wanted.md) for review.
# Progress goes to _tools\auto-games.log; lines starting with "EVENT" are the ones worth reading.
# Run it hidden: start-auto-games.vbs. State lives in _tools\auto-state.json (delete a folder's entry to redo it).
param([string]$Dumps = "D:\ps5 games dumps from ps5", [string]$Downloads = "G:\ps5 downloads", [int]$IntervalSeconds = 60, [int]$MinFreeGB = 60)
# Downloads may also land on G: (a second USB drive), one folder per game: those are extracted straight into
# $Dumps\<folder name>, so downloading (G:) and playing/extracting (D:) do not share one drive.
$ErrorActionPreference = "Stop"
# Polite in the background: below-normal CPU priority, which every program the pipeline starts (extractions, the
# relinker, builds) inherits, so whatever Blu runs gets the CPU first. Extractions also use background disk priority.
try { (Get-Process -Id $PID).PriorityClass = "BelowNormal" } catch { }
$ProgressPreference = "SilentlyContinue"
$Tools = $PSScriptRoot
# The game folders stay on D:; _tools is a link to C: since 11-03 and $PSScriptRoot comes back as the C: side.
$Games = "D:\AnyPS5 Games"
$GamesG = "G:\AnyPS5 Games"
$GamesC = "C:\AnyPS5 Games"
$Settings = Get-Content (Join-Path $Tools "settings.json") -Raw | ConvertFrom-Json
$Unwrap = Join-Path $Settings.anyps5Source "tools\unwrap_self.py"
$ExfatTool = Join-Path $Tools "exfat_extract.py"
$UnRar = "C:\Program Files\WinRAR\UnRAR.exe"
$WinRar = "C:\Program Files\WinRAR\WinRAR.exe"
$LogPath = Join-Path $Tools "auto-games.log"
$StatePath = Join-Path $Tools "auto-state.json"
$Finished = Join-Path $Dumps "_finished archives (safe to delete)"
$ArchiveExt = ".rar", ".7z", ".zip", ".exfat"
# PR testing: the worktree and build folder used for PR libraries, and the commit the games are built from.
$Repo = "boykopovar/AnyPS5"
$PrSource = "D:\AnyPS5\wt-pr"
$PrBuild = "D:\AnyPS5\build\pr-rint"
$Reports = Join-Path $Games "_reports"
$Base = ((Get-Content -LiteralPath (Join-Path $Settings.anyps5Build "COMMIT.txt") -TotalCount 1) -split ' ')[0]
# The nightly health run: a new AnyPS5 is taken from its build only after a clean run (see Update-AnyPS5).
$HealthReports = "D:\AnyPS5\scratchpad\helpers\health"
$HealthBuild = "D:\AnyPS5\build\health"
$HealthSource = "D:\AnyPS5\wt-health"
$RefusedHealth = ""

$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\AnyPS5GamesAutoPipeline", [ref]$created)
if (-not $created) { exit 0 }

# Logging never stops the pipeline: a write that fails (the file briefly held open by a reader) is retried, then
# dropped. A failed write once left it stuck right after Bendy's build, before its archive was moved.
function Log([string]$text) {
    $line = "{0}  {1}`r`n" -f (Get-Date -Format "MM-dd HH:mm:ss"), $text
    Trace $text
    for ($try = 1; $try -le 10; $try++) {
        try { [IO.File]::AppendAllText($LogPath, $line, [Text.Encoding]::UTF8); return } catch { Start-Sleep -Milliseconds 300 }
    }
}
# Step-by-step trace for the AnyPS5 Games window: every log line, every command run ("$ ...") and every line of its
# output as it arrives. Kept to the last ~5 MB (rotated to .old).
$TracePath = Join-Path $Tools "auto-games-trace.log"
function Trace([string]$text) {
    try {
        if ((Test-Path -LiteralPath $TracePath) -and (Get-Item -LiteralPath $TracePath).Length -gt 5MB) { Move-Item -LiteralPath $TracePath -Destination "$TracePath.old" -Force }
        [IO.File]::AppendAllText($TracePath, ("{0}  {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text), [Text.Encoding]::UTF8)
    } catch { }
}
$env:PYTHONUNBUFFERED = "1"   # python helpers print their progress line by line into the trace
function Event([string]$text) { Log "EVENT $text" }

$State = @{}
if (Test-Path -LiteralPath $StatePath) { (Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $State[$_.Name] = $_.Value } }
# Written beside the file and swapped in, so a stop half-way never loses it; a copy is kept once an hour.
function Save-State {
    $tmp = "$StatePath.tmp"
    $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $StatePath -Force
    $copy = "$StatePath.backup"
    if (-not (Test-Path -LiteralPath $copy) -or ((Get-Date) - (Get-Item -LiteralPath $copy).LastWriteTime).TotalHours -ge 1) { Copy-Item -LiteralPath $StatePath -Destination $copy -Force }
}
function Get-Entry([string]$name) {
    if (-not $State.ContainsKey($name)) { $State[$name] = [pscustomobject]@{ status = "new"; extracted = @(); note = ""; signature = ""; gameDir = ""; failure = ""; chain = @(); triage = ""; recheck = ""; previousBuild = "" } }
    # Entries read back from the state file may lack fields added later; PowerShell refuses to assign a missing one.
    foreach ($field in "status", "extracted", "note", "signature", "gameDir", "failure", "chain", "triage", "triagedAt", "claudeFixAt", "raidAt", "expAt", "fixerNone", "recheck", "previousBuild", "plays", "played", "extractedTo", "newCopy", "prTest", "titlePrs", "first") {
        if (-not ($State[$name].PSObject.Properties.Name -contains $field)) { $State[$name] | Add-Member -NotePropertyName $field -NotePropertyValue "" }
    }
    $State[$name]
}

# A SELF says in its segment table whether it is encrypted; the first 4 bytes alone do not (5414f5ee was wrongly taken to mean encrypted, 10-31).
function Self-Encrypted([byte[]]$head) {
    $count = [BitConverter]::ToUInt16($head, 0x18)
    if ($count -eq 0 -or $count -gt 256) { return $true }
    for ($i = 0; $i -lt $count; $i++) { if (([BitConverter]::ToUInt64($head, 0x20 + $i * 32) -band 2) -ne 0) { return $true } }
    $false
}
function Magic([string]$path) {
    $s = [IO.File]::OpenRead($path); try { $b = New-Object byte[] 8192; [void]$s.Read($b, 0, 8192) } finally { $s.Close() }
    if ((($b[0..3] | ForEach-Object { $_.ToString("x2") }) -join "") -eq "5414f5ee") { if (Self-Encrypted $b) { return "encrypted" } else { return "fself" } }
    $b = $b[0..3]
    switch -regex (($b | ForEach-Object { $_.ToString("x2") }) -join "") {
        "^7f454c46$" { "elf" } "^4f153d1d$" { "fself" } "^5414f5ee$" { "encrypted" } default { "unknown" }
    }
}
function Exfat-Complete([IO.FileInfo]$image) {
    $s = [IO.File]::OpenRead($image.FullName); try { $b = New-Object byte[] 512; [void]$s.Read($b, 0, 512) } finally { $s.Close() }
    if ([Text.Encoding]::ASCII.GetString($b, 3, 8) -ne "EXFAT   ") { return $false }
    [uint64]$image.Length -ge [BitConverter]::ToUInt64($b, 0x48) * [uint64](1 -shl $b[0x6C])
}
function Busy([string]$folder) {
    $jobs = Get-CimInstance Win32_Process -Filter "Name='WinRAR.exe' OR Name='UnRAR.exe' OR Name='Rar.exe' OR Name='7zG.exe' OR Name='7z.exe' OR Name='7zFM.exe' OR Name='python.exe'" | ForEach-Object { $_.CommandLine }
    # Commands typed with forward slashes (G:/ps5 games ...) count too.
    [bool]($jobs | Where-Object { $_ -and $_.Replace([char]47, [char]92).IndexOf($folder, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
}
# Folder names stay plain ASCII: native tools get them on the command line, where other characters can turn into '?'.
function Safe-Name([string]$s) { (($s -replace '[\\/:*?"<>|]', '') -replace '[^\x20-\x7E]', '' -replace '\s+', ' ').Trim().TrimEnd('.') }
function Folder-Bytes([string]$dir) {
    (Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\(decrypted|fakelib)\\' } | Measure-Object Length -Sum).Sum
}
# 11-04 a Godot render of the AI kingdom (outside the games folder) overlapped the FPS optimizer's Octopath test and
# skewed it: GPU work outside the games folder sets _tools\live\gpu-busy.flag, and no test, FPS run or unpack starts
# while it is fresh (20 minutes, so a crashed render cannot block the pipeline for long).
# 11-12 the PC lock (assistant\skills\gpu_flag.ps1): one heavy job at a time. The lock is "busy" for the pipeline only
# while another live process holds it; the pipeline takes it itself around game tests and builds.
. (Join-Path $Tools "assistant\skills\gpu_flag.ps1")
$script:PcPriority = 60
$script:PcWaitNoted = [datetime]::MinValue
$PcWait = {
    Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
    if (((Get-Date) - $script:PcWaitNoted).TotalSeconds -ge 60) {
        $o = Pc-Owner
        Trace ("waiting for the PC: " + $(if ($o -and $o.job) { "$($o.job) has it" } elseif (Test-Path -LiteralPath (Join-Path $Tools "pause.flag")) { "paused" } else { $gp = Get-Process -ErrorAction SilentlyContinue | Where-Object { $q = $_.Path; $q -and ($q.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -or $q.StartsWith("G:\AnyPS5 Games\", [StringComparison]::OrdinalIgnoreCase)) -and $q -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*" } | Select-Object -First 1; if ($gp) { "game $($gp.ProcessName) (pid $($gp.Id)) has run $([int]((Get-Date) - $gp.StartTime).TotalMinutes) min" } else { "a game is running or a more urgent job is queued" } }))
        $script:PcWaitNoted = Get-Date
    }
}
function Gpu-Busy { $o = Pc-Owner; [bool]($o -and $o.alive -and $o.pid -ne $PID -and $o.minutes -lt 20) }
# 11-05 a game moved to G: (a junction at its D: path) runs from G:\AnyPS5 Games, so both folders count.
# 11-06 games the GPU lanes (gpu-lanes.ps1) run on the 1650 and the Radeon are listed in _tools\live\lanes\: they are
# not Blu playing and not a pipeline test, so nothing waits for them.
function Game-Blockers {
    $mine = Lane-Pids
    foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
        if ($mine -contains $p.Id -or -not $p.Path -or $p.Path -like "*\_tools\*" -or $p.ProcessName -like "AnyPS5*") { continue }
        if (-not ($p.Path.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -or $p.Path.StartsWith("G:\AnyPS5 Games\", [StringComparison]::OrdinalIgnoreCase))) { continue }
        if ($p.Threads.Count -le 1 -and $p.WorkingSet64 -lt 1MB) {
            if (-not $script:DeadGames) { $script:DeadGames = @{} }
            if (-not $script:DeadGames[$p.Id]) { $script:DeadGames[$p.Id] = $true; try { $p.Kill() } catch { }; Log "EVENT STUCK $($p.ProcessName) pid $($p.Id) hung while closing (1 thread, no memory); it no longer blocks tests" }
            continue
        }
        "$($p.ProcessName) pid $($p.Id)"
    }
}

function Lane-Pids { @(Get-ChildItem -LiteralPath (Join-Path $Tools "live\lanes") -Filter *.json -ErrorAction SilentlyContinue | ForEach-Object { try { [int](Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).pid } catch { } }) }
function Game-Running { $lanePids = Lane-Pids; [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { $lanePids -notcontains $_.Id } | Where-Object { $p = $_.Path; $p -and ($p.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -or $p.StartsWith("G:\AnyPS5 Games\", [StringComparison]::OrdinalIgnoreCase)) -and $p -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*" -and -not $(try { $_.HasExited } catch { $false }) }) -or (Gpu-Busy) }  # AnyPS5 Games.exe (this window) is not a game; Path is read once (11-07: a process exiting between two reads gave null)

# Archives of a folder that still need extracting; a multi-part RAR set is represented by its first part.
function Pending-Archives([string]$folder, $entry) {
    # Archives that came out of an archive unpacked to the other drive (Maiden of Black Water's 7z held an exFAT image,
    # left on G:) are pending too.
    $roots = @($folder) + @($entry.extractedTo | Where-Object { $_ -and $_ -ne $folder -and (Test-Path -LiteralPath $_) })
    $all = $roots | ForEach-Object { Get-ChildItem -LiteralPath $_ -File -Force } | Where-Object { $_.Extension.ToLowerInvariant() -in $ArchiveExt -or $_.Name -match '\.(7z|zip)\.\d{3}$' }
    foreach ($a in $all) {
        if ($a.Name -in @($entry.extracted)) { continue }
        if ($a.Name -match '\.part0*(\d+)\.rar$' -and [int]$Matches[1] -ne 1) { continue }
        if ($a.Name -match '\.(7z|zip)\.(\d{3})$' -and [int]$Matches[2] -ne 1) { continue }
        $a
    }
}
function Rar-Set-Complete([IO.FileInfo]$first) {
    if ($first.Name -match '^(.*)\.(7z|zip)\.001$') {
        $stem = [regex]::Escape($Matches[1] + "." + $Matches[2])
        $parts = Get-ChildItem -LiteralPath $first.DirectoryName -File | Where-Object { $_.Name -match "^$stem\.(\d{3})$" } | ForEach-Object { [pscustomobject]@{ n = [int]$Matches[1]; f = $_ } } | Sort-Object n
    } elseif ($first.Name -match '^(.*)\.part0*1\.rar$') {
        $stem = [regex]::Escape($Matches[1])
        $parts = Get-ChildItem -LiteralPath $first.DirectoryName -File | Where-Object { $_.Name -match "^$stem\.part0*(\d+)\.rar$" } | ForEach-Object { [pscustomobject]@{ n = [int]$Matches[1]; f = $_ } } | Sort-Object n
    } else { return $true }
    $numbers = @($parts.n)
    for ($i = 0; $i -lt $numbers.Count; $i++) { if ($numbers[$i] -ne $i + 1) { return $false } }
    # The last volume of a finished set is normally shorter than the others; equal sizes mean more may be coming.
    $numbers.Count -ge 2 -and $parts[-1].f.Length -lt $parts[0].f.Length
}
function Extract([IO.FileInfo]$a, [string]$folder = $a.DirectoryName) {
    New-Item -ItemType Directory -Force $folder | Out-Null
    switch ($a.Extension.ToLowerInvariant()) {
        ".exfat" {
            $out = Join-Path $folder ([IO.Path]::GetFileNameWithoutExtension($a.Name))
            $n = Native { python -I $ExfatTool $a.FullName $out }; $code = $n.code; Log "  exfat: $($n.out -join ' ')"
        }
        # Download-site archives are often password-protected; _tools\archive-passwords.txt lists the passwords to try
        # (one per line) after no password.
        ".rar" {
            foreach ($pw in @("-p-") + @(Get-Content -LiteralPath (Join-Path $Tools "archive-passwords.txt") -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | ForEach-Object { "-p" + $_.Trim() })) {
                $n = Native { & $UnRar x -y -o- -ri1:5 $pw -idq $a.FullName ($folder + "\") }; $code = $n.code
                if ($code -eq 0) { break }
                # A password that opens the archive gives the real error (for example a download cut short); keep that one.
                if (-not (($n.out -join ' ') -match 'Incorrect password')) { $opened = $n }
            }
            if ($code -ne 0 -and $opened) { $n = $opened }
            $script:CutShort = ""
            if ($code -ne 0 -and ($n.out -join ' ') -match 'Unexpected end of archive') {
                $script:CutShort = @($n.out | Where-Object { $_ -match '(\S[^\\]*)\s+-\s+checksum error' } | ForEach-Object { $Matches[1].Trim() } | Select-Object -First 1)[0]
                if (-not $script:CutShort) { $script:CutShort = "the end of the archive" }
            }
            if ($code -ne 0 -and $n.out) { Log "  unrar: $(($n.out | Select-Object -Last 3) -join ' ')" }
            $opened = $null
        }
        default {
            foreach ($pw in @("-p-") + @(Get-Content -LiteralPath (Join-Path $Tools "archive-passwords.txt") -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | ForEach-Object { "-p" + $_.Trim() })) {
                Trace ('$ WinRAR x -ibck -y -o- ' + $(if ($pw -eq "-p-") { "-p-" } else { "-p***" }) + " `"$($a.FullName)`" `"$folder\`"   (no progress output; this can take a while)")
                $p = Start-Process -FilePath $WinRar -ArgumentList @("x", "-ibck", "-y", "-o-", "-ri1:5", "`"$pw`"", "`"$($a.FullName)`"", "`"$folder\`"") -PassThru -Wait -WindowStyle Hidden
                Trace "  (exit $($p.ExitCode))"
                $code = $p.ExitCode
                if ($code -eq 0) { break }
            }
        }
    }
    $code
}

# Chooses the code eboot, the data folder and the modules. Returns $null with a reason when nothing is usable.
function Plan-Game([string]$folder, [string]$prefer = "") {
    $eboots = Get-ChildItem -LiteralPath $folder -Recurse -File -Filter "eboot.bin" -Force | Where-Object { $_.FullName -notmatch '\\fakelib\\' }
    if (-not $eboots) { return [pscustomobject]@{ ok = $false; reason = "no eboot.bin found" } }
    $rank = @{ elf = 3; fself = 2; unknown = 0; encrypted = 0 }
    $cands = foreach ($e in $eboots) { [pscustomobject]@{ file = $e; kind = (Magic $e.FullName); backport = $e.FullName -match 'backport' -or (Test-Path -LiteralPath (Join-Path $e.DirectoryName "fakelib")); param = Test-Path -LiteralPath (Join-Path $e.DirectoryName "sce_sys\param.json") } }
    # An eboot.bin inside another candidate's folder, with no sce_sys of its own, belongs to that game, not a game of its
    # own (POOLS keeps one in Media\StreamingAssets\VR, which was taken for the game's data). A game folder under an
    # overlay (Total Chaos: PPSA32393\ under a backport at the top) has its sce_sys\param.json, and a decrypted\ folder
    # holds the real program: both stay.
    $candDirs = @($cands | ForEach-Object { $_.file.DirectoryName })
    $cands = @($cands | Where-Object { $d = $_.file.DirectoryName; $_.param -or $d -match '\\decrypted$' -or -not @($candDirs | Where-Object { $_ -ne $d -and $d.StartsWith($_ + "\", [StringComparison]::OrdinalIgnoreCase) }).Count })
    # Some downloads come with two backports (POOLS: 4xx and 10xx) and a title may need the other one: a "backport" request
    # names part of the folder to use.
    $code = $cands | Sort-Object @{ e = { $rank[$_.kind] }; Descending = $true }, @{ e = { [bool]$prefer -and $_.file.FullName.IndexOf($prefer, [StringComparison]::OrdinalIgnoreCase) -ge 0 }; Descending = $true }, @{ e = { $_.backport } }, @{ e = { $_.param }; Descending = $true }, @{ e = { $_.file.FullName.Length } } | Select-Object -First 1
    if ($rank[$code.kind] -eq 0) { return [pscustomobject]@{ ok = $false; reason = "every eboot.bin is still encrypted (needs decrypted files)" } }
    $codeDir = $code.file.DirectoryName
    $dataDirs = $cands | Where-Object { $_.file.DirectoryName -notmatch '\\decrypted$' -and -not $_.backport } | ForEach-Object { $_.file.DirectoryName } | Select-Object -Unique
    if (-not $dataDirs) {
        # Every eboot looks like a backport (Spider-Man Remastered: the game's folder has a fakelib too). A folder holding another candidate only looks biggest because of it, and choosing it copied 199 GB of archives.
        $dataDirs = @($cands | ForEach-Object { $_.file.DirectoryName } | Select-Object -Unique)
        $dataDirs = @($dataDirs | Where-Object { $x = $_; -not @($dataDirs | Where-Object { $_ -ne $x -and $_.StartsWith($x + "\", [StringComparison]::OrdinalIgnoreCase) }).Count })
    }
    # A folder around the code's own folder is not its data: its size includes the game, and what lies directly in it is
    # another archive's overlay (Hello Neighbor 2: two prx folders with the same modules stopped the relinker).
    $codeBase = $codeDir -replace '\\decrypted$', ''
    $around = @($dataDirs | Where-Object { $codeBase.StartsWith($_ + "\", [StringComparison]::OrdinalIgnoreCase) })
    $inner = @($dataDirs | Where-Object { $_ -notin $around })
    if ($inner) { $dataDirs = $inner }
    $dataDir = $dataDirs | Sort-Object { Folder-Bytes $_ } -Descending | Select-Object -First 1
    $paramPath = @("$dataDir\sce_sys\param.json", "$codeDir\sce_sys\param.json", "$codeDir\..\sce_sys\param.json") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    # A backport alone holds the program but not the game (Street Fighter 6 on 10-31: built, then "param.json or param.sfo not found").
    if (-not $paramPath -and -not (Test-Path -LiteralPath "$dataDir\sce_sys\param.sfo") -and -not (Test-Path -LiteralPath "$codeDir\sce_sys\param.sfo")) { return [pscustomobject]@{ ok = $false; reason = "incomplete: no sce_sys\param.json, so this is probably a backport without the game; add the game's files" } }
    $title = $null; $titleId = $null
    if ($paramPath) {
        $p = Get-Content -LiteralPath $paramPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $titleId = $p.titleId
        $lp = $p.localizedParameters
        if ($lp) { $lang = if ($lp.defaultLanguage) { $lp.defaultLanguage } else { "en-US" }; $title = $lp.$lang.titleName; if (-not $title) { $title = $lp."en-US".titleName } }
    }
    if (-not $titleId -and $folder -match '(PPSA\d{5})') { $titleId = $Matches[1] }
    if (-not $title) { $title = Split-Path $folder -Leaf }
    [pscustomobject]@{ ok = $true; kind = $code.kind; code = $code.file; codeDir = $codeDir; dataDir = $dataDir; title = (Safe-Name $title); titleId = $titleId }
}

# Package images (FPKG .pkg, .ffpkg, .ffpfs, .ffpfsc) are unpacked with ps5-dump-forge if Blu has put it in _tools\ps5-dump-forge. Asset files named .pkg inside a game are not packages: a real one starts with \x7fFIH or \x7fCNT or has one of the image extensions.
function Unpack-Packages([string]$folder, $e) {
    $forge = Join-Path $Tools "ps5-dump-forge\ps5-dump-forge.exe"
    $worked = $false
    $script:UnpackWaiting = $false
    $files = @(Get-ChildItem -LiteralPath $folder -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in ".pkg", ".ffpkg", ".ffpfs", ".ffpfsc" -and $_.Length -gt 256MB -and $_.FullName -notmatch '\\unpacked\\' })
    foreach ($f in $files) {
        if ($f.Extension -eq ".pkg") {
            $s = [IO.File]::OpenRead($f.FullName); try { $b = New-Object byte[] 4; [void]$s.Read($b, 0, 4) } finally { $s.Close() }
            if ([Text.Encoding]::ASCII.GetString($b, 1, 3) -notin "FIH", "CNT") { continue }
        }
        $out = Join-Path $f.DirectoryName "unpacked"  # short: the tool limits output names
        if (Test-Path -LiteralPath (Join-Path $out "eboot.bin")) { continue }
        if (-not (Test-Path -LiteralPath $forge)) {
            $why = "$($f.Name) needs ps5-dump-forge to unpack; put ps5-dump-forge.exe in _tools\ps5-dump-forge"
            if ($e.note -ne $why) { $e.note = $why; Save-State; Event "WAITING $($f.Name): needs ps5-dump-forge in _tools\ps5-dump-forge" }
            continue
        }
        $need = $f.Length / 1GB * 2 + $ReserveGB
        if ((Free-GB $folder (Split-Path $folder -Leaf)) -lt $need) { $why = "no room to unpack $($f.Name): needs about {0:N0} GB" -f $need; if ($e.note -ne $why) { $e.note = $why; Save-State; Event "WAITING FOR SPACE $($f.Name): $why" }; continue }
        if ((Game-Running) -or (Assistant-Busy) -or (Drive-Extracting @($f.FullName, $out))) { $script:UnpackWaiting = $true; continue }
        Add-Type -AssemblyName Microsoft.VisualBasic
        foreach ($old in @(Get-ChildItem -LiteralPath $f.DirectoryName -Directory -Force -Filter "unpacked*.part" -ErrorAction SilentlyContinue)) {
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($old.FullName, "OnlyErrorDialogs", "SendToRecycleBin")
            Log "$($f.Name): an earlier unpack was cut off; its partial folder went to the Recycle Bin"
        }
        Log "$($f.Name): unpacking the package to $out"
        $n = Native { & $forge convert $f.FullName --to folder --threads 6 -o $out }
        $worked = $true
        if ($n.code -eq 0 -and (Test-Path -LiteralPath (Join-Path $out "eboot.bin"))) { $e.note = ""; $e.signature = ""; Save-State; Event "UNPACKED $($f.Name) -> $out" }
        else { $e.note = "could not unpack $($f.Name): " + (($n.out | Select-Object -Last 2) -join " "); Save-State; Event "UNPACK FAILED $($f.Name): $($e.note)" }
    }
    $worked
}

# Folders under a game that hold a program of their own (an eboot.bin), such as POOLS's VR build in
# Media\StreamingAssets\VR: their modules belong to that program, and taking them too gave the relinker two
# Il2CppUserAssemblies.prx.
function Nested-Programs([string]$base) {
    @(Get-ChildItem -LiteralPath $base -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Where-Object { $_.DirectoryName -ne $base -and $_.FullName -notmatch '\\(fakelib|decrypted)\\' -and $_.DirectoryName -notmatch '\\decrypted$' -and -not (Test-Path -LiteralPath (Join-Path $_.DirectoryName "sce_sys\param.json")) } | ForEach-Object { $_.DirectoryName + "\" })
}
function In-Nested([string]$path, [string[]]$nested) {
    [bool]@($nested | Where-Object { $_ -and $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count
}

function Build-Game([string]$folder, $plan, [bool]$newCopy = $false) {
    $gameDir = Join-Path $Games ("{0} ({1})" -f $plan.title, $plan.titleId)
    # A folder with a game.json and an .exe is a finished game (maybe set up by hand): leave it. Without an .exe it is
    # an interrupted build, which is redone.
    # 11-05 Hades II's new copy was refused here: a new copy, or a game whose data folder is gone, is built again in its
    # folder (saves stay), with its old unwrapped code moved aside.
    $oldCfg = Join-Path $gameDir "game.json"
    if ((Test-Path -LiteralPath $oldCfg) -and @(Get-ChildItem -LiteralPath $gameDir -Filter *.exe -File -ErrorAction SilentlyContinue).Count) {
        $old = Get-Content -LiteralPath $oldCfg -Raw | ConvertFrom-Json
        $oldData = if ($old.resources) { $old.resources } else { $old.dump }
        if (-not $newCopy -and $oldData -and (Test-Path -LiteralPath $oldData)) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "a game folder already exists; left as it is" } }
        # Blu 11-05: no leftovers for him to delete; the old unwrapped code is rebuilt from the new copy anyway.
        $oldSrc = Join-Path $Games ("_sources\{0} ({1})" -f $plan.title, $plan.titleId)
        if (Test-Path -LiteralPath $oldSrc) {
            & cmd.exe /d /c "rd /s /q `"$oldSrc`""
            Log "  $($plan.title): replacing the old copy (its data was $oldData); deleted its old unwrapped code"
        }
    }
    # 11-05 D: was full (each game folder holds its own app0, a whole copy when the data is on another drive): a game
    # whose data is on G: gets its folder in G:\AnyPS5 Games, where app0 is hard links, and a junction at its D: path
    # so the app, the tools and Rebuild.cmd find it as before. G:\ is excluded from Defender (Astro's exe was blocked in
    # a folder that was not).
    # 11-07 the same for a game unpacked to C: (Spider-Man 2 fit on neither D: nor G:): its folder is in C:\AnyPS5 Games.
    $dataRoot = if ($plan.dataDir) { [IO.Path]::GetPathRoot($plan.dataDir).ToUpper() } else { "" }
    $altGames = if ($dataRoot -eq [IO.Path]::GetPathRoot($GamesG).ToUpper()) { $GamesG } elseif ($dataRoot -eq [IO.Path]::GetPathRoot($GamesC).ToUpper()) { $GamesC } else { $null }
    if ($altGames -and (Test-Path -LiteralPath $dataRoot)) {
        $real = Join-Path $altGames (Split-Path $gameDir -Leaf)
        $item = Get-Item -LiteralPath $gameDir -ErrorAction SilentlyContinue
        if (-not $item -or $item.LinkType -ne "Junction") {
            New-Item -ItemType Directory -Force $real | Out-Null
            if ($item) {
                foreach ($keep in "_sd", "_trophy", "mods", "crash-logs") {
                    $from = Join-Path $gameDir $keep
                    if (Test-Path -LiteralPath $from) {
                        & robocopy $from (Join-Path $real $keep) /E /NFL /NDL /NJH /NJS /NP | Out-Null
                        if ($LASTEXITCODE -ge 8) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "copying its $keep to $real failed (robocopy $LASTEXITCODE); the D: folder is left as it is" } }
                    }
                }
                Get-ChildItem -LiteralPath $gameDir -File | Where-Object { $_.Extension -in ".cmd", ".txt" } | Copy-Item -Destination $real -Force
                # Blu 11-05: no "delete when happy" folders; the saves and mods are copied, the rest is rebuilt.
                & cmd.exe /d /c "rd /s /q `"$gameDir`""
                if (Test-Path -LiteralPath $gameDir) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "its old D: folder could not be removed (a file in use?); saves are copied to $real" } }
                Log "  $($plan.title): its folder moved to $real (saves and mods copied, the old D: folder deleted)"
            }
            New-Item -ItemType Junction -Path $gameDir -Target $real | Out-Null
        }
    }
    New-Item -ItemType Directory -Force $gameDir | Out-Null
    $warnings = @()
    $cfg = [ordered]@{ title = $plan.title; titleId = $plan.titleId }
    if ($plan.kind -eq "elf") {
        # Already a clean ELF tree (a dump's "decrypted" folder): convert it in place.
        $cfg.dump = $plan.codeDir
        if ($plan.dataDir -ne $plan.codeDir) { $cfg.resources = $plan.dataDir }
        $nested = Nested-Programs $plan.codeDir
        $mods = Get-ChildItem -LiteralPath $plan.codeDir -Recurse -File -Force | Where-Object { $_.Extension -in ".prx", ".sprx" -and -not (In-Nested $_.FullName $nested) } | ForEach-Object { $_.DirectoryName.Substring($plan.codeDir.Length).TrimStart('\').Replace('\', '/') } | Select-Object -Unique
    } else {
        # Unwrap the fSELF code and every module into _sources (code folder first, the data folder for the rest).
        $src = Join-Path $Games ("_sources\{0} ({1})" -f $plan.title, $plan.titleId)
        New-Item -ItemType Directory -Force $src | Out-Null
        $n = Native { python -I $Unwrap $plan.code.FullName (Join-Path $src "eboot.elf") }
        if ($n.code -ne 0) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "unwrapping eboot.bin failed: $($n.out -join ' ')" } }
        $seen = @{}
        foreach ($base in @($plan.codeDir, $plan.dataDir) | Select-Object -Unique) {
            $nested = Nested-Programs $base
            foreach ($m in Get-ChildItem -LiteralPath $base -Recurse -File -Force | Where-Object { $_.Extension -in ".prx", ".sprx" -and $_.FullName -notmatch '\\(fakelib|sce_sys|decrypted)\\' -and $_.Name -notmatch '\.bak' -and -not (In-Nested $_.FullName $nested) }) {
                $rel = $m.FullName.Substring($base.Length + 1)
                if ($seen[$rel.ToLowerInvariant()]) { continue }
                $kind = Magic $m.FullName
                $dst = Join-Path $src $rel
                New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
                if ($kind -eq "fself") { $n = Native { python -I $Unwrap $m.FullName $dst }; if ($n.code -ne 0) { $warnings += "could not unwrap $rel"; continue } }
                elseif ($kind -eq "elf") { Copy-Item -LiteralPath $m.FullName -Destination $dst }
                else { $warnings += "$rel is $kind"; continue }
                $seen[$rel.ToLowerInvariant()] = $true
            }
        }
        $cfg.dump = $src
        $cfg.resources = $plan.dataDir
        $cfg.eboot = "eboot.elf"
        $mods = $seen.Keys | ForEach-Object { (Split-Path $_ -Parent).Replace('\', '/') } | Select-Object -Unique
    }
    # A module beside the eboot has an empty folder: it is the relinker's ".". Dropping it left the game unable to start.
    $moduleDirs = @($mods | ForEach-Object { if ($_) { $_ } else { "." } } | Select-Object -Unique | Where-Object { $_ -ne "sce_module" -and $_ -notlike "sce_module/*" })
    if ($moduleDirs) { $cfg.moduleDirs = $moduleDirs }
    # The game's data may come without sce_sys while its backport has it (POOLS: "param.json or param.sfo not found" at
    # start): build-game fills app0\sce_sys from the backport's.
    $codeSys = @("$($plan.codeDir)\sce_sys", "$($plan.codeDir)\..\sce_sys") | Where-Object { Test-Path -LiteralPath "$_\param.json" } | Select-Object -First 1
    if ($codeSys -and -not (Test-Path -LiteralPath "$($plan.dataDir)\sce_sys\param.json")) { $cfg.sceSys = (Resolve-Path -LiteralPath $codeSys).Path }
    $cfg | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gameDir "game.json") -Encoding ascii
    $rebuild = Get-ChildItem -LiteralPath $Games -Directory | ForEach-Object { Join-Path $_.FullName "Rebuild.cmd" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($rebuild) { Copy-Item -LiteralPath $rebuild -Destination $gameDir -Force }
    $n = Use-Pc "pipeline build of $(Split-Path $gameDir -Leaf)" $script:PcPriority { Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $gameDir } } $PcWait
    $out = $n.out
    $ok = $n.code -eq 0 -and ($out -match '^Done\.')
    Log ("  build: " + (($out | Select-Object -Last 6) -join ' | '))
    $reason = if ($ok) { "" } else { ($out | Select-Object -Last 3) -join ' ' }
    # 11-04 Hollow Knight waited days for a "FontGsm fix": its libSceFontGsm.prx was damaged (every SCE name offset 0x220
    # too high) while 8 other games' copies were fine. A module whose tables the relinker rejects is compared with the
    # other games' copies, and a damaged copy is named as such: no AnyPS5 change can fix it.
    if (-not $ok -and "$out" -match '([\w.-]+\.s?prx)[^\\/:]*: (String offset out of bounds|Unterminated ELF string|Invalid string or symbol table|Missing or ambiguous dynamic tag)') {
        $module = $Matches[1]
        $check = Native { python -I -c "import sys; sys.path.insert(0, r'$Tools\assistant'); import work_skills as w; print(w.module_copies(sys.argv[1], sys.argv[2]))" $module $plan.title }
        $mine = @($check.out | Where-Object { $_ -match '^\s+\S' } | Select-Object -First 1)
        if ("$mine" -match 'outside it' -and @($check.out | Where-Object { $_ -match ': fine$' }).Count) { $reason += " | damaged copy: this game's $module has names outside its string table while other games' copies are fine; it needs a clean copy, not an AnyPS5 fix" }
    }
    [pscustomobject]@{ ok = [bool]$ok; gameDir = $gameDir; reason = $reason; warnings = $warnings }
}

# The assistant in the AnyPS5 Games window keeps its 14B model on the RTX 4070 SUPER, which a game needs: stop that
# server right before a game starts (the assistant switches to the 7B on the Radeon by itself).
function Free-Gpu {
    foreach ($s in Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match '--port 8093\b' -and $_.CommandLine -match 'Vulkan0' }) {
        Trace "stopping the assistant's 14B model so the game gets the RTX 4070 SUPER"
        Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue; Start-Sleep 2
    }
}
# A game's window title, with AnyPS5's FPS counter. A game playing inside the AnyPS5 Games window is no longer a main
# window, so that window writes its title to _tools\live\title-<pid>.txt every second.
function Game-Title($p) {
    if ($p.MainWindowTitle) { return $p.MainWindowTitle }
    $f = Join-Path $Tools "live\title-$($p.Id).txt"
    if (Test-Path -LiteralPath $f) { return [string](Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) }
    ""
}
# Blu is using the PC for something that needs the graphics card: a full-screen program in front that is not one of
# the AnyPS5 games (any game, a video), or the RTX 4070 busy with something else. Game tests and AI play wait for it.
# 11-08: a PR test with libraries that lack an entry point the game imports left a Windows "Entry Point Not Found" box
# on Blu's streamed screen until someone clicked it. Games started by the pipeline inherit this mode and exit instead.
Add-Type 'using System; using System.Runtime.InteropServices; public static class PipeErr { [DllImport("kernel32.dll")] public static extern uint SetErrorMode(uint m); }'
[void][PipeErr]::SetErrorMode(0x8003)
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Fore {
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr h, uint flags);
  [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr m, ref MI mi);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  public struct R { public int L, T, Ri, B; }
  [StructLayout(LayoutKind.Sequential)] public struct MI { public int cb; public R mon, work; public uint flags; }
  public static uint FullScreenPid() {
    IntPtr h = GetForegroundWindow(); if (h == IntPtr.Zero) return 0;
    var cls = new System.Text.StringBuilder(64); GetClassName(h, cls, 64);
    if (cls.ToString() == "Progman" || cls.ToString() == "WorkerW") return 0;  // the desktop
    R r; GetWindowRect(h, out r); var mi = new MI(); mi.cb = Marshal.SizeOf(mi);
    if (!GetMonitorInfo(MonitorFromWindow(h, 2), ref mi)) return 0;
    if (r.L > mi.mon.L || r.T > mi.mon.T || r.Ri < mi.mon.Ri || r.B < mi.mon.B) return 0;
    uint pid; GetWindowThreadProcessId(h, out pid); return pid;
  }
}
"@
function Blu-Busy {
    $pid2 = [Fore]::FullScreenPid()
    if ($pid2) {
        $path = try { (Get-Process -Id $pid2 -ErrorAction Stop).Path } catch { "" }
        if ($path -and -not $path.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase)) { return "a full-screen program is in front ($(Split-Path $path -Leaf))" }
    }
    if (-not (Game-Running)) {
        # 11-08: total utilisation counted the desktop compositor redrawing Blu's live AnyPS5 Machines panes and Discord's
        # clip recorder (30-55%) as "busy", so tests waited while nothing was gaming. Only a real GPU user counts now.
        $quiet = '^(dwm|explorer|Discord|discord_clips|chrome|msedgewebview2|claude|Claude|Spotify|vncviewer|AnyPS5 Machines|ProtonVPN|ShellHost|SearchHost|StartMenuExper|TextInputHost|ShellExperience|CrossDeviceResu|TabTip|TranslucentTB|steamwebhelper)'
        $hot = try { & nvidia-smi pmon -c 1 -s u -i 0 2>$null | Where-Object { $_ -notmatch '^\s*#' } | ForEach-Object {
            $f = ($_.Trim() -split '\s+'); if ($f.Count -ge 10 -and $f[3] -match '^\d+$' -and [int]$f[3] -ge 30) { [pscustomobject]@{ Sm = [int]$f[3]; Name = ($f[9..($f.Count - 1)] -join ' ') } } } |
            Where-Object { $_.Name -notmatch $quiet } | Sort-Object Sm -Descending | Select-Object -First 1 } catch { $null }
        if ($hot) { return "the RTX 4070 is $($hot.Sm)% busy with $($hot.Name)" }
    }
    $null
}
function Wait-For-Blu([string]$what) {
    while ($why = Blu-Busy) {
        if (Test-Path -LiteralPath $PauseFlag) { return }
        Trace "${what}: waiting, $why (checks again in a minute)"
        Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
        Start-Sleep 60
    }
}
function Test-Game([string]$gameDir, [string]$label = "first-test", [hashtable]$envs = @{}) {
    Use-Pc "pipeline test of $(Split-Path $gameDir -Leaf) ($label)" $script:PcPriority { Test-GameUnlocked $gameDir $label $envs } $PcWait -Game
}
function Test-GameUnlocked([string]$gameDir, [string]$label = "first-test", [hashtable]$envs = @{}) {
    Wait-For-Blu "test of $(Split-Path $gameDir -Leaf)"
    # A launch on a saturated D: records a false "no frames"; wait for it (Wait-Quiet gives up after 15 minutes).
    Wait-Nightly "test of $(Split-Path $gameDir -Leaf)"
    # 11-20 a GPU lane started the same game a minute before this test (Fatal Frame II twice, RAM 98.5%): wait for the
    # lane's run of this game to end (lanes close a game after ~10 minutes), at most 15 minutes.
    $laneWaitStart = Get-Date
    while (((Get-Date) - $laneWaitStart).TotalMinutes -lt 15) {
        $lanePids = @(Lane-Pids)
        $same = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $lanePids -contains $_.Id -and $_.Path -and $_.Path.StartsWith($gameDir + "\", [StringComparison]::OrdinalIgnoreCase) })
        if (-not $same.Count) { break }
        if (-not $laneWaited) { Log "test of $(Split-Path $gameDir -Leaf): a GPU lane is running this game; waiting for it to finish"; $laneWaited = $true }
        Set-Content -LiteralPath (Join-Path $Tools "live\lanes\yield.flag") -Value "pipeline test of $(Split-Path $gameDir -Leaf)" -Encoding utf8
        Start-Sleep -Seconds 20
    }
    # 11-04 a game moved to the SSD (a junction at its D: path, Astro's Playroom) does not wait for D:.
    $onD = $true
    try { $t = (Get-Item -LiteralPath $gameDir).Target; if ($t -and "$t" -notmatch '^D:') { $onD = $false } } catch { }
    while ($onD -and -not (Wait-Quiet "test of $(Split-Path $gameDir -Leaf)")) { Start-Sleep 30 }
    Free-Gpu
    $cfg = Get-Content -LiteralPath (Join-Path $gameDir "game.json") -Raw | ConvertFrom-Json
    $exe = Join-Path $gameDir ("{0}.exe" -f $(if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }))
    # The game's own launch settings (game-env.txt, as the AnyPS5 Games window starts it) and a fix try's on top, for
    # this launch only: a test without them says nothing about a game that needs one (Scorn, Visage: PlayGo).
    $launch = @{}
    $saved = Join-Path $gameDir "game-env.txt"
    if (Test-Path -LiteralPath $saved) { foreach ($l in Get-Content -LiteralPath $saved) { $i = $l.IndexOf('='); if ($i -gt 0) { $launch[$l.Substring(0, $i).Trim()] = $l.Substring($i + 1).Trim() } } }
    foreach ($k in $envs.Keys) { $launch[$k] = "$($envs[$k])" }
    # Blu 11-06: "I wanted the actual Claude AI to test run the games ... with nobody clicking buttons, how is that a
    # test run". While Claude may play (its limits lifted, or Settings aiPlayerBrain = claude) and nobody has touched
    # the PC for 2 minutes, Claude plays every test from its first frame (ai-player.py --attach) and the test runs 8
    # minutes instead of 3. The game gets ai-player's key layout; moving the mouse stops Claude at once.
    $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
    # Blu 11-07: "for each game, a Claude Code dedicated for the test so it can get through, and whatever the time
    # limits for them is it". Claude's buttons go through the game's controller command file (APS5_PAD_INPUT_FILE), not
    # the keyboard, so it plays while Blu uses the PC and the 2-idle-minutes rule is gone; the test lasts until that
    # Claude says it is done (ai-player --minutes 0, a two-hour ceiling).
    # Blu 11-07 01:30: "scrap the AI test, it's just gonna eat up our usage". Claude plays a test only when Settings
    # aiPlayerBrain is "claude"; "off" (the setting now) means nobody plays the tests but Blu.
    $playAlong = [bool]($app -and $app.aiPlayerBrain -eq "claude")
    $padFile = Join-Path $gameDir "ai-pad.txt"
    if ($playAlong) { $launch["ANYPS5_INPUT_CONFIG"] = Join-Path $Tools "ai-input.ini"; Remove-Item -LiteralPath $padFile -ErrorAction SilentlyContinue; $launch["APS5_PAD_INPUT_FILE"] = $padFile }
    # Blu 11-15: PR #9508 drew Octopath Traveler II's title upside down at 4 FPS and the test said "runs". The window
    # shots come back black when the game sits inside another window, so AnyPS5 itself saves 16 real frames, one every
    # 900 presented (frame_NNN.bmp); frame_check.py turns them into test-shots PNGs and judges them after the test.
    Get-ChildItem -LiteralPath $gameDir -Filter "frame_*.bmp" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    if (-not $launch.ContainsKey("APS5_DUMP_FRAMES")) { $launch["APS5_DUMP_FRAMES"] = "16"; $launch["APS5_DUMP_FRAMES_EVERY"] = "900" }
    # Blu 11-17: every test also measures speed and finds what slows the game down. AnyPS5 writes one summary line every
    # 10 s (FPS, frame p99, GPU busy, busiest workers and phases) into its own folder; perf_report.py turns it into
    # _reports\perf-history.jsonl and perf-report.md, with the bottleneck, log problems and changes against the last run.
    $perfDir = Join-Path $Games ("_reports\perf-runs\{0}-{1}-{2}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ((Split-Path $gameDir -Leaf) -replace '[^A-Za-z0-9]+', '-'), $label)
    if (-not $launch.ContainsKey("APS5_TRACE_SUMMARY_EVERY")) { New-Item -ItemType Directory -Force $perfDir | Out-Null; $launch["APS5_TRACE_SUMMARY_EVERY"] = "10"; $launch["APS5_TRACE_DIR"] = $perfDir }
    $player = $null
    $outer = @{}
    foreach ($k in $launch.Keys) { $outer[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $launch[$k]) }
    if ($launch.Count) { Log "  launch settings: $(($launch.Keys | Sort-Object | ForEach-Object { "$_=$($launch[$_])" }) -join ' ')" }
    # 11-05 VLC's test launched while Claude ran Gran Turismo by hand: the check before the build was minutes old. Two
    # games on one GPU spoil both results, so it is checked again right before the launch.
    while ($true) { $blockers = @(Game-Blockers); if (-not $blockers) { break }; Trace "test of $(Split-Path $gameDir -Leaf): waiting, another game is running: $($blockers -join ' | ') (checks again in 30 s)"; Start-Sleep 30 }
    $p = $null
    try { foreach ($try in 1..3) { try { $p = & (Join-Path $PSScriptRoot "start-game.ps1") -Exe $exe -Dir $gameDir; break } catch { Start-Sleep 8 } } }
    finally { foreach ($k in $outer.Keys) { [Environment]::SetEnvironmentVariable($k, $outer[$k]) } }
    if (-not $p) { return "could not start (blocked?)" }
    $started = Get-Date; $deadline = $started.AddSeconds(240); $limit = $started.AddMinutes(20); $frames = 0; $title = ""; $fpsLog = @()
    $logFile = Join-Path $gameDir "anyps5.log"; $mark = Get-Date; $cpu0 = 0.0; $size0 = -1L; $seen0 = 0; $firstFrame = $null
    $cache0 = @(Get-ChildItem -LiteralPath (Join-Path $gameDir "shader_cache") -Recurse -File -ErrorAction SilentlyContinue).Count; $compiling = $false
    # 11-04 one shot at the end caught Astro's Playroom on a black frame while it was running: a shot every 45 s once
    # frames come, so the report can use the newest one that shows something.
    $shotDir = Join-Path $gameDir "test-shots"; $shotStamp = Get-Date -Format "yyyy-MM-dd-HHmm"; $nextShot = $null
    while (-not $p.HasExited) {
        Start-Sleep 3; $p.Refresh(); $title = Game-Title $p
        if ($firstFrame -and (Get-Date) -ge $nextShot) {
            New-Item -ItemType Directory -Force $shotDir | Out-Null
            try { & (Join-Path $Tools "window-shot.ps1") -GameDir $gameDir -Out (Join-Path $shotDir ("{0}-{1}-t{2:000}.png" -f $shotStamp, $label, [int]((Get-Date) - $started).TotalSeconds)) | Out-Null } catch { }
            $nextShot = (Get-Date).AddSeconds(45)
        }
        if ($title -match 'FPS: ([0-9.]+) \(([0-9]+)\)') {
            $frames = [int]$Matches[2]
            if ($firstFrame) { $fpsLog += , @((Get-Date), [double]$Matches[1]) }
            if (-not $firstFrame) {
                $firstFrame = Get-Date; $nextShot = $firstFrame.AddSeconds(20)
                if ($playAlong) {
                    $player = Start-Process python -ArgumentList "-I", "`"$(Join-Path $Tools 'ai-player.py')`"", "`"$gameDir`"", "--attach", $p.Id, "--brain", "claude", "--minutes", 0, "--pad-file", "`"$padFile`"" -WindowStyle Hidden -PassThru
                    $limit = $started.AddMinutes(125)
                    Log "  this test's own Claude plays it from its first frame until it is done (ai-player, process $($player.Id), controller file $padFile)"
                }
            }
            $playing = $player -and -not $player.HasExited
            if ($frames -gt 600 -and -not $playing -and ((Get-Date) - $firstFrame).TotalSeconds -ge 180) { break }
        }
        if (((Get-Date) - $mark).TotalSeconds -ge 60) {
            # Blu 11-05: My First Gran Turismo, like Spider-Man Remastered, spends its first run compiling shaders. While
            # the game's shader cache keeps growing the run goes on, up to an hour.
            $cacheNow = @(Get-ChildItem -LiteralPath (Join-Path $gameDir "shader_cache") -Recurse -File -ErrorAction SilentlyContinue).Count
            if ($cacheNow -gt $cache0) { $compiling = $true; $limit = [datetime]([Math]::Max($limit.Ticks, [Math]::Min((Get-Date).AddMinutes(5).Ticks, $started.AddMinutes(60).Ticks))); $deadline = (Get-Date).AddSeconds(180) } else { $compiling = $false }
            $cache0 = $cacheNow
            $cpu = try { $p.TotalProcessorTime.TotalSeconds } catch { 0.0 }
            $size = if (Test-Path -LiteralPath $logFile) { (Get-Item -LiteralPath $logFile).Length } else { 0L }
            if ($cpu - $cpu0 -gt 3 -or $size -ne $size0 -or $frames -ne $seen0) { $deadline = (Get-Date).AddSeconds(180) }
            $cpu0 = $cpu; $size0 = $size; $seen0 = $frames; $mark = Get-Date
        }
        if ((Get-Date) -gt $deadline -or (Get-Date) -gt $limit) { break }
    }
    # "Pause everything" in the AnyPS5 Games window closed this game: no result; the game is tested again after Resume.
    $interrupted = Join-Path $Tools "test-interrupted.flag"
    if (Test-Path -LiteralPath $interrupted) { Remove-Item -LiteralPath $interrupted -ErrorAction SilentlyContinue; throw "test of $(Split-Path $gameDir -Leaf) interrupted by Pause; it runs again after Resume" }
    # "runs" needs at least 300 frames: a game that drew a frame or two and then stopped (Ghost Song with only #11575:
    # 2 frames in 4 minutes) is stalled, not running.
    $result = if ($p.HasExited) { "exited 0x{0:X8} after {1:N0} s ({2})" -f $p.ExitCode, ((Get-Date) - $p.StartTime).TotalSeconds, $(if ($frames -gt 0) { "$frames frames drawn" } else { "no frame drawn" }) } elseif ($frames -ge 300) { "runs: $title | first frame after $([int]($firstFrame - $started).TotalSeconds) s" } elseif ($frames -gt 0) { "stalls: only $frames frames in $([int]((Get-Date) - $started).TotalMinutes) min ($title)" } else { ("no frames after {0:N0} min" -f ((Get-Date) - $started).TotalMinutes) }
    if ($compiling -and $result -notlike "runs:*") { $result += " | shader cache still growing when the test ended" }
    # 11-05 Divinity's test of #16025 (the fix for its stop) exited 0xC0000135 (a DLL not found) and triage took it for
    # progress. A Windows loader code means the game never ran: say why, and callers treat it as "did not start".
    if ($p.HasExited -and ("{0:X8}" -f $p.ExitCode) -in "C0000135", "C0000139", "C000007B", "C0000142") {
        $m = Native { python -I (Join-Path $Tools "assistant\missing_dll.py") $gameDir }
        $why = if ($m.code -eq 1) { (@($m.out | Where-Object { $_ -like "missing *" }) | Select-Object -First 3) -join "; " } else { "every import of the exe and libs resolves; a library loaded later failed" }
        $result += " | did not start: $why"
    }
    # What the game showed at the end of the test (Blu 11-04: the Game bring-up reports say what was on screen on the
    # commit tested): the window as seen, title bar with AnyPS5's FPS included, in <game>\test-shots.
    if (-not $p.HasExited) {
        $shots = Join-Path $gameDir "test-shots"
        New-Item -ItemType Directory -Force $shots | Out-Null
        try { & (Join-Path $Tools "window-shot.ps1") -GameDir $gameDir -Out (Join-Path $shots ("{0}-{1}-zend.png" -f $shotStamp, $label)) | Out-Null } catch { }
        Stop-Process -Id $p.Id -Force; Start-Sleep 2
        if ($player) { try { [void]$player.WaitForExit(90000) } catch { }; Log "  Claude's play: $((Get-ChildItem -LiteralPath (Join-Path $gameDir 'ai-runs') -Directory -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1).FullName)" }
    }
    # Blu 11-15 (Octopath Traveler II with #9508): "runs" also needs a real picture. Every saved frame one flat colour is
    # "blank"; on every test that runs Claude (the bridge's Haiku "look", 80 a day) looks at the newest real frames and a BROKEN answer
    # (upside down, garbled) makes it "looks broken". Neither counts as runs. A game that falls under 12 FPS for its
    # last minute after reaching 30 keeps "runs" with a note, since some games are only slow.
    $fc = $null
    $fcArgs = @("-I", (Join-Path $Tools "assistant\frame_check.py"), $gameDir, (Join-Path $gameDir "test-shots"), "$shotStamp-$label")
    if ($result -like "runs:*") { $fcArgs += "--look" }
    try { $fc = (& python @fcArgs 2>$null | Select-Object -Last 1) | ConvertFrom-Json } catch { }
    if ($fc) { Log ("  frames AnyPS5 saved: {0} ({1} one flat colour){2}" -f $fc.frames, $fc.flat, $(if ($fc.look) { "; Claude's look: $($fc.look) $($fc.answer)" } else { "" })) }
    if ($fc -and $result -like "runs:*") {
        if ($fc.all_flat -and $fc.frames -ge 3) { $result = "blank: every frame AnyPS5 saved was $(@($fc.colours) -join ' or ') ($($fc.frames) frames) | was $result" }
        elseif ($fc.look -eq "broken") { $result = "looks broken: $($fc.answer) (Claude looked at the frames AnyPS5 saved, $($fc.newest)) | was $result" }
    }
    if ($result -like "runs:*" -and $fpsLog.Count -ge 5) {
        $end = $fpsLog[-1][0]
        $late = @($fpsLog | Where-Object { $_[0] -ge $end.AddSeconds(-60) } | ForEach-Object { $_[1] } | Sort-Object)
        $peak = ($fpsLog | ForEach-Object { $_[1] } | Measure-Object -Maximum).Maximum
        if ($late.Count -ge 5) { $median = $late[[int][Math]::Floor($late.Count / 2)]; if ($median -lt 12 -and $peak -ge 30) { $result += " | slow at the end: {0:N1} FPS for its last minute after {1:N0} FPS earlier" -f $median, $peak } }
    }
    $log = Join-Path $gameDir "anyps5.log"
    if (Test-Path -LiteralPath $log) {
        $why = Select-String -LiteralPath $log -Pattern "not implemented|^FATAL|unhandled exception" | Select-Object -First 2 | ForEach-Object { $_.Line.Trim() }
        if (($why -or $result -like "*did not start*") -and $result -notlike "runs:*") {
            if ($why) { $result += " | " + ($why -join " | ") }
            New-Item -ItemType Directory -Force (Join-Path $gameDir "crash-logs") | Out-Null
            Copy-Item -LiteralPath $log -Destination (Join-Path $gameDir ("crash-logs\{0}-{1}.log" -f (Get-Date -Format "yyyy-MM-dd-HHmm"), $label))
        }
    }
    if ($launch["APS5_TRACE_DIR"] -eq $perfDir) {
        $pr = Join-Path $Tools "perf-tests\perf_report.py"
        $ran = [int]((Get-Date) - $mark).TotalSeconds
        try {
            $rec = (& python -I $pr --device pc --game (Split-Path $gameDir -Leaf) --build $label --dir $perfDir --log $log --history (Join-Path $Games "_reports\perf-history.jsonl") --report (Join-Path $Games "_reports\perf-report.md") --ran $ran --ended ($result -split ':')[0] 2>$null | Select-Object -Last 1) | ConvertFrom-Json
            if ($rec.perf.fps_median) { Log ("  perf: FPS {0} (low {1}, worst {2}), frame p99 {3} ms, {4}-bound; {5}" -f $rec.perf.fps_median, $rec.perf.fps_p10, $rec.perf.fps_worst, $rec.perf.frame_p99_ms_median, $rec.perf.bound, (@($rec.changes) -join '; ')) }
        } catch { Log "  perf report failed: $_" }
    }
    $result
}

# Blu 11-05: test copies (_test\<game>, _test\_fps, _test\_fix, _test\_src, _test\_set aside) are deleted once nothing
# has used them for an hour; they had grown to dozens of folders, and hard links in them kept 29 GB of Astro's Playroom
# on D: after its folder was deleted. Whoever needs one again makes a new one (hard links, quick).
# 11-05 the first sweep deleted ~60 copies one by one inside the pass and held up rechecks and tests for 45 minutes
# (Blu: "it's just been deleting stuff"): the deleting now runs in its own low-priority process, one batch at a time.
# A game folder that is a junction to another drive (G:) gets its test copies on that drive, where the game's files
# can be hard-linked: a copy on D: cannot link them (11-06 High on Life: "linking contentids.json failed (error 17)").
function Test-Roots {
    $own = $Games.Substring(0, 3)
    @((Join-Path $Games "_test")) + @(Get-ChildItem -LiteralPath $Games -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.LinkType -eq "Junction" } |
        ForEach-Object { "$(@($_.Target)[0])" } | Where-Object { $_ -match '^[A-Za-z]:\\' } | ForEach-Object { $_.Substring(0, 3).ToUpper() } |
        Where-Object { $_ -ne $own.ToUpper() } | Select-Object -Unique | ForEach-Object { Join-Path $_ "AnyPS5 Games\_test" })
}
function Fix-Root([string]$gameDir) {
    $target = try { "$(@((Get-Item -LiteralPath $gameDir -Force).Target)[0])" } catch { "" }
    if ($target -match '^[A-Za-z]:\\' -and $target.Substring(0, 3).ToUpper() -ne $Games.Substring(0, 3).ToUpper()) { return Join-Path $target.Substring(0, 3) "AnyPS5 Games\_test\_fix" }
    Join-Path $Games "_test\_fix"
}
$CleanJob = $null
function Clean-TestCopies {
    $roots = @(Test-Roots | Where-Object { Test-Path -LiteralPath $_ })
    if (-not $roots) { return }
    if ($script:CleanJob) {
        if (-not $script:CleanJob.proc.HasExited) { return }
        foreach ($p in $script:CleanJob.items) { if (-not (Test-Path -LiteralPath $p)) { Log "  deleted the unused test copy $p" } }
        $script:CleanJob = $null
    }
    $busy = @(Get-CimInstance Win32_Process | ForEach-Object { "$($_.ExecutablePath) $($_.CommandLine)" } | Where-Object { $_ -match '\\_test\\' })
    $cut = (Get-Date).AddMinutes(-60)
    $gone = @()
    $items = foreach ($root in $roots) {
        foreach ($i in Get-ChildItem -LiteralPath $root -Force) {
            if ($i.PSIsContainer -and $i.Name -in "_fps", "_fix", "_src", "_set aside") { Get-ChildItem -LiteralPath $i.FullName -Force } else { $i }
        }
    }
    foreach ($i in $items) {
        $log = Join-Path $i.FullName "anyps5.log"
        $last = @($i.LastWriteTime) + @(if ($i.PSIsContainer -and (Test-Path -LiteralPath $log)) { (Get-Item -LiteralPath $log).LastWriteTime }) | Sort-Object | Select-Object -Last 1
        if ($last -gt $cut) { continue }
        if ($busy | Where-Object { $_.IndexOf($i.FullName, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) { continue }
        $gone += $i.FullName
    }
    if (-not $gone) { return }
    $cmdFile = Join-Path $Tools "live\clean-test-copies.cmd"
    Set-Content -LiteralPath $cmdFile -Encoding oem -Value (@("@echo off") + @($gone | ForEach-Object { if (Test-Path -LiteralPath $_ -PathType Container) { "rd /s /q `"$_`"" } else { "del /f /q `"$_`"" } }))
    $proc = Start-Process cmd.exe -ArgumentList "/d", "/c", "`"$cmdFile`"" -WindowStyle Hidden -PassThru
    $null = $proc.Handle
    try { $proc.PriorityClass = "Idle" } catch { }
    $script:CleanJob = @{ proc = $proc; items = $gone }
    Log "  deleting $($gone.Count) unused test copies in the background"
}

# Blu 11-05: our GitHub threads must not wait days for an answer (#14168, #14296 did). needs_reply.py lists every open
# issue and PR of ours where someone else spoke last; each new one is an EVENT, and _reports\needs-reply.md lists them.
function Check-NeedsReply {
    $n = Native { python -I (Join-Path $Tools "assistant\needs_reply.py") --json }
    if ($n.code -ne 0) { Log "  needs-reply check failed: $(($n.out | Select-Object -Last 1))"; return }
    $now = @((($n.out -join "`n") | ConvertFrom-Json) | Where-Object { $_ })
    $seenPath = Join-Path $Tools "live\needs-reply-seen.txt"
    $seen = @(Get-Content -LiteralPath $seenPath -ErrorAction SilentlyContinue)
    foreach ($t in $now) {
        $key = "$($t.number) $($t.waiting_since)"
        if ($key -in $seen) { continue }
        Event "NEEDS REPLY #$($t.number) ($($t.title)): $($t.from -join ', ') waiting since $($t.waiting_since.Substring(0, 16)) - $($t.url)"
    }
    Set-Content -LiteralPath $seenPath -Value @($now | ForEach-Object { "$($_.number) $($_.waiting_since)" }) -Encoding utf8
}

function Retire-Archives([string]$folder, $entry) {
    # Archives go to the "_finished archives" folder on their own drive (moving is instant there).
    $root = Join-Path (Split-Path $folder -Parent) "_finished archives (safe to delete)"
    $dest = Join-Path $root (Split-Path $folder -Leaf)
    $moved = 0
    # 11-07 a .pkg dump (Red Dead Redemption, unpacked to "unpacked\") is an archive too once the game is built.
    foreach ($a in Get-ChildItem -LiteralPath $folder -File -Force | Where-Object { $_.Extension.ToLowerInvariant() -in @($ArchiveExt) + ".pkg" -or $_.Name -match '\.(7z|zip)\.\d{3}$' }) {
        New-Item -ItemType Directory -Force $dest | Out-Null
        Move-Item -LiteralPath $a.FullName -Destination $dest -Force; $moved++
    }
    if ($moved) { Log "  moved $moved archive file(s) to $dest" }
}

# Runs a native command and returns its exit code and output lines. With $ErrorActionPreference = Stop, Windows
# PowerShell turns any stderr line of a redirected native command into a terminating error; this collects it instead.
function Native([scriptblock]$command) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $shown = try { $ExecutionContext.InvokeCommand.ExpandString($command.ToString().Trim()) } catch { $command.ToString().Trim() }
    # Archive passwords (UnRAR -p<password>) never reach the console: Blu does not want them shown.
    Trace ('$ ' + (($shown -replace '(?<=\s)-p\S*', '-p***') -replace '\s+', ' '))
    try { $out = @(& $command 2>&1 | ForEach-Object { $l = "$_"; Trace ("  " + $l); $l }); Trace "  (exit $LASTEXITCODE)"; [pscustomobject]@{ code = $LASTEXITCODE; out = $out } }
    finally { $ErrorActionPreference = $previous }
}
function Copy-Retry([string]$from, [string]$to) {
    for ($try = 1; ; $try++) { try { Copy-Item -LiteralPath $from -Destination $to -Force -ErrorAction Stop; return } catch { if ($try -ge 20) { throw }; Start-Sleep -Milliseconds 500 } }
}

# What stopped a failed test, read from the game's anyps5.log.
function Read-Failure([string]$gameDir) {
    $log = Join-Path $gameDir "anyps5.log"
    # kind: "missing function" (function), "stopped" (AnyPS5 threw another message), "crash" (where: module+offset)
    # or "no frames"; message is the text a PR search starts from.
    $f = [pscustomobject]@{ kind = "no frames"; function = ""; where = ""; line = ""; message = "" }
    if (-not (Test-Path -LiteralPath $log)) { return $f }
    $what = Select-String -LiteralPath $log -Pattern 'what\(\):\s+(\S.*)$' | Select-Object -First 1
    if ($what) {
        $text = $what.Matches[0].Groups[1].Value.Trim(); $f.line = $what.Line.Trim()
        # "sceFoo not implemented" or "sceFoo: flag 0x8 not implemented" names a function; "NGS2: custom module id
        # 0x17 is not implemented" names none, so it is searched by its words.
        $id = if ($text -match '^([A-Za-z_]\w*)\b') { $Matches[1] } else { "" }
        if ($id -and ($id -match '^(sce|_)' -or $id -cmatch '^[a-z]+[A-Z]') -and $text -match 'not implemented') { $f.kind = "missing function"; $f.function = $id; return $f }
        $f.kind = "stopped"; $f.message = $text; return $f
    }
    $thrown = Select-String -LiteralPath $log -Pattern '\b(sce[A-Za-z0-9_]+)\b.*not implemented' | Select-Object -First 1
    if ($thrown) { $f.kind = "missing function"; $f.function = $thrown.Matches[0].Groups[1].Value; $f.line = $thrown.Line.Trim(); return $f }
    $fatal = Select-String -LiteralPath $log -Pattern '^FATAL' | Select-Object -First 1
    if ($fatal) {
        $f.kind = "crash"; $f.line = $fatal.Line.Trim()
        $frame = Get-Content -LiteralPath $log | Select-Object -Skip $fatal.LineNumber -First 30 | Where-Object { $_ -match '^\s+(rip|#\d+)\s+0x[0-9a-f]+\s+\S+\+0x[0-9a-f]+' } | Select-Object -First 1
        if ($frame -and $frame -match '\s(\S+\+0x[0-9a-f]+)') { $f.where = $Matches[1] }
        if ($f.line -notmatch 'unhandled exception 0x') { $f.message = $f.line -replace '^FATAL:?\s*', '' }
        return $f
    }
    # No stop and no picture: the first warning that names something unsupported is the lead.
    $hint = Select-String -LiteralPath $log -Pattern '(?i)\b(not implemented|not supported|unsupported|unhandled|unknown \w+ (id|format|opcode|type))\b' | Select-Object -First 1
    if ($hint) { $f.line = $hint.Line.Trim(); $f.message = $f.line }
    $f
}

# The AnyPS5 libraries that export a function, at the commit the games are built from.
function Find-Libraries([string]$function) {
    $r = Native { git -C $PrSource grep -l -E "APS5_VABI[^(]*\b$function\(" $Base -- core/libs/prx }
    @($r.out | ForEach-Object { if ($_ -match 'core/libs/prx/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
}

# Open AnyPS5 PRs that mention the function and change one of its libraries, most recently updated first.
# With $files, a PR must change one of those files (the code that threw); otherwise one of the libraries.
function Find-Prs([string[]]$queries, [string[]]$libraries, [string[]]$files = @()) {
    $found = @{}
    foreach ($q in $queries) {
        $r = Native { gh search prs --repo $Repo --state open $q --json number,title,url,updatedAt --limit 30 }
        if ($r.code -ne 0 -or -not ($r.out -join "").Trim()) { continue }
        $list = ($r.out -join "`n") | ConvertFrom-Json
        foreach ($p in $list) { $found[[int]$p.number] = $p }
    }
    $prs = foreach ($p in ($found.Values | Sort-Object updatedAt -Descending)) {
        $v = Native { gh pr view $p.number --repo $Repo --json files,headRefOid }
        if ($v.code -ne 0) { continue }
        $info = ($v.out -join "`n") | ConvertFrom-Json
        $libs = @($info.files.path | ForEach-Object { if ($_ -match '^core/libs/prx/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
        if ($info.files.path | Where-Object { $_ -like "core/shader/*" }) { $libs = @($libs) + "libSceAgcDriver" | Select-Object -Unique }
        if (-not $libs) { continue }   # nothing a game loads changes (docs, tools, the relinker)
        if ($files) { if (-not ($info.files.path | Where-Object { $_ -in $files })) { continue } }
        elseif ($libraries -and -not ($libs | Where-Object { $_ -in $libraries })) { continue }
        [pscustomobject]@{ number = [int]$p.number; title = $p.title; url = $p.url; head = $info.headRefOid.Substring(0, 10); libraries = $libs }
    }
    @($prs)
}

# Predicted missing functions (predict_missing.py): every function a game imports that its AnyPS5 build lacks, has only
# as a stub, or stops on for some inputs, read from the relinker's import lists; written to _reports\predicted\<game>.md
# and _reports\predicted-missing.md. A failure here is only logged, never stops the pipeline.
$Predictor = Join-Path $Tools "predict_missing.py"
function Predict-Missing([string[]]$gameDirs = @(), [int]$maxRelinks = -1) {
    if (-not (Test-Path -LiteralPath $Predictor)) { return }
    $predictArgs = @(if ($gameDirs) { foreach ($g in $gameDirs) { "--game"; $g } } else { "--all" }) + @("--max-relinks", "$maxRelinks")
    try {
        $n = Native { python -I $Predictor @predictArgs }
        foreach ($l in @($n.out | Where-Object { $_ -match ': \d+ imports|not checked|^ranking' })) { Log "  predicted: $l" }
        if ($n.code -ne 0) { Log "predicted missing functions: exit $($n.code)" }
    } catch { Log "error: predicted missing functions: $($_.Exception.Message)" }
}

# Open PRs for what a failed game will likely meet next, from its predicted list (the same library as the stop first,
# then functions AnyPS5 lacks, then stubs): a PR test takes them along, so a game the stop's PR gets further does not
# stop again at the next function of the same setup. At most $count PRs, from the list's first 6 named functions.
function Upcoming-Prs([string]$gameDir, [string]$stopLabel, [int]$count = 2) {
    $path = Join-Path $Reports ("predicted\" + (Split-Path $gameDir -Leaf) + ".json")
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $p = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($p.error) { return @() }
    $picked = @()
    foreach ($f in @($p.predicted | Where-Object { $_.named -and -not $_.weak -and -not $_.stopsNow -and $_.function -ne $stopLabel } | Select-Object -First 6)) {
        $prs = @(Find-Prs @($f.function) @($f.library) | Where-Object { $_.number -notin @($picked | ForEach-Object { $_.number }) })
        if ($prs) { $picked += ($prs[0] | Add-Member -NotePropertyName forFunction -NotePropertyValue $f.function -PassThru -Force) }
        if ($picked.Count -ge $count) { break }
    }
    @($picked)
}

# What a failure is matched on: the same failure after a PR means the PR did not help. Numbers and addresses vary
# between runs, so they do not count.
function Failure-Key($f) { ("$($f.kind)|$($f.function)|$($f.message)|$($f.where -replace '\+0x[0-9a-fA-F]+', '')") -replace '0x[0-9a-fA-F]+|\d+', '#' }
function Failure-Label($f) {
    if ($f.function) { return $f.function }
    if ($f.message) { return $f.message.Substring(0, [Math]::Min(90, $f.message.Length)) }
    if ($f.where) { return "$($f.kind) at $($f.where)" }
    "$($f.kind)"
}
# The plain-word runs of a message (2 words or more), without values, quotes or "not implemented", longest first.
function Message-Pieces([string]$text) {
    $t = $text -replace '"[^"]*"|''[^'']*''|`[^`]*`', ' ' -replace '0x[0-9a-fA-F]+|\b\d+\b', ' ' -replace '(?i)\b((is|are) )?not (implemented|supported)\b', ' '
    @($t -split '[:;,.()\[\]{}=<>/|!?]' | ForEach-Object { ($_ -replace '\s+', ' ').Trim() } | Where-Object { @($_ -split ' ').Count -ge 2 } | Sort-Object { @($_ -split ' ').Count } -Descending)
}
# Where to look for PRs: a missing function by name and its libraries; a message by the library its prefix names
# ("NGS2: ...", "scePlayGo: ...") and the source files that contain its words; a crash by the library it happened in.
# A crash in the game's own code or a game without a picture and without any warning gives no lead.
function Find-Leads($f) {
    if ($f.kind -eq "missing function") { return [pscustomobject]@{ queries = @($f.function); libraries = @(Find-Libraries $f.function); files = @() } }
    $files = @(); $libs = @(); $queries = @()
    $dirs = @((Native { git -C $PrSource ls-tree --name-only "${Base}:core/libs/prx" }).out | ForEach-Object { Split-Path $_ -Leaf })
    if ($f.message -match '^\s*([A-Za-z][\w ]{0,30}?):') {
        $token = (($Matches[1] -split ' ')[0]) -replace '^(sce|lib)', ''
        if ($token.Length -ge 3) { $libs += @($dirs | Where-Object { ($_ -replace '^libSce', '' -replace '\.native$', '') -like "$token*" }) }
    }
    $pieces = @(if ($f.message) { Message-Pieces $f.message })
    if ($pieces) {
        # A message is often put together at run time ("the custom " + name + " module"), so every 3-word window is
        # searched at once and the files with the most hits win.
        $windows = @(foreach ($p in $pieces) { $w = @($p -split ' '); if ($w.Count -le 3) { $p } else { for ($i = 0; $i -le $w.Count - 3; $i++) { $w[$i..($i + 2)] -join ' ' } } }) | Select-Object -Unique -First 40
        $patterns = @($windows | ForEach-Object { "-e"; $_ })
        $r = Native { git -C $PrSource grep -F -o -I $patterns $Base -- core }
        $hits = @($r.out | Where-Object { $_ -match '^[0-9a-f]+:(core/[^:]+):' } | ForEach-Object { ($_ -split ':', 3)[1] } | Group-Object | Sort-Object Count -Descending)
        if ($hits) { $top = $hits[0].Count; $files = @($hits | Where-Object { $_.Count -eq $top } | Select-Object -First 5 | ForEach-Object { $_.Name }) }
        $queries += "`"$($pieces[0])`""
    }
    if ($f.where -match '^(lib[A-Za-z0-9_]+)') {
        $module = $Matches[1]
        $libs += @($dirs | Where-Object { $_ -eq $module -or $_ -like "$module.*" })
    }
    $libs += @($files | ForEach-Object { if ($_ -match '^core/libs/prx/([^/]+)/') { $Matches[1] } })
    $libs = @($libs | Where-Object { $_ } | Select-Object -Unique)
    foreach ($l in $libs) { $short = $l -replace '^libSce', '' -replace '\.native$', ''; $queries += $short; if ($short -match 'Driver$') { $queries += ($short -replace 'Driver$', '') } }
    [pscustomobject]@{ queries = @($queries | Where-Object { $_ } | Select-Object -Unique); libraries = $libs; files = $files }
}

# Merges a stack of PRs onto $Base in the PR worktree (local merge commits only, nothing pushed) and builds the
# libraries they change.
function Build-PrLibraries($stack, [string]$onto = $Base, [string[]]$libs = @()) {
    Use-Pc "pipeline build of PR libraries $(@($stack | ForEach-Object { '#' + $_.number }) -join ' ')" $script:PcPriority { Build-PrLibrariesUnlocked $stack $onto $libs } $PcWait
}
function Build-PrLibrariesUnlocked($stack, [string]$onto = $Base, [string[]]$libs = @()) {
    $fail ={ param($why) [pscustomobject]@{ ok = $false; reason = $why; commit = ""; built = @{} } }
    # A stop in the middle of a git command leaves index.lock behind and every later checkout fails; remove one that
    # is older than 10 minutes while no git process runs.
    $gitDir = (Native { git -C $PrSource rev-parse --git-dir }).out | Select-Object -First 1
    $lock = Join-Path $(if ([IO.Path]::IsPathRooted($gitDir)) { $gitDir } else { Join-Path $PrSource $gitDir }) "index.lock"
    if ((Test-Path -LiteralPath $lock) -and ((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalMinutes -gt 10 -and -not (Get-CimInstance Win32_Process -Filter "Name='git.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape((Split-Path $PrSource -Leaf)) })) { Remove-Item -LiteralPath $lock -Force; Log "removed a stale $lock" }
    $r = Native { git -C $PrSource checkout -q --detach $onto }
    if ($r.code -ne 0) { return & $fail "checking out $onto failed: $($r.out -join ' ')" }
    foreach ($p in $stack) {
        $fetched = $false
        foreach ($try in 1..3) { $r = Native { git -C $PrSource fetch -q origin "+refs/pull/$($p.number)/head:refs/remotes/pr/$($p.number)" }; if ($r.code -eq 0) { $fetched = $true; break }; Start-Sleep 20 }
        if (-not $fetched) { return & $fail "fetching #$($p.number) failed" }
        $r = Native { git -C $PrSource -c user.name=blu -c user.email=user@example.com merge --no-edit -q "refs/remotes/pr/$($p.number)" }
        if ($r.code -ne 0) { Native { git -C $PrSource merge --abort } | Out-Null; return & $fail "#$($p.number) does not merge cleanly onto $onto" }
    }
    # Newer commits and PRs add submodules (3rdparty/libressl-openbsd, 11-03); without them CMake stops before any
    # library builds, so every PR test failed while the checkout lacked them.
    $r = Native { git -C $PrSource submodule update --init --recursive --jobs 4 }
    if ($r.code -ne 0) { return & $fail "updating submodules failed: $(($r.out | Select-Object -Last 2) -join ' ')" }
    $commit = (Native { git -C $PrSource rev-parse --short=10 HEAD }).out | Select-Object -First 1
    if ($env:PATH -notlike "*$($Settings.mingwBin)*") { $env:PATH = "$($Settings.mingwBin);$env:PATH" }
    $built = @{}
    foreach ($lib in @(@($stack | ForEach-Object { $_.libraries }) + $libs | Where-Object { $_ } | Select-Object -Unique)) {
        $r = Native { & (Join-Path $Settings.mingwBin "ninja.exe") -j 8 -C $PrBuild "core/libs/libs/$lib.prx" }
        if ($r.code -ne 0) { return & $fail "building $lib.prx failed: $(($r.out | Select-Object -Last 3) -join ' ')" }
        $built[$lib] = Join-Path $PrBuild "core\libs\libs\$lib.prx"
    }
    [pscustomobject]@{ ok = $true; reason = ""; commit = $commit; built = $built }
}

# Swaps PR libraries into a game, test-launches it, and always puts the game's own libraries back.
function Test-WithLibraries([string]$gameDir, $built, [string]$label, [hashtable]$envs = @{}) {
    $swapped = @()
    # 11-07 Blu: the GPU lanes rerun every PR test on the 1650 and the Radeon when nothing else is due. The PR's
    # libraries and this run's result are kept as a job in live\pr-jobs\ (gpu-lanes.ps1 picks it up); the last 30 stay.
    $jobDir = Join-Path $Tools ("live\pr-jobs\" + (Safe-Name ("{0} {1} {2}" -f (Split-Path $gameDir -Leaf), $label, (Get-Date -Format 'MMdd-HHmmss'))))
    try {
        New-Item -ItemType Directory -Force $jobDir | Out-Null
        foreach ($lib in $built.Keys) { Copy-Retry $built[$lib] (Join-Path $jobDir "$lib.prx") }
        Get-ChildItem -LiteralPath (Split-Path $jobDir) -Directory | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | ForEach-Object { & cmd.exe /d /c "rd /s /q `"$($_.FullName)`"" }
    } catch { Log "  keeping the PR test for the GPU lanes failed: $($_.Exception.Message)"; $jobDir = $null }
    try {
        foreach ($lib in $built.Keys) {
            $dst = Join-Path $gameDir "libs\$lib.prx"
            $had = Test-Path -LiteralPath $dst
            if ($had) { Copy-Retry $dst "$dst.pipeline-original" }
            Copy-Retry $built[$lib] $dst
            $swapped += [pscustomobject]@{ path = $dst; had = $had }
        }
        $result = Test-Game $gameDir $label $envs
        if ($jobDir) {
            [ordered]@{ gameDir = $gameDir; label = $label; libs = @($built.Keys); made = (Get-Date -Format o); envs = $envs; main = "$result"; results = @{} } |
                ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $jobDir "job.json") -Encoding utf8
        }
        [pscustomobject]@{ result = $result; failure = (Read-Failure $gameDir); started = ($result -notlike "*did not start*") }
    } finally {
        foreach ($s in $swapped) {
            if ($s.had) { if (Test-Path -LiteralPath "$($s.path).pipeline-original") { Copy-Retry "$($s.path).pipeline-original" $s.path; Remove-Item -LiteralPath "$($s.path).pipeline-original" -Force } }
            else { Remove-Item -LiteralPath $s.path -Force -ErrorAction SilentlyContinue }
        }
    }
}

# Blu 11-05: every game, not only one that stopped, is checked against what is open upstream. The Exit 8 "runs" with
# black walls and floors and had no stop to search from. Every 6 hours each built game's title ID is searched in open
# PRs; a PR (or a new push of one) that changes a library the game loads and was not tested on it yet is queued as a
# PR test, one per game at a time. The heads tested are kept in the entry's titlePrs.
function Check-TitlePrs {
    # 8 games per call (every 45 minutes, all of them in about 5 hours): searching all 50 at once held up My First Gran
    # Turismo's "first" unpack for 9 minutes on 11-05.
    $names = @($State.Keys | Sort-Object)
    if (-not $names) { return }
    $start = [int]$script:TitlePrsCursor % $names.Count
    $script:TitlePrsCursor = $start + 8
    foreach ($name in @(for ($i = 0; $i -lt [Math]::Min(8, $names.Count); $i++) { $names[($start + $i) % $names.Count] })) {
        $e = Get-Entry $name
        if (-not $e.gameDir -or $e.prTest -or $e.status -notin "done", "skip") { continue }
        $id = try { "$((Get-Content -LiteralPath (Join-Path $e.gameDir "game.json") -Raw | ConvertFrom-Json).titleId)" } catch { "" }
        if ($id -notmatch '^[A-Z]{4}\d{5}$') { continue }
        Start-Sleep 3
        $seen = @("$($e.titlePrs)" -split ' ' | Where-Object { $_ })
        # Blu 11-05 ("why are we stuck testing every game?"): only fix and feature PRs, not perf, test, docs or CI ones;
        # a game that already runs only takes a PR that also names its current problem.
        $new = @(Find-Prs @($id) @() | Where-Object { "$($_.number)@$($_.head)" -notin $seen -and $_.title -match '^(fix|feat)(\(|:)' })
        if ("$($e.note)" -like "runs:*") { $new = @($new | Where-Object { $_.title -match '(?i)black|dark|missing|wrong|crash|hang|stall|freez' }) }
        if (-not $new) { continue }
        $pick = $new[0]
        $e.titlePrs = (@($seen) + "$($pick.number)@$($pick.head)") -join ' '
        $e.prTest = "$($pick.number)"; Save-State
        Event "TITLE PR $name ($id): #$($pick.number) $($pick.title) names this game; queued as a PR test"
    }
}

# A PR library built from newer sources than the game's own libraries can need exports or libraries those lack, and the
# game then does not start (Divinity with #16025, 11-05: 0xC0000135, taken for progress). Then every library the game
# has is taken from the PR build, so the PR is judged on a game that ran.
function Expand-SwapSet([string]$gameDir, $b) {
    # 11-12: the first kept log of such a run (RuneScape Dragonwilds with #18176) showed why it nearly always failed:
    # libSceVideoOut.prx, still the game's own, imported names the PR's libSceAgcDriver no longer had (GetLastError 127).
    # pr_swap_set.py compares import and export tables and names the game libraries that must also come from the PR
    # build; they are built and added until the set agrees (a few libraries instead of all ~400).
    $prLibs = Join-Path $PrBuild "core\libs\libs"
    foreach ($round in 1..4) {
        $n = Native { python -I (Join-Path $Tools "assistant\pr_swap_set.py") $gameDir $prLibs @($b.built.Keys) }
        $extra = @(try { ($n.out -join "`n") | ConvertFrom-Json } catch { @() }) | Where-Object { $_ -and -not $b.built.ContainsKey($_) }
        if ($n.code -ne 0 -or -not $extra) { break }
        Log "  $(Split-Path $gameDir -Leaf): $($extra -join ', ') must also come from the PR build (their imports and exports do not match the swapped libraries)"
        # 11-12: one ninja call for all of them. One call per library cost about 5 s of ninja start-up each, about
        # 35 minutes for the ~400 libraries a libc or libkernel change pulls in (Spider-Man 2 with #7395).
        $targets = @($extra | ForEach-Object { "core/libs/libs/$_.prx" })
        $r = Native { & (Join-Path $Settings.mingwBin "ninja.exe") -j 8 -C $PrBuild @targets }
        if ($r.code -ne 0) { Log "  building the extra libraries failed; the all-libraries fallback will decide"; break }
        foreach ($lib in $extra) { $b.built[$lib] = Join-Path $prLibs "$lib.prx" }
    }
}

function Test-PrStack([string]$gameDir, $stack, $b, [string]$label) {
    Use-Pc "pipeline PR test $label on $(Split-Path $gameDir -Leaf)" $script:PcPriority { Test-PrStackUnlocked $gameDir $stack $b $label } $PcWait
}
function Test-PrStackUnlocked([string]$gameDir, $stack, $b, [string]$label) {
    Expand-SwapSet $gameDir $b
    $t = Test-WithLibraries $gameDir $b.built $label
    if ($t.started) { return $t }
    $dirs = @((Native { git -C $PrSource ls-tree --name-only "${Base}:core/libs/prx" }).out | ForEach-Object { Split-Path $_ -Leaf })
    $all = @(Get-ChildItem -LiteralPath (Join-Path $gameDir "libs") -Filter *.prx -File | ForEach-Object { $_.BaseName } | Where-Object { $_ -in $dirs })
    Log "  $(Split-Path $gameDir -Leaf) did not start with $(@($b.built.Keys) -join ', ') swapped in; trying again with all $($all.Count) of its libraries from the PR build"
    $b2 = Build-PrLibraries $stack $Base $all
    if (-not $b2.ok) { $t.result += " (all libraries from the PR build: $($b2.reason))"; return $t }
    $t2 = Test-WithLibraries $gameDir $b2.built "$label-all"
    $t2.result += " (all $($all.Count) libraries from the PR build)"
    $t2
}

# 11-05 Blu: "prtest <game> = #N [#M]" tests those PRs on that game now (Spider-Man Remastered with #14296, the
# shader fix two testers had confirmed and nobody had tried on the game it was written for). The result, a picture of
# the run and its first stop, goes to _reports\pr-tests.md so the PR thread can be answered with it.
function Test-NamedPrs($e, [string[]]$nums) {
    $stack = foreach ($n in $nums) {
        $v = Native { gh pr view $n --repo $Repo --json number,title,files,headRefOid,state,mergeCommit }
        if ($v.code -ne 0) { return "gh pr view #$n failed" }
        $info = ($v.out -join "`n") | ConvertFrom-Json
        # 11-05 "prtest The Exit 8 = 14475 11346": #14475 was merged and already in the build, and the whole test was
        # dropped. A PR already in the AnyPS5 the game is built from is left out; the others are still tested.
        if ($info.state -eq "MERGED" -and $info.mergeCommit -and (Native { git -C $PrSource merge-base --is-ancestor $info.mergeCommit.oid $Base }).code -eq 0) { Log "  #$n is merged and already in $Base; testing the others"; continue }
        if ($info.state -ne "OPEN") { return "#$n is $($info.state.ToLower()), not open" }
        $libs = @($info.files.path | ForEach-Object { if ($_ -match '^core/libs/prx/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
        if ($info.files.path | Where-Object { $_ -like "core/shader/*" }) { $libs = @($libs) + "libSceAgcDriver" | Select-Object -Unique }
        [pscustomobject]@{ number = [int]$n; title = $info.title; head = $info.headRefOid.Substring(0, 10); libraries = @(Library-Variants $libs) }
    }
    if (-not @($stack).Count) { return "every PR named ($($nums -join ', ')) is already in $Base; nothing to test" }
    $label = "pr-" + ($nums -join "-")
    $b = Build-PrLibraries @($stack)
    $what = ($stack | ForEach-Object { "#$($_.number) $($_.head)" }) -join " + "
    $result = if ($b.ok) { (Test-PrStack $e.gameDir @($stack) $b $label).result } else { "not run: $($b.reason)" }
    $line = "- $(Get-Date -Format 'MM-dd HH:mm') $(Split-Path $e.gameDir -Leaf): $what onto $Base$(if ($b.ok) { " = $($b.commit)" }) (libraries: $(@($stack.libraries | Select-Object -Unique) -join ', ')): $result"
    Add-Content -LiteralPath (Join-Path $Reports "pr-tests.md") -Value $line -Encoding utf8
    "$what onto $Base -> $result"
}

# For a failed game: find open PRs for what stopped it (Find-Leads: a missing function, an AnyPS5 message, the library
# a crash happened in, or a warning of a game without a picture), test the game with them, and follow whatever stops
# it next the same way, up to three PRs deep.
function Triage-Game([string]$name, $e) {
    $chain = @()
    if (-not $e.failure -or -not $e.failure.kind) { $e.chain = $chain; return }
    # A PR is kept only when it gets the game further (past this failure); a candidate that only mentions it is
    # dropped and the next one tried. At most three candidates per failure and six launches in all.
    # A rerun (daily) skips PR heads an earlier run launched the game with, so only new PRs or new pushes cost a
    # launch; a PR that failed to build is tried again.
    $old = @($e.chain); $tried = @($old | Where-Object { "$($_.merged)" -like "* onto *" -and "$($_.result)" -notlike "*did not start*" } | ForEach-Object { "$($_.merged)" })
    $stack = @(); $failure = $e.failure; $launches = 0
    $titleId = try { "$((Get-Content -LiteralPath (Join-Path $e.gameDir "game.json") -Raw | ConvertFrom-Json).titleId)" } catch { "" }
    # 11-04 Ghost Song stopped 35 s into the FPS optimizer's test copy, never in its own folder (Blu's saves skip the
    # online start). Its PR test ran in the game folder, never reached the stop and was taken as "runs". A stop found
    # in a longer FPS run is tested where it showed up.
    $testDir = $e.gameDir; $where = ""
    $fpsCopy = Join-Path $Games ("_test\_fps\" + (Split-Path $e.gameDir -Leaf))
    if ("$($e.note)" -like "stops later*" -and (Test-Path -LiteralPath (Join-Path $fpsCopy "game.json"))) { $testDir = $fpsCopy; $where = " (in the FPS test copy, where it stopped)" }
    while ($failure -and $launches -lt 6) {
        $label = Failure-Label $failure; $key = Failure-Key $failure
        $leads = Find-Leads $failure; $libs = $leads.libraries
        if (-not $leads.queries -and -not $titleId) { $chain += [pscustomobject]@{ function = $label; kind = $failure.kind; libraries = ""; prs = ""; merged = ""; result = "no lead: the log names no function, message or library to search PRs for" }; break }
        # 11-05 Blu: every failed game must be checked against what is open upstream. A PR that names the game's title
        # ID (#16025 named PPSA06866, Divinity's stop) is a lead even when it changes another file; one found both ways
        # goes first.
        $byLead = @(Find-Prs $leads.queries $libs $leads.files)
        $byTitle = @(if ($titleId) { Find-Prs @($titleId) @() })
        $both = @($byLead | Where-Object { $_.number -in @($byTitle | ForEach-Object { $_.number }) })
        $ordered = @($both) + @($byLead | Where-Object { $_.number -notin @($both | ForEach-Object { $_.number }) }) + @($byTitle | Where-Object { $_.number -notin @($byLead | ForEach-Object { $_.number }) })
        $candidates = @($ordered | Where-Object { $c = $_; $c.number -notin @($stack | ForEach-Object { $_.number }) -and -not ($tried | Where-Object { $_ -like "*#$($c.number) $($c.head)*" }) } | Select-Object -First 4)
        if (-not $candidates) { $chain += [pscustomobject]@{ function = $label; kind = $failure.kind; libraries = ($libs -join ', '); prs = ""; merged = ""; result = $(if ($tried) { "no new open PR since the last check" } else { "no open PR found for it" }) }; break }
        $found = (($candidates | ForEach-Object { "#" + $_.number }) -join ' ')
        $next = $null; $progress = $false
        # PRs for the functions the game likely needs next (Upcoming-Prs) go along with each candidate. When they do
        # not merge, or the game then crashes or shows nothing, the candidate is tried again without them.
        $upcoming = @(Upcoming-Prs $e.gameDir $label | Where-Object { $_.number -notin @($stack | ForEach-Object { $_.number }) -and $_.number -notin @($candidates | ForEach-Object { $_.number }) })
        foreach ($pr in $candidates) {
            $trial = @($stack) + $pr
            $extra = $upcoming
            $show = { param($prs) ($prs | ForEach-Object { "#$($_.number) $($_.head)" + $(if ($_.forFunction) { " (upcoming $($_.forFunction))" } else { "" }) }) -join " + " }
            Log "${name}: testing $label with $((@($trial) + $extra | ForEach-Object { '#' + $_.number }) -join ' + ')"
            $row = [pscustomobject]@{ function = $label; kind = $failure.kind; libraries = ($libs -join ', '); prs = $found; merged = (& $show (@($trial) + $extra)); result = "" }
            $b = Build-PrLibraries (@($trial) + $extra)
            if (-not $b.ok -and $extra) { $extra = @(); $row.merged = & $show $trial; $b = Build-PrLibraries $trial }
            if (-not $b.ok) { $row.result = $b.reason; $chain += $row; continue }
            $row.merged += " onto $Base = $($b.commit)"
            $row.merged += $where
            $t = Test-PrStack $testDir (@($trial) + $extra) $b ("pr-" + ((@($trial) + $extra | ForEach-Object { $_.number }) -join "-"))
            $launches++
            if ($extra -and $t.result -notlike "runs:*" -and $t.failure.kind -in "crash", "no frames" -and $launches -lt 6) {
                $withThem = "$($t.failure.kind)"
                $b2 = Build-PrLibraries $trial
                if ($b2.ok) {
                    $t = Test-PrStack $testDir $trial $b2 ("pr-" + (($trial | ForEach-Object { $_.number }) -join "-"))
                    $launches++
                    $extra = @(); $row.merged = (& $show $trial) + " onto $Base = $($b2.commit) (upcoming PRs left out: '$withThem' with them)$where"
                }
            }
            $row.result = $t.result
            $chain += $row
            if (-not $t.started) { Event "PR TEST DID NOT START ${name}: #$($pr.number) $($pr.head): $($t.result); tried again on the next triage"; continue }
            if ($t.result -like "runs:*") { $stack = @($trial) + $extra; $progress = $true; break }
            if ((Failure-Key $t.failure) -eq $key) { continue }   # this PR did not help
            $stack = @($trial) + $extra; $progress = $true; $next = $t.failure
            break
        }
        if (-not $progress) { break }
        $failure = $next
    }
    if ($launches -eq 0 -and $old) { $chain = @($old | Where-Object { $_.result -notlike "no *" }) + $chain }
    $e.chain = $chain
}

# A library and its .native twin build from the same sources (libSceNgs2 and libSceNgs2.native, 11-04): a fix that
# names one is built and swapped in as both, or a game that loads the other never runs the new code.
function Library-Variants([string[]]$libs) {
    $dirs = @((Native { git -C $PrSource ls-tree --name-only "HEAD:core/libs/prx" }).out | ForEach-Object { Split-Path $_ -Leaf })
    @(foreach ($l in $libs) { $l; $twin = if ($l -like "*.native") { $l -replace '\.native$', '' } else { "$l.native" }; if ($twin -in $dirs) { $twin } }) | Select-Object -Unique
}

# Blu 11-03: when no PR gets a failed game further, Claude Code (Sonnet, claude_bridge.py fix: at most 2 a day, once a
# week per game, Settings can turn it off) writes a general fix in the PR worktree on a local branch claude-fix/...
# (nothing pushed), the pipeline builds the libraries it names and tests the game with them, so a game gets every
# try before the next one starts. A fix that gets it further is kept on its branch and named in the report for Blu.
$ClaudeFixRuns = Join-Path $Tools "repair-runs"
# -Mode raid (Blu 11-03): a game every normal try failed on gets the whole team: the boss plans up to 3 parts, Sonnet
# members do them in turn on one claude-raid/ branch, each reviewed; then the same build, test and rewards (15 points
# each for "further", 30 for "runs", 5 for the boss). At most one raid a day, once a week per game, and only while
# today's Claude spend is under $0.50, since a raid costs about a day's limit.
function Uncapped-Today {
    $o = try { Get-Content -LiteralPath (Join-Path $Tools "repair-runs\uncap-override.json") -Raw | ConvertFrom-Json } catch { $null }
    [bool]($o -and $o.until -and (Get-Date -Format "yyyy-MM-ddTHH:mm") -lt "$($o.until)")
}
function Raid-Due($e) {
    $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
    if ($app -and $app.claudeRaid -eq $false) { return $false }
    $rows = @($e.chain)
    if ($rows.Count -lt 5 -or -not ($rows | Where-Object { $_.prs -eq "Claude fix" -and "$($_.result)" -match 'no Claude fix|did not help' })) { return $false }
    if ($e.raidAt -and ((Get-Date) - [datetime]$e.raidAt).TotalDays -lt 7) { return $false }
    if (Uncapped-Today) { return $true }
    if (@($State.Keys | Where-Object { "$((Get-Entry $_).raidAt)".StartsWith((Get-Date -Format "yyyy-MM-dd")) })) { return $false }
    $spent = [double]("$((Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") spent }).out | Select-Object -Last 1)" -replace '[^\d.]', '')
    $spent -lt 0.5
}
function Claude-Fix([string]$name, $e, [string]$Mode = "fix") {
    $last = @($e.chain) | Select-Object -Last 1
    if (-not $e.failure -or -not $e.failure.kind -or ($last -and "$($last.result)" -like "runs:*")) { return }
    # -Mode exp (Blu 11-04: "I want the AI to create the code the game is missing"): right after the Fixer answered
    # "none" because the real PS5 behaviour is unknown, the Experimenter writes its best implementation on a claude-exp/
    # branch; if the game gets further it is kept in that game's libs (the original kept beside it), never sent upstream.
    $fixerSaid = "$($e.fixerNone)"
    if ($Mode -eq "exp") {
        $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
        if (-not $fixerSaid -or ($app -and $app.claudeExperiment -eq $false)) { return }
        if ($e.expAt -and ((Get-Date) - [datetime]$e.expAt).TotalDays -lt 7) { return }
        $e.expAt = (Get-Date).ToString("s"); Save-State
    }
    elseif ($Mode -eq "raid") { if (-not (Raid-Due $e)) { return }; $e.raidAt = (Get-Date).ToString("s"); Save-State; Event "RAID on $(Split-Path $e.gameDir -Leaf): the whole AI team takes it on" }
    else {
        if ($e.claudeFixAt -and ((Get-Date) - [datetime]$e.claudeFixAt).TotalDays -lt 7) { return }
        $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
        if ($app -and $app.claudeFix -eq $false) { return }
        $e.claudeFixAt = (Get-Date).ToString("s"); Save-State
    }
    $r = Native { git -C $PrSource checkout -q --detach $Base }
    if ($r.code -ne 0) { Log "${name}: Claude fix skipped: checking out $Base failed"; return }
    $branch = "claude-$Mode/" + (($name -replace '[^A-Za-z0-9]+', '-').Trim('-')) + "-" + (Get-Date -Format "MMdd-HHmm")
    $r = Native { git -C $PrSource checkout -q -b $branch }
    if ($r.code -ne 0) { Log "${name}: Claude fix skipped: making branch $branch failed"; return }
    # Evidence before code (Blu 11-04: the next code must be the next function, not a guess): an experiment or a raid
    # first runs the game once with APS5_TRACE_CALLS, so the crash report lists the last system calls before the stop,
    # and the prompt carries the game's own list of functions it will call next that AnyPS5 still lacks.
    $nextMissing = ""
    if ($Mode -in "exp", "raid") {
        Log "${name}: a traced run first, for the $Mode (the last system calls before the stop)"
        $traced = Test-Game $e.gameDir "trace" @{ APS5_TRACE_CALLS = "1" }
        Log "${name}: traced run: $traced"
        $predicted = Join-Path $Games ("_reports\predicted\" + [IO.Path]::GetFileName("$($e.gameDir)") + ".md")
        if (Test-Path -LiteralPath $predicted) { $nextMissing = (@(Get-Content -LiteralPath $predicted | Where-Object { $_ -match '\S' }) | Select-Object -First 30) -join "`n" }
    }
    $gameLog = Join-Path $e.gameDir "anyps5.log"
    $excerpt = if (Test-Path -LiteralPath $gameLog) {
        $lines = @(Get-Content -LiteralPath $gameLog -Tail 4000)
        # The crash report under a FATAL line (rip, registers, frame pointer chain) is the best lead there is; 11-03
        # the Fixer got only the FATAL line and said the log named no address.
        $fatal = [array]::FindIndex([string[]]$lines, [Predicate[string]]{ param($l) $l -match '^FATAL' })
        $report = if ($fatal -ge 0) { @($lines[$fatal..([Math]::Min($lines.Count - 1, $fatal + 60))] | Where-Object { $_ -notmatch '^\s+\[\[' }) } else { @() }
        (@($lines | Where-Object { $_ -match 'FATAL|error|warn|not implemented|unsupported|unhandled|exception' } | Select-Object -Last 30) + "--- crash report ---" + $report + "--- last lines ---" + @($lines | Select-Object -Last 30)) -join "`n"
    } else { "(no anyps5.log)" }
    $tries = (@($e.chain) | ForEach-Object { "- $($_.function): $($_.merged) -> $($_.result)" }) -join "`n"
    # Blu 11-05: KytyPS5 runs games further; how it handles the stop (docs/dev/REVERSE_ENGINEERING.md source 3) goes
    # into the brief as a reference, never to copy.
    $kytyTerm = if ($e.failure.function) { $e.failure.function } elseif ("$($e.failure.message)" -match '\b([A-Z][A-Z0-9_]{4,})\b') { $Matches[1] } else { "" }
    $kyty = if ($kytyTerm) { ((Native { python -I (Join-Path $Tools "assistant\kyty_ref.py") --update $kytyTerm }).out | Select-Object -First 60) -join "`n" } else { "" }
    $stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
    $prompt = Join-Path $ClaudeFixRuns "$stamp-$Mode-prompt.md"; $report = Join-Path $ClaudeFixRuns "$stamp-$Mode-report.md"
    New-Item -ItemType Directory -Force -Path $ClaudeFixRuns | Out-Null
    @"
You fix AnyPS5 (D:\AnyPS5\wt-pr, a git worktree of the AnyPS5 repo, checked out on local branch $branch made from $Base) so the PS5 game "$name" gets past what stops it. AnyPS5 relinks PS5 executables and reimplements the system libraries (core/libs/prx/<library>); read AGENTS.md and docs/dev/CONVENTIONS.md first and follow them: no comments in code, never return a made-up success (call NotImplemented_nid_no_patch for what you cannot implement), implement for every input, never special-case this game's values or paths.

What stops it: kind=$($e.failure.kind) function=$($e.failure.function) where=$($e.failure.where) message=$($e.failure.message)
PR tests already tried:
$tries

The game's log (anyps5.log, errors and its end):
$excerpt
$(if ($nextMissing) { "`nFunctions this game imports that AnyPS5 still lacks or stubs, the ones it will call next (from its import table):`n$nextMissing`n" })
$(if ($kyty -match 'found in KytyPS5') { "`nHow KytyPS5 (C:\AnyPS5-work\refs\KytyPS5, GPL-2.0 like AnyPS5; a reference source in docs/dev/REVERSE_ENGINEERING.md) handles it. Learn the behaviour from it, write AnyPS5 code in AnyPS5's style, and name KytyPS5 as a source in the summary:`n$kyty`n" })
A crash inside the game's own code (rip in <game>.exe+offset) usually means an AnyPS5 function returned something the game did not expect (a null, a failure, a wrong size). The local AI's game-code tools show what the game was doing there: run python -I assistant/tool_cli.py with game_crash_code '{"game": "$name"}', decompile_game_code '{"game": "$name", "where": "<offset>"}', stack_callers, how_game_calls or game_strings (python -I assistant/tool_cli.py alone lists them). Work back from the crash to the AnyPS5 call that set it up.

$(if ($Mode -eq "exp") { "EXPERIMENT (you are the Experimenter, not the Fixer): the Fixer already looked and said: $fixerSaid. The real PS5 behaviour is not documented, so write your best real implementation of what the PS5 most likely does (from the function name and arguments, its librarys neighbours, docs/dev and what the game does with the result), working for every input. Start the commit message with experimental: and write the guess and your reasoning in docs/dev/debt/<library>.md. This is tested on the game and kept only in Blus copy if it helps; it never goes upstream. Never special-case this game, never fake a result, never weaken a check.`n`n" })Steps: find the cause in the code (Grep/Read; read narrowly), write the smallest general fix, commit it on $branch (git -C D:\AnyPS5\wt-pr commit; never push, never reset or clean, never touch other branches). Do not build: the pipeline builds and tests it. If you cannot find a cause you can fix with confidence, commit nothing.
Your last line must be one JSON object: {"libraries": ["libSceName", ...], "summary": "what you changed and why"} naming each core/libs/prx library your commit changes, or {"none": "why not"}.
"@ | Set-Content -LiteralPath $prompt -Encoding utf8
    Log "${name}: Claude Code is writing a $(if ($Mode -eq 'raid') { 'team raid' } else { 'fix' }) on $branch"
    $c = Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") $Mode $prompt $report }
    $text = if (Test-Path -LiteralPath $report) { Get-Content -LiteralPath $report -Raw } else { "$($c.out -join ' ')" }
    $answer = try { ("$text".Trim() -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json } catch { $null }
    # Edits Claude left uncommitted would stop the next PR checkout; they are kept in a stash, not thrown away.
    if ((Native { git -C $PrSource status --porcelain }).out) { Native { git -C $PrSource stash push -u -q -m "claude-fix leftovers $branch" } | Out-Null }
    $commit = (Native { git -C $PrSource rev-parse --short=10 HEAD }).out | Select-Object -First 1
    $baseCommit = (Native { git -C $PrSource rev-parse --short=10 $Base }).out | Select-Object -First 1
    $row = [pscustomobject]@{ function = (Failure-Label $e.failure); kind = $e.failure.kind; libraries = ""; prs = "Claude fix"; merged = "Claude fix $branch onto $Base = $commit"; result = "" }
    # Claude did not run (a daily cap, the spending limit, signed out): no try was spent, so it comes back tomorrow, not
    # in a week (11-03 Divinity lost its turn to the fix cap).
    if ("$text" -match 'cap reached|is off:|not signed in|not installed') {
        $retry = (Get-Date).AddDays(-6).ToString("s"); if ($Mode -eq "exp") { $e.expAt = $retry } elseif ($Mode -eq "raid") { $e.raidAt = $retry } else { $e.claudeFixAt = $retry }
        $e.chain = @($e.chain) + [pscustomobject]@{ function = (Failure-Label $e.failure); kind = $e.failure.kind; libraries = ""; prs = "Claude fix"; merged = ""; result = "Claude fix waits until tomorrow: $("$text".Trim().Substring(0, [Math]::Min(120, "$text".Trim().Length)))" }
        Native { git -C $PrSource checkout -q --detach $Base } | Out-Null; return
    }
    $struck = "$text" -match 'Boss verdict: STRIKE'
    if (-not $answer -or $answer.none -or -not $answer.libraries -or $commit -eq $baseCommit -or $struck) {
        if ($Mode -eq "fix" -and $answer.none) { $e.fixerNone = "$($answer.none)" }
        $row.result = "no Claude fix: " + $(if ($struck) { "the boss did not pass it" } elseif ($answer.none) { "$($answer.none)" } elseif ($commit -eq $baseCommit) { "it committed nothing" } else { ("$text".Trim() -split "`n" | Select-Object -Last 1) })
        $e.chain = @($e.chain) + $row; Native { git -C $PrSource checkout -q --detach $Base } | Out-Null; return
    }
    $row.libraries = (@($answer.libraries) -join ', ')
    if ($env:PATH -notlike "*$($Settings.mingwBin)*") { $env:PATH = "$($Settings.mingwBin);$env:PATH" }
    $built = @{}
    foreach ($lib in @(Library-Variants @($answer.libraries | Where-Object { $_ -match '^[A-Za-z0-9_.]+$' }))) {
        $b = Native { & (Join-Path $Settings.mingwBin "ninja.exe") -j 8 -C $PrBuild "core/libs/libs/$lib.prx" }
        if ($b.code -ne 0) { $row.result = "Claude fix did not build ($lib): $(($b.out | Select-Object -Last 3) -join ' ')"; $e.chain = @($e.chain) + $row; return }
        $built[$lib] = Join-Path $PrBuild "core\libs\libs\$lib.prx"
    }
    $t = Test-WithLibraries $e.gameDir $built ("claude-fix")
    $helped = $t.result -like "runs:*" -or (Failure-Key $t.failure) -ne (Failure-Key $e.failure)
    # An experiment that helped stays in Blu's game: its libraries go into the game's libs (each original kept as
    # <lib>.prx.before-exp-<date>); a rebuild of the game on a newer AnyPS5 replaces them, the claude-exp/ branch stays.
    if ($helped -and $Mode -eq "exp") {
        foreach ($lib in $built.Keys) {
            $dst = Join-Path $e.gameDir "libs\$lib.prx"
            if (Test-Path -LiteralPath $dst) { Copy-Retry $dst "$dst.before-exp-$(Get-Date -Format 'MMdd')" }
            Copy-Retry $built[$lib] $dst
        }
        $e.note = "experimental: $(if ($t.result -like 'runs:*') { $t.result } else { "got further with $branch; next stop: $(Failure-Label $t.failure)" }) (AI-written $(@($built.Keys) -join ', ') kept in this game; not upstream)"
        if ($t.result -notlike "runs:*") { $e.failure = $t.failure; $e.triage = "todo" }
        Save-State; Write-Reports
    }
    $row.result = "$($t.result)" + $(if ($helped) { " (got further: branch $branch kept for Blu; $($answer.summary))" } else { " (did not help)" })
    # A game that has been through many tries already (4 or more rows of PR tests and fixes) is a hard one: +3 more.
    $hardBonus = if (@($e.chain).Count -ge 5) { 3 } else { 0 }
    $pts = $hardBonus + $(if ($t.result -like "runs:*") { 10 } else { 5 })
    if ($Mode -eq "raid") { $pts = $(if ($t.result -like "runs:*") { 30 } else { 15 }) }
    if ($helped) { Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") reward $(if ($Mode -eq "exp") { "exp" } else { "fix" }) $pts "$(Split-Path $e.gameDir -Leaf) $(if ($t.result -like 'runs:*') { 'runs' } else { 'got further' }) with $branch$(if ($hardBonus) { ' (a long-stuck game: +3)' })" } | Out-Null }
    $e.chain = @($e.chain) + $row
    if ($helped -and $Mode -eq "raid") { Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") reward boss 5 "led the raid that got $(Split-Path $e.gameDir -Leaf) further" } | Out-Null }
    Event "CLAUDE $($Mode.ToUpper()) $(Split-Path $e.gameDir -Leaf): $(if ($helped) { 'got further' } else { 'did not help' }) - $branch - $("$($answer.summary)".Substring(0, [Math]::Min(160, "$($answer.summary)".Length)))"
}

# Blu 11-03: the team also works ahead instead of only waiting for a game to fail. Once a day the Builder (Sonnet,
# claude_bridge.py build, reviewed by the boss) implements the most useful missing function no open PR covers
# (build_targets.py: functions that stop games first, then the ones most games import), on a local claude-build/
# branch; the pipeline builds it and tests the games that function stops. Results: _tools\builder-tries.json.
$BuilderTries = Join-Path $Tools "builder-tries.json"
function Builder-Due {
    $app = try { Get-Content -LiteralPath (Join-Path $Tools "app-settings.json") -Raw | ConvertFrom-Json } catch { $null }
    if ($app -and $app.claudeBuild -eq $false) { return $false }
    if (Uncapped-Today) { return $true }
    $tries = @(try { Get-Content -LiteralPath $BuilderTries -Raw | ConvertFrom-Json | ForEach-Object { $_ } } catch { })
    -not ($tries | Where-Object { "$($_.at)".StartsWith((Get-Date -Format "yyyy-MM-dd")) })
}
function Build-Next {
    # ConvertFrom-Json hands back a JSON array as one object in Windows PowerShell; ForEach-Object unrolls it (11-03 the
    # whole list was searched as one target and every function was skipped).
    $targets = @(try { (Native { python -I (Join-Path $Tools "build_targets.py") }).out -join "" | ConvertFrom-Json | ForEach-Object { $_ } } catch { })
    $tries = @(try { Get-Content -LiteralPath $BuilderTries -Raw | ConvertFrom-Json | ForEach-Object { $_ } } catch { })
    $save = { param($row) $script:BuilderList = @($tries) + $row; ConvertTo-Json -InputObject @($script:BuilderList) -Depth 4 | Set-Content -LiteralPath $BuilderTries -Encoding utf8 }
    $target = $null
    foreach ($t in $targets) {
        $libs = @(Find-Libraries $t.function)
        $open = @(Find-Prs @($t.function) $libs)
        if ($open) { Log "builder: $($t.function) has open PR(s) $(($open | ForEach-Object { '#' + $_.number }) -join ' '); the PR tests cover it"; continue }
        $target = $t; break
    }
    if (-not $target) { & $save ([pscustomobject]@{ at = (Get-Date).ToString("s"); function = ""; result = "nothing to build: every target has an open PR or was tried this week" }); return }
    $libs = @(Find-Libraries $target.function)
    # Blu 11-03: one job does the whole family (up to 4 missing functions of the same library, no open PR for any), for
    # about the cost of one; the games any of them stop are all tested.
    $family = @($target)
    if ($libs) {
        foreach ($t in $targets) {
            if ($family.Count -ge 4) { break }
            if ($t.function -eq $target.function) { continue }
            if (-not (@(Find-Libraries $t.function) | Where-Object { $_ -in $libs })) { continue }
            if (@(Find-Prs @($t.function) $libs)) { continue }
            $family += $t
        }
    }
    $target = [pscustomobject]@{ function = (($family | ForEach-Object { $_.function }) -join ", "); why = (($family | ForEach-Object { "$($_.function): $($_.why)" }) -join "; ")
                                 games = @($family | ForEach-Object { $_.games } | Where-Object { $_ } | Select-Object -Unique) }
    $r = Native { git -C $PrSource checkout -q --detach $Base }
    $branch = "claude-build/$($family[0].function)$(if ($family.Count -gt 1) { "-and-$($family.Count - 1)-more" })-$(Get-Date -Format 'MMdd-HHmm')"
    if ($r.code -eq 0) { $r = Native { git -C $PrSource checkout -q -b $branch } }
    if ($r.code -ne 0) { & $save ([pscustomobject]@{ at = (Get-Date).ToString("s"); function = $target.function; result = "checkout failed: $($r.out -join ' ')" }); Log "builder: checkout failed: $($r.out -join ' ')"; return }
    $stamp = Get-Date -Format "yyyy-MM-dd-HHmm"
    $prompt = Join-Path $ClaudeFixRuns "$stamp-build-prompt.md"; $report = Join-Path $ClaudeFixRuns "$stamp-build-report.md"
    $gameNames = (@($target.games) | ForEach-Object { Split-Path $_ -Leaf }) -join ", "
    @"
You implement $(if ($family.Count -gt 1) { "these $($family.Count) functions of one library, together" } else { "this function" }): $($target.function), in AnyPS5 (D:\AnyPS5\wt-pr, a git worktree of the AnyPS5 repo, on local branch $branch made from $Base). Why: $($target.why)$(if ($gameNames) { " ($gameNames)" }). Its library: $(if ($libs) { $libs -join ', ' } else { 'find it under core/libs/prx' }).
Read AGENTS.md and docs/dev/CONVENTIONS.md first and follow them: no comments in code, never return a made-up success, implement it for every input, never special-case one game's values or paths; behaviour you cannot establish throws (NotImplemented_nid_no_patch) and goes in docs/dev/debt/<library>.md. Study how the library's neighbouring functions are written and the PS5 behaviour they model; add or extend its unit test the way the repo's tests for that library do.
Commit on $branch (git -C D:\AnyPS5\wt-pr commit; never push, never reset or clean, never touch other branches). Do not build: the pipeline builds and tests it.
Your last line must be one JSON object: {"libraries": ["libSceName", ...], "summary": "what you implemented and from what"} or {"none": "why not"}.
"@ | Set-Content -LiteralPath $prompt -Encoding utf8
    Log "builder: Claude Code implements $($target.function) on $branch ($($target.why))"
    $c = Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") build $prompt $report }
    $text = if (Test-Path -LiteralPath $report) { Get-Content -LiteralPath $report -Raw } else { "$($c.out -join ' ')" }
    $answer = try { ("$text".Trim() -split "`n" | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1) | ConvertFrom-Json } catch { $null }
    if ((Native { git -C $PrSource status --porcelain }).out) { Native { git -C $PrSource stash push -u -q -m "claude-build leftovers $branch" } | Out-Null }
    # Claude could not finish (a cap, the spending limit, a budget cut mid-job): nothing was tried, so the functions are
    # not marked as tried this week; the Builder takes them again on its next day.
    if ("$text" -match 'cap reached|spend cap|is off:|not signed in|gave no answer|error_max_budget|max_budget') {
        if ((Native { git -C $PrSource status --porcelain }).out) { Native { git -C $PrSource stash push -u -q -m "claude-build cut short $branch" } | Out-Null }
        Native { git -C $PrSource checkout -q --detach $Base } | Out-Null
        & $save ([pscustomobject]@{ at = (Get-Date).ToString("s"); function = ""; result = "cut short, tries again next day: $("$text".Trim().Substring(0, [Math]::Min(150, "$text".Trim().Length)))" })
        Event "BUILDER $($target.function): Claude could not finish today; it tries again on its next day"; return
    }
    $boss = if ("$text" -match 'Boss verdict: (\w+)') { $Matches[1] } else { "not reviewed" }
    $row = [pscustomobject]@{ at = (Get-Date).ToString("s"); function = $target.function; branch = $branch; boss = $boss; summary = "$($answer.summary)"; result = "" }
    $commit = (Native { git -C $PrSource rev-parse --short=10 HEAD }).out | Select-Object -First 1
    $baseCommit = (Native { git -C $PrSource rev-parse --short=10 $Base }).out | Select-Object -First 1
    if (-not $answer -or $answer.none -or -not $answer.libraries -or $commit -eq $baseCommit -or $boss -eq "STRIKE") {
        $row.result = "not built: " + $(if ($answer.none) { "$($answer.none)" } elseif ($boss -eq "STRIKE") { "the boss did not pass it" } elseif ($commit -eq $baseCommit) { "nothing committed" } else { "no answer" })
        & $save $row; Native { git -C $PrSource checkout -q --detach $Base } | Out-Null
        Event "BUILDER $($target.function): $($row.result)"; return
    }
    if ($env:PATH -notlike "*$($Settings.mingwBin)*") { $env:PATH = "$($Settings.mingwBin);$env:PATH" }
    $built = @{}
    foreach ($lib in @(Library-Variants @($answer.libraries | Where-Object { $_ -match '^[A-Za-z0-9_.]+$' }))) {
        $b = Native { & (Join-Path $Settings.mingwBin "ninja.exe") -j 8 -C $PrBuild "core/libs/libs/$lib.prx" }
        if ($b.code -ne 0) { $row.result = "did not build ($lib): $(($b.out | Select-Object -Last 3) -join ' ')"; & $save $row; Event "BUILDER $($target.function): $($row.result)"; return }
        $built[$lib] = Join-Path $PrBuild "core\libs\libs\$lib.prx"
    }
    $results = @()
    foreach ($g in @($target.games | Select-Object -First 3)) {
        if ((Game-Running) -or (Test-Path -LiteralPath $PauseFlag)) { $results += "$(Split-Path $g -Leaf): not tested (Blu is playing or paused)"; continue }
        $before = Read-Failure $g
        $t = Test-WithLibraries $g $built "claude-build"
        $further = $t.result -like "runs:*" -or (Failure-Key $t.failure) -ne (Failure-Key $before)
        $results += "$(Split-Path $g -Leaf): $(if ($further) { 'got further' } else { 'same stop' }) ($($t.result))"
        if ($further) { Native { python -I (Join-Path $Tools "assistant\claude_bridge.py") reward build $(if ($t.result -like "runs:*") { 10 } else { 5 }) "$(Split-Path $g -Leaf) $(if ($t.result -like 'runs:*') { 'runs' } else { 'got further' }) with $($target.function)" } | Out-Null }
    }
    $row.result = if ($results) { $results -join "; " } else { "built; no game stops on it yet (imported by many), so only the boss's review and the build checked it" }
    & $save $row
    Event "BUILDER $($target.function) on ${branch}: boss $boss; $($row.result)"
}

# Fix prediction (predict_fix.py, 11-03): for each failed game, what most likely gets it past its stop, ranked with
# the evidence: a launch setting the stop message names, a playbook's workaround, a newer AnyPS5 commit already
# merged, an open PR, the game's own graphics mode. Written to _reports\fix-predictions.md. Plain code; Claude (Haiku,
# capped, cached per stop) only when nothing else has evidence. A failure here is only logged.
$FixPredictor = Join-Path $Tools "predict_fix.py"
function Predict-Fixes([string[]]$gameDirs = @()) {
    if (-not (Test-Path -LiteralPath $FixPredictor)) { return }
    $fixArgs = @("predict", "--ask") + @(foreach ($g in $gameDirs) { "--game"; $g })
    try {
        $n = Native { python -I $FixPredictor @fixArgs }
        foreach ($l in @($n.out | Where-Object { $_ -match ' -> ' })) { Log "  fix prediction: $l" }
        if ($n.code -ne 0) { Log "fix prediction: exit $($n.code)" }
    } catch { Log "error: fix prediction: $($_.Exception.Message)" }
}

# The test copy fixes are tried on: _test\_fix\<game>, built from the game's own dump on the AnyPS5 build the game
# uses (make_test_copy.ps1 -Into), so the try is the only difference; the game folder is only read. Libraries of an
# older build are moved aside, never recycled (every Recycle Bin here deletes at once). Returns "" when it cannot.
function Fix-Copy([string]$gameDir) {
    $leaf = Split-Path $gameDir -Leaf
    $copy = Join-Path (Fix-Root $gameDir) $leaf
    $cfg = Get-Content -LiteralPath (Join-Path $gameDir "game.json") -Raw | ConvertFrom-Json
    $build = if ($cfg.anyps5Build) { $cfg.anyps5Build } else { $Settings.anyps5Build }
    $have = if (Test-Path -LiteralPath (Join-Path $copy "game.json")) { Get-Content -LiteralPath (Join-Path $copy "game.json") -Raw | ConvertFrom-Json } else { $null }
    $exe = if ($have) { Join-Path $copy ("{0}.exe" -f $(if ($have.exeName) { $have.exeName } else { $have.title })) } else { "" }
    if (-not ($have -and $have.anyps5Build -eq $build -and (Test-Path -LiteralPath $exe))) {
        if ($have -and $have.anyps5Build -ne $build -and (Test-Path -LiteralPath (Join-Path $copy "libs"))) {
            $aside = Join-Path $Games ("_test\_set aside\{0} libs {1}" -f $leaf, (Get-Date -Format "MMdd-HHmm"))
            New-Item -ItemType Directory -Force (Split-Path $aside -Parent) | Out-Null
            Move-Item -LiteralPath (Join-Path $copy "libs") -Destination $aside
            Log "  moved the fix copy's libraries of $(Split-Path $have.anyps5Build -Leaf) aside to $aside"
        }
        while (-not (Wait-Quiet "fix copy of $leaf")) { Start-Sleep 30 }
        Log "${leaf}: building its fix test copy on $(Split-Path $build -Leaf)"
        $n = Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "assistant\skills\make_test_copy.ps1") -Name $leaf -Build $build -Into $copy }
        $have = if (Test-Path -LiteralPath (Join-Path $copy "game.json")) { Get-Content -LiteralPath (Join-Path $copy "game.json") -Raw | ConvertFrom-Json } else { $null }
        $exe = if ($have) { Join-Path $copy ("{0}.exe" -f $(if ($have.exeName) { $have.exeName } else { $have.title })) } else { "" }
        if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { Log "${leaf}: the fix test copy did not build: $(($n.out | Select-Object -Last 2) -join ' ')"; return "" }
    }
    # The copy starts with the game's own settings; a try adds its own on top.
    $own = Join-Path $gameDir "game-env.txt"
    if (Test-Path -LiteralPath $own) { Copy-Item -LiteralPath $own -Destination (Join-Path $copy "game-env.txt") -Force }
    else { Set-Content -LiteralPath (Join-Path $copy "game-env.txt") -Value "" -Encoding utf8 }
    $copy
}

# Tests the top untried candidates of a game's fix prediction on its fix copy: a setting as a launch setting, newer
# AnyPS5 or a PR the playbook names as libraries built in the PR worktree and swapped into the copy. Open PRs that a
# gh search found stay with Triage-Game. At most 3 launches; a candidate is tried once per stop and version. A setting
# that gets the copy running or past the stop ("further") goes into the game's game-env.txt (and Play <game>.cmd),
# and the game itself is tested again with it; returns "setting kept" then.
function Try-Fixes([string]$name, $e) {
    if (-not (Test-Path -LiteralPath $FixPredictor)) { return "" }
    Predict-Fixes @($e.gameDir)
    $n = Native { python -I $FixPredictor next --game $e.gameDir --count 3 }
    # Windows PowerShell's ConvertFrom-Json emits a JSON list as one object; going through a variable unrolls it.
    $parsed = try { (@($n.out | Where-Object { $_ -match '^\[' }) -join "") | ConvertFrom-Json } catch { $null }
    $list = @($parsed | Where-Object { $_ -and $_.id })
    if (-not $list) { return "" }
    $copy = Fix-Copy $e.gameDir
    if (-not $copy) { return "" }
    $kept = $null
    foreach ($c in $list) {
        $envs = @{}
        if ($c.env) { foreach ($p in $c.env.PSObject.Properties) { $envs[$p.Name] = "$($p.Value)" } }
        $label = "fix-" + ($c.id -replace '[^\w=.-]', '_')
        Log "${name}: fix try on the test copy: $($c.title)"
        if ($c.kind -eq "setting") { $result = Test-Game $copy $label $envs }
        else {
            $stack = @(foreach ($pr in @($c.prs)) { if ($pr) { [pscustomobject]@{ number = [int]$pr; libraries = @($c.libraries) } } })
            $onto = $Base
            if ($c.kind -eq "main") { Native { git -C $PrSource fetch -q origin main } | Out-Null; $onto = "origin/main" }
            $b = Build-PrLibraries $stack $onto @($c.libraries)
            $result = if ($b.ok) { Expand-SwapSet $copy $b; (Test-WithLibraries $copy $b.built $label).result } else { "not tested: $($b.reason)" }
        }
        $r = Native { python -I $FixPredictor record --game $e.gameDir --copy $copy --id $c.id --version "$($c.version)" --result $result }
        $row = try { @($r.out | Where-Object { $_ -match '^\{' }) | Select-Object -Last 1 | ConvertFrom-Json } catch { $null }
        $verdict = if ($row) { $row.verdict } else { "?" }
        Event "FIX TRY $(Split-Path $e.gameDir -Leaf): $($c.title) -> $verdict ($result)"
        if ($c.kind -eq "setting" -and $verdict -in "fixed", "further" -and (-not $kept -or ($verdict -eq "fixed" -and $kept.verdict -ne "fixed"))) { $kept = [pscustomobject]@{ id = $c.id; verdict = $verdict } }
        if ($verdict -eq "fixed") { break }
    }
    if (-not $kept) { return "" }
    $k = Native { python -I $FixPredictor keep --game $e.gameDir --id $kept.id }
    Event "FIX PROVEN $(Split-Path $e.gameDir -Leaf): $($kept.id -replace '^setting:', '') ($($kept.verdict) on the test copy), kept in its game-env.txt; testing the game itself with it. $(($k.out | Select-Object -First 1))"
    $e.status = "built"; Save-State
    "setting kept"
}

# Takes a newer AnyPS5 from the nightly health build, but only after a run that built, did not bisect a regression
# (a bisect leaves the build folder at another commit) and still has its checkout at the tested commit. The build
# is copied to its own _tools\anyps5-<commit> (older ones stay, for pinned games); then every game is rechecked.
# Its health state path must not be called $statePath: PowerShell names ignore case, so that local hid the script's
# $StatePath and the Save-State below wrote the games state over the health state (10-28; every nightly run after
# that stopped on it and no newer AnyPS5 was taken until 11-03).
function Update-AnyPS5 {
    $healthState = Join-Path $HealthReports "state-latest.json"
    if (-not (Test-Path -LiteralPath $healthState)) { return }
    if (Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='ninja.exe' OR Name='cmake.exe' OR Name='ctest.exe'" | Where-Object { $_.CommandLine -match 'health' }) { return }
    $health = Get-Content -LiteralPath $healthState -Raw | ConvertFrom-Json
    $sha = "$($health.sha)"
    if (-not $sha -or $sha.StartsWith($Base) -or $Base.StartsWith($sha) -or $script:RefusedHealth -eq $sha) { return }
    $report = Get-Content -LiteralPath (Join-Path $HealthReports "latest.md") -Raw -ErrorAction SilentlyContinue
    $head = "$((Native { git -C $HealthSource rev-parse --short=10 HEAD }).out | Select-Object -First 1)"
    # Since 11-04 health.py rebuilds the tested commit after a bisect and then writes "built"; only without it does
    # a bisect mean the build folder holds other commits.
    $rebuilt = "$($health.built)" -eq $sha
    if (-not $report -or $report -notmatch [regex]::Escape($sha) -or $report -match 'build failed|cmake failed|rebuild failed' -or ($report -match 'bisecting' -and -not $rebuilt) -or -not $head.StartsWith($sha)) {
        $script:RefusedHealth = $sha; Log "AnyPS5 $sha from the nightly run not taken: that run failed, bisected, or its build folder has moved on"; return
    }
    $dst = Join-Path $Tools "anyps5-$sha"
    Native { robocopy (Join-Path $HealthBuild "core\relinker") (Join-Path $dst "core\relinker") /XD CMakeFiles /NFL /NDL /NJH /NJS /NP } | Out-Null
    Native { robocopy (Join-Path $HealthBuild "core\libs\libs") (Join-Path $dst "core\libs\libs") *.prx /NFL /NDL /NJH /NJS /NP } | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $dst "core\relinker\relinker.exe")) -or @(Get-ChildItem -LiteralPath (Join-Path $dst "core\libs\libs") -Filter *.prx).Count -lt 300) {
        $script:RefusedHealth = $sha; Log "copying AnyPS5 $sha failed; staying on $Base"; return
    }
    "$sha $(Get-Date -Format 'yyyy-MM-dd') (origin/main, from the nightly health build)" | Set-Content -LiteralPath (Join-Path $dst "COMMIT.txt") -Encoding utf8
    $previous = $Settings.anyps5Build; $previousBase = $Base
    $Settings.anyps5Build = $dst
    $Settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Tools "settings.json") -Encoding utf8
    $script:Base = $sha
    # Blu 11-05: a game that runs is not rechecked when the update changes nothing it uses: only docs, tools, tests or
    # libraries it does not load (its imports\*.needed.json). Shaders, the relinker and the core libraries every game
    # goes through always count, and a game that stops is always rechecked.
    $diff = Native { git -C $HealthSource diff --name-only $previousBase $sha }
    $changed = @(if ($diff.code -eq 0) { $diff.out | Where-Object { $_ } })
    $coreLibs = "libkernel", "libc", "libSceLibcInternal", "libSceAgc", "libSceAgcDriver", "libSceVideoOut", "libSceGnmDriver", "libScePad", "libSceAudioOut"
    # Blu 11-05 ("do both"): working games are rechecked on every third update only (stopped games on every one), and
    # their rechecks run overnight (Recheck-Hours).
    $roundPath = Join-Path $Tools "live\update-round.txt"
    $round = 1 + [int]("$(Get-Content -LiteralPath $roundPath -ErrorAction SilentlyContinue)" -replace '\D', '')
    Set-Content -LiteralPath $roundPath -Value $round -Encoding ascii
    $workingRound = $round % 3 -eq 0
    $count = 0; $skipped = @()
    foreach ($name in @($State.Keys)) {
        $e = Get-Entry $name
        if (-not ($e.gameDir -and $e.status -in "done", "skip", "built")) { continue }
        if (-not $workingRound -and "$($e.note)" -like "runs:*") { $skipped += $name; continue }
        if ($diff.code -eq 0 -and "$($e.note)" -like "runs:*") {
            $used = @(Get-ChildItem -LiteralPath (Join-Path $e.gameDir "imports") -Filter *.needed.json -ErrorAction SilentlyContinue | ForEach-Object { try { Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json } catch { } } | ForEach-Object { "$_" -replace '\.s?prx$', '' } | Select-Object -Unique)
            $relevant = @($changed | Where-Object {
                if ($_ -notlike "core/*" -or $_ -match '/tests?/') { return $false }
                if ($_ -match '^core/libs/prx/([^/]+)/') { $lib = $Matches[1] -replace '\.native$', ''; return $lib -in $coreLibs -or $lib -in $used }
                $true
            })
            if ($used -and -not $relevant) { $skipped += $name; continue }
        }
        $e.recheck = "todo"; $e.previousBuild = $previous; $count++
    }
    Save-State
    Event "ANYPS5 UPDATED $previousBase -> $sha (update $round); rechecking $count games one at a time$(if ($skipped) { "; $($skipped.Count) working games skipped ($(if ($workingRound) { 'the update changes nothing they use' } else { 'working games are rechecked on every third update' }))" })"
    # Every game's predicted list against the new build right away (no relinker runs: the import lists do not change
    # with AnyPS5); each recheck below refreshes its game again after the rebuild.
    Predict-Missing -maxRelinks 0
}

# Rebuilds a game on the current AnyPS5 and tests it. A game that worked before and fails now goes back to the
# build it worked on (pinned in its game.json) so it stays playable; one that failed and now runs is marked FIXED.
function Recheck-Game([string]$name, $e) {
    $cfgPath = Join-Path $e.gameDir "game.json"
    $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
    $pin = if ($cfg.anyps5Build) { $cfg.anyps5Build } else { $e.previousBuild }
    if ($cfg.anyps5Build) { $cfg.PSObject.Properties.Remove("anyps5Build"); $cfg | ConvertTo-Json | Set-Content -LiteralPath $cfgPath -Encoding ascii }
    $before = "$($e.note)"
    # 11-04 Need for Speed Unbound's note said "runs: ... 1 frames, 0.00 FPS" (a test-copy run that never drew), so each
    # recheck called it REGRESSED and pinned it back: a run with 0 or 1 frames never worked.
    # 11-05 Spider-Man Remastered's "runs: ... 684 frames, 0.47 FPS" was a shader-compile slideshow, not a working game.
    $workedBefore = ($before -like "runs:*" -and $before -notmatch '\b[01] frames\b|\b0\.\d+ FPS|FPS: 0\.\d+') -or $e.status -eq "skip"
    $n = Use-Pc "pipeline rebuild of $(Split-Path $e.gameDir -Leaf)" $script:PcPriority { Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $e.gameDir } } $PcWait
    $after = if ($n.code -eq 0 -and ($n.out -match '^Done\.')) { Test-Game $e.gameDir "recheck-$Base" } else { "build failed: " + (($n.out | Select-Object -Last 2) -join ' ') }
    # 11-06 Stray was pinned back as a regression because a newer AnyPS5 stops until a launch setting is chosen
    # ("set APS5_PLAYGO_UNLISTED_CHUNKS=installed, ..."). A game that ran before gets one more test with the first value
    # the stop message names; if it runs, the setting is kept in its game-env.txt.
    if ($after -notlike "runs:*" -and $after -notlike "build failed*" -and $workedBefore) {
        $logFile = Join-Path $e.gameDir "anyps5.log"
        $named = if (Test-Path -LiteralPath $logFile) { Select-String -LiteralPath $logFile -Pattern 'set (APS5_[A-Z0-9_]+)=([A-Za-z0-9_.-]+)' -ErrorAction SilentlyContinue | Select-Object -Last 1 } else { $null }
        $own = Join-Path $e.gameDir "game-env.txt"
        $key = if ($named) { $named.Matches[0].Groups[1].Value } else { "" }
        $value = if ($named) { $named.Matches[0].Groups[2].Value } else { "" }
        $has = $key -and (Test-Path -LiteralPath $own) -and (Select-String -LiteralPath $own -Pattern "^$key=" -Quiet -ErrorAction SilentlyContinue)
        if ($key -and -not $has) {
            Log "${name}: the recheck stopped asking for $key; testing once more with $key=$value"
            $retry = Test-Game $e.gameDir "recheck-$Base-setting" @{ $key = $value }
            if ($retry -like "runs:*") {
                Add-Content -LiteralPath $own -Value "$key=$value" -Encoding utf8
                Event "SETTING KEPT ${name}: $key=$value (newer AnyPS5 asks for it; the game runs with it) in its game-env.txt"
                $after = $retry
            }
        }
    }
    # 11-04 Astro's Playroom "could not start" (Defender blocked its exe in a new folder) and was pinned back as a
    # regression: a launch that never happened says nothing about the new AnyPS5.
    if ($after -like "could not start*") {
        $e.recheck = "NOT TESTED on ${Base}: $after"
    } elseif ($after -notlike "runs:*" -and $workedBefore -and $pin) {
        $cfg | Add-Member -NotePropertyName anyps5Build -NotePropertyValue $pin -Force
        $cfg | ConvertTo-Json | Set-Content -LiteralPath $cfgPath -Encoding ascii
        Use-Pc "pipeline rebuild of $(Split-Path $e.gameDir -Leaf)" $script:PcPriority { Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $e.gameDir } } $PcWait | Out-Null
        $e.recheck = "REGRESSED on $Base ($after); pinned back to $(Split-Path $pin -Leaf)"
    } else {
        $fixed = $after -like "runs:*" -and $before -notlike "runs:*" -and $e.status -ne "skip"
        $e.recheck = $(if ($fixed) { "FIXED on $Base" } else { "checked on $Base" }) + ": $after"
        if ($e.status -ne "skip") { $e.note = $after }
        if ($after -notlike "runs:*" -and $e.status -ne "skip") { $e.failure = Read-Failure $e.gameDir; $e.triage = "todo" }
    }
}

# Blu's own play sessions: when a game started from a game folder ends with a crash or a missing function, its log
# is kept in crash-logs\, the session is listed in the reports, and a missing function gets the PR test.
$Playing = @{}
function Watch-Play {
    $now = @{}
    $lanes = @(Lane-Pids)
    foreach ($p in Get-Process -ErrorAction SilentlyContinue | Where-Object { $lanes -notcontains $_.Id -and $_.Path -and $_.Path.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5*" }) {
        $now[$p.Id] = $true
        if (-not $script:Playing.ContainsKey($p.Id)) { try { $script:Playing[$p.Id] = [pscustomobject]@{ dir = (Split-Path $p.Path -Parent); started = $p.StartTime } } catch { } }
    }
    foreach ($id in @($script:Playing.Keys)) {
        if ($now.ContainsKey($id)) { continue }
        $s = $script:Playing[$id]; $script:Playing.Remove($id)
        $log = Join-Path $s.dir "anyps5.log"
        if (-not (Test-Path -LiteralPath $log) -or -not (Select-String -LiteralPath $log -Pattern '^FATAL' -Quiet)) { continue }
        $f = Read-Failure $s.dir
        $minutes = [int]((Get-Date) - $s.started).TotalMinutes
        New-Item -ItemType Directory -Force (Join-Path $s.dir "crash-logs") | Out-Null
        Copy-Item -LiteralPath $log -Destination (Join-Path $s.dir ("crash-logs\{0}-play-session.log" -f (Get-Date -Format "yyyy-MM-dd-HHmm")))
        $what = "$($f.kind)" + $(if ($f.function) { ": $($f.function)" } elseif ($f.where) { " at $($f.where)" } else { "" })
        $entryName = @($State.Keys | Where-Object { (Get-Entry $_).gameDir -eq $s.dir }) | Select-Object -First 1
        if ($entryName) {
            $e = Get-Entry $entryName
            $e.plays = @(@($e.plays) | Where-Object { $_ }) + ("{0}: crashed after {1} min of play ({2})" -f (Get-Date -Format "MM-dd HH:mm"), $minutes, $what)
            if ($f.kind -in "missing function", "stopped", "crash" -and ((Failure-Key $e.failure) -ne (Failure-Key $f) -or $e.triage -ne "done")) { $e.failure = $f; $e.triage = "todo" }
            Save-State; Write-Reports
        }
        Event "PLAY SESSION CRASH $(Split-Path $s.dir -Leaf) after $minutes min: $what"
    }
}

# AI play sessions (ai-player.py): in-game screenshots for #showcase and play past the menus. Only when the D: drive
# is quiet (a Coffee Talk session sat 4 minutes on a logo while extractions loaded the drive) and nobody has touched
# the PC for 10 minutes. The F12 screenshots go to _reports\showcase\<game>\.
Add-Type 'using System; using System.Runtime.InteropServices; public static class AutoIdle { [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO { public uint cbSize; public uint dwTime; } [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO p); public static uint IdleMs() { var i = new LASTINPUTINFO(); i.cbSize = (uint)Marshal.SizeOf(i); GetLastInputInfo(ref i); return (uint)Environment.TickCount - i.dwTime; } }'
# Busy = the D: drive moved more than 15 MB/s on average over 10 s (an open WinRAR window alone is not busy; an
# extraction or download writing to D: is).
function Drive-Busy {
    try {
        # Bytes alone miss a drive saturated by small random reads: 11-03 the nightly build kept D: 100% busy at
        # 4.7 MB/s and a PR test launch read 1 MB in 2 minutes. So low idle time counts as busy too.
        $samples = (Get-Counter '\PhysicalDisk(*)\Disk Bytes/sec', '\PhysicalDisk(*)\% Idle Time' -SampleInterval 2 -MaxSamples 5 -ErrorAction Stop).CounterSamples | Where-Object { $_.InstanceName -match '\bd:' }
        if (-not $samples) { return $false }
        $bytes = ($samples | Where-Object { $_.Path -like '*disk bytes/sec' } | Measure-Object CookedValue -Average).Average
        $idle = ($samples | Where-Object { $_.Path -like '*idle time' } | Measure-Object CookedValue -Average).Average
        $bytes -gt 15MB -or $idle -lt 15
    } catch { $true }
}
# The nightly AnyPS5 build and the pipeline take turns on D: (a slow USB drive): 11-03 both ran at once, D: sat at
# 100% busy, and a PR test's game read 1 MB in 2 minutes. The nightly build goes first, since rechecks need its
# AnyPS5; health.py in turn waits while a game runs.
# The task state as well: under heavy load the process list can come back without command lines (11-03 a Scorn fix
# test launched while health.py ran).
function Nightly-Running {
    if ((Get-ScheduledTask -TaskName "AnyPS5 nightly health" -ErrorAction SilentlyContinue).State -eq "Running") { return $true }
    [bool](Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'health\.py' })
}
# Only while it compiles or keeps D: busy: 11-03 its test reruns ran for 8 hours on a quiet D: and held every game test.
function Nightly-Building {
    [bool](Get-Process -Name ninja, cmake, clang-cl, lld-link -ErrorAction SilentlyContinue)
}
function Wait-Nightly([string]$what) {
    while ((Nightly-Running) -and (Nightly-Building)) {
        if (Test-Path -LiteralPath $PauseFlag) { return }
        Trace "${what}: waiting for the nightly AnyPS5 build to finish (it keeps D: busy; checks again in a minute)"
        Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
        Start-Sleep 60
    }
}
# Before a heavy step: while downloads keep D: busy, wait (up to 15 minutes, then go ahead anyway).
$BusySince = $null
# 11-05 Blu asked "what downloads?" when Claude's two unpacks were what kept D: busy: name the work using D:.
function Drive-Users {
    $jobs = @(Get-CimInstance Win32_Process -Filter "Name='WinRAR.exe' OR Name='UnRAR.exe' OR Name='7z.exe' OR Name='python.exe' OR Name='robocopy.exe' OR Name='chrome.exe' OR Name='ninja.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine.Replace([char]47, [char]92) -match '(?i)\bD:\\(?!AnyPS5\\|AnyPS5-tools\\|AnyPS5 Games\\_tools\\)' })
    $names = @($jobs | ForEach-Object {
        $c = $_.CommandLine
        $game = if ($c -match '(?i)dumps from ps5\\([^\\"]+)') { $Matches[1] } elseif ($c -match '(?i)AnyPS5 Games\\([^\\"]+)') { $Matches[1] } else { "" }
        $kind = switch -regex ($_.Name) { 'python' { if ($c -match 'exfat_extract') { "unpacking" } else { "python" } } 'RAR|7z' { "unpacking" } 'robocopy' { "copying" } 'chrome' { "Chrome download" } 'ninja' { "build" } }
        if ($game) { "$kind $game" } else { $kind }
    } | Select-Object -Unique)
    if ($names) { $names -join ", " } else { "something outside the pipeline" }
}
function Wait-Quiet([string]$what) {
    if (-not (Drive-Busy)) { $script:BusySince = $null; return $true }
    $who = Drive-Users
    if ($who -eq "something outside the pipeline" -and @(Lane-Pids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }).Count) {
        $who = "the GPU lane tests; they start no new game until this test has run"
        Set-Content -LiteralPath (Join-Path $Tools "live\lanes\yield.flag") -Value $what -Encoding utf8
    }
    Trace "${what}: waiting, the D: drive is busy ($who)"
    if (-not $script:BusySince) { $script:BusySince = Get-Date; Log "${what}: D: is busy ($who); waiting up to 15 minutes" }
    if (((Get-Date) - $script:BusySince).TotalMinutes -ge 15) { $script:BusySince = $null; return $true }
    $false
}
function Play-Game([string]$name, $e) {
    Wait-For-Blu "AI play of $name"
    Free-Gpu
    $player = Join-Path $Tools "ai-player.py"
    $n = Native { python -I $player $e.gameDir --minutes 10 }
    $line = ($n.out | Where-Object { $_ } | Select-Object -Last 1)
    $shots = @(Get-ChildItem -LiteralPath (Join-Path $e.gameDir "anyps5-screenshots") -Filter *.png -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddMinutes(-15) })
    if ($shots.Count) {
        $dest = Join-Path $Reports ("showcase\" + (Split-Path $e.gameDir -Leaf))
        New-Item -ItemType Directory -Force $dest | Out-Null
        foreach ($s in $shots) { Copy-Item -LiteralPath $s.FullName -Destination $dest -Force }
    }
    $e.played = "{0}: {1} ({2} screenshot(s))" -f (Get-Date -Format "MM-dd HH:mm"), $line, $shots.Count
}

# FPS optimizer (assistant\skills\fps_optimize.py): A/B runs of AnyPS5 settings on a hard-linked test copy of one game
# marked runs:, base and change in turns, 3 runs a side; a setting goes into the game's game-env.txt only when every
# run with it beat every base run. Plain measurements, no Claude calls. It stops by itself when Blu touches the PC, a
# game starts or the nightly build begins, and goes on next time (runs are kept in _reports\fps\<game>\runs.jsonl).
# Report: _reports\fps-optimizations.md. "nothing to do" waits 6 hours before asking again.
$FpsTool = Join-Path $Tools "assistant\skills\fps_optimize.py"
$FpsIdleUntil = Get-Date
function Fps-Ready { (Test-Path -LiteralPath $FpsTool) -and (Get-Date) -ge $script:FpsIdleUntil -and [AutoIdle]::IdleMs() -gt 600000 -and -not (Drive-Busy) }
function Optimize-Fps {
    Free-Gpu
    $n = Native { python -I $FpsTool --next --from-pipeline --apply --budget-minutes 60 }
    foreach ($l in @($n.out | Where-Object { $_ -match ': (done|stopped|time budget)|NOT RUN|nothing to do' })) { Log "  fps: $l" }
    if ($n.code -eq 4) { $script:FpsIdleUntil = (Get-Date).AddHours(6) }
    foreach ($l in @($n.out | Where-Object { $_ -match ': done\.' })) { Event "FPS $l" }
    # A game that passed its short test but stops later in the FPS run (11-03: Subnautica, The Exit 8, Hello Neighbor 2)
    # goes to the fix work with what stopped it there.
    foreach ($l in @($n.out | Where-Object { $_ -match '^(.+?): done\..*(stopped by itself|no measurable frames).*base run' })) {
        $leaf = $Matches[1].Trim()
        $copy = Join-Path $Games "_test\_fps\$leaf"
        $name = @($State.Keys | Where-Object { [IO.Path]::GetFileName("$((Get-Entry $_).gameDir)") -ieq $leaf }) | Select-Object -First 1
        if (-not $name -or -not (Test-Path -LiteralPath (Join-Path $copy "anyps5.log"))) { continue }
        $e = Get-Entry $name
        $e.failure = Read-Failure $copy
        $e.note = "stops later: $(Failure-Label $e.failure) (found in a longer FPS run; the first test passed)"
        $e.triage = "todo"; $e.triagedAt = ""
        Save-State; Write-Reports
        Event "STOPS LATER ${leaf}: $(Failure-Label $e.failure); queued for PR tests and a Claude fix"
    }
    if ($n.code -notin 0, 3, 4) { Log "fps optimizer: exit $($n.code)" }
}

# _reports\SUMMARY.md (every folder), one file per failed game, and most-wanted.md (missing functions by how many
# games they stop).
function Write-Reports {
    New-Item -ItemType Directory -Force $Reports | Out-Null
    $rows = foreach ($name in ($State.Keys | Sort-Object)) {
        $e = $State[$name]
        $game = if ($e.gameDir) { Split-Path $e.gameDir -Leaf } else { $name }
        "| $game | $($e.status) | $(("$($e.note)") -replace '\|', '/') | $(("$($e.played)") -replace '\|', '/') | $(("$($e.recheck)") -replace '\|', '/') |"
    }
    @("# AnyPS5 games: status", "", "Updated $(Get-Date -Format 'yyyy-MM-dd HH:mm') by auto-games.ps1; games are built from AnyPS5 $Base.", "", "| Game | Status | Last result | AI play session | After the last AnyPS5 update |", "|---|---|---|---|---|") + $rows | Set-Content -LiteralPath (Join-Path $Reports "SUMMARY.md") -Encoding utf8
    $wanted = @{}
    foreach ($name in $State.Keys) {
        $e = $State[$name]
        if ((-not $e.failure -and -not @($e.plays | Where-Object { $_ }).Count) -or -not $e.gameDir) { continue }
        $game = Split-Path $e.gameDir -Leaf
        $what = "$($e.failure.kind)" + $(if ($e.failure.function) { ": $($e.failure.function)" } elseif ($e.failure.message) { ": $($e.failure.message)" } elseif ($e.failure.where) { " at $($e.failure.where)" } else { "" })
        $lines = @("# $game", "", "First test: $($e.note)", "", "Stopped by: $what", "")
        if ($e.failure.line) { $lines += '```', $e.failure.line, '```', "" }
        if (@($e.chain).Count) {
            $lines += "| What stopped it | Library | Open PRs found | Tested with | Result |", "|---|---|---|---|---|"
            foreach ($c in @($e.chain)) { $lines += "| $($c.function) | $($c.libraries) | $($c.prs) | $($c.merged) | $(("$($c.result)") -replace '\|', '/') |" }
        } elseif ($e.triage -eq "done") { $lines += "No PR test yet." }
        elseif ($e.triage -eq "todo") { $lines += "PR test still to come." }
        $sessions = @($e.plays | Where-Object { $_ })
        if ($sessions.Count) { $lines += "", "## Crashes while playing", ""; $lines += @($sessions | ForEach-Object { "- $_" }) }
        $predictedPath = Join-Path $Reports "predicted\$game.json"
        if (Test-Path -LiteralPath $predictedPath) {
            try {
                $pj = Get-Content -LiteralPath $predictedPath -Raw | ConvertFrom-Json
                if (-not $pj.error) { $lines += "", "Predicted next missing functions: $((@($pj.predicted | Where-Object { -not $_.weak } | Select-Object -First 8 | ForEach-Object { "$($_.function) ($($_.class))" }) -join ', ')); all $(@($pj.predicted).Count) in predicted\$game.md" }
            } catch { }
        }
        $lines += "", "Logs: $($e.gameDir)\crash-logs"
        $lines | Set-Content -LiteralPath (Join-Path $Reports "$game.md") -Encoding utf8
        $found = @()
        if ($e.failure.function) { $found += [pscustomobject]@{ function = $e.failure.function; libraries = ""; prs = ""; result = "" } }
        $found += @($e.chain | Where-Object { $_ -and $_.function -and (-not $_.kind -or $_.kind -eq "missing function") })
        foreach ($c in $found) {
            if (-not $wanted.ContainsKey($c.function)) { $wanted[$c.function] = [pscustomobject]@{ games = @(); libraries = ""; prs = "" } }
            $w = $wanted[$c.function]
            if ($game -notin $w.games) { $w.games += $game }
            if ($c.libraries) { $w.libraries = $c.libraries }
            if ($c.prs) { $w.prs = $c.prs } elseif ("$($c.result)" -like "no open PR*") { $w.prs = "none: candidate for a new PR" }
        }
    }
    $rows = $wanted.GetEnumerator() | Sort-Object { @($_.Value.games).Count } -Descending | ForEach-Object { "| $($_.Key) | $($_.Value.libraries) | $(@($_.Value.games).Count) | $(@($_.Value.games) -join ', ') | $($_.Value.prs) |" }
    @("# Most-wanted missing functions", "", "Functions that stopped a game, the most games first. Open PRs: AnyPS5 PRs found for it.", "", "| Function | Library | Games | Which | Open PRs |", "|---|---|---|---|---|") + $rows | Set-Content -LiteralPath (Join-Path $Reports "most-wanted.md") -Encoding utf8
}

Log "pipeline started (watching $Dumps and $Downloads; AnyPS5 $Base)"
# A stop in the middle of a PR test leaves the PR library in the game and its own copy as *.pipeline-original:
# put those back first.
foreach ($orig in Get-ChildItem -LiteralPath $Games -Recurse -Depth 2 -Filter "*.prx.pipeline-original" -File -ErrorAction SilentlyContinue) {
    try { Copy-Retry $orig.FullName ($orig.FullName -replace '\.pipeline-original$', ''); Remove-Item -LiteralPath $orig.FullName -Force; Log "restored $($orig.FullName -replace '\.pipeline-original$', '') after an interrupted PR test" } catch { Log "error: restoring $($orig.FullName): $($_.Exception.Message)" }
}
try { Write-Reports } catch { Log "error: writing reports: $($_.Exception.Message)" }
# Requests from the AnyPS5 Games window and its assistant: _tools\pause.flag pauses the pipeline (after the current
# step), and _tools\requests.txt lines "retest <game>" / "redo <game>" are applied at the start of the next pass.
$PauseFlag = Join-Path $Tools "pause.flag"
$RequestsPath = Join-Path $Tools "requests.txt"
# Downloads Chrome got again after a failure (the AnyPS5 download keeper extension) land in Downloads\AnyPS5 retries\
# <game folder>\, because Chrome may only save automatic downloads there: a finished file (no .crdownload, untouched for
# 2 minutes) moves into its game folder. A failed copy already there (an 8-byte file, a short image) is set aside in
# "_finished archives (safe to delete)" first, never deleted.
$Retries = Join-Path $env:USERPROFILE "Downloads\AnyPS5 retries"
function Move-Retries {
    if (-not (Test-Path -LiteralPath $Retries)) { return }
    foreach ($f in Get-ChildItem -LiteralPath $Retries -Recurse -File -ErrorAction SilentlyContinue) {
        if ($f.Extension -in ".crdownload", ".tmp", ".part", ".partial", ".download") { continue }
        if (((Get-Date) - $f.LastWriteTime).TotalMinutes -lt 2 -or (Busy $f.DirectoryName)) { continue }
        $game = Split-Path $f.DirectoryName -Leaf
        $name = $f.Name -replace ' \(\d+\)(?=\.[^.]+$)', ''
        $folder = Join-Path $Dumps $game
        $dest = Join-Path $folder $name
        New-Item -ItemType Directory -Force $folder | Out-Null
        if (Test-Path -LiteralPath $dest) {
            $aside = Join-Path $Finished "$game\failed download $(Get-Date -Format 'yyyy-MM-dd-HHmm') $name"
            New-Item -ItemType Directory -Force (Split-Path $aside -Parent) | Out-Null
            Move-Item -LiteralPath $dest -Destination $aside
        }
        Log "${game}: moving the downloaded-again $name ($([int]($f.Length / 1MB)) MB) into its game folder"
        Move-Item -LiteralPath $f.FullName -Destination $dest
        $e = Get-Entry $game; $e.signature = ""; if ($e.status -in "failed", "encrypted", "download again", "set aside") { $e.status = "new"; $e.note = "" }; Save-State
        Event "DOWNLOADED AGAIN ${game}: $name is in the game folder now"
    }
}
# The space an archive needs once unpacked and built, in GB: an exFAT image unpacks to at most its own size; a RAR,
# 7z or zip set (game data barely compresses) to about its parts' size; plus 5 GB for the build.
$ReserveGB = 25
# Second games drive: when a game does not fit on D:, it is unpacked to G: (G:\ps5 games dumps from ps5\<game>) and
# built from there. One USB drive reading while the other writes is also faster than one drive doing both.
$Overflow = "G:\ps5 games dumps from ps5"
if (Test-Path -LiteralPath "G:\") { New-Item -ItemType Directory -Force $Overflow | Out-Null }
# C: (the NVMe SSD) is the first choice for unpacking when it has room (60 GB stay free for Windows): much faster than
# the USB drives, and a game whose files are on C: also loads faster.
$FastRoot = "C:\ps5 games dumps from ps5"
# Blu 11-10: C: is set aside for a big game (320 GiB install; folder "_a big game" below, skipped while it starts
# with "_"). Other games unpack to G: or D: until then; put this back to 60 once a big game is on C:.
$CReserveGB = 400
New-Item -ItemType Directory -Force $FastRoot | Out-Null
# A game already unpacked on C: or G: (by an earlier run, or by hand): its folder there has an eboot.bin and nothing
# is writing to it.
function Found-Elsewhere([string]$name) {
    foreach ($root in $FastRoot, $Overflow) {
        $p = Join-Path $root $name
        if ($p -ne $d.FullName -and (Test-Path -LiteralPath $p) -and -not (Busy $p) -and (Get-ChildItem -LiteralPath $p -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) { return $p }
    }
    $null
}
# Background extractions: exFAT images unpack in their own process, two at a time, while the pipeline goes on with the
# next game (Blu: start the next extraction instead of waiting for one 121 GB image).
$BgExtract = @{}
# A download still arriving: a browser's partial file (.crdownload, .part, .tmp ...) sitting in the folder itself, or one
# written in the last 30 minutes. Game files that only end that way deep inside an unpacked game (RuneScape Dragonwilds'
# movies\*.bk2.tmp, 11-09) are not downloads; treating them as one kept that game unbuilt for four days.
function Downloading($files, [string]$folder) {
    [bool]($files | Where-Object { $_.Extension -in ".crdownload", ".part", ".partial", ".tmp", ".download" -and ($_.DirectoryName -eq $folder -or ((Get-Date) - $_.LastWriteTime).TotalMinutes -lt 30) } | Select-Object -First 1)
}
# The game folders on D:, G: and C:. A name on several drives counts once: D: first, unless only another drive's copy has
# anything in it (a folder made for a download that landed elsewhere stays empty).
function Game-Folders {
    $seen = @{}
    foreach ($root in @($Dumps, $Overflow, $FastRoot)) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($d in Get-ChildItem -LiteralPath $root -Directory | Where-Object { -not $_.Name.StartsWith("_") }) {
            $has = [bool](Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
            if (-not $seen.ContainsKey($d.Name) -or ($has -and -not $seen[$d.Name].has)) { $seen[$d.Name] = @{ d = $d; has = $has } }
        }
    }
    $seen.Values | ForEach-Object { $_.d } | Sort-Object Name
}
# One extraction per drive: two on one USB disk take longer than one after the other and make the whole PC crawl.
function Drive-Extracting([string[]]$paths) {
    $letters = @($paths | ForEach-Object { $_.Substring(0, 1) } | Select-Object -Unique)
    $jobs = @(Get-CimInstance Win32_Process -Filter "Name='WinRAR.exe' OR Name='UnRAR.exe' OR Name='7z.exe' OR Name='python.exe'" | Where-Object { $_.CommandLine -match 'exfat_extract|RAR\.exe"? x|7z' } | ForEach-Object { $_.CommandLine -replace '"[^"]*(exfat_extract\.py|RAR\.exe|7z\.exe|python\.exe)"', '' })
    [bool]($jobs | Where-Object { $c = $_; $letters | Where-Object { $c -match ('"' + $_ + ':\\') } })
}
# Blu is waiting on the assistant: no new heavy work starts until it has answered.
function Assistant-Busy {
    $f = Join-Path $Tools "live\ai-busy.flag"
    (Test-Path -LiteralPath $f) -and ((Get-Date) - (Get-Item -LiteralPath $f).LastWriteTime).TotalMinutes -lt 15
}
# Jobs the assistant starts by hand (an extraction, a rebuild) register in _tools\live\jobs\<id>.json with the game, what
# it does, its process ID, its log and what the pipeline should do after it ("rebuild"). Their output shows in the live
# console, the game is left alone while one runs, and its end is an EVENT (Blu: the pipeline should know what is going on).
$JobsDir = Join-Path $Tools "live\jobs"
$HandBusy = @{}
function Check-HandJobs {
    $script:HandBusy = @{}
    if (-not (Test-Path -LiteralPath $JobsDir)) { return }
    foreach ($f in Get-ChildItem -LiteralPath $JobsDir -Filter *.json -File -ErrorAction SilentlyContinue) {
        try { $j = Get-Content -LiteralPath $f.FullName -Raw | ConvertFrom-Json } catch { continue }
        $key = $f.BaseName
        if (-not $script:JobShown.ContainsKey($key)) { $script:JobShown[$key] = 0; Event "ASSISTANT JOB $($j.game): $($j.what) (started $($j.started))" }
        if ($j.log -and (Test-Path -LiteralPath $j.log)) {
            $new = @(Get-Content -LiteralPath $j.log -ErrorAction SilentlyContinue | Select-Object -Skip $script:JobShown[$key])
            $script:JobShown[$key] += $new.Count
            foreach ($l in ($new | Select-Object -Last 40)) { if ("$l".Trim()) { Trace "  [$($j.game)] $l" } }
        }
        $alive = $j.pid -and (Get-Process -Id $j.pid -ErrorAction SilentlyContinue)
        if ($alive) {
            $script:HandBusy[$j.game] = $true
            if ($j.watch -and (Scan-Due ("job: " + $key) 2)) {
                $gb = (@(Get-ChildItem -LiteralPath $j.watch -Recurse -File -Force -ErrorAction SilentlyContinue) | Measure-Object Length -Sum).Sum / 1GB
                Trace ("  [$($j.game)] $($j.what): {0:N1} GB so far{1}" -f $gb, $(if ($j.total) { " of about $($j.total) GB" } else { "" }))
            }
            continue
        }
        $tail = if ($j.log -and (Test-Path -LiteralPath $j.log)) { "$(Get-Content -LiteralPath $j.log -Tail 1)" } else { "" }
        $ok = -not ($tail -match '^JOB EXIT (\d+)' -and [int]$Matches[1] -ne 0)
        Event ("ASSISTANT JOB {0} {1}: {2}{3}" -f $(if ($ok) { "FINISHED" } else { "FAILED" }), $j.game, $j.what, $(if ($tail) { " ($tail)" } else { "" }))
        if ($ok -and $j.then -in "rebuild", "retest") {
            $name = @($State.Keys | Where-Object { $_ -eq $j.game -or [IO.Path]::GetFileName("$((Get-Entry $_).gameDir)") -ieq $j.game }) | Select-Object -First 1
            if ($name) {
                $e = Get-Entry $name
                if ($j.then -eq "rebuild") { $e.status = "new"; $e.note = ""; $e.signature = ""; Event "REQUEST ${name}: build again after the assistant's job" }
                elseif ($e.gameDir) { $e.status = "built"; $e.note = ""; Event "REQUEST ${name}: test again after the assistant's job" }
                Save-State
            } else { Log "assistant job for $($j.game): no pipeline entry to $($j.then)" }
        }
        $done = Join-Path $JobsDir "done"
        New-Item -ItemType Directory -Force $done | Out-Null
        Move-Item -LiteralPath $f.FullName -Destination (Join-Path $done $f.Name) -Force
        $script:JobShown.Remove($key)
    }
}
$JobShown = @{}
function Check-BgExtract {
    foreach ($name in @($script:BgExtract.Keys)) {
        $j = $script:BgExtract[$name]
        $new = @(Get-Content -LiteralPath $j.log -ErrorAction SilentlyContinue | Select-Object -Skip $j.shown)
        $j.shown += $new.Count
        foreach ($l in $new) { Trace "  [$name] $l" }
        if (-not $j.proc.HasExited) {
            if ($new.Count -or -not $j.moved) { $j.moved = Get-Date }
            elseif (((Get-Date) - $j.moved).TotalMinutes -gt 60) { Stop-Process -Id $j.proc.Id -Force -ErrorAction SilentlyContinue; Event "EXTRACT STUCK ${name}: nothing new for an hour; stopped it" }
            continue
        }
        $script:BgExtract.Remove($name)
        $e = Get-Entry $name
        $tail = @(Get-Content -LiteralPath $j.log -Tail 1 -ErrorAction SilentlyContinue)
        if ($j.proc.ExitCode -eq 0 -or "$tail" -match '^[\d,]+ files, ') {
            $e.extracted = @($e.extracted) + $j.archive; $e.note = ""; $e.signature = ""
            if ($j.where -ne (Join-Path $Dumps $name)) { $e.extractedTo = $j.where }
            Save-State; Event "EXTRACTED ${name}: $($j.archive) -> $($j.where)"
        } else { $e.note = "extract failed ($($j.proc.ExitCode))"; $e.signature = ""; Save-State; Event "EXTRACT FAILED ${name}: $($j.archive) (exit $($j.proc.ExitCode))" }
    }
}
# Space kept for a big download: lines of "<drive> <GB> <game folder name>" in space-reservations.txt, for example "D 390 Street Fighter 6". The named game may use its own; a line stops counting once that game is built.
function Reserved-GB([string]$path, [string]$self) {
    $file = Join-Path $Tools "space-reservations.txt"
    if (-not (Test-Path -LiteralPath $file)) { return 0 }
    $total = 0
    foreach ($line in @(Get-Content -LiteralPath $file -ErrorAction SilentlyContinue)) {
        if ($line -notmatch '^\s*([A-Za-z])\s+(\d+)\s+(.+?)\s*$') { continue }
        $drive, $gb, $game = $Matches[1], [int]$Matches[2], $Matches[3]
        if ($drive -ine $path.Substring(0, 1) -or $game -ieq $self) { continue }
        if ($State -and $State.ContainsKey($game) -and $State[$game].status -in "built", "done") { continue }
        $have = 0; try { $have = (Get-ChildItem -LiteralPath ("{0}:\ps5 games dumps from ps5\{1}" -f $drive, $game) -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1GB } catch { }
        $total += [Math]::Max(0, $gb - $have)
    }
    $total
}
function Free-GB([string]$path, [string]$self = "") { try { (Get-PSDrive ($path.Substring(0, 1))).Free / 1GB - (Reserved-GB $path $self) } catch { 0 } }
function Need-GB([IO.FileInfo]$a) {
    if ($a.Extension -eq ".exfat") { return $a.Length / 1GB + 5 }
    $stem = $a.Name -replace '(\.part0*1)?\.(rar|7z|zip)(\.\d{3})?$', ''
    $parts = @(Get-ChildItem -LiteralPath $a.DirectoryName -File -Force | Where-Object { $_.Name.StartsWith($stem) -and $_.Name -match '\.(rar|7z|zip|r\d{2}|\d{3})$' })
    (($parts | Measure-Object Length -Sum).Sum / 1GB) * 1.1 + 5
}
# True when the thing named $key was last looked at over $minutes ago (and notes that it is being looked at now).
$LastScan = @{}
function Scan-Due([string]$key, [int]$minutes) {
    if ($script:LastScan.ContainsKey($key) -and ((Get-Date) - $script:LastScan[$key]).TotalMinutes -lt $minutes) { return $false }
    $script:LastScan[$key] = Get-Date
    $true
}
function Apply-Requests {
    if (-not (Test-Path -LiteralPath $RequestsPath)) { return }
    $lines = @(Get-Content -LiteralPath $RequestsPath -ErrorAction SilentlyContinue)
    Remove-Item -LiteralPath $RequestsPath -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        # "experiment <game>": the Experimenter takes the game now, from the Fixer's last "none" in its history.
        if ($line -match '^\s*experiment\s+(.+?)\s*$') {
            $game = $Matches[1]
            $name = @($State.Keys | Where-Object { [IO.Path]::GetFileName("$((Get-Entry $_).gameDir)") -ieq $game -or $_ -ieq $game }) | Select-Object -First 1
            if ($name) {
                $e = Get-Entry $name
                $none = @($e.chain) | Where-Object { $_.prs -eq "Claude fix" -and "$($_.result)" -like "no Claude fix: *" -and "$($_.result)" -notmatch 'committed nothing|did not pass|no answer' } | Select-Object -Last 1
                if ($none -or $e.fixerNone) {
                    if (-not $e.fixerNone) { $e.fixerNone = "$($none.result)" -replace '^no Claude fix: ', '' }
                    $e.expAt = ""; $e.triage = "todo"; Save-State; Log "${name}: the Experimenter takes it next ($($e.fixerNone.Substring(0, [Math]::Min(100, $e.fixerNone.Length))))"
                } else { Log "${name}: no Fixer answer to experiment from yet" }
            } else { Log "request '$line': no game matches" }
            continue
        }
        # "claudefix <game>": test PRs for it again and let Claude Code try a fix now, not after its weekly wait.
        if ($line -match '^\s*claudefix\s+(.+?)\s*$') {
            $game = $Matches[1]
            $name = @($State.Keys | Where-Object { [IO.Path]::GetFileName("$((Get-Entry $_).gameDir)") -ieq $game -or $_ -ieq $game }) | Select-Object -First 1
            if ($name) {
                $e = Get-Entry $name
                $copy = Join-Path $Games ("_test\_fps\" + [IO.Path]::GetFileName("$($e.gameDir)"))
                if ((-not $e.failure -or -not $e.failure.kind -or "$($e.note)" -like "runs:*") -and (Test-Path -LiteralPath (Join-Path $copy "anyps5.log"))) {
                    $e.failure = Read-Failure $copy; $e.note = "stops later: $(Failure-Label $e.failure) (found in a longer FPS run; the first test passed)"
                    Log "${name}: what stops it, from its FPS test copy: $(Failure-Label $e.failure)"
                } elseif ("$($e.note)" -like "runs:*") { Log "${name}: no FPS test copy log at $copy; its failure stays as it was" }
                $e.claudeFixAt = ""; $e.triage = "todo"; Save-State; Log "${name}: PR tests and a Claude fix queued again"
            } else { Log "request '$line': no game matches" }
            continue
        }
        # A test run outside the pipeline reports here, so the window and the assistant show what was really found.
        if ($line -match '^\s*tested\s+(.+?)\s*=\s*(.+?)\s*$') {
            $game, $what = $Matches[1], $Matches[2]
            $name = @($State.Keys | Where-Object { [IO.Path]::GetFileName("$((Get-Entry $_).gameDir)") -ieq $game -or $_ -ieq $game }) | Select-Object -First 1
            if ($name) { $e = Get-Entry $name; $e.status = "done"; $e.note = $what; Save-State; Log "tested ${name}: $what" } else { Log "request '$line': no game matches" }
            continue
        }
        # "aside <game> = why": Blu set it aside (Street Fighter 6 waits until he downloads it again); "redownload <game> =
        # why": its download is broken. Neither is worked on until its files change.
        # "backport <game> = <part of a folder name>": build from that backport (for example 10xx instead of 4xx), now.
        if ($line -match '^\s*backport\s+(.+?)\s*=\s*(.+?)\s*$') {
            $game, $pick = $Matches[1], $Matches[2]
            $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" }) | Select-Object -First 1
            if (-not $name) { Log "request '$line': no game matches"; continue }
            $e = Get-Entry $name; $e | Add-Member -Force NoteProperty backport $pick; $e.status = "new"; $e.note = ""; $e.signature = ""; Save-State
            Event "REQUEST ${name}: build from the backport folder with '$pick' in its name"
            continue
        }
        if ($line -match '^\s*(aside|redownload)\s+(.+?)\s*=\s*(.+?)\s*$') {
            $what, $game, $why = $Matches[1], $Matches[2], $Matches[3]
            $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" }) | Select-Object -First 1
            if (-not $name) { Log "request '$line': no game matches"; continue }
            $e = Get-Entry $name; $e.status = $(if ($what -eq "aside") { "set aside" } else { "download again" }); $e.note = $why; $e.signature = ""; Save-State
            Event "REQUEST ${name}: $($e.status) ($why)"
            continue
        }
        # Blu 11-05: "first <game>" puts a game at the top: it is extracted, built and tested before every other game,
        # even while another built game waits for its test.
        if ($line -match '^\s*first\s+(.+?)\s*$') {
            $game = $Matches[1]
            $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" }) | Select-Object -First 1
            if (-not $name) { Log "request '$line': no game matches"; continue }
            foreach ($k in @($State.Keys)) { (Get-Entry $k).first = $false }
            $e = Get-Entry $name; $e.first = $true; Save-State
            Event "REQUEST ${name}: first in the queue (extract, build and test before the others)"
            continue
        }
        if ($line -match '^\s*prtest\s+(.+?)\s*=\s*(.+?)\s*$') {
            $game, $prs = $Matches[1], $Matches[2]
            $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" -or (Get-Entry $_).gameDir -like "*$game*" }) | Select-Object -First 1
            if (-not $name -or -not (Get-Entry $name).gameDir) { Log "request '$line': no built game matches"; continue }
            $e = Get-Entry $name; $e.prTest = $prs; Save-State
            Event "REQUEST ${name}: test PR(s) $prs on it in the next quiet pass"
            continue
        }
        # "fixtry <game>": its fix prediction is tried (Try-Fixes, then the PR test) in the next quiet pass, not a day later.
        if ($line -match '^\s*fixtry\s+(.+?)\s*$') {
            $game = $Matches[1]
            $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" -or (Get-Entry $_).gameDir -like "*$game*" }) | Select-Object -First 1
            if (-not $name) { Log "request '$line': no game matches"; continue }
            $e = Get-Entry $name
            if (-not $e.gameDir -or "$($e.note)" -like "runs:*") { Log "request '$line': $name has no failure to fix"; continue }
            $e.triage = "todo"; $e.triagedAt = ""; Save-State
            Event "REQUEST ${name}: fix prediction and tries in the next quiet pass"
            continue
        }
        if ($line -notmatch '^\s*(retest|redo|rebuild|recheck)\s+(.+?)\s*$') { continue }
        $what, $game = $Matches[1], $Matches[2]
        $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" -or (Get-Entry $_).gameDir -like "*$game*" }) | Select-Object -First 1
        if (-not $name) { Log "request '$line': no game matches"; continue }
        $e = Get-Entry $name
        if ($what -eq "retest" -and $e.gameDir) { $e.status = "built"; $e.note = ""; Event "REQUEST $name`: test again" }
        elseif ($what -eq "recheck" -and $e.gameDir) { $e.recheck = "todo"; Event "REQUEST $name`: recheck on AnyPS5 $Base (relink, test, screenshot)" }
        elseif ($what -eq "rebuild") { $e.status = "new"; $e.note = ""; $e.signature = ""; Event "REQUEST $name`: build again (files stay extracted)" }
        elseif ($what -eq "redo") { $State.Remove($name); Event "REQUEST $name`: start over (extract, build, test)" }
        else { Log "request '$line': $name has no game folder to test" }
        Save-State
    }
}
$CurrentGame = ""; $PassErrors = @{}; $GoodKept = $false
$ScriptLoaded = (Get-Item -LiteralPath $PSCommandPath).LastWriteTime; $ScriptRefused = $null
$TestsOff = Join-Path $Tools "tests-off.flag"
while ($true) {
    if (Test-Path -LiteralPath $PauseFlag) {
        Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
        Trace "paused (Resume in the AnyPS5 Games window, or delete _tools\pause.flag)"
        Start-Sleep 15; continue
    }
    try {
        $didWork = $false
        # Blu 11-10: "I don't want it to be running anything else. We're dedicating it to ... a big game". While
        # _tools\live\only-game.txt exists (its first line names the game) a pass only shows the assistant's jobs:
        # no PR scan, and no other game is extracted, built, tested, PR-tested or FPS-tuned. Delete the file to end it.
        $onlyGame = Join-Path $Tools "live\only-game.txt"
        if (Test-Path -LiteralPath $onlyGame) {
            Check-HandJobs
            if (Scan-Due "only game" 5) { Trace "dedicated to $(Get-Content -LiteralPath $onlyGame -TotalCount 1): every other game, PR scan, PR test, FPS run and AI play is on hold" }
            Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
            if ((Get-Item -LiteralPath $PSCommandPath).LastWriteTime -gt $ScriptLoaded) {
                $parseErrors = $null
                [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$null, [ref]$parseErrors)
                if (-not $parseErrors) { Log "auto-games.ps1 changed; exiting between passes so the new version starts"; $mutex.ReleaseMutex(); exit 0 }
            }
            Start-Sleep 20; continue
        }
        Apply-Requests
        if (Scan-Due "clean test copies" 15) { Clean-TestCopies }
        if (Scan-Due "needs reply" 30) { Check-NeedsReply }
        if (Scan-Due "title prs" 45) { Check-TitlePrs }
        Move-Retries
        Check-BgExtract
        Check-HandJobs
        $free = Free-GB $Dumps
        # Downloads on G: -> extracted into D:. One archive per pass, like the rest.
        if (Test-Path -LiteralPath $Downloads) {
                foreach ($d in Get-ChildItem -LiteralPath $Downloads -Directory | Where-Object { -not $_.Name.StartsWith("_") }) {
                if ($didWork) { break }
                $e = Get-Entry ("download: " + $d.Name)
                $files = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
                if (-not $files -or (Downloading $files $d.FullName) -or (Busy $d.FullName)) { continue }
                $sig = "{0}/{1}" -f $files.Count, ($files | Measure-Object Length -Sum).Sum
                if ($e.signature -ne $sig) { $e.signature = $sig; Save-State; continue }
                $pending = @(Pending-Archives $d.FullName $e)
                if (-not $pending) { continue }
                $a = $pending[0]
                if ($a.Extension -eq ".exfat" -and -not (Exfat-Complete $a)) { if ($e.note -ne "image incomplete") { $e.note = "image incomplete"; Save-State; Log "download $($d.Name): $($a.Name) is not complete yet" }; continue }
                if (($a.Extension -eq ".rar" -or $a.Name -match '\.\d{3}$') -and -not (Rar-Set-Complete $a)) { if ($e.note -ne "rar set incomplete") { $e.note = "rar set incomplete"; Save-State; Log "download $($d.Name): waiting for the rest of the parts of $($a.Name)" }; continue }
                if ($free -lt $MinFreeGB) { if ($e.note -ne "low disk") { $e.note = "low disk"; Save-State; Event ("PAUSED download $($d.Name): only {0:N0} GB free on D:" -f $free) }; continue }
                $target = Join-Path $Dumps $d.Name
                Log "download $($d.Name): extracting $($a.Name) to $target"
                $code = Extract $a $target; $didWork = $true
                if ($code -eq 0) {
                    $e.extracted = @($e.extracted) + $a.Name; $e.note = ""; $e.signature = ""; Save-State
                    Event "EXTRACTED download $($d.Name): $($a.Name) -> $target"
                    if (-not @(Pending-Archives $d.FullName $e)) { Retire-Archives $d.FullName $e; $e.status = "done"; Save-State }
                } else { $e.note = "extract failed ($code)"; $e.signature = ""; Save-State; Event "EXTRACT FAILED download $($d.Name): $($a.Name) (exit $code)" }
            }
        }
        # One game at a time, to completion (Blu): a game that is part-way (some archives extracted, or built and
        # waiting for its test) is the only one worked on until it is done.
        # Only a built game waiting for its test holds the queue. (Holding it for any part-extracted game let Poppy
        # Playtime, whose archive held only a console .pkg, block every other game for a whole day.)
        $focus = @($State.Keys | Where-Object { -not $_.StartsWith("download: ") } | Where-Object { (Get-Entry $_).status -eq "built" }) | Select-Object -First 1
        if (Test-Path -LiteralPath $TestsOff) { $focus = $null }   # nothing is waiting for a test, so nothing holds the queue
        $first = @($State.Keys | Where-Object { $x = Get-Entry $_; $x.first -and $x.status -notin "built", "done", "skip", "set aside" }) | Select-Object -First 1
        if ($first) { $focus = $first }
        foreach ($d in Game-Folders) {
            $CurrentGame = $d.Name
            if ($didWork) { break }
            if ($focus -and $d.Name -ne $focus) { continue }
            if ($BgExtract.ContainsKey($d.Name)) { continue }   # unpacking in the background
            if ($HandBusy.ContainsKey($d.Name)) { Trace "checking $($d.Name) (the assistant's job is running on it; waiting)"; continue }
            $free = Free-GB $d.FullName $d.Name
            $e = Get-Entry $d.Name
            if ($e.status -notin "done", "skip", "set aside") { Trace "checking $($d.Name) ($($e.status)$(if ($e.note) { ': ' + $e.note }))" }
            # A folder marked encrypted or failed gets another try when its files change (Silt was re-downloaded as a
            # new image after being marked encrypted).
            # A build that failed only because D: was full (Scorn, Need for Speed Unbound and MOUSE P.I. on 10-29, while
            # Spider-Man Remastered's 121 GB image unpacked) is built again once there is room; its files stay extracted.
            if ($e.status -eq "failed" -and "$($e.note)" -match "No space left|Errno 28|low disk" -and $free -ge $MinFreeGB) {
                Log "$($d.Name): its build failed for lack of space; D: has $([int]$free) GB free now, building again"
                $e.status = "new"; $e.note = ""; $e.signature = ""; Save-State
            }
            if ($e.status -in "encrypted", "failed", "download again", "set aside" -and (Scan-Due ("sig: " + $d.Name) 10)) {
                $now = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
                $nowSig = "{0}/{1}" -f $now.Count, ($now | Measure-Object Length -Sum).Sum
                if (-not $e.signature) { $e.signature = $nowSig; Save-State }
                elseif ($nowSig -ne $e.signature -and -not (Downloading $now $d.FullName)) {
                    Log "$($d.Name): its files changed since it was marked $($e.status); trying again"
                    $e.status = "new"; $e.extracted = @(); $e.note = ""; $e.signature = ""; Save-State
                }
            }
            if ($e.status -in "built", "done", "skip", "encrypted", "failed", "download again", "set aside") { continue }   # built: waits for its test launch below
            # A folder still waiting for its download is listed again every 5 minutes, not every minute: listing every
            # file of every waiting folder each pass kept the USB drive busy around the clock.
            if ("$($e.note)" -match "waiting|incomplete|low disk" -and -not (Scan-Due ("files: " + $d.Name) 5)) { continue }
            $files = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
            if (-not $files) { continue }
            if (Downloading $files $d.FullName) { continue }
            # An archiver (WinRAR by hand, or one left running by a restarted pipeline) is working on this folder:
            # remember it, and use its result afterwards instead of extracting everything again.
            $unpackedIn = @($d.FullName) + @($e.extractedTo | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
            if (@($unpackedIn | Where-Object { Busy $_ }).Count) {
                if ($e.note -ne "being extracted") { $e.note = "being extracted"; Save-State; Log "$($d.Name): an archiver is extracting it; waiting for it to finish" }
                # Started by hand or by the assistant, so its progress shows here too.
                if (Scan-Due ("busy: " + $d.Name) 2) { Trace ("  [$($d.Name)] being extracted: {0:N1} GB unpacked so far" -f ((@($unpackedIn | ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -File -Force -ErrorAction SilentlyContinue } | Where-Object { $_.Extension.ToLowerInvariant() -notin $ArchiveExt -and $_.Directory.FullName -notin $unpackedIn }) | Measure-Object Length -Sum).Sum / 1GB)) }
                continue
            }
            if ($e.note -eq "being extracted" -and ($unpackedIn | ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue } | Select-Object -First 1)) {
                foreach ($a in @(Pending-Archives $d.FullName $e)) { $e.extracted = @($e.extracted) + $a.Name }
                $e.note = ""; $e.signature = ""; Save-State
                Event "EXTRACTED $($d.Name): the archiver that was running finished; using its files"
                continue
            }
            $sig = "{0}/{1}" -f $files.Count, ($files | Measure-Object Length -Sum).Sum
            if ($e.signature -ne $sig) { $e.signature = $sig; Save-State; continue }

            # Extract.
            $pending = @(Pending-Archives $d.FullName $e)
            if ($pending) {
                if ($free -lt $MinFreeGB -and (Free-GB $Overflow) -lt $MinFreeGB -and (Free-GB $FastRoot) - $CReserveGB -lt $MinFreeGB) { if ($e.note -ne "low disk") { $e.note = "low disk"; Save-State; Event ("PAUSED $($d.Name): only {0:N0} GB free on D:" -f $free) }; continue }
                $a = $pending[0]
                if ($a.Extension -eq ".exfat" -and -not (Exfat-Complete $a)) { if ($e.note -ne "image incomplete") { $e.note = "image incomplete"; Save-State; Log "$($d.Name): $($a.Name) is not complete yet" }; continue }
                if (($a.Extension -eq ".rar" -or $a.Name -match '\.\d{3}$') -and -not (Rar-Set-Complete $a)) { if ($e.note -ne "rar set incomplete") { $e.note = "rar set incomplete"; Save-State; Log "$($d.Name): waiting for the rest of the parts of $($a.Name)" }; continue }
                # Room first: the unpacked game plus its build must fit with $ReserveGB to spare, or it waits (Scorn,
                # Need for Speed and MOUSE P.I. once failed half-way when Spider-Man's 121 GB image filled D:).
                $elsewhere = Found-Elsewhere $d.Name
                if ($elsewhere) {
                    foreach ($x in @(Pending-Archives $d.FullName $e)) { $e.extracted = @($e.extracted) + $x.Name }
                    $e.extractedTo = $elsewhere; $e.note = ""; $e.signature = ""; Save-State
                    Event "EXTRACTED $($d.Name): already unpacked in $elsewhere; using it"
                    continue
                }
                $need = Need-GB $a
                $where = $d.FullName
                if ($free - $need -lt $ReserveGB) {
                    $freeG = Free-GB $Overflow
                    # 11-07 Blu: Spider-Man 2 (259 GB) waited with D: and G: too small while C: had 413 GB free. C: (the
                    # SSD) comes before G: and keeps $CReserveGB free for Windows; its folder is in C:\AnyPS5 Games.
                    $freeC = Free-GB $FastRoot
                    if ($freeC - $need -ge $CReserveGB) {
                        $where = Join-Path $FastRoot $d.Name
                        if ($e.extractedTo -ne $where) { $e.extractedTo = $where; Save-State; Log ("$($d.Name): D: has room for {0:N0} GB of the {1:N0} it needs; unpacking to C: ({2:N0} GB free)" -f [Math]::Max(0, $free - $ReserveGB), $need, $freeC) }
                    } elseif ((Test-Path -LiteralPath $Overflow) -and $freeG - $need -ge $ReserveGB) {
                        $where = Join-Path $Overflow $d.Name
                        if ($e.extractedTo -ne $where) { $e.extractedTo = $where; Save-State; Log ("$($d.Name): D: has room for {0:N0} GB of the {1:N0} it needs; unpacking to G: ({2:N0} GB free)" -f [Math]::Max(0, $free - $ReserveGB), $need, $freeG) }
                    } else {
                        $why = "needs {0:N0} GB; D: has {1:N0} GB free, G: {2:N0} GB, C: {3:N0} GB" -f $need, $free, $freeG, $freeC
                        if ($e.note -ne "no room: $why") { $e.note = "no room: $why"; Save-State; Event "WAITING FOR SPACE $($d.Name): $why (it goes on by itself once there is room)" }
                        continue
                    }
                }
                # Already extracted by hand (WinRAR "Extract to <archive name>\" nests it one level deeper): use that.
                $stem = $a.Name -replace '(\.part0*1)?\.(rar|7z|zip|exfat)(\.\d{3})?$', ''
                $nested = Join-Path $d.FullName $stem
                if ((Test-Path -LiteralPath $nested) -and (Get-ChildItem -LiteralPath $nested -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                    $e.extracted = @($e.extracted) + $a.Name; $e.note = "already extracted by hand in $stem"; $e.signature = ""; Save-State
                    Event "EXTRACTED $($d.Name): $($a.Name) was already extracted (in $stem); using those files"
                    break
                }
                if (-not (Wait-Quiet $d.Name)) { break }
                if ((Game-Running) -or (Assistant-Busy) -or (Drive-Extracting @($a.FullName, $where))) { continue }
                if ($a.Extension -eq ".exfat") {
                    if ($BgExtract.Count -ge 2) { continue }   # two at a time
                    $out = Join-Path $where ([IO.Path]::GetFileNameWithoutExtension($a.Name))
                    New-Item -ItemType Directory -Force $out | Out-Null
                    $log = Join-Path $Tools ("extract-" + (Safe-Name $d.Name) + ".log")
                    $proc = Start-Process python -ArgumentList "-I", "`"$ExfatTool`"", "`"$($a.FullName)`"", "`"$out`"" -RedirectStandardOutput $log -WindowStyle Hidden -PassThru
                    $null = $proc.Handle   # without this ExitCode stays empty
                    try { $proc.PriorityClass = "BelowNormal" } catch { }
                    $BgExtract[$d.Name] = @{ proc = $proc; archive = $a.Name; where = $where; log = $log; shown = 0 }
                    $e.note = "being extracted (background, to $where)"; Save-State
                    Event "EXTRACTING $($d.Name): $($a.Name) -> $where, in the background"
                    continue
                }
                Log "$($d.Name): extracting $($a.Name)"
                $script:CutShort = ""; $code = Extract $a $where; $didWork = $true
                if ($code -eq 0) { $e.extracted = @($e.extracted) + $a.Name; $e.note = ""; $e.signature = ""; Save-State; Event "EXTRACTED $($d.Name): $($a.Name)" }
                elseif ($script:CutShort) {
                    # The password opened it but the file ends early (Phasmophobia and POOLS on 11-01): the half-extracted
                    # files are not the game, so do not build them.
                    $e.status = "download again"; $e.note = "$($a.Name) is cut short ($script:CutShort broken); download it again"; $e.signature = ""; Save-State
                    Event "DOWNLOAD BROKEN $($d.Name): $($e.note)"
                }
                elseif (Get-ChildItem -LiteralPath $where -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1) {
                    # Password-protected archives (encrypted headers, e.g. Octopath Traveler 0) cannot be opened here,
                    # but Blu already extracted them with WinRAR: use those files.
                    $e.extracted = @($e.extracted) + $a.Name; $e.note = "used the files already extracted (archive could not be opened, exit $code)"; $e.signature = ""; Save-State
                    Event "EXTRACTED $($d.Name): $($a.Name) could not be opened (password?); using the game files already extracted next to it"
                }
                else { $e.note = "extract failed ($code)"; $e.signature = ""; Save-State; Event "EXTRACT FAILED $($d.Name): $($a.Name) (exit $code); will retry when the folder changes" }
                break
            }

            if (Game-Running) { continue }
            # Plan and build.
            if (Unpack-Packages $d.FullName $e) { $didWork = $true }
            if ($script:UnpackWaiting) { continue }
            $source = if ($e.extractedTo -and (Test-Path -LiteralPath $e.extractedTo)) { $e.extractedTo } else { $d.FullName }  # unpacked on C: or G:
            $plan = Plan-Game $source "$($e.backport)"
            # Only a cover picture (or other loose files) so far: the download has not arrived yet. Keep waiting.
            if (-not $plan.ok -and $plan.reason -eq "no eboot.bin found" -and @($e.extracted | Where-Object { $_ }).Count) {
                # Everything is extracted and still no game: e.g. only a console install package (.pkg).
                $pkg = Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Filter *.pkg -Force -ErrorAction SilentlyContinue | Select-Object -First 1
                $e.status = "failed"; $e.note = $(if ($pkg) { "only a package file ($($pkg.Name)); put ps5-dump-forge.exe in _tools\ps5-dump-forge to unpack it" } else { "no eboot.bin after extracting" })
                $e.signature = "{0}/{1}" -f $files.Count, ($files | Measure-Object Length -Sum).Sum; Save-State
                Event "NOT BUILT $($d.Name): $($e.note)"; continue
            }
            if (-not $plan.ok -and $plan.reason -eq "no eboot.bin found") {
                if ($e.note -ne "waiting for the game's files") { $e.note = "waiting for the game's files"; Save-State; Log "$($d.Name): no game files yet; waiting" }
                continue
            }
            if (-not $plan.ok) {
                $e.status = $(if ($plan.reason -like "*encrypted*") { "encrypted" } else { "failed" }); $e.note = $plan.reason
                $e.signature = "{0}/{1}" -f $files.Count, ($files | Measure-Object Length -Sum).Sum; Save-State
                Event "NOT BUILT $($d.Name): $($plan.reason)"; continue
            }
            if (-not (Wait-Quiet $d.Name)) { break }
            Log "$($d.Name): building $($plan.title) ($($plan.titleId)) from $($plan.kind) $($plan.code.FullName); data $($plan.dataDir)"
            $b = Build-Game $source $plan ($e.newCopy -eq $true); $didWork = $true
            if (-not $b.ok) { $e.status = "failed"; $e.note = $b.reason; Save-State; Event "BUILD FAILED $($plan.title): $($b.reason)"; continue }
            $warn = if ($b.warnings) { " (warnings: " + ($b.warnings -join "; ") + ")" } else { "" }
            $e.status = "built"; $e.gameDir = $b.gameDir; $e.note = ""; $e.newCopy = $false; Save-State
            Event "BUILT $($plan.title) ($($plan.titleId)) -> $($b.gameDir)$warn"
            Retire-Archives $d.FullName $e
            Predict-Missing @($b.gameDir)
        }
        $CurrentGame = ""
        # Test-launch built games, one at a time, never while a game is being played.
        foreach ($name in @(@($State.Keys) | Sort-Object { -not (Get-Entry $_).first })) {
            $e = Get-Entry $name
            # Catch up on archives a built game still has (a stop between building and moving them leaves them).
            # 11-05 Hades II was downloaded again into its built folder and this moved the new archive away unextracted:
            # an archive the entry never extracted is a new copy, so the folder starts over (extract, build, test).
            if ($e.status -in "built", "done" -and (Test-Path -LiteralPath (Join-Path $Dumps $name))) {
                $fresh = @(Pending-Archives (Join-Path $Dumps $name) $e)
                if ($fresh) {
                    $e.status = "new"; $e.note = ""; $e.signature = ""; $e.extractedTo = $null; $e.newCopy = $true; Save-State
                    Event "NEW COPY $name`: $($fresh[0].Name) was not extracted yet; extracting and building it again"
                } else { Retire-Archives (Join-Path $Dumps $name) $e }
            }
            # 11-07 Blu: built games' archives on G: and C: were never moved (this only looked on D:), so he deleted games
            # by hand to make room. There they are moved once everything in them is extracted; a new copy is left alone.
            if ($e.status -in "built", "done") {
                foreach ($other in @($Overflow, $FastRoot | ForEach-Object { Join-Path $_ $name } | Where-Object { Test-Path -LiteralPath $_ })) {
                    if (-not @(Pending-Archives $other $e)) { Retire-Archives $other $e }
                }
            }
            # A failed test run outside the pipeline (assistant night runs) records only a note; read its log so the
            # game still gets PR tests. A failed game is searched again once a day for new PRs.
            # Before 11-03 "NGS2: custom module ... not implemented" was taken for a missing function named NGS2.
            # A failure recorded before crashes and no-picture games got PR tests has no triage yet.
            if ($e.status -eq "done" -and $e.gameDir -and "$($e.note)" -like "problem:*" -and (-not $e.failure -or -not $e.triage -or ($e.failure.kind -eq "missing function" -and $e.failure.function -notmatch '^(sce|_)' -and $e.failure.function -cnotmatch '^[a-z]+[A-Z]'))) {
                $e.failure = Read-Failure $e.gameDir
                $e.triage = "todo"; Save-State
            }
            if ($e.failure -and $e.failure.kind -and $e.triage -eq "done" -and "$($e.note)" -notlike "runs:*" -and
                (-not $e.triagedAt -or ((Get-Date) - [datetime]$e.triagedAt).TotalHours -ge 24)) { $e.triage = "todo" }
            # Blu 11-02: test on its own again, but only after 10 minutes without keyboard or mouse use.
            # A game Blu put first (Blu 11-05: "please start the Gran Turismo test") does not wait for the idle minutes.
            if ($e.status -ne "built" -or (Game-Running) -or (Test-Path -LiteralPath $TestsOff) -or (-not $e.first -and [AutoIdle]::IdleMs() -lt 600000)) { continue }
            Log "${name}: test launch"
            $t = Test-Game $e.gameDir
            # A first run spent compiling shaders says little; the game gets one more run on its warm cache.
            if ($t -like "*shader cache still growing*" -or ($t -like "runs:*" -and $t -match 'FPS: 0\.\d+')) {
                Log "${name}: the first run was mostly shader compiling ($t); testing again on the warm shader cache"
                $t = Test-Game $e.gameDir "warm-cache"
            }
            $e.status = "done"; $e.note = $t; $e.first = $false; $didWork = $true
            if ($t -notlike "runs:*") { $e.failure = Read-Failure $e.gameDir; $e.triage = "todo" }
            Save-State; Write-Reports
            Event "TESTED $(Split-Path $e.gameDir -Leaf): $t"
        }
        Watch-Play
        # Slower work, one item per pass and only when nothing new is waiting and no game is being played:
        # a newer AnyPS5, then rechecking games on it, then PR tests for failed games.
        # 11-05 Blu: "let PR tests run alongside the nightly build". The builds live on the SSD since 11-03 (D:\AnyPS5 is a
        # link to C:), so the nightly compile no longer keeps D: busy; PR tests (named ones and triage) go on while it
        # compiles. Rechecks, AI play, the builder and FPS runs still wait: their numbers would share the CPU with it.
        $nightlyBusy = (Nightly-Running) -and (Nightly-Building)
        $prWork = [bool]@($State.Keys | Where-Object { $x = Get-Entry $_; $x.prTest -or $x.triage -eq "todo" }).Count
        $held = if ($didWork) { "" } elseif (Test-Path -LiteralPath $TestsOff) { "tests are switched off" } elseif (Gpu-Busy) { "the GPUs are busy: " + ((@(Get-Content -LiteralPath (Join-Path $Tools "live\gpu-busy.flag") -TotalCount 1 -ErrorAction SilentlyContinue) -join '') -replace '^\d+ \d\d:\d\d:\d\d ', '') } elseif (Game-Running) { "a game is running" } elseif ($nightlyBusy -and -not $prWork) { "the nightly AnyPS5 build is compiling" } else { "" }
        if ($held -and @($State.Keys | Where-Object { $x = Get-Entry $_; $x.recheck -eq "todo" -or $x.prTest -or $x.triage -eq "todo" }).Count) { Trace "rechecks and PR tests wait: $held" }
        if ($nightlyBusy -and $prWork -and -not $didWork) { Trace "the nightly build is compiling; PR tests go on beside it, rechecks wait" }
        # A "first" game still on its way to being built goes before any PR test or recheck (11-05: a MOUSE PR test ran
        # while My First Gran Turismo waited one pass for its download to settle).
        if ($first) { Trace "rechecks and PR tests wait: $first is first in the queue" }
        if (-not $first -and -not $didWork -and -not (Test-Path -LiteralPath $TestsOff) -and -not (Game-Running) -and (-not $nightlyBusy -or $prWork) -and (Wait-Quiet "background work")) {
            if (-not $nightlyBusy) { Update-AnyPS5 }
            # A PR test for a game Blu put first goes before rechecks (11-05: Hades II's recheck ran ahead of Gran Turismo's #14709).
            $firstPr = @($State.Keys | Where-Object { $x = Get-Entry $_; $x.first -and $x.prTest }).Count
            # Blu 11-05: a working game's recheck (a regression check) runs only overnight, 00:00 to 08:00, so it never
            # takes the PC while Blu uses it; a stopped game's recheck (it may be fixed now) runs any time.
            $night = (Get-Date).Hour -lt 8
            $recheck = if ($nightlyBusy -or $firstPr) { $null } else { @($State.Keys | Where-Object { $x = Get-Entry $_; $x.recheck -eq "todo" -and ($night -or "$($x.note)" -notlike "runs:*") }) | Select-Object -First 1 }
            # 11-04 a hand job (a crash A/B) runs in a game's test copy; its PR test would swap libraries into that copy
            # mid-run, so a game with an assistant job running waits (the job names the game by key or folder).
            $triage = @($State.Keys | Where-Object { $te = Get-Entry $_; $te.triage -eq "todo" -and -not $HandBusy.ContainsKey($_) -and -not ($te.gameDir -and $HandBusy.ContainsKey((Split-Path $te.gameDir -Leaf))) }) | Select-Object -First 1
            if ($recheck) {
                $e = Get-Entry $recheck
                Log "${recheck}: recheck on AnyPS5 $Base"
                Recheck-Game $recheck $e
                Predict-Missing @($e.gameDir)
                Save-State; Write-Reports
                Event "RECHECKED $(Split-Path $e.gameDir -Leaf): $($e.recheck)"
            } elseif ($prtest = @($State.Keys | Where-Object { (Get-Entry $_).prTest } | Sort-Object { -not (Get-Entry $_).first }) | Select-Object -First 1) {
                $e = Get-Entry $prtest
                $nums = @("$($e.prTest)" -split '[\s,#]+' | Where-Object { $_ -match '^\d+$' })
                $e.prTest = ""; $e.first = $false; Save-State
                # Title-scan picks queued before 11-05's filter are held to it too: fix or feat PRs only, and for a
                # game that runs, one that names a problem.
                if ($nums.Count -eq 1 -and "$($e.titlePrs)" -match "(^| )$($nums[0])@") {
                    $prTitle = "$((Native { gh pr view $nums[0] --repo $Repo --json title --jq .title }).out -join '')"
                    if ($prTitle -notmatch '^(fix|feat)(\(|:)' -or ("$($e.note)" -like "runs:*" -and $prTitle -notmatch '(?i)black|dark|missing|wrong|crash|hang|stall|freez')) { Log "  ${prtest}: title-scan PR #$($nums[0]) ($prTitle) dropped: not a fix for this game"; continue }
                }
                Event "PR TESTED $(Split-Path $e.gameDir -Leaf): $(Test-NamedPrs $e $nums)"
                Write-Reports
            } elseif (-not $nightlyBusy -and -not $triage -and -not (Drive-Busy) -and [AutoIdle]::IdleMs() -gt 600000 -and "$($(try { Get-Content -LiteralPath (Join-Path $Tools 'app-settings.json') -Raw | ConvertFrom-Json } catch { $null }).aiPlayerBrain)" -ne "off" -and
                      ($play = @($State.Keys | Where-Object { $x = Get-Entry $_; $x.gameDir -and (-not $x.played -or ((Uncapped-Today) -and "$($x.played)" -notmatch "by Claude")) -and ($x.note -like "runs:*" -or $x.status -eq "skip") -and (Test-Path -LiteralPath (Join-Path $x.gameDir "game.json")) }) | Select-Object -First 1)) {
                $e = Get-Entry $play
                Log "${play}: AI play session"
                Play-Game $play $e
                Save-State; Write-Reports
                Event "PLAYED $(Split-Path $e.gameDir -Leaf): $($e.played)"
            } elseif (-not $nightlyBusy -and (Builder-Due)) {
                Build-Next
            } elseif (-not $nightlyBusy -and (-not $triage -or $script:FpsTurn) -and (Fps-Ready)) {
                # FPS work takes every other turn with PR tests, so a long triage list does not starve it.
                $script:FpsTurn = $false
                Log "fps optimizer: next game marked runs:"
                Optimize-Fps
            } elseif ($triage) {
                $script:FpsTurn = $true
                $e = Get-Entry $triage
                # First what the fix prediction can test without a PR search (a setting the stop names, a playbook's
                # workaround, newer AnyPS5), on a test copy. A proven setting makes the game test again before any PR
                # test: until it is set, the stop says nothing about the game's real problems.
                if ((Try-Fixes $triage $e) -eq "setting kept") { $e.triage = ""; Save-State; Write-Reports }
                else {
                    Log "${triage}: PR test for $($e.failure.kind) $($e.failure.function)"
                    Triage-Game $triage $e
                    Claude-Fix $triage $e
                    Claude-Fix $triage $e "exp"
                    Claude-Fix $triage $e "raid"
                    $e.triage = "done"; $e.triagedAt = (Get-Date).ToString("s"); Save-State; Write-Reports
                    Predict-Fixes @($e.gameDir)
                    $last = @($e.chain) | Select-Object -Last 1
                    Event ("TRIAGED $(Split-Path $e.gameDir -Leaf): $($e.failure.kind) $($e.failure.function)" + $(if ($last) { " -> $($last.merged) : $($last.result)" } else { "" }))
                }
            } elseif (-not $nightlyBusy -and (Scan-Due "fix-predictions" 360)) {
                Predict-Fixes
            }
        }
    } catch {
        Log "error: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
        if ($CurrentGame) {
            $PassErrors[$CurrentGame] = 1 + [int]$PassErrors[$CurrentGame]
            if ($PassErrors[$CurrentGame] -ge 3) {
                $bad = Get-Entry $CurrentGame; $bad.status = "failed"; $bad.note = "keeps failing: $($_.Exception.Message)"; $bad.signature = ""; Save-State
                Event "NOT BUILT ${CurrentGame}: the same step failed 3 times in a row, moving on: $($_.Exception.Message)"
                $PassErrors.Remove($CurrentGame)
            }
        }
    }
    if (-not $GoodKept) { Copy-Item -LiteralPath $PSCommandPath -Destination "$PSCommandPath.good" -Force -ErrorAction SilentlyContinue; $GoodKept = $true }   # it completed a pass: the copy pipeline_doctor.py restores
    # Heartbeat: shows the pipeline is alive between events.
    Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
    # A changed script takes effect by itself: it exits between passes and the scheduled task (or the watchdog)
    # starts the new one within minutes. A script that does not parse is not loaded; the running one goes on.
    if ((Get-Item -LiteralPath $PSCommandPath).LastWriteTime -gt $ScriptLoaded) {
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($PSCommandPath, [ref]$null, [ref]$parseErrors)
        if (-not $parseErrors) { Log "auto-games.ps1 changed; exiting between passes so the new version starts"; $mutex.ReleaseMutex(); exit 0 }
        if ($ScriptRefused -ne (Get-Item -LiteralPath $PSCommandPath).LastWriteTime) { $ScriptRefused = (Get-Item -LiteralPath $PSCommandPath).LastWriteTime; Event "auto-games.ps1 changed but does not parse ($($parseErrors[0].Message) at line $($parseErrors[0].Extent.StartLineNumber)); still running the old version" }
    }
    Trace "pass done; next check in $IntervalSeconds s"
    Start-Sleep $IntervalSeconds
}
