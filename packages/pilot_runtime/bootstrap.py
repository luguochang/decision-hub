from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import SourceManifest
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.sources import NotificationPort
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.notifications.smtp import SMTPConfig, SMTPNotificationAdapter
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig

from .config import PilotSettings
from .readiness import PilotReadinessService


def build_readiness_service(
    database: Database,
    source_manifests: Sequence[SourceManifest],
    *,
    settings: PilotSettings | None = None,
    environment: Mapping[str, str] | None = None,
) -> PilotReadinessService:
    """Build the shared readiness boundary for API and worker composition roots."""

    env = os.environ if environment is None else environment
    provider_configuration_valid = True
    try:
        provider_config = ProviderConfig.from_env()
    except Exception:
        provider_config = None
        provider_configuration_valid = False
    try:
        pilot_settings = settings or PilotSettings.from_env()
    except Exception:
        pilot_settings = PilotSettings()
        provider_configuration_valid = False
    return PilotReadinessService(
        database,
        source_manifests,
        pilot_settings,
        provider_config=provider_config,
        provider_configuration_valid=provider_configuration_valid,
        environment=env,
    )


def build_notification_adapters(
    settings: PilotSettings,
    data_dir: Path,
) -> dict[str, NotificationPort]:
    """Create exactly the configured outbox channel adapter."""

    if settings.notification_channel == "local":
        return {
            "local": LocalNotificationAdapter(
                data_dir / "exports" / "notifications.jsonl"
            )
        }
    return {
        "email": SMTPNotificationAdapter(
            SMTPConfig(
                host=settings.smtp_host or "",
                port=settings.smtp_port,
                sender=settings.smtp_sender or "",
                recipient=settings.smtp_recipient or "",
                username=settings.smtp_username,
                password=settings.smtp_password,
                starttls=settings.smtp_starttls,
                timeout_seconds=settings.smtp_timeout_seconds,
            )
        )
    }
