from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts.models import (
    PilotReadinessCheck,
    PilotReadinessReport,
    SourceManifest,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig

from .config import PilotSettings

EXPECTED_MIGRATION_HEAD = "0010_source_poll_schedule"
PRIVATE_EXECUTION_ENV_VARS = (
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BYBIT_API_KEY",
    "BYBIT_API_SECRET",
    "DECISION_HUB_TRADING_PRIVATE_KEY",
    "EXCHANGE_PRIVATE_KEY",
)
LIVE_CANARIES = (
    "provider_structured_output",
    "source_connectivity_and_authorization",
    "market_data_quality",
    "notification_delivery",
    "continuous_operation",
)


class PilotReadinessService:
    """Aggregates local readiness without invoking any external adapter."""

    def __init__(
        self,
        database: Database,
        source_manifests: Sequence[SourceManifest],
        settings: PilotSettings,
        *,
        provider_config: ProviderConfig | None,
        provider_configuration_valid: bool = True,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.source_manifests = tuple(source_manifests)
        self.settings = settings
        self.provider_config = provider_config
        self.provider_configuration_valid = provider_configuration_valid
        self.environment = environment if environment is not None else os.environ
        self.clock = clock

    def report(self) -> PilotReadinessReport:
        checks = (
            self._pilot_mode(),
            *self._database(),
            self._provider(),
            self._sources(),
            self._market(),
            self._notification(),
            self._execution_boundary(),
        )
        ready = all(item.status != "fail" for item in checks)
        return PilotReadinessReport(
            status="ready" if ready else "not_ready",
            checked_at=self.clock(),
            pilot_mode=self.settings.pilot_mode,
            notification_channel=self.settings.notification_channel,
            source_ids=tuple(item.source_id for item in self.source_manifests if item.enabled),
            checks=checks,
            live_canaries_required=LIVE_CANARIES,
        )

    def _pilot_mode(self) -> PilotReadinessCheck:
        return _check(
            "pilot_mode",
            self.settings.pilot_mode,
            "single-owner pilot mode is explicitly enabled",
            "pilot mode is disabled",
            "pilot_mode_disabled",
        )

    def _database(self) -> tuple[PilotReadinessCheck, ...]:
        try:
            with self.database.session() as session:
                session.execute(text("SELECT 1"))
            connection = _pass("database_connection", "business ledger is reachable")
        except Exception:
            return (
                _fail(
                    "database_connection",
                    "business ledger is unavailable",
                    "database_unavailable",
                ),
            )
        try:
            with self.database.session() as session:
                revision = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            migration = _check(
                "database_migration",
                revision == EXPECTED_MIGRATION_HEAD,
                f"migration head is {EXPECTED_MIGRATION_HEAD}",
                "database migration head is not current",
                "database_migration_outdated",
            )
        except Exception:
            migration = _fail(
                "database_migration",
                "database migration metadata is unavailable",
                "database_migration_missing",
            )
        integrity = self._database_integrity()
        writable = self._data_directory_writable()
        return connection, migration, integrity, writable

    def _database_integrity(self) -> PilotReadinessCheck:
        if self.database.sqlite_path is None:
            return _warning(
                "database_integrity",
                "non-SQLite integrity requires a deployment-specific canary",
                "database_integrity_canary_required",
            )
        try:
            with self.database.session() as session:
                result = session.execute(text("PRAGMA quick_check")).scalar_one()
            return _check(
                "database_integrity",
                result == "ok",
                "SQLite quick_check passed",
                "SQLite quick_check failed",
                "database_integrity_failed",
            )
        except Exception:
            return _fail(
                "database_integrity",
                "SQLite integrity could not be checked",
                "database_integrity_unavailable",
            )

    def _data_directory_writable(self) -> PilotReadinessCheck:
        path = self.database.sqlite_path
        directory = path.parent if path is not None else Path.cwd()
        probe = directory / f".pilot-readiness-{uuid.uuid4().hex}.tmp"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe.write_text("readiness", encoding="ascii")
            probe.unlink()
        except OSError:
            return _fail(
                "data_directory",
                "data directory is not writable",
                "data_directory_not_writable",
            )
        return _pass("data_directory", "data directory is writable")

    def _provider(self) -> PilotReadinessCheck:
        has_key = any(
            self.environment.get(name)
            for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "SUB2API_API_KEY")
        )
        valid = (
            self.environment.get("DECISION_HUB_LLM_ENABLED", "0") == "1"
            and has_key
            and self.provider_configuration_valid
            and self.provider_config is not None
            and self.provider_config.supports_structured_output
            and self.provider_config.api_mode in self.provider_config.supported_api_modes
        )
        detail = (
            f"provider configured: {self.provider_config.provider_id}/"
            f"{self.provider_config.model}/{self.provider_config.api_mode}"
            if valid and self.provider_config is not None
            else "external structured-output provider is not safely configured"
        )
        return _check(
            "provider",
            valid,
            detail,
            detail,
            "provider_configuration_invalid",
        )

    def _sources(self) -> PilotReadinessCheck:
        authorized = tuple(
            manifest
            for manifest in self.source_manifests
            if manifest.enabled
            and manifest.authority_level != "unverified"
            and bool(manifest.allowed_domains)
        )
        valid = self.settings.sources_enabled and bool(authorized)
        return _check(
            "sources",
            valid,
            f"{len(authorized)} authorized source manifests enabled",
            "no authorized source manifest is enabled",
            "sources_not_ready",
        )

    def _market(self) -> PilotReadinessCheck:
        return _check(
            "market",
            self.settings.market_enabled,
            "public market outcome adapter is explicitly enabled",
            "market outcome adapter is disabled",
            "market_not_ready",
        )

    def _notification(self) -> PilotReadinessCheck:
        if self.settings.notification_channel == "local":
            return _pass("notification", "local committed-outbox delivery is configured")
        valid = all(
            (self.settings.smtp_host, self.settings.smtp_sender, self.settings.smtp_recipient)
        )
        return _check(
            "notification",
            valid,
            "SMTP committed-outbox delivery is configured",
            "SMTP host, sender, and recipient are required",
            "notification_configuration_invalid",
        )

    def _execution_boundary(self) -> PilotReadinessCheck:
        private_keys = tuple(
            name for name in PRIVATE_EXECUTION_ENV_VARS if self.environment.get(name)
        )
        valid = not self.settings.auto_trade and not private_keys
        return _check(
            "execution_boundary",
            valid,
            "automatic trading is disabled and no exchange credentials are present",
            "automatic trading or private execution credentials are forbidden",
            "auto_trade_forbidden",
        )


def _check(
    check_id: str,
    condition: bool,
    pass_detail: str,
    fail_detail: str,
    error_code: str,
) -> PilotReadinessCheck:
    return _pass(check_id, pass_detail) if condition else _fail(check_id, fail_detail, error_code)


def _pass(check_id: str, detail: str) -> PilotReadinessCheck:
    return PilotReadinessCheck(check_id=check_id, status="pass", detail=detail)


def _warning(check_id: str, detail: str, error_code: str) -> PilotReadinessCheck:
    return PilotReadinessCheck(
        check_id=check_id, status="warning", detail=detail, error_code=error_code
    )


def _fail(check_id: str, detail: str, error_code: str) -> PilotReadinessCheck:
    return PilotReadinessCheck(
        check_id=check_id, status="fail", detail=detail, error_code=error_code
    )
