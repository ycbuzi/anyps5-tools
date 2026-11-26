# Builds the named AnyPS5 libraries from one pull request merged onto -Base (with -NoPr: -Base alone, for the other
# side of an A/B), in its own worktree and build folder
# (D:\AnyPS5\wt-prtest, D:\AnyPS5\build\prtest), so it never touches the pipeline's wt-pr. The .prx files land in
# -OutDir\pr<N>-<base>\ for crash_ab.ps1 -LibDir or a test copy. Holds _tools\live\gpu-busy.flag through gpu_flag.ps1
# (a build skews any game test running beside it). Nothing is pushed.
param([int]$Number = 0, [switch]$NoPr, [Parameter(Mandatory)][string]$Base, [Parameter(Mandatory)][string]$Libs, [string]$OutDir = "D:\AnyPS5 Games\_reports\crash-ab")
$ErrorActionPreference = "Continue"
. D:\AnyPS5-tools\env.ps1 | Out-Null
. (Join-Path $PSScriptRoot "gpu_flag.ps1")
$Tree = "D:\AnyPS5\wt-prtest"
$Build = "D:\AnyPS5\build\prtest"
function Run-Git { & git.exe -C $Tree @args 2>&1; if ($LASTEXITCODE) { throw "git $($args -join ' ') failed (exit $LASTEXITCODE)" } }
Enter-GpuFlag "building $(if ($NoPr) { $Base } else { "PR #$Number" }) libraries"
try {
    if (-not (Test-Path -LiteralPath $Tree)) { & git -C D:\AnyPS5 worktree add --detach $Tree $Base 2>&1 | Select-Object -Last 1 }
    if (-not $NoPr -and $Number -le 0) { throw "give -Number, or -NoPr for the base alone" }
    if (-not $NoPr) { Run-Git fetch -q origin "+refs/pull/$Number/head:refs/remotes/pr-claude/$Number" | Out-Null }
    Run-Git checkout -q -f --detach $Base | Out-Null
    if (-not $NoPr) { Run-Git -c user.name=blu -c user.email=user@example.com merge --no-edit -q "refs/remotes/pr-claude/$Number" | Out-Null }
    Run-Git submodule update --init --recursive --jobs 4 | Out-Null
    $commit = (Run-Git rev-parse --short=10 HEAD | Select-Object -First 1).Trim()
    $label = if ($NoPr) { "main-$Base" } else { "pr$Number-$Base" }
    if ($NoPr) { "$Base alone = $commit" } else { "PR #$Number merged onto $Base = $commit" }
    & cmake -S $Tree -B $Build -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE) { throw "cmake failed (exit $LASTEXITCODE)" }
    $dest = Join-Path $OutDir $label
    New-Item -ItemType Directory -Force $dest | Out-Null
    foreach ($lib in $Libs -split '[,; ]+' | Where-Object { $_ }) {
        $p = Start-Process -FilePath ninja -ArgumentList "-C", $Build, "-j", "8", "core/libs/libs/$lib.prx" -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $dest "build-$lib.txt")
        $null = $p.Handle
        $p.PriorityClass = "BelowNormal"
        while (-not $p.WaitForExit(300000)) { Touch-GpuFlag; "still building $lib ($(Get-Date -Format HH:mm))" }
        if ($p.ExitCode -ne 0) { Get-Content (Join-Path $dest "build-$lib.txt") -Tail 5; throw "building $lib.prx failed (ninja exit $($p.ExitCode))" }
        Copy-Item -LiteralPath (Join-Path $Build "core\libs\libs\$lib.prx") -Destination $dest -Force
        "built $lib.prx"
    }
    "$commit $(if ($NoPr) { "$Base alone" } else { "PR #$Number onto $Base" })" | Set-Content -LiteralPath (Join-Path $dest "COMMIT.txt") -Encoding utf8
    "RESULT $label libraries in $dest"
    $ok = $true
} catch { "FAILED: $_"; $ok = $false } finally { Exit-GpuFlag }
exit $(if ($ok) { 0 } else { 1 })
