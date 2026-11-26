#!/bin/bash
# Cloud Shell (11-09, Blu: "delete the Windows VMs"): deletes the 7 Windows test VMs in rg anyps5-cus and what only they use:
# OS/data disks, NICs, public IPs, then their VNETs and NSGs when nothing else is left on them. Linux/BSD VMs and the Key Vault stay.
G=anyps5-cus
VMS="anyps5-win anyps5-ws2016 anyps5-ws2019 anyps5-ws2022 anyps5-win10 anyps5-win11 anyps5-win11arm"
declare -a DISKS NICS IPS VNETS NSGS FOUND
for v in $VMS; do
  if ! az vm show -g $G -n $v -o none 2>/dev/null; then echo "$v: not found, skipped"; continue; fi
  FOUND+=($v)
  DISKS+=($(az vm show -g $G -n $v --query "[storageProfile.osDisk.managedDisk.id, storageProfile.dataDisks[].managedDisk.id][]" -o tsv))
  for n in $(az vm show -g $G -n $v --query "networkProfile.networkInterfaces[].id" -o tsv); do
    NICS+=($n)
    IPS+=($(az network nic show --ids $n --query "ipConfigurations[].publicIPAddress.id" -o tsv))
    NSGS+=($(az network nic show --ids $n --query "networkSecurityGroup.id" -o tsv))
    VNETS+=($(az network nic show --ids $n --query "ipConfigurations[].subnet.id" -o tsv | sed 's|/subnets/.*||'))
  done
done
uniq_() { printf '%s\n' "$@" | grep -v '^$' | grep -iv '^none$' | sort -u; }
echo; echo "Will delete these VMs: ${FOUND[*]}"
echo "Disks:";       uniq_ "${DISKS[@]}" | sed 's|.*/|  |'
echo "NICs:";        uniq_ "${NICS[@]}"  | sed 's|.*/|  |'
echo "Public IPs:";  uniq_ "${IPS[@]}"   | sed 's|.*/|  |'
echo "VNETs and NSGs, only if nothing else uses them:"; uniq_ "${VNETS[@]}" "${NSGS[@]}" | sed 's|.*/|  |'
echo; read -r -p "This cannot be undone. Type DELETE to go ahead: " ok
[ "$ok" = DELETE ] || { echo "Stopped, nothing deleted."; exit 1; }
for v in "${FOUND[@]}"; do az vm delete -g $G -n $v --yes --no-wait -o none && echo "$v: delete started"; done
for v in "${FOUND[@]}"; do az vm wait -g $G -n $v --deleted 2>/dev/null; echo "$v: deleted"; done
for id in $(uniq_ "${NICS[@]}");  do az network nic delete --ids $id -o none 2>/dev/null; echo "NIC ${id##*/}: gone"; done
for id in $(uniq_ "${IPS[@]}");   do az network public-ip delete --ids $id -o none 2>/dev/null; echo "IP ${id##*/}: gone"; done
for id in $(uniq_ "${DISKS[@]}"); do az disk delete --ids $id --yes -o none 2>/dev/null; echo "disk ${id##*/}: gone"; done
for id in $(uniq_ "${VNETS[@]}"); do
  used=$(az network vnet show --ids $id --query "length(subnets[].ipConfigurations[] || \`[]\`)" -o tsv 2>/dev/null)
  if [ "${used:-0}" = 0 ]; then az network vnet delete --ids $id -o none 2>/dev/null; echo "VNET ${id##*/}: gone"; else echo "VNET ${id##*/}: kept, $used other users"; fi
done
for id in $(uniq_ "${NSGS[@]}"); do
  used=$(az network nsg show --ids $id --query "[networkInterfaces[].id, subnets[].id][]" -o tsv 2>/dev/null | grep -c .)
  if [ "${used:-0}" = 0 ]; then az network nsg delete --ids $id -o none 2>/dev/null; echo "NSG ${id##*/}: gone"; else echo "NSG ${id##*/}: kept, $used other users"; fi
done
echo; echo "What is left in $G:"; az resource list -g $G --query "[].{name:name, type:type}" -o table
