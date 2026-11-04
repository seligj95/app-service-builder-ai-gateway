targetScope = 'resourceGroup'

param appName string
param gatewayHost string
param identityId string
param identityClientId string
param publicHost string
param expiresAt string

resource app 'Microsoft.Web/builderApps@2026-09-01-preview' = {
  name: appName
  location: 'westus2'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    versionConfiguration: {
      variables: [
        { name: 'AZURE_AI_GATEWAY_HOST', value: gatewayHost }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
        { name: 'APP_PUBLIC_HOST', value: publicHost }
        { name: 'SAMPLE_EXPIRES_AT', value: expiresAt }
      ]
    }
  }
}
