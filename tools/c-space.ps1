param([switch]$DryRun, [switch]$Register, [switch]$Unregister)
$ErrorActionPreference = 'Stop'
$TaskName = 'AnyPS5 C-space'
$Log = 'C:\AnyPS5 Games\_reports\c-space.log'
$TargetFreeGB = 400
$Moves = @(
    @{ From = "C:\AnyPS5 Games\Marvel's Spider-Man 2 (PPSA03016)"; To = "G:\AnyPS5 Games\Marvel's Spider-Man 2 (PPSA03016)" }
)
$SpotifyCaches = @("$env:LOCALAPPDATA\Spotify\Data")
$MarginGB = 10

function Write-Log([string]$text) {
    $line = '{0:yyyy-MM-dd HH:mm} {1}' -f (Get-Date), $text
    Add-Content -LiteralPath $Log -Value $line -Encoding utf8
    Write-Output $line
}
function Free-GB([string]$drive) { [math]::Round((Get-PSDrive $drive.Substring(0, 1)).Free / 1GB, 1) }
function Is-Junction([string]$path) { (Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
function Tree-Stats([string]$root) {
    $count = 0; $bytes = [int64]0; $links = 0
    $stack = New-Object System.Collections.Stack
    $stack.Push((Get-Item -LiteralPath $root -Force))
    while ($stack.Count) {
        $dir = $stack.Pop()
        foreach ($e in $dir.EnumerateFileSystemInfos()) {
            if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { $links++; continue }
            if ($e -is [IO.DirectoryInfo]) { $stack.Push($e) } else { $count++; $bytes += $e.Length }
        }
    }
    [pscustomobject]@{ Files = $count; Bytes = $bytes; Links = $links }
}
function In-Use([string]$root) {
    [bool](Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } catch { $false } })
}

if ($Register) {
    $ps = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\conhost.exe" -Argument "--headless `"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 12) -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Log "registered task '$TaskName' (every 30 min until C: has $TargetFreeGB GB free and all moves are done)"
    return
}
if ($Unregister) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue; Write-Log "task removed"; return }

Write-Log "start: C: $(Free-GB 'C') GB free, G: $(Free-GB 'G') GB free$(if ($DryRun) { ' (dry run)' })"

if (Get-Process Spotify -ErrorAction SilentlyContinue) {
    Write-Log "spotify: running, cache left for a later run"
} else {
    foreach ($cache in $SpotifyCaches) {
        if (-not (Test-Path -LiteralPath $cache) -or (Is-Junction $cache)) { continue }
        $stats = Tree-Stats $cache
        if ($stats.Bytes -lt 100MB) { continue }
        Write-Log ("spotify: clearing {0} ({1:N1} GB)" -f $cache, ($stats.Bytes / 1GB))
        if (-not $DryRun) {
            & cmd.exe /d /c rd /s /q "`"$cache`""
            New-Item -ItemType Directory -Path $cache -Force | Out-Null
        }
    }
}

$pending = 0
foreach ($m in $Moves) {
    $from = $m.From; $to = $m.To
    if (Is-Junction $from) { continue }
    if (-not (Test-Path -LiteralPath $from)) { Write-Log "move: $from is gone, skipped"; continue }
    $pending++
    if (In-Use $from) { Write-Log "move: $from is in use, waiting"; continue }
    $src = Tree-Stats $from
    if ($src.Links -gt 0) { Write-Log "move: $from holds $($src.Links) links; not moved, needs a look"; continue }
    $haveGB = 0
    if (Test-Path -LiteralPath $to) { $haveGB = (Tree-Stats $to).Bytes / 1GB }
    $needGB = $src.Bytes / 1GB - $haveGB + $MarginGB
    $toFree = Free-GB $to
    if ($toFree -lt $needGB) { Write-Log ("move: {0} needs {1:N1} GB on {2}, only {3} GB free; waiting for room" -f $from, $needGB, $to.Substring(0, 2), $toFree); continue }
    Write-Log ("move: copying {0} ({1:N1} GB, {2} files) to {3}" -f $from, ($src.Bytes / 1GB), $src.Files, $to)
    if ($DryRun) { continue }
    $p = Start-Process robocopy.exe -ArgumentList @("`"$from`"", "`"$to`"", '/E', '/COPY:DAT', '/DCOPY:DAT', '/XJ', '/R:2', '/W:5', '/NP', '/NFL', '/NDL', "/LOG+:`"$Log.robocopy`"") -WindowStyle Hidden -PassThru
    try { $p.PriorityClass = 'BelowNormal' } catch { }
    $p.WaitForExit()
    if ($p.ExitCode -ge 8) { Write-Log "move: robocopy failed with $($p.ExitCode); source kept"; continue }
    $dst = Tree-Stats $to
    if ($dst.Files -ne $src.Files -or $dst.Bytes -ne $src.Bytes) { Write-Log "move: copy differs (files $($dst.Files)/$($src.Files), bytes $($dst.Bytes)/$($src.Bytes)); source kept"; continue }
    if (In-Use $from) { Write-Log "move: $from came into use during the copy; source kept, retry later"; continue }
    & cmd.exe /d /c rd /s /q "`"$from`""
    if (Test-Path -LiteralPath $from) { Write-Log "move: could not remove $from fully; left as is"; continue }
    & cmd.exe /d /c mklink /J "`"$from`"" "`"$to`"" | Out-Null
    if (Is-Junction $from) { Write-Log "move: done, $from now points to $to"; $pending-- } else { Write-Log "move: junction at $from failed; game is at $to" }
}

$cFree = Free-GB 'C'
Write-Log "end: C: $cFree GB free, G: $(Free-GB 'G') GB free, moves pending $pending"
if (-not $DryRun -and $pending -eq 0) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "all moves done; task removed$(if ($cFree -lt $TargetFreeGB) { ", C: still under $TargetFreeGB GB" })"
}
