param([string]$Ref = "origin/main", [switch]$Check)
$ErrorActionPreference = "Continue"
(Get-Process -Id $PID).PriorityClass = "BelowNormal"
$env:GIT_CONFIG_COUNT = 1; $env:GIT_CONFIG_KEY_0 = "core.fsmonitor"; $env:GIT_CONFIG_VALUE_0 = "false"
$Work = "C:\AnyPS5-work"; $Src = "$Work\src"; $Out = "$Work\build"; $Tool = "$Work\mingw64"
$env:PATH = "$Tool\bin;$env:PATH"
git -C $Src fetch -q origin main
if ($LASTEXITCODE) { "STOPPED: could not fetch from GitHub"; exit 1 }
$now = git -C $Src log -1 --format='%h %s'
$next = git -C $Src log -1 --format='%h %s' $Ref
"Build folder is at: $now"
"Would build: $next"
$dirty = @(git -C $Src status --porcelain --ignore-submodules=all)
if ($dirty.Count) { "STOPPED: $($dirty.Count) changed files in $Src. They are not mine to discard:"; $dirty | Select-Object -First 8; exit 1 }
$lib = "$Out\core\libs\libs\libSceAgcDriver.prx"
if (Test-Path $lib) { "Library now: " + (Get-FileHash $lib -Algorithm SHA1).Hash.Substring(0, 12) + " " + (Get-Item $lib).LastWriteTime.ToString("MM-dd HH:mm") }
if ($Check) { "Checked only; nothing was built."; exit 0 }
git -C $Src checkout -q --detach $Ref
if ($LASTEXITCODE) { "STOPPED: checkout failed"; exit 1 }
git -C $Src submodule update --init --recursive 2>&1 | Select-Object -Last 2
$sw = [Diagnostics.Stopwatch]::StartNew()
& "$Tool\bin\ninja.exe" -C $Out -j 8 2>&1 | Select-Object -Last 3
$a = $LASTEXITCODE
& "$Tool\bin\ninja.exe" -C $Out -j 8 core/libs/libs 2>&1 | Tee-Object -Variable lines | Select-Object -Last 3
$b = $LASTEXITCODE
"Programs exit $a, libraries exit $b, {0:N0} s" -f $sw.Elapsed.TotalSeconds
if ($a -or $b) { $lines | Select-String 'FAILED:|error:' | Select-Object -First 10; exit 1 }
"Library after: " + (Get-FileHash $lib -Algorithm SHA1).Hash.Substring(0, 12) + " " + (Get-Item $lib).LastWriteTime.ToString("MM-dd HH:mm")
"Trust the hash and time above. The commit label in a game's summary stays stale, and a build of a few seconds means the libraries were not rebuilt."
