# Test a protected MCP callback

Use this Python/FastAPI fixture to test a dedicated AI Gateway callback to App
Service Builder. Its synthetic `callback_probe` tool requires a Builder-verified
gateway identity and an independent backend secret.

In this tutorial, you learn how to:

- Run local MCP protocol and authorization tests.
- Configure identity-based secret loading for an isolated hosted test.
- Check both callback credentials before connecting a tool to an agent.

> [!IMPORTANT]
> Use this experimental fixture only in an isolated DEV/TEST environment, for at
> most two hours. Keep Builder's required Entra authentication enabled. The app
> relies on Builder to validate tokens and replace incoming identity headers;
> don't expose it directly on another host.

## Prerequisites

- Python 3.14 and Git for the local tests.
- For a hosted test: a Builder app, a dedicated AI Gateway, a user-assigned managed
  identity, and an Azure Key Vault secret in your approved test environment.
- Permission to configure the app's Entra receiver and narrow identity/secret
  access. Use [Builder authentication](https://builder.appservice.azure.com/docs/guides/authentication/)
  and [managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/)
  instructions for those resources.

The local tests use synthetic credentials and don't require Azure resources.

## 1. Set up Python

Clone this repository, open its root directory, and create an environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 2. Run the local tests

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py tests
```

The tests initialize MCP, discover `callback_probe`, and invoke it with synthetic
credentials. They also check missing or incorrect credentials, duplicate headers,
expiration, transport restrictions, and credential-safe denial logs.

## 3. Configure an isolated hosted test

On the **Access** step of the create-app wizard, require Entra authentication.
After creation, use **Authentication** and **Identity** to manage these settings.
Allow only your gateway's tenant and principal object ID. The gateway must obtain
a token for your receiver application's audience.

Attach the app's user-assigned identity and grant it secret-read access to only
the test secret. Set these nonsecret variables before deploying `builder.yaml`:

| Variable | Value |
|---|---|
| `EXPECTED_GATEWAY_PRINCIPAL_ID` | Gateway's principal object-ID GUID |
| `AZURE_CLIENT_ID` | Attached identity's client-ID GUID |
| `KEY_VAULT_URI` | `https://<vault-name>.vault.azure.net` |
| `KEY_VAULT_SECRET_NAME` | Test secret's name |
| `CALLBACK_PUBLIC_HOST` | App's lowercase hostname, without scheme or path |
| `CALLBACK_TEST_EXPIRES_AT` | Absolute deadline with timezone, at most two hours ahead |

Use a 32-256-character printable ASCII secret with a matching future expiration.
The app reads it through the Key Vault SDK at startup; Key Vault reference
expansion isn't used. Loading failures prevent startup.

Configure the dedicated ToolServer to call the app's `/mcp` endpoint with the
gateway's managed identity and `x-appservice-mcp-secret` header. Keep the header
value in your secret-management workflow, not application variables or Git.
Grant the test caller Tools User access at that ToolServer only.

Use the dedicated gateway preview's documented regional availability and pricing.
Its current no-charge offering isn't a permanent `Free` SKU. Separate Builder
and Key Vault resources can incur charges.

## Verify the callback

Through the gateway, initialize MCP, list tools, and call `callback_probe`.
Require this actual tool result, not just HTTP 200:

```json
{
  "status": "ok",
  "identity_verified": true,
  "secret_verified": true,
  "invocation_count": 1
}
```

The process-local count increases on subsequent successful calls. Missing caller
authorization, either missing backend credential, a wrong secret, or a wrong
audience must prevent a successful probe. Gateway errors can wrap upstream
failures; inspect the MCP result too.

Authorization denials log constant event names rather than header values.
`/health` is process liveness, not callback verification, and its public URL
remains protected by Builder authentication.

## Clean up resources

Stop the test at its deadline. Remove only the test resources and grants you
recorded when creating them: the caller role, ToolServer, receiver registration
and its identity grants, Builder app, managed identity, and vault/secret.
Respect Key Vault soft-delete retention; don't purge the vault or unregister a
subscription-wide provider. Keep tokens, receipts, and cleanup inventory outside Git.

## Related content

- [Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/)
- [Dedicated gateway identity and access](https://ai.gateway.azure.com/docs/security-identity)
- [Gateway MCP access](https://ai.gateway.azure.com/docs/mcp-access)
