# Shows the AnyPS5 game you're playing as your Discord status ("Playing <app name>", with the game's title).
# Works for every game folder in D:\AnyPS5 Games: it watches for a running <Game>.exe from one of them and reads
# that folder's game.json. The games themselves are not changed.
#
# Needs a Discord application ID in _tools\discord.json: { "clientId": "123..." } (Discord Developer Portal ->
# New Application; its name is what Discord shows after "Playing").
#
# usage: discord-presence.ps1            keep running in the background (start-discord-presence.vbs starts it hidden)
#        discord-presence.ps1 -Once      check once, print what it would show, and exit (a test)
param([switch]$Once)
$ErrorActionPreference = "Stop"
$GamesRoot = "D:\AnyPS5 Games"
$configPath = Join-Path $PSScriptRoot "discord.json"

# One copy at a time.
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Local\AnyPS5GamesDiscordPresence", [ref]$created)
if (-not $created -and -not $Once) { "already running"; exit 0 }

function Find-Game {
    $exes = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($GamesRoot + "\", [StringComparison]::OrdinalIgnoreCase) }
    foreach ($p in $exes) {
        $dir = Split-Path $p.Path -Parent
        if ((Split-Path $dir -Parent) -ne $GamesRoot) { continue }          # only <Games>\<Game>\<Game>.exe
        $json = Join-Path $dir "game.json"
        if (-not (Test-Path -LiteralPath $json)) { continue }
        try { $cfg = Get-Content -LiteralPath $json -Raw | ConvertFrom-Json } catch { continue }
        return [pscustomobject]@{ Pid = $p.Id; Title = $cfg.title; TitleId = $cfg.titleId; Started = $p.StartTime.ToUniversalTime() }
    }
    return $null
}

# Discord's local IPC: frames of (int32 opcode, int32 length, UTF-8 JSON) over \\.\pipe\discord-ipc-N.
$script:pipe = $null
function Send-Frame([int]$op, $payload) {
    $body = [Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 10 -Compress))
    $frame = New-Object byte[] (8 + $body.Length)
    [BitConverter]::GetBytes($op).CopyTo($frame, 0); [BitConverter]::GetBytes($body.Length).CopyTo($frame, 4); $body.CopyTo($frame, 8)
    $script:pipe.Write($frame, 0, $frame.Length); $script:pipe.Flush()
    $header = New-Object byte[] 8; $read = 0
    while ($read -lt 8) { $n = $script:pipe.Read($header, $read, 8 - $read); if ($n -le 0) { throw "Discord closed the connection" }; $read += $n }
    $length = [BitConverter]::ToInt32($header, 4); $reply = New-Object byte[] $length; $read = 0
    while ($read -lt $length) { $n = $script:pipe.Read($reply, $read, $length - $read); if ($n -le 0) { throw "Discord closed the connection" }; $read += $n }
    $answer = [Text.Encoding]::UTF8.GetString($reply) | ConvertFrom-Json
    if ([BitConverter]::ToInt32($header, 0) -eq 2 -or $answer.evt -eq "ERROR") { throw "Discord refused: $($answer.data.message)" }
    $answer
}
function Connect-Discord([string]$clientId) {
    foreach ($i in 0..9) {
        $p = New-Object System.IO.Pipes.NamedPipeClientStream(".", "discord-ipc-$i", [System.IO.Pipes.PipeDirection]::InOut)
        try { $p.Connect(500) } catch { $p.Dispose(); continue }
        $script:pipe = $p
        try { [void](Send-Frame 0 @{ v = 1; client_id = $clientId }); return $true } catch { $p.Dispose(); $script:pipe = $null; throw }
    }
    $false
}
function Set-Activity($game) {
    $activity = $null
    if ($game) {
        $activity = @{
            details    = "$($game.Title)"
            state      = "PS5 game on Windows ($($game.TitleId))"
            timestamps = @{ start = [int64]($game.Started - [datetime]'1970-01-01').TotalSeconds }
        }
    }
    [void](Send-Frame 1 @{ cmd = "SET_ACTIVITY"; args = @{ pid = $PID; activity = $activity }; nonce = [guid]::NewGuid().ToString() })
}

if ($Once) {
    $g = Find-Game
    if ($g) { "would show: Playing <app> | $($g.Title) | PS5 game on Windows ($($g.TitleId)) | since $($g.Started.ToLocalTime())" } else { "no AnyPS5 game running" }
    if (Test-Path $configPath) {
        $id = (Get-Content $configPath -Raw | ConvertFrom-Json).clientId
        if (Connect-Discord $id) { Set-Activity $g; "Discord accepted the status"; Start-Sleep 15; Set-Activity $null; $script:pipe.Dispose() } else { "Discord is not running" }
    } else { "no _tools\discord.json yet (needs the Discord application ID)" }
    exit 0
}

$shown = $null
while ($true) {
    try {
        if (-not (Test-Path $configPath)) { Start-Sleep 30; continue }
        $game = Find-Game
        $key = if ($game) { "$($game.Pid)" } else { "" }
        if ($key -ne $shown) {
            if (-not $script:pipe -or -not $script:pipe.IsConnected) {
                if (-not $game) { $shown = ""; Start-Sleep 5; continue }
                if (-not (Connect-Discord (Get-Content $configPath -Raw | ConvertFrom-Json).clientId)) { Start-Sleep 15; continue }
            }
            Set-Activity $game
            $shown = $key
        }
    } catch {
        if ($script:pipe) { $script:pipe.Dispose(); $script:pipe = $null }
        $shown = $null
        Start-Sleep 15
    }
    Start-Sleep 5
}
