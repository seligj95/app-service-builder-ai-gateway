# Connect a Builder app to AI Gateway

The app sends Chat Completions requests to the dedicated AI Gateway alias
`appservice-chat`. The gateway authenticates to the model account with its
managed identity. Responses stream back to the browser as server-sent events.

The hosted walkthrough is **Under construction**. The [README](../README.md)
currently provides local checks.

## Identity and access

Use Builder-managed Microsoft Entra sign-in with **Sign-in** set to **Required**.
Pin the tenant and approved user object IDs in Builder and in the app settings.
You don't need a customer app registration or application secret.
Follow the shared [authentication](https://builder.appservice.azure.com/docs/guides/authentication/)
and [managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/)
instructions.

| Identity | Access |
|---|---|
| Approved users | Builder sign-in and exact tenant/object-ID rules |
| App user-assigned managed identity | AI Gateway Models User on the `appservice-chat` alias |
| Gateway managed identity | Cognitive Services OpenAI User on the model account |

Builder verifies the visitor and replaces incoming identity and forwarding
headers. The app checks those trusted claims and the exact `X-Forwarded-Host`
before allowing chat; the transport `Host` is an internal routing destination.
Don't expose Uvicorn directly
or disable Builder authentication.

The app has no tools or callback endpoint. It can't inspect your Azure
deployments. Model requests never fall back to a direct provider.

## Configuration

Set these nonsecret variables through Builder:

| Variable | Value |
|---|---|
| `AZURE_AI_GATEWAY_HOST` | Returned dedicated gateway hostname, without scheme or path |
| `AZURE_CLIENT_ID` | Attached app identity's client-ID GUID |
| `ENTRA_TENANT_ID` | Approved users' tenant GUID |
| `CHAT_USER_OBJECT_IDS` | Comma-separated list of 1-20 approved user object-ID GUIDs |
| `APP_PUBLIC_HOST` | Returned `<app>.app.westus2.builderapps.dev` hostname |
| `SAMPLE_EXPIRES_AT` | Timezone-aware deadline, at most two hours ahead |

The token scope is `https://azure-api.net/aigateway/.default`; the model URL is
`https://<gateway-host>/default/models/openai/v1/`. The gateway's model provider
uses the `https://cognitiveservices.azure.com/` managed-identity resource.

This sample targets `Microsoft.ApiManagement/aigateways`, not a regular API
Management service. The dedicated preview's no-charge offer doesn't cover
Builder, model inference, monitoring, or cross-region traffic.

Check dedicated-tier availability and model quota before deployment. The Builder
app runs in West US 2; placing its gateway or model elsewhere adds cross-region
transit and latency. Global model deployments may process outside their resource
region. Use synthetic prompts without secrets or personal data.

## Streaming and limits

Use a streaming Chat Completions model that accepts `reasoning_effort: low`.
Requests use the stable alias rather than a physical deployment name.
Each response allows 2,048 completion tokens, including reasoning.

The app accepts up to 4,000 message characters, two concurrent chats, and a
60-second gateway operation. It retries at most once for a rejected 429/503
before an accepted response or output, honoring a Retry-After of at most two
seconds. It never follows redirects or replays partial output.

The DEV/TEST process stops after 12 model requests or the configured deadline.
These counters reset on restart; track total use separately and don't restart
to extend an approved test budget. Delete resources when finished; this guard
isn't a subscription spending cap.

Diagnostics contain constant event codes, not prompts, tokens, responses, or
upstream bodies. Sensitive SDK instrumentation and Uvicorn access logs are off.

## Cleanup

Record resource and role-assignment IDs as you provision them. Stop requests,
remove the app and gateway role assignments, delete the model deployment and
account, then delete the gateway, Builder app, and app identity. Remove optional
monitoring only if you created it for this sample. Verify asynchronous deletion
has finished and preserve pre-existing resources. Keep this inventory outside Git.
