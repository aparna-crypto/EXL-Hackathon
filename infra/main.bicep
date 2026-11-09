// Model Risk Factory - Azure footprint for the demo (Stage 8).
// One resource group: storage, key vault, Log Analytics + App Insights, ACR Basic,
// Azure ML workspace, Container Apps environment + Streamlit app (0..1 replicas), budget.
// Deployed twice by deploy.sh: deployApp=false (infra + registry), push image, deployApp=true.

targetScope = 'resourceGroup'

@description('Azure region; Azure for Students restricts regions by policy - see docs/azure_deployment.md')
param location string = resourceGroup().location

@description('Short prefix for resource names (lowercase letters/digits)')
@minLength(3)
@maxLength(8)
param prefix string = 'mrf'

@description('Create the Container App (second pass, after the image is in ACR)')
param deployApp bool = false

@description('Image tag in ACR, e.g. model-risk-factory:20261108')
param imageTag string = 'model-risk-factory:latest'

@description('Role definition GUID of AcrPull, looked up by deploy.sh')
param acrPullRoleId string

@description('Only this IPv4 address may reach the UI (CIDR). Empty = public.')
param allowedIpCidr string = ''

@description('Create the cost budget (some offers do not support budgets)')
param deployBudget bool = true

@description('Monthly budget in the billing currency')
param budgetAmount int = 10

@description('Email address for budget alerts')
param alertEmail string = ''

@description('First day of the current month, yyyy-MM-01')
param budgetStartDate string = utcNow('yyyy-MM-01')

var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'model-risk-factory', stage: '8', data: 'public-or-synthetic-only' }

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: take('${prefix}st${suffix}', 24)
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('${prefix}-kv-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take('${prefix}acr${suffix}', 50)
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource mlWorkspace 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: '${prefix}-mlw-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    friendlyName: 'Model Risk Factory'
    storageAccount: storage.id
    keyVault: vault.id
    applicationInsights: appInsights.id
    containerRegistry: registry.id
    publicNetworkAccess: 'Enabled'
  }
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-app-id-${suffix}'
  location: location
  tags: tags
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, appIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource appEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-cae-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: '${prefix}-ui'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  dependsOn: [ acrPull ]
  properties: {
    managedEnvironmentId: appEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8501
        transport: 'auto'
        allowInsecure: false
        ipSecurityRestrictions: empty(allowedIpCidr) ? [] : [
          { name: 'deployer', action: 'Allow', ipAddressRange: allowedIpCidr }
        ]
      }
      registries: [ { server: registry.properties.loginServer, identity: appIdentity.id } ]
    }
    template: {
      containers: [
        {
          name: 'ui'
          image: '${registry.properties.loginServer}/${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
            { name: 'AZUREML_MLFLOW_URI', value: mlWorkspace.properties.mlFlowTrackingUri }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/_stcore/health', port: 8501 }, initialDelaySeconds: 20, periodSeconds: 30 }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = if (deployBudget && !empty(alertEmail)) {
  name: '${prefix}-budget'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 50, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
      actual80: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 80, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
      actual100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Actual', contactEmails: [ alertEmail ] }
      forecast100: { enabled: true, operator: 'GreaterThanOrEqualTo', threshold: 100, thresholdType: 'Forecasted', contactEmails: [ alertEmail ] }
    }
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output mlWorkspaceName string = mlWorkspace.name
output mlflowTrackingUri string = mlWorkspace.properties.mlFlowTrackingUri
output appUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
