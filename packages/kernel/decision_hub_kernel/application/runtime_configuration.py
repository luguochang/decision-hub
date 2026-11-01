"""Explicit, secret-free startup configuration recorded by each running process."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ServiceRuntimeConfigRecord,
    ServiceHeartbeatRecord,
    as_utc,
)


class RuntimeConfigurationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["runtime-configuration.v1"] = "runtime-configuration.v1"
    config_id: str
    service_id: str
    instance_id: str
    process_id: int
    started_at: datetime
    recorded_at: datetime
    fingerprint: str
    settings: dict[str, str | bool | int | float | None]


def capture_runtime_settings(database: Database, mode: str) -> dict:
    def enabled(key: str) -> bool:
        return os.getenv("DECISION_HUB_" + key, "0").strip().lower() in {"1", "true", "yes", "on"}

    # Deliberate allowlist. Never serialize the process environment or a URL
    # containing provider credentials, even when the API is only local.
    market = enabled("MARKET_ENABLED")
    return {
        "runtime_mode": mode,
        "sources_enabled": enabled("SOURCES_ENABLED"),
        "market_enabled": market,
        "calendar_discovery_enabled": enabled("CALENDAR_DISCOVERY_ENABLED"),
        "event_window_capture": market and enabled("EVENT_WINDOW_LIVE_ENABLED"),
        "document_correction_mode": "model"
        if enabled("LLM_ENABLED") and enabled("DOCUMENT_CORRECTION_LLM_ENABLED")
        else "deterministic",
        "notification_channel": os.getenv("DECISION_HUB_NOTIFICATION_CHANNEL", "local"),
        "research_runtime_setting": os.getenv("DECISION_HUB_RESEARCH_RUNTIME"),
        "database_dialect": database.engine.dialect.name,
        "database_file": database.engine.url.database
        if database.engine.dialect.name == "sqlite"
        else None,
        "plugin_build_hash": os.getenv("DECISION_HUB_DSH_PLUGIN_BUILD_HASH"),
        "dsh_source_commit": os.getenv("DECISION_HUB_DSH_SOURCE_COMMIT"),
    }


def configuration_record(
    *,
    service_id: str,
    instance_id: str,
    started_at: datetime,
    now: datetime,
    settings: dict,
) -> ServiceRuntimeConfigRecord:
    content = json.dumps(settings, sort_keys=True, ensure_ascii=False)
    identity = f"{service_id}:{instance_id}:{started_at.isoformat()}"
    return ServiceRuntimeConfigRecord(
        config_id="config_" + hashlib.sha256(identity.encode()).hexdigest()[:32],
        service_id=service_id,
        instance_id=instance_id,
        process_id=os.getpid(),
        started_at=started_at,
        recorded_at=now,
        fingerprint=hashlib.sha256(content.encode()).hexdigest(),
        settings_json=content,
    )


def list_runtime_configurations(
    database: Database, limit: int = 50, *, current_only: bool = False
) -> list[RuntimeConfigurationView]:
    with database.session() as session:
        query = session.query(ServiceRuntimeConfigRecord)
        if current_only:
            # Pick one latest exact startup per durable heartbeat BEFORE the
            # global limit. Host activity/history cannot crowd out consumers.
            latest = (
                select(ServiceRuntimeConfigRecord.config_id)
                .where(
                    ServiceRuntimeConfigRecord.service_id == ServiceHeartbeatRecord.service_id,
                    ServiceRuntimeConfigRecord.instance_id == ServiceHeartbeatRecord.instance_id,
                    ServiceRuntimeConfigRecord.started_at == ServiceHeartbeatRecord.started_at,
                )
                .order_by(
                    ServiceRuntimeConfigRecord.recorded_at.desc(),
                    ServiceRuntimeConfigRecord.config_id.desc(),
                )
                .limit(1)
                .correlate(ServiceHeartbeatRecord)
                .scalar_subquery()
            )
            query = query.join(
                ServiceHeartbeatRecord, ServiceRuntimeConfigRecord.config_id == latest
            )
        rows = (
            query
            .order_by(
                ServiceRuntimeConfigRecord.recorded_at.desc(), ServiceRuntimeConfigRecord.config_id
            )
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return [
            RuntimeConfigurationView(
                config_id=row.config_id,
                service_id=row.service_id,
                instance_id=row.instance_id,
                process_id=row.process_id,
                started_at=as_utc(row.started_at),
                recorded_at=as_utc(row.recorded_at),
                fingerprint=row.fingerprint,
                settings=json.loads(row.settings_json),
            )
            for row in rows
        ]
