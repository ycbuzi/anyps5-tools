# Follows a running game for the AnyPS5 Games window, which shows it in place of the game list while the game runs
# (Blu: "I'd love to see what's actually going on" instead of a black screen). Every few seconds it writes
# _tools\live\game-watch.json: time up, frames, game memory, memory left, a giant shader compile in progress, and the
# moments that matter from the game's anyps5.log (a compile starting and ending, stop messages, crashes).
# It waits up to 10 minutes for the game to start and ends when the game closes.
param([Parameter(Mandatory = $true)][string]$GameDir, [string]$Label = "")
$Out = "D:\AnyPS5 Games\_tools\live\game-watch.json"
$GameDir = $GameDir.TrimEnd('\')
$name = if ($Label) { $Label } else { Split-Path $GameDir -Leaf }
$log = Join-Path $GameDir "anyps5.log"
function Game { Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($GameDir + "\", [StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1 }
$g = Game
for ($i = 0; -not $g -and $i -lt 120; $i++) { Start-Sleep 5; $g = Game }
if (-not $g) { exit 1 }
$events = New-Object System.Collections.Generic.List[string]
function Event([string]$text) { $events.Add((Get-Date -Format "HH:mm") + "  " + $text); while ($events.Count -gt 40) { $events.RemoveAt(0) } }
function Read-Tail([long]$from) {
    $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
    try { $fs.Seek($from, 'Begin') | Out-Null; $t = (New-Object IO.StreamReader($fs)).ReadToEnd(); @($t, $fs.Position) } finally { $fs.Close() }
}
$pos = 0; $flips = 0; $compiling = $null
if (Test-Path -LiteralPath $log) {
    # Joined late (the game was already running): count what is there, and notice a compile already under way: its
    # "creating a pipeline" line with no frame after it, while the log is quiet (a compile writes nothing).
    $all = Read-Tail 0; $pos = $all[1]
    $flips = ([regex]::Matches($all[0], 'flip')).Count
    $start = [regex]::Matches($all[0], 'creating a pipeline for (\d+) SPIR-V words') | Where-Object { [int]$_.Groups[1].Value -ge 500000 } | Select-Object -Last 1
    $shown = [regex]::Matches($all[0], 'flip') | Select-Object -Last 1
    if ($start -and (-not $shown -or $shown.Index -lt $start.Index)) {
        $compiling = @{ words = [int]$start.Groups[1].Value; since = (Get-Item -LiteralPath $log).LastWriteTime; flips = $flips }
        Event ("a giant shader is compiling ({0:N1} million SPIR-V words) since about {1:HH:mm}" -f ($compiling.words / 1e6), $compiling.since)
    }
}
Event "watching $name (started $($g.StartTime.ToString('HH:mm')))"
while ($g = Game) {
    if (Test-Path -LiteralPath $log) {
        $r = Read-Tail $pos
        if ($r[1] -lt $pos) { $r = Read-Tail 0 }
        $pos = $r[1]
        foreach ($line in $r[0] -split "`n") {
            if ($line -match 'flip') { $flips++ }
            if ($line -match 'creating a pipeline for (\d+) SPIR-V words') {
                $words = [int]$Matches[1]
                if ($words -ge 500000) { $compiling = @{ words = $words; since = Get-Date; flips = $flips }; Event ("compiling a giant shader: {0:N1} million SPIR-V words (normal ones are a few thousand); the game waits for it" -f ($words / 1e6)) }
            }
            elseif ($compiling -and $flips -gt $compiling.flips) {
                Event ("the giant shader compiled after {0:N0} min; the game is drawing again" -f ((Get-Date) - $compiling.since).TotalMinutes); $compiling = $null
            }
            if ($line -match 'FATAL|not implemented|is not supported') { Event $line.Trim().Substring(0, [Math]::Min(200, $line.Trim().Length)) }
        }
    }
    $os = Get-CimInstance Win32_OperatingSystem
    $state = [ordered]@{
        name = $name; pid = $g.Id; started = $g.StartTime.ToString("HH:mm"); updated = (Get-Date -Format "HH:mm:ss")
        up_min = [int]((Get-Date) - $g.StartTime).TotalMinutes; frames = $flips
        game_gb = [math]::Round($g.PagedMemorySize64 / 1GB, 1); left_gb = [math]::Round($os.FreeVirtualMemory / 1MB, 1); total_gb = [math]::Round($os.TotalVirtualMemorySize / 1MB, 0)
        compiling = $(if ($compiling) { "a giant shader ({0:N1} million SPIR-V words) for {1:N0} min" -f ($compiling.words / 1e6), ((Get-Date) - $compiling.since).TotalMinutes } else { "" })
        events = @($events)
    }
    try { [IO.File]::WriteAllText($Out, ($state | ConvertTo-Json -Depth 3), [Text.Encoding]::UTF8) } catch { }
    Start-Sleep 5
}
try { Remove-Item -LiteralPath $Out -ErrorAction SilentlyContinue } catch { }
