# Windows containers on anyps5-win (11-08): Containers feature + Docker CE static binaries from download.docker.com,
# then the ltsc2025 Server Core image (process isolation; host and image are both build 26100).
# Step 1 (before reboot): .\containers.ps1 install    Step 2 (after reboot): .\containers.ps1 image
param([string]$Step = 'install')
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'
if ($Step -eq 'install') {
    $f = Install-WindowsFeature -Name Containers
    "Containers feature: $($f.ExitCode) restart needed: $($f.RestartNeeded)"
    if (-not (Test-Path 'C:\Program Files\Docker\dockerd.exe')) {
        $idx = (Invoke-WebRequest -UseBasicParsing 'https://download.docker.com/win/static/stable/x86_64/').Links.href |
            Where-Object { $_ -match '^docker-(\d+)\.(\d+)\.(\d+)\.zip$' } |
            Sort-Object { [version]($_ -replace '^docker-|\.zip$', '') } | Select-Object -Last 1
        Invoke-WebRequest -UseBasicParsing "https://download.docker.com/win/static/stable/x86_64/$idx" -OutFile C:\x64win\dl\docker.zip
        tar -xf C:\x64win\dl\docker.zip -C 'C:\Program Files'
        Remove-Item C:\x64win\dl\docker.zip
        & 'C:\Program Files\Docker\dockerd.exe' --register-service
        "docker installed: $idx"
    }
} else {
    Start-Service docker
    & 'C:\Program Files\Docker\docker.exe' pull -q mcr.microsoft.com/windows/servercore:ltsc2025
    & 'C:\Program Files\Docker\docker.exe' run --rm --isolation=process mcr.microsoft.com/windows/servercore:ltsc2025 cmd /c ver
}
