from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import CapabilityManifest
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.workbench import (
    CapabilityCall,
    CapabilityExecutor,
)
from packages.workbench_adapters.dsh import DshCapabilityAdapter

OUTPUT_SCHEMA: Mapping[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {"type": "string"}}},
}


def manifest(*, timeout_seconds: float = 1) -> CapabilityManifest:
    return CapabilityManifest(
        capability_id="dsh.search.web",
        version="1.0.0",
        capability_type="tool",
        provider="dsh",
        license="MIT",
        input_schema_ref="search-query.v1",
        output_schema_ref="evidence-list.v1",
        permissions=["network:https"],
        network_domains=["example.com"],
        timeout_seconds=timeout_seconds,
        max_cost_usd=0.05,
        status="discovered",
    )


def call() -> CapabilityCall:
    return CapabilityCall(
        capability_id="dsh.search.web",
        input={"query": "Powell"},
        deadline_at=datetime.now(UTC) + timedelta(seconds=1),
        request_id="capability-call-1",
    )


def adapter(
    tmp_path: Path,
    executor: CapabilityExecutor,
    *,
    timeout_seconds: float = 1,
) -> DshCapabilityAdapter:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'capability.sqlite3'}")
    database.create_all()
    result = DshCapabilityAdapter(
        WorkbenchAssetService(database),
        {"dsh.search.web": executor},
        {"evidence-list.v1": OUTPUT_SCHEMA},
        owner_id="owner",
    )
    result.discover(manifest(timeout_seconds=timeout_seconds))
    return result


def test_capability_is_denied_until_owner_audits_and_enables(tmp_path: Path) -> None:
    async def execute(_call: CapabilityCall) -> Mapping[str, object]:
        return {"claims": ["Policy remains restrictive."]}

    selected = adapter(tmp_path, execute)
    with pytest.raises(AgentExecutionError) as denied:
        asyncio.run(selected.execute(call()))
    assert denied.value.error_code == "tool_denied"
    with pytest.raises(PermissionError, match="owner_identity_mismatch"):
        selected.audit("dsh.search.web", owner_id="other")

    selected.audit("dsh.search.web", owner_id="owner")
    selected.enable("dsh.search.web", owner_id="owner")
    result = asyncio.run(selected.execute(call()))

    assert result.status == "succeeded"
    assert result.output["claims"] == ["Policy remains restrictive."]


def test_capability_uses_manifest_timeout_and_validates_output(tmp_path: Path) -> None:
    async def slow(_call: CapabilityCall) -> Mapping[str, object]:
        await asyncio.sleep(0.05)
        return {"claims": []}

    selected = adapter(tmp_path, slow, timeout_seconds=0.001)
    selected.audit("dsh.search.web", owner_id="owner")
    selected.enable("dsh.search.web", owner_id="owner")
    with pytest.raises(AgentExecutionError) as timeout:
        asyncio.run(selected.execute(call()))
    assert timeout.value.error_code == "provider_timeout"

    async def invalid(_call: CapabilityCall) -> Mapping[str, object]:
        return {"unexpected": True}

    invalid_adapter = adapter(tmp_path / "invalid", invalid)
    invalid_adapter.audit("dsh.search.web", owner_id="owner")
    invalid_adapter.enable("dsh.search.web", owner_id="owner")
    with pytest.raises(AgentExecutionError) as invalid_output:
        asyncio.run(invalid_adapter.execute(call()))
    assert invalid_output.value.error_code == "structured_output_invalid"
