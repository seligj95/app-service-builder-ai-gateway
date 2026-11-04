"""Agent Framework calls a model exclusively through one dedicated AI Gateway."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
from typing import Literal

import httpx2 as httpx

from config import MODEL_ALIAS, Settings


SCOPE = "https://azure-api.net/aigateway/.default"
logger = logging.getLogger("builder_gateway")


class GatewayFailure(RuntimeError):
    def __init__(self, code="gateway_request_failed"):
        super().__init__(code)
        self.code = code


@dataclass
class Turn:
    model_accepted: bool = False
    emitted: bool = False
    rejected_status: int | None = None
    retry_delay: float | None = 0.5

    @property
    def retryable(self):
        return (
            self.rejected_status in (429, 503)
            and not self.model_accepted and not self.emitted
            and self.retry_delay is not None
        )


class GatewayAgent:
    def __init__(self, settings: Settings, credential, transport=None):
        self.settings = settings
        self.credential = credential
        self.transport = transport
        self.gateway_requests = 0
        self.model_requests = 0

    async def token(self):
        return (await self.credential.get_token(SCOPE)).token

    async def guard_request(self, turn: Turn, request: httpx.Request):
        expected = self.settings
        if datetime.now(timezone.utc) >= expected.expires_at:
            raise GatewayFailure("sample_expired")
        if (
            request.url.scheme != "https" or request.url.host != expected.gateway_host
            or request.url.port not in (None, 443)
            or request.url.path != "/default/models/openai/v1/chat/completions"
            or request.url.query
        ):
            raise GatewayFailure("gateway_endpoint_rejected")
        if request.method != "POST" or len(await request.aread()) > 32_768:
            raise GatewayFailure("gateway_model_request_rejected")
        if self.gateway_requests >= 80 or self.model_requests >= 12:
            raise GatewayFailure("sample_request_budget_exhausted")
        self.gateway_requests += 1
        self.model_requests += 1
        logger.warning("gateway_model_request_started")

    async def observe_response(self, turn: Turn, response: httpx.Response):
        if not response.request.url.path.endswith("/chat/completions"):
            return
        turn.rejected_status = response.status_code
        if response.status_code < 400:
            turn.model_accepted = True
        try:
            delay = float(response.headers.get("Retry-After", "0.5"))
        except ValueError:
            turn.retry_delay = None
            return
        turn.retry_delay = delay if math.isfinite(delay) and 0 <= delay <= 2 else None

    @asynccontextmanager
    async def connected(self, turn: Turn):
        # SDK diagnostics can contain prompts, tokens, tool payloads or upstream bodies.
        for name in ("agent_framework", "openai", "httpx2", "httpcore2", "azure"):
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)

        from agent_framework import Agent
        from agent_framework.openai import OpenAIChatCompletionClient, OpenAIChatCompletionOptions
        from agent_framework.observability import disable_instrumentation
        from openai import AsyncOpenAI

        disable_instrumentation()

        class ModelOptions(OpenAIChatCompletionOptions, total=False):
            reasoning_effort: Literal["low"]

        async def before(request):
            await self.guard_request(turn, request)

        async def after(response):
            await self.observe_response(turn, response)

        common = dict(
            follow_redirects=False, timeout=httpx.Timeout(30, connect=10),
            event_hooks={"request": [before], "response": [after]},
        )
        if self.transport is not None:
            common["transport"] = self.transport
        async with (
            httpx.AsyncClient(**common) as model_http,
            AsyncOpenAI(
                api_key=self.token, base_url=self.settings.model_url,
                http_client=model_http, max_retries=0,
            ) as openai,
        ):
            client = OpenAIChatCompletionClient(
                model=MODEL_ALIAS, async_client=openai,
            )
            yield Agent(
                client=client, name="BuilderGatewayAgent",
                instructions=(
                    "You are a concise assistant demonstrating App Service Builder and Azure AI Gateway. "
                    "Answer general questions. You cannot inspect deployments or access external tools. "
                    "Do not claim access to live operational data."
                ),
                default_options=ModelOptions(max_tokens=2048, store=False, reasoning_effort="low"),
            )

    async def attempt(self, message: str, turn: Turn):
        async with self.connected(turn) as agent:
            async for update in agent.run(message, stream=True):
                if any(content.type == "error" or content.exception for content in update.contents):
                    raise GatewayFailure("gateway_model_failed")
                if update.text:
                    turn.emitted = True
                    yield update.text

    async def stream(self, message: str):
        turn = Turn()
        try:
            async with asyncio.timeout(60):
                for attempt in range(2):
                    try:
                        async for text in self.attempt(message, turn):
                            yield text
                        if not turn.emitted:
                            raise GatewayFailure("gateway_empty_response")
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if attempt == 0 and turn.retryable:
                            logger.warning("gateway_retry_before_output")
                            await asyncio.sleep(turn.retry_delay)
                            turn.rejected_status = None
                            continue
                        logger.warning("gateway_request_failed")
                        raise GatewayFailure() from None
        except TimeoutError:
            logger.warning("gateway_timeout")
            raise GatewayFailure("gateway_timeout") from None
