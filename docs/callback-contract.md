# Protected callback contract

The hosted integration is **Under construction**. These requirements describe
the intended contract, not a runnable deployment tutorial. Configuration
acceptance and local tests don't establish combined credential delivery.

## Identity and secret

A hosted test needs an isolated Builder app, dedicated AI Gateway, user-assigned
managed identity, Key Vault secret, and permission to configure narrow access.
Use the shared [authentication](https://builder.appservice.azure.com/docs/guides/authentication/)
and [managed identity](https://builder.appservice.azure.com/docs/guides/managed-identity/)
guides for supported Builder setup.

The app's required Entra policy must allow only the gateway's tenant and principal
object ID. The gateway needs a token for the receiver application's audience.
Builder must replace caller-supplied identity headers with verified identity.
The fixture doesn't validate bearer tokens itself.

The fixture emits a constant DEV/TEST startup notice after loading its secret and
starting MCP. Denial events distinguish missing, duplicate and mismatched
credentials without recording their values. Verify diagnostic delivery before a
hosted credential test.

The attached app identity needs read access to only the test secret. The fixture
expects these nonsecret variables:

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

Qualification requires the dedicated ToolServer to deliver both its managed
identity token and the `x-appservice-mcp-secret` header to `/mcp`. Keep the secret
out of application variables and Git. The test caller needs Tools User access
at that ToolServer only.

Use the dedicated gateway preview's documented regional availability and pricing.
Its current no-charge offering isn't a permanent `Free` SKU. Separate Builder
and Key Vault resources can incur charges.

## Acceptance criteria

Through the gateway, MCP initialization, discovery and `callback_probe` must
return the successful result described in the [local checks](../README.md#verify).
Missing caller authorization, either missing backend credential, a wrong secret,
or a wrong audience must prevent a successful probe.

Gateway errors can wrap upstream failures; HTTP 200 alone isn't success. Check
the MCP result. `/health` is process liveness, not callback verification, and its
public URL must remain protected by Builder authentication.

## Cleanup

Stop a hosted test at its deadline. Remove only the test resources and grants
recorded when creating them: the caller role, ToolServer, receiver registration
and its identity grants, Builder app, managed identity, and vault/secret.
Respect Key Vault soft-delete retention; don't purge the vault or unregister a
subscription-wide provider. Keep tokens, receipts, and cleanup inventory outside Git.
