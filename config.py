"""Fail-closed configuration for the Builder-hosted gateway sample."""

from dataclasses import dataclass
from datetime import datetime
import os
import re
from typing import Mapping
from uuid import UUID


MODEL_ALIAS = "appservice-chat"


def guid(value: str) -> str:
    parsed = UUID(value)
    if parsed.int == 0:
        raise ValueError("An identity must be a nonzero UUID")
    return str(parsed)


@dataclass(frozen=True)
class Settings:
    gateway_host: str
    identity_client_id: str
    public_host: str
    expires_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "identity_client_id", guid(self.identity_client_id))
        if not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.ai\.gateway\.azure\.com", self.gateway_host):
            raise ValueError("Configure a dedicated Azure AI Gateway hostname")
        if not re.fullmatch(r"[a-z0-9-]+\.app\.westus2\.builderapps\.dev", self.public_host):
            raise ValueError("Configure the returned West US 2 Builder hostname")
        if self.expires_at.tzinfo is None:
            raise ValueError("SAMPLE_EXPIRES_AT must include a timezone")

    @property
    def origin(self) -> str:
        return "https://" + self.public_host

    @property
    def model_url(self) -> str:
        return f"https://{self.gateway_host}/default/models/openai/v1/"

    @classmethod
    def from_env(cls, source: Mapping[str, str] | None = None):
        env = os.environ if source is None else source
        return cls(
            gateway_host=env["AZURE_AI_GATEWAY_HOST"],
            identity_client_id=env["AZURE_CLIENT_ID"],
            public_host=env["APP_PUBLIC_HOST"],
            expires_at=datetime.fromisoformat(env["SAMPLE_EXPIRES_AT"]),
        )
