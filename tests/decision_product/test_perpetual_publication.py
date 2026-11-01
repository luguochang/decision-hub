"""Pure publication derivation fixtures; no SQL/native/provider authority claim."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


def fixture(
    branch="flat", *, unknown=False, lexical=False, economic_cost=True, candidate_cost=True,
    complete_domain=False,
):
    import json

    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        perpetual_cost_assumptions_original,
    )
    from packages.kernel.decision_hub_kernel.application.perpetual_research_reader import (
        TrustedPerpetualResearch,
    )
    from packages.kernel.decision_hub_kernel.decision.publication_anchor import (
        freeze_publication_anchor,
    )
    from packages.kernel.decision_hub_kernel.ports.research_observation import ObservedExecutionRef
    from tests.contracts.test_d2_candidate_contract import body_ref, review_request
    from tests.decision_product.test_action_comparison import fixture as method_fixture

    costs = perpetual_cost_assumptions_original()
    packet = review_request()
    synth = packet["synthesis"]
    original_method = method_fixture()
    context = synth["candidate_set"]["context"]
    context["utility_ref"] = deepcopy(original_method["bindings"]["utility"])
    rows = synth["candidate_set"]["candidates"]
    definitions = [
        ("intent-A", "open", "long"),
        ("intent-B", "open", "short"),
        ("intent-C", "no_change", "flat"),
    ]
    if branch != "flat":
        definitions = [
            ("intent-A", "no_change", branch),
            ("intent-B", "reduce", branch),
            ("intent-C", "close", "flat"),
        ]
    for row, (identity, action, side) in zip(rows, definitions, strict=True):
        row.update(
            candidate_id=identity,
            action=action,
            target_side=side,
            position_branch=branch,
            context=deepcopy(context),
            cost_ref=costs.base_cost_ref.model_dump(mode="json") if candidate_cost else None,
        )
    if complete_domain:
        child_ref = body_ref(rows[1]["candidate_id"], rows[1])
        conditional = deepcopy(rows[1])
        conditional.update(candidate_id="conditional-short", action="conditional",
                           condition_refs=[body_ref("entry-trigger", {"kind": "fixture"})],
                           nested_action_ref=child_ref)
        rows.append(conditional)
        for held in ("long", "short"):
            for action in ("no_change", "add", "reduce", "close"):
                other = deepcopy(rows[0])
                other.update(candidate_id=f"{held}-{action}", position_branch=held,
                             action=action, target_side="flat" if action == "close" else held)
                rows.append(other)
    synth["candidate_set"]["candidate_refs"] = [body_ref(row["candidate_id"], row) for row in rows]
    synth["candidate_set_ref"] = body_ref("set", synth["candidate_set"])
    if economic_cost:
        synth["economic_input_refs"].extend(
            [costs.reference.model_dump(mode="json"), costs.base_cost_ref.model_dump(mode="json")]
        )
    if lexical:
        synth["as_of"] = synth["as_of"].replace("Z", ".000Z")
        synth["known_at_max"] = synth["known_at_max"].replace("Z", ".000Z")
        synth["candidate_set"]["as_of"] = synth["as_of"]
        for estimate in (
            synth["event_probability"]["occurrence_distribution"],
            synth["event_probability"]["outcome_given_occurrence"],
            synth["market_distribution"]["direction_distribution"],
        ):
            estimate["as_of"] = synth["as_of"]
        synth["candidate_set_ref"] = body_ref("set", synth["candidate_set"])
        synth["event_probability_ref"] = body_ref("event", synth["event_probability"])
        synth["market_distribution"]["event_scenario_ref"] = synth["event_probability_ref"]
        synth["market_distribution_ref"] = body_ref("market", synth["market_distribution"])
    # Model included-body hashes are re-sealed for this pure mathematical fixture.
    domain = packet["allowed_domain"]
    domain["candidate_set_ref"] = synth["candidate_set_ref"]
    domain["event_probability_ref"] = synth["event_probability_ref"]
    domain["market_distribution_ref"] = synth["market_distribution_ref"]
    synth["allowed_domain_ref"] = body_ref("domain", domain)
    packet.update(
        synthesis_ref=body_ref("synth", synth), allowed_domain_ref=synth["allowed_domain_ref"]
    )
    auth = dict(
        schema_version="action-review-authorization.v2",
        review_request_ref=body_ref("review", packet),
        synthesis_ref=packet["synthesis_ref"],
        candidate_set_ref=synth["candidate_set_ref"],
        candidate_target_ref=synth["candidate_set"]["candidate_target_ref"],
        anchor_rule_ref=synth["candidate_set"]["anchor_rule_ref"],
        event_probability_ref=synth["event_probability_ref"],
        market_distribution_ref=synth["market_distribution_ref"],
        method_ref=synth["method_ref"],
        method_code_hash=synth["method_code_hash"],
        allowed_domain_ref=synth["allowed_domain_ref"],
        reviewed_at="2026-01-01T00:20:00Z",
        valid_until="2026-01-01T01:00:00Z",
        invalidation_refs=domain["invalidation_refs"],
        reviewer_input_ref=body_ref("review", packet),
        verdict="approved",
    )
    assigned = datetime(2026, 1, 1, 0, 25, tzinfo=UTC)
    observed = ObservedExecutionRef(
        output_id="mapped-original",
        role="mapped",
        schema_version="research-mapped-observation.v2",
        payload_hash="a" * 64,
        recorded_at=assigned,
    )
    materials = dict(
        context=deepcopy(context),
        opportunity_id=synth["opportunity_id"],
        candidate_target=synth["candidate_target"],
        candidate_target_ref=synth["candidate_set"]["candidate_target_ref"],
        anchor_rule=synth["anchor_rule"],
        anchor_rule_ref=synth["candidate_set"]["anchor_rule_ref"],
        utility_policy=original_method["utility_policy"],
        account_snapshot=None,
        position_context={"state": "unknown" if unknown else branch},
        cost_assumptions=costs.body,
        cost_assumptions_ref=costs.reference.model_dump(mode="json"),
        cost_policy=costs.base_cost_policy.model_dump(mode="json"),
        cost_policy_ref=costs.base_cost_ref.model_dump(mode="json"),
        close_window={"window_hash": "b" * 64, "as_of": "2026-01-01T00:00:00Z"},
        method_ref=synth["method_ref"],
        method_code_hash=synth["method_code_hash"],
    )
    trusted = TrustedPerpetualResearch(
        run_id="run-pure",
        owner_id=context["owner_id"],
        opportunity_id=synth["opportunity_id"],
        mapped_ref=observed,
        transport_ref=observed,
        provider_emission_ref=observed,
        admission_ref_json=json.dumps(body_ref("admission", materials)),
        task_binding_ref_json=json.dumps(body_ref("task", {})),
        synthesis_json=json.dumps(synth),
        review_request_json=json.dumps(packet),
        authorization_json=json.dumps(auth),
        materials_json=json.dumps(materials),
        native_selection_json=json.dumps({"call_id": "actual-reviewed-call"}),
        producer_generation=1,
        known_at=assigned,
    )
    bound = freeze_publication_anchor(
        candidate_target=synth["candidate_target"],
        anchor_rule=synth["anchor_rule"],
        candidate_target_ref=synth["candidate_set"]["candidate_target_ref"],
        anchor_rule_ref=synth["candidate_set"]["anchor_rule_ref"],
        assigned_at=assigned,
        opportunity_id=synth["opportunity_id"],
        commit_id="commit-pure",
    )
    return trusted, bound, costs


@pytest.mark.parametrize("branch", ["flat"])
def test_complete_reviewed_branch_uses_one_final_target_and_original_candidate_ids(branch):
    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(branch)
    before = (trusted.synthesis_json, trusted.materials_json)
    value = derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
    assert len(value.branches) == 1
    result = value.branches[0]
    assert (
        result.position_branch == branch
        and result.comparison_inputs.schema_version == "decision-method-inputs.v2"
    )
    assert result.comparison_inputs.target == bound.target
    assert result.comparison.target_ref == bound.target_ref
    assert (
        value.snapshot.bindings.final_target
        == value.frozen_forecast.final_target_ref
        == bound.target_ref
    )
    assert result.revision.bindings == value.snapshot.bindings
    assert result.revision.selected_candidate.candidate_id in {"intent-A", "intent-B", "intent-C"}
    assert (
        result.head.state == "proposed"
        and result.head.monitor_state == "paused"
        and result.head.next_due is None
    )
    assert (
        result.comparison.financial_qualified is False
        and result.comparison.executable_contracts is None
    )
    assert len(result.sensitivity_cases) == 4
    assert before == (trusted.synthesis_json, trusted.materials_json)
    assert len({item.reference.ref for item in value.originals}) == len(value.originals)


def test_complete_twelve_intents_preserve_unsupported_candidates_without_ranking_them():
    import json

    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(complete_domain=True)
    before = trusted.synthesis_json
    value = derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
    original_set = next(item for item in value.originals if item.role == "candidate_set")
    assert len(json.loads(original_set.body_json)["candidates"]) == 12
    derivation = json.loads(
        next(item.body_json for item in value.originals if item.role == "derivation")
    )
    unavailable = derivation["unavailable_candidates"]
    assert len(unavailable) == 9
    conditional = next(row for row in unavailable if row["candidate_id"] == "conditional-short")
    assert conditional["reason"] == "conditional_fill_distribution_unavailable"
    assert conditional["reviewed_candidate_ref"] in (
        json.loads(before)["candidate_set"]["candidate_refs"]
    )
    assert all(
        set(row) == {"candidate_id", "reviewed_candidate_ref", "reason"} for row in unavailable
    )
    assert set(derivation["candidate_id_mapping"].values()) == {"intent-A", "intent-B", "intent-C"}
    assert "reviewed_candidates_unavailable_and_unranked" in value.gaps
    assert value.branches[0].revision.selected_candidate.candidate_id in {
        "intent-A", "intent-B", "intent-C"
    }
    assert len(value.branches[0].comparison.candidate_results) == 3
    assert trusted.synthesis_json == before


def test_unknown_state_is_explicit_assumed_flat_and_known_state_never_falls_back():
    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(unknown=True)
    value = derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
    assert value.branches[0].revision.position_context == "unknown"
    assert "assumed_flat_not_account_operation" in value.gaps
    import json

    materials = json.loads(trusted.materials_json)
    materials["position_context"]["state"] = "long"
    with pytest.raises(ValueError, match="perpetual_publication_reviewed_branch_incomplete"):
        derive_perpetual_publication(
            trusted=replace(trusted, materials_json=json.dumps(materials)),
            bound=bound,
            cost_original=costs,
        )


def test_raw_forecast_timestamp_bytes_remain_original_while_typed_comparison_has_own_hashes():
    import json

    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(lexical=True)
    value = derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
    raw = json.loads(trusted.synthesis_json)
    assert (
        value.frozen_forecast.event_probability_ref.model_dump(mode="json")
        == raw["event_probability_ref"]
    )
    original = next(item for item in value.originals if item.role == "native_synthesis")
    assert json.loads(original.body_json) == json.loads(trusted.synthesis_json)
    wire = json.loads(
        next(item.body_json for item in value.originals if item.role == "native_wire_originals")
    )
    assert wire["synthesis_json"] == trusted.synthesis_json
    import hashlib

    assert all(
        hashlib.sha256(item.body_json.encode()).hexdigest() == item.ref.hash
        for item in value.originals
    )
    assert raw["as_of"].endswith(".000Z")
    assert value.branches[0].comparison.forecast_as_of == value.frozen_forecast.forecast_as_of


@pytest.mark.parametrize("branch", ["long", "short"])
def test_held_unit_reduce_without_authenticated_half_policy_is_explicitly_uncovered(branch):
    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(branch)
    with pytest.raises(ValueError, match="perpetual_publication_reviewed_branch_unsupported"):
        derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)


@pytest.mark.parametrize("change", ["cost", "candidate_cost", "path", "anchor"])
def test_missing_cost_or_changed_raw_path_or_unrelated_anchor_cannot_publish(change):
    import json

    from packages.kernel.decision_hub_kernel.decision.perpetual_publication import (
        derive_perpetual_publication,
    )

    trusted, bound, costs = fixture(
        economic_cost=change != "cost", candidate_cost=change != "candidate_cost"
    )
    synth = json.loads(trusted.synthesis_json)
    if change == "cost":
        with pytest.raises(ValueError, match="perpetual_publication_cost_original_missing"):
            derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
        return
    elif change == "candidate_cost":
        with pytest.raises(
            ValueError, match="perpetual_publication_candidate_cost_original_missing"
        ):
            derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
        return
    elif change == "path":
        synth["path_distribution"]["scenarios"][0]["terminal_return_fraction"] = "0.9"
        trusted = replace(trusted, synthesis_json=json.dumps(synth))
    else:
        bound = replace(bound, target_ref=SimpleNamespace(ref="other-final", hash="f" * 64))
    with pytest.raises(ValueError):
        derive_perpetual_publication(trusted=trusted, bound=bound, cost_original=costs)
