from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import (
    CausalCase,
    CausalLink,
    CoverageAssessment,
    EvidenceCandidate,
    EvidenceRequirement,
    HorizonDecision,
    ResearchInputEvidence,
    ResearchPlan,
    ResearchReview,
    ResearchRound,
    ResearchSessionResult,
    ResearchStopReason,
    ResearchTask,
)
from packages.contracts_py.decision_hub_contracts.models import ObservationCreate, SourceType
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.research_context import (
    build_task_policy,
    project_excerpt,
    select_context,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    ResearchReviewService,
    has_valid_accepted_independent_review,
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _requirement() -> EvidenceRequirement:
    return EvidenceRequirement(
        requirement_id="policy_delta",
        description="Verify the official policy decision and inflation risks.",
        importance="hard",
        source_priority=["official"],
        authority_floor="official",
        preferred_capabilities=["official.macro"],
        freshness_seconds=86_400,
        minimum_independent_sources=1,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )


def _input(
    evidence_id: str,
    text: str,
    authority: Literal[
        "official",
        "exchange",
        "audited_aggregator",
        "verified_web",
        "search_derived",
        "unverified",
    ] = "official",
) -> ResearchInputEvidence:
    return ResearchInputEvidence(
        evidence_id=evidence_id,
        kind="official",
        authority=authority,
        source_id=evidence_id,
        source_url=None,
        published_at=NOW - timedelta(minutes=1),
        observed_at=NOW - timedelta(minutes=1),
        received_at=NOW,
        content_hash="a" * 64,
        excerpt=text,
    )


def test_context_manifest_selects_relevant_middle_and_records_exclusions() -> None:
    long_text = (
        "header " + ("background " * 500) + " policy decision inflation risks " + ("tail " * 500)
    )
    projected = project_excerpt(long_text, ("policy", "inflation"), 4000)
    initial = [_input("trigger", projected)]
    policy = build_task_policy(
        research_mode="current_decision",
        requirements=[_requirement()],
        created_at=NOW,
        max_evidence_rounds=3,
    )
    selected, manifest = select_context(
        initial,
        [_candidate(f"extra-{index}", "unrelated evidence") for index in range(15)],
        requirements=[_requirement()],
        policy=policy,
        manifest_prefix="context:run-1",
        revision=1,
        created_at=NOW,
        allowed_capabilities=["official.macro"],
    )
    assert policy.max_parallel_tasks == 2
    assert manifest.policy_ref.endswith(policy.content_hash[:16])
    assert manifest.input_fingerprint
    assert selected[0].evidence_id == "trigger"
    assert "policy decision" in selected[0].excerpt
    assert manifest.excluded_inputs


def test_review_is_structured_and_pinned_to_result_hash(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'review.sqlite3'}")
    database.create_all()
    evidence = _candidate("evidence-1", "Official policy decision.")
    coverage = CoverageAssessment(
        status="sufficient",
        covered_requirement_ids=["policy_delta"],
        gaps=[],
        conflicts=[],
        hard_coverage_ratio=1.0,
        soft_coverage_ratio=1.0,
        assessed_at=NOW,
    )
    now = NOW
    plan = ResearchPlan(
        plan_id="plan-1",
        objective="Verify policy",
        tasks=[
            ResearchTask(
                task_id="task-1",
                requirement_id="policy_delta",
                capability_id="official.macro",
                objective="Verify policy",
                question="What changed?",
                input_evidence_refs=[evidence.evidence_id],
                output_schema_ref="research-capability-result.v1",
                depends_on=[],
                success_condition="One official source",
                priority=1,
            )
        ],
        required_capabilities=["official.macro"],
        created_at=now,
    )
    result = ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id="request-1",
        research_session_id="session-1",
        runtime_id="fake",
        runtime_version="fake.v1",
        profile_ref="profile.v1",
        trace_ref="trace://1",
        trace_hash="b" * 64,
        status="completed",
        rounds=[
            ResearchRound(
                round=1,
                plan=plan,
                tool_invocations=[],
                tool_results=[],
                new_evidence_refs=[evidence.evidence_id],
                coverage=coverage,
                started_at=now,
                finished_at=now,
            )
        ],
        evidence_candidates=[evidence],
        facts=[],
        final_coverage=coverage,
        causal_case=CausalCase(
            case_id="case-1",
            thesis="Policy is restrictive.",
            main_chain=[
                CausalLink(
                    link_id="main-1",
                    claim_type="fact",
                    statement="Policy is restrictive.",
                    evidence_refs=[evidence.evidence_id],
                    confirmation="Rates remain high.",
                    invalidation="Official correction.",
                    affected_horizons=["24h"],
                )
            ],
            opposite_chain=[
                CausalLink(
                    link_id="opp-1",
                    claim_type="scenario",
                    statement="Policy may be priced.",
                    evidence_refs=[evidence.evidence_id],
                    confirmation="Price reverses.",
                    invalidation="Price confirms.",
                    affected_horizons=["24h"],
                )
            ],
            unresolved_questions=[],
            evidence_refs=[evidence.evidence_id],
        ),
        horizons=[
            HorizonDecision(
                horizon=h,
                action="neutral",
                subjective_probability=0.5,
                probability_status="uncalibrated",
                evidence_refs=[evidence.evidence_id],
                trigger="policy",
                invalidation="correction",
                expires_at=now + timedelta(hours=1),
                next_review_at=now + timedelta(minutes=10),
                missing_facts=[],
                confidence_cap_reason=None,
            )
            for h in ("30m", "24h", "72h")
        ],
        stop_reason=ResearchStopReason(
            code="sufficient", detail="sufficient", bounded=False, remaining_hard_gaps=[]
        ),
        total_tool_calls=1,
        total_subagents=1,
        total_tokens=None,
        estimated_cost_usd=None,
        started_at=now,
        finished_at=now,
    )
    service = ResearchReviewService(database)
    review = service.review("run-1", result)
    same = service.review("run-1", result)
    assert review.review_id == same.review_id
    assert review.role == "independent_reviewer"
    assert review.result_hash != "b" * 64
    assert review.evidence_refs == [evidence.evidence_id]
    assert database.find_role_output("run-1", "independent_reviewer") is not None
    with pytest.raises(ValueError, match="research_review_result_hash_mismatch"):
        service.review("run-1", result.model_copy(update={"request_id": "request-2"}))

    native_hash = independent_review_receipt_hash(
        role="independent_reviewer",
        target_claim="Policy is restrictive.",
        evidence_refs=[evidence.evidence_id],
        challenge="The event reaction may already have been priced.",
        resolution="bounded",
        limitations=["No independent rates window was available."],
    )
    native = ResearchReview(
        review_id=f"review_{native_hash[:32]}",
        role="independent_reviewer",
        target_claim="Policy is restrictive.",
        evidence_refs=[evidence.evidence_id],
        result_hash=research_result_content_hash(result),
        challenge="The event reaction may already have been priced.",
        resolution="bounded",
        limitations=["No independent rates window was available."],
        created_at=NOW,
    )
    native_result = result.model_copy(update={"reviews": [native]})
    preserved = service.review("run-native", native_result)
    assert preserved == native
    stored = database.find_role_output("run-native", "independent_reviewer")
    assert stored is not None
    assert ResearchReview.model_validate(stored[1]) == native
    stale_base = result.model_copy(
        update={
            "evidence_candidates": [
                item.model_copy(update={"quality": "stale"})
                for item in result.evidence_candidates
            ],
            "reviews": [],
        }
    )
    stale_result = stale_base.model_copy(
        update={
            "reviews": [
                native.model_copy(update={"result_hash": research_result_content_hash(stale_base)})
            ]
        }
    )
    assert has_valid_accepted_independent_review(stale_result) is False
    with pytest.raises(ValueError, match="research_native_review_hash_mismatch"):
        service.review(
            "run-native-invalid",
            native_result.model_copy(
                update={"reviews": [native.model_copy(update={"result_hash": "d" * 64})]}
            ),
        )
    with pytest.raises(ValueError, match="research_native_review_hash_mismatch"):
        service.review(
            "run-native-other-result",
            native_result.model_copy(update={"request_id": "request-2"}),
        )
    mismatched_target = native.model_copy(
        update={
            "target_claim": "A different final thesis.",
            "resolution": "accepted",
        }
    )
    mismatched_target_result = result.model_copy(update={"reviews": [mismatched_target]})
    with pytest.raises(ValueError, match="research_native_review_target_mismatch"):
        service.review("run-native-other-target", mismatched_target_result)


def test_review_accepts_trigger_snapshot_evidence_refs(tmp_path: Path) -> None:
    """A reviewer may cite the immutable question/trigger Evidence."""

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'trigger-review.sqlite3'}")
    database.create_all()
    event_id, _envelope, admitted = AdmissionService(database, clock=lambda: NOW).admit(
        ObservationCreate(
            text="Verify the current BTC market snapshot.",
            source_id="decision-desk",
            source_type=SourceType.manual,
            observed_at=NOW,
            language="en",
        )
    )
    assert admitted
    run_id, created = RunService(database).create(event_id, strategy_version="research.v1")
    assert created
    from packages.kernel.decision_hub_kernel.application.snapshot import SnapshotService

    snapshot_id, _ = SnapshotService(database).freeze_for_run(run_id, event_id)
    trigger_id = str(database.get_snapshot_evidence(snapshot_id)[0]["evidence_id"])

    evidence = _candidate("evidence-1", "Official policy decision.")
    # Rebuild the minimal result used above with a claim backed by the trigger
    # snapshot rather than a capability result.
    result = _review_result(evidence)
    assert result.causal_case is not None
    causal_case = result.causal_case.model_copy(
        update={
            "evidence_refs": [trigger_id],
            "main_chain": [
                result.causal_case.main_chain[0].model_copy(update={"evidence_refs": [trigger_id]})
            ],
            "opposite_chain": [
                result.causal_case.opposite_chain[0].model_copy(
                    update={"evidence_refs": [trigger_id]}
                )
            ],
        }
    )
    reviewed = ResearchReviewService(database).review(
        run_id, result.model_copy(update={"causal_case": causal_case})
    )
    assert reviewed.resolution == "bounded"
    assert "Automated evidence check only" in reviewed.limitations[0]
    assert reviewed.evidence_refs == [trigger_id]


def _candidate(evidence_id: str, excerpt: str) -> EvidenceCandidate:
    return EvidenceCandidate(
        evidence_id=evidence_id,
        requirement_id="policy_delta",
        kind="official",
        authority="official",
        source_id=evidence_id,
        source_url=None,
        published_at=NOW - timedelta(minutes=1),
        observed_at=NOW - timedelta(minutes=1),
        received_at=NOW,
        content_hash="c" * 64,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id=None,
        research_session_id="session-1",
        round=1,
        quality="accepted",
        freshness_status="fresh",
        conflict_group=None,
    )


def _review_result(evidence: EvidenceCandidate) -> ResearchSessionResult:
    coverage = CoverageAssessment(
        status="sufficient",
        covered_requirement_ids=["policy_delta"],
        gaps=[],
        conflicts=[],
        hard_coverage_ratio=1.0,
        soft_coverage_ratio=1.0,
        assessed_at=NOW,
    )
    plan = ResearchPlan(
        plan_id="plan-trigger-review",
        objective="Verify policy",
        tasks=[
            ResearchTask(
                task_id="task-trigger-review",
                requirement_id="policy_delta",
                capability_id="official.macro",
                objective="Verify policy",
                question="What changed?",
                input_evidence_refs=[evidence.evidence_id],
                output_schema_ref="research-capability-result.v1",
                depends_on=[],
                success_condition="One official source",
                priority=1,
            )
        ],
        required_capabilities=["official.macro"],
        created_at=NOW,
    )
    causal = CausalCase(
        case_id="case-trigger-review",
        thesis="Policy is restrictive.",
        main_chain=[
            CausalLink(
                link_id="main-trigger-review",
                claim_type="fact",
                statement="Policy is restrictive.",
                evidence_refs=[evidence.evidence_id],
                confirmation="Rates remain high.",
                invalidation="Official correction.",
                affected_horizons=["24h"],
            )
        ],
        opposite_chain=[
            CausalLink(
                link_id="opp-trigger-review",
                claim_type="scenario",
                statement="Policy may be priced.",
                evidence_refs=[evidence.evidence_id],
                confirmation="Price reverses.",
                invalidation="Price confirms.",
                affected_horizons=["24h"],
            )
        ],
        unresolved_questions=[],
        evidence_refs=[evidence.evidence_id],
    )
    horizons = [
        HorizonDecision(
            horizon=h,
            action="neutral",
            subjective_probability=0.5,
            probability_status="uncalibrated",
            evidence_refs=[evidence.evidence_id],
            trigger="policy",
            invalidation="correction",
            expires_at=NOW + timedelta(hours=1),
            next_review_at=NOW + timedelta(minutes=10),
            missing_facts=[],
            confidence_cap_reason=None,
        )
        for h in ("30m", "24h", "72h")
    ]
    return ResearchSessionResult(
        schema_version="research-session-result.v1",
        request_id="request-trigger-review",
        research_session_id="session-trigger-review",
        runtime_id="fake",
        runtime_version="fake.v1",
        profile_ref="profile.v1",
        trace_ref="trace://trigger-review",
        trace_hash="d" * 64,
        status="completed",
        rounds=[
            ResearchRound(
                round=1,
                plan=plan,
                tool_invocations=[],
                tool_results=[],
                new_evidence_refs=[evidence.evidence_id],
                coverage=coverage,
                started_at=NOW,
                finished_at=NOW,
            )
        ],
        evidence_candidates=[evidence],
        facts=[],
        final_coverage=coverage,
        causal_case=causal,
        horizons=horizons,
        stop_reason=ResearchStopReason(
            code="sufficient", detail="sufficient", bounded=False, remaining_hard_gaps=[]
        ),
        total_tool_calls=1,
        total_subagents=1,
        total_tokens=None,
        estimated_cost_usd=None,
        started_at=NOW,
        finished_at=NOW,
    )
