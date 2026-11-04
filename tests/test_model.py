import json
from types import SimpleNamespace
import unittest

import httpx2 as httpx

from config import MODEL_ALIAS
from gateway import GatewayAgent, GatewayFailure, SCOPE, Turn
from test_app import settings


class Credential:
    async def get_token(self, scope):
        assert scope == SCOPE
        return SimpleNamespace(token="synthetic-gateway-test-token")


def completion(delta, finish=None):
    return {
        "id": "test-completion", "object": "chat.completion.chunk",
        "created": 0, "model": MODEL_ALIAS,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def streaming(*chunks):
    data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=data)


class Wire:
    def __init__(self, statuses=()):
        self.statuses = list(statuses)
        self.requests = []

    def __call__(self, request):
        assert request.url.host == settings().gateway_host
        assert request.url.path == "/default/models/openai/v1/chat/completions"
        assert request.headers["Authorization"].removeprefix("Bearer ") == "synthetic-gateway-test-token"
        self.requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == MODEL_ALIAS
        assert body["stream"] is True
        assert body.get("store") is False
        assert body["max_completion_tokens"] == 2048
        assert body["reasoning_effort"] == "low"
        assert "max_tokens" not in body
        assert not body.get("tools")
        assert not body.get("tool_choice")
        if self.statuses:
            code = self.statuses.pop(0)
            if code != 200:
                return httpx.Response(code, headers={"Retry-After": "0"}, json={
                    "error": {"message": "sensitive-upstream-response", "type": "error", "code": "test"},
                })
        return streaming(
            completion({"role": "assistant", "content": "Hello through the gateway."}),
            completion({}, "stop"),
        )


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_agent_framework_streams_one_model_call_without_tools(self):
        wire = Wire()
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        output = "".join([text async for text in agent.stream("Hello")])
        self.assertEqual(output, "Hello through the gateway.")
        self.assertEqual(len(wire.requests), 1)

    async def test_sdk_sensitive_instrumentation_is_disabled(self):
        from agent_framework.observability import OBSERVABILITY_SETTINGS
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(Wire()))
        _ = [text async for text in agent.stream("Hello")]
        self.assertFalse(OBSERVABILITY_SETTINGS.enable_instrumentation)
        self.assertFalse(OBSERVABILITY_SETTINGS.enable_sensitive_data)

    async def test_explicit_rejection_retries_once_before_output(self):
        for status in (429, 503):
            wire = Wire((status, 200))
            agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
            self.assertTrue("".join([text async for text in agent.stream("Hello")]))
            self.assertEqual(len(wire.requests), 2)

    async def test_auth_failures_and_not_found_are_not_retried(self):
        for status in (401, 403, 404):
            wire = Wire((status,))
            agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("Hello")]
            self.assertEqual(len(wire.requests), 1)

    async def test_retry_budget_and_transient_error_sanitization(self):
        wire = Wire((503, 503))
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        with self.assertLogs("builder_gateway", level="WARNING") as logs:
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("private-prompt")]
        self.assertEqual(len(wire.requests), 2)
        self.assertNotIn("private-prompt", " ".join(logs.output))
        self.assertNotIn("sensitive-upstream-response", " ".join(logs.output))

    async def test_only_exact_model_endpoint_and_bounded_post_are_allowed(self):
        agent = GatewayAgent(settings(), Credential())
        for url in (
            "https://provider.openai.azure.com/chat/completions",
            settings().model_url + "other",
            settings().model_url + "chat/completions?target=provider",
            "http://" + settings().gateway_host + "/default/models/openai/v1/chat/completions",
            "https://" + settings().gateway_host + "/default/toolservers/anything/mcp",
        ):
            with self.assertRaises(GatewayFailure):
                await agent.guard_request(Turn(), httpx.Request("POST", url, json={}))
        for method, content in (("GET", b"{}"), ("POST", b"x" * 32769)):
            with self.assertRaises(GatewayFailure):
                await agent.guard_request(Turn(), httpx.Request(method, settings().model_url + "chat/completions", content=content))
        self.assertEqual(agent.model_requests, 0)

    async def test_empty_accepted_response_is_not_a_success_or_retried(self):
        wire = Wire()

        def empty(request):
            wire(request)
            return streaming(completion({"role": "assistant"}, "length"))

        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(empty))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Hello")]
        self.assertEqual(len(wire.requests), 1)

    async def test_redirect_is_not_followed(self):
        wire = Wire()

        def redirect(request):
            wire(request)
            return httpx.Response(307, headers={"Location": "https://provider.openai.azure.com/"})

        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(redirect))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Hello")]
        self.assertEqual(len(wire.requests), 1)

    async def test_model_budget_persists_across_turns_before_network(self):
        wire = Wire()
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        for _ in range(12):
            self.assertTrue("".join([text async for text in agent.stream("Hello")]))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Hello")]
        self.assertEqual(len(wire.requests), 12)

    async def test_retry_after_is_never_shortened_or_guessed(self):
        for hint in ("10", "nan", "-1", "invalid"):
            wire = Wire((429,))

            def reject(request):
                response = wire(request)
                response.headers["Retry-After"] = hint
                return response

            agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(reject))
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("Hello")]
            self.assertEqual(len(wire.requests), 1)

    async def test_partial_output_never_restarts(self):
        class PartialAgent(GatewayAgent):
            async def attempt(self, message, turn):
                self.calls = getattr(self, "calls", 0) + 1
                turn.emitted = True
                yield "partial"
                turn.rejected_status = 503
                raise GatewayFailure()

        agent = PartialAgent(settings(), Credential())
        chunks = []
        with self.assertRaises(GatewayFailure):
            async for text in agent.stream("x"):
                chunks.append(text)
        self.assertEqual(chunks, ["partial"])
        self.assertEqual(agent.calls, 1)
