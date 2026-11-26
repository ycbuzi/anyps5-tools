# Paste into Azure Cloud Shell (Bash), once (11-09). Lets azwin start the test VMs and each VM switch itself off.
G=anyps5-cus; S=$(az group show -n $G --query id -o tsv)
for v in anyps5-win anyps5-ws2016 anyps5-ws2019 anyps5-ws2022 anyps5-win10 anyps5-win11 anyps5-win11arm; do az vm identity assign -g $G -n $v --role "Virtual Machine Contributor" --scope $S -o none && echo "$v ok"; done
az network public-ip list -g $G --query "[?publicIPAllocationMethod=='Dynamic'].name" -o tsv | while read ip; do az network public-ip update -g $G -n $ip --allocation-method Static -o none && echo "$ip now static"; done
