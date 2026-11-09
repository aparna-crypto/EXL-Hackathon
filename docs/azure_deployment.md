# Azure deployment (Stage 8)

**Status: written and checked offline, not yet deployed.** Devin did not run anything against Azure
and holds no Azure credentials (brief: "DO NOT run anything against Azure yourself"). You run the
scripts below from your own machine after `az login`, then report the result.

## What gets created

One resource group (default `mrf-demo-rg`, region `uksouth`), defined in `infra/main.bicep`:

| Resource | SKU / setting | Why |
|---|---|---|
| Log Analytics workspace | PerGB2018, 30-day retention, **1 GB/day cap** | Container logs; caps the main runaway-cost risk |
| Application Insights | workspace-based | Monitoring; required by the ML workspace |
| Storage account | Standard_LRS, TLS 1.2, no public blobs | Default store of the ML workspace |
| Key Vault | standard, RBAC, soft delete 7 days | Required by the ML workspace; no secrets are written by the scripts |
| Container Registry | **Basic**, admin user off | Holds the UI image |
| Azure ML workspace | Basic, system identity, **no compute** | MLflow tracking target for `TRACKING_BACKEND=azureml` |
| User-assigned identity + AcrPull role | - | Lets the app pull from ACR without passwords |
| Container Apps environment | Consumption | Hosts the app |
| Container App `mrf-ui` | 0.5 vCPU / 1 GiB, **min 0 / max 1 replicas**, HTTPS only, **IP allowlist** | Serves the Streamlit UI |
| Budget `mrf-budget` | monthly, default **10**, alerts at 50/80/100% actual and 100% forecast | Spend alerts |

The image (`Dockerfile`) contains the code, config, model cards, reports and audit logs from the
commit. It contains no `.env`, no API key and no datasets (`.dockerignore`). It runs as a non-root
user with `LLM_PROVIDER=fake`; the UI makes no LLM calls.

## Before you deploy (Azure for Students)

1. Install the Azure CLI and Docker, then `az login` and `az account set --subscription "<name>"`.
2. **Region.** Azure for Students limits regions by policy. Check which are allowed:
   `az policy assignment list --query "[].{name:displayName, params:parameters}" -o json`
   If `uksouth` is not allowed, pass e.g. `LOCATION=swedencentral` (any allowed region with
   Container Apps and Azure ML).
3. **Budget.** Budgets are not available on every offer type. If step 1 of the deploy fails on the
   budget, re-run with `DEPLOY_BUDGET=false`. Azure for Students already has a hard spending limit:
   services stop when the credit runs out, so the budget is an early warning, not the safety net.
4. **ACR Tasks** (`az acr build`) can be blocked on free/student subscriptions, so the default builds
   the image locally with Docker. Use `BUILD=acr` if you have no local Docker.

## Deploy

```bash
ALERT_EMAIL=you@example.com infra/deploy.sh
# optional: RG=... LOCATION=... PREFIX=... BUDGET=10 DEPLOY_BUDGET=false ALLOWED_IP=any BUILD=acr
```

What it does: registers the resource providers, creates the group, deploys the infrastructure with no
app, builds and pushes the image (tagged with the git commit), then deploys again with the Container
App. It prints the app URL, ML workspace name and MLflow tracking URI. Only your current public IP
can open the UI unless you set `ALLOWED_IP=any`. The first request after idle takes about 30-60 s
because the app scales from zero.

**Send back:** the printed outputs, or the full error text if any step fails.

Optional, to log pipeline runs to the workspace from your machine:
`pip install -r requirements-azure.txt`, then
`TRACKING_BACKEND=azureml AZUREML_MLFLOW_URI=<printed mlflowTrackingUri> make run MODEL=credit_scorecard`.

## Cost estimate

Approximate list prices in USD, checked against Azure's public pricing pages as I understood them,
**not** against your subscription. Confirm with the Azure pricing calculator for your region.

| Item | While deployed and idle | Notes |
|---|---|---|
| Container Registry Basic | ~$5 / month (~$0.17 / day) | Fixed daily charge: the main cost |
| Container App | ~$0 | Scales to zero; usage is within the monthly free grant (180k vCPU-s, 360k GiB-s, 2M requests) for demo traffic |
| Log Analytics + App Insights | ~$0-2 / month | Small ingestion; capped at 1 GB/day |
| Storage, Key Vault | < $1 / month | A few MB and a handful of operations |
| Azure ML workspace | $0 | No compute is created; the workspace itself is not billed |
| **Total** | **about $5-8 / month** | A week-long demo is roughly $1-2 |

With $20 of credit, deploying for the demo and tearing down straight after is comfortably inside it.
The 10 budget triggers emails at 5, 8 and 10.

## Shut everything down

```bash
infra/teardown.sh            # lists the resources, asks you to type the group name
```

It purges the ML workspace (otherwise soft-deleted), deletes the resource group (which removes the
budget too), purges the soft-deleted Key Vault and lists any group still tagged
`project=model-risk-factory`. Check afterwards in the portal under **Cost Management > Cost
analysis** a day later; costs appear with a delay of up to 24 h.

Cheaper pause without deleting: there is little to pause. The app already scales to zero; ACR Basic
is the only fixed charge and can only be stopped by deleting it.

## Checked offline

- `bicep build` and `bicep lint`: no errors or warnings (Bicep CLI 0.49.2). Also run in CI.
- `shellcheck` on both scripts: clean. Also run in CI.
- `docker build`, then running the image: health endpoint OK; all seven UI pages render with no
  exceptions inside the container; `pip check` clean; image has no `.env`.
- `tests/test_infra.py`: required resources present, min 0 / max 1 replicas, ACR Basic, no secrets
  in the image, strict and executable scripts, teardown purges.

Not checked: an actual deployment (ARM validation, region and offer policies, role propagation,
image pull, budget support on Azure for Students).
