param([ValidateSet("on", "off", "status")] [string]$Mode = "status")
$Tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$Flag = Join-Path $Tools "pause.flag"
$Log = Join-Path $Tools "live\game-mode.log"
$Heavy = 'ninja|cmake|cc1plus|cc1|ld|ld\.lld|clang\+\+|clang|g\+\+|ccache|ctest|robocopy|7z|python|pythonw|wsl|wslhost|vmmem|vmmemWSL'
function Say($m) { $line = "$(Get-Date -Format 'MM-dd HH:mm:ss') $m"; Add-Content -LiteralPath $Log -Value $line -Encoding utf8; $line }
Add-Type -Namespace GM -Name Nt -MemberDefinition '[DllImport("ntdll.dll")] public static extern int NtSuspendProcess(IntPtr h); [DllImport("ntdll.dll")] public static extern int NtResumeProcess(IntPtr h);' -ErrorAction SilentlyContinue
$FrozenFile = Join-Path $Tools "live\game-mode-frozen.txt"
function Build-Processes {
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(ninja|cc1plus|cc1|ld|ld\.lld|g\+\+|gcc|ccache|cmake|ctest|git|ranlib|ar)\.exe$' -and ($_.CommandLine -match 'AnyPS5|anyps5|mingw|\.tmp-|repack|pack-objects' -or $_.Name -match '^(cc1plus|cc1|ld|ranlib|ar)\.exe$') }
}
function Freeze {
    $ids = @()
    foreach ($p in Build-Processes) { try { $h = (Get-Process -Id $p.ProcessId -ErrorAction Stop).Handle; if ([GM.Nt]::NtSuspendProcess($h) -eq 0) { $ids += $p.ProcessId } } catch {} }
    Set-Content -LiteralPath $FrozenFile -Value $ids -Encoding ascii
    $ids.Count
}
function Thaw {
    $n = 0
    if (Test-Path -LiteralPath $FrozenFile) { foreach ($id in Get-Content -LiteralPath $FrozenFile) { try { $h = (Get-Process -Id ([int]$id) -ErrorAction Stop).Handle; if ([GM.Nt]::NtResumeProcess($h) -eq 0) { $n++ } } catch {} }; Remove-Item -LiteralPath $FrozenFile -ErrorAction SilentlyContinue }
    $n
}
function Work-Processes {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match "^($Heavy)(\.exe)?$" -and ($_.CommandLine -match 'AnyPS5|anyps5|aos|acn|/root/qb|_tools|AnyPS5-work' -or $_.Name -match '^(vmmem|vmmemWSL|wslhost)')
    }
}
switch ($Mode) {
    "on" {
        Set-Content -LiteralPath $Flag -Value "game mode: Blu is gaming (game-mode.ps1 on $(Get-Date -Format 'MM-dd HH:mm'))" -Encoding utf8
        $n = 0
        foreach ($p in Work-Processes) { try { (Get-Process -Id $p.ProcessId -ErrorAction Stop).PriorityClass = "Idle"; $n++ } catch {} }
        $f = Freeze
        Say "ON: pause.flag set (no new builds/tests/game tests), $n running AnyPS5 work processes set to Idle priority, $f build processes frozen"
    }
    "off" {
        Remove-Item -LiteralPath $Flag -ErrorAction SilentlyContinue
        $t = Thaw
        $n = 0
        foreach ($p in Work-Processes) { try { (Get-Process -Id $p.ProcessId -ErrorAction Stop).PriorityClass = "BelowNormal"; $n++ } catch {} }
        Say "OFF: pause.flag removed, $t frozen build processes resumed, $n work processes back to BelowNormal"
    }
    default {
        $on = Test-Path -LiteralPath $Flag
        $w = @(Work-Processes)
        "game mode: " + $(if ($on) { "ON (" + (Get-Content -LiteralPath $Flag -Raw).Trim() + ")" } else { "OFF" })
        "AnyPS5 work processes running: $($w.Count)"
        $w | Group-Object Name | Sort-Object Count -Descending | ForEach-Object { "  $($_.Count) x $($_.Name)" }
    }
}
