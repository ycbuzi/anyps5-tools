# anyps5-win (Azure Spot B2as_v2, Windows Server 2025, AMD EPYC 7763: AVX2, no GPU) offline Windows PR runner (11-08).
# Modelled on azlinux's x64_prtest.sh. For each PR: fetch it to refs/remotes/pr/N, check that head is really there,
# merge it into origin/main, build the relinker-only tree with WinLibs GCC 15.2, run ctest twice and compare with
# main's failures. Writes C:\x64win\prtests\<N>.md with a draft comment. Nothing is pushed or posted.
# usage: prtest.ps1 <PR>...      test these PRs
#        prtest.ps1 -Auto        pick up to 3 new open PRs (head < 2 days old): relinker/tools/cmake PRs on every runner,
#                                other code PRs (full build, at most 1 per run) only where shard.txt "i/N" matches PR % N;
#                                docs-only PRs are skipped. With nothing new, selfstop.ps1 deallocates an on-demand VM.
param([switch]$Auto, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Prs)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$W = 'C:\x64win'; $repo = 'C:\x64win\AnyPS5'; $build = "$repo\build-prtest"
$logs = "$W\logs"; $out = "$W\prtests"; $status = "$W\status.md"
New-Item -ItemType Directory -Force $logs, $out, "$W\tmp" | Out-Null
try { (Get-Process -Id $PID).PriorityClass = 'BelowNormal' } catch {}
try { $lock = [IO.File]::Open("$W\.prtest.lock", 'OpenOrCreate', 'ReadWrite', 'None') } catch { Write-Output 'busy'; exit 3 }
$env:PATH = 'C:\tools\mingw64\bin;C:\tools\cmake\bin;C:\tools\Git\cmd;C:\tools\Git\usr\bin;C:\tools\Python314;' + $env:PATH
$env:SSL_CERT_FILE = 'C:\tools\Git\mingw64\etc\ssl\certs\ca-bundle.crt'
$env:TEMP = "$W\tmp"; $env:TMP = "$W\tmp"; $env:TMPDIR = "$W\tmp"
function Say($m) { $l = "$(Get-Date -Format 'MM-dd HH:mm:ss') $m"; Add-Content $status $l; Write-Output $l }
function G { & git -C $repo @args 2>&1 }
function Failures($f) {
    if (-not (Test-Path $f)) { return }
    $in = $false
    foreach ($l in Get-Content $f) {
        if ($l -match '^The following tests FAILED:') { $in = $true; continue }
        if ($in -and $l -match '^\s+\d+ - (.+?)( \(.*\))?$') { $Matches[1] } elseif ($in -and $l -notmatch '^\s') { $in = $false }
    }
}
function Summary($f) { if (Test-Path $f) { (Select-String -Path $f -Pattern 'tests passed' | Select-Object -Last 1).Line } }
function Dir($k) { if ($k -eq 'full') { "$repo\build-full" } else { $build } }
function Configure($k) {
    $ro = if ($k -eq 'full') { 'OFF' } else { 'ON' }
    & cmake -S $repo -B (Dir $k) -G Ninja -DCMAKE_BUILD_TYPE=Release "-DANYPS5_RELINKER_ONLY=$ro" -DBUILD_TESTING=ON -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ *> "$logs\configure-$k.log"
}
function BuildAll($log, $k) { & ninja -C (Dir $k) -j2 -k 0 *> $log; return $LASTEXITCODE }
function RunCtest($log, $k) { & ctest --test-dir (Dir $k) -j2 --timeout 300 *> $log }
function Kind($ref) {
    $files = @(G log --format= --name-only --no-merges "origin/main..$ref" | Where-Object { $_ })
    if (-not $files) { return '' }
    if (-not ($files | Where-Object { $_ -notmatch '^docs/|\.md$' })) { return 'docs' }
    if ($files | Where-Object { $_ -notmatch '^(core/relinker/|tools/|cmake/)|^CMakeLists\.txt$' }) { return 'full' }
    'relinker'
}
$shard = if (Test-Path "$W\shard.txt") { @((Get-Content "$W\shard.txt" -TotalCount 1) -split '/') } else { $null }
function Mine($n, $k) {
    if ($k -eq 'relinker') { return $true }
    if ($k -ne 'full' -or -not $shard) { return $false }
    ([int]$n % [int]$shard[1]) -eq [int]$shard[0]
}
function BaseFile($k) { if ($k -eq 'full') { "$W\main-$main-full.failures" } else { "$W\main-$main.failures" } }
function BaseLog($k) { if ($k -eq 'full') { "$logs\main-$main-full" } else { "$logs\main-$main" } }
function EnsureBase($k) {
    $b = BaseFile $k; $bl = BaseLog $k
    if (Test-Path $b) { return }
    Say "main ${main}: $k baseline build and test"
    G checkout -q -f --detach origin/main | Out-Null
    Configure $k
    $t0 = Get-Date
    BuildAll "$bl-build.log" $k | Out-Null
    RunCtest "$bl-ctest.log" $k
    Failures "$bl-ctest.log" | Set-Content $b
    Say "main ${main}: $k built+tested in $([int]((Get-Date) - $t0).TotalSeconds)s; $(Summary "$bl-ctest.log"); failing: $((Get-Content $b) -join ' ')"
}

G fetch -q origin | Out-Null
if ($Auto) {
    $cut = (Get-Date).ToUniversalTime().AddDays(-2)
    $heads = @()
    for ($p = 1; $p -le 30; $p++) {
        try { $page = Invoke-RestMethod -TimeoutSec 60 "https://api.github.com/repos/boykopovar/AnyPS5/pulls?state=open&per_page=100&page=$p" } catch { Say "GitHub API page $p failed"; break }
        $heads += $page | ForEach-Object { [pscustomobject]@{ n = $_.number; sha = $_.head.sha } }
        if ($page.Count -lt 100) { break }
    }
    $Prs = @(); $full = 0
    foreach ($h in $heads) {
        if ($Prs.Count -ge 3) { break }
        $short = $h.sha.Substring(0, 10)
        if ((Test-Path "$out\$($h.n).md") -and (Select-String -Quiet -Path "$out\$($h.n).md" -Pattern "head $short")) { continue }
        G cat-file -e $h.sha | Out-Null
        if ($LASTEXITCODE -ne 0) { G fetch -q origin "+refs/pull/$($h.n)/head:refs/remotes/pr/$($h.n)" | Out-Null }
        $t = G log -1 --format=%ct $h.sha
        if (-not $t -or [DateTimeOffset]::FromUnixTimeSeconds([int64]$t).UtcDateTime -lt $cut) { continue }
        $k = Kind $h.sha
        if (-not (Mine $h.n $k)) { continue }
        if ($k -eq 'full') { if ($full -ge 1) { continue }; $full++ }
        $Prs += "$($h.n)"
    }
    if (-not $Prs) {
        Add-Content "$logs\auto.log" "$(Get-Date -Format 'MM-dd HH:mm:ss') scan: $($heads.Count) open, nothing new"
        $lock.Close()
        & powershell -NoProfile -ExecutionPolicy Bypass -File "$W\selfstop.ps1"
        exit 0
    }
}
$main = (G rev-parse --short=10 origin/main | Select-Object -First 1)
EnsureBase 'relinker'
G checkout -q -f --detach origin/main | Out-Null
& powershell -NoProfile -ExecutionPolicy Bypass -File "$W\tcmatrix.ps1" baseline $main *>> "$logs\tc-run.log"
Set-Content "$W\current" ($Prs -join ' ')
foreach ($n in $Prs) {
    $n = $n.TrimStart('#')
    G checkout -q -f --detach origin/main | Out-Null
    G fetch -f origin "+refs/pull/$n/head:refs/remotes/pr/$n" *> "$logs\pr-$n-fetch.log"
    $frc = $LASTEXITCODE
    $head = "$(G rev-parse --short=10 "refs/remotes/pr/$n")".Trim()
    if ($frc -ne 0 -or $head -notmatch '^[0-9a-f]{10}$') { Say "#${n}: fetch failed (rc $frc): $((Get-Content "$logs\pr-$n-fetch.log" -Tail 2) -join ' ')"; continue }
    if ((G rev-parse "refs/remotes/pr/$n") -eq (G rev-parse origin/main)) { Say "#${n}: fetched ref equals main, not testing"; continue }
    G -c user.name=azwin -c user.email=azwin@localhost merge -q --no-edit "refs/remotes/pr/$n" *> "$logs\pr-$n-merge.log"
    if ($LASTEXITCODE -ne 0) {
        G merge --abort | Out-Null
        Say "#$n ($head): does not merge cleanly into main $main, skipped"
        Set-Content "$out\$n.md" "# #$n`nhead $head does not merge cleanly into main $main; not tested."
        continue
    }
    G merge-base --is-ancestor "refs/remotes/pr/$n" HEAD | Out-Null
    if ($LASTEXITCODE -ne 0) { Say "#${n}: PR head $head is not in the merged tree, not testing"; continue }
    $merged = (G rev-parse --short=10 HEAD | Select-Object -First 1)
    $k = Kind "refs/remotes/pr/$n"
    if ($k -eq 'docs' -or -not $k) { Say "#$n ($head): no code changes, not testing"; continue }
    if ($k -eq 'full' -and -not (Test-Path (BaseFile 'full'))) {
        EnsureBase 'full'
        G checkout -q -f --detach origin/main | Out-Null
        G -c user.name=azwin -c user.email=azwin@localhost merge -q --no-edit "refs/remotes/pr/$n" *> "$logs\pr-$n-merge.log"
    }
    $base = BaseFile $k
    Configure $k
    $t0 = Get-Date
    $brc = BuildAll "$logs\pr-$n-build.log" $k
    $bsec = [int]((Get-Date) - $t0).TotalSeconds
    $fails = @(); $runs = @()
    foreach ($i in 1, 2) {
        RunCtest "$logs\pr-$n-ctest-$i.log" $k
        $fails += Failures "$logs\pr-$n-ctest-$i.log"
        $runs += "run ${i}: $(Summary "$logs\pr-$n-ctest-$i.log")"
    }
    $fails = @($fails | Where-Object { $_ } | Sort-Object -Unique)
    $tc = @(if ($k -eq 'relinker') { & powershell -NoProfile -ExecutionPolicy Bypass -File "$W\tcmatrix.ps1" pr $n $main 2>> "$logs\tc-run.log" | Where-Object { $_ -like '|*' } })
    $mx = ''
    if ($tc) {
        $tok = @($tc | Where-Object { $_ -like '*| ok |*' }).Count
        $tnew = @($tc | Where-Object { ($_ -split '\|')[4] -notmatch '^\s*none' } | ForEach-Object { ($_ -split '\|')[1].Trim() })
        $mx = "matrix: $tok of $($tc.Count) toolchain builds ok; new failures in: $(if ($tnew) { $tnew -join ' ' } else { 'none' })"
    }
    $mainFails = @(Get-Content $base | Where-Object { $_ })
    $new = @($fails | Where-Object { $mainFails -notcontains $_ })
    $same = @($fails | Where-Object { $mainFails -contains $_ })
    $lerr = @(Select-String -Path "$logs\pr-$n-build.log" -Pattern '^FAILED:').Count
    $mlerr = @(Select-String -Path "$(BaseLog $k)-build.log" -Pattern '^FAILED:' -ErrorAction SilentlyContinue).Count
    $newTxt = if ($new) { $new -join ' ' } else { 'none' }
    Say "#$n ($head merged as $merged): build rc $brc in ${bsec}s, $lerr failed targets (main $mlerr); $($runs -join '; '); new failures: $newTxt"
    $gcc = (& gcc -dumpfullversion)
    $r = @("# #$n", "head $head merged into main $main as $merged; $k build rc $brc in ${bsec}s; failed build targets $lerr (main $mlerr); $($runs -join '; ')")
    $r += "failing on main too: $(if ($same) { $same -join ' ' } else { 'none' })"
    $r += "new failures: $newTxt"
    $r += $(if ($k -eq 'full') { 'GPU/Vulkan tests: no GPU on this VM, so they fail or skip on main too' } else { 'not run: GPU/Vulkan tests (no GPU on this VM; the relinker-only build has none)' })
    foreach ($t in $new) { $r += "## $t"; $r += (Select-String -Path "$logs\pr-$n-ctest-1.log" -Pattern "Test +#\d+: $([regex]::Escape($t)) " -Context 0, 12 | Select-Object -First 1 | Out-String).Trim() }
    if ($lerr -gt $mlerr) { $r += '## build'; $r += (Select-String -Path "$logs\pr-$n-build.log" -Pattern '^FAILED:' -Context 2, 8 | Select-Object -First 4 | Out-String).Trim() }
    if ($mx) {
        $r += $mx; $r += ''; $r += '## Windows toolchain matrix (relinker-only, one ctest run each; new failures vs main with the same toolchain)'
        $r += '| toolchain | version | build | new failures vs main | time |'; $r += '|---|---|---|---|---|'; $r += $tc
    }
    $r += ''; $r += '## Draft comment'
    $os = (Get-CimInstance Win32_OperatingSystem).Caption -replace '^Microsoft ', ''; $cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name.Trim()
    $r += "Tested on $os $env:PROCESSOR_ARCHITECTURE ($cpu, $([Environment]::ProcessorCount) vCPUs, no GPU), WinLibs GCC $gcc, $(if ($k -eq 'full') { 'full build' } else { 'relinker-only build' }); PR head ``$head`` merged into main ``$main`` as ``$merged``."
    if ($lerr -gt $mlerr) { $r += 'The build has more failed targets than main here; first errors are below.' }
    elseif (-not $new) { $r += "The ctest suite ran twice ($($runs -join '; ')) with no failure that main does not also have here$(if ($same) { ' (`' + ($same -join '`, `') + '`)' })." }
    else { $r += "New failures compared with main on this machine: ``$($new -join '`, `')`` (details below)." }
    if ($mx) { $r += "Also built and tested with WinLibs GCC 13/14 (UCRT and MSVCRT), llvm-mingw Clang 18/19/20 and in a Server Core 2025 container: $($mx -replace '^matrix: ', '')." }
    $r += 'AI-assisted: yes (Claude Code)'
    Set-Content "$out\$n.md" $r
}
G checkout -q -f --detach origin/main | Out-Null
Remove-Item "$W\current" -ErrorAction SilentlyContinue
Say "done: $($Prs -join ' ')"
$lock.Close()
