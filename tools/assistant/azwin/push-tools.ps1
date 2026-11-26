# On azwin: copies the toolchain package to new Windows test VMs inside Azure (11-09).
# Runs as a one-shot scheduled task (ssh started from inside an ssh session hangs on Windows).
# Usage: push-tools.ps1 <IP> [<IP> ...]   Log: C:\x64win\logs\push-tools.log
$log = 'C:\x64win\logs\push-tools.log'
function L($t) { Add-Content $log "$(Get-Date -Format 'MM-dd HH:mm:ss') $t" }
$o = @('-i', 'C:\x64win\vmkey', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=C:\x64win\known_hosts', '-o', 'LogLevel=ERROR')
foreach ($Ip in $args) {
    $has = & ssh.exe -n @o "blu@$Ip" 'Test-Path C:\tools\mingw64\bin\gcc.exe' 2>&1
    if ("$has" -match 'True') { L "$Ip already has the toolchain"; continue }
    & ssh.exe -n @o "blu@$Ip" 'New-Item -ItemType Directory -Force C:\x64win\in | Out-Null' 2>&1 | Out-Null
    $t = Get-Date
    $r = & scp.exe -q @o 'C:\x64win\out\tools.zip' "blu@${Ip}:C:/x64win/in/tools.zip" 2>&1
    L "$Ip scp exit $LASTEXITCODE in $([int]((Get-Date) - $t).TotalSeconds)s $r"
}
L 'push done'
