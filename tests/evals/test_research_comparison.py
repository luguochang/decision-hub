from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import (
    CausalCase,
    CausalLink,
    ExecutionBudget,
    HorizonDecision,
    ResearchPlan,
    ResearchRound,
    ResearchSessionRequest,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchTask,
)
from packages.evals.research_comparison import (
    ResearchComparisonRunner,
    _manual_rework_flags,
    _request_for,
    evidence_for_scoring,
    facts_for_scoring,
)
from packages.evals.research_dataset import load_research_evaluation_dataset
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchTraceSink
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.fixed_research_runtime import FixedResearchRuntime

DATASET = Path("packs/crypto_macro/evaluations/r2r_pit_v1/manifest.json")
BUDGET = ExecutionBudget(
    max_evidence_rounds=3,
    max_tool_calls=12,
    max_subagents=6,
    total_deadline_seconds=30,
    per_tool_timeout_seconds=20,
    per_model_step_timeout_seconds=20,
    max_structured_repairs=1,
    max_estimated_cost_usd=1.0,
)


class CandidateRuntimeFixture:
    runtime_id = "candidate-test"
    runtime_version = "candidate-test.v1"
    profile_ref = "candidate-test-profile.v1"

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del trace_sink
        template = next(
            item.evidence_candidates[0]
            for item in _DATASET_BY_ID[request.event_id].archived_capability_fixtures
            if item.requirement_id == "event_identity"
        )
        session_id = f"candidate:{request.request_id}"
        evidence = template.model_copy(
            update={
                "evidence_id": research_evidence_instance_id(
                    content_hash=template.content_hash,
                    research_session_id=session_id,
                ),
                "tool_call_id": f"tool:{request.request_id}",
                "research_session_id": session_id,
                # This fixture models evidence already admitted by the Kernel;
                # raw replay candidates are tested separately as unadmitted.
                "quality": "accepted",
                "freshness_status": "fresh",
            }
        )
        coverage = assess_evidence_sufficiency(
            request.evidence_requirements,
            [evidence],
            cutoff_at=_DATASET_BY_ID[request.event_id].cutoff_at,
        )
        now = datetime.now(UTC)
        plan = ResearchPlan(
            plan_id=f"plan:{request.request_id}",
            objective="Acquire one archived source.",
            tasks=[
                ResearchTask(
                    task_id="task:event-identity",
                    requirement_id="event_identity",
                    capability_id="replay.research",
                    objective="Verify event identity.",
                    question="What is the event identity?",
                    input_evidence_refs=request.evidence_refs,
                    output_schema_ref="evidence-candidate.v1",
                    depends_on=[],
                    success_condition="One official source is returned.",
                    priority=1,
                )
            ],
            required_capabilities=["replay.research"],
            created_at=now,
        )
        research_round = ResearchRound(
            round=1,
            plan=plan,
            tool_invocations=[],
            tool_results=[],
            new_evidence_refs=[evidence.evidence_id],
            coverage=coverage,
            started_at=now,
            finished_at=now + timedelta(milliseconds=1),
        )
        causal = CausalCase(
            case_id=f"case:{request.request_id}",
            thesis="An official event exists, but market confirmation remains incomplete.",
            main_chain=[
                CausalLink(
                    link_id="main:1",
                    claim_type="fact",
                    statement="The archived official source verifies the event.",
                    evidence_refs=[evidence.evidence_id],
                    confirmation="A second source confirms the policy delta.",
                    invalidation="The source is withdrawn or corrected.",
                    affected_horizons=["30m", "24h", "72h"],
                )
            ],
            opposite_chain=[
                CausalLink(
                    link_id="counter:1",
                    claim_type="scenario",
                    statement="The event may already be priced in.",
                    evidence_refs=[evidence.evidence_id],
                    confirmation="Price rejects the event direction.",
                    invalidation="Independent market evidence confirms repricing.",
                    affected_horizons=["30m", "24h", "72h"],
                )
            ],
            unresolved_questions=["Market confirmation remains unavailable."],
            evidence_refs=[evidence.evidence_id],
        )
        horizon_specs: tuple[
            tuple[Literal["30m", "24h", "72h"], float, timedelta, timedelta], ...
        ] = (
            ("30m", 0.50, timedelta(minutes=30), timedelta(minutes=10)),
            ("24h", 0.51, timedelta(hours=24), timedelta(hours=6)),
            ("72h", 0.52, timedelta(hours=72), timedelta(hours=24)),
        )
        horizons = [
            HorizonDecision(
                horizon=horizon,
                action="no_trade",
                subjective_probability=probability,
                probability_status="uncalibrated",
                evidence_refs=[evidence.evidence_id],
                trigger=f"{horizon} requires its own market confirmation.",
                invalidation=f"{horizon} event thesis is invalidated by opposite repricing.",
                expires_at=now + delay,
                next_review_at=now + review,
                missing_facts=[gap.requirement_id for gap in coverage.gaps],
                confidence_cap_reason="Open hard evidence gaps require research_only.",
            )
            for horizon, probability, delay, review in horizon_specs
        ]
        hard_gaps = [item.requirement_id for item in coverage.gaps if item.importance == "hard"]
        return ResearchSessionResult(
            schema_version="research-session-result.v1",
            request_id=request.request_id,
            research_session_id=session_id,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            profile_ref=self.profile_ref,
            trace_ref=f"candidate://{request.request_id}",
            trace_hash="1" * 64,
            status="degraded",
            rounds=[research_round],
            evidence_candidates=[evidence],
            final_coverage=coverage,
            causal_case=causal,
            horizons=horizons,
            stop_reason=ResearchStopReason(
                code="critical_data_unavailable",
                detail="Only one archived requirement was available.",
                bounded=True,
                remaining_hard_gaps=hard_gaps,
            ),
            total_tool_calls=1,
            total_subagents=0,
            total_tokens=100,
            estimated_cost_usd=0.01,
            started_at=now,
            finished_at=now + timedelta(milliseconds=1),
        )

    async def close(self) -> None:
        return None


class _FailingRuntime(CandidateRuntimeFixture):
    runtime_id = "candidate-failing"
    runtime_version = "candidate-failing.v1"

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        del request, trace_sink
        raise AgentExecutionError("fixture_failure", "intentional comparison failure")


_DATASET = load_research_evaluation_dataset(DATASET)
_DATASET_BY_ID = {case.case_id: case for case in _DATASET.cases}


def test_scoring_keeps_typed_expectation_baseline_without_freshening_old_snapshots() -> None:
    dataset = load_research_evaluation_dataset(
        Path("packs/crypto_macro/evaluations/r2r_dev_core_v2/manifest.json")
    )
    case = next(item for item in dataset.cases if item.case_id == "fomc_cut_25bp_20241218")
    fixture = next(
        item
        for item in case.archived_capability_fixtures
        if item.requirement_id == "expectation_pricing"
    )
    template = fixture.evidence_candidates[0]
    baseline_id = f"{template.evidence_id}:baseline"
    current_id = f"{template.evidence_id}:t-5m"
    baseline_at = case.cutoff_at - timedelta(hours=24)
    current_at = case.cutoff_at - timedelta(minutes=5)
    baseline = template.model_copy(
        update={
            "evidence_id": baseline_id,
            "published_at": baseline_at,
            "observed_at": baseline_at,
            "received_at": case.cutoff_at,
            "quality": "accepted",
        }
    )
    current = template.model_copy(
        update={
            "evidence_id": current_id,
            "published_at": current_at,
            "observed_at": current_at,
            "received_at": case.cutoff_at,
            "quality": "accepted",
        }
    )
    facts = [
        fact.model_copy(
            update={
                "evidence_id": baseline_id if fact.event_offset == "baseline" else current_id,
                "published_at": baseline_at
                if fact.event_offset == "baseline"
                else current_at,
                "observed_at": baseline_at
                if fact.event_offset == "baseline"
                else current_at,
                "received_at": case.cutoff_at,
                "quality": "accepted",
            }
        )
        for fact in (fixture.facts or [])
    ]
    expectation = next(
        item
        for item in case.evidence_requirements
        if item.requirement_id == "expectation_pricing"
    )

    scored = evidence_for_scoring(case, [baseline, current], facts)
    assert {item.evidence_id: item.quality for item in scored} == {
        baseline_id: "accepted",
        current_id: "accepted",
    }
    coverage = assess_evidence_sufficiency(
        [expectation],
        scored,
        cutoff_at=case.cutoff_at,
        facts=facts_for_scoring(facts, scored),
    )
    assert coverage.status == "sufficient"

    unlabeled = evidence_for_scoring(case, [baseline])
    assert unlabeled[0].quality == "stale"


@pytest.mark.asyncio
async def test_dsh_quality_requires_a_trusted_independent_review_receipt() -> None:
    case = _DATASET.cases[0]
    request = _request_for(case, "review-gate", "candidate-test", BUDGET)
    result = await CandidateRuntimeFixture().execute(request)
    result = result.model_copy(
        update={
            "runtime_id": "dsh",
            "status": "completed",
            "reviews": [],
            "stop_reason": ResearchStopReason(
                code="sufficient",
                detail="All required evidence is available.",
                bounded=False,
                remaining_hard_gaps=[],
            ),
        }
    )

    flags = _manual_rework_flags(
        result=result,
        status="completed",
        failure_code=None,
        hard_gap_count=0,
        pit_violations=0,
        citation_support_ratio=1.0,
        horizon_distinct=True,
    )

    assert flags == ["independent_review_receipt"]


@pytest.mark.asyncio
async def test_runner_writes_all_raw_reports_before_aggregate(tmp_path: Path) -> None:
    comparison = await ResearchComparisonRunner(
        _DATASET, budget=BUDGET, output_dir=tmp_path
    ).run(
        experiment_id="r2-r-06c-offline",
        baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=CandidateRuntimeFixture(),
    )

    assert len(comparison.case_reports) == 24
    assert len(list(tmp_path.glob("*/*.json"))) == 24
    assert (tmp_path / "comparison.json").is_file()
    summaries = {item.runtime_id: item for item in comparison.summaries}
    assert summaries["fixed-baseline"].sample_count == 12
    assert summaries["candidate-test"].sample_count == 12
    assert summaries["candidate-test"].hard_coverage_mean > (
        summaries["fixed-baseline"].hard_coverage_mean
    )
    # v3 accepts its three different time boundaries. This does not certify
    # independent causal views or turn its degraded research into usable work.
    assert summaries["fixed-baseline"].horizon_distinct_count == 12
    assert summaries["candidate-test"].horizon_distinct_count == 12
    assert summaries["candidate-test"].quality_success_count == 0
    assert summaries["candidate-test"].manual_rework_count == 12
    assert all(
        not item.quality_success
        and item.manual_rework_flags
        for item in comparison.case_reports
        if item.runtime_id == "candidate-test"
    )
    assert comparison.conclusion == "retain_baseline"


@pytest.mark.asyncio
async def test_runner_keeps_every_failed_candidate_case(tmp_path: Path) -> None:
    comparison = await ResearchComparisonRunner(
        _DATASET, budget=BUDGET, output_dir=tmp_path
    ).run(
        experiment_id="r2-r-06c-failure",
        baseline=FixedResearchRuntime(FakeAgentRuntime()),
        candidate=_FailingRuntime(),
    )

    summary = next(
        item for item in comparison.summaries if item.runtime_id == "candidate-failing"
    )
    assert summary.sample_count == 12
    assert summary.failed_count == 12
    assert summary.failure_counts == {"fixture_failure": 12}
    assert len(
        [item for item in comparison.case_reports if item.runtime_id == "candidate-failing"]
    ) == 12
