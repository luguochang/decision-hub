import hashlib
import json

import pytest
from pydantic import ValidationError

from packages.kernel.decision_hub_kernel.decision.research_quality import (
    QualityDecisionV1,
    ResearchChallengeV1,
    ResearchKeyRiskV1,
    ResearchQualityInputV1,
    assess_research_quality,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def report():
    return {
        "schema_version": "research-session-result.v1",
        "status": "completed",
        "causal_case": {
            "thesis": "方向假说偏多，但不能忽略流动性冲击。",
            "main_chain": [
                {
                    "statement": "实际利率下降支持需求",
                    "evidence_refs": ["fact:rates"],
                    "confirmation": "后续持续",
                    "invalidation": "实际利率反向上行",
                }
            ],
            "opposite_chain": [
                {
                    "statement": "去杠杆仍可能导致尾部下跌",
                    "evidence_refs": ["fact:liquidity"],
                    "confirmation": "净卖压增加",
                    "invalidation": "流动性恢复",
                }
            ],
            "unresolved_questions": ["非公开持仓无法确认"],
        },
        "horizons": [
            {
                "horizon": "24h",
                "action": "long",
                "subjective_probability": 0.7,
                "probability_status": "uncalibrated",
                "evidence_refs": ["fact:rates"],
                "trigger": "现货确认",
                "invalidation": "风险指标上行",
                "missing_facts": ["场外流动性缺口"],
            }
        ],
        "reviews": [],
        "all_details": {
            "unit": "USDT/BTC",
            "timestamp": "2026-10-08T00:00:00Z",
            "history": "完整报告" * 2000,
        },
    }


def quality_input(**updates):
    body = report()
    fields = dict(
        run_id="run:test",
        scope_id="crypto:spot:okx:BTC-USDT:trade:1m",
        source_result_hash=canonical_hash(body),
        full_report_json=json.dumps(body, ensure_ascii=False),
        accepted_evidence_refs=("fact:rates", "fact:liquidity"),
        factual_coverage_complete=True,
        direction_evidence_verified=True,
        runtime_attestation_ref="attestation:test",
        required_context_refs=("numeric-risk", "main-chain", "strongest-counter", "boundary"),
        retained_context_refs=("numeric-risk", "main-chain", "strongest-counter", "boundary"),
        context_fidelity_verified=True,
        budget_state="available",
        monitor_state="active",
        key_risks=(
            ResearchKeyRiskV1(
                risk_id="tail-liquidity",
                statement="多仓先经历大幅下跌后再涨，期末收益不能说明路径安全",
                evidence_refs=("fact:liquidity",),
                affected_scope=("path_risk", "execution"),
            ),
        ),
    )
    fields.update(updates)
    return ResearchQualityInputV1(**fields)


def full_input(**updates):
    return quality_input(
        path_risk_model_qualified=True,
        path_risk_qualification_ref="qualification:separate-verified",
        execution_support_verified=True,
        **updates,
    )


def challenge(**updates):
    fields = dict(
        challenge_id="critical:liquidity",
        severity="critical",
        blocking_scope=("direction", "path_risk"),
        evidence_refs=("fact:liquidity",),
    )
    fields.update(updates)
    return ResearchChallengeV1(**fields)


def test_complete_coverage_does_not_override_open_critical_challenge():
    value = full_input(
        challenges=(challenge(),),
        declared_requirement_ids=("liquidity",),
        executable_requirement_ids=("liquidity",),
    )
    decision = assess_research_quality(value)
    assert decision.factual_status == "complete"
    assert decision.status == "continue" and decision.should_continue
    assert decision.blocks_completion and decision.direction_status == "blocked"
    assert decision.path_risk_status == "blocked"
    assert decision.publication_mode == "research_only"


def test_unknown_path_and_execution_can_publish_limited_direction():
    value = quality_input()
    result = assess_research_quality(value)
    assert result.status == "partial" and not result.should_continue
    assert result.direction_status == "eligible"
    assert result.path_risk_status == result.execution_status == "unknown"
    assert result.publication_mode == "partial" and result.blocks_completion
    assert result.full_report_json == value.full_report_json
    assert json.loads(result.full_report_json)["horizons"][0]["subjective_probability"] == 0.7
    assert set(type(result).model_fields).isdisjoint({"risk_probability", "probability"})


def test_tail_only_challenge_does_not_erase_supported_direction():
    result = assess_research_quality(
        full_input(challenges=(challenge(blocking_scope=("path_risk",)),))
    )
    assert result.direction_status == "eligible" and result.path_risk_status == "blocked"
    assert result.status == "partial"


@pytest.mark.parametrize("state", ["exhausted", "timeout", "failed", "unknown"])
def test_budget_and_runtime_end_never_become_sufficient_even_with_full_coverage(state):
    result = assess_research_quality(full_input(budget_state=state))
    assert result.status == "unresolved" and not result.should_continue
    assert result.publication_mode == "research_only" and result.blocks_completion
    assert "research_budget_" + state in result.reason_codes


def test_context_must_retain_counter_and_critical_semantics():
    missing = assess_research_quality(
        full_input(retained_context_refs=("numeric-risk", "main-chain", "boundary"))
    )
    altered = assess_research_quality(full_input(context_fidelity_verified=False))
    assert missing.context_status == altered.context_status == "incomplete"
    assert missing.status == altered.status == "unresolved"
    assert missing.full_report_json == altered.full_report_json


@pytest.mark.parametrize(
    "status,disposition",
    [("deferred", "accepted"), ("answered", "accepted"), ("rejected", "rebutted")],
)
def test_relabelled_challenge_without_resolution_basis_stays_open(status, disposition):
    result = assess_research_quality(
        full_input(challenges=(challenge(status=status, disposition=disposition),))
    )
    assert result.status != "sufficient"
    assert result.unresolved_challenge_ids == ("critical:liquidity",)


def test_answered_challenge_requires_current_result_hash_accepted_refs_and_effects():
    value = full_input()
    answered = challenge(
        status="answered",
        disposition="accepted",
        resolution_evidence_refs=("fact:liquidity",),
        resolved_result_hash=value.source_result_hash,
        effect_on_direction="reduced",
        effect_on_tail_risk="increased",
        reason="纳入流动性反证并降低方向确信，尾部风险上调",
        reviewer_version="independent-review.v1",
    )
    result = assess_research_quality(value.model_copy(update={"challenges": (answered,)}))
    assert result.status == "sufficient" and not result.blocks_completion
    assert result.challenges[0].effect_on_tail_risk == "increased"
    stale = answered.model_copy(update={"resolved_result_hash": "a" * 64})
    assert (
        assess_research_quality(value.model_copy(update={"challenges": (stale,)})).status
        != "sufficient"
    )
    false_citation = answered.model_copy(update={"resolution_evidence_refs": ("not-in-this-run",)})
    assert (
        assess_research_quality(value.model_copy(update={"challenges": (false_citation,)})).status
        != "sufficient"
    )


def test_unmapped_native_reviewer_cannot_disappear_behind_coverage():
    body = report()
    body["reviews"] = [
        {
            "review_id": "native-review",
            "resolution": "requires_new_evidence",
            "challenge": "流动性被低估",
        }
    ]
    value = full_input(full_report_json=json.dumps(body), source_result_hash=canonical_hash(body))
    result = assess_research_quality(value)
    assert result.status == "unresolved" and result.direction_status == "blocked"
    assert result.unresolved_challenge_ids == ("unmapped_review:native-review",)


def test_untrusted_citations_missing_risk_or_counter_do_not_publish_complete():
    for change in ("unknown_ref", "counter", "risk"):
        body = report()
        updates = {}
        if change == "unknown_ref":
            body["causal_case"]["main_chain"][0]["evidence_refs"] = ["invented"]
        elif change == "counter":
            body["causal_case"]["opposite_chain"] = []
        else:
            updates["key_risks"] = ()
        result = assess_research_quality(
            full_input(
                full_report_json=json.dumps(body),
                source_result_hash=canonical_hash(body),
                **updates,
            )
        )
        assert result.report_status == "incomplete" and result.status != "sufficient"


def test_no_attestation_or_rejected_synthesis_is_retained_but_untrusted():
    value = full_input(runtime_attestation_ref=None)
    result = assess_research_quality(value)
    assert result.report_status == "untrusted" and result.status == "unresolved"
    assert result.full_report_json == value.full_report_json
    body = report()
    body["synthesis_failure_code"] = "native_synthesis_rejected"
    result = assess_research_quality(
        full_input(full_report_json=json.dumps(body), source_result_hash=canonical_hash(body))
    )
    assert result.report_status == "untrusted"


def test_full_report_refs_unknowns_and_monitor_state_are_preserved_without_truncation():
    value = quality_input(
        unknowns=("账户保证金规则未知",),
        unmapped_inquiries=("未批准的新暗池来源",),
        monitor_state="paused",
    )
    decision = assess_research_quality(value)
    assert decision.monitor_state == "paused"
    assert set(decision.unknowns) == {"账户保证金规则未知", "非公开持仓无法确认", "场外流动性缺口"}
    assert decision.unmapped_inquiries == value.unmapped_inquiries
    assert decision.retained_evidence_refs == value.accepted_evidence_refs
    assert decision.full_report_hash == hashlib.sha256(value.full_report_json.encode()).hexdigest()
    assert len(json.loads(decision.full_report_json)["all_details"]["history"]) == 8000
    assert decision == assess_research_quality(value)


def test_new_free_form_need_cannot_grant_route_and_hash_cannot_be_rebound():
    with pytest.raises(ValidationError, match="undeclared_route"):
        quality_input(executable_requirement_ids=("model-invented-source",))
    with pytest.raises(ValidationError, match="hash_mismatch"):
        quality_input(source_result_hash="0" * 64)


def test_no_progress_does_not_reopen_a_bounded_loop():
    result = assess_research_quality(
        quality_input(
            no_progress=True,
            declared_requirement_ids=("liquidity",),
            executable_requirement_ids=("liquidity",),
        )
    )
    assert not result.should_continue and result.status == "partial"


def test_sidecar_and_report_hashes_cannot_be_edited_after_decision():
    result = assess_research_quality(full_input())
    payload = result.model_dump()
    payload["status"] = "partial"
    with pytest.raises(ValidationError, match="decision_seal_mismatch"):
        QualityDecisionV1.model_validate(payload)
    payload = result.model_dump()
    payload["full_report_json"] = "{}"
    with pytest.raises(ValidationError, match="report_seal_mismatch"):
        QualityDecisionV1.model_validate(payload)


def test_unknown_report_schema_stays_untrusted_even_if_hard_coverage_is_complete():
    body = report()
    body["schema_version"] = "future-result.unsupported"
    result = assess_research_quality(
        full_input(full_report_json=json.dumps(body), source_result_hash=canonical_hash(body))
    )
    assert result.report_status == "untrusted" and result.status == "unresolved"
