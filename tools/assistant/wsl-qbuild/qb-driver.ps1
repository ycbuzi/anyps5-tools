# WSL Quest builder driver (11-16; replaces the Chromebook's cb-quest-builds.timer). Runs hidden from the scheduled task
# "AnyPS5 WSL Quest builder" (at logon, re-checked every 15 min; a mutex keeps one copy). Every 10 minutes, or within
# 20 s of a kick (jobboard.py / fleet.py touch _tools\live\wsl-qbuild.kick), it asks WSL Arch how many Quest build
# requests are open; if any, it takes the PC lock (gpu_flag.ps1 Use-Pc, priority 40) and runs
# /root/qb/bin/qb-quest-builds.sh in WSL at nice 15, refreshing the lock every minute until the run ends.
# State: _tools\live\wsl-qbuild.state (one line), log: _tools\live\wsl-qbuild.log; build log in WSL /root/qb/logs.
# Manual start: conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File <this>
$ErrorActionPreference = 'Continue'
. "C:\AnyPS5 Games\_tools\assistant\skills\gpu_flag.ps1"
$live = "C:\AnyPS5 Games\_tools\live"
$log = Join-Path $live "wsl-qbuild.log"
$kick = Join-Path $live "wsl-qbuild.kick"
$stateFile = Join-Path $live "wsl-qbuild.state"
$mutex = New-Object Threading.Mutex($false, "Global\AnyPS5WslQuestBuilder")
try { if (-not $mutex.WaitOne(0)) { exit 0 } } catch [Threading.AbandonedMutexException] { }

function Say([string]$text) {
    $line = "$(Get-Date -Format 'MM-dd HH:mm:ss') $text"
    Add-Content -LiteralPath $log -Value $line -Encoding utf8
    if ((Get-Item -LiteralPath $log).Length -gt 1MB) { $keep = Get-Content -LiteralPath $log -Tail 2000; Set-Content -LiteralPath $log -Value $keep -Encoding utf8 }
}
function Set-State([string]$text) { [IO.File]::WriteAllText($stateFile, "$text`n") }
function Start-Wsl([string]$arguments) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = "$env:SystemRoot\System32\wsl.exe"
    $psi.Arguments = "-d archlinux -u root -- $arguments"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    [Diagnostics.Process]::Start($psi)
}
function Get-Pending {
    $p = Start-Wsl "bash /root/qb/bin/qb-quest-builds.sh pending"
    $out = $p.StandardOutput.ReadToEndAsync()
    if (-not $p.WaitForExit(120000)) { try { $p.Kill() } catch { }; return -1 }
    $n = 0
    if ([int]::TryParse(($out.Result -split "`n" | Where-Object { $_ -match '^\d+\s*$' } | Select-Object -Last 1), [ref]$n)) { return $n }
    return -1
}

Say "driver started (pid $PID)"
$next = Get-Date
$lastOk = "never"
while ($true) {
    if ((Test-Path -LiteralPath $kick) -or (Get-Date) -ge $next) {
        Remove-Item -LiteralPath $kick -ErrorAction SilentlyContinue
        $next = (Get-Date).AddMinutes(10)
        $n = Get-Pending
        if ($n -lt 0) {
            Set-State "WSL did not answer at $(Get-Date -Format 'MM-dd HH:mm')"
            Say "pending check failed (WSL did not answer)"
        } elseif ($n -eq 0) {
            Set-State "idle (checked $(Get-Date -Format 'MM-dd HH:mm'), last build run $lastOk)"
        } else {
            Say "$n open request(s): waiting for the PC lock"
            Set-State "waiting for the PC lock since $(Get-Date -Format 'MM-dd HH:mm') ($n request(s))"
            $code = $null
            Use-Pc "Quest build (WSL)" 40 {
                Say "PC lock taken; building"
                Set-State "building since $(Get-Date -Format 'MM-dd HH:mm') ($n request(s)); see WSL /root/qb/state.txt"
                $p = Start-Wsl "nice -n 15 bash /root/qb/bin/qb-quest-builds.sh"
                $null = $p.StandardOutput.ReadToEndAsync(); $null = $p.StandardError.ReadToEndAsync()
                while (-not $p.WaitForExit(60000)) { Touch-GpuFlag }
                $script:code = $p.ExitCode
            }
            $lastOk = "$(Get-Date -Format 'MM-dd HH:mm') exit $code"
            Say "build run ended, exit $code; PC lock released"
            Set-State "idle (last build run $lastOk)"
            $next = (Get-Date).AddMinutes(10)
        }
    }
    Start-Sleep 20
}
