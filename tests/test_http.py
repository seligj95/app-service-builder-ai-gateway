from datetime import datetime, timedelta, timezone
import logging
import os
import unittest
from unittest.mock import patch

from azure.identity.aio import ManagedIdentityCredential
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import ChatRequest, create_app
from config import Settings
from gateway import GatewayFailure


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
ADC_HOST = "a0a7f3a7-0c9c-49b6-a81d-f11556de623e.apps.westus2.adcapps.io"


def settings(**overrides):
    return Settings(**({
        "gateway_host": "example.eastus2-01.ai.gateway.azure.com",
        "identity_client_id": "00000000-0000-4000-8000-000000000009",
        "public_host": "sample.app.westus2.builderapps.dev",
        "expires_at": NOW + timedelta(hours=2),
    } | overrides))


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

    def test_anonymous_page_and_chat_need_no_cookie_or_identity_header(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.post(
            "/api/chat/stream", json={"message": "Hello"},
            headers={"Origin": settings().origin},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: delta", response.text)
        self.assertIn("Hello ", response.text)
        self.assertIn("Builder.", response.text)
        self.assertTrue(response.text.endswith("event: done\ndata: {}\n\n"))
        self.assertEqual(self.app.state.active_chats, 0)
        self.assertEqual(self.client.post("/mcp", json={}).status_code, 404)

    def test_actual_adc_host_without_forwarded_host_serves_page_and_chat(self):
        headers = {"Host": ADC_HOST}
        page = self.client.get("/", headers=headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn("AI Gateway chat", page.text)
        response = self.client.post(
            "/api/chat/stream", headers=headers | {"Origin": settings().origin},
            json={"message": "Hello"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: delta", response.text)

    def test_transport_and_forwarded_headers_cannot_change_trusted_origin(self):
        response = self.client.post(
            "/api/chat/stream",
            headers={"Host": ADC_HOST, "X-Forwarded-Host": "hostile.example",
                     "X-Forwarded-Proto": "http", "Origin": "https://hostile.example"},
            json={"message": "Hello"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.state.next_chat_at, 0)

    def test_missing_hostile_and_duplicate_origins_are_rejected(self):
        for headers in ({}, {"Origin": "https://hostile.example"},
                        [("Origin", settings().origin), ("Origin", settings().origin)]):
            response = self.client.post("/api/chat/stream", headers=headers, json={"message": "Hello"})
            self.assertEqual(response.status_code, 403)

    def test_invalid_input_and_overloaded_chat_are_explicit_errors(self):
        headers = {"Origin": settings().origin}
        for payload in ({"message": ""}, {"message": " "}, {"message": "x" * 4001},
                        {"message": "x", "extra": "private"}):
            response = self.client.post("/api/chat/stream", headers=headers, json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("private", response.text)
        self.app.state.active_chats = 2
        self.assertEqual(self.client.post(
            "/api/chat/stream", headers=headers, json={"message": "x"},
        ).status_code, 429)

    def test_global_admission_interval_is_five_seconds(self):
        with patch("app.monotonic", return_value=100) as clock:
            def post():
                return self.client.post("/api/chat/stream", headers={"Origin": settings().origin},
                                        json={"message": "Hello"})
            self.assertEqual(post().status_code, 200)
            clock.return_value = 104.99
            denied = post()
            self.assertEqual(denied.status_code, 429)
            self.assertEqual(denied.headers["Retry-After"], "5")
            clock.return_value = 105
            self.assertEqual(post().status_code, 200)

    def test_partial_stream_failure_is_sanitized_not_replayed(self):
        self.agent.fail = True
        response = self.client.post("/api/chat/stream", headers={"Origin": settings().origin},
                                    json={"message": "x"})
        self.assertIn("Hello ", response.text)
        self.assertIn("event: error", response.text)
        self.assertNotIn("sensitive-upstream-content", response.text)
        self.assertEqual(self.app.state.active_chats, 0)

    def test_expiry_health_and_chunked_body_bounds(self):
        response = self.client.post("/api/chat/stream", headers={
            "Origin": settings().origin, "Content-Type": "application/json",
        }, content=(chunk for chunk in (b'{"message":"', b"x" * 9000, b"x" * 9000, b'"}')))
        self.assertEqual(response.status_code, 413)
        self.clock = NOW + timedelta(hours=2)
        self.assertEqual(self.client.get("/").status_code, 403)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_cancelled_stream_releases_chat_slot(self):
        async def check():
            endpoint = next(route.endpoint for route in self.app.routes
                            if getattr(route, "path", "") == "/api/chat/stream")
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

    def test_page_and_assets_have_csp_without_identity_requirements(self):
        page = self.client.get("/")
        self.assertIn("sha256-", page.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/static/chat.js").status_code, 200)
        self.assertEqual(self.client.get("/static/unknown.js").status_code, 404)


class ConfigTests(unittest.TestCase):
    def test_direct_provider_or_ambiguous_endpoint_configuration_is_rejected(self):
        for host in ("https://example.ai.gateway.azure.com", "resource.openai.azure.com",
                     "example.ai.gateway.azure.com.evil", "example.ai.gateway.azure.com/path"):
            with self.assertRaises(ValueError):
                settings(gateway_host=host)
        for changes in ({"public_host": "hostile.example"}, {"identity_client_id": "bad"},
                        {"expires_at": NOW.replace(tzinfo=None)}):
            with self.assertRaises(ValueError):
                settings(**changes)

    def test_expired_or_extended_startup_fails_closed(self):
        for expiry in (NOW, NOW + timedelta(hours=3)):
            with self.assertRaises(RuntimeError):
                with TestClient(create_app(settings(expires_at=expiry), FakeAgent(), now=lambda: NOW)):
                    pass
