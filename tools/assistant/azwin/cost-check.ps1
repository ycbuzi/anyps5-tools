# Lists what bills in rg anyps5-cus (11-09): every VM's size, priority, region and power state, and every disk's SKU and size.
$h = @{ Metadata = 'true' }
$md = Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/instance/compute?api-version=2021-02-01'
$a = @{ Authorization = 'Bearer ' + (Invoke-RestMethod -Headers $h -TimeoutSec 10 'http://192.0.2.1/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/').access_token }
$rg = "https://management.azure.com/subscriptions/$($md.subscriptionId)/resourceGroups/anyps5-cus/providers"
foreach ($v in (Invoke-RestMethod -Headers $a "$rg/Microsoft.Compute/virtualMachines?api-version=2024-07-01").value) {
    $iv = Invoke-RestMethod -Headers $a "$rg/Microsoft.Compute/virtualMachines/$($v.name)/instanceView?api-version=2024-07-01"
    $os = if ($v.properties.storageProfile.osDisk.osType) { $v.properties.storageProfile.osDisk.osType } else { '?' }
    "VM $($v.name) $($v.location) $($v.properties.hardwareProfile.vmSize) $(if ($v.properties.priority) { $v.properties.priority } else { 'Regular' }) $os $(($iv.statuses | Where-Object { $_.code -like 'PowerState/*' }).code -replace 'PowerState/', '')"
}
foreach ($d in (Invoke-RestMethod -Headers $a "$rg/Microsoft.Compute/disks?api-version=2024-03-02").value) { "DISK $($d.name) $($d.sku.name) $($d.properties.diskSizeGB)GB" }
