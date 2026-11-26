# Checks the on-demand setup from azwin (11-09): managed identity token, VM power states, public IP allocation.
$h = @{ Metadata = 'true' }
$md = Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/instance/compute?api-version=2021-02-01'
$tok = (Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/').access_token
$a = @{ Authorization = "Bearer $tok" }
$rg = "https://management.azure.com/subscriptions/$($md.subscriptionId)/resourceGroups/anyps5-cus/providers"
foreach ($vm in 'anyps5-ws2016', 'anyps5-ws2019', 'anyps5-ws2022', 'anyps5-win10', 'anyps5-win11', 'anyps5-win11arm') {
    $iv = Invoke-RestMethod -Headers $a "$rg/Microsoft.Compute/virtualMachines/$vm/instanceView?api-version=2024-07-01"
    "$vm $(($iv.statuses | Where-Object { $_.code -like 'PowerState/*' }).code)"
}
(Invoke-RestMethod -Headers $a "$rg/Microsoft.Network/publicIPAddresses?api-version=2024-05-01").value | ForEach-Object { "$($_.name) $($_.properties.publicIPAllocationMethod) $($_.properties.ipAddress)" }
