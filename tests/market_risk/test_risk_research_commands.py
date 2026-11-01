"""Actual isolated writers preserve fresh scope and idempotent child receipts."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import ResearchRunCommand
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
)
from packages.kernel.decision_hub_kernel.application.research_run_scope import (
    read_run_spot_admission_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord
from tests.market_risk.test_risk_research_admission import prepared as risk_prepared
from tests.market_risk.test_risk_research_admission import service


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    yield from risk_prepared.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_spot_child_requires_current_scope_and_original_replay_keeps_proof(prepared):
    import asyncio

    db, now, *_ = prepared
    bridge = await asyncio.to_thread(service, prepared)
    assert await bridge.process_due() == 2
    with db.session() as session:
        parent = session.scalar(select(RunRecord).limit(1))
        parent.status, parent.finished_at = "degraded", now[0]
        run_id = parent.run_id
    command = ResearchRunCommand(
        schema_version="research-run-command.v1",
        request_id="spot-recheck",
        command="recheck",
        reason="test",
    )
    with pytest.raises(ValueError, match="not_prepared"):
        ResearchCommandService(db, clock=lambda: now[0]).apply(run_id, command)
    commands = ResearchCommandService(
        db,
        clock=lambda: now[0],
        spot_admission_resolver=bridge.runs.spot_admission_resolver,
    )
    result = commands.apply(run_id, command)
    with db.session() as session:
        frozen = read_run_spot_admission_in_session(session, result.target_run_id)
        assert frozen.run_id == result.target_run_id
    now[0] += timedelta(hours=1)
    assert commands.apply(run_id, command).target_run_id == result.target_run_id
    with pytest.raises(ValueError):
        commands.apply(run_id, command.model_copy(update={"request_id": "expired-new-child"}))
    with db.session() as session:
        assert session.query(RunRecord).count() == 3
