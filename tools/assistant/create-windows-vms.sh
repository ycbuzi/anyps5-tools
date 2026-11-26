#!/bin/bash
# Creates the on-demand Windows test VMs for Blu's AnyPS5 PR testing (11-08). Run it yourself in Azure Cloud Shell (Bash).
# It asks for ONE admin password (hidden, never shown, logged or saved) and uses it for every VM; afterwards the PC tools
# switch each VM to key-only SSH, so the password stays with Blu. Each VM is created, then stopped (deallocated) so it only
# costs its disk until a test starts it. Already existing VMs are skipped. Blu approved the cost on 11-08.
set -u
RG=anyps5-cus
ALLOW="192.0.2.1 192.0.2.1"
# 11-09: the first run failed every VM because Azure rejected the password (complexity or a pasted control character);
# it is now checked here and asked again until Azure's rules are met, before anything is created.
printf '\e[?2004l'
GEN=""
while :; do
  read -r -s -p "Admin password for the Windows VMs (just press Enter to have a strong one made and kept in Key Vault): " PW; echo
  if [ -z "$PW" ]; then
    KV=$(az keyvault list -g $RG --query "[?starts_with(name,'anyps5-kv')].name | [0]" -o tsv 2>/dev/null)
    if [ -z "$KV" ]; then az provider register -n Microsoft.KeyVault --wait -o none; KV="anyps5-kv-$(openssl rand -hex 3)"; az keyvault create -g $RG -n "$KV" -l centralus --enable-rbac-authorization false -o none || { echo "Could not create the Key Vault."; exit 1; }; fi
    PW=$(az keyvault secret show --vault-name "$KV" -n windows-vm-password --query value -o tsv 2>/dev/null)
    if [ -n "$PW" ]; then echo "Using the password already kept in Key Vault $KV ('windows-vm-password')."; GEN=1; break; fi
    PW="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 20)Aa7#"; GEN=1
    az keyvault secret set --vault-name "$KV" -n windows-vm-password --value "$PW" -o none || { echo "Could not save to Key Vault $KV."; exit 1; }
    echo "Made a 24-character password and saved it in Key Vault $KV as 'windows-vm-password' (portal: Key vaults > $KV > Secrets)."
    break
  fi
  PW=${PW//$'\r'/}; PW=${PW//$'\e[200~'/}; PW=${PW//$'\e[201~'/}; PW=${PW#\[200~}; PW=${PW%\[201~}
  if [ -n "$(printf %s "$PW" | LC_ALL=C tr -d '[:print:]')" ]; then echo "It contains a hidden/control character (an arrow key or a paste code). Type it plainly."; continue; fi
  n=0; [[ $PW =~ [A-Z] ]] && n=$((n+1)); [[ $PW =~ [a-z] ]] && n=$((n+1)); [[ $PW =~ [0-9] ]] && n=$((n+1)); [[ $PW =~ [^A-Za-z0-9] ]] && n=$((n+1))
  if [[ $PW =~ [[:cntrl:]] ]]; then echo "It contains a control character (pasted?). Type it instead."; continue; fi
  if [ ${#PW} -lt 12 ] || [ ${#PW} -gt 123 ]; then echo "It must be 12 to 123 characters."; continue; fi
  c(){ [[ $PW =~ $1 ]] && echo yes || echo no; }; echo "Got ${#PW} characters: uppercase $(c '[A-Z]'), lowercase $(c '[a-z]'), digit $(c '[0-9]'), symbol $(c '[^A-Za-z0-9]')"
  if [ $n -lt 3 ]; then echo "It needs at least 3 of: uppercase, lowercase, digit, symbol."; continue; fi
  case ${PW,,} in *blu*|*admin*|*password*) echo "Azure rejects passwords containing the user name or common words."; continue ;; esac
  read -r -s -p "Type it again: " PW2; echo; PW2=${PW2//$'\r'/}; PW2=${PW2//$'\e[200~'/}; PW2=${PW2//$'\e[201~'/}; PW2=${PW2#\[200~}; PW2=${PW2%\[201~}
  [ "$PW" = "$PW2" ] && break; echo "They did not match."
done
case $PW in [@-]*) echo "It cannot start with @ or - (the Azure CLI reads those as a file or an option)."; exit 1 ;; esac
unset PW2

nsg_for() {  # one locked-down NSG per region: RDP 3389 and SSH 22 only from Blu's home and VPN
  local loc=$1 name="anyps5-win-nsg-$1"
  if ! az network nsg show -g $RG -n "$name" -o none 2>/dev/null; then
    az network nsg create -g $RG -n "$name" -l "$loc" -o none
    az network nsg rule create -g $RG --nsg-name "$name" -n allow-blu --priority 100 --access Allow --protocol Tcp \
      --direction Inbound --source-address-prefixes $ALLOW --destination-port-ranges 22 3389 -o none
  fi
  echo "$name"
}

# 11-09: Azure often has no free capacity for one size in one region ("SkuNotAvailable ... Capacity Restrictions"),
# so each VM tries several 2-vCPU sizes in every allowed region, Spot first, remembering what failed and what worked.
X64_SIZES="Standard_D2as_v5 Standard_D2s_v5 Standard_D2ads_v5 Standard_D2as_v4 Standard_D2s_v4 Standard_D2s_v3 Standard_B2ms Standard_B2s"
ARM_SIZES="Standard_D2pls_v5 Standard_D2ps_v5 Standard_D2pds_v5 Standard_B2pls_v2 Standard_B2ps_v2"
REGIONS="centralus eastus westus3 mexicocentral belgiumcentral"
MAX_TRIES=14
declare -A OFFERED BAD FULL  # FULL[region:kind]: that region's vCPU quota for Spot or regular VMs is used up
# Azure for Students: 3 Spot vCPUs per region and a stopped Spot VM still counts, so one 2-vCPU Spot VM per region;
# regular VMs only count while running, so the rest are made as regular VMs (they stay stopped until a test).
offered() {  # sizes this subscription may use in a region (cached)
  [ -n "${OFFERED[$1]+x}" ] || OFFERED[$1]=" $(az vm list-skus -l "$1" --resource-type virtualMachines --query "[?length(restrictions)==\`0\`].name" -o tsv 2>/dev/null | tr '\n' ' ') "
  [[ ${OFFERED[$1]} == *" $2 "* ]]
}
GOOD_X64=""; GOOD_ARM=""

# name | image | license | arch
VMS=(
"anyps5-ws2016|MicrosoftWindowsServer:WindowsServer:2016-datacenter-smalldisk-g2:latest|none|x64"
"anyps5-ws2019|MicrosoftWindowsServer:WindowsServer:2019-datacenter-smalldisk-g2:latest|none|x64"
"anyps5-ws2022|MicrosoftWindowsServer:WindowsServer:2022-datacenter-smalldisk-g2:latest|none|x64"
"anyps5-win10|MicrosoftWindowsDesktop:Windows-10:win10-22h2-pro-g2:latest|Windows_Client|x64"
"anyps5-win11|MicrosoftWindowsDesktop:windows-11:win11-24h2-pro:latest|Windows_Client|x64"
"anyps5-win11arm|MicrosoftWindowsDesktop:windows11preview-arm64:win11-24h2-pro:latest|Windows_Client|arm"
)

cleanup() {
  az vm delete -g $RG -n "$1" --yes -o none 2>/dev/null
  az network nic delete -g $RG -n "${1}VMNic" -o none 2>/dev/null
  az network public-ip delete -g $RG -n "${1}PublicIP" -o none 2>/dev/null
  az network vnet delete -g $RG -n "${1}VNET" -o none 2>/dev/null
}

declare -a RESULT
for entry in "${VMS[@]}"; do
  IFS='|' read -r NAME IMAGE LIC ARCH <<< "$entry"
  if az vm show -g $RG -n "$NAME" -o none 2>/dev/null; then RESULT+=("$NAME: already exists, skipped"); continue; fi
  if [ "$ARCH" = arm ]; then SIZES=$ARM_SIZES; GOOD=$GOOD_ARM; else SIZES=$X64_SIZES; GOOD=$GOOD_X64; fi
  CANDS="$GOOD"
  for k in spot regular; do for l in $REGIONS; do for z in $SIZES; do CANDS+=" $l:$z:$k"; done; done; done
  made=""; tries=0; : > /tmp/err-$NAME.log; cleanup "$NAME"
  for c in $CANDS; do
    [ -n "${BAD[$c]+x}" ] && continue
    IFS=':' read -r LOC SIZE KIND <<< "$c"
    [ -n "${FULL[$LOC:$KIND]+x}" ] && continue
    offered "$LOC" "$SIZE" || { BAD[$c]=1; continue; }
    tries=$((tries+1)); [ $tries -gt $MAX_TRIES ] && break
    NSG=$(nsg_for "$LOC")
    EXTRA=(); [ "$KIND" = spot ] && EXTRA+=(--priority Spot --eviction-policy Deallocate --max-price -1)
    [ "$LIC" != none ] && EXTRA+=(--license-type "$LIC")
    case $IMAGE in *smalldisk*) EXTRA+=(--os-disk-size-gb 64) ;; esac
    [ "$ARCH" = arm ] && EXTRA+=(--security-type Standard)
    echo "== $NAME: trying $LOC $SIZE ($KIND)"
    if az vm create -g $RG -n "$NAME" -l "$LOC" --image "$IMAGE" --size "$SIZE" --admin-username blu --admin-password="$PW"          --nsg "$NSG" --public-ip-sku Standard --os-disk-delete-option Delete --nic-delete-option Delete          --storage-sku StandardSSD_LRS --tags project=anyps5 role=windows-pr-test "${EXTRA[@]}" -o none 2>/tmp/err-$NAME.txt; then
      IP=$(az vm show -d -g $RG -n "$NAME" --query publicIps -o tsv)
      az vm deallocate -g $RG -n "$NAME" -o none
      if [ "$ARCH" = arm ]; then GOOD_ARM=$c; else GOOD_X64=$c; fi
      made="$NAME: created in $LOC, $SIZE $KIND, IP $IP (now stopped until a test starts it)"; break
    fi
    cat /tmp/err-$NAME.txt >> /tmp/err-$NAME.log
    WHY=$(grep -m1 -oE 'SkuNotAvailable|QuotaExceeded|OperationNotAllowed|ImageNotFound|PlatformImageNotFound|InvalidParameter[^.]*\.[^.]*|Spot[^.]*not[^.]*|BadRequest[^.]*|Conflict[^.]*|[A-Za-z]*[Qq]uota[A-Za-z]*' /tmp/err-$NAME.txt | head -1)
    echo "   failed: ${WHY:-unknown (see /tmp/err-$NAME.log)}"
    cleanup "$NAME"
    case $WHY in
      *assword*) echo "Azure rejected the password itself; stopping."; exit 1 ;;
      ImageNotFound|PlatformImageNotFound) break ;;
      *[Qq]uota*) FULL[$LOC:$KIND]=1 ;;
      *) BAD[$c]=1 ;;
    esac
  done
  RESULT+=("${made:-$NAME: NOT created after $((tries>MAX_TRIES?MAX_TRIES:tries)) tries (see /tmp/err-$NAME.log)}")
done
unset PW
echo; echo "===== Windows VMs ====="; printf '%s\n' "${RESULT[@]}"
echo "Done. Tell Claude 'ok' and it will set up key login, the PR runner and the Machines app entries."
