param([Parameter(Mandatory = $true)][int]$Number, [Parameter(Mandatory = $true)][string]$Library, [string]$With = "")
$ErrorActionPreference = "Continue"
(Get-Process -Id $PID).PriorityClass = "BelowNormal"
$env:GIT_CONFIG_COUNT = 1; $env:GIT_CONFIG_KEY_0 = "core.fsmonitor"; $env:GIT_CONFIG_VALUE_0 = "false"
$Work = "C:\AnyPS5-work"; $Src = "$Work\prs"; $Out = "$Work\build-prs"; $Tool = "$Work\mingw64"; $Keep = "$Work\pr-libs\pr-$Number" + $(if ($With) { "-with-" + ($With -replace "[^0-9]+", "-") } else { "" })
$env:PATH = "$Tool\bin;$env:PATH"
git -C $Src fetch -q origin main "+refs/pull/$Number/head:refs/remotes/origin/pr/$Number"
if ($LASTEXITCODE) { "STOPPED: could not fetch PR $Number"; exit 1 }
if (git -C $Src status --porcelain --ignore-submodules=all -- core tools) { "STOPPED: $Src has uncommitted work; commit it first, because this step would erase it"; exit 1 }
git -C $Src merge --abort 2>$null
git -C $Src checkout -q -f --detach "origin/pr/$Number"
if ($LASTEXITCODE) { "STOPPED: checkout failed"; exit 1 }
$head = git -C $Src log -1 --format='%h %s'
$main = git -C $Src log -1 --format='%h' origin/main
"PR $Number head: $head"
git -C $Src -c user.name=test -c user.email=user@example.com merge -q --no-edit origin/main 2>&1 | Select-Object -Last 2
if ($LASTEXITCODE) { git -C $Src merge --abort 2>$null; "STOPPED: PR $Number conflicts with main $main, so it cannot be tested this way"; exit 1 }
foreach ($other in @($With -split "[^0-9]+" | Where-Object { $_ })) {
    git -C $Src fetch -q origin "+refs/pull/$other/head:refs/remotes/origin/pr/$other"
    git -C $Src -c user.name=test -c user.email=user@example.com merge -q --no-edit "origin/pr/$other" 2>&1 | Select-Object -Last 2
    if ($LASTEXITCODE) {
        $clash = @(git -C $Src diff --name-only --diff-filter=U)
        if (@($clash | Where-Object { $_ -notmatch "/tests/|^docs/" }).Count) { git -C $Src merge --abort 2>$null; "STOPPED: PR $other conflicts with PR $Number in code: $($clash -join ', ')"; exit 1 }
        foreach ($file in $clash) { git -C $Src checkout -q --ours -- $file; git -C $Src add -- $file }
        git -C $Src -c user.name=test -c user.email=user@example.com commit -q --no-edit
        "Tests or docs conflicted, keeping PR $Number's copy: $($clash -join ', ')"
    }
    "Also merged PR ${other}: " + (git -C $Src log -1 --format=%h "origin/pr/$other")
}
"Merged onto main $main. Nothing here is pushed."
foreach ($row in @(git -C $Src ls-tree HEAD 3rdparty/ | Where-Object { $_ -match "^160000" })) {
    $path = ($row -split "`t")[1]
    $full = Join-Path $Src $path
    $hasFiles = { @(Get-ChildItem -LiteralPath $full -Force -ErrorAction SilentlyContinue | Where-Object Name -ne ".git").Count -gt 0 }
    if (-not (& $hasFiles)) {
        "fetching $path"
        git -C $Src submodule update --init --recursive -- $path 2>&1 | Select-Object -Last 1
        if (-not (& $hasFiles) -and (Test-Path -LiteralPath "$full\.git")) { git -C $full checkout -f (($row -split "\s+")[2]) 2>&1 | Select-Object -Last 1 }
        if (-not (& $hasFiles)) { "STOPPED: could not fetch $path"; exit 1 }
    }
}
$sw = [Diagnostics.Stopwatch]::StartNew()
$targets = & "$Tool\bin\ninja.exe" -C $Out -t targets all 2>$null | ForEach-Object { ($_ -split ':')[0] } | Where-Object { $_ -match "(^|/)$Library\.prx$" }
if (-not $targets) { "STOPPED: no build target for $Library.prx"; exit 1 }
$target = @($targets)[0]
& "$Tool\bin\ninja.exe" -C $Out -j 6 $target 2>&1 | Tee-Object -Variable lines | Select-Object -Last 2
if ($LASTEXITCODE) { $lines | Select-String 'FAILED:|error:|CMake Error|does not contain' | Select-Object -First 12; "BUILD FAILED"; exit 1 }
New-Item -ItemType Directory -Force $Keep | Out-Null
Copy-Item -LiteralPath (Join-Path $Out $target) -Destination $Keep -Force
$built = Join-Path $Keep "$Library.prx"
"Built in {0:N0} s: $built, SHA1 {1}" -f $sw.Elapsed.TotalSeconds, (Get-FileHash $built -Algorithm SHA1).Hash.Substring(0, 12)
"Use it with test_run's library setting, on a test copy. Test the same copy without it too."
