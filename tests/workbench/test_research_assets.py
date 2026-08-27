from __future__ import annotations

from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    ObservationCreate,
    ResearchMemoCreate,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database


def test_research_memo_requires_existing_run_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'workbench.sqlite3'}")
    database.create_all()
    service = WorkbenchAssetService(database)
    request = ResearchMemoCreate(
        request_id="memo-request-1",
        run_id="missing-run",
        snapshot_id=None,
        domain_pack_ref="crypto_macro.v1",
        created_by="owner",
        claims=["Policy is restrictive."],
        evidence_refs=["official:fed:test"],
        counterpoints=["Growth could weaken faster."],
        uncertainties=["Next CPI is unknown."],
        follow_up_questions=["What changed versus the prior meeting?"],
    )

    with pytest.raises(ValueError, match="workbench_run_not_found"):
        service.create_memo(request)

    event_id, _, admitted = AdmissionService(database).admit(
        ObservationCreate(text="Workbench memo fixture")
    )
    assert admitted is True
    run_id, _ = RunService(database).create(event_id)
    accepted = request.model_copy(update={"run_id": run_id})
    first = service.create_memo(accepted)
    second = service.create_memo(accepted)
    assert first == second
    assert first.status == "submitted"
    assert len(service.list_memos()) == 1


def test_capability_is_deny_by_default_until_owner_enable(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'capabilities.sqlite3'}")
    database.create_all()
    service = WorkbenchAssetService(database)
    capability = service.register_capability(
        CapabilityManifest(
            capability_id="dsh.search.web",
            version="1.0.0",
            capability_type="tool",
            provider="dsh",
            license="MIT",
            input_schema_ref="search-query.v1",
            output_schema_ref="evidence-list.v1",
            permissions=["network:https"],
            network_domains=["example.com"],
            timeout_seconds=15,
            max_cost_usd=0.05,
            status="discovered",
        )
    )

    assert capability.status == "discovered"
    assert service.can_execute(capability.capability_id) is False
    with pytest.raises(PermissionError, match="capability_not_enabled"):
        service.require_enabled(capability.capability_id)
