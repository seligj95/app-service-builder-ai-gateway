# Connect a Python agent through an AI gateway

This App Service Builder sample combines browser streaming chat, Microsoft Agent
Framework, and two read-only MCP tools. Model and tool requests use one dedicated
Azure AI Gateway; the app has no direct-provider fallback.

In this tutorial, you learn how to:

- Check the Python agent and MCP protocol locally.
- Verify separate access for chat users and the gateway.
- Check streaming failures and prevent tool-call replay.

The hosted integration is **Under construction**. The steps below run local
checks without Azure resources. They don't deploy or qualify the hosted sample.

## Prerequisites

- Python 3.14 and Git.

> [!IMPORTANT]
> The hosted app depends on Builder's required Entra authentication to validate
> tokens and replace incoming identity headers. Don't expose Uvicorn directly.
> Use an isolated DEV/TEST environment and a test window of at most two hours.

## 1. Set up Python

Clone this repository and open its root directory. Create an environment and
install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 2. Run the local checks

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py config.py gateway.py identity.py tests
.venv/bin/python -m pip check
```

The checks use synthetic identities and mocked HTTP responses with the installed
Agent Framework, OpenAI, and MCP clients. They don't sign in, send inference
requests, or create cloud resources.

## Verify

The test suite finishes with `OK`. It initializes MCP, discovers
`get_service_status` and `get_deployment_context`, and calls each tool. It also
checks browser SSE output, access separation, expiration, bounded input,
credential-safe failures, and gateway-only model and tool requests.

Service status is synthetic process liveness, not Azure resource health.
Deployment context contains only configured, nonsecret labels.

The agent can retry one rejected model request before a model response, tool
call, or text output. It doesn't replay an issued tool call, restart partial
output, or switch providers.

## Clean up resources

These local checks create no Azure resources. Remove the local Python environment
when you're finished. For separately provisioned hosted resources, follow the
[exact-inventory cleanup requirements](docs/callback-contract.md#cleanup).

## Related content

- [Hosted architecture and configuration](docs/callback-contract.md)
- [Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/)
- [Dedicated AI Gateway](https://ai.gateway.azure.com/docs/)
- [Python gateway reference](https://github.com/seligj95/app-service-ai-gateway-tier-python)
