# On azwin (11-09): Azure credit is $100 total until 2027-08-20, so the Windows test VMs are switched off.
# Disables the wake task, deallocates the six test VMs, then azwin itself. Restart one: Cloud Shell `az vm start -g anyps5-cus -n <vm>`.
Disable-ScheduledTask x64win-vmwake -ErrorAction SilentlyContinue | Out-Null
$h = @{ Metadata = 'true' }
$md = Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/instance/compute?api-version=2021-02-01'
$a = @{ Authorization = 'Bearer ' + (Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/').access_token }
$rg = "https://management.azure.com/subscriptions/$($md.subscriptionId)/resourceGroups/anyps5-cus/providers/Microsoft.Compute/virtualMachines"
foreach ($vm in 'anyps5-ws2016', 'anyps5-ws2019', 'anyps5-ws2022', 'anyps5-win10', 'anyps5-win11', 'anyps5-win11arm', 'anyps5-win') {
    try { Invoke-WebRequest -UseBasicParsing -Method Post -Headers $a -ContentType 'application/json' -Body '{}' "$rg/$vm/deallocate?api-version=2024-07-01" | ForEach-Object { "$vm deallocate sent ($($_.StatusCode))" } } catch { "$vm FAILED $($_.Exception.Message)" }
}
