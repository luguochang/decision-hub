from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ExecutionBudget,
    ResearchInputEvidence,
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, SnapshotRecord
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.graphs.agentic_research_graph import (
    attempts_from_result,
    build_agentic_research_graph,
    initial_research_state,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _request(
    *,
    execution_mode: Literal["live", "replay"] = "replay",
    pit_cutoff_at: datetime = NOW,
    deadline_at: datetime | None = None,
) -> ResearchSessionRequest:
    text = "The official said inflation risks remain elevated."
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="request-graph-1",
        run_id="run-graph-1",
        event_id="event-graph-1",
        trigger_snapshot_id="snap-trigger",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode=execution_mode,
        pit_cutoff_at=pit_cutoff_at,
        current_round=1,
        evidence_refs=["trigger-evidence"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="trigger-evidence",
                kind="transcript",
                authority="unverified",
                source_id="manual",
                source_url=None,
                published_at=NOW - timedelta(seconds=2),
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
                content_hash="0" * 64,
                excerpt=text,
            )
        ],
        evidence_requirements=[
            EvidenceRequirement(
                requirement_id="event_identity",
                description="Verify the event identity.",
                importance="hard",
                source_priority=["official"],
                authority_floor="official",
                preferred_capabilities=["replay.research"],
                freshness_seconds=3600,
                minimum_independent_sources=1,
                allowed_fallbacks=["web.search"],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=2,
            max_tool_calls=4,
            max_subagents=2,
            total_deadline_seconds=180,
            per_tool_timeout_seconds=20,
            per_model_step_timeout_seconds=60,
            max_structured_repairs=1,
            max_estimated_cost_usd=1.0,
        ),
        deadline_at=deadline_at or NOW + timedelta(minutes=3),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


class FakeResearchHarness:
    runtime_id = "fake-harness"
    runtime_version = "fake-harness.v1"
    profile_ref = "fake-harness.v1"

    def __init__(
        self,
        result_factory: Callable[[ResearchSessionRequest, int], ResearchSessionResult],
    ):
        self.result_factory = result_factory
        self.requests: list[ResearchSessionRequest] = []

    async def execute(
        self, request: ResearchSessionRequest, trace_sink: Any = None
    ) -> ResearchSessionResult:
        self.requests.append(request)
        return self.result_factory(request, len(self.requests))

    async def close(self):
        return None


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'graph.sqlite3'}")
    database.create_all()
    with database.session() as session:
        trigger_evidence = {
            "evidence_id": "trigger-evidence",
            "text": "The official said inflation risks remain elevated.",
            "source_id": "manual",
            "source_type": "transcript",
            "observed_at": (NOW - timedelta(seconds=1)).isoformat(),
            "published_at": (NOW - timedelta(seconds=2)).isoformat(),
            "received_at": NOW.isoformat(),
            "cutoff_at": NOW.isoformat(),
            "content_hash": "0" * 64,
        }
        session.add(
            SnapshotRecord(
                snapshot_id="snap-trigger",
                event_id="event-graph-1",
                cutoff_at=NOW,
                snapshot_hash=hashlib.sha256(json.dumps(trigger_evidence).encode()).hexdigest(),
                evidence_json=json.dumps([trigger_evidence]),
                snapshot_type="trigger",
                run_id="run-graph-1",
                generation=1,
                created_at=NOW,
            )
        )
        session.add(
            RunRecord(
                run_id="run-graph-1",
                event_id="event-graph-1",
                snapshot_id="snap-trigger",
                status="running",
                strategy_version="candidate.v1",
                runtime_version="fake-harness.v1",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return database


def _result(
    request: ResearchSessionRequest,
    *,
    candidates: list[dict[str, Any]],
    horizons: list[dict[str, Any]],
) -> dict[str, Any]:
    coverage = {
        "status": "insufficient",
        "covered_requirement_ids": [],
        "gaps": [],
        "conflicts": [],
        "hard_coverage_ratio": 0.0,
        "soft_coverage_ratio": 1.0,
        "assessed_at": NOW.isoformat(),
    }
    return {
        "schema_version": "research-session-result.v1",
        "request_id": request.request_id,
        "research_session_id": "session-graph-1",
        "runtime_id": "fake-harness",
        "runtime_version": "fake-harness.v1",
        "profile_ref": "fake-harness.v1",
        "trace_ref": "fake://trace",
        "trace_hash": "0" * 64,
        "status": "completed",
        "rounds": [
            {
                "round": request.current_round,
                "plan": {
                    "plan_id": "plan-1",
                    "objective": "verify",
                    "tasks": [
                        {
                            "task_id": "task-1",
                            "requirement_id": "event_identity",
                            "capability_id": "replay.research",
                            "objective": "verify",
                            "question": "verify",
                            "input_evidence_refs": list(request.evidence_refs),
                            "output_schema_ref": "evidence-candidate.v1",
                            "depends_on": [],
                            "success_condition": "one source",
                            "priority": 1,
                        }
                    ],
                    "required_capabilities": ["replay.research"],
                    "created_at": NOW.isoformat(),
                },
                "tool_invocations": [],
                "tool_results": [],
                "new_evidence_refs": [],
                "coverage": coverage,
                "started_at": NOW.isoformat(),
                "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
            }
        ],
        "evidence_candidates": candidates,
        "final_coverage": coverage,
        "causal_case": None,
        "horizons": horizons,
        "stop_reason": {
            "code": "sufficient",
            "detail": "model self-report",
            "bounded": False,
            "remaining_hard_gaps": [],
        },
        "total_tool_calls": 1,
        "total_subagents": 0,
        "total_tokens": 1,
        "estimated_cost_usd": 0.01,
        "started_at": NOW.isoformat(),
        "finished_at": (NOW + timedelta(seconds=1)).isoformat(),
    }


def _candidate(
    *,
    published_at: str | None = None,
    round: int = 1,
    evidence_id: str = "evidence-official",
    authority: str = "official",
    kind: str = "official",
    source_id: str = "official-feed",
) -> dict[str, Any]:
    excerpt = "Official event transcript."
    published = (
        datetime.fromisoformat(published_at)
        if published_at is not None
        else NOW - timedelta(seconds=4)
    )
    content_hash = research_evidence_content_hash(
        requirement_id="event_identity",
        kind="official",
        authority=authority,
        source_id=source_id,
        source_url="https://www.federalreserve.gov/",
        published_at=published,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return {
        "evidence_id": evidence_id,
        "requirement_id": "event_identity",
        "kind": kind,
        "authority": authority,
        "source_id": source_id,
        "source_url": "https://www.federalreserve.gov/",
        "published_at": published.isoformat(),
        "observed_at": (NOW - timedelta(seconds=3)).isoformat(),
        "received_at": (NOW - timedelta(seconds=2)).isoformat(),
        "content_hash": content_hash,
        "excerpt": excerpt,
        "structured_payload_ref": None,
        "tool_call_id": "tool-1",
        "research_session_id": "session-graph-1",
        "round": round,
        "quality": "candidate",
        "freshness_status": "unknown",
        "conflict_group": None,
    }


def test_attempt_lineage_uses_requirement_id_after_task_reordering() -> None:
    request = _request().model_copy(
        update={
            "evidence_requirements": [
                _request().evidence_requirements[0],
                _request().evidence_requirements[0].model_copy(
                    update={"requirement_id": "macro_transmission"}
                ),
            ]
        }
    )
    payload = _result(request, candidates=[], horizons=[])
    tasks = payload["rounds"][0]["plan"]["tasks"]
    tasks[:] = [
        {
            **tasks[0],
            "task_id": "macro",
            "requirement_id": "macro_transmission",
            "capability_id": "market.cross_asset",
        },
        {
            **tasks[0],
            "task_id": "event",
            "requirement_id": "event_identity",
            "capability_id": "official.macro",
        },
    ]
    result = ResearchSessionResult.model_validate(payload)

    assert attempts_from_result(result, request) == {
        "macro_transmission": ["market.cross_asset"],
        "event_identity": ["official.macro"],
    }


@pytest.mark.asyncio
async def test_graph_continues_after_missing_hard_evidence_and_freezes_snapshot(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    # Return real contract instances from the fake to exercise the graph boundary.
    harness = FakeResearchHarness(
        lambda request, count: ResearchSessionResult.model_validate(
            _result(
                request,
                candidates=[
                    _candidate(
                        authority="verified_web",
                        kind="web",
                        source_id="web-feed",
                        round=1,
                        evidence_id="evidence-stale",
                    )
                ]
                if count == 1
                else [_candidate(round=2)],
                horizons=[],
            )
        )
    )
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))
    result = await graph.ainvoke(initial_research_state(_request()))
    assert len(harness.requests) == 2
    assert harness.requests[1].current_round == 2
    assert harness.requests[1].target_gaps[0].requirement_id == "event_identity"
    assert result["final_result"]["final_coverage"]["status"] == "sufficient"
    assert result["decision_snapshot_id"].startswith("snap_dec_")
    assert result["final_result"]["stop_reason"]["code"] == "sufficient"


@pytest.mark.asyncio
async def test_next_generation_receives_only_the_remaining_tool_budget(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    def results(request: ResearchSessionRequest, count: int) -> ResearchSessionResult:
        payload = _result(
            request,
            candidates=(
                [
                    _candidate(
                        authority="verified_web",
                        kind="web",
                        source_id="web-feed",
                        round=1,
                        evidence_id="evidence-low-authority",
                    )
                ]
                if count == 1
                else [_candidate(round=2)]
            ),
            horizons=[],
        )
        payload["total_tool_calls"] = 3 if count == 1 else 1
        return ResearchSessionResult.model_validate(payload)

    harness = FakeResearchHarness(results)
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))
    result = await graph.ainvoke(initial_research_state(_request()))

    assert len(harness.requests) == 2
    assert harness.requests[0].execution_budget.max_tool_calls == 4
    assert harness.requests[1].execution_budget.max_tool_calls == 1
    assert result["total_tool_calls"] == 4


@pytest.mark.asyncio
async def test_retryable_continuation_failure_keeps_last_attested_round_as_degraded_report(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    request = _request()
    request = request.model_copy(
        update={
            "evidence_requirements": [
                request.evidence_requirements[0].model_copy(
                    update={"minimum_independent_sources": 2}
                )
            ]
        }
    )

    def first_round_then_timeout(
        round_request: ResearchSessionRequest, count: int
    ) -> ResearchSessionResult:
        if count == 1:
            return ResearchSessionResult.model_validate(
                _result(round_request, candidates=[_candidate()], horizons=[])
            )
        raise AgentExecutionError(
            "provider_timeout",
            "the continuation generation exceeded the product deadline",
            retryable=True,
            provider_id="dsh-web",
            origin="orchestration",
            cause_code="dsh_web_deadline_elapsed",
            deadline_ms=180_000,
        )

    harness = FakeResearchHarness(first_round_then_timeout)
    observability = ResearchObservabilityService(database, clock=lambda: NOW)
    graph = build_agentic_research_graph(
        harness,
        ResearchEvidenceService(database),
        trace_sink=observability,
        progress_reader=observability,
    )

    result = await graph.ainvoke(initial_research_state(request))

    assert len(harness.requests) == 2
    final = result["final_result"]
    assert final["status"] == "degraded"
    assert final["stop_reason"]["code"] == "critical_data_unavailable"
    assert "provider_timeout" in final["stop_reason"]["detail"]
    assert len(final["rounds"]) == 1
    assert [item["evidence_id"] for item in final["evidence_candidates"]] == [
        "evidence-official"
    ]
    assert final["total_tool_calls"] == 1
    failure = next(
        item
        for item in observability.list_trace(request.run_id)
        if item.event_type == "round_completed" and item.error_code == "provider_timeout"
    )
    assert failure.status == "degraded"
    assert failure.error is not None
    assert failure.error.cause_code == "dsh_web_deadline_elapsed"


@pytest.mark.asyncio
async def test_graph_bounded_stop_does_not_accept_model_sufficiency_without_evidence(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    harness = FakeResearchHarness(
        lambda request, count: ResearchSessionResult.model_validate(
            _result(request, candidates=[], horizons=[])
        )
    )
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))
    result = await graph.ainvoke(initial_research_state(_request()))
    assert len(harness.requests) == 1
    assert result["final_result"]["status"] == "degraded"
    assert result["final_result"]["stop_reason"]["code"] == "critical_data_unavailable"
    assert result["final_result"]["final_coverage"]["status"] == "insufficient"


@pytest.mark.asyncio
async def test_graph_never_promotes_evidence_only_fallback_even_when_coverage_is_sufficient(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    def evidence_only(request: ResearchSessionRequest, _count: int) -> ResearchSessionResult:
        payload = _result(request, candidates=[_candidate()], horizons=[])
        payload["synthesis_failure_code"] = "dsh_evidence_unattested"
        payload["status"] = "degraded"
        payload["causal_case"] = None
        payload["stop_reason"] = {
            "code": "critical_data_unavailable",
            "detail": "Synthesis was rejected at evidence attestation.",
            "bounded": True,
            "remaining_hard_gaps": [],
        }
        return ResearchSessionResult.model_validate(payload)

    graph = build_agentic_research_graph(
        FakeResearchHarness(evidence_only), ResearchEvidenceService(database)
    )
    result = await graph.ainvoke(initial_research_state(_request()))

    final = result["final_result"]
    assert final["status"] == "degraded"
    assert final["synthesis_failure_code"] == "dsh_evidence_unattested"
    assert final["stop_reason"]["code"] == "critical_data_unavailable"
    assert final["horizons"] == []


@pytest.mark.asyncio
async def test_graph_decision_cutoff_includes_evidence_acquired_after_trigger(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    def acquired_after_trigger(
        request: ResearchSessionRequest, _count: int
    ) -> ResearchSessionResult:
        candidate = _candidate()
        candidate["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
        candidate["received_at"] = (NOW + timedelta(seconds=2)).isoformat()
        payload = _result(request, candidates=[candidate], horizons=[])
        payload["rounds"][0]["finished_at"] = (NOW + timedelta(seconds=3)).isoformat()
        payload["finished_at"] = (NOW + timedelta(seconds=3)).isoformat()
        return ResearchSessionResult.model_validate(payload)

    harness = FakeResearchHarness(acquired_after_trigger)
    live_deadline = NOW + timedelta(minutes=3)
    request = _request(
        execution_mode="live",
        pit_cutoff_at=live_deadline,
        deadline_at=live_deadline,
    )
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))

    result = await graph.ainvoke(initial_research_state(request))

    assert result["final_result"]["final_coverage"]["status"] == "sufficient"
    with database.session() as session:
        decision = session.get(SnapshotRecord, result["decision_snapshot_id"])
        assert decision is not None
        assert decision.cutoff_at == (NOW + timedelta(seconds=3)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_replay_decision_cutoff_stays_at_historical_pit_cutoff(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    completed_at = NOW + timedelta(days=1)

    def completed_during_current_execution(
        request: ResearchSessionRequest, _count: int
    ) -> ResearchSessionResult:
        payload = _result(request, candidates=[_candidate()], horizons=[])
        payload["rounds"][0]["started_at"] = completed_at.isoformat()
        payload["rounds"][0]["finished_at"] = completed_at.isoformat()
        payload["started_at"] = completed_at.isoformat()
        payload["finished_at"] = completed_at.isoformat()
        return ResearchSessionResult.model_validate(payload)

    request = _request(deadline_at=completed_at + timedelta(minutes=3))
    harness = FakeResearchHarness(completed_during_current_execution)
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))

    result = await graph.ainvoke(initial_research_state(request))

    with database.session() as session:
        decision = session.get(SnapshotRecord, result["decision_snapshot_id"])
        assert decision is not None
        assert decision.cutoff_at == NOW.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_replay_horizons_use_historical_pit_cutoff_not_execution_time(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    completed_at = NOW + timedelta(days=365)
    horizon_specs = (
        ("30m", timedelta(minutes=30), timedelta(minutes=5)),
        ("24h", timedelta(hours=24), timedelta(hours=2)),
        ("72h", timedelta(hours=72), timedelta(hours=24)),
    )
    horizons = [
        {
            "horizon": horizon,
            "action": "no_trade",
            "subjective_probability": 0.5,
            "probability_status": "uncalibrated",
            "evidence_refs": ["trigger-evidence"],
            "trigger": f"{horizon} requires independent confirmation.",
            "invalidation": f"{horizon} is invalidated by contrary evidence.",
            "expires_at": (NOW + expiry).isoformat(),
            "next_review_at": (NOW + review).isoformat(),
            "missing_facts": ["event_identity"],
            "confidence_cap_reason": "Hard evidence remains open.",
        }
        for horizon, expiry, review in horizon_specs
    ]

    def completed_during_current_execution(
        request: ResearchSessionRequest, _count: int
    ) -> ResearchSessionResult:
        payload = _result(request, candidates=[], horizons=horizons)
        payload["rounds"][0]["started_at"] = completed_at.isoformat()
        payload["rounds"][0]["finished_at"] = completed_at.isoformat()
        payload["started_at"] = completed_at.isoformat()
        payload["finished_at"] = completed_at.isoformat()
        return ResearchSessionResult.model_validate(payload)

    request = _request(deadline_at=completed_at + timedelta(minutes=3))
    harness = FakeResearchHarness(completed_during_current_execution)
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))

    result = await graph.ainvoke(initial_research_state(request))

    assert [item["horizon"] for item in result["final_result"]["horizons"]] == [
        "30m",
        "24h",
        "72h",
    ]


@pytest.mark.asyncio
async def test_replay_rejects_evidence_received_after_historical_cutoff(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    def future_evidence(
        request: ResearchSessionRequest, _count: int
    ) -> ResearchSessionResult:
        candidate = _candidate()
        candidate["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
        candidate["received_at"] = (NOW + timedelta(seconds=2)).isoformat()
        return ResearchSessionResult.model_validate(
            _result(request, candidates=[candidate], horizons=[])
        )

    harness = FakeResearchHarness(future_evidence)
    graph = build_agentic_research_graph(harness, ResearchEvidenceService(database))

    with pytest.raises(ResearchCapabilityError) as raised:
        await graph.ainvoke(initial_research_state(_request()))
    assert raised.value.error_code == "research_pit_violation"
