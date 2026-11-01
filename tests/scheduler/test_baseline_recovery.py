import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.decision.gate import evaluate_gate
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    OutboxRecord,
    RunRecord,
)
from packages.orchestration.langgraph import build_analyze_text_service
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_killed_baseline_is_recovered_once_and_late_owner_cannot_publish(tmp_path):
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'baseline.sqlite3'}")
    db.create_all()
    service = build_analyze_text_service(db, FakeAgentRuntime())
    event, _, _ = service.admission.admit(
        ObservationCreate(text="Powell says rates may stay higher for longer.")
    )
    run_id, _ = service.runs.create(event)
    assert service.runs.claim_for_execution(run_id, worker_id="killed", lease_seconds=1)
    with db.session() as session:
        session.get(RunRecord, run_id).lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert service.runs.admitted_targets(strategy_version="baseline.v1", recover_expired=True) == [
        (event, run_id)
    ]
    assert service.runs.claim_for_execution(run_id, worker_id="replacement", lease_seconds=30)
    gate = evaluate_gate({"direction": "no_trade"})
    with pytest.raises(PermissionError, match="lease_lost"):
        CommitDecisionService(db).commit(run_id, event, {}, gate, expected_lease_owner="killed")
    assert service.runs.fail_if_owned(run_id, "killed", error_code="late_failure") is False
    with db.session() as session:
        assert session.query(ArtifactRecord).count() == 0
        row = session.get(RunRecord, run_id)
        assert row.lease_owner == "replacement" and row.status == "running"
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert asyncio.run(service.run_admitted(event, run_id, worker_id="final-owner"))
    assert not asyncio.run(service.run_admitted(event, run_id, worker_id="another-owner"))
    with db.session() as session:
        assert session.query(ArtifactRecord).count() == 1
        assert session.query(OutboxRecord).count() == 1
        assert session.get(RunRecord, run_id).lease_owner is None
