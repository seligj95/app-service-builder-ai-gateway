"""Fail-closed configuration for the Builder-hosted gateway sample."""

from dataclasses import dataclass
from datetime import datetime
import os
import re
from typing import Mapping
from uuid import UUID


MODEL_ALIAS = "appservice-chat"
TOOL_SERVER = "appservice-ops"
TOOL_NAMES = ("get_service_status", "get_deployment_context")
REMOTE_TOOLS = tuple(f"{TOOL_SERVER}_{name}" for name in TOOL_NAMES)


def guid(value: str) -> str:
    parsed = UUID(value)
    if parsed.int == 0:
        raise ValueError("An identity must be a nonzero UUID")
    return str(parsed)


@dataclass(frozen=True)
class Settings:
    gateway_host: str
    gateway_principal_id: str
    tenant_id: str
    chat_user_ids: tuple[str, ...]
    identity_client_id: str
    public_host: str
    expires_at: datetime
    app_name: str = "builder-gateway-agent"
    region: str = "westus2"

    def __post_init__(self):
        for field in ("gateway_principal_id", "tenant_id", "identity_client_id"):
            object.__setattr__(self, field, guid(getattr(self, field)))
        users = tuple(guid(value) for value in self.chat_user_ids)
        if not 1 <= len(users) <= 20 or len(set(users)) != len(users):
            raise ValueError("Configure 1-20 distinct chat identities")
        if self.gateway_principal_id in users:
            raise ValueError("Gateway and browser identities must be separate")
        object.__setattr__(self, "chat_user_ids", users)
        if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ai\.gateway\.azure\.com", self.gateway_host):
            raise ValueError("Configure a dedicated Azure AI Gateway hostname")
        if not re.fullmatch(r"[a-z0-9-]+\.app\.westus2\.builderapps\.dev", self.public_host):
            raise ValueError("Configure the returned West US 2 Builder hostname")
        if self.expires_at.tzinfo is None:
            raise ValueError("SAMPLE_EXPIRES_AT must include a timezone")
        for value in (self.app_name, self.region):
            if not re.fullmatch(r"[a-z0-9-]{1,63}", value):
                raise ValueError("App name and region must be nonsecret labels")

    @property
    def origin(self) -> str:
        return "https://" + self.public_host

    @property
    def model_url(self) -> str:
        return f"https://{self.gateway_host}/default/models/openai/v1/"

    @property
    def mcp_url(self) -> str:
        return f"https://{self.gateway_host}/default/toolservers/{TOOL_SERVER}/mcp"

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None):
        env = os.environ if source is None else source
        return cls(
            gateway_host=env["AZURE_AI_GATEWAY_HOST"],
            gateway_principal_id=env["EXPECTED_GATEWAY_PRINCIPAL_ID"],
            tenant_id=env["ENTRA_TENANT_ID"],
            chat_user_ids=tuple(env["CHAT_USER_OBJECT_IDS"].split(",")),
            identity_client_id=env["AZURE_CLIENT_ID"],
            public_host=env["CALLBACK_PUBLIC_HOST"],
            expires_at=datetime.fromisoformat(env["SAMPLE_EXPIRES_AT"]),
            app_name=env.get("SAMPLE_APP_NAME", "builder-gateway-agent"),
            region=env.get("SAMPLE_REGION", "westus2"),
        )
