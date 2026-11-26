param([int]$Seconds = 10, [int]$Top = 15)
$Tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$Live = Join-Path $Tools "live"
$Snap = Join-Path $Live "pc-monitor.json"
$Hist = Join-Path $Live "pc-monitor-history.csv"
$Alerts = Join-Path $Live "pc-monitor-alerts.log"
$created = $false
$single = New-Object System.Threading.Mutex($true, "Local\AnyPS5PcMonitor", [ref]$created)
if (-not $created) { exit 0 }
$cores = [Environment]::ProcessorCount
$pub = @{}
$last = @{}
$hot = @{}
function Alert($key, $msg) {
    if ($last[$key] -and ((Get-Date) - $last[$key]).TotalMinutes -lt 15) { return }
    $last[$key] = Get-Date
    Add-Content -LiteralPath $Alerts -Value "$(Get-Date -Format 'MM-dd HH:mm:ss') $msg" -Encoding utf8
}
function Publisher($path) {
    if (-not $path) { return "" }
    if ($pub.ContainsKey($path)) { return $pub[$path] }
    $s = try { (Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop).SignerCertificate.Subject } catch { "" }
    $name = if ($s -match 'O=("?)([^,"]+)') { $Matches[2] } elseif ($s -match 'CN=("?)([^,"]+)') { $Matches[2] } else { "unsigned" }
    $pub[$path] = $name
    $name
}
$counters = '\Processor(_Total)\% Processor Time', '\Memory\Available MBytes', '\Memory\% Committed Bytes In Use',
            '\PhysicalDisk(_Total)\% Disk Time', '\PhysicalDisk(_Total)\Disk Bytes/sec', '\Network Interface(*)\Bytes Total/sec',
            '\GPU Engine(*)\Utilization Percentage', '\Process(*)\% Processor Time', '\Process(*)\ID Process'
$totalMb = [int]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
if (-not (Test-Path -LiteralPath $Hist)) { "time,cpu,ram_pct,disk_pct,disk_mbps,net_mbps,gpu0,gpu1,gpu2,game_mode" | Set-Content -LiteralPath $Hist -Encoding utf8 }
while ($true) {
    try {
        $s = (Get-Counter -Counter $counters -ErrorAction SilentlyContinue).CounterSamples
        $v = { param($p) ($s | Where-Object { $_.Path -like "*$p" } | Measure-Object CookedValue -Sum).Sum }
        $cpu = [math]::Round((& $v '\processor(_total)\% processor time'), 1)
        $availMb = [int](& $v '\memory\available mbytes')
        $ramPct = [math]::Round(100 * ($totalMb - $availMb) / $totalMb, 1)
        $disk = [math]::Round([math]::Min(100, (& $v '\physicaldisk(_total)\% disk time')), 1)
        $diskMb = [math]::Round((& $v '\physicaldisk(_total)\disk bytes/sec') / 1MB, 1)
        $netMb = [math]::Round(($s | Where-Object { $_.Path -like '*\network interface(*)\bytes total/sec' } | Measure-Object CookedValue -Sum).Sum / 1MB, 2)
        $gpuRows = $s | Where-Object { $_.Path -like '*\gpu engine(*)\utilization percentage' -and $_.CookedValue -gt 0 }
        $byGpu = @{}; $byPidGpu = @{}
        foreach ($g in $gpuRows) {
            if ($g.InstanceName -match 'pid_(\d+)_luid_0x[0-9a-f]+_(0x[0-9a-f]+)_phys_(\d+)_eng_\d+_engtype_(\w+)') {
                $pidG = [int]$Matches[1]; $phys = $Matches[3]; $type = $Matches[4]
                $k = "phys$phys"; $byGpu[$k] = [math]::Max([double]$byGpu[$k], [double]$g.CookedValue)
                $byPidGpu[$pidG] = [math]::Max([double]$byPidGpu[$pidG], [double]$g.CookedValue)
            }
        }
        $procCpu = @{}
        $pidOf = @{}
        foreach ($r in $s | Where-Object { $_.Path -like '*\process(*)\id process' }) { $pidOf[$r.InstanceName] = [int]$r.CookedValue }
        foreach ($r in $s | Where-Object { $_.Path -like '*\process(*)\% processor time' }) {
            if ($r.InstanceName -in '_total', 'idle') { continue }
            $id = $pidOf[$r.InstanceName]; if ($id) { $procCpu[$id] = [math]::Round($r.CookedValue / $cores, 1) }
        }
        $procs = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, CommandLine, ExecutablePath, WorkingSetSize, CreationDate
        $rows = foreach ($p in $procs) {
            [pscustomobject]@{ pid = [int]$p.ProcessId; ppid = [int]$p.ParentProcessId; name = $p.Name; cpu = [double]$procCpu[[int]$p.ProcessId]
                mem_mb = [int]($p.WorkingSetSize / 1MB); gpu = [math]::Round([double]$byPidGpu[[int]$p.ProcessId], 1); path = $p.ExecutablePath; cmd = $p.CommandLine }
        }
        $topRows = $rows | Sort-Object { $_.cpu * 4 + $_.mem_mb / 256 + $_.gpu * 2 } -Descending | Select-Object -First $Top | ForEach-Object {
            $_ | Add-Member -NotePropertyName publisher -NotePropertyValue (Publisher $_.path) -PassThru | Add-Member -NotePropertyName ours -NotePropertyValue ([bool]($_.cmd -match 'AnyPS5|anyps5|_tools|AnyPS5-work|/root/')) -PassThru
        }
        $gm = Test-Path -LiteralPath (Join-Path $Tools "pause.flag")
        $vpn = try { [string](Get-NetAdapter -Name "ProtonVPN" -ErrorAction Stop).Status } catch { "absent" }
        if ($script:lastVpn -and $vpn -ne $script:lastVpn) { Alert "vpn-$vpn" "ProtonVPN $($script:lastVpn) -> $vpn" }
        $script:lastVpn = $vpn
        $snapObj = [ordered]@{ time = (Get-Date).ToString('s'); cpu = $cpu; ram_pct = $ramPct; ram_free_gb = [math]::Round($availMb / 1024, 1); disk_pct = $disk; disk_mbps = $diskMb; net_mbps = $netMb
            gpus = $byGpu; game_mode = $gm; vpn = $vpn; process_count = @($procs).Count
            top = @($topRows | ForEach-Object { [ordered]@{ pid = $_.pid; ppid = $_.ppid; name = $_.name; cpu = $_.cpu; mem_mb = $_.mem_mb; gpu = $_.gpu; publisher = $_.publisher; ours = $_.ours; cmd = if ($_.cmd) { $_.cmd.Substring(0, [Math]::Min(200, $_.cmd.Length)) } else { "" } } }) }
        $tmp = "$Snap.tmp"; ($snapObj | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $tmp -Encoding utf8; Move-Item -LiteralPath $tmp -Destination $Snap -Force
        $g = @(($byGpu.Keys | Sort-Object) | ForEach-Object { [math]::Round($byGpu[$_], 0) }) + @(0, 0, 0)
        "$((Get-Date).ToString('s')),$cpu,$ramPct,$disk,$diskMb,$netMb,$($g[0]),$($g[1]),$($g[2]),$([int]$gm)" | Add-Content -LiteralPath $Hist -Encoding utf8
        if ($ramPct -ge 85) { Alert "ram" "RAM $ramPct% used ($([math]::Round($availMb/1024,1)) GB free); top: $(($rows | Sort-Object mem_mb -Descending | Select-Object -First 3 | ForEach-Object { "$($_.name) $($_.mem_mb) MB" }) -join ', ')" }
        if ($cpu -ge 92) { $hot.cpu = [int]$hot.cpu + 1; if ($hot.cpu -ge 6) { Alert "cpu" "CPU $cpu% for a minute; top: $(($rows | Sort-Object cpu -Descending | Select-Object -First 3 | ForEach-Object { "$($_.name) $($_.cpu)%" }) -join ', ')" } } else { $hot.cpu = 0 }
        if ($gm) { foreach ($r in $rows | Where-Object { $_.cmd -match 'AnyPS5|anyps5|_tools|AnyPS5-work' -and ($_.cpu -ge 15 -or $_.gpu -ge 15) }) { Alert "gm-$($r.pid)" "game mode ON but AnyPS5 work is busy: $($r.name) pid $($r.pid) cpu $($r.cpu)% gpu $($r.gpu)%" } }
        $hourAgo = (Get-Date).AddHours(-24)
        if ((Get-Item -LiteralPath $Hist).Length -gt 20MB) { $keep = Get-Content -LiteralPath $Hist | Select-Object -Last 100000; $keep | Set-Content -LiteralPath $Hist -Encoding utf8 }
    } catch { Add-Content -LiteralPath $Alerts -Value "$(Get-Date -Format 'MM-dd HH:mm:ss') monitor error: $($_.Exception.Message)" -Encoding utf8 }
    Start-Sleep -Seconds $Seconds
}
