targetScope = 'resourceGroup'

@minLength(3)
@maxLength(8)
@description('Lowercase name prefix. Use a new resource group dedicated to this sample.')
param namePrefix string = 'aigw'

param gatewayName string = '${namePrefix}${uniqueString(resourceGroup().id)}'
param modelAccountName string = 'ai${uniqueString(resourceGroup().id)}'
param appIdentityName string = 'id-${gatewayName}'

var modelName = 'gpt-5.4-mini'
var modelVersion = '2026-03-17'
var modelsUserRole = 'a6b68e3d-a1f0-4c60-8678-237c050cf4ea'
var inferenceRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource gateway 'Microsoft.ApiManagement/aigateways@2026-11-01' = {
  name: gatewayName
  location: 'eastus2'
  identity: { type: 'SystemAssigned' }
  properties: {}
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: appIdentityName
  location: 'westus2'
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: modelAccountName
  location: 'eastus2'
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: modelAccountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'sample-chat'
  sku: { name: 'GlobalStandard', capacity: 20 }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource inference 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, gateway.id, inferenceRole)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', inferenceRole)
    principalId: gateway.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource provider 'Microsoft.ApiManagement/aigateways/workspaces/modelProviders@2025-09-01-preview' = {
  name: '${gateway.name}/default/sample-foundry'
  properties: {
    kind: 'Foundry'
    displayName: 'Sample model account'
    foundry: {
      endpoint: account.properties.endpoint
      resourceIds: [account.id]
      authentication: {
        kind: 'ManagedIdentity'
        managedIdentity: { resource: 'https://cognitiveservices.azure.com/' }
      }
    }
  }
  dependsOn: [gateway, inference]
}

resource backendModel 'Microsoft.ApiManagement/aigateways/workspaces/modelProviders/models@2025-09-01-preview' = {
  parent: provider
  name: 'sample-chat'
  properties: {
    displayName: 'sample-chat'
    apiFormat: 'OpenAIChatCompletions'
    supportedEndpoints: ['/openai/v1/chat/completions']
    deployment: { resourceId: model.id, modelName: model.name, modelVersion: modelVersion }
  }
}

resource alias 'Microsoft.ApiManagement/aigateways/workspaces/aliases@2025-09-01-preview' = {
  name: '${gateway.name}/default/appservice-chat'
  properties: {
    aliasName: 'appservice-chat'
    backendModels: [{ resourceId: backendModel.id }]
  }
}

resource appModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(alias.id, appIdentity.id, modelsUserRole)
  scope: alias
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', modelsUserRole)
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output gatewayHost string = gateway.properties.frontend.defaultHostname
output appIdentityId string = appIdentity.id
output appIdentityClientId string = appIdentity.properties.clientId
