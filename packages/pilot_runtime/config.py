from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NotificationChannel = Literal["local", "email"]


class PilotSettings(BaseSettings):
    """Process-local pilot controls; secrets are never part of public reports."""

    model_config = SettingsConfigDict(
        env_prefix="DECISION_HUB_",
        env_file=None,
        extra="ignore",
        frozen=True,
    )

    pilot_mode: bool = False
    sources_enabled: bool = False
    market_enabled: bool = False
    notification_channel: NotificationChannel = "local"
    auto_trade: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, gt=0, le=65535)
    smtp_sender: str | None = None
    smtp_recipient: str | None = None
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = Field(default=10, gt=0)

    @model_validator(mode="after")
    def smtp_credentials_are_paired(self) -> PilotSettings:
        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("SMTP username and password must be configured together")
        return self

    @classmethod
    def from_env(cls) -> PilotSettings:
        return cls()
