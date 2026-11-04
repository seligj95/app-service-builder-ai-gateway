from datetime import datetime, timedelta, timezone
import json
import unittest

from fastapi.testclient import TestClient

from app import PRINCIPAL_HEADER, SECRET_HEADER, Settings, create_app


PRINCIPAL = "00000000-0000-4000-8000-000000000001"
SECRET = "synthetic-test-value-not-a-real-credential-00000000"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def settings(**overrides) -> Settings:
    values = {
        "gateway_principal_id": PRINCIPAL,
        "identity_client_id": "00000000-0000-4000-8000-000000000002",
        "vault_url": "https://example-fixture.vault.azure.net",
        "secret_name": "mcp-backend-secret",
        "public_host": "callback.example.test",
        "expires_at": NOW + timedelta(hours=2),
    }
    return Settings(**(values | overrides))


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.clock = NOW
        self.app = create_app(settings(), lambda: SECRET, now=lambda: self.clock)
        self.client = TestClient(
            self.app, base_url="https://callback.example.test", follow_redirects=False
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def rpc(self, method, params=None, headers=None, request_id=1):
        body = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            body["params"] = params
        return self.client.post(
            "/mcp",
            json=body,
            headers={
                "Accept": "application/json, text/event-stream",
                PRINCIPAL_HEADER: PRINCIPAL,
                SECRET_HEADER: SECRET,
            } | (headers or {}),
        )

    def test_initialize_discover_and_invoke(self):
        response = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "local-qualification", "version": "1"},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["protocolVersion"], "2025-06-18")
        response = self.rpc("tools/list")
        self.assertEqual(response.status_code, 200)
        tools = response.json()["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["callback_probe"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        response = self.rpc("tools/call", {"name": "callback_probe", "arguments": {}})
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertFalse(result.get("isError", False))
        content = result.get("structuredContent") or json.loads(
            result["content"][0]["text"]
        )
        self.assertEqual(
            content,
            {
                "status": "ok",
                "identity_verified": True,
                "secret_verified": True,
                "invocation_count": 1,
            },
        )
        self.assertNotIn(SECRET, response.text)
        self.assertNotIn(PRINCIPAL, response.text)

    def test_secret_only_does_not_authorize(self):
        response = self.client.post(
            "/mcp", headers={SECRET_HEADER: SECRET}, json={}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "gateway_identity_required")

    def test_identity_without_secret_does_not_authorize(self):
        response = self.client.post(
            "/mcp", headers={PRINCIPAL_HEADER: PRINCIPAL}, json={}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "backend_secret_required")

    def test_wrong_identity_or_secret_never_executes(self):
        for headers in [
            {PRINCIPAL_HEADER: "00000000-0000-4000-8000-000000000099"},
            {SECRET_HEADER: "wrong"},
        ]:
            with self.subTest(headers=list(headers)):
                response = self.rpc(
                    "tools/call",
                    {"name": "callback_probe", "arguments": {}},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(self.app.state.successful_calls, 0)
                self.assertNotIn(SECRET, response.text)

    def test_duplicate_secret_is_rejected(self):
        response = self.client.post(
            "/mcp",
            json={},
            headers=[
                (PRINCIPAL_HEADER, PRINCIPAL),
                (SECRET_HEADER, SECRET),
                (SECRET_HEADER, SECRET),
            ],
        )
        self.assertEqual(response.status_code, 401)

    def test_identity_denial_logs_only_a_constant_event(self):
        with self.assertLogs("callback_probe", level="WARNING") as captured:
            response = self.rpc("tools/list", headers={PRINCIPAL_HEADER: SECRET})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            captured.output,
            ["WARNING:callback_probe:callback_gateway_identity_mismatch"],
        )

    def test_secret_denial_logs_only_a_constant_event(self):
        with self.assertLogs("callback_probe", level="WARNING") as captured:
            response = self.rpc("tools/list", headers={SECRET_HEADER: SECRET + "-wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            captured.output,
            ["WARNING:callback_probe:callback_backend_secret_mismatch"],
        )

    def test_denial_categories_never_include_header_values(self):
        cases = [
            ([], "callback_gateway_identity_missing"),
            ([(PRINCIPAL_HEADER, PRINCIPAL), (PRINCIPAL_HEADER, SECRET)], "callback_gateway_identity_duplicate"),
            ([(PRINCIPAL_HEADER, PRINCIPAL)], "callback_backend_secret_missing"),
            (
                [(PRINCIPAL_HEADER, PRINCIPAL), (SECRET_HEADER, SECRET), (SECRET_HEADER, SECRET)],
                "callback_backend_secret_duplicate",
            ),
        ]
        for headers, event in cases:
            with self.subTest(event=event), self.assertLogs("callback_probe", level="WARNING") as captured:
                response = self.client.post("/mcp", headers=headers, json={})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(captured.output, ["WARNING:callback_probe:" + event])
            self.assertEqual(self.app.state.successful_calls, 0)
            self.assertNotIn(SECRET, response.text)

    def test_expiry_blocks_previously_valid_credentials(self):
        self.clock = NOW + timedelta(hours=2)
        response = self.rpc("tools/list")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.app.state.successful_calls, 0)

    def test_hostile_host_is_rejected_by_mcp_transport(self):
        with self.assertNoLogs(level="WARNING"):
            response = self.rpc("tools/list", headers={"Host": SECRET.lower()})
        self.assertEqual(response.status_code, 421)
        self.assertNotIn(SECRET.lower(), response.text)

    def test_hostile_origin_is_rejected_without_logging_it(self):
        with self.assertNoLogs(level="WARNING"):
            response = self.rpc(
                "tools/list", headers={"Origin": f"https://{SECRET.lower()}.example.test"}
            )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SECRET.lower(), response.text)

    def test_health_is_liveness_only_and_docs_disabled(self):
        response = self.client.get("/health")
        self.assertEqual(response.json(), {"status": "ok"})
        for path in ["/docs", "/openapi.json"]:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_unknown_tool_cannot_be_called(self):
        response = self.rpc("tools/call", {"name": "not_allowed", "arguments": {}})
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(self.app.state.successful_calls, 0)

    def test_oversized_mcp_body_is_rejected(self):
        response = self.rpc("tools/list", {"padding": "x" * 17_000})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.app.state.successful_calls, 0)


class ConfigurationTests(unittest.TestCase):
    def test_startup_marker_follows_successful_secret_loading(self):
        with self.assertLogs("callback_probe", level="WARNING") as captured:
            with TestClient(create_app(settings(), lambda: SECRET, now=lambda: NOW)):
                pass
        self.assertEqual(
            captured.output,
            ["WARNING:callback_probe:callback_fixture_dev_test_only"],
        )
        with self.assertNoLogs("callback_probe", level="WARNING"), self.assertRaises(RuntimeError):
            with TestClient(create_app(settings(), lambda: "", now=lambda: NOW)):
                pass

    def test_unsafe_vault_origin_or_host_is_rejected(self):
        for value in [
            "http://example-fixture.vault.azure.net",
            "https://example-fixture.vault.azure.net.evil.test",
            "https://user@example-fixture.vault.azure.net",
            "https://example-fixture.vault.azure.net?secret=value",
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                settings(vault_url=value)
        with self.assertRaises(ValueError):
            settings(public_host="https://callback.example.test")

    def test_timezone_is_required(self):
        with self.assertRaises(ValueError):
            settings(expires_at=datetime(2030, 1, 1))

    def test_bad_secret_or_expired_configuration_fails_startup(self):
        for value in ["", "short", "unsafe\n" + "x" * 40]:
            with self.subTest(secret_length=len(value)), self.assertRaises(RuntimeError):
                with TestClient(create_app(settings(), lambda: value, now=lambda: NOW)):
                    pass
        with self.assertRaises(RuntimeError):
            with TestClient(
                create_app(settings(expires_at=NOW), lambda: SECRET, now=lambda: NOW)
            ):
                pass
        with self.assertRaisesRegex(RuntimeError, "cannot exceed two hours"):
            with TestClient(
                create_app(
                    settings(expires_at=NOW + timedelta(hours=3)),
                    lambda: SECRET,
                    now=lambda: NOW,
                )
            ):
                pass

    def test_secret_loader_failure_is_not_replaced_by_a_default(self):
        def fail():
            raise RuntimeError("synthetic-secret-provider-unavailable")

        with self.assertRaisesRegex(RuntimeError, "synthetic-secret-provider-unavailable"):
            with TestClient(create_app(settings(), fail, now=lambda: NOW)):
                pass


if __name__ == "__main__":
    unittest.main()
