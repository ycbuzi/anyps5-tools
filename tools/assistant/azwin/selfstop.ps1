# On the on-demand Windows test VMs (11-09): deallocates this VM through its managed identity so it stops billing.
# prtest.ps1 -Auto calls it when there is nothing new to test. It does nothing when C:\x64win\autostop is missing
# (azwin), when someone is logged on, within 20 minutes of boot, or while C:\x64win\keepon exists.
$W = 'C:\x64win'
function L($t) { Add-Content "$W\logs\selfstop.log" "$(Get-Date -Format 'MM-dd HH:mm:ss') $t" }
if (-not (Test-Path "$W\autostop") -or (Test-Path "$W\keepon")) { exit 0 }
$up = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
if ($up.TotalMinutes -lt 20) { L "up $([int]$up.TotalMinutes) min, not stopping yet"; exit 0 }
if ((& quser 2>$null) -match '\sActive\s') { L 'someone is logged on, not stopping'; exit 0 }
$h = @{ Metadata = 'true' }
try {
    $md = Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/instance/compute?api-version=2021-02-01'
    $tok = (Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/').access_token
    L "idle, deallocating $($md.name)"
    Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $tok" } -ContentType 'application/json' -Body '{}' -TimeoutSec 60 "https://management.azure.com$($md.resourceId)/deallocate?api-version=2024-07-01" | Out-Null
} catch { L "deallocate failed: $($_.Exception.Message)" }
