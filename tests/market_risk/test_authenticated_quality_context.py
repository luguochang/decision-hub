"""Pure quality regressions; actual provider authentication is tested separately."""

from copy import deepcopy

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchReview
from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
    _require_preexecution_context,
)
from packages.kernel.decision_hub_kernel.application.research_quality_projection import (
    QualityProjectionContextV1,
    quality_input_from_state,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import (
    ResearchChallengeV1,
    assess_research_quality,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from tests.market_risk.test_quality_projection_v3 import material
from tests.market_risk.test_recipe_selection_v3 import baseline
from tests.market_risk.test_research_quality import challenge, full_input


def test_old_context_and_challenge_payloads_keep_their_wire_hash():
    context = baseline().model_dump(mode="json")
    issue = challenge().model_dump(mode="json")
    assert "authenticated_review_ids" not in context
    assert "authenticated_retained_context_refs" not in context
    assert "effect_on_report" not in issue
    assert QualityProjectionContextV1.model_validate(context).model_dump(mode="json") == context
    assert ResearchChallengeV1.model_validate(issue).model_dump(mode="json") == issue
    assert canonical_hash(context) == canonical_hash(
        QualityProjectionContextV1.model_validate(context).model_dump(mode="json")
    )


@pytest.mark.parametrize(
    "field", ["authenticated_review_ids", "authenticated_retained_context_refs"]
)
def test_creation_cannot_claim_future_review_or_delivered_body(field):
    with pytest.raises(ValueError, match="preexecution_claim_forbidden"):
        _require_preexecution_context(baseline().model_copy(update={field: ("future-proof",)}))


def test_monitor_effect_does_not_resolve_a_report_challenge():
    value = full_input()
    resolved = challenge(
        blocking_scope=("report",),
        status="answered",
        disposition="accepted",
        resolved_result_hash=value.source_result_hash,
        resolution_evidence_refs=("fact:liquidity",),
        effect_on_monitor="increased",
        reason="调整监控不能证明报告已修正",
        reviewer_version="independent-review.v2",
    )
    blocked = assess_research_quality(value.model_copy(update={"challenges": (resolved,)}))
    assert blocked.status == "unresolved" and blocked.direction_status == "blocked"
    with_report = resolved.model_copy(update={"effect_on_report": "reduced"})
    eligible = assess_research_quality(value.model_copy(update={"challenges": (with_report,)}))
    assert eligible.status == "sufficient"


def test_dynamic_body_proof_counts_only_for_the_exact_bound_manifest_and_result():
    _, result, state, context = material()
    manifest = result.context_manifest.model_copy(
        update={"effective_inputs": ["numeric-risk", "latest-event", "boundary"]}
    )
    result = result.model_copy(update={"context_manifest": manifest})
    state["latest_result"] = result.model_dump(mode="json")
    context = context.model_copy(
        update={
            "bound_result_hash": canonical_hash(result.model_dump(mode="json")),
            "selection_manifest_hash": canonical_hash(manifest.model_dump(mode="json")),
        }
    )
    assert (
        assess_research_quality(quality_input_from_state(state, context=context)).context_status
        == "incomplete"
    )
    delivered = context.model_copy(
        update={"authenticated_retained_context_refs": ("evidence-official",)}
    )
    verified = assess_research_quality(quality_input_from_state(state, context=delivered))
    assert verified.context_status == "complete" and verified.status == "partial"
    for changes in (
        {"bound_result_hash": "f" * 64},
        {"selection_manifest_hash": "e" * 64},
        {"context_fidelity_verified": False},
    ):
        value = quality_input_from_state(state, context=delivered.model_copy(update=changes))
        assert "evidence-official" not in value.retained_context_refs
        assert assess_research_quality(value).context_status == "incomplete"
    assert verified.path_risk_status == "unknown"


def test_authenticated_empty_issue_review_and_missing_original_are_distinct():
    _, result, state, context = material()
    review = ResearchReview(
        review_id="server-public-review",
        role="independent_reviewer",
        target_claim="结论与反方",
        evidence_refs=["evidence-official"],
        result_hash="c" * 64,
        challenge="本次结构审查无新增问题",
        resolution="accepted",
        limitations=[],
        created_at=result.finished_at,
    )
    result = result.model_copy(update={"reviews": [review]})
    state["latest_result"] = result.model_dump(mode="json")
    context = context.model_copy(
        update={"bound_result_hash": canonical_hash(result.model_dump(mode="json"))}
    )
    missing = quality_input_from_state(state, context=context)
    assert missing.challenges and assess_research_quality(missing).status == "unresolved"
    proven = context.model_copy(update={"authenticated_review_ids": (review.review_id,)})
    accepted = quality_input_from_state(state, context=proven)
    assert not accepted.challenges and assess_research_quality(accepted).status == "partial"
    stale = proven.model_copy(update={"bound_result_hash": "f" * 64})
    assert quality_input_from_state(deepcopy(state), context=stale).challenges
