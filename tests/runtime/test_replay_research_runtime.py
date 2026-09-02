from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ExecutionBudget,
    ResearchInputEvidence,
    ResearchSessionRequest,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.replay_runtime import ReplayResearchRuntime

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "packs/crypto_macro/fixtures/research-worker-replay.json"
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _request(*, run_id: str = "run-replay-1", current_round: int = 1) -> ResearchSessionRequest:
    return ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id=f"research-request:{run_id}",
        run_id=run_id,
        event_id="event-replay-1",
        trigger_snapshot_id="snapshot-replay-1",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="replay",
        pit_cutoff_at=NOW,
        current_round=current_round,
        evidence_refs=["trigger-evidence-current"],
        input_evidence=[
            ResearchInputEvidence(
                evidence_id="trigger-evidence-current",
                kind="transcript",
                authority="unverified",
                source_id="manual",
                source_url=None,
                published_at=NOW - timedelta(minutes=1),
                observed_at=NOW - timedelta(seconds=1),
                received_at=NOW,
                content_hash="0" * 64,
                excerpt="The official discussed inflation risks.",
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
                freshness_seconds=604800,
                minimum_independent_sources=1,
                allowed_fallbacks=["replay.research"],
                confidence_cap=0.45,
            )
        ],
        target_gaps=[],
        allowed_capabilities=["replay.research"],
        execution_budget=ExecutionBudget(
            max_evidence_rounds=3,
            max_tool_calls=12,
            max_subagents=6,
            total_deadline_seconds=180,
            per_tool_timeout_seconds=20,
            per_model_step_timeout_seconds=60,
            max_structured_repairs=1,
            max_estimated_cost_usd=1.0,
        ),
        deadline_at=NOW + timedelta(minutes=3),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )


def test_replay_runtime_rebinds_identity_and_all_evidence_references() -> None:
    runtime = ReplayResearchRuntime.from_path(FIXTURE)

    assert all(
        result.total_tool_calls
        == sum(len(round_item.tool_invocations) for round_item in result.rounds)
        for result in runtime.fixture.results
    )

    first = asyncio.run(runtime.execute(_request()))
    retried = asyncio.run(runtime.execute(_request()))
    independent = asyncio.run(runtime.execute(_request(run_id="run-replay-2")))

    evidence = first.evidence_candidates[0]
    assert first.request_id == "research-request:run-replay-1"
    assert first.research_session_id == "replay:research-request:run-replay-1"
    assert first.runtime_id == "research-replay"
    assert first.rounds[0].plan.tasks[0].input_evidence_refs == [
        "trigger-evidence-current"
    ]
    assert first.causal_case is not None
    assert first.causal_case.evidence_refs == [evidence.evidence_id]
    assert all(item.evidence_refs == [evidence.evidence_id] for item in first.horizons)
    assert retried.evidence_candidates[0].evidence_id == evidence.evidence_id
    assert independent.evidence_candidates[0].evidence_id != evidence.evidence_id
    assert independent.evidence_candidates[0].content_hash == evidence.content_hash


def test_replay_runtime_can_explicitly_repeat_last_archived_result() -> None:
    runtime = ReplayResearchRuntime.from_path(FIXTURE)

    result = asyncio.run(runtime.execute(_request(current_round=2)))

    assert result.rounds[0].round == 2
    assert result.evidence_candidates[0].round == 2


def test_replay_runtime_fails_closed_for_missing_round_and_invalid_fixture(
    tmp_path: Path,
) -> None:
    runtime = ReplayResearchRuntime.from_path(FIXTURE)
    strict_runtime = ReplayResearchRuntime(
        runtime.fixture.model_copy(update={"repeat_last_result": False})
    )

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(strict_runtime.execute(_request(current_round=2)))
    assert raised.value.error_code == "research_replay_round_missing"

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="research_runtime_replay_fixture_invalid"):
        ReplayResearchRuntime.from_path(invalid)
