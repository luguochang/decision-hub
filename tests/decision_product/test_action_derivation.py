"""Deterministic projection controls. Synthetic originals, not native authority."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.decision.action_derivation import (
    ActionBasisEvidence,
    derive_action_publication,
)
from packages.kernel.decision_hub_kernel.decision.action_features import (
    ACTION_MONITOR_MANIFEST_REF,
    action_feature_manifest,
)
from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
    research_action_catalog,
)
from packages.kernel.decision_hub_kernel.ports.action_plan import Opportunity
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchActionSynthesisSidecar
from tests.decision_product.test_research_signal_contracts import sidecar as raw_sidecar

AT = datetime(2026, 10, 13, tzinfo=UTC)
NOW = AT + timedelta(minutes=1)


def inputs():
    body = raw_sidecar()
    catalog = research_action_catalog("BTC-USDT")
    flat = body["synthesis"]["action_intents"][0]
    flat.update(
        size_policy_ref=catalog["size_policy"]["ref"],
        cost_policy_ref=catalog["cost_policy"]["ref"],
        target_spec_ref=catalog["targets"]["24h"]["ref"],
    )
    flat["probability"]["target_id"] = flat["target_spec_ref"]
    for purpose in ("exit", "invalidation"):
        condition = copy.deepcopy(flat["entry"][0])
        condition.update(id=purpose + "-1", purpose=purpose)
        flat[purpose] = [condition]
    long = copy.deepcopy(flat)
    long.update(position_context="long", action="reduce_long", exit=[], invalidation=[])
    long["probability"].update(kind="subjective", value=0.61)
    body["synthesis"]["action_intents"].append(long)
    opportunity = Opportunity.model_validate(
        {
            "schema_version": "decision-opportunity.v1",
            "id": "op-derive",
            "key": "test-only-op",
            "scope_id": body["scope_id"],
            "horizon": "24h",
            "kind": "scheduled",
            "event_ref": None,
            "registered_at": AT,
            "time_start": AT,
            "time_end": AT + timedelta(days=1),
            "target_spec_hash": catalog["targets"]["24h"]["hash"],
            "recipe_hash": "f" * 64,
            "cohort": "diagnostic",
            "status": "open",
            "reason": None,
        }
    )
    refs = [
        ActionBasisEvidence("evidence-1", "a" * 64, "evidence", AT, "primary-a", True),
        ActionBasisEvidence("evidence-2", "b" * 64, "evidence", AT, "primary-b", True),
        ActionBasisEvidence("alternative-1", "c" * 64, "evidence", AT, "primary-c", True),
        ActionBasisEvidence("claim_a", "d" * 64, "claim", AT, "author-a", True),
    ]
    return body, opportunity, refs


def freeze(body):
    value = ResearchActionSynthesisSidecar.model_validate(body)
    wire = value.model_dump(mode="json")
    wire["payload_hash"] = canonical_hash({k: v for k, v in wire.items() if k != "payload_hash"})
    return ResearchActionSynthesisSidecar.model_validate(wire)


def derive(body, opportunity, refs, *, now=NOW):
    return derive_action_publication(
        sidecar=freeze(body),
        sidecar_ref="output-sidecar",
        opportunity=opportunity,
        published_at=now,
        evidence=tuple(refs),
    )


def test_unknown_probability_does_not_block_entry_or_replace_branch_probability():
    body, opportunity, refs = inputs()
    original = copy.deepcopy(body)
    result = derive(body, opportunity, refs)
    flat, long, unknown = result.branches
    assert result.snapshot.probability.value is None
    assert [b.revision.advice_qualification for b in result.branches] == [
        "research_trial",
        "research_trial",
        "blocked",
    ]
    assert flat.revision.action == "enter_long" and long.revision.action == "reduce_long"
    assert unknown.revision.action == "wait" and unknown.head.state == "proposed"
    assert flat.head.state == long.head.state == "armed"
    assert body == original
    assert flat.revision.basis_refs[1].hash != long.revision.basis_refs[1].hash
    assert "#position=long" in long.revision.basis_refs[1].ref
    assert result == derive(body, opportunity, refs)


def test_missing_entry_protection_preserves_reduction_and_original_intent():
    body, opportunity, refs = inputs()
    body["synthesis"]["action_intents"][0]["exit"] = []
    result = derive(body, opportunity, refs)
    flat, long, _ = result.branches
    assert flat.revision.action == "enter_long"
    assert flat.revision.advice_qualification == "blocked"
    assert flat.head.next_due is None
    assert long.revision.advice_qualification == "research_trial"
    assert any("保护" in gap.reason and gap.scope == "flat" for gap in result.snapshot.gaps)


@pytest.mark.parametrize("mode", ["same_bytes", "future", "claim_as_fact"])
def test_counter_not_independent_keeps_entry_blocked_but_reduction_available(mode):
    body, opportunity, refs = inputs()
    row = refs[1]
    refs[1] = ActionBasisEvidence(
        row.ref,
        refs[0].payload_hash if mode == "same_bytes" else row.payload_hash,
        "claim" if mode == "claim_as_fact" else row.kind,
        AT + timedelta(seconds=1) if mode == "future" else AT,
        refs[0].independence_key if mode == "same_source" else row.independence_key,
        True,
    )
    result = derive(body, opportunity, refs)
    assert result.branches[0].revision.advice_qualification == "blocked"
    if mode != "future":
        assert result.branches[1].revision.advice_qualification == "research_trial"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("policy", "policy_reference_mismatch"),
        ("calibrated", "calibration_original_required"),
        ("missing_branch", "position_branches_required"),
        ("skill", "skill_consumption_original_required"),
        ("scope", "opportunity_target_mismatch"),
        ("late_registration", "publication_time_invalid"),
    ],
)
def test_false_binding_and_unsupported_authority_rejected(mutation, code):
    body, opportunity, refs = inputs()
    if mutation == "policy":
        body["synthesis"]["action_intents"][0]["size_policy_ref"] = "invented"
    elif mutation == "calibrated":
        body["synthesis"]["action_intents"][0]["probability"].update(
            kind="calibrated", value=0.73, provenance_ref="claimed-only"
        )
    elif mutation == "missing_branch":
        body["synthesis"]["action_intents"].pop()
    elif mutation == "skill":
        body["synthesis"]["skill_consumption_refs"] = [
            {
                "version_id": "skill-1",
                "version_hash": "a" * 64,
                "consumption_receipt_ref": "invented-skill-receipt",
                "consumption_receipt_hash": "b" * 64,
            }
        ]
    elif mutation == "scope":
        opportunity = opportunity.model_copy(update={"target_spec_hash": "a" * 64})
    elif mutation == "late_registration":
        opportunity = opportunity.model_copy(update={"registered_at": NOW})
    with pytest.raises(ValueError, match=code):
        derive(body, opportunity, refs)


def test_sidecar_tamper_and_expiry_rejected_without_rewriting_time():
    body, opportunity, refs = inputs()
    frozen = freeze(body)
    with pytest.raises(ValueError, match="sidecar_hash_mismatch"):
        derive_action_publication(
            sidecar=frozen.model_copy(update={"payload_hash": "c" * 64}),
            sidecar_ref="output-sidecar",
            opportunity=opportunity,
            published_at=NOW,
            evidence=tuple(refs),
        )
    with pytest.raises(ValueError, match="publication_time_invalid"):
        derive(body, opportunity, refs, now=AT + timedelta(days=1))


def test_missing_claim_stays_explicit_and_no_trade_is_not_range_forecast():
    body, opportunity, refs = inputs()
    body["synthesis"]["research_candidate_v1"]["horizons"][0]["action"] = "no_trade"
    result = derive(body, opportunity, refs[:-1])
    assert result.snapshot.direction_status == "unknown"
    assert {h.direction for h in result.snapshot.hypotheses} == {"long", "short", "range"}
    assert all("待验证情景" in h.rationale for h in result.snapshot.hypotheses)
    assert all(b.revision.advice_qualification == "blocked" for b in result.branches)
    assert any("外部观点" in gap.reason for gap in result.snapshot.gaps)


def test_same_venue_can_supply_distinct_opposing_facts_without_implying_multiple_sources():
    body, opportunity, refs = inputs()
    for index in (0, 1):
        row = refs[index]
        refs[index] = ActionBasisEvidence(
            row.ref, row.payload_hash, row.kind, row.known_at,
            "primary-a", row.eligible, "f" * 64,
        )
    result = derive(body, opportunity, refs)
    assert result.branches[0].revision.advice_qualification == "research_trial"
    assert result.branches[1].revision.advice_qualification == "research_trial"


def test_duplicate_assertion_with_different_source_labels_is_not_counterevidence():
    body, opportunity, refs = inputs()
    for index in (0, 1):
        row = refs[index]
        refs[index] = ActionBasisEvidence(
            row.ref, row.payload_hash, row.kind, row.known_at,
            row.independence_key, row.eligible, None, "f" * 64,
        )
    result = derive(body, opportunity, refs)
    assert result.branches[0].revision.advice_qualification == "blocked"


def test_due_review_is_not_expiry_and_preserves_risk_reduction():
    body, opportunity, refs = inputs()
    result = derive(body, opportunity, refs, now=AT + timedelta(hours=2))
    assert result.branches[1].revision.advice_qualification == "research_trial"
    assert result.branches[1].head.next_due == AT + timedelta(hours=2)


def test_each_branch_binds_exact_monitor_calculation_before_publication():
    body, opportunity, refs = inputs()
    result = derive(body, opportunity, refs)
    expected = canonical_hash(action_feature_manifest("BTC-USDT"))
    for branch in result.branches:
        matched = [row for row in branch.revision.basis_refs
                   if row.ref == ACTION_MONITOR_MANIFEST_REF]
        assert len(matched) == 1 and matched[0].hash == expected


def test_claim_original_cannot_corroborate_itself_and_independent_reduction_survives():
    body, opportunity, refs = inputs()
    for index in (0, 3):
        row = refs[index]
        refs[index] = ActionBasisEvidence(
            row.ref, row.payload_hash, row.kind, row.known_at,
            row.independence_key, row.eligible, "f" * 64,
        )
    review = copy.deepcopy(body["synthesis"]["mechanism_reviews"][0])
    review["review_id"] = "independent-reduction"
    review["edges"][0]["support_refs"] = ["evidence-2"]
    review["nodes"][0]["refs"] = ["evidence-2"]
    body["synthesis"]["mechanism_reviews"].append(review)
    long = body["synthesis"]["action_intents"][1]
    long.update(mechanism_review_ref="independent-reduction", evidence_refs=["evidence-2"])
    result = derive(body, opportunity, refs)
    assert result.branches[0].revision.advice_qualification == "blocked"
    assert result.branches[1].revision.advice_qualification == "research_trial"
