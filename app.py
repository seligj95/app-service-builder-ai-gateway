"""Protected Builder chat streaming model responses through AI Gateway."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import hashlib
import logging
import re
from pathlib import Path
from uuid import uuid4

from azure.identity.aio import ManagedIdentityCredential
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from config import Settings
from gateway import GatewayAgent, GatewayFailure
from identity import verified_identity


logger = logging.getLogger("builder_gateway")
STATIC = Path(__file__).parent / "static"


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 16_384:
                await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)
                return
            if chunk:
                chunks.append(chunk)
            if not message.get("more_body"):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks)}
            return await receive()

        await self.app(scope, replay, send)


def utc_now():
    return datetime.now(timezone.utc)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None, agent=None, now=utc_now):
    config = settings if settings is not None else Settings.from_env()
    logger.setLevel(logging.WARNING)
    html = (STATIC / "index.html").read_text()

    def inline_hash(tag):
        text = re.search(f"<{tag}>(.*?)</{tag}>", html, re.DOTALL).group(1)
        digest = base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        return f"'sha256-{digest}'"

    script_hash, style_hash = inline_hash("script"), inline_hash("style")

    @asynccontextmanager
    async def lifespan(app):
        if not timedelta(0) < config.expires_at - now() <= timedelta(hours=2):
            raise RuntimeError("The isolated DEV/TEST window must be within two hours")
        credential = None
        if agent is None:
            credential = ManagedIdentityCredential(client_id=config.identity_client_id)
        app.state.agent = agent if agent is not None else GatewayAgent(config, credential)
        app.state.active_chats = 0
        try:
            logger.warning("sample_dev_test_ready")
            yield
        finally:
            if credential is not None:
                await credential.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        path = request.scope["path"]
        if path == "/health":
            return await call_next(request)
        if request.headers.getlist("host") != [config.public_host]:
            return JSONResponse({"error": "invalid_host"}, status_code=400)
        if now() >= config.expires_at:
            return JSONResponse({"error": "sample_expired"}, status_code=403)
        identity = verified_identity(request, config)
        if identity not in config.chat_user_ids:
            logger.warning("chat_identity_denied")
            return JSONResponse({"error": "identity_required"}, status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; connect-src 'self'; script-src 'self' {script_hash}; "
            f"style-src 'self' {style_hash}; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    @app.get("/static/{name}")
    async def static(name: str):
        if name not in ("chat.js", "site.css"):
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(STATIC / name)

    @app.post("/api/chat/stream")
    async def chat(request: Request, payload: ChatRequest):
        if request.headers.getlist("origin") != [config.origin]:
            return JSONResponse({"error": "same_origin_required"}, status_code=403)
        if not payload.message.strip():
            return JSONResponse({"error": "invalid_request"}, status_code=422)
        if app.state.active_chats >= 2:
            return JSONResponse({"error": "chat_busy"}, status_code=429)
        correlation = str(uuid4())

        async def events():
            def sse(event, data):
                return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"

            if app.state.active_chats >= 2:
                yield sse("error", {"code": "chat_busy", "message": "Two chats are already active."})
                yield sse("done", {})
                return
            app.state.active_chats += 1
            logger.warning("chat_started")
            try:
                yield sse("meta", {"correlationId": correlation})
                async with asyncio.timeout(65):
                    async for text in app.state.agent.stream(payload.message):
                        if now() >= config.expires_at:
                            raise GatewayFailure("sample_expired")
                        yield sse("delta", {"text": text})
                yield sse("done", {})
            except (GatewayFailure, TimeoutError):
                logger.warning("chat_failed")
                yield sse("error", {"code": "gateway_unavailable", "message": "The gateway request failed."})
                yield sse("done", {})
            finally:
                app.state.active_chats -= 1

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "X-Correlation-Id": correlation},
        )

    return app
