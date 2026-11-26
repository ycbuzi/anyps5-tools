# Windows toolchain matrix for anyps5-win (11-08), the Windows twin of azlinux's matrix.sh. Relinker-only build and one
# ctest run per toolchain in C:\x64win\tc\<name>\build; main baselines cached per main commit and toolchain.
# Toolchains: WinLibs GCC 13/14 UCRT+MSVCRT, llvm-mingw Clang 18/19/20 (C:\tools\tc, see toolchains.ps1) and the
# native GCC 15 UCRT inside a Server Core ltsc2025 container (process isolation). The native GCC 15 UCRT run is prtest.ps1 itself.
# usage: tcmatrix.ps1 baseline <main>      (main checked out)
#        tcmatrix.ps1 pr <N> <main>        (merged PR checked out; prints one report line per toolchain)
param([string]$Mode, [string]$A1, [string]$A2)
$ErrorActionPreference = 'Continue'
$W = 'C:\x64win'; $T = "$W\tc"; $src = "$W\AnyPS5"; $L = "$W\logs\tc"
New-Item -ItemType Directory -Force $T, $L | Out-Null
$base = 'C:\tools\cmake\bin;C:\tools\Git\cmd;C:\tools\Python314;C:\tools\mingw64\bin'
$orig = $env:PATH
$list = @()
foreach ($d in Get-ChildItem C:\tools\tc -Directory -ErrorAction SilentlyContinue) {
    if (-not (Test-Path "$($d.FullName)\.ok")) { continue }
    $clang = $d.Name -like 'clang*'
    $list += [pscustomobject]@{ name = $d.Name; bin = (Get-Content "$($d.FullName)\.ok"); cc = $(if ($clang) { 'clang' } else { 'gcc' }); cxx = $(if ($clang) { 'clang++' } else { 'g++' }) }
}
$docker = 'C:\Program Files\Docker\docker.exe'
if ((Test-Path $docker) -and (& $docker image ls -q mcr.microsoft.com/windows/servercore:ltsc2025 2>$null)) { $list += [pscustomobject]@{ name = 'servercore2025'; bin = ''; cc = 'gcc'; cxx = 'g++' } }
function Fails($f) { if (-not (Test-Path $f)) { return }; $in = $false
    foreach ($l in Get-Content $f) { if ($l -match '^The following tests FAILED:') { $in = $true; continue }
        if ($in -and $l -match '^\s+\d+ - (.+?)( \(.*\))?$') { $Matches[1] } elseif ($in -and $l -notmatch '^\s') { $in = $false } } }
function Summ($f) { if (Test-Path $f) { ((Select-String -Path $f -Pattern 'tests passed' | Select-Object -Last 1).Line -replace '^\d+% tests passed, ', '') } }
function RunTc($t, $tag) {
    $d = "$T\$($t.name)"; New-Item -ItemType Directory -Force "$d\tmp" | Out-Null
    Remove-Item "$d\build.rc", "$d\build.log", "$d\ctest.log" -ErrorAction SilentlyContinue
    Set-Content "$W\tc-now.txt" "$tag $($t.name): building + ctest"
    $cfg = "-S {0} -B {1}\build -G Ninja -DCMAKE_BUILD_TYPE=Release -DANYPS5_RELINKER_ONLY=ON -DBUILD_TESTING=ON -DCMAKE_C_COMPILER={2} -DCMAKE_CXX_COMPILER={3}"
    if ($t.name -eq 'servercore2025') {
        $inner = "`$env:PATH='$base;'+`$env:PATH; `$env:TEMP='C:\w\tmp'; `$env:TMP='C:\w\tmp'; Set-Location C:\w; g++ --version | Select-Object -First 1 | Set-Content cc.txt; " +
            "cmake $($cfg -f 'C:\src', 'C:\w', 'gcc', 'g++') *> configure.log; if (`$LASTEXITCODE -ne 0) { 'configure' | Set-Content build.rc; Copy-Item configure.log build.log } " +
            "else { ninja -C C:\w\build -j2 -k 0 *> build.log; `$LASTEXITCODE | Set-Content build.rc; ctest --test-dir C:\w\build -j2 --timeout 300 *> ctest.log }"
        & $docker run --rm --isolation=process --cpus 2 -v C:\tools:C:\tools -v "${src}:C:\src" -v "${d}:C:\w" mcr.microsoft.com/windows/servercore:ltsc2025 powershell -NoProfile -Command $inner *> "$d\docker.log"
    } else {
        $env:PATH = "$($t.bin);$base;$orig"; $env:TEMP = "$d\tmp"; $env:TMP = "$d\tmp"
        (& $t.cxx --version | Select-Object -First 1) | Set-Content "$d\cc.txt"
        & cmake ($cfg -f $src, $d, $t.cc, $t.cxx).Split(' ') *> "$d\configure.log"
        if ($LASTEXITCODE -ne 0) { Set-Content "$d\build.rc" 'configure'; Copy-Item "$d\configure.log" "$d\build.log" }
        else { & ninja -C "$d\build" -j2 -k 0 *> "$d\build.log"; Set-Content "$d\build.rc" $LASTEXITCODE; & ctest --test-dir "$d\build" -j2 --timeout 300 *> "$d\ctest.log" }
        $env:PATH = $orig
    }
    Copy-Item "$d\build.log" "$L\$tag-$($t.name)-build.log" -ErrorAction SilentlyContinue
    Copy-Item "$d\ctest.log" "$L\$tag-$($t.name)-ctest.log" -ErrorAction SilentlyContinue
}
foreach ($t in $list) {
    $d = "$T\$($t.name)"
    if ($Mode -eq 'baseline') {
        $b = "$T\main-$A1-$($t.name)"
        if (Test-Path "$b.rc") { continue }
        $t0 = Get-Date; RunTc $t "main-$A1"
        Copy-Item "$d\build.rc" "$b.rc" -ErrorAction SilentlyContinue; if (-not (Test-Path "$b.rc")) { Set-Content "$b.rc" 'none' }
        Fails "$d\ctest.log" | Set-Content "$b.failures"
        Add-Content "$W\tc-status.txt" "$(Get-Date -Format 'MM-dd HH:mm') main $A1 $($t.name): build rc $(Get-Content "$b.rc") in $([int]((Get-Date) - $t0).TotalSeconds)s; $(Summ "$d\ctest.log")"
    } else {
        $b = "$T\main-$A2-$($t.name)"
        $mrc = if (Test-Path "$b.rc") { "$(Get-Content "$b.rc")" } else { '?' }
        $t0 = Get-Date; RunTc $t "pr-$A1"; $s = [int]((Get-Date) - $t0).TotalSeconds
        $rc = if (Test-Path "$d\build.rc") { "$(Get-Content "$d\build.rc")" } else { 'none' }
        $mf = @(Get-Content "$b.failures" -ErrorAction SilentlyContinue | Where-Object { $_ })
        $new = @(Fails "$d\ctest.log" | Where-Object { $mf -notcontains $_ })
        $ver = "$(Get-Content "$d\cc.txt" -ErrorAction SilentlyContinue)" -replace '^.*?(\d+\.\d+\.\d+).*$', '$1'
        $bt = if ($rc -eq '0') { 'ok' } elseif ($mrc -ne '0') { "FAILED (rc $rc; main also rc $mrc)" } else { "FAILED (rc $rc; main ok)" }
        $nt = if ($new) { $new -join ' ' } else { 'none' }
        "| $($t.name) | $ver | $bt | $nt ($(Summ "$d\ctest.log")) | ${s}s |"
        Add-Content "$W\tc-status.txt" "$(Get-Date -Format 'MM-dd HH:mm') #$A1 $($t.name): build $bt, new failures $nt, ${s}s"
    }
}
Remove-Item "$W\tc-now.txt" -ErrorAction SilentlyContinue
