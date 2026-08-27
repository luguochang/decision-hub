from __future__ import annotations

import asyncio
import smtplib
from collections.abc import Callable
from email.message import EmailMessage

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.kernel.decision_hub_kernel.ports.sources import (
    NotificationMessage,
    NotificationResult,
)


class SMTPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: str = Field(min_length=1)
    port: int = Field(default=587, gt=0, le=65535)
    sender: str = Field(min_length=3)
    recipient: str = Field(min_length=3)
    username: str | None = None
    password: SecretStr | None = None
    starttls: bool = True
    timeout_seconds: float = Field(default=10, gt=0)


Sender = Callable[[SMTPConfig, EmailMessage], str | None]


class SMTPNotificationAdapter:
    channel = "email"

    def __init__(self, config: SMTPConfig, *, sender: Sender | None = None) -> None:
        self.config = config
        self.sender = sender or _send

    async def deliver(self, message: NotificationMessage) -> NotificationResult:
        email = EmailMessage()
        email["From"] = self.config.sender
        email["To"] = self.config.recipient
        email["Subject"] = message.subject
        email["X-Decision-Hub-Dedupe-Key"] = message.dedupe_key
        email.set_content(message.body)
        try:
            provider_id = await asyncio.to_thread(self.sender, self.config, email)
        except smtplib.SMTPResponseException as exc:
            retryable = exc.smtp_code >= 400 and exc.smtp_code < 500
            return NotificationResult(
                delivered=False,
                retryable=retryable,
                error_code="notification_rate_limited" if retryable else "notification_rejected",
            )
        except (OSError, TimeoutError, smtplib.SMTPException):
            return NotificationResult(
                delivered=False,
                retryable=True,
                error_code="notification_unavailable",
            )
        return NotificationResult(delivered=True, provider_message_id=provider_id)


def _send(config: SMTPConfig, message: EmailMessage) -> str | None:
    with smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds) as client:
        if config.starttls:
            client.starttls()
        if config.username and config.password:
            client.login(config.username, config.password.get_secret_value())
        client.send_message(message)
    return message.get("Message-ID")
