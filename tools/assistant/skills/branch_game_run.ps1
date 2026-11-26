# Builds one AnyPS5 commit (any fork branch head, e.g. a Quest diag build) for Windows in its own worktree and build
# folder (D:\AnyPS5\wt-branch, D:\AnyPS5\build\branch), converts a COPY of one game with it into
# C:\AnyPS5-work\branch-runs\<Tag>\ (the game folder is never touched), runs that copy for -Seconds with -EnvVars set,
# and leaves the log and a summary in _reports\branch-runs\<Tag>\. Holds the PC lock (Use-Pc) for build and run.
# The test copy is deleted at the end unless -Keep.
# usage: branch_game_run.ps1 -Commit 1f613a635f -GameFolder "D:\AnyPS5 Games\TERRIFIER The ArtCade Game (PPSA26362)"
#          -Tag terrifier-fmt12 -Seconds 150 -EnvVars "APS5_TRACE_CALLS=all;APS5_TRACE_STRINGS=1" -RelinkExtra "--trace-imports"
# Start it detached (detach.ps1), never as a child of a Claude shell.
param(
    [Parameter(Mandatory)][string]$Commit,
    [Parameter(Mandatory)][string]$GameFolder,
    [Parameter(Mandatory)][string]$Tag,
    [int]$Seconds = 150,
    [string]$EnvVars = "",
    [string]$RelinkExtra = "",
    [string]$Remote = "fork",
    [switch]$Keep
)
$ErrorActionPreference = "Continue"
. D:\AnyPS5-tools\env.ps1 | Out-Null
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
$Tree = "D:\AnyPS5\wt-branch"
$Build = "D:\AnyPS5\build\branch"
$Copy = "C:\AnyPS5-work\branch-runs\$Tag"
$Out = "C:\AnyPS5 Games\_reports\branch-runs\$Tag"
New-Item -ItemType Directory -Force $Out | Out-Null
$Summary = Join-Path $Out "summary.txt"
function Say([string]$m) { $l = (Get-Date -Format "MM-dd HH:mm:ss ") + $m; $l; Add-Content -LiteralPath $Summary -Value $l -Encoding utf8 }
function Run-Git { & git.exe -C $Tree @args 2>&1; if ($LASTEXITCODE) { throw "git $($args -join ' ') failed (exit $LASTEXITCODE)" } }

$ok = $false
Use-Pc "branch run $Tag ($Commit)" 40 {
    try {
        Say "start: $Commit on $(Split-Path $GameFolder -Leaf), $Seconds s, env '$EnvVars', relink extra '$RelinkExtra'"
        & git -C D:\AnyPS5 fetch -q $Remote 2>&1 | Select-Object -Last 1
        if (-not (Test-Path -LiteralPath $Tree)) { & git -C D:\AnyPS5 worktree add --detach $Tree $Commit 2>&1 | Select-Object -Last 1 }
        # A run stopped in the middle of a git step leaves index.lock files behind (11-14: 3rdparty/icu, after a pause
        # killed the run) and every later submodule update fails; remove ones older than 10 minutes while no git runs.
        if (-not (Get-Process git -ErrorAction SilentlyContinue)) {
            Get-ChildItem -LiteralPath "D:\AnyPS5\.git\worktrees\wt-branch" -Recurse -Filter index.lock -ErrorAction SilentlyContinue |
                Where-Object { ((Get-Date) - $_.LastWriteTime).TotalMinutes -gt 10 } | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force; Say "removed a stale $($_.FullName)" }
        }
        Run-Git checkout -q -f --detach $Commit | Out-Null
        Run-Git submodule update --init --recursive --force --jobs 4 | Out-Null
        $full = (Run-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Say "checked out $full"
        & cmake -S $Tree -B $Build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF 2>&1 | Select-Object -Last 2
        if ($LASTEXITCODE) { throw "cmake failed (exit $LASTEXITCODE)" }
        foreach ($target in "relinker", "libs") {
            $log = Join-Path $Out "build-$target.txt"
            $p = Start-Process -FilePath ninja -ArgumentList "-C", $Build, $target -NoNewWindow -PassThru -RedirectStandardOutput $log
            $null = $p.Handle
            try { $p.PriorityClass = "BelowNormal" } catch { }
            while (-not $p.WaitForExit(300000)) { Touch-GpuFlag }
            if ($p.ExitCode -ne 0) { Get-Content $log -Tail 5; throw "building $target failed (ninja exit $($p.ExitCode)); see $log" }
            Say "built $target"
        }

        if (Test-Path -LiteralPath $Copy) { & cmd.exe /d /c "rd /s /q `"$Copy`"" }
        New-Item -ItemType Directory -Force $Copy | Out-Null
        $cfg = Get-Content (Join-Path $GameFolder "game.json") -Raw | ConvertFrom-Json
        $cfg | Add-Member -NotePropertyName anyps5Build -NotePropertyValue $Build -Force
        $cfg | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Copy "game.json") -Encoding utf8
        $mods = Join-Path $GameFolder "mods\patches"
        if (Test-Path -LiteralPath $mods) { New-Item -ItemType Directory -Force (Join-Path $Copy "mods") | Out-Null; Copy-Item -LiteralPath $mods -Destination (Join-Path $Copy "mods") -Recurse }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\AnyPS5 Games\_tools\build-game.ps1" -GameDir $Copy *> (Join-Path $Out "build-game.txt")
        if ($LASTEXITCODE) { throw "build-game.ps1 failed; see build-game.txt" }
        $exeName = if ($cfg.exeName) { $cfg.exeName } else { $cfg.title }
        $exe = Join-Path $Copy "$exeName.exe"
        if ($RelinkExtra) {
            $eboot = if ($cfg.eboot) { $cfg.eboot } else { "eboot.bin" }
            $dump = if (Test-Path -LiteralPath (Join-Path $cfg.dump $eboot)) { $cfg.dump } else { Join-Path $Copy "app0" }
            $moduleArgs = @(); foreach ($m in @($cfg.moduleDirs)) { if ($m -and $m.Trim('/', '\') -notin "prx", "sce_module", "sce_modules") { $moduleArgs += @("--module-dir", $m) } }
            if ($cfg.skipSceModule) { $moduleArgs += "--skip-sce-module" }
            $patchArgs = @("--no-patches")
            $merged = Get-ChildItem -LiteralPath $Copy -Recurse -Filter mods.patches.toml -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($merged) { $patchArgs = @("--patches", $merged.FullName) }
            Push-Location -LiteralPath $dump
            try { & (Join-Path $Build "core\relinker\relinker.exe") --windows --windows-gui --registry @($RelinkExtra -split ' ' | Where-Object { $_ }) @patchArgs @moduleArgs $eboot $exe *> (Join-Path $Out "relink-extra.txt") }
            finally { Pop-Location }
            if ($LASTEXITCODE) { throw "relinking with '$RelinkExtra' failed; see relink-extra.txt" }
            Say "relinked with $RelinkExtra"
        }

        $logFile = Join-Path $Out "run.log"
        $env:ANYPS5_LOG_FILE = $logFile
        foreach ($kv in $EnvVars -split ';' | Where-Object { $_ -match '=' }) { $k, $v = $kv -split '=', 2; Set-Item -Path "env:$($k.Trim())" -Value $v }
        $start = Get-Date
        $proc = & "C:\AnyPS5 Games\_tools\start-game.ps1" -Exe $exe -Dir $Copy
        if (-not $proc.WaitForExit($Seconds * 1000)) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; $how = "stopped after $Seconds s" }
        else { $how = "exited 0x{0:X8} after {1:N0} s" -f $proc.ExitCode, ((Get-Date) - $start).TotalSeconds }
        Say "run: $how"
        $size = if (Test-Path -LiteralPath $logFile) { (Get-Item -LiteralPath $logFile).Length } else { 0 }
        Say "log: $logFile ($size bytes)"
        Select-String -LiteralPath $logFile -Pattern 'FATAL|Unable to find type|presents' -ErrorAction SilentlyContinue | Select-Object -First 3 | ForEach-Object { Say ("  " + $_.Line.Substring(0, [Math]::Min(200, $_.Line.Length))) }
        $script:ok = $true
    } catch { Say "FAILED: $_" }
    finally {
        if (-not $Keep -and (Test-Path -LiteralPath $Copy)) { & cmd.exe /d /c "rd /s /q `"$Copy`""; Say "removed the test copy" }
    }
}
Say $(if ($ok) { "RESULT ok" } else { "RESULT failed" })
exit $(if ($ok) { 0 } else { 1 })
