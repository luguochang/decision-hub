import json
from datetime import timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchReview, ResearchSessionResult
from packages.kernel.decision_hub_kernel.application import research_graph_projection as v1
from packages.kernel.decision_hub_kernel.application import research_graph_projection_v3 as v3
from packages.kernel.decision_hub_kernel.application.research_quality_projection import (
    QualityProjectionContextV1,
    quality_input_from_state,
)
from packages.kernel.decision_hub_kernel.decision.research_quality import ResearchChallengeV1
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def material():
    # Existing explicit synthetic DTO factories; no harness/graph/model executes.
    from tests.orchestration.test_agentic_research_graph import (
        NOW,
        _candidate,
        _causal_case,
        _horizons,
        _request,
        _result,
    )

    request = _request()
    candidate = _candidate()
    candidate["quality"] = "accepted"
    candidate["freshness_status"] = "fresh"
    horizons = _horizons()
    horizons[1]["action"], horizons[1]["subjective_probability"] = "long", 0.73
    body = _result(request, candidates=[candidate], horizons=horizons)
    body["causal_case"] = _causal_case(
        thesis="方向保留但尾部风险需要单独判断", unresolved_questions=["杠杆流动性未知"]
    )
    body["context_manifest"] = {
        "manifest_id": "fixture:manifest",
        "policy_ref": "fixture:protected",
        "revision": 1,
        "selected_inputs": ["evidence-official", "numeric-risk", "latest-event", "boundary"],
        "effective_inputs": ["evidence-official", "numeric-risk", "latest-event", "boundary"],
        "excluded_inputs": [],
        "rules_ref": "fixture:rules",
        "tool_versions": {},
        "data_versions": {},
        "input_fingerprint": "a" * 64,
        "created_at": NOW.isoformat(),
    }
    result = ResearchSessionResult.model_validate(body)
    state = v1.initial_research_state(request)
    state.update(
        {
            "latest_result": result.model_dump(mode="json"),
            "evidence": [candidate],
            "rounds": [item.model_dump(mode="json") for item in result.rounds],
            "facts": [],
            "current_round": 1,
            "total_tool_calls": 1,
            "total_subagents": 0,
            "total_tokens": 100,
            "estimated_cost_usd": 0.01,
            "coverage": {
                "status": "sufficient",
                "covered_requirement_ids": ["event_identity"],
                "gaps": [],
                "conflicts": [],
                "hard_coverage_ratio": 1,
                "soft_coverage_ratio": 1,
                "assessed_at": NOW.isoformat(),
            },
            "loop_should_continue": False,
        }
    )
    context = QualityProjectionContextV1(
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        bound_result_hash=canonical_hash(result.model_dump(mode="json")),
        runtime_attestation_ref="fixture:verified-result",
        effective_budget_ref="fixture:effective-budget",
        assessed_at=NOW + timedelta(seconds=2),
        selection_manifest_hash=canonical_hash(result.context_manifest.model_dump(mode="json")),
        additional_required_context_refs=("numeric-risk", "latest-event", "boundary"),
        context_fidelity_verified=True,
        monitor_state="active",
    )
    return request, result, state, context


def test_mapping_uses_actual_refs_budget_context_and_unknown_path():
    _, result, state, context = material()
    value = quality_input_from_state(state, context=context)
    assert value.budget_state == "available" and value.context_fidelity_verified
    assert value.factual_coverage_complete and value.direction_evidence_verified
    assert value.source_result_hash == context.bound_result_hash
    assert json.loads(value.full_report_json) == result.model_dump(mode="json")
    assert value.key_risks[0].statement == result.causal_case.opposite_chain[0].statement
    assert not value.path_risk_model_qualified
    quality = v3.quality_for_state(state, quality_context=context)
    assert quality.status == "partial" and quality.path_risk_status == "unknown"


@pytest.mark.parametrize(
    "missing", ["cost", "budget_ref", "attestation", "fidelity", "source_binding"]
)
def test_missing_actual_proof_cannot_be_inferred_from_completed_or_coverage(missing):
    _, _, state, context = material()
    if missing == "cost":
        state["estimated_cost_usd"] = None
    else:
        field, value = {
            "budget_ref": ("effective_budget_ref", None),
            "attestation": ("runtime_attestation_ref", None),
            "fidelity": ("context_fidelity_verified", False),
            "source_binding": ("bound_result_hash", "b" * 64),
        }[missing]
        context = context.model_copy(update={field: value})
    quality = v3.quality_for_state(state, quality_context=context)
    assert quality.status == "unresolved"
    assert quality.publication_mode == "research_only" and quality.blocks_completion


def test_critical_challenge_precedes_old_coverage_shortcut_and_finalization():
    request, _, state, context = material()
    critical = ResearchChallengeV1(
        challenge_id="liquidity",
        severity="critical",
        blocking_scope=("direction", "path_risk"),
        evidence_refs=("evidence-official",),
    )
    context = context.model_copy(update={"challenges": (critical,)})
    state["loop_should_continue"] = True
    state["loop_target_requirement_ids"] = ["event_identity"]
    assert v3.should_continue_after_round(state, quality_context=context)
    with pytest.raises(ValueError, match="finalized_before_continuation"):
        v3.project_final(state, quality_context=context)
    state["loop_should_continue"] = False
    quality = v3.quality_for_state(state, quality_context=context)
    code, _ = v3._stop_reason(
        state,
        result_coverage(state),
        request.execution_budget,
        quality_context=context,
    )
    assert code == "mixed" and quality.status == "unresolved"
    assert v3.project_final(state, quality_context=context).status == "degraded"


def result_coverage(state):
    from packages.contracts_py.decision_hub_contracts import CoverageAssessment

    return CoverageAssessment.model_validate(state["coverage"])


def test_finalizer_and_continuation_do_not_call_v1_global_decisions(monkeypatch):
    request, result, state, context = material()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V1 early-success path must not run")

    for name in (
        "project_final",
        "should_continue_after_round",
        "_stop_reason",
        "_can_close_direction_view",
    ):
        monkeypatch.setattr(v1, name, forbidden)
    assert not v3.should_continue_after_round(state, quality_context=context)
    bundle = v3.finalize(state, request, result.evidence_candidates, quality_context=context)
    assert bundle.quality.status == "partial"
    assert bundle.result.status == "degraded" and bundle.result.stop_reason.code != "sufficient"
    assert bundle.result.causal_case == result.causal_case
    assert bundle.result.horizons == result.horizons
    assert bundle.result.horizons[1].subjective_probability == 0.73
    assert json.loads(bundle.quality.full_report_json) == result.model_dump(mode="json")
    assert bundle.source_result_hash == canonical_hash(result.model_dump(mode="json"))
    assert bundle.projected_result_hash == canonical_hash(bundle.result.model_dump(mode="json"))


def test_legacy_reviewer_labels_cannot_invent_structured_resolution():
    _, result, state, context = material()
    review = ResearchReview(
        review_id="legacy",
        role="independent_reviewer",
        target_claim="liquidity",
        evidence_refs=["evidence-official"],
        result_hash="c" * 64,
        challenge="流动性风险是否降低结论",
        resolution="accepted",
        limitations=[],
        created_at=result.finished_at,
    )
    result = result.model_copy(update={"reviews": [review]})
    state["latest_result"] = result.model_dump(mode="json")
    context = context.model_copy(
        update={"bound_result_hash": canonical_hash(result.model_dump(mode="json"))}
    )
    value = quality_input_from_state(state, context=context)
    assert value.challenges[0].status == "deferred"
    assert value.challenges[0].effect_on_direction == "unknown"
    assert v3.quality_for_state(state, quality_context=context).status == "unresolved"


def test_actual_round_limit_and_deadline_never_report_sufficient():
    request, _, state, context = material()
    state["current_round"] = request.execution_budget.max_evidence_rounds
    quality = v3.quality_for_state(state, quality_context=context)
    assert quality.status == "unresolved" and "research_budget_exhausted" in quality.reason_codes
    assert v3.project_final(state, quality_context=context).stop_reason.code == "round_budget"
    state["current_round"] = 1
    context = context.model_copy(update={"assessed_at": request.deadline_at})
    assert v3.project_final(state, quality_context=context).stop_reason.code == "deadline"


def test_cached_quality_is_bound_to_current_state_and_cannot_be_reused_after_change():
    _, _, state, context = material()
    state["quality_decision"] = v3.quality_for_state(state, quality_context=context).model_dump(
        mode="json"
    )
    state["estimated_cost_usd"] = None
    with pytest.raises(ValueError, match="sidecar_changed"):
        v3.should_continue_after_round(state, quality_context=context)


def test_round_attaches_full_current_report_and_quality_without_using_v1_finalizer():
    request, result, _, context = material()
    initial = v1.initial_research_state(request)
    delta, citation, quality = v3.project_round_v3(
        initial,
        request,
        request,
        result,
        result.evidence_candidates,
        [],
        result.evidence_candidates,
        quality_context=context,
    )
    merged = {**initial, **delta}
    assert citation.disposition == "accepted" and quality.status == "partial"
    assert merged["quality_source_result"] == result.model_dump(mode="json")
    assert v3.quality_for_state(merged).decision_hash == quality.decision_hash
    final, _, same_quality = v3.project_final_v3(merged, request, result.evidence_candidates)
    assert final.status == "degraded" and same_quality == quality


def test_invalid_three_horizon_contract_is_not_silently_relabelled_complete():
    _, result, state, context = material()
    result = result.model_copy(update={"horizons": [result.horizons[1]]})
    state["latest_result"] = result.model_dump(mode="json")
    context = context.model_copy(
        update={"bound_result_hash": canonical_hash(result.model_dump(mode="json"))}
    )
    quality = v3.quality_for_state(state, quality_context=context)
    assert quality.report_status == "untrusted" and quality.status == "unresolved"
    assert any(item.startswith("horizon_contract_invalid:") for item in quality.unknowns)


def test_accepted_label_does_not_allow_evidence_received_after_decision_cutoff():
    request, _, state, context = material()
    state["evidence"][0]["received_at"] = (request.pit_cutoff_at + timedelta(minutes=1)).isoformat()
    quality = v3.quality_for_state(state, quality_context=context)
    assert "evidence-official" not in quality.retained_evidence_refs
    assert quality.status == "unresolved" and quality.publication_mode == "research_only"
    assert "report_evidence_unverified" in quality.reason_codes


def test_rejected_current_citation_retains_raw_report_and_prior_selected_result_separately():
    request, prior, initial, context = material()
    source = prior.model_dump(mode="json")
    source["causal_case"]["main_chain"][0]["evidence_refs"] = ["foreign-run-evidence"]
    current = ResearchSessionResult.model_validate(source)
    context = context.model_copy(update={"bound_result_hash": canonical_hash(source)})
    initial["current_round"] = 0
    initial["rounds"] = []
    initial["total_tool_calls"] = 0
    initial["estimated_cost_usd"] = 0.0
    delta, citation, quality = v3.project_round_v3(
        initial,
        request,
        request,
        current,
        prior.evidence_candidates,
        [],
        prior.evidence_candidates,
        prior_selected_result=prior,
        quality_context=context,
    )
    merged = {**initial, **delta}
    assert citation.disposition == "rejected"
    assert merged["quality_source_result"] == source
    assert merged["latest_result"] == prior.model_dump(mode="json")
    assert quality.report_status == "untrusted" and quality.publication_mode == "research_only"
    bundle = v3.finalize(merged, request, prior.evidence_candidates)
    assert bundle.result.status == "degraded" and bundle.result.causal_case == prior.causal_case
    assert json.loads(bundle.quality.full_report_json) == source
    assert bundle.source_result_hash == canonical_hash(source)
    assert bundle.projected_result_hash == canonical_hash(bundle.result.model_dump(mode="json"))
