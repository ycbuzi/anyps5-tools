# Run on a new AnyPS5 Windows test VM through Azure Run command (11-09): turns on OpenSSH Server with key-only login for
# the admin account, so the PC tools, azwin and the Machines app connect with keys. Works on Server 2016 (no built-in
# OpenSSH: Microsoft's Win32-OpenSSH release from GitHub is installed) and on 2019/2022/2025/Windows 10/11.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cap) { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
}
if (-not (Get-Service sshd -ErrorAction SilentlyContinue)) {
    $zip = "$env:TEMP\OpenSSH-Win64.zip"
    Invoke-WebRequest 'https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive $zip 'C:\Program Files' -Force
    & powershell -NoProfile -ExecutionPolicy Bypass -File 'C:\Program Files\OpenSSH-Win64\install-sshd.ps1' | Out-Null
    $mp = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if ($mp -notlike '*OpenSSH-Win64*') { [Environment]::SetEnvironmentVariable('Path', "$mp;C:\Program Files\OpenSSH-Win64", 'Machine') }
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
New-Item -ItemType Directory -Force C:\ProgramData\ssh | Out-Null
$keys = @(
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKD3ZQ2xLXZwwKY4t8p+A4wlNKUa+OxEo9zpb59GUvKz anyps5-pc-azure'
)
$auth = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $auth -Value $keys -Encoding ascii
icacls $auth /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
$conf = 'C:\ProgramData\ssh\sshd_config'
if (-not (Test-Path $conf)) { Copy-Item 'C:\Program Files\OpenSSH-Win64\sshd_config_default' $conf -ErrorAction SilentlyContinue }
$text = Get-Content $conf -Raw
if ($text -notmatch 'administrators_authorized_keys') { foreach ($dflt in 'C:\Program Files\OpenSSH-Win64\sshd_config_default', 'C:\Windows\System32\OpenSSH\sshd_config_default') { if (Test-Path $dflt) { $text = Get-Content $dflt -Raw; break } } }
if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = $text -replace '(?m)^#?PasswordAuthentication .*$', 'PasswordAuthentication no'; if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = "PasswordAuthentication no`r`n" + $text } }
Set-Content -Path $conf -Value $text -Encoding ascii
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force | Out-Null
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null }
Set-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -Profile Any -Enabled True -ErrorAction SilentlyContinue
icacls C:\ProgramData\ssh /inheritance:r /grant 'SYSTEM:(OI)(CI)F' /grant 'Administrators:(OI)(CI)F' /grant 'Authenticated Users:(OI)(CI)RX' /T /C /Q | Out-Null
Get-ChildItem C:\ProgramData\ssh -Filter 'ssh_host_*_key' | ForEach-Object { icacls $_.FullName /inheritance:r /grant 'SYSTEM:F' /grant 'Administrators:F' /Q | Out-Null }
icacls $auth /inheritance:r /grant:r 'SYSTEM:F' /grant:r 'Administrators:F' /remove 'Authenticated Users' /Q | Out-Null
if (-not (Get-NetFirewallRule -Name anyps5-ssh-22 -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name anyps5-ssh-22 -DisplayName 'AnyPS5 SSH 22' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -Profile Any | Out-Null }
Restart-Service sshd
"$env:COMPUTERNAME sshd: $((Get-Service sshd).Status); key file: $((Get-Item $auth).Length) bytes; password login off"
