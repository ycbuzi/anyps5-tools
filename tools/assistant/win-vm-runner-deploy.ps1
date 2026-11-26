# Installs the AnyPS5 Windows PR runner (the same one as azwin) on a new Azure Windows test VM (11-09).
# Usage: powershell -File win-vm-runner-deploy.ps1 <ssh alias> <IP> "<Machines app name>"
#   e.g. win-vm-runner-deploy.ps1 azws2016 192.0.2.1 "Azure Windows Server 2016"
# Needs: the VM running with key-only SSH (win-vm-ssh-setup.ps1 through Run command) and azwin reachable.
# The toolchain (mingw64, cmake, Git, Python314) is copied VM to VM from azwin inside Azure, not through the PC.
param([Parameter(Mandatory)] [string]$Alias, [Parameter(Mandatory)] [string]$Ip, [string]$AppName = '')
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Join-Path $env:USERPROFILE '.ssh\config'
function Say($t) { Write-Host "[$Alias] $t" }

if (-not (Select-String -Path $cfg -Pattern "^Host $Alias\s*$" -Quiet)) {
    Add-Content $cfg "`nHost $Alias`n  HostName $Ip`n  User blu`n  IdentityFile ~/.ssh/id_ed25519_azure`n  BatchMode yes`n  StrictHostKeyChecking accept-new"
    Say 'added to ~/.ssh/config'
}
ssh -o ConnectTimeout=15 $Alias 'hostname' | Out-Host
if ($LASTEXITCODE -ne 0) { throw "ssh $Alias failed: is the VM running and win-vm-ssh-setup.ps1 applied?" }

Say 'azwin: key and toolchain archive'
if ((ssh azwin 'Test-Path C:\x64win\vmkey') -notmatch 'True') {
    $k = Join-Path $env:USERPROFILE '.ssh\azwin-vmkey'
    scp -q $k azwin:C:/x64win/vmkey; scp -q "$k.pub" azwin:C:/x64win/vmkey.pub
    ssh azwin "icacls C:\x64win\vmkey /inheritance:r /grant SYSTEM:F /grant Administrators:F /grant blu:F | Out-Null"
}
ssh azwin "New-Item -ItemType Directory -Force C:\x64win\out | Out-Null; if (-not (Test-Path C:\x64win\out\tools.zip)) { tar -a -cf C:\x64win\out\tools.zip -C C:\tools mingw64 cmake Git Python314 }; (Get-Item C:\x64win\out\tools.zip).Length/1MB"
$pub = (ssh azwin 'Get-Content C:\x64win\vmkey.pub').Trim()
if (-not $pub.StartsWith('ssh-ed25519')) { throw 'could not read the azwin key' }
ssh $Alias "`$f='C:\ProgramData\ssh\administrators_authorized_keys'; if (-not (Select-String -Path `$f -SimpleMatch '$pub' -Quiet)) { Add-Content `$f '$pub' -Encoding ascii }; New-Item -ItemType Directory -Force C:\x64win\in | Out-Null"

Say 'copying scripts'
foreach ($s in 'setup.ps1', 'prtest.ps1', 'live.ps1', 'jobrun.ps1') { scp -q (Join-Path $here "azwin\$s") "${Alias}:C:/x64win/$s"; if ($LASTEXITCODE) { throw "scp $s failed" } }

Say 'copying the toolchain from azwin (inside Azure)'
scp -q (Join-Path $here 'azwin\push-tools.ps1') azwin:C:/x64win/push-tools.ps1; ssh -n azwin "powershell -NoProfile -ExecutionPolicy Bypass -File C:\x64win\push-tools.ps1 $Ip"

Say 'running setup on the VM'
ssh $Alias 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\x64win\setup.ps1' | Out-Host

$wd = Join-Path $here 'machines-watchdog.ps1'
if (-not (Select-String -Path $wd -Pattern "'$Alias'" -SimpleMatch -Quiet)) {
    $line = "    @('$(if ($AppName) { $AppName } else { $Alias }) PR tests', '$Alias', `"if ((Get-ScheduledTask x64win-prtest -EA 0).State -in 'Ready','Running') { 'OK' }`", 'Enable-ScheduledTask x64win-prtest | Out-Null', 'ondemand'),"
    $t = Get-Content $wd -Raw
    $t = $t -replace "(\s+@\('Chromebook timers')", ("`r`n" + $line + '$1')
    [IO.File]::WriteAllText($wd, $t)
    Say 'added to the machines watchdog'
}
Say 'done'
