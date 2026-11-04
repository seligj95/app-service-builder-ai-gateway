# Connect models and protected tools

The hosted integration is **Under construction**. This page describes its
required architecture, not a deployment walkthrough. Use the
[local checks](../README.md) until hosted setup is ready.

## Request flow

An approved user opens the Builder app and sends a chat message. The app's
user-assigned managed identity calls a dedicated AI Gateway model alias. Agent
Framework invokes either of the gateway's two read-only MCP tools as needed.
The gateway authenticates to the app's `/mcp` endpoint with its own managed
identity. The model then streams its answer through Builder to the browser.

The model alias is `appservice-chat`. The ToolServer is `appservice-ops`, exposing
`appservice-ops_get_service_status` and
`appservice-ops_get_deployment_context`. Both tools accept no arguments.

The model behind the alias must support streaming Chat Completions, function
tools, and `reasoning_effort: low`. Each completion is limited to 2,048 tokens,
including reasoning, and each serialized model request to 32 KiB.

Only the dedicated `Microsoft.ApiManagement/aigateways` resource is intended
here. Regular API Management services, subscription-key policies, and direct
Foundry or OpenAI URLs aren't interchangeable configuration.

## Identity boundaries

Use the shared Builder [authentication](https://builder.appservice.azure.com/docs/guides/authentication/)
and [managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/)
guides. Keep required customer Entra authentication enabled.

| Caller | Required access |
|---|---|
| Approved browser users | Receiver's User-only `Chat.Use` app role and exact user object-ID allowlists |
| Builder app identity | `AI Gateway Models User` on the model alias and `AI Gateway Tools User` on the ToolServer |
| Gateway identity | Receiver's Application-only `Callback.Invoke` app role and exact gateway object-ID allowlists |
| Gateway identity to Foundry | Inference permission on the selected model account |

Pin the receiver to one tenant and API audience. Require assignment on its
service principal. Configure Builder's provider object-ID rules for only the
gateway and approved users; a tenant-only or email rule would broaden access.
Configure the receiver's returned browser redirect URI and the supported Builder
identity/federation setup before enabling browser sign-in.

App-role assignments control token issuance. Builder doesn't enforce or forward
the role values to this app. The app authorizes the verified tenant and object
ID: only the gateway can use `/mcp`, and only the configured humans can use chat
and static assets. Human access never grants tool-callback access.

The callback uses exact-principal Entra authentication, not an additional shared
secret. No callback secret, Key Vault lookup, or Key Vault reference expansion is
required. Don't disable the Builder authentication boundary to make a callback
work.

## Application settings

Configure these nonsecret variables through Builder. Don't commit actual
identities, tokens, deployment receipts, or cleanup inventory.

| Variable | Value |
|---|---|
| `AZURE_AI_GATEWAY_HOST` | Returned dedicated gateway hostname, without scheme or path |
| `EXPECTED_GATEWAY_PRINCIPAL_ID` | Gateway identity's principal object-ID GUID |
| `ENTRA_TENANT_ID` | Required receiver tenant GUID |
| `CHAT_USER_OBJECT_IDS` | Comma-separated list of 1-20 approved human object-ID GUIDs |
| `AZURE_CLIENT_ID` | Attached Builder identity's client-ID GUID |
| `CALLBACK_PUBLIC_HOST` | Returned `<app>.app.westus2.builderapps.dev` hostname |
| `SAMPLE_EXPIRES_AT` | Absolute timezone-aware deadline, at most two hours ahead |
| `SAMPLE_APP_NAME` | Optional nonsecret label; default `builder-gateway-agent` |
| `SAMPLE_REGION` | Optional nonsecret label; default `westus2` |

The app requests gateway tokens with scope
`https://azure-api.net/aigateway/.default`. Model requests use
`https://<gateway-host>/default/models/openai/v1/`; tool requests use
`https://<gateway-host>/default/toolservers/appservice-ops/mcp`.

The gateway's backend managed-identity resource must match the receiver's
application ID URI, such as `api://<receiver-client-id>`. Its model provider uses
the gateway identity to reach Foundry.

Confirm dedicated-tier region availability and model quota before creating
resources. Builder is in West US 2; placing the gateway or model elsewhere adds
cross-region latency and data transit. The dedicated preview's no-charge offer
isn't a permanent Free SKU. Builder, model capacity/inference, and optional
monitoring can incur separate charges.

## Verification requirements

Check browser sign-in, a complete SSE response, and answers to both example
questions. Each example must call its matching tool through the gateway.
Inspect the MCP result, not only the outer HTTP status.

Verify that anonymous callers, the wrong tenant or principal, and users calling
`/mcp` are denied. The gateway must be denied chat access. Wrong audiences must
be rejected by Builder before the app. At expiration, application routes deny
access; `/health` remains process liveness and must still be protected at the
Builder boundary.

Don't enter secrets or personal data in chat. Application diagnostics contain
constant event codes, not prompts, tokens, tool payloads, or upstream errors.
Agent Framework instrumentation is disabled; don't enable sensitive SDK tracing
or HTTP access/body logging.

## Cleanup

Record each created resource ID, Entra object ID, assignment ID, and owner when
provisioning. At the deadline, stop requests and delete only that inventory:
model deployment/account, gateway and children, narrow RBAC assignments,
receiver and its grants, Builder app, and managed identity. Remove optional
monitoring resources only if this test created them.

Verify deletion completion, including asynchronous operations. Preserve
pre-existing shared resources and subscription-wide provider registrations.
Keep the cleanup ledger outside Git.
