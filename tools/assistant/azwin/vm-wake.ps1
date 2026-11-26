# On azwin (11-09): starts the on-demand Windows test VMs when GitHub has PR heads they have not seen.
# Scheduled task x64win-vmwake, every 30 minutes. The VMs deallocate themselves when idle (selfstop.ps1).
# Needs azwin's managed identity with Virtual Machine Contributor on rg anyps5-cus. Log: C:\x64win\logs\vm-wake.log
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$W = 'C:\x64win'; $seenFile = "$W\vm-wake-seen.txt"
$vms = 'anyps5-ws2016', 'anyps5-ws2019', 'anyps5-ws2022', 'anyps5-win10', 'anyps5-win11', 'anyps5-win11arm'
function L($t) { Add-Content "$W\logs\vm-wake.log" "$(Get-Date -Format 'MM-dd HH:mm:ss') $t" }
$cut = (Get-Date).ToUniversalTime().AddDays(-2)
$heads = @()
for ($p = 1; $p -le 30; $p++) {
    try { $page = Invoke-RestMethod -TimeoutSec 60 "https://api.github.com/repos/boykopovar/AnyPS5/pulls?state=open&sort=updated&direction=desc&per_page=100&page=$p" } catch { L "GitHub API page $p failed: $($_.Exception.Message)"; exit 1 }
    $recent = @($page | Where-Object { ([datetime]$_.updated_at).ToUniversalTime() -ge $cut })
    $heads += $recent | ForEach-Object { $_.head.sha }
    if ($page.Count -lt 100 -or $recent.Count -lt $page.Count) { break }
}
$seen = @(if (Test-Path $seenFile) { Get-Content $seenFile })
$new = @($heads | Where-Object { $seen -notcontains $_ })
if (-not $new) { exit 0 }
$h = @{ Metadata = 'true' }
try {
    $md = Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/instance/compute?api-version=2021-02-01'
    $tok = (Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/').access_token
} catch { L "no managed identity token: $($_.Exception.Message)"; exit 1 }
$ok = 0
foreach ($vm in $vms) {
    try {
        Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $tok" } -ContentType 'application/json' -Body '{}' -TimeoutSec 60 "https://management.azure.com/subscriptions/$($md.subscriptionId)/resourceGroups/anyps5-cus/providers/Microsoft.Compute/virtualMachines/$vm/start?api-version=2024-07-01" | Out-Null
        $ok++
    } catch { L "start $vm failed: $($_.Exception.Message)" }
}
L "$($new.Count) new PR heads; start sent to $ok of $($vms.Count) VMs"
if ($ok) { Set-Content $seenFile @(($seen + $new) | Select-Object -Unique | Select-Object -Last 5000) }
