"""Explicit, read-only release baseline wiring; no readiness or approval grant.

Only a trusted application bootstrap may inject the installed baseline bytes.
No environment/header/passed flag is converted into that dependency. Runtime
metadata remains unverified until the independent native execution boundary.
This loader never migrates, seeds, approves or creates a deployment pointer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import inspect

from packages.kernel.decision_hub_kernel.application.research_binding import (
    PreparedResearchPolicyRefs,
    load_prepared_pack_definition,
)
from packages.kernel.decision_hub_kernel.application.research_harness_artifact import (
    HarnessArtifactIndexV1,
    InstalledHarnessBaseline,
)
from packages.kernel.decision_hub_kernel.application.research_harness_consumption import (
    HarnessFirstFreezeService,
)
from packages.kernel.decision_hub_kernel.application.research_harness_deployment import (
    HARNESS_METADATA,
    ResearchHarnessDeploymentService,
)
from packages.kernel.decision_hub_kernel.application.research_policy_resources import (
    normalized_relative_ref,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database


class HarnessBaselineWorkerConfig(BaseModel):
    """Internal bootstrap configuration, not an API or user-selected version."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["research-harness-worker-baseline-config.v1"]
    selection_mode: Literal["release_baseline_only"]
    archive_relative_ref: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    release_artifact_index: HarnessArtifactIndexV1


def configured_baseline_first_freeze(
    database: Database,
    *,
    config: HarnessBaselineWorkerConfig | None,
    installed_baseline: InstalledHarnessBaseline | None,
    data_root: Path,
    prepared_pack_root: Path,
    refs: PreparedResearchPolicyRefs,
    allowed_capabilities: tuple[str, ...],
    execution_mode: Literal["live", "replay"],
    runtime_id: str,
) -> HarnessFirstFreezeService | None:
    if config is None:
        if installed_baseline is not None:
            raise ValueError("harness_baseline_config_required")
        return None
    if installed_baseline is None:
        raise ValueError("harness_installed_baseline_dependency_required")
    if database.engine.dialect.name != "sqlite":
        raise ValueError("harness_worker_sqlite_required")
    # Typed bytes are a server-owned snapshot, not proof of a live Host. Its
    # actual definition must match this Worker composition's actual Pack.
    config = HarnessBaselineWorkerConfig.model_validate_json(config.model_dump_json())
    baseline = InstalledHarnessBaseline(
        installed_baseline.definition_json, installed_baseline.runtime_json
    )
    actual_definition = load_prepared_pack_definition(
        prepared_pack_root, refs, allowed_capabilities=allowed_capabilities,
        execution_mode=execution_mode,
    )
    if actual_definition != baseline.definition:
        raise ValueError("harness_worker_pack_baseline_mismatch")
    if runtime_id != baseline.runtime.runtime_id:
        raise ValueError("harness_worker_runtime_identity_mismatch")
    # Archive paths are relative to the trusted application data directory;
    # no new directory is created. Symlink escapes and missing paths fail.
    relative = normalized_relative_ref(config.archive_relative_ref)
    root = data_root.resolve(strict=True)
    archive = (root / relative).resolve(strict=True)
    if not archive.is_relative_to(root) or not archive.is_dir():
        raise ValueError("harness_worker_archive_outside_data_root")
    # Ordinary startup must already have applied Alembic 0067. This loader
    # performs no CREATE/seed and never silently falls back on missing tables.
    schema = inspect(database.engine)
    for table in HARNESS_METADATA.sorted_tables:
        if not schema.has_table(table.name, schema="main"):
            raise ValueError("harness_worker_migration_required")
        columns = schema.get_columns(table.name, schema="main")
        actual = {
            item["name"]: (str(item["type"]).upper(), item["nullable"], bool(item["primary_key"]))
            for item in columns
        }
        expected = {
            column.name: (str(column.type).upper(), column.nullable, column.primary_key)
            for column in table.columns
        }
        if actual != expected:
            raise ValueError("harness_worker_schema_mismatch")
    deployment = ResearchHarnessDeploymentService(
        database, archive_root=archive, baseline=baseline,
        release_baseline_index=config.release_artifact_index,
        owner_identity="baseline-read-only",
        # principal_authorizer remains None: every owner mutation is denied.
    )
    return HarnessFirstFreezeService(deployment)
