"""Graph phase times are execution observations, not source/PIT cutoffs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import (
    ResearchReview,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchTraceEvent,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchTraceRecord,
    SnapshotRecord,
    as_utc,
)
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    build_agentic_research_graph,
    initial_research_state,
)
from tests.orchestration.test_agentic_research_graph import (
    NOW,
    FakeResearchHarness,
    _candidate,  # pyright: ignore[reportPrivateUsage]
    _causal_case,  # pyright: ignore[reportPrivateUsage]
    _database,  # pyright: ignore[reportPrivateUsage]
    _horizons,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _result,  # pyright: ignore[reportPrivateUsage]
)
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert forbid_external_connections is not None


class ExecutionClock:
    def __init__(self) -> None:
        self.samples: list[datetime] = []

    def __call__(self) -> datetime:
        current = NOW + timedelta(seconds=30 + len(self.samples))
        self.samples.append(current)
        return current.astimezone(timezone(timedelta(hours=8)))


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_mode", ["live", "replay"])
async def test_actual_graph_phase_times_keep_original_pit_snapshot_and_old_trace(
    tmp_path: Path, execution_mode: Literal["live", "replay"],
) -> None:
    database = _database(tmp_path)
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    request = _request(
        execution_mode=execution_mode,
        pit_cutoff_at=NOW + timedelta(minutes=3) if execution_mode == "live" else NOW,
    )
    original_request = request.model_dump_json()
    prior = observability.append_trace(ResearchTraceEvent(
        schema_version="research-trace-event.v1",
        run_id=request.run_id,
        research_session_id=f"product:{request.run_id}",
        sequence_no=0,
        event_type="session_started",
        occurred_at=NOW,
        stage="planning",
        summary="An original historical trace retained verbatim.",
        reference_type="snapshot",
        reference_id=request.trigger_snapshot_id,
        status="running",
        error_code=None,
    ))
    with database.session() as session:
        old_row = session.query(ResearchTraceRecord).filter_by(
            run_id=request.run_id, sequence_no=prior.sequence_no,
        ).one()
        old_columns = {
            column.name: getattr(old_row, column.name)
            for column in ResearchTraceRecord.__table__.columns
        }
        trigger = session.get(SnapshotRecord, request.trigger_snapshot_id)
        assert trigger is not None
        trigger_columns = {
            column.name: getattr(trigger, column.name)
            for column in SnapshotRecord.__table__.columns
        }

    def reviewer_then_revision(
        round_request: ResearchSessionRequest, count: int,
    ) -> ResearchSessionResult:
        # Controlled local DTOs exercise the production graph and real saver;
        # this is neither a Provider execution nor a product success fixture.
        payload = _result(
            round_request, candidates=[_candidate()] if count == 1 else [],
            horizons=_horizons(),
        )
        payload["causal_case"] = _causal_case(
            thesis="The event is restrictive.", unresolved_questions=[],
        )
        started_at = NOW + timedelta(seconds=30 if count == 1 else 33)
        finished_at = started_at + timedelta(seconds=1)
        payload["started_at"] = started_at.isoformat()
        payload["finished_at"] = finished_at.isoformat()
        payload["rounds"][0]["started_at"] = started_at.isoformat()
        payload["rounds"][0]["finished_at"] = finished_at.isoformat()
        payload["rounds"][0]["plan"]["created_at"] = started_at.isoformat()
        payload["rounds"][0]["tool_invocations"][0]["started_at"] = started_at.isoformat()
        payload["rounds"][0]["tool_invocations"][0]["finished_at"] = finished_at.isoformat()
        if count == 1:
            payload["reviews"] = [ResearchReview(
                review_id="review-trace-time-replan",
                role="independent_reviewer",
                target_claim="The event is restrictive.",
                evidence_refs=["evidence-official"],
                result_hash="f" * 64,
                challenge="Acquire an independent source in the next bounded round.",
                resolution="requires_new_evidence",
                limitations=["Controlled unresolved challenge."],
                created_at=finished_at,
            ).model_dump(mode="json")]
        return ResearchSessionResult.model_validate(payload)

    def selection_clock() -> datetime:
        assert execution_mode == "live", "Replay must not consume the live selection clock"
        return NOW + timedelta(seconds=30)

    clock = ExecutionClock()
    harness = FakeResearchHarness(reviewer_then_revision)
    graph = build_agentic_research_graph(
        harness, ResearchEvidenceService(database), trace_sink=observability,
        selection_clock=selection_clock, execution_clock=clock,
    )
    try:
        state = await graph.ainvoke(initial_research_state(request))
        assert len(harness.requests) == 2
        new_trace = observability.list_trace(request.run_id, after=prior.sequence_no)
        phases = [
            event for event in new_trace
            if event.event_type in {"session_started", "coverage_assessed", "replan"}
        ]
        assert [(event.event_type, event.reference_id) for event in phases] == [
            ("session_started", request.trigger_snapshot_id),
            ("coverage_assessed", "round:1:before"),
            ("coverage_assessed", "round:1:after"),
            ("coverage_assessed", "round:2:before"),
            ("replan", "review-trace-time-replan"),
            ("coverage_assessed", "round:2:after"),
        ]
        assert len(clock.samples) == 6
        assert [event.occurred_at for event in phases] == clock.samples
        assert all(event.occurred_at > NOW for event in phases)
        assert request.model_dump_json() == original_request
        assert state["request"] == request.model_dump(mode="json")
        assert all(
            item.pit_cutoff_at == request.pit_cutoff_at
            and item.deadline_at == request.deadline_at
            and item.allowed_capabilities == request.allowed_capabilities
            for item in harness.requests
        )
        expected_cutoff = NOW if execution_mode == "replay" else NOW + timedelta(seconds=34)
        final = ResearchSessionResult.model_validate(state["final_result"])
        assert final.final_coverage.assessed_at == expected_cutoff
        with database.session() as session:
            original_row = session.query(ResearchTraceRecord).filter_by(
                run_id=request.run_id, sequence_no=prior.sequence_no,
            ).one()
            assert {
                column.name: getattr(original_row, column.name)
                for column in ResearchTraceRecord.__table__.columns
            } == old_columns
            trigger = session.get(SnapshotRecord, request.trigger_snapshot_id)
            assert trigger is not None
            assert {
                column.name: getattr(trigger, column.name)
                for column in SnapshotRecord.__table__.columns
            } == trigger_columns
            decision = session.get(SnapshotRecord, state["decision_snapshot_id"])
            assert decision is not None and as_utc(decision.cutoff_at) == expected_cutoff
    finally:
        database.engine.dispose()
