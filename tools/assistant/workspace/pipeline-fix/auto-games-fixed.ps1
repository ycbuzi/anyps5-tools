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
param([string[]]$Dumps = @("D:\ps5 games dumps from ps5"), [string]$Downloads = "G:\ps5 downloads", [int]$IntervalSeconds = 60, [int]$MinFreeGB = 60)
# Also monitor standard Chrome downloads folder
$ChromeDownloads = "$env:USERPROFILE\Downloads"
# Downloads may also land on G: (a second USB drive), one folder per game: those are extracted straight into
# $Dumps\<folder name>, so downloading (G:) and playing/extracting (D:) do not share one drive.
$ErrorActionPreference = "Stop"
# Polite in the background: below-normal CPU priority, which every program the pipeline starts (extractions, the
# relinker, builds) inherits, so whatever Blu runs gets the CPU first. Extractions also use background disk priority.
try { (Get-Process -Id $PID).PriorityClass = "BelowNormal" } catch { }
$ProgressPreference = "SilentlyContinue"
$Tools = $PSScriptRoot
$Games = Split-Path $Tools -Parent
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
function Save-State { $State | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatePath -Encoding utf8 }
function Get-Entry([string]$name) {
    if (-not $State.ContainsKey($name)) { $State[$name] = [pscustomobject]@{ status = "new"; extracted = @(); note = ""; signature = ""; gameDir = ""; failure = ""; chain = @(); triage = ""; recheck = ""; previousBuild = "" } }
    # Entries read back from the state file may lack fields added later; PowerShell refuses to assign a missing one.
    foreach ($field in "status", "extracted", "note", "signature", "gameDir", "failure", "chain", "triage", "recheck", "previousBuild", "plays", "played", "extractedTo") {
        if (-not ($State[$name].PSObject.Properties.Name -contains $field)) { $State[$name] | Add-Member -NotePropertyName $field -NotePropertyValue "" }
    }
    $State[$name]
}

function Magic([string]$path) {
    $s = [IO.File]::OpenRead($path); try { $b = New-Object byte[] 4; [void]$s.Read($b, 0, 4) } finally { $s.Close() }
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
    [bool]($jobs | Where-Object { $_ -and $_.IndexOf($folder, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
}
# Folder names stay plain ASCII: native tools get them on the command line, where other characters can turn into '?'.
function Safe-Name([string]$s) { (($s -replace '[\\/:*?"<>|]', '') -replace '[^\x20-\x7E]', '' -replace '\s+', ' ').Trim().TrimEnd('.') }
function Folder-Bytes([string]$dir) {
    (Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\(decrypted|fakelib)\\' } | Measure-Object Length -Sum).Sum
}
function Game-Running { [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5 *" }) }  # AnyPS5 Games.exe (this window) is not a game

# Archives of a folder that still need extracting; a multi-part RAR set is represented by its first part.
function Pending-Archives([string]$folder, $entry) {
    $all = Get-ChildItem -LiteralPath $folder -File -Force | Where-Object { $_.Extension.ToLowerInvariant() -in $ArchiveExt -or $_.Name -match '\.(7z|zip)\.\d{3}$' }
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
            }
            if ($code -ne 0 -and $n.out) { Log "  unrar: $(($n.out | Select-Object -Last 3) -join ' ')" }
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
function Plan-Game([string]$folder) {
    $eboots = Get-ChildItem -LiteralPath $folder -Recurse -File -Filter "eboot.bin" -Force | Where-Object { $_.FullName -notmatch '\\fakelib\\' }
    if (-not $eboots) { return [pscustomobject]@{ ok = $false; reason = "no eboot.bin found" } }
    $rank = @{ elf = 3; fself = 2; unknown = 0; encrypted = 0 }
    $cands = foreach ($e in $eboots) { [pscustomobject]@{ file = $e; kind = (Magic $e.FullName); backport = $e.FullName -match 'backport' } }
    $code = $cands | Sort-Object @{ e = { $rank[$_.kind] }; Descending = $true }, @{ e = { $_.backport } }, @{ e = { $_.file.FullName.Length } } | Select-Object -First 1
    if ($rank[$code.kind] -eq 0) { return [pscustomobject]@{ ok = $false; reason = "every eboot.bin is still encrypted (needs decrypted files)" } }
    $codeDir = $code.file.DirectoryName
    $dataDirs = $cands | Where-Object { $_.file.DirectoryName -notmatch '\\decrypted$' -and -not $_.backport } | ForEach-Object { $_.file.DirectoryName } | Select-Object -Unique
    if (-not $dataDirs) { $dataDirs = @($cands | ForEach-Object { $_.file.DirectoryName } | Select-Object -Unique) }
    $dataDir = $dataDirs | Sort-Object { Folder-Bytes $_ } -Descending | Select-Object -First 1
    $paramPath = @("$dataDir\sce_sys\param.json", "$codeDir\sce_sys\param.json", "$codeDir\..\sce_sys\param.json") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
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

function Build-Game([string]$folder, $plan) {
    $gameDir = Join-Path $Games ("{0} ({1})" -f $plan.title, $plan.titleId)
    # A folder with a game.json and an .exe is a finished game (maybe set up by hand): leave it. Without an .exe it is
    # an interrupted build, which is redone.
    if ((Test-Path -LiteralPath (Join-Path $gameDir "game.json")) -and @(Get-ChildItem -LiteralPath $gameDir -Filter *.exe -File -ErrorAction SilentlyContinue).Count) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "a game folder already exists; left as it is" } }
    New-Item -ItemType Directory -Force $gameDir | Out-Null
    $warnings = @()
    $cfg = [ordered]@{ title = $plan.title; titleId = $plan.titleId }
    if ($plan.kind -eq "elf") {
        # Already a clean ELF tree (a dump's "decrypted" folder): convert it in place.
        $cfg.dump = $plan.codeDir
        if ($plan.dataDir -ne $plan.codeDir) { $cfg.resources = $plan.dataDir }
        $mods = Get-ChildItem -LiteralPath $plan.codeDir -Recurse -File -Force | Where-Object { $_.Extension -in ".prx", ".sprx" } | ForEach-Object { $_.DirectoryName.Substring($plan.codeDir.Length).TrimStart('\').Replace('\', '/') } | Select-Object -Unique
    } else {
        # Unwrap the fSELF code and every module into _sources (code folder first, the data folder for the rest).
        $src = Join-Path $Games ("_sources\{0} ({1})" -f $plan.title, $plan.titleId)
        New-Item -ItemType Directory -Force $src | Out-Null
        $n = Native { python -I $Unwrap $plan.code.FullName (Join-Path $src "eboot.elf") }
        if ($n.code -ne 0) { return [pscustomobject]@{ ok = $false; gameDir = $gameDir; reason = "unwrapping eboot.bin failed: $($n.out -join ' ')" } }
        $seen = @{}
        foreach ($base in @($plan.codeDir, $plan.dataDir) | Select-Object -Unique) {
            foreach ($m in Get-ChildItem -LiteralPath $base -Recurse -File -Force | Where-Object { $_.Extension -in ".prx", ".sprx" -and $_.FullName -notmatch '\\(fakelib|sce_sys|decrypted)\\' -and $_.Name -notmatch '\.bak' }) {
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
    $moduleDirs = @($mods | Where-Object { $_ -and $_ -ne "sce_module" -and $_ -notlike "sce_module/*" })
    if ($moduleDirs) { $cfg.moduleDirs = $moduleDirs }
    $cfg | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gameDir "game.json") -Encoding ascii
    $rebuild = Get-ChildItem -LiteralPath $Games -Directory | ForEach-Object { Join-Path $_.FullName "Rebuild.cmd" } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($rebuild) { Copy-Item -LiteralPath $rebuild -Destination $gameDir -Force }
    $n = Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $gameDir }
    $out = $n.out
    $ok = $n.code -eq 0 -and ($out -match '^Done\.')
    Log ("  build: " + (($out | Select-Object -Last 6) -join ' | '))
    [pscustomobject]@{ ok = [bool]$ok; gameDir = $gameDir; reason = $(if ($ok) { "" } else { ($out | Select-Object -Last 3) -join ' ' }); warnings = $warnings }
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
        $gpu = try { [int]((& nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits -i 0) | Select-Object -First 1) } catch { 0 }
        if ($gpu -ge 40) { return "the RTX 4070 is $gpu% busy with something else" }
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
function Test-Game([string]$gameDir, [string]$label = "first-test") {
    Wait-For-Blu "test of $(Split-Path $gameDir -Leaf)"
    Free-Gpu
    $cfg = Get-Content -LiteralPath (Join-Path $gameDir "game.json") -Raw | ConvertFrom-Json
    $exe = Join-Path $gameDir ("{0}.exe" -f $(if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }))
    $p = $null
    foreach ($try in 1..3) { try { $p = Start-Process -FilePath $exe -WorkingDirectory $gameDir -PassThru -ErrorAction Stop; break } catch { Start-Sleep 8 } }
    if (-not $p) { return "could not start (blocked?)" }
    $deadline = (Get-Date).AddSeconds(240); $frames = 0; $title = ""
    while ((Get-Date) -lt $deadline -and -not $p.HasExited) {
        Start-Sleep 3; $p.Refresh(); $title = Game-Title $p
        if ($title -match 'FPS: ([0-9.]+) \(([0-9]+)\)') { $frames = [int]$Matches[2]; if ($frames -gt 600) { break } }
    }
    # "Pause everything" in the AnyPS5 Games window closed this game: no result; the game is tested again after Resume.
    $interrupted = Join-Path $Tools "test-interrupted.flag"
    if (Test-Path -LiteralPath $interrupted) { Remove-Item -LiteralPath $interrupted -ErrorAction SilentlyContinue; throw "test of $(Split-Path $gameDir -Leaf) interrupted by Pause; it runs again after Resume" }
    # "runs" needs at least 300 frames: a game that drew a frame or two and then stopped (Ghost Song with only #11575:
    # 2 frames in 4 minutes) is stalled, not running.
    $result = if ($p.HasExited) { "exited 0x{0:X8} after {1:N0} s" -f $p.ExitCode, ((Get-Date) - $p.StartTime).TotalSeconds } elseif ($frames -ge 300) { "runs: $title" } elseif ($frames -gt 0) { "stalls: only $frames frames in 4 min ($title)" } else { "no frames after 4 min" }
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Start-Sleep 2 }
    $log = Join-Path $gameDir "anyps5.log"
    if (Test-Path -LiteralPath $log) {
        $why = Select-String -LiteralPath $log -Pattern "not implemented|^FATAL|unhandled exception" | Select-Object -First 2 | ForEach-Object { $_.Line.Trim() }
        if ($why -and $result -notlike "runs:*") {
            $result += " | " + ($why -join " | ")
            New-Item -ItemType Directory -Force (Join-Path $gameDir "crash-logs") | Out-Null
            Copy-Item -LiteralPath $log -Destination (Join-Path $gameDir ("crash-logs\{0}-{1}.log" -f (Get-Date -Format "yyyy-MM-dd-HHmm"), $label))
        }
    }
    $result
}

function Retire-Archives([string]$folder, $entry) {
    # Archives go to the "_finished archives" folder on their own drive (moving is instant there).
    $root = if ($folder.StartsWith($Downloads, [StringComparison]::OrdinalIgnoreCase)) { Join-Path $Downloads "_finished archives (safe to delete)" } else { $Finished }
    $dest = Join-Path $root (Split-Path $folder -Leaf)
    $moved = 0
    foreach ($a in Get-ChildItem -LiteralPath $folder -File -Force | Where-Object { $_.Extension.ToLowerInvariant() -in $ArchiveExt -or $_.Name -match '\.(7z|zip)\.\d{3}$' }) {
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
    Trace ('$ ' + ($shown -replace '\s+', ' '))
    try { $out = @(& $command 2>&1 | ForEach-Object { $l = "$_"; Trace ("  " + $l); $l }); Trace "  (exit $LASTEXITCODE)"; [pscustomobject]@{ code = $LASTEXITCODE; out = $out } }
    finally { $ErrorActionPreference = $previous }
}
function Copy-Retry([string]$from, [string]$to) {
    for ($try = 1; ; $try++) { try { Copy-Item -LiteralPath $from -Destination $to -Force -ErrorAction Stop; return } catch { if ($try -ge 20) { throw }; Start-Sleep -Milliseconds 500 } }
}

# What stopped a failed test, read from the game's anyps5.log.
function Read-Failure([string]$gameDir) {
    $log = Join-Path $gameDir "anyps5.log"
    $f = [pscustomobject]@{ kind = "no frames"; function = ""; where = ""; line = "" }
    if (-not (Test-Path -LiteralPath $log)) { return $f }
    $thrown = Select-String -LiteralPath $log -Pattern 'what\(\):\s+([A-Za-z_]\w*)\b.*not implemented' | Select-Object -First 1
    if (-not $thrown) { $thrown = Select-String -LiteralPath $log -Pattern '\b(sce[A-Za-z0-9_]+)\b.*not implemented' | Select-Object -First 1 }
    if ($thrown) { $f.kind = "missing function"; $f.function = $thrown.Matches[0].Groups[1].Value; $f.line = $thrown.Line.Trim(); return $f }
    $fatal = Select-String -LiteralPath $log -Pattern '^FATAL' | Select-Object -First 1
    if ($fatal) {
        $f.kind = "crash"; $f.line = $fatal.Line.Trim()
        $frame = Get-Content -LiteralPath $log | Select-Object -Skip $fatal.LineNumber -First 30 | Where-Object { $_ -match '^\s+(rip|#\d+)\s+0x[0-9a-f]+\s+\S+\+0x[0-9a-f]+' } | Select-Object -First 1
        if ($frame -and $frame -match '\s(\S+\+0x[0-9a-f]+)') { $f.where = $Matches[1] }
    }
    $f
}

# The AnyPS5 libraries that export a function, at the commit the games are built from.
function Find-Libraries([string]$function) {
    $r = Native { git -C $PrSource grep -l -E "APS5_VABI[^(]*\b$function\(" $Base -- core/libs/prx }
    @($r.out | ForEach-Object { if ($_ -match 'core/libs/prx/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
}

# Open AnyPS5 PRs that mention the function and change one of its libraries, most recently updated first.
function Find-Prs([string]$function, [string[]]$libraries) {
    $r = Native { gh search prs --repo $Repo --state open $function --json number,title,url,updatedAt --limit 10 }
    if ($r.code -ne 0 -or -not ($r.out -join "").Trim()) { return @() }
    $found = @(($r.out -join "`n") | ConvertFrom-Json)
    $prs = foreach ($p in ($found | Sort-Object updatedAt -Descending)) {
        $v = Native { gh pr view $p.number --repo $Repo --json files,headRefOid }
        if ($v.code -ne 0) { continue }
        $info = ($v.out -join "`n") | ConvertFrom-Json
        $libs = @($info.files.path | ForEach-Object { if ($_ -match '^core/libs/prx/([^/]+)/') { $Matches[1] } } | Select-Object -Unique)
        if ($libraries -and -not ($libs | Where-Object { $_ -in $libraries })) { continue }
        [pscustomobject]@{ number = [int]$p.number; title = $p.title; url = $p.url; head = $info.headRefOid.Substring(0, 10); libraries = $libs }
    }
    @($prs)
}

# Merges a stack of PRs onto $Base in the PR worktree (local merge commits only, nothing pushed) and builds the
# libraries they change.
function Build-PrLibraries($stack) {
    $fail = { param($why) [pscustomobject]@{ ok = $false; reason = $why; commit = ""; built = @{} } }
    # A stop in the middle of a git command leaves index.lock behind and every later checkout fails; remove one that
    # is older than 10 minutes while no git process runs.
    $gitDir = (Native { git -C $PrSource rev-parse --git-dir }).out | Select-Object -First 1
    $lock = Join-Path $(if ([IO.Path]::IsPathRooted($gitDir)) { $gitDir } else { Join-Path $PrSource $gitDir }) "index.lock"
    if ((Test-Path -LiteralPath $lock) -and ((Get-Date) - (Get-Item -LiteralPath $lock).LastWriteTime).TotalMinutes -gt 10 -and -not (Get-CimInstance Win32_Process -Filter "Name='git.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape((Split-Path $PrSource -Leaf)) })) { Remove-Item -LiteralPath $lock -Force; Log "removed a stale $lock" }
    $r = Native { git -C $PrSource checkout -q --detach $Base }
    if ($r.code -ne 0) { return & $fail "checking out $Base failed: $($r.out -join ' ')" }
    foreach ($p in $stack) {
        $fetched = $false
        foreach ($try in 1..3) { $r = Native { git -C $PrSource fetch -q origin "refs/pull/$($p.number)/head:refs/remotes/pr/$($p.number)" }; if ($r.code -eq 0) { $fetched = $true; break }; Start-Sleep 20 }
        if (-not $fetched) { return & $fail "fetching #$($p.number) failed" }
        $r = Native { git -C $PrSource -c user.name=blu -c user.email=user@example.com merge --no-edit -q "refs/remotes/pr/$($p.number)" }
        if ($r.code -ne 0) { Native { git -C $PrSource merge --abort } | Out-Null; return & $fail "#$($p.number) does not merge cleanly onto $Base" }
    }
    $commit = (Native { git -C $PrSource rev-parse --short=10 HEAD }).out | Select-Object -First 1
    if ($env:PATH -notlike "*$($Settings.mingwBin)*") { $env:PATH = "$($Settings.mingwBin);$env:PATH" }
    $built = @{}
    foreach ($lib in @($stack | ForEach-Object { $_.libraries } | Select-Object -Unique)) {
        $r = Native { & (Join-Path $Settings.mingwBin "ninja.exe") -C $PrBuild "core/libs/libs/$lib.prx" }
        if ($r.code -ne 0) { return & $fail "building $lib.prx failed: $(($r.out | Select-Object -Last 3) -join ' ')" }
        $built[$lib] = Join-Path $PrBuild "core\libs\libs\$lib.prx"
    }
    [pscustomobject]@{ ok = $true; reason = ""; commit = $commit; built = $built }
}

# Swaps PR libraries into a game, test-launches it, and always puts the game's own libraries back.
function Test-WithLibraries([string]$gameDir, $built, [string]$label) {
    $swapped = @()
    try {
        foreach ($lib in $built.Keys) {
            $dst = Join-Path $gameDir "libs\$lib.prx"
            $had = Test-Path -LiteralPath $dst
            if ($had) { Copy-Retry $dst "$dst.pipeline-original" }
            Copy-Retry $built[$lib] $dst
            $swapped += [pscustomobject]@{ path = $dst; had = $had }
        }
        $result = Test-Game $gameDir $label
        [pscustomobject]@{ result = $result; failure = (Read-Failure $gameDir) }
    } finally {
        foreach ($s in $swapped) {
            if ($s.had) { Copy-Retry "$($s.path).pipeline-original" $s.path; Remove-Item -LiteralPath "$($s.path).pipeline-original" -Force }
            else { Remove-Item -LiteralPath $s.path -Force -ErrorAction SilentlyContinue }
        }
    }
}

# For a game stopped by a missing function: find open PRs for it, test the game with them, and follow the next
# missing function the same way, up to three PRs deep. Crashes and hangs are only reported.
function Triage-Game([string]$name, $e) {
    $chain = @()
    if (-not $e.failure -or $e.failure.kind -ne "missing function") { $e.chain = $chain; return }
    # A PR is kept only when it gets the game further (past this function); a candidate that only mentions the
    # function is dropped and the next one tried. At most three candidates per function and six launches in all.
    $stack = @(); $function = $e.failure.function; $launches = 0
    while ($function -and $launches -lt 6) {
        $libs = @(Find-Libraries $function)
        $candidates = @(Find-Prs $function $libs | Where-Object { $_.number -notin @($stack | ForEach-Object { $_.number }) } | Select-Object -First 3)
        if (-not $candidates) { $chain += [pscustomobject]@{ function = $function; libraries = ($libs -join ', '); prs = ""; merged = ""; result = "no open PR implements it" }; break }
        $found = (($candidates | ForEach-Object { "#" + $_.number }) -join ' ')
        $next = $null; $progress = $false
        foreach ($pr in $candidates) {
            $trial = @($stack) + $pr
            Log "${name}: testing $function with $(($trial | ForEach-Object { '#' + $_.number }) -join ' + ')"
            $row = [pscustomobject]@{ function = $function; libraries = ($libs -join ', '); prs = $found; merged = (($trial | ForEach-Object { "#$($_.number) $($_.head)" }) -join " + "); result = "" }
            $b = Build-PrLibraries $trial
            if (-not $b.ok) { $row.result = $b.reason; $chain += $row; continue }
            $row.merged += " onto $Base = $($b.commit)"
            $t = Test-WithLibraries $e.gameDir $b.built ("pr-" + (($trial | ForEach-Object { $_.number }) -join "-"))
            $launches++
            $row.result = $t.result
            $chain += $row
            if ($t.result -like "runs:*") { $stack = $trial; $progress = $true; break }
            if ($t.failure.kind -eq "missing function" -and $t.failure.function -eq $function) { continue }   # this PR did not help
            $stack = $trial; $progress = $true
            if ($t.failure.kind -eq "missing function") { $next = $t.failure.function }
            break
        }
        if (-not $progress) { break }
        $function = $next
    }
    $e.chain = $chain
}

# Takes a newer AnyPS5 from the nightly health build, but only after a run that built, did not bisect a regression
# (a bisect leaves the build folder at another commit) and still has its checkout at the tested commit. The build
# is copied to its own _tools\anyps5-<commit> (older ones stay, for pinned games); then every game is rechecked.
function Update-AnyPS5 {
    $statePath = Join-Path $HealthReports "state-latest.json"
    if (-not (Test-Path -LiteralPath $statePath)) { return }
    if (Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='ninja.exe' OR Name='cmake.exe' OR Name='ctest.exe'" | Where-Object { $_.CommandLine -match 'health' }) { return }
    $sha = "$((Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).sha)"
    if (-not $sha -or $sha.StartsWith($Base) -or $Base.StartsWith($sha) -or $script:RefusedHealth -eq $sha) { return }
    $report = Get-Content -LiteralPath (Join-Path $HealthReports "latest.md") -Raw -ErrorAction SilentlyContinue
    $head = "$((Native { git -C $HealthSource rev-parse --short=10 HEAD }).out | Select-Object -First 1)"
    if (-not $report -or $report -notmatch [regex]::Escape($sha) -or $report -match 'bisecting|build failed|cmake failed' -or -not $head.StartsWith($sha)) {
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
    $count = 0
    foreach ($name in @($State.Keys)) {
        $e = Get-Entry $name
        if ($e.gameDir -and $e.status -in "done", "skip", "built") { $e.recheck = "todo"; $e.previousBuild = $previous; $count++ }
    }
    Save-State
    Event "ANYPS5 UPDATED $previousBase -> $sha; rechecking $count games one at a time"
}

# Rebuilds a game on the current AnyPS5 and tests it. A game that worked before and fails now goes back to the
# build it worked on (pinned in its game.json) so it stays playable; one that failed and now runs is marked FIXED.
function Recheck-Game([string]$name, $e) {
    $cfgPath = Join-Path $e.gameDir "game.json"
    $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
    $pin = if ($cfg.anyps5Build) { $cfg.anyps5Build } else { $e.previousBuild }
    if ($cfg.anyps5Build) { $cfg.PSObject.Properties.Remove("anyps5Build"); $cfg | ConvertTo-Json | Set-Content -LiteralPath $cfgPath -Encoding ascii }
    $before = "$($e.note)"
    $workedBefore = $before -like "runs:*" -or $e.status -eq "skip"
    $n = Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $e.gameDir }
    $after = if ($n.code -eq 0 -and ($n.out -match '^Done\.')) { Test-Game $e.gameDir "recheck-$Base" } else { "build failed: " + (($n.out | Select-Object -Last 2) -join ' ') }
    if ($after -notlike "runs:*" -and $workedBefore -and $pin) {
        $cfg | Add-Member -NotePropertyName anyps5Build -NotePropertyValue $pin -Force
        $cfg | ConvertTo-Json | Set-Content -LiteralPath $cfgPath -Encoding ascii
        Native { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Tools "build-game.ps1") -GameDir $e.gameDir } | Out-Null
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
    foreach ($p in Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Games + "\", [StringComparison]::OrdinalIgnoreCase) -and $_.Path -notlike "*\_tools\*" -and $_.ProcessName -notlike "AnyPS5 *" }) {
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
            if ($f.kind -eq "missing function" -and ("$($e.failure.function)" -ne $f.function -or $e.triage -ne "done")) { $e.failure = $f; $e.triage = "todo" }
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
        $samples = (Get-Counter '\PhysicalDisk(*)\Disk Bytes/sec' -SampleInterval 2 -MaxSamples 5 -ErrorAction Stop).CounterSamples | Where-Object { $_.InstanceName -match '\bd:' }
        if (-not $samples) { return $false }
        ($samples | Measure-Object CookedValue -Average).Average -gt 15MB
    } catch { $true }
}
# Before a heavy step: while downloads keep D: busy, wait (up to 15 minutes, then go ahead anyway).
$BusySince = $null
function Wait-Quiet([string]$what) {
    if (-not (Drive-Busy)) { $script:BusySince = $null; return $true }
    Trace "${what}: waiting, the D: drive is busy with downloads"
    if (-not $script:BusySince) { $script:BusySince = Get-Date; Log "${what}: D: is busy (downloads); waiting up to 15 minutes" }
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
        $what = "$($e.failure.kind)" + $(if ($e.failure.function) { ": $($e.failure.function)" } elseif ($e.failure.where) { " at $($e.failure.where)" } else { "" })
        $lines = @("# $game", "", "First test: $($e.note)", "", "Stopped by: $what", "")
        if ($e.failure.line) { $lines += '```', $e.failure.line, '```', "" }
        if (@($e.chain).Count) {
            $lines += "| Missing function | Library | Open PRs found | Tested with | Result |", "|---|---|---|---|---|"
            foreach ($c in @($e.chain)) { $lines += "| $($c.function) | $($c.libraries) | $($c.prs) | $($c.merged) | $(("$($c.result)") -replace '\|', '/') |" }
        } elseif ($e.triage -eq "done") { $lines += "No PR test: only a missing function gets one." }
        elseif ($e.triage -eq "todo") { $lines += "PR test still to come." }
        $sessions = @($e.plays | Where-Object { $_ })
        if ($sessions.Count) { $lines += "", "## Crashes while playing", ""; $lines += @($sessions | ForEach-Object { "- $_" }) }
        $lines += "", "Logs: $($e.gameDir)\crash-logs"
        $lines | Set-Content -LiteralPath (Join-Path $Reports "$game.md") -Encoding utf8
        $found = @()
        if ($e.failure.function) { $found += [pscustomobject]@{ function = $e.failure.function; libraries = ""; prs = ""; result = "" } }
        $found += @($e.chain | Where-Object { $_ -and $_.function })
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
        $e = Get-Entry $game; $e.signature = ""; if ($e.status -in "failed", "encrypted") { $e.status = "new"; $e.note = "" }; Save-State
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
$CReserveGB = 60
New-Item -ItemType Directory -Force $FastRoot | Out-Null
# A game already unpacked on C: or G: (by an earlier run, or by hand): its folder there has an eboot.bin and nothing
# is writing to it.
function Found-Elsewhere([string]$name) {
    foreach ($root in $FastRoot, $Overflow) {
        $p = Join-Path $root $name
        if ((Test-Path -LiteralPath $p) -and -not (Busy $p) -and (Get-ChildItem -LiteralPath $p -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) { return $p }
    }
    $null
}
# Background extractions: exFAT images unpack in their own process, two at a time, while the pipeline goes on with the
# next game (Blu: start the next extraction instead of waiting for one 121 GB image).
$BgExtract = @{}
function Check-BgExtract {
    foreach ($name in @($script:BgExtract.Keys)) {
        $j = $script:BgExtract[$name]
        $new = @(Get-Content -LiteralPath $j.log -ErrorAction SilentlyContinue | Select-Object -Skip $j.shown)
        $j.shown += $new.Count
        foreach ($l in $new) { Trace "  [$name] $l" }
        if (-not $j.proc.HasExited) { continue }
        $script:BgExtract.Remove($name)
        $e = Get-Entry $name
        if ($j.proc.ExitCode -eq 0) {
            $e.extracted = @($e.extracted) + $j.archive; $e.note = ""; $e.signature = ""
            if ($j.where -ne (Join-Path $Dumps $name)) { $e.extractedTo = $j.where }
            Save-State; Event "EXTRACTED ${name}: $($j.archive) -> $($j.where)"
        } else { $e.note = "extract failed ($($j.proc.ExitCode))"; $e.signature = ""; Save-State; Event "EXTRACT FAILED ${name}: $($j.archive) (exit $($j.proc.ExitCode))" }
    }
}
function Free-GB([string]$path) { try { (Get-PSDrive ($path.Substring(0, 1))).Free / 1GB } catch { 0 } }
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
        if ($line -notmatch '^\s*(retest|redo|rebuild)\s+(.+?)\s*$') { continue }
        $what, $game = $Matches[1], $Matches[2]
        $name = @($State.Keys | Where-Object { $_ -eq $game }) + @($State.Keys | Where-Object { $_ -like "*$game*" -or (Get-Entry $_).gameDir -like "*$game*" }) | Select-Object -First 1
        if (-not $name) { Log "request '$line': no game matches"; continue }
        $e = Get-Entry $name
        if ($what -eq "retest" -and $e.gameDir) { $e.status = "built"; $e.note = ""; Event "REQUEST $name`: test again" }
        elseif ($what -eq "rebuild") { $e.status = "new"; $e.note = ""; $e.signature = ""; Event "REQUEST $name`: build again (files stay extracted)" }
        elseif ($what -eq "redo") { $State.Remove($name); Event "REQUEST $name`: start over (extract, build, test)" }
        else { Log "request '$line': $name has no game folder to test" }
        Save-State
    }
}
while ($true) {
    if (Test-Path -LiteralPath $PauseFlag) {
        Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
        Trace "paused (Resume in the AnyPS5 Games window, or delete _tools\pause.flag)"
        Start-Sleep 15; continue
    }
    try {
        $didWork = $false
        Apply-Requests
        Move-Retries
        Check-BgExtract
        $free = (Get-PSDrive ($Dumps[0])).Free / 1GB
        # Downloads on G: -> extracted into D:. One archive per pass, like the rest.
        # Also check Chrome downloads folder
        $downloadLocations = @($Downloads)
        if (Test-Path -LiteralPath $ChromeDownloads) {
            $downloadLocations += $ChromeDownloads
        }
        
        foreach ($dl in $downloadLocations) {
            if (Test-Path -LiteralPath $dl) {
                foreach ($d in Get-ChildItem -LiteralPath $dl -Directory | Where-Object { -not $_.Name.StartsWith("_") }) {
                if ($didWork) { break }
                $e = Get-Entry ("download: " + $d.Name)
                $files = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
                if (-not $files -or ($files | Where-Object { $_.Extension -in ".crdownload", ".part", ".partial", ".tmp", ".download" }) -or (Busy $d.FullName)) { continue }
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
        foreach ($root in $Dumps) {
            if (Test-Path -LiteralPath $root) {
                foreach ($d in Get-ChildItem -LiteralPath $root -Directory | Where-Object { -not $_.Name.StartsWith("_") }) {
                    $dl = $root
            if ($didWork) { break }
            if ($focus -and $d.Name -ne $focus) { continue }
            if ($BgExtract.ContainsKey($d.Name)) { continue }   # unpacking in the background
            $e = Get-Entry $d.Name
            if ($e.status -notin "done", "skip") { Trace "checking $($d.Name) ($($e.status)$(if ($e.note) { ': ' + $e.note }))" }
            # A folder marked encrypted or failed gets another try when its files change (Silt was re-downloaded as a
            # new image after being marked encrypted).
            # A build that failed only because D: was full (Scorn, Need for Speed Unbound and MOUSE P.I. on 10-29, while
            # Spider-Man Remastered's 121 GB image unpacked) is built again once there is room; its files stay extracted.
            if ($e.status -eq "failed" -and "$($e.note)" -match "No space left|Errno 28|low disk" -and $free -ge $MinFreeGB) {
                Log "$($d.Name): its build failed for lack of space; D: has $([int]$free) GB free now, building again"
                $e.status = "new"; $e.note = ""; $e.signature = ""; Save-State
            }
            if ($e.status -in "encrypted", "failed" -and (Scan-Due ("sig: " + $d.Name) 10)) {
                $now = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
                $nowSig = "{0}/{1}" -f $now.Count, ($now | Measure-Object Length -Sum).Sum
                if (-not $e.signature) { $e.signature = $nowSig; Save-State }
                elseif ($nowSig -ne $e.signature -and -not ($now | Where-Object { $_.Extension -in ".crdownload", ".part", ".partial", ".tmp", ".download" })) {
                    Log "$($d.Name): its files changed since it was marked $($e.status); trying again"
                    $e.status = "new"; $e.extracted = @(); $e.note = ""; $e.signature = ""; Save-State
                }
            }
            if ($e.status -in "built", "done", "skip", "encrypted", "failed") { continue }   # built: waits for its test launch below
            # A folder still waiting for its download is listed again every 5 minutes, not every minute: listing every
            # file of every waiting folder each pass kept the USB drive busy around the clock.
            if ("$($e.note)" -match "waiting|incomplete|low disk" -and -not (Scan-Due ("files: " + $d.Name) 5)) { continue }
            $files = @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction SilentlyContinue)
            if (-not $files) { continue }
            if ($files | Where-Object { $_.Extension -in ".crdownload", ".part", ".partial", ".tmp", ".download" }) { continue }
            # An archiver (WinRAR by hand, or one left running by a restarted pipeline) is working on this folder:
            # remember it, and use its result afterwards instead of extracting everything again.
            if (Busy $d.FullName) { if ($e.note -ne "being extracted") { $e.note = "being extracted"; Save-State; Log "$($d.Name): an archiver is extracting it; waiting for it to finish" }; continue }
            if ($e.note -eq "being extracted" -and (Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
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
                if ($free -lt $MinFreeGB -and (Free-GB $Overflow) -lt $MinFreeGB) { if ($e.note -ne "low disk") { $e.note = "low disk"; Save-State; Event ("PAUSED $($d.Name): only {0:N0} GB free on D:" -f $free) }; continue }
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
                    if ((Test-Path -LiteralPath $Overflow) -and $freeG - $need -ge $ReserveGB) {
                        $where = Join-Path $Overflow $d.Name
                        if ($e.extractedTo -ne $where) { $e.extractedTo = $where; Save-State; Log ("$($d.Name): D: has room for {0:N0} GB of the {1:N0} it needs; unpacking to G: ({2:N0} GB free)" -f [Math]::Max(0, $free - $ReserveGB), $need, $freeG) }
                    } else {
                        $why = "needs {0:N0} GB; D: has {1:N0} GB free, G: {2:N0} GB" -f $need, $free, $freeG
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
                if ($a.Extension -eq ".exfat") {
                    if ($BgExtract.Count -ge 2) { continue }   # two at a time
                    $out = Join-Path $where ([IO.Path]::GetFileNameWithoutExtension($a.Name))
                    New-Item -ItemType Directory -Force $out | Out-Null
                    $log = Join-Path $Tools ("extract-" + (Safe-Name $d.Name) + ".log")
                    $proc = Start-Process python -ArgumentList "-I", "`"$ExfatTool`"", "`"$($a.FullName)`"", "`"$out`"" -RedirectStandardOutput $log -WindowStyle Hidden -PassThru
                    try { $proc.PriorityClass = "BelowNormal" } catch { }
                    $BgExtract[$d.Name] = @{ proc = $proc; archive = $a.Name; where = $where; log = $log; shown = 0 }
                    $e.note = "being extracted (background, to $where)"; Save-State
                    Event "EXTRACTING $($d.Name): $($a.Name) -> $where, in the background"
                    continue
                }
                Log "$($d.Name): extracting $($a.Name)"
                $code = Extract $a $where; $didWork = $true
                if ($code -eq 0) { $e.extracted = @($e.extracted) + $a.Name; $e.note = ""; $e.signature = ""; Save-State; Event "EXTRACTED $($d.Name): $($a.Name)" }
                elseif (Get-ChildItem -LiteralPath $where -Recurse -File -Filter "eboot.bin" -Force -ErrorAction SilentlyContinue | Select-Object -First 1) {
                    # Password-protected archives (encrypted headers, e.g. Octopath Traveler 0) cannot be opened here,
                    # but Blu already extracted them with WinRAR: use those files.
                    $e.extracted = @($e.extracted) + $a.Name; $e.note = "used the files already extracted (archive could not be opened, exit $code)"; $e.signature = ""; Save-State
                    Event "EXTRACTED $($d.Name): $($a.Name) could not be opened (password?); using the game files already extracted next to it"
                }
                else { $e.note = "extract failed ($code)"; $e.signature = ""; Save-State; Event "EXTRACT FAILED $($d.Name): $($a.Name) (exit $code); will retry when the folder changes" }
                break
            }

            # Plan and build.
            $source = if ($e.extractedTo -and (Test-Path -LiteralPath $e.extractedTo)) { $e.extractedTo } else { $d.FullName }  # unpacked on C: or G:
            $plan = Plan-Game $source
            # Only a cover picture (or other loose files) so far: the download has not arrived yet. Keep waiting.
            if (-not $plan.ok -and $plan.reason -eq "no eboot.bin found" -and @($e.extracted | Where-Object { $_ }).Count) {
                # Everything is extracted and still no game: e.g. only a console install package (.pkg).
                $pkg = Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Filter *.pkg -Force -ErrorAction SilentlyContinue | Select-Object -First 1
                $e.status = "failed"; $e.note = $(if ($pkg) { "only a console install package ($($pkg.Name)); needs a release with the game's files extracted" } else { "no eboot.bin after extracting" })
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
            $b = Build-Game $source $plan; $didWork = $true
            if (-not $b.ok) { $e.status = "failed"; $e.note = $b.reason; Save-State; Event "BUILD FAILED $($plan.title): $($b.reason)"; continue }
            $warn = if ($b.warnings) { " (warnings: " + ($b.warnings -join "; ") + ")" } else { "" }
            $e.status = "built"; $e.gameDir = $b.gameDir; $e.note = ""; Save-State
            Event "BUILT $($plan.title) ($($plan.titleId)) -> $($b.gameDir)$warn"
            Retire-Archives $d.FullName $e
        }
        # Test-launch built games, one at a time, never while a game is being played.
        foreach ($name in @($State.Keys)) {
            $e = Get-Entry $name
            # Catch up on archives a built game still has (a stop between building and moving them leaves them).
            if ($e.status -in "built", "done" -and (Test-Path -LiteralPath (Join-Path $Dumps $name))) { Retire-Archives (Join-Path $Dumps $name) $e }
            if ($e.status -ne "built" -or (Game-Running)) { continue }
            Log "${name}: test launch"
            $t = Test-Game $e.gameDir
            $e.status = "done"; $e.note = $t; $didWork = $true
            if ($t -notlike "runs:*") { $e.failure = Read-Failure $e.gameDir; $e.triage = "todo" }
            Save-State; Write-Reports
            Event "TESTED $(Split-Path $e.gameDir -Leaf): $t"
        }
        Watch-Play
        # Slower work, one item per pass and only when nothing new is waiting and no game is being played:
        # a newer AnyPS5, then rechecking games on it, then PR tests for failed games.
        if (-not $didWork -and -not (Game-Running) -and (Wait-Quiet "background work")) {
            Update-AnyPS5
            $recheck = @($State.Keys | Where-Object { (Get-Entry $_).recheck -eq "todo" }) | Select-Object -First 1
            $triage = @($State.Keys | Where-Object { (Get-Entry $_).triage -eq "todo" }) | Select-Object -First 1
            if ($recheck) {
                $e = Get-Entry $recheck
                Log "${recheck}: recheck on AnyPS5 $Base"
                Recheck-Game $recheck $e
                Save-State; Write-Reports
                Event "RECHECKED $(Split-Path $e.gameDir -Leaf): $($e.recheck)"
            } elseif (-not $triage -and -not (Drive-Busy) -and [AutoIdle]::IdleMs() -gt 600000 -and
                      ($play = @($State.Keys | Where-Object { $x = Get-Entry $_; $x.gameDir -and -not $x.played -and ($x.note -like "runs:*" -or $x.status -eq "skip") -and (Test-Path -LiteralPath (Join-Path $x.gameDir "game.json")) }) | Select-Object -First 1)) {
                $e = Get-Entry $play
                Log "${play}: AI play session"
                Play-Game $play $e
                Save-State; Write-Reports
                Event "PLAYED $(Split-Path $e.gameDir -Leaf): $($e.played)"
            } elseif ($triage) {
                $e = Get-Entry $triage
                Log "${triage}: PR test for $($e.failure.kind) $($e.failure.function)"
                Triage-Game $triage $e
                $e.triage = "done"; Save-State; Write-Reports
                $last = @($e.chain) | Select-Object -Last 1
                Event ("TRIAGED $(Split-Path $e.gameDir -Leaf): $($e.failure.kind) $($e.failure.function)" + $(if ($last) { " -> $($last.merged) : $($last.result)" } else { "" }))
            }
        }
    } catch {
        Log "error: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
    }
    # Heartbeat: shows the pipeline is alive between events.
    Set-Content -LiteralPath (Join-Path $Tools "auto-games.alive") -Value (Get-Date -Format "MM-dd HH:mm:ss") -ErrorAction SilentlyContinue
    Trace "pass done; next check in $IntervalSeconds s"
    Start-Sleep $IntervalSeconds
}
