import json
from types import SimpleNamespace
import unittest

import httpx2 as httpx

from config import MODEL_ALIAS, REMOTE_TOOLS
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
    def __init__(self, tool=REMOTE_TOOLS[0], statuses=(), tool_error=None):
        self.tool = tool
        self.statuses = list(statuses)
        self.requests = []
        self.tool_calls = []
        self.models = 0
        self.tool_error = tool_error

    def __call__(self, request):
        assert request.url.host == settings().gateway_host
        assert request.headers["Authorization"] == "Bearer synthetic-gateway-test-token"
        self.requests.append(request)
        if request.url.path.endswith("/mcp"):
            if request.method != "POST":
                return httpx.Response(405)
            body = json.loads(request.content)
            method, identifier = body["method"], body.get("id")
            if method == "notifications/initialized":
                return httpx.Response(202)
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "local-wire", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}} for name in REMOTE_TOOLS]}
            elif method == "tools/call":
                self.tool_calls.append(body["params"]["name"])
                if self.tool_error == "terminated":
                    return httpx.Response(200, json={
                        "jsonrpc": "2.0", "id": identifier,
                        "error": {"code": -32000, "message": "session terminated"},
                    })
                if self.tool_error:
                    return httpx.Response(200, json={
                        "jsonrpc": "2.0", "id": identifier,
                        "result": {"content": [{"type": "text", "text": "sensitive-private-error"}], "isError": True},
                    })
                result = {"content": [{"type": "text", "text": '{"status":"healthy"}'}], "isError": False}
            else:
                result = {}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": identifier, "result": result})
        self.models += 1
        body = json.loads(request.content)
        assert body["model"] == MODEL_ALIAS
        assert body["stream"] is True
        assert body.get("store") is False
        assert body["max_completion_tokens"] == 2048
        assert body["reasoning_effort"] == "low"
        assert "max_tokens" not in body
        if self.statuses:
            code = self.statuses.pop(0)
            if code != 200:
                return httpx.Response(code, headers={"Retry-After": "0"}, json={"error": {"message": "sensitive-upstream-response", "type": "error", "code": "test"}})
        if self.tool and not self.tool_calls:
            return streaming(
                completion({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_one", "type": "function", "function": {"name": self.tool, "arguments": "{}"}}]}),
                completion({}, "tool_calls"),
            )
        return streaming(completion({"role": "assistant", "content": "Healthy through the gateway."}), completion({}, "stop"))


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_agent_framework_discovers_calls_tool_and_streams_through_gateway(self):
        for tool in REMOTE_TOOLS:
            with self.subTest(tool=tool):
                wire = Wire(tool)
                agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
                prompt = "What is the current service status?" if tool == REMOTE_TOOLS[0] else "Show the deployment context."
                output = "".join([text async for text in agent.stream(prompt)])
                self.assertEqual(output, "Healthy through the gateway.")
                self.assertEqual(wire.tool_calls, [tool])
                self.assertEqual(wire.models, 2)
                requests = [json.loads(r.content) for r in wire.requests if r.url.path.endswith("/chat/completions")]
                self.assertEqual(requests[0]["tool_choice"]["function"]["name"], tool)
                self.assertIsNone(requests[1].get("tool_choice"))
                self.assertTrue(any(message["role"] == "tool" for message in requests[1]["messages"]))

    async def test_sdk_sensitive_instrumentation_is_disabled(self):
        from agent_framework.observability import OBSERVABILITY_SETTINGS
        wire = Wire(tool=None)
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        _ = [text async for text in agent.stream("Hello")]
        self.assertFalse(OBSERVABILITY_SETTINGS.enable_instrumentation)
        self.assertFalse(OBSERVABILITY_SETTINGS.enable_sensitive_data)

    async def test_explicit_rejection_retries_once_before_output(self):
        wire = Wire(tool=None, statuses=(429, 200))
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        output = "".join([text async for text in agent.stream("Explain the sample.")])
        self.assertTrue(output)
        self.assertEqual(wire.models, 2)
        self.assertEqual(wire.tool_calls, [])

    async def test_no_retry_after_tool_or_accepted_model_response(self):
        wire = Wire(statuses=(200, 503))
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        with self.assertRaises(GatewayFailure) as failure:
            _ = [text async for text in agent.stream("What is the current service status?")]
        self.assertNotIn("sensitive", str(failure.exception))
        self.assertEqual(wire.models, 2)
        self.assertEqual(wire.tool_calls, [REMOTE_TOOLS[0]])

    async def test_tool_error_stops_before_a_success_shaped_answer(self):
        wire = Wire(tool_error="failed")
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Check the service status.")]
        self.assertEqual(wire.models, 1)
        self.assertEqual(wire.tool_calls, [REMOTE_TOOLS[0]])

    async def test_framework_reconnect_cannot_replay_an_issued_tool_call(self):
        wire = Wire(tool_error="terminated")
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Check the service status.")]
        self.assertEqual(wire.models, 1)
        self.assertEqual(wire.tool_calls, [REMOTE_TOOLS[0]])

    async def test_auth_failures_and_not_found_are_not_retried(self):
        for status in (401, 403, 404):
            wire = Wire(tool=None, statuses=(status,))
            agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("Hello")]
            self.assertEqual(wire.models, 1)

    async def test_retry_budget_and_transient_error_sanitization(self):
        wire = Wire(tool=None, statuses=(503, 503))
        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(wire))
        with self.assertLogs("builder_gateway", level="WARNING") as logs:
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("private-prompt")]
        self.assertEqual(wire.models, 2)
        self.assertNotIn("private-prompt", " ".join(logs.output))
        self.assertNotIn("sensitive-upstream-response", " ".join(logs.output))

    async def test_endpoint_and_tool_replay_guards_run_before_network(self):
        agent = GatewayAgent(settings(), Credential())
        turn = Turn()
        for url in ("https://provider.openai.azure.com/chat/completions", settings().model_url + "other", settings().mcp_url + "?target=provider", "http://" + settings().gateway_host + "/default/toolservers/appservice-ops/mcp"):
            with self.assertRaises(GatewayFailure):
                await agent.guard_request(turn, httpx.Request("POST", url, json={}))
        request = httpx.Request("POST", settings().mcp_url, json={"method": "tools/call", "params": {"name": REMOTE_TOOLS[0], "arguments": {}}})
        await agent.guard_request(turn, request)
        with self.assertRaisesRegex(GatewayFailure, "replay_blocked"):
            await agent.guard_request(turn, request)
        for params in ({"name": "delete_app"}, {"name": REMOTE_TOOLS[1], "arguments": {"url": "https://elsewhere"}}):
            with self.assertRaises(GatewayFailure):
                await agent.guard_request(turn, httpx.Request("POST", settings().mcp_url, json={"method": "tools/call", "params": params}))
        with self.assertRaises(GatewayFailure):
            await agent.guard_request(turn, httpx.Request("POST", settings().model_url + "chat/completions", content=b"x" * 32769))

    async def test_empty_model_output_is_not_a_success(self):
        wire = Wire(tool=None)

        def empty(request):
            response = wire(request)
            if request.url.path.endswith("/chat/completions"):
                return streaming(completion({"role": "assistant"}, "length"))
            return response

        agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(empty))
        with self.assertRaises(GatewayFailure):
            _ = [text async for text in agent.stream("Hello")]
        self.assertEqual(wire.models, 1)

    async def test_retry_after_is_never_shortened_or_guessed(self):
        for hint in ("10", "nan", "-1", "invalid"):
            wire = Wire(tool=None, statuses=(429,))

            def reject(request):
                response = wire(request)
                if response.status_code == 429:
                    response.headers["Retry-After"] = hint
                return response

            agent = GatewayAgent(settings(), Credential(), httpx.MockTransport(reject))
            with self.assertRaises(GatewayFailure):
                _ = [text async for text in agent.stream("Hello")]
            self.assertEqual(wire.models, 1)

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
