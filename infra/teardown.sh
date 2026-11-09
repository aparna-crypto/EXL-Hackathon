#!/usr/bin/env bash
# Delete everything deploy.sh created, including soft-deleted Key Vault and ML workspace.
# Usage: infra/teardown.sh            (RG defaults to mrf-demo-rg; asks for confirmation)
#        YES=1 infra/teardown.sh      (no prompt)
set -euo pipefail

RG="${RG:-mrf-demo-rg}"
command -v az >/dev/null || { echo "Azure CLI (az) is required"; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first"; exit 1; }

if [ "$(az group exists --name "$RG")" != true ]; then
  echo "Resource group $RG does not exist; nothing to delete."; exit 0
fi
az resource list --resource-group "$RG" --query '[].{name:name, type:type}' -o table
if [ "${YES:-0}" != 1 ]; then
  read -r -p "Delete resource group $RG and everything above? Type the group name to confirm: " ans
  [ "$ans" = "$RG" ] || { echo "Aborted."; exit 1; }
fi

SUB="$(az account show --query id -o tsv)"
KV="$(az keyvault list --resource-group "$RG" --query '[0].name' -o tsv)"
LOC="$(az group show --name "$RG" --query location -o tsv)"

# ML workspaces are soft-deleted by default; purge so the name and linked resources are released.
for ws in $(az resource list --resource-group "$RG" \
              --resource-type Microsoft.MachineLearningServices/workspaces --query '[].name' -o tsv); do
  echo "Purging ML workspace $ws..."
  az rest --method delete \
    --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.MachineLearningServices/workspaces/$ws?api-version=2024-04-01&forceToPurge=true" \
    || echo "  (purge request failed; the resource group delete below still removes it)"
done

echo "Deleting resource group $RG (takes a few minutes)..."
az group delete --name "$RG" --yes

if [ -n "$KV" ]; then
  echo "Purging soft-deleted Key Vault $KV..."
  az keyvault purge --name "$KV" --location "$LOC" || echo "  (already purged or purge not permitted)"
fi

echo "Remaining resource groups tagged project=model-risk-factory:"
az group list --tag project=model-risk-factory --query '[].name' -o tsv
echo "Teardown complete. The budget lived in the resource group and was removed with it."
