from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import text

from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime.bootstrap import build_notification_adapters
from packages.pilot_runtime.config import PilotSettings
from packages.pilot_runtime.readiness import PilotReadinessService
from packages.provider_adapters.notifications.smtp import SMTPNotificationAdapter
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig
from packages.source_adapters.official_feeds import official_source_presets


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'pilot.sqlite3'}")
    database.initialize()
    return database


def _service(
    tmp_path: Path,
    *,
    settings: PilotSettings | None = None,
    environment: dict[str, str] | None = None,
) -> PilotReadinessService:
    return PilotReadinessService(
        _database(tmp_path),
        [source.manifest for source in official_source_presets()],
        settings
        or PilotSettings(
            pilot_mode=True,
            sources_enabled=True,
            market_enabled=True,
        ),
        provider_config=ProviderConfig(
            provider_id="fixture-provider",
            model="fixture-model",
            api_mode="responses",
        ),
        environment=(
            environment
            if environment is not None
            else {"DECISION_HUB_LLM_ENABLED": "1", "OPENAI_API_KEY": "test-secret"}
        ),
        clock=lambda: datetime(2026, 8, 27, 8, 0, tzinfo=UTC),
    )


def test_complete_local_pilot_configuration_is_ready_without_live_claims(tmp_path: Path) -> None:
    report = _service(tmp_path).report()

    assert report.status == "ready"
    assert all(item.status == "pass" for item in report.checks)
    assert report.source_ids == (
        "fed-press",
        "fed-speeches",
        "bls-releases",
        "bea-news",
        "bls-calendar",
    )
    assert "provider_structured_output" in report.live_canaries_required
    assert "test-secret" not in report.model_dump_json()


def test_missing_provider_configuration_fails_closed(tmp_path: Path) -> None:
    report = _service(tmp_path, environment={}).report()

    assert report.status == "not_ready"
    provider = next(item for item in report.checks if item.check_id == "provider")
    assert provider.error_code == "provider_configuration_invalid"


def test_auto_trade_or_private_execution_credentials_are_rejected(tmp_path: Path) -> None:
    settings = PilotSettings(
        pilot_mode=True, sources_enabled=True, market_enabled=True, auto_trade=True
    )
    report = _service(
        tmp_path,
        settings=settings,
        environment={
            "DECISION_HUB_LLM_ENABLED": "1",
            "OPENAI_API_KEY": "test-secret",
            "OKX_SECRET_KEY": "private-secret",
        },
    ).report()

    assert report.status == "not_ready"
    boundary = next(item for item in report.checks if item.check_id == "execution_boundary")
    assert boundary.error_code == "auto_trade_forbidden"
    assert "private-secret" not in report.model_dump_json()


def test_incomplete_email_configuration_is_rejected(tmp_path: Path) -> None:
    settings = PilotSettings(
        pilot_mode=True,
        sources_enabled=True,
        market_enabled=True,
        notification_channel="email",
        smtp_host="smtp.example.test",
    )
    report = _service(tmp_path, settings=settings).report()

    assert report.status == "not_ready"
    notification = next(item for item in report.checks if item.check_id == "notification")
    assert notification.error_code == "notification_configuration_invalid"


def test_database_without_alembic_head_is_not_ready(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'unmigrated.sqlite3'}")
    database.create_all()
    service = PilotReadinessService(
        database,
        [source.manifest for source in official_source_presets()],
        PilotSettings(pilot_mode=True, sources_enabled=True, market_enabled=True),
        provider_config=ProviderConfig(),
        environment={"DECISION_HUB_LLM_ENABLED": "1", "OPENAI_API_KEY": "test-secret"},
    )

    report = service.report()

    assert report.status == "not_ready"
    migration = next(item for item in report.checks if item.check_id == "database_migration")
    assert migration.error_code == "database_migration_missing"


def test_database_at_historical_head_is_not_ready(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with database.session() as session:
        session.execute(
            text("UPDATE alembic_version SET version_num = '0010_source_poll_schedule'")
        )
    service = PilotReadinessService(
        database,
        [source.manifest for source in official_source_presets()],
        PilotSettings(pilot_mode=True, sources_enabled=True, market_enabled=True),
        provider_config=ProviderConfig(
            provider_id="fixture-provider",
            model="fixture-model",
            api_mode="responses",
        ),
        environment={"DECISION_HUB_LLM_ENABLED": "1", "OPENAI_API_KEY": "test-secret"},
    )

    report = service.report()

    assert report.status == "not_ready"
    migration = next(item for item in report.checks if item.check_id == "database_migration")
    assert migration.error_code == "database_migration_outdated"


def test_email_adapter_receives_secret_without_exposing_it(tmp_path: Path) -> None:
    settings = PilotSettings(
        notification_channel="email",
        smtp_host="smtp.example.test",
        smtp_sender="owner@example.test",
        smtp_recipient="owner@example.test",
        smtp_username="owner",
        smtp_password=SecretStr("smtp-secret"),
    )
    adapters = build_notification_adapters(settings, tmp_path)

    adapter = adapters["email"]
    assert adapter.channel == "email"
    assert isinstance(adapter, SMTPNotificationAdapter)
    assert adapter.config.password is not None
    assert adapter.config.password.get_secret_value() == "smtp-secret"
    assert "smtp-secret" not in repr(adapter.config)
