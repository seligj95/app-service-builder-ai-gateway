import base64
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import unittest
from unittest.mock import patch

from azure.identity.aio import ManagedIdentityCredential
from fastapi.testclient import TestClient

from app import ChatRequest, create_app
from config import Settings, TOOL_NAMES
from gateway import GatewayFailure
from identity import OBJECT_CLAIM, PRINCIPAL_HEADER, TENANT_CLAIM
from starlette.requests import Request


GATEWAY = "00000000-0000-4000-8000-000000000001"
HUMAN = "00000000-0000-4000-8000-000000000002"
TENANT = "00000000-0000-4000-8000-000000000003"
OTHER = "00000000-0000-4000-8000-000000000009"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def settings(**overrides):
    return Settings(**({
        "gateway_host": "example.eastus2-01.ai.gateway.azure.com",
        "gateway_principal_id": GATEWAY, "tenant_id": TENANT,
        "chat_user_ids": (HUMAN,), "identity_client_id": OTHER,
        "public_host": "sample.app.westus2.builderapps.dev",
        "expires_at": NOW + timedelta(hours=2),
    } | overrides))


def identity(principal=GATEWAY, tenant=TENANT):
    payload = {
        "auth_typ": "aad",
        "claims": [{"typ": OBJECT_CLAIM, "val": principal}, {"typ": TENANT_CLAIM, "val": tenant}],
    }
    return {
        PRINCIPAL_HEADER: principal,
        "x-ms-client-principal-idp": "aad",
        "x-ms-client-principal": base64.b64encode(json.dumps(payload).encode()).decode(),
    }


class FakeAgent:
    def __init__(self):
        self.fail = False

    async def stream(self, message):
        yield "Hello "
        if self.fail:
            raise GatewayFailure("sensitive-upstream-content")
        yield "Builder."


class StartupTests(unittest.TestCase):
    def test_real_agent_and_async_identity_start_without_requesting_a_token(self):
        app = create_app(settings(), now=lambda: NOW)
        self.assertTrue(logging.getLogger("builder_gateway").isEnabledFor(logging.WARNING))
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(ManagedIdentityCredential, "get_token", side_effect=AssertionError("Unexpected token request")) as token,
            TestClient(app, base_url=settings().origin) as client,
        ):
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            token.assert_not_called()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.agent = FakeAgent()
        self.app = create_app(settings(), self.agent, now=lambda: self.clock)
        self.client = TestClient(self.app, base_url=settings().origin, follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def rpc(self, method, params=None, headers=None):
        return self.client.post("/mcp", headers={
            **identity(), "Accept": "application/json, text/event-stream", **(headers or {}),
        }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_gateway_discovers_and_calls_exactly_two_readonly_tools_without_secret(self):
        initialized = self.rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "local-test", "version": "1"},
        })
        self.assertEqual(initialized.status_code, 200)
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, set(TOOL_NAMES))
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        for name in TOOL_NAMES:
            response = self.rpc("tools/call", {"name": name, "arguments": {}})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["result"].get("isError", False))
            self.assertNotIn(TENANT, response.text)
            self.assertNotIn(GATEWAY, response.text)
        self.assertEqual(self.app.state.tool_calls, dict.fromkeys(TOOL_NAMES, 1))

    def test_anonymous_human_wrong_principal_or_tenant_cannot_call_mcp(self):
        for headers in ({}, identity(HUMAN), identity(OTHER), identity(tenant=OTHER)):
            response = self.client.post("/mcp", json={}, headers=headers)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(sum(self.app.state.tool_calls.values()), 0)

    def test_duplicate_identity_headers_and_claims_are_rejected(self):
        headers = list(identity().items()) + [(PRINCIPAL_HEADER, GATEWAY)]
        self.assertEqual(self.client.post("/mcp", headers=headers, json={}).status_code, 401)
        for corrupt in ("invalid-base64", base64.b64encode(b"[]").decode()):
            response = self.rpc("tools/list", headers={"x-ms-client-principal": corrupt})
            self.assertEqual(response.status_code, 401)

    def test_provider_and_full_principal_must_match(self):
        for headers in (
            {"x-ms-client-principal-idp": "github"},
            {"x-ms-client-principal": identity(HUMAN)["x-ms-client-principal"]},
        ):
            self.assertEqual(self.rpc("tools/list", headers=headers).status_code, 401)

    def test_denial_never_logs_identity_or_header_content(self):
        with self.assertLogs("builder_gateway", level="WARNING") as logs:
            response = self.rpc("tools/list", headers={PRINCIPAL_HEADER: "sensitive-private-value"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(logs.output, ["WARNING:builder_gateway:callback_identity_denied"])

    def test_unknown_tool_and_nonempty_arguments_do_not_execute(self):
        response = self.rpc("tools/call", {"name": "delete_app", "arguments": {}})
        self.assertTrue(response.json()["result"]["isError"])
        response = self.rpc("tools/call", {"name": TOOL_NAMES[0], "arguments": {"secret": "not-allowed"}})
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(sum(self.app.state.tool_calls.values()), 0)

    def test_human_chat_streams_and_gateway_cannot_use_browser_routes(self):
        response = self.client.post(
            "/api/chat/stream", json={"message": "Hello"},
            headers={**identity(HUMAN), "Origin": settings().origin},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: delta", response.text)
        self.assertIn("Hello ", response.text)
        self.assertIn("Builder.", response.text)
        self.assertTrue(response.text.endswith("event: done\ndata: {}\n\n"))
        self.assertEqual(self.app.state.active_chats, 0)
        self.assertEqual(self.client.get("/", headers=identity()).status_code, 401)

    def test_cross_origin_invalid_input_and_overloaded_chat_are_explicit_errors(self):
        for origin in (None, "https://evil.example"):
            headers = identity(HUMAN) | ({"Origin": origin} if origin else {})
            self.assertEqual(self.client.post("/api/chat/stream", headers=headers, json={"message": "x"}).status_code, 403)
        headers = identity(HUMAN) | {"Origin": settings().origin}
        for payload in ({"message": ""}, {"message": " "}, {"message": "x" * 4001}, {"message": "x", "extra": "private"}):
            response = self.client.post("/api/chat/stream", headers=headers, json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("private", response.text)
        self.app.state.active_chats = 2
        self.assertEqual(self.client.post("/api/chat/stream", headers=headers, json={"message": "x"}).status_code, 429)

    def test_partial_stream_failure_is_sanitized_not_replayed(self):
        self.agent.fail = True
        response = self.client.post("/api/chat/stream", headers={
            **identity(HUMAN), "Origin": settings().origin,
        }, json={"message": "x"})
        self.assertIn("Hello ", response.text)
        self.assertIn("event: error", response.text)
        self.assertNotIn("sensitive-upstream-content", response.text)
        self.assertEqual(self.app.state.active_chats, 0)

    def test_expiry_health_host_and_body_bounds(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health", headers={"Host": "127.0.0.1:8080"}).status_code, 200)
        self.assertEqual(self.rpc("tools/list", {"padding": "x" * 17000}).status_code, 413)
        response = self.client.post("/api/chat/stream", headers={
            **identity(HUMAN), "Origin": settings().origin, "Content-Type": "application/json",
        }, content=(chunk for chunk in (b'{"message":"', b"x" * 9000, b"x" * 9000, b'"}')))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.rpc("tools/list", headers={"Host": "hostile.example"}).status_code, 400)
        self.clock = NOW + timedelta(hours=2)
        self.assertEqual(self.rpc("tools/list").status_code, 403)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_unstarted_or_cancelled_stream_does_not_leak_a_chat_slot(self):
        async def check():
            endpoint = next(route.endpoint for route in self.app.routes if getattr(route, "path", "") == "/api/chat/stream")
            request = Request({
                "type": "http", "method": "POST", "path": "/api/chat/stream",
                "headers": [(b"origin", settings().origin.encode())],
            })
            response = await endpoint(request, ChatRequest(message="Hello"))
            self.assertEqual(self.app.state.active_chats, 0)
            await anext(response.body_iterator)
            self.assertEqual(self.app.state.active_chats, 1)
            await response.body_iterator.aclose()
            self.assertEqual(self.app.state.active_chats, 0)
        self.client.portal.call(check)

    def test_page_assets_have_csp_and_render_no_untrusted_html(self):
        page = self.client.get("/", headers=identity(HUMAN))
        self.assertEqual(page.status_code, 200)
        self.assertIn("sha256-", page.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/static/chat.js", headers=identity(HUMAN)).status_code, 200)
        self.assertEqual(self.client.get("/static/unknown.js", headers=identity(HUMAN)).status_code, 404)


class ConfigTests(unittest.TestCase):
    def test_direct_provider_or_ambiguous_endpoint_configuration_is_rejected(self):
        for host in ("https://example.ai.gateway.azure.com", "resource.openai.azure.com", "example.eastus2.ai.gateway.azure.com.evil", "example.eastus2.ai.gateway.azure.com/path"):
            with self.assertRaises(ValueError):
                settings(gateway_host=host)
        for changes in ({"chat_user_ids": ()}, {"chat_user_ids": (GATEWAY,)}, {"chat_user_ids": (HUMAN, HUMAN)}, {"tenant_id": "bad"}, {"expires_at": NOW.replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                settings(**changes)

    def test_expired_or_extended_startup_fails_closed(self):
        for expiry in (NOW, NOW + timedelta(hours=3)):
            with self.assertRaises(RuntimeError):
                with TestClient(create_app(settings(expires_at=expiry), FakeAgent(), now=lambda: NOW)):
                    pass
