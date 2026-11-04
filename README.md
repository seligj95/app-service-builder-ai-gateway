# Connect a Python agent through an AI gateway

Use a Python web app on App Service Builder to stream answers from a model
through a dedicated Azure AI Gateway. Microsoft Agent Framework uses managed
identity to call the gateway; the gateway uses its identity to call the model.

In this tutorial, you learn how to:

- Check the Python agent locally.
- Use workload managed identities without visitor sign-in.
- Stream a model response through the gateway.

The browser sends a message to Builder, which calls the stable `appservice-chat`
model alias through the gateway. Responses stream back to the chat interface.
Messages are independent; previous turns are not sent to the model.

## Prerequisites

- An Azure subscription with permission to create resources and role assignments
  in an isolated resource group.
- Azure CLI and the [Builder CLI](https://builder.appservice.azure.com/docs/quickstarts/builder-cli/),
  signed in to the intended subscription.
- A GitHub fork of this repository, with Builder source access configured as
  described in the shared CLI quickstart. Use the repository root.
- Dedicated AI Gateway availability and at least 20k TPM of GlobalStandard
  `gpt-5.4-mini` quota in East US 2. The template runs Builder in West US 2
  and the gateway and model account in East US 2.

> [!IMPORTANT]
> This is a public demo: anyone can invoke the app and incur model charges.
> Its in-process limits aren't authentication or a spending cap.
> Use synthetic prompts, an isolated DEV/TEST environment and a test window of
> at most two hours, then delete the resources.

The dedicated gateway preview's no-charge offer doesn't include Builder,
model inference or cross-region traffic. GlobalStandard processing may occur
outside the model account's region.

## 1. Create the sample resources

Clone your fork and open its root. In Bash, set your subscription, a **new**
resource group, and your fork's owner/name:

```bash
SUBSCRIPTION_ID="<your-subscription-id>"
RESOURCE_GROUP="rg-builder-ai-gateway-demo"
REPOSITORY="<your-github-owner>/<your-fork>"

az group create --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" --location westus2

az deployment group create --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" --name gateway-sample \
  --template-file infra/main.bicep

mkdir -p .azure
az deployment group show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" --name gateway-sample \
  --query properties.outputs --output json > .azure/deployment-outputs.json

APP_NAME=$(az deployment group show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" --name gateway-sample \
  --query properties.outputs.builderAppName.value --output tsv)
APP_URL=$(az deployment group show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$RESOURCE_GROUP" --name gateway-sample \
  --query properties.outputs.builderAppUrl.value --output tsv)
```

The template creates the dedicated gateway, one model deployment, an empty
Builder app and its managed identity. It grants the app identity
**AI Gateway Models User** only on the alias and the gateway identity
**Cognitive Services OpenAI User** only on the model account. Local model keys
and visitor sign-in are disabled. No customer app registration, callback,
MCP server, API key or application secret is needed.

The four app settings and a two-hour expiry are configured before source
deployment. Keep the saved outputs outside Git. Don't rerun deployment to
silently extend an approved test window.

## 2. Connect your fork

```bash
builder app source connect github --name "$APP_NAME" \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$RESOURCE_GROUP" \
  --repository "$REPOSITORY" --branch main
```

Complete any GitHub installation/access step described by the shared quickstart.
The supplied root `builder.yaml` selects Python 3.14 and starts Uvicorn.
Source connection and deployment are separate operations.

## 3. Deploy the app

```bash
builder app deploy --name "$APP_NAME" \
  --subscription "$SUBSCRIPTION_ID" --resource-group "$RESOURCE_GROUP"

printf '%s\n' "$APP_URL"
```

Wait for the deployment to succeed, then open the returned URL.

## 4. Verify the gateway chat

The page opens without visitor sign-in. Select **Explain AI Gateway** and send
the message. Text should appear in the transcript, followed by
**Response complete**. Try **Suggest an app idea** as a second independent turn.
Enter sends; Shift+Enter inserts a newline. **Stop** cancels a response without
replaying partial output.

If the demo returns HTTP 429, wait at least five seconds before another message.
The app admits at most two concurrent chats and one new chat every five seconds,
per process. It stops model calls at the configured deadline or after 12 model
requests, including retries. In-process counters reset on restart and multiply
with scaling; they don't guarantee a spending cap.

The client uses managed identity, the fixed gateway endpoint and a stable model
alias. It never falls back to a direct provider. See
[architecture and configuration](docs/architecture.md) for the exact endpoints,
permissions, token bounds and retry rules.

## Optional: run local checks

Install Python 3.14 to run the checks without Azure resources.

Clone this repository and open its root directory. Create an environment and
install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py config.py gateway.py tests
.venv/bin/python -m pip check
```

The checks use mocked managed identity and HTTP responses with the installed
Agent Framework and OpenAI clients. They don't sign in, send inference
requests, or create cloud resources.

The test suite finishes with `OK`. It checks anonymous browser SSE output, exact
Origin checking, expiration, bounded input, admission throttling, credential-safe failures, and model requests
sent only to the configured gateway.

The agent can retry one rejected model request before an accepted response or
text output. It doesn't restart partial output or switch providers.

## Clean up resources

Stop sending messages and delete the dedicated group you created:

```bash
az group delete --subscription "$SUBSCRIPTION_ID" \
  --name "$RESOURCE_GROUP" --yes
```

Wait for deletion to finish and verify the group is absent. This removes the
Builder app, gateway, model deployment/account, identities and scoped grants.
Normal service soft-delete retention can remain; don't purge unrelated retained
resources. The local checks create no Azure resources. Remove your local Python
environment when finished.

## Related content

- [Architecture and configuration](docs/architecture.md)
- [Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/)
- [Dedicated AI Gateway](https://ai.gateway.azure.com/docs/)
- [Python gateway reference](https://github.com/seligj95/app-service-ai-gateway-tier-python)
