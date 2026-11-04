# Validate a protected MCP receiver

Use this Python/FastAPI fixture to validate MCP authorization locally. Its
synthetic `callback_probe` tool models an App Service Builder callback that
requires a verified gateway identity and an independent backend secret.

In this tutorial, you learn how to:

- Run local MCP protocol and authorization tests.
- Check credential rejection and expiration.
- Check that authorization errors don't disclose credentials.

The hosted integration is **Under construction**. Run the local checks below;
connect an agent only after combined identity-and-secret delivery is verified.

> [!IMPORTANT]
> Use this experimental fixture only in an isolated DEV/TEST environment, for at
> most two hours. Keep Builder's required Entra authentication enabled. The app
> relies on Builder to validate tokens and replace incoming identity headers;
> don't expose it directly on another host.

## Prerequisites

- Python 3.14 and Git for the local tests.

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

## Verify

The suite must finish with `OK`. Its MCP checks initialize a session, list tools,
and call `callback_probe` with synthetic credentials. One successful local tool
result contains:

```json
{
  "status": "ok",
  "identity_verified": true,
  "secret_verified": true,
  "invocation_count": 1
}
```

Credential and expiration tests must reject invalid requests. Authorization
denials log constant event names rather than header values. Local success doesn't
establish hosted gateway compatibility.

## Clean up resources

The local checks don't create Azure resources. Remove the local `.venv` directory
when you're finished. If you separately created hosted test resources, follow the
[exact-inventory cleanup requirements](docs/callback-contract.md#cleanup).

## Related content

- [Hosted callback contract (under construction)](docs/callback-contract.md)
- [Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/)
- [Gateway MCP access](https://ai.gateway.azure.com/docs/mcp-access)
