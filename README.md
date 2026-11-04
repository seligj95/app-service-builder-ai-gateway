# App Service Builder AI Gateway callback experiment

**Experimental test fixture, not a finished AI agent sample.** This personal
testing repository contains one Python/FastAPI MCP server for checking a dedicated
AI Gateway's combined backend credentials. It does not contain a chat UI, model
route, inference, Agent Framework integration, or a supported deployment claim.
Nothing here has been qualified for production or published to Azure-Samples.

The eventual agent sample still needs its separate browser streaming, gateway-only
model routing, and two fixed read-only tool checks. This fixture does not silently
replace those requirements.

## What this tests

```text
Authorized MCP client
  -> dedicated AI Gateway ToolServer
  -> Builder required Entra authentication
  -> FastAPI /mcp: trusted gateway principal ID AND independent secret header
  -> one synthetic read-only callback_probe tool
```

The tool returns only success flags and a process-local invocation count. It makes
no model calls or downstream business operations. An explicit expiry deadline
blocks new MCP requests even if credentials remain cached. Startup rejects a deadline
more than two hours in the future. Only one fixed replica
is intended; its counter is diagnostic, not persistent state.

The app trusts `X-MS-CLIENT-PRINCIPAL-ID` **only because Builder strips untrusted
identity headers and emits its verified identity**. This fixture does not itself
validate Entra bearer tokens. Do not expose it directly on another hosting service
without an equivalent trusted authentication boundary. Builder's required visitor
authentication must remain enabled for the whole application.

## Local checks

Use Python 3.14 in an isolated virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py tests
```

Dependencies, including transitive SDK requirements, are pinned in `requirements.txt`.
Tests inject a synthetic secret and clock in process; they need no Azure resource,
token, real secret, or `.env` file. They exercise MCP initialize/list/call and
negative identity, secret, duplicate-header, expiry, host, and startup cases.
Local checks cannot prove Builder runtime activation or gateway token/header
composition.

## Hosted configuration

Root `builder.yaml` declares one Python 3.14 web component, port 8080, explicit
Uvicorn startup, and `/health` process liveness. Interactive API docs are disabled.
Access logging is disabled. `/health` reveals no secret or configuration and does
not exempt its public URL from Builder's authentication policy.

Set these **non-secret** application variables before building:

| Variable | Value |
|---|---|
| `EXPECTED_GATEWAY_PRINCIPAL_ID` | Exact system-assigned gateway principal object-ID GUID |
| `AZURE_CLIENT_ID` | Client-ID GUID of the user-assigned identity attached to Builder |
| `KEY_VAULT_URI` | Exact public HTTPS vault origin, such as `https://<vault-name>.vault.azure.net` |
| `KEY_VAULT_SECRET_NAME` | Name of the independently provisioned backend secret |
| `CALLBACK_PUBLIC_HOST` | Builder app's actual lowercase hostname, without scheme, path or port |
| `CALLBACK_TEST_EXPIRES_AT` | Approved absolute deadline as an ISO 8601 timestamp with timezone |

At startup the app uses `ManagedIdentityCredential` and Azure Key Vault SDK to read
the secret. The identity needs only secret-read permission on that one secret.
The secret must be 32-256 printable ASCII characters and have a future Key Vault
expiration. Loading failure or expired configuration prevents startup; there is
no local-secret or anonymous fallback. No App Service Key Vault reference expansion
is assumed. The value stays in memory and is removed from application state at
shutdown.

Keep actual settings, secrets, source credentials, cloud responses, tokens,
deployment receipts, and cleanup inventory outside Git. `.azure/`, `.env` files,
private keys, generated archives, and receipt files are ignored. Review the
publication diff rather than relying on ignore rules alone.

## Cloud experiment boundaries

Cloud creation and source connection require separate exact-target approval and
the supported native Builder confirmations. This repository does not create
resources, grant roles, request consent, install GitHub integration, or deploy
automatically. Never substitute CLI operations for a disabled native action.

The intended small experiment uses a dedicated gateway in East US 2 and a
protected Builder app in West US 2, one user-assigned identity and one Standard
vault/secret. It needs no Foundry resource or model deployment. The documented
dedicated gateway **public preview currently has no gateway charge**; this is not
a permanent `Free` SKU. Do not substitute paid regular APIM Developer, Basic, or
other tiers. Separately provisioned dependencies can incur charges.

Required outcome matrix:

| Credentials / case | Expected result |
|---|---|
| No caller credential at gateway | Gateway denies; no successful tool call |
| Backend secret only | Builder's required Entra gate denies |
| Backend MI token only | Builder accepts identity; app rejects missing secret |
| Backend MI token plus correct secret | Real discovery and tool call succeed |
| Backend MI token plus wrong secret | App rejects; invocation count unchanged |
| Wrong token audience | Builder rejects if the gateway actually acquired/sent a token |
| Expired experiment | App rejects even previously valid credentials |

The gateway may wrap a failed backend request in HTTP 200 with an MCP error.
Assert the JSON-RPC/tool result, not HTTP status alone. Successful local tests
are not evidence that the gateway executes MI and static headers together.
Real token renewal across expiration is a separate qualification.

References:
[Builder authentication](https://builder.appservice.azure.com/docs/guides/authentication/),
[Builder managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/),
[Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/),
[dedicated gateway access](https://ai.gateway.azure.com/docs/security-identity),
and [MCP access](https://ai.gateway.azure.com/docs/mcp-access).
