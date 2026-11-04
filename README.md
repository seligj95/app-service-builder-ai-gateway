# Connect a Python agent through an AI gateway

Use a Python web app on App Service Builder to stream answers from a model
through a dedicated Azure AI Gateway. Microsoft Agent Framework uses managed
identity to call the gateway; the gateway uses its identity to call the model.

In this tutorial, you learn how to:

- Check the Python agent locally.
- Use workload managed identities without visitor sign-in.
- Stream a model response through the gateway.

The hosted integration is **Under construction**. The steps below run local
checks without Azure resources. They don't deploy or qualify the hosted sample.

## Prerequisites

- Python 3.14 and Git.

> [!IMPORTANT]
> This is a public demo: anyone can invoke the app and incur model charges.
> Its in-process limits aren't authentication or a spending cap.
> Use synthetic prompts, an isolated DEV/TEST environment and a test window of
> at most two hours, then delete the resources.

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
.venv/bin/python -m compileall -q app.py config.py gateway.py tests
.venv/bin/python -m pip check
```

The checks use mocked managed identity and HTTP responses with the installed
Agent Framework and OpenAI clients. They don't sign in, send inference
requests, or create cloud resources.

## Verify

The test suite finishes with `OK`. It checks anonymous browser SSE output, exact
Origin checking, expiration, bounded input, admission throttling, credential-safe failures, and model requests
sent only to the configured gateway.

The agent can retry one rejected model request before an accepted response or
text output. It doesn't restart partial output or switch providers.

## Clean up resources

These local checks create no Azure resources. Remove the local Python environment
when you're finished. For separately provisioned hosted resources, follow the
[cleanup guidance](docs/architecture.md#cleanup).

## Related content

- [Architecture and configuration](docs/architecture.md)
- [Builder manifest](https://builder.appservice.azure.com/docs/guides/builder-yaml/)
- [Dedicated AI Gateway](https://ai.gateway.azure.com/docs/)
- [Python gateway reference](https://github.com/seligj95/app-service-ai-gateway-tier-python)
