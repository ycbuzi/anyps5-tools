# Run on anyps5-win through the portal's Run command (RunPowerShellScript), 11-08: turns on OpenSSH Server with key-only
# login for the admin account, so the PC tools and the Machines app connect with keys and Blu's password stays his.
$ErrorActionPreference = 'Continue'
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
$key = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKD3ZQ2xLXZwwKY4t8p+A4wlNKUa+OxEo9zpb59GUvKz anyps5-pc-azure'
$auth = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $auth -Value $key -Encoding ascii
icacls $auth /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
$conf = 'C:\ProgramData\ssh\sshd_config'
$text = Get-Content $conf -Raw
if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = $text -replace '(?m)^#?PasswordAuthentication .*$', 'PasswordAuthentication no'; if ($text -notmatch '(?m)^PasswordAuthentication no') { $text = "PasswordAuthentication no`r`n" + $text } }
Set-Content -Path $conf -Value $text -Encoding ascii
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -Value 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -PropertyType String -Force | Out-Null
if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null }
Restart-Service sshd
"sshd: $((Get-Service sshd).Status); key file: $((Get-Item $auth).Length) bytes; password login off"
