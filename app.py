"""Disposable protected MCP callback fixture; not an agent or model demo."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import logging
import os
import re
from urllib.parse import urlsplit
from uuid import UUID

from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations


logger = logging.getLogger("callback_probe")
SECRET_HEADER = "x-appservice-mcp-secret"
PRINCIPAL_HEADER = "x-ms-client-principal-id"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Settings:
    gateway_principal_id: str
    identity_client_id: str
    vault_url: str
    secret_name: str
    public_host: str
    expires_at: datetime

    def __post_init__(self) -> None:
        UUID(self.gateway_principal_id)
        UUID(self.identity_client_id)
        vault = urlsplit(self.vault_url)
        if (
            vault.scheme != "https"
            or not vault.hostname
            or not re.fullmatch(r"[a-z0-9-]+\.vault\.azure\.net", vault.hostname)
            or vault.netloc != vault.hostname
            or vault.path not in ("", "/")
            or vault.query
            or vault.fragment
        ):
            raise ValueError("KEY_VAULT_URI must be a public Azure Key Vault HTTPS origin")
        if not re.fullmatch(r"[A-Za-z0-9-]{1,127}", self.secret_name):
            raise ValueError("KEY_VAULT_SECRET_NAME is invalid")
        if not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", self.public_host
        ):
            raise ValueError("CALLBACK_PUBLIC_HOST must be a lowercase hostname")
        if self.expires_at.tzinfo is None:
            raise ValueError("CALLBACK_TEST_EXPIRES_AT must include a timezone")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gateway_principal_id=os.environ["EXPECTED_GATEWAY_PRINCIPAL_ID"],
            identity_client_id=os.environ["AZURE_CLIENT_ID"],
            vault_url=os.environ["KEY_VAULT_URI"],
            secret_name=os.environ["KEY_VAULT_SECRET_NAME"],
            public_host=os.environ["CALLBACK_PUBLIC_HOST"],
            expires_at=datetime.fromisoformat(os.environ["CALLBACK_TEST_EXPIRES_AT"]),
        )


def load_secret(settings: Settings) -> str:
    try:
        with (
            ManagedIdentityCredential(client_id=settings.identity_client_id) as credential,
            SecretClient(
                vault_url=settings.vault_url,
                credential=credential,
                connection_timeout=10,
                read_timeout=10,
                retry_total=2,
            ) as client,
        ):
            secret = client.get_secret(settings.secret_name)
    except AzureError:
        logger.error("callback_secret_unavailable")
        raise RuntimeError("Callback secret could not be loaded") from None
    if secret.properties.expires_on is None or secret.properties.expires_on <= utc_now():
        raise RuntimeError("Callback secret must have a future expiration")
    return secret.value or ""


def create_app(
    settings: Settings | None = None,
    secret_loader: Callable[[], str] | None = None,
    now: Callable[[], datetime] = utc_now,
) -> FastAPI:
    config = settings if settings is not None else Settings.from_env()
    # SDK warning messages include raw Host/Origin values; HTTP errors remain explicit.
    logging.getLogger("mcp.server.transport_security").setLevel(logging.ERROR)
    server = MCPServer("callback-compatibility", log_level="WARNING")
    transport = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=16_384,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[config.public_host, f"{config.public_host}:*"],
            allowed_origins=[f"https://{config.public_host}"],
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if now() >= config.expires_at:
            raise RuntimeError("Callback experiment has expired")
        if config.expires_at - now() > timedelta(hours=2):
            raise RuntimeError("Callback experiment cannot exceed two hours")
        value = await asyncio.to_thread(
            secret_loader if secret_loader is not None else lambda: load_secret(config)
        )
        if not isinstance(value, str) or not re.fullmatch(r"[!-~]{32,256}", value):
            raise RuntimeError("Callback secret must be 32-256 printable ASCII characters")
        app.state.callback_secret = value.encode("ascii")
        app.state.successful_calls = 0
        try:
            async with transport.router.lifespan_context(transport):
                logger.warning("callback_fixture_dev_test_only")
                yield
        finally:
            app.state.callback_secret = b""

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def authorize_callback(request: Request, call_next):
        if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            if now() >= config.expires_at:
                return JSONResponse({"error": "experiment_expired"}, status_code=403)
            principals = request.headers.getlist(PRINCIPAL_HEADER)
            if len(principals) != 1 or not hmac.compare_digest(
                principals[0].lower().encode("utf-8"),
                config.gateway_principal_id.lower().encode("utf-8"),
            ):
                reason = "missing" if not principals else "duplicate" if len(principals) > 1 else "mismatch"
                logger.warning("callback_gateway_identity_%s", reason)
                return JSONResponse({"error": "gateway_identity_required"}, status_code=401)
            secrets = request.headers.getlist(SECRET_HEADER)
            expected = getattr(app.state, "callback_secret", b"")
            if not expected or len(secrets) != 1 or not hmac.compare_digest(
                secrets[0].encode("utf-8"), expected
            ):
                reason = (
                    "unavailable" if not expected
                    else "missing" if not secrets
                    else "duplicate" if len(secrets) > 1
                    else "mismatch"
                )
                logger.warning("callback_backend_secret_%s", reason)
                return JSONResponse({"error": "backend_secret_required"}, status_code=401)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @server.tool(
        name="callback_probe",
        description="Return a synthetic result after both callback authorization checks.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def callback_probe() -> dict[str, bool | str | int]:
        app.state.successful_calls += 1
        return {
            "status": "ok",
            "identity_verified": True,
            "secret_verified": True,
            "invocation_count": app.state.successful_calls,
        }

    app.mount("/", transport)
    return app
