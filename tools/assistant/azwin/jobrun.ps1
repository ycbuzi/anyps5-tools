# Job board wrapper on anyps5-win: runs prtest.ps1 for one PR, waiting while the runner is busy (exit 3),
# and leaves C:\x64win\jobboard\<id>.state / .rc / .log for the PC's jobboard.py. Started as a one-shot scheduled task.
param([string]$Id, [string]$Pr)
$d = 'C:\x64win\jobboard'; New-Item -ItemType Directory -Force $d | Out-Null
$st = "$d\$Id"
while ($true) {
    Set-Content "$st.state" 'running'
    & powershell -NoProfile -ExecutionPolicy Bypass -File C:\x64win\prtest.ps1 $Pr *> "$st.log"
    $rc = $LASTEXITCODE
    if ($rc -ne 3) { break }
    Set-Content "$st.state" 'waiting'; Start-Sleep 120
}
Set-Content "$st.rc" $rc
Set-Content "$st.state" 'done'
Unregister-ScheduledTask -TaskName "jobboard-$Id" -Confirm:$false -ErrorAction SilentlyContinue
