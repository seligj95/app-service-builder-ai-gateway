# Connect a Builder app to AI Gateway

The app sends Chat Completions requests to the dedicated AI Gateway alias
`appservice-chat`. The gateway authenticates to the model account with its
managed identity. Responses stream back to the browser as server-sent events.

The [README](../README.md) provides the hosted walkthrough and optional local checks.

## Identity and access

Visitor sign-in is disabled for this temporary public demo. Anyone can invoke
the app and incur model charges. Use synthetic prompts only and delete the
resources after testing. Workload managed identity remains enabled; follow the
shared [managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/)
instructions. No user token or on-behalf-of flow is sent to AI Gateway.

| Identity | Access |
|---|---|
| App user-assigned managed identity | AI Gateway Models User on the `appservice-chat` alias |
| Gateway managed identity | Cognitive Services OpenAI User on the model account |

The canonical public origin is configured explicitly. Chat requires that exact
Origin header; transport Host and forwarded headers never select links,
redirects, authorization or credential destinations. Builder may route with an
internal ADC Host and no forwarded hostname. Origin checking is a browser
cross-origin safeguard, not authentication or a spending cap.

The app has no tools or callback endpoint. It can't inspect your Azure
deployments. Model requests never fall back to a direct provider.

## Configuration

Set these nonsecret variables through Builder:

| Variable | Value |
|---|---|
| `AZURE_AI_GATEWAY_HOST` | Returned dedicated gateway hostname, without scheme or path |
| `AZURE_CLIENT_ID` | Attached app identity's client-ID GUID |
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

The app accepts up to 4,000 message characters, one new chat every five seconds
per process, two concurrent chats, and a
60-second gateway operation. It retries at most once for a rejected 429/503
before an accepted response or output, honoring a Retry-After of at most two
seconds. It never follows redirects or replays partial output.

The DEV/TEST process stops after 12 model requests or the configured deadline.
These in-process limits reset on restart and multiply with scaling; track total use separately and don't restart
to extend an approved test budget. Delete resources when finished; this guard
isn't a subscription spending cap.

Diagnostics contain constant event codes, not prompts, tokens, responses, or
upstream bodies. Sensitive SDK instrumentation and Uvicorn access logs are off.

## Browser interface

The transcript uses the page's scroll rather than a nested scrolling panel.
The bottom composer sends with Enter and inserts a newline with Shift+Enter.
Suggested questions are empty-state helpers, not persistent controls over replies.
Assistant Markdown is a deliberately small subset: paragraphs, headings, lists,
emphasis, code and HTTP(S) links. It is built with allowlisted DOM nodes and text
nodes; raw HTML and non-HTTP(S) links are never executed. Streaming and stopped
output use the same renderer. No conversation history is sent to the model.

## Cleanup

Record resource and role-assignment IDs as you provision them. Stop requests,
remove the app and gateway role assignments, delete the model deployment and
account, then delete the gateway, Builder app, and app identity. Remove optional
monitoring only if you created it for this sample. Verify asynchronous deletion
has finished and preserve pre-existing resources. Keep this inventory outside Git.
