"""Focused policy declarations; no Host, Graph, model or live database execution."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import (
    canonical,
    derived_ref,
    prepare_perpetual_synthesis,
)
from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)


def condition_case():
    fixture = json.loads(
        (ROOT / "artifacts/d2-native-preparation/draft-fixture.json").read_text(
            encoding="utf-8-sig"
        )
    )
    policy = {
        "schema_version": "perpetual-evidence-condition.v1",
        "causal_link_id": "main",
        "candidate_ids": ["open_long"],
        "purpose": "invalidation",
        "expression": {
            "op": "compare",
            "feature": "close",
            "scope_id": "crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
            "unit": "USDT/BTC",
            "cmp": "lte",
            "threshold": "67000",
            "max_age_seconds": 120,
        },
        "min_fresh_samples": 2,
        "evaluation_seconds": 60,
        "valid_until": "2026-01-01T01:00:00Z",
    }
    assumption = {
        "schema_version": "decision-model-assumption.v1",
        "assumption_id": "monitor_support_lost",
        "role": "entry_condition",
        "statement": canonical(policy),
        "evidence_refs": [],
    }
    fixture["draft"]["assumptions"].append(assumption)
    prepared = prepare_perpetual_synthesis(fixture["draft"], **fixture["options"])
    return fixture, policy, assumption, prepared


def emission_ref(name):
    return ObservedExecutionRef(
        output_id=name,
        role="provider_tool_emission",
        schema_version="research-provider-tool-emission.v1",
        payload_hash="a" * 64,
        recorded_at=AT,
    )


def test_explicit_condition_survives_current_v1_prepare_without_execution_authority():
    from packages.kernel.decision_hub_kernel.decision.perpetual_conditions import (
        decode_perpetual_condition,
    )

    fixture, policy, assumption, prepared = condition_case()
    result = decode_perpetual_condition(assumption, synthesis=prepared["candidate"])
    assert result.status == "declared"
    assert result.policy.purpose == "invalidation"
    assert result.policy.min_fresh_samples == 2
    assert json.loads(result.policy.expression_json)["threshold"] == "67000"
    assert result.assumption_ref_json == canonical(
        derived_ref("decision_model_assumption", assumption)
    )
    assert prepared["assumptions"] == fixture["draft"]["assumptions"]
    assert prepared["candidate"]["candidate_set"]["candidates"][0]["condition_refs"] == []
    assert (
        prepared["candidate"]["market_distribution"]["direction_distribution"]
        == prepare_perpetual_synthesis(
            dict(fixture["draft"], assumptions=fixture["draft"]["assumptions"][:-1]),
            **fixture["options"],
        )["candidate"]["market_distribution"]["direction_distribution"]
    )


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("prose", "condition_declaration_not_json"),
        ("broken_json", "condition_declaration_invalid_json"),
        ("noncanonical", "condition_declaration_not_canonical"),
        ("unsupported_metric", "action_feature_not_admitted"),
        ("foreign_scope", "action_feature_not_admitted"),
        ("wrong_unit", "action_feature_not_admitted"),
        ("missing_threshold", "action_condition_invalid"),
        ("missing_samples", "condition_declaration_fields_invalid"),
        ("foreign_link", "condition_causal_link_unavailable"),
        ("foreign_candidate", "condition_candidate_unavailable"),
        ("expired_domain", "condition_validity_outside_target"),
    ],
)
def test_unmonitored_declaration_preserves_normal_forecast(fault, reason):
    from packages.kernel.decision_hub_kernel.decision.perpetual_conditions import (
        decode_perpetual_condition,
    )

    _fixture, policy, assumption, prepared = condition_case()
    if fault == "unsupported_metric":
        policy["expression"]["feature"] = "real_yield"
    elif fault == "foreign_scope":
        policy["expression"]["scope_id"] = "crypto:spot:okx:BTC-USDT:trade:1m"
    elif fault == "wrong_unit":
        policy["expression"]["unit"] = "USDT/ETH"
    elif fault == "missing_threshold":
        del policy["expression"]["threshold"]
    elif fault == "missing_samples":
        del policy["min_fresh_samples"]
    elif fault == "foreign_link":
        policy["causal_link_id"] = "another_run_link"
    elif fault == "foreign_candidate":
        policy["candidate_ids"] = ["another_run_action"]
    elif fault == "expired_domain":
        policy["valid_until"] = "2026-01-02T01:00:00Z"
    assumption["statement"] = canonical(policy)
    if fault == "prose":
        assumption["statement"] = "当价格明显走弱时撤销观点。"
    elif fault == "broken_json":
        assumption["statement"] = "{bad"
    elif fault == "noncanonical":
        assumption["statement"] = json.dumps(policy, indent=2)
    before = deepcopy(prepared["candidate"])
    result = decode_perpetual_condition(assumption, synthesis=prepared["candidate"])
    assert result.status == "unmonitored" and result.policy is None
    assert result.reason_code == reason
    assert prepared["candidate"] == before


def test_prepared_condition_extraction_binds_exact_reviewed_assumption_and_run():
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _prepared_condition_declarations,
    )

    _fixture, policy, assumption, prepared = condition_case()
    assumptions = prepared["assumptions"]
    prompt = "\n".join(
        [
            "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON",
            canonical(assumptions),
            "END_DECLARED_MODEL_ASSUMPTIONS_JSON",
            "MODEL_ASSUMPTIONS_SHA256=" + derived_ref("x", assumptions)["hash"],
        ]
    )
    result = _prepared_condition_declarations(
        prepared["candidate"],
        assumptions,
        run_id="same_run",
        synthesis_ref=prepared["review_request"]["synthesis_ref"],
        prepare_emission_ref=emission_ref("prepare"),
        actual_review_prompt=prompt,
    )
    assert result.run_id == "same_run"
    assert result.candidate_target_ref_json == canonical(
        prepared["candidate"]["candidate_set"]["candidate_target_ref"]
    )
    assert result.prepare_emission_ref.output_id == "prepare"
    declared = next(
        row for row in result.declarations if row.assumption_id == assumption["assumption_id"]
    )
    assert declared.status == "declared" and declared.policy.expression_json == canonical(
        policy["expression"]
    )
    changed = deepcopy(assumptions)
    changed[-1]["statement"] = changed[-1]["statement"].replace('"67000"', '"66000"')
    with pytest.raises(ValueError, match="perpetual_condition_reviewed_assumptions_mismatch"):
        _prepared_condition_declarations(
            prepared["candidate"],
            changed,
            run_id="same_run",
            synthesis_ref=prepared["review_request"]["synthesis_ref"],
            prepare_emission_ref=emission_ref("prepare"),
            actual_review_prompt=prompt,
        )
    with pytest.raises(ValueError, match="perpetual_condition_synthesis_ref_mismatch"):
        _prepared_condition_declarations(
            prepared["candidate"],
            assumptions,
            run_id="same_run",
            synthesis_ref={"ref": "other", "hash": "f" * 64},
            prepare_emission_ref=emission_ref("prepare"),
            actual_review_prompt=prompt,
        )


def test_legacy_packet_cannot_claim_prepared_condition_policy():
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        _prepared_condition_declarations,
    )

    _fixture, _policy, _assumption, prepared = condition_case()
    result = _prepared_condition_declarations(
        prepared["candidate"],
        [],
        run_id="same_run",
        synthesis_ref=prepared["review_request"]["synthesis_ref"],
        prepare_emission_ref=None,
        actual_review_prompt=None,
    )
    assert result.declarations == ()
    assert result.reason_code == "prepared_condition_declarations_unavailable"


@pytest.mark.parametrize("fault", [None, "run", "target", "provider"])
def test_condition_reader_rejects_foreign_source_binding(monkeypatch, fault):
    """Unit adapter gate: existing native/SQL authentication is a trusted dependency.

    This tests the new source-binding consumer, not the origin of the fixture's
    approval. Full native authentication remains a separate integration gate.
    """
    from types import SimpleNamespace

    from packages.kernel.decision_hub_kernel.application import (
        perpetual_research_binding as binding,
    )
    from packages.kernel.decision_hub_kernel.application import perpetual_research_reader as reader
    from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
        normalize_action_synthesis,
    )

    _fixture, _policy, _assumption, prepared = condition_case()
    assumptions = prepared["assumptions"]
    prompt = "\n".join(
        [
            "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON",
            canonical(assumptions),
            "END_DECLARED_MODEL_ASSUMPTIONS_JSON",
            "MODEL_ASSUMPTIONS_SHA256=" + derived_ref("x", assumptions)["hash"],
        ]
    )
    conditions = reader._prepared_condition_declarations(
        prepared["candidate"],
        assumptions,
        run_id="same_run",
        synthesis_ref=prepared["review_request"]["synthesis_ref"],
        prepare_emission_ref=emission_ref("prepare"),
        actual_review_prompt=prompt,
    )
    native = SimpleNamespace(
        synthesis=normalize_action_synthesis(prepared["candidate"]),
        authorization=SimpleNamespace(reviewed_at=AT),
    )
    context = SimpleNamespace(
        run_id="same_run",
        request_id=prepared["candidate"]["request_id"],
        dsh_session_id="same_session",
    )
    mapped = SimpleNamespace(
        context=context, perpetual_synthesis=native, mapping_input_generation=1
    )
    monkeypatch.setattr(reader, "read_mapped_perpetual_observation", lambda *_: mapped)
    monkeypatch.setattr(
        binding,
        "read_authenticated_perpetual_execution_in_session",
        lambda *_a, **_k: (
            None,
            {"admission": {}},
            SimpleNamespace(execution=SimpleNamespace(dsh_session_id="same_session")),
        ),
    )
    monkeypatch.setattr(reader, "validate_perpetual_packet_scope", lambda *_a, **_k: None)
    monkeypatch.setattr(
        reader,
        "_read_perpetual_provider_originals_and_definitions_in_session",
        lambda *_a, **_k: SimpleNamespace(
            emission_ref=emission_ref("finalize"), conditions=conditions
        ),
    )
    target = deepcopy(prepared["candidate"]["candidate_set"]["candidate_target_ref"])
    if fault == "target":
        target["hash"] = "f" * 64
    args = dict(
        source_run_id="foreign_run" if fault == "run" else "same_run",
        candidate_target_ref=target,
        mapped_ref=emission_ref("mapped"),
        provider_emission_ref=emission_ref("foreign" if fault == "provider" else "finalize"),
        known_at=AT,
    )
    if fault:
        with pytest.raises(
            ValueError,
            match={
                "run": "perpetual_condition_source_run_mismatch",
                "target": "perpetual_condition_source_target_mismatch",
                "provider": "perpetual_condition_provider_mismatch",
            }[fault],
        ):
            reader.read_perpetual_conditions_in_session(None, **args)
    else:
        result = reader.read_perpetual_conditions_in_session(None, **args)
        assert result.run_id == "same_run" and result.provider_emission_ref.output_id == "finalize"
        assert result.mapped_ref.output_id == "mapped"
        assert result.declarations[-1].status == "declared"
        assert result.declarations[-1].assumption_json == canonical(assumptions[-1])
