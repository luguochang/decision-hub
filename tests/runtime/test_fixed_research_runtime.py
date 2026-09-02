from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ExecutionBudget, ResearchSessionRequest
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime


@pytest.mark.asyncio
async def test_fixed_runtime_wraps_legacy_graph_without_tool_claims() -> None:
    case = load_research_evaluation_dataset(
        Path("packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json")
    ).cases[0]
    request = ResearchSessionRequest(
        schema_version="research-session-request.v1",
        request_id="fixed-eval-request",
        run_id="fixed-eval-run",
        event_id=case.case_id,
        trigger_snapshot_id="trigger-fixed",
        domain_pack_ref="crypto_macro.v1",
        role_profile_ref="crypto_macro.manager.v1",
        execution_mode="replay",
        pit_cutoff_at=case.cutoff_at,
        current_round=1,
        evidence_refs=[case.trigger_evidence.evidence_id],
        input_evidence=[case.trigger_evidence],
        evidence_requirements=case.evidence_requirements,
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
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
        output_schema_ref="research-session-result.v1",
        repair_instructions=None,
    )

    result = await FixedResearchRuntime(FakeAgentRuntime()).execute(request)

    assert result.runtime_id == "fixed-baseline"
    assert result.total_tool_calls == 0
    assert result.status == "degraded"
    assert result.stop_reason.code == "critical_data_unavailable"
    assert len(result.horizons) == 3
    assert result.evidence_candidates == []
