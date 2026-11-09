#!/usr/bin/env bash
# Deploy the Model Risk Factory demo to one Azure resource group. Run it yourself after `az login`.
# Usage: ALERT_EMAIL=you@example.com infra/deploy.sh
# Optional env: RG (mrf-demo-rg), LOCATION (uksouth), PREFIX (mrf), BUDGET (10), DEPLOY_BUDGET (true),
#               ALLOWED_IP (auto-detected public IPv4; "any" = public UI), BUILD (local|acr).
set -euo pipefail

RG="${RG:-mrf-demo-rg}"
LOCATION="${LOCATION:-uksouth}"
PREFIX="${PREFIX:-mrf}"
BUDGET="${BUDGET:-10}"
DEPLOY_BUDGET="${DEPLOY_BUDGET:-true}"
BUILD="${BUILD:-local}"
TAG="model-risk-factory:$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

: "${ALERT_EMAIL:?set ALERT_EMAIL to receive budget alerts}"
command -v az >/dev/null || { echo "Azure CLI (az) is required"; exit 1; }
az account show >/dev/null || { echo "Run 'az login' first"; exit 1; }
[ "$BUILD" = acr ] || command -v docker >/dev/null || { echo "docker is required (or BUILD=acr)"; exit 1; }

if [ -z "${ALLOWED_IP:-}" ]; then
  ALLOWED_IP="$(curl -fsS https://api.ipify.org || true)"
fi
case "$ALLOWED_IP" in
  any) CIDR="" ;;
  "") echo "Could not detect your public IP; set ALLOWED_IP=<ipv4> or ALLOWED_IP=any"; exit 1 ;;
  *) CIDR="$ALLOWED_IP/32" ;;
esac

echo "Subscription: $(az account show --query name -o tsv)"
echo "Resource group $RG in $LOCATION, budget $BUDGET/month -> $ALERT_EMAIL, UI allowed from: ${CIDR:-anyone}"

for ns in Microsoft.App Microsoft.ContainerRegistry Microsoft.MachineLearningServices Microsoft.KeyVault \
          Microsoft.Storage Microsoft.OperationalInsights Microsoft.Insights Microsoft.ManagedIdentity \
          Microsoft.Consumption; do
  az provider register --namespace "$ns" --wait >/dev/null
done

az group create --name "$RG" --location "$LOCATION" --tags project=model-risk-factory -o none
ACR_PULL="$(az role definition list --name AcrPull --query '[0].name' -o tsv)"

deploy() {  # $1 = deployApp
  az deployment group create --resource-group "$RG" --name "mrf-$1-$(date +%s)" \
    --template-file "$HERE/main.bicep" \
    --parameters location="$LOCATION" prefix="$PREFIX" deployApp="$1" imageTag="$TAG" \
                 acrPullRoleId="$ACR_PULL" allowedIpCidr="$CIDR" deployBudget="$DEPLOY_BUDGET" \
                 budgetAmount="$BUDGET" alertEmail="$ALERT_EMAIL" \
    --query properties.outputs -o json
}

echo "1/3 Infrastructure (registry, workspace, environment, budget)..."
OUT="$(deploy false)"
ACR="$(echo "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["registryName"]["value"])')"

echo "2/3 Image $TAG -> $ACR ($BUILD build)..."
if [ "$BUILD" = acr ]; then
  az acr build --registry "$ACR" --image "$TAG" "$ROOT"
else
  az acr login --name "$ACR"
  SERVER="$(az acr show --name "$ACR" --query loginServer -o tsv)"
  docker build --platform linux/amd64 -t "$SERVER/$TAG" "$ROOT"
  docker push "$SERVER/$TAG"
fi

echo "3/3 Container App..."
OUT="$(deploy true)"
echo "$OUT" | python3 -c 'import json,sys; o=json.load(sys.stdin); [print(f"{k}: {v[\"value\"]}") for k,v in o.items()]'
echo "Done. The UI scales to zero when idle; the first request takes ~30-60 s. Tear down with infra/teardown.sh"
