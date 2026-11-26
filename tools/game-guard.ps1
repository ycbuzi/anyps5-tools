# Keeps every game in AnyPS5 Games on the libraries its own build put there (11-11, Blu: "whenever I do go to hit play a
# game, it's actually gonna launch on a proper running one ... it needs to keep the regression of the working game
# state"). PR tests (auto-games.ps1 Test-WithLibraries, gpu-lanes.ps1) swap a PR's libraries into a game's libs\ and put
# the game's own back afterwards; on 11-11 a restore that failed left eight PR #10492 libraries in Divinity Original
# Sin 2, and the game stopped at start (libSceUserService.prx: procedure not found).
#
# For each libs\*.prx this compares size and time with what build-game.ps1 copied in: mods\libs\<name>.prx when the game
# has that override, else <the game's AnyPS5 build>\core\libs\libs\<name>.prx (the build named in info.txt). A file that
# differs is copied back, a .prx neither of them has is removed, and leftover .lane-original / .pipeline-original
# backups go. A game that is running, or whose backups are under 30 minutes old (a PR test is using it), is left alone,
# unless -Force: then a GPU-lane test of that game is stopped first; any other process running from it -> exit 2.
#   game-guard.ps1 -GameDir <dir> -Force    the app's Play button, before the game starts
#   game-guard.ps1 -All                     every game (the pipeline watchdog, each pass; gpu-lanes.ps1 at start)
#   add -Check to only report what it would change
param([string]$GameDir, [switch]$All, [switch]$Force, [switch]$Check)
$ErrorActionPreference = "Continue"
$Games = if (Test-Path -LiteralPath "D:\AnyPS5 Games\_tools") { "D:\AnyPS5 Games" } else { Split-Path $PSScriptRoot }
$Tools = $PSScriptRoot
$LogFile = Join-Path $Games "_reports\game-guard.log"
$settings = try { Get-Content -LiteralPath (Join-Path $Tools "settings.json") -Raw | ConvertFrom-Json } catch { $null }
function Say($text) { $line = "$(Get-Date -Format 'MM-dd HH:mm:ss') $(if ($Check) { '[check only] ' })$text"; if (-not $Check) { Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8 }; Write-Host $line }

function Build-Root($dir) {
    $info = Join-Path $dir "info.txt"
    if (Test-Path -LiteralPath $info) {
        $m = Select-String -LiteralPath $info -Pattern '^AnyPS5:.*\(from (.+?)\)\s*$' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($m -and (Test-Path -LiteralPath (Join-Path $m.Matches[0].Groups[1].Value "core\libs\libs"))) { return $m.Matches[0].Groups[1].Value }
    }
    $cfg = try { Get-Content -LiteralPath (Join-Path $dir "game.json") -Raw | ConvertFrom-Json } catch { $null }
    if ($cfg -and $cfg.anyps5Build) { return $cfg.anyps5Build }
    if ($settings) { return $settings.anyps5Build }
    $null
}

function Users-Of($dir, $real) {
    @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path.StartsWith($dir + "\", [StringComparison]::OrdinalIgnoreCase) -or $_.Path.StartsWith($real + "\", [StringComparison]::OrdinalIgnoreCase)) })
}

function Guard-Game([string]$dir) {
    $dir = $dir.TrimEnd('\')
    $libs = Join-Path $dir "libs"
    if (-not (Test-Path -LiteralPath (Join-Path $dir "game.json")) -or -not (Test-Path -LiteralPath $libs)) { return 0 }
    $item = Get-Item -LiteralPath $dir
    $real = if ($item.LinkType -eq "Junction") { "$(@($item.Target)[0])".TrimEnd('\') } else { $dir }
    $name = Split-Path $dir -Leaf
    $backups = @(Get-ChildItem -LiteralPath $libs -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.(lane|pipeline)-original$' })
    $users = Users-Of $dir $real
    if (-not $Force) {
        if ($users.Count) { return 0 }
        if (@($backups | Where-Object { $_.CreationTime -gt (Get-Date).AddMinutes(-30) }).Count) { return 0 }
    } elseif ($users.Count) {
        $lanePids = @(Get-ChildItem -LiteralPath (Join-Path $Tools "live\lanes") -Filter *.json -ErrorAction SilentlyContinue | ForEach-Object { try { (Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json).pid } catch { } })
        foreach ($p in $users) {
            if ($lanePids -notcontains $p.Id) { Say "${name}: not checked, $($p.ProcessName) ($($p.Id)) is running from it" | Out-Null; exit 2 }
            Say "${name}: stopping the GPU-lane test running it ($($p.Id)) so it can be played" | Out-Null
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            try { [void]$p.WaitForExit(15000) } catch { }
        }
        Start-Sleep -Seconds 2
    }
    $root = Build-Root $dir
    $ref = if ($root) { Join-Path $root "core\libs\libs" } else { $null }
    if (-not $ref -or -not (Test-Path -LiteralPath $ref)) { Say "${name}: not checked, its AnyPS5 build ($root) is gone" | Out-Null; return 0 }
    $mods = Join-Path $dir "mods\libs"
    $fixed = @(); $removed = @(); $failed = @()
    foreach ($f in @(Get-ChildItem -LiteralPath $libs -Filter *.prx -File -ErrorAction SilentlyContinue)) {
        $want = Join-Path $mods $f.Name
        if (-not (Test-Path -LiteralPath $want)) { $want = Join-Path $ref $f.Name }
        if (-not (Test-Path -LiteralPath $want)) {
            if ($Check) { $removed += $f.BaseName; continue }
            try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed += $f.BaseName } catch { $failed += $f.BaseName }
            continue
        }
        $w = Get-Item -LiteralPath $want
        if ($w.Length -eq $f.Length -and [Math]::Abs(($w.LastWriteTimeUtc - $f.LastWriteTimeUtc).TotalSeconds) -lt 3) { continue }
        if ($Check) { $fixed += $f.BaseName; continue }
        $ok = $false
        for ($try = 0; $try -lt 5 -and -not $ok; $try++) {
            try { Copy-Item -LiteralPath $want -Destination $f.FullName -Force -ErrorAction Stop; $ok = $true } catch { Start-Sleep -Seconds 2 }
        }
        if ($ok) { $fixed += $f.BaseName } else { $failed += $f.BaseName }
    }
    foreach ($m in @(Get-ChildItem -LiteralPath $ref -Filter *.prx -File)) {
        if (-not (Test-Path -LiteralPath (Join-Path $libs $m.Name))) {
            if ($Check) { $fixed += $m.BaseName; continue }
            try { Copy-Item -LiteralPath $m.FullName -Destination (Join-Path $libs $m.Name) -Force -ErrorAction Stop; $fixed += $m.BaseName } catch { $failed += $m.BaseName }
        }
    }
    if (-not $failed.Count -and -not $Check) { foreach ($b in $backups) { Remove-Item -LiteralPath $b.FullName -Force -ErrorAction SilentlyContinue } }
    if ($fixed.Count -or $removed.Count -or $failed.Count) {
        $parts = @()
        if ($fixed.Count) { $parts += "put back its own $($fixed -join ', ')" }
        if ($removed.Count) { $parts += "removed $($removed -join ', ') (not in its build)" }
        if ($failed.Count) { $parts += "COULD NOT fix $($failed -join ', ') (in use?)" }
        Say "${name}: $($parts -join '; ') (build $(Split-Path $root -Leaf))"
    }
    if ($failed.Count) { return -1 }
    $fixed.Count + $removed.Count
}

if ($All) {
    $total = 0
    foreach ($d in @(Get-ChildItem -LiteralPath $Games -Directory | Where-Object { $_.Name -notlike "_*" })) { $n = Guard-Game $d.FullName; if ($n -gt 0) { $total += $n } }
    exit 0
}
if (-not $GameDir) { "usage: game-guard.ps1 -GameDir <dir> [-Force] | -All"; exit 1 }
$n = Guard-Game $GameDir
if ($n -lt 0) { exit 3 }
# 11-16 shader cache SSD tier: the Play button records the play and moves the game's shader_cache to C: first when
# that takes under a minute (bigger caches move in the idle prewarm). Never blocks the start: 90 s at most, errors ignored.
if ($Force -and -not $Check -and (Test-Path -LiteralPath (Join-Path $Tools "shader_tier.py"))) {
    try {
        $py = "%USERPROFILE%\AppData\Local\Programs\Python\Python314\python.exe"
        if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
        $si = New-Object System.Diagnostics.ProcessStartInfo $py, ('-I "' + (Join-Path $Tools "shader_tier.py") + '" play --game "' + $GameDir.TrimEnd('\') + '"')
        $si.UseShellExecute = $false; $si.CreateNoWindow = $true
        $tier = [System.Diagnostics.Process]::Start($si)
        if (-not $tier.WaitForExit(90000)) { Say "$(Split-Path $GameDir -Leaf): shader tier still busy after 90 s; starting the game anyway" | Out-Null }
    } catch { }
}
exit 0
