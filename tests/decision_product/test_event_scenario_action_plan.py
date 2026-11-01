"""Synthetic arithmetic/selection proof; no native review or source authority."""

import importlib
from copy import deepcopy

import pytest

from tests.decision_product.test_action_comparison import (
    content_hash,
    estimate,
    fixture,
    resign,
)

PACKAGE = "packages.kernel.decision_hub_kernel."
AS_OF = "2026-01-01T00:00:00Z"
START = "2026-01-01T00:02:00Z"
END = "2026-01-01T04:02:00Z"


def core():
    return importlib.import_module(PACKAGE + "decision.event_scenario_action_plan")


def fixture_plan(*, omit_b=False):
    originals = {}

    def original(name, body=None):
        body = body if body is not None else {"synthetic_original": name, "known_at": AS_OF}
        reference = {"ref": "synthetic:event-plan:" + name, "hash": content_hash(body)}
        originals[reference["ref"]] = deepcopy(body)
        return reference

    def hydrate(value):
        if isinstance(value, dict):
            if set(value) == {"ref", "hash"}:
                return original(value["ref"])
            return {key: hydrate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [hydrate(item) for item in value]
        return value

    event_definition = original("event-definition")
    observation_rule = original("first-release-rule")
    partition_ref = original("explicit-two-band-partition")
    predicates = []
    for scenario, operator in (("A", "lt"), ("B", "ge")):
        predicate = {
            "any_of": [
                {
                    "all_of": [
                        {
                            "metric_id": "synthetic-release",
                            "unit": "pp",
                            "reference_period": "2025-12",
                            "stage_id": "release",
                            "operator": operator,
                            "value": "0",
                        }
                    ]
                }
            ]
        }
        predicates.append(
            dict(
                scenario_id=scenario,
                predicate=predicate,
                definition_ref=original("scenario-" + scenario, predicate),
            )
        )
    event = dict(
        event_key="synthetic-macro-2026-01",
        family="synthetic_macro",
        stage_id="release",
        schedule_ref=original("schedule"),
        scheduled_at="2026-01-01T00:01:00Z",
        event_definition_ref=event_definition,
        observation_rule_ref=observation_rule,
        partition_ref=partition_ref,
    )
    occurrence = estimate(original("occurrence-target"), {"occurs": "1", "not_occurs": "0"})
    outcomes = estimate(
        original("outcome-target"), {"A": "0.60", "B": "0.40"}, condition=event_definition
    )
    probability = dict(
        schema_version="event-probability.v1",
        event_definition_ref=event_definition,
        occurrence_target_ref=occurrence["target_ref"],
        outcome_target_ref=outcomes["target_ref"],
        occurrence_distribution=occurrence,
        outcome_given_occurrence=outcomes,
        joint_partition_ref=partition_ref,
        observation_rule_ref=observation_rule,
    )
    probability_ref = original("event-probability", probability)
    base_method = original("base-financial-method")
    method = dict(
        schema_version="event-scenario-comparison-method.v1",
        method_id="event-scenario-assumed-unit-mean-net.v1",
        base_method_ref=base_method,
        base_method_code_hash="a" * 64,
        implementation_dependencies={"decision/event_scenario_action_plan.py": "b" * 64},
        qualification="research_trial",
        financial_qualified=False,
    )
    method["method_code_hash"] = content_hash(
        {
            "base_method_code_hash": method["base_method_code_hash"],
            "implementation_dependencies": method["implementation_dependencies"],
        }
    )
    method_ref = original("method", method)
    branches = []
    for scenario, probabilities in (
        ("A", ("0.70", "0.20", "0.10")),
        ("B", ("0.20", "0.30", "0.50")),
    ):
        if scenario == "B" and omit_b:
            continue
        condition = next(
            row["definition_ref"] for row in predicates if row["scenario_id"] == scenario
        )
        inputs = {}
        for position in ("flat", "long", "short"):
            value = hydrate(fixture())
            value["target"].update(
                anchor="post_release_response",
                window_start=START,
                window_end=END,
                conditioning_ref=condition,
                target_id="synthetic-" + scenario,
                target_family_ref=original("family-" + scenario),
            )
            target_ref = original("target-" + scenario, value["target"])
            value.update(
                event_probability=deepcopy(probability),
                position_branch=position,
                comparison_known_at_max=AS_OF,
                forecast_known_at_max=AS_OF,
            )
            value["bindings"].update(
                target=target_ref, event_probability=probability_ref, method=base_method
            )
            value["cost_policy"].update(
                entry_trade_cost_usdt="0.10",
                exit_trade_cost_usdt="0.10",
                long_funding_cash_usdt="0",
            )
            value["utility_policy"]["position_scenario"] = "unknown"
            action_ids = (
                ("open_long", "open_short", "no_change")
                if position == "flat"
                else ("no_change", "reduce_half", "close")
            )
            value["action_target_refs"] = {
                key: original(scenario + position + key) for key in action_ids
            }
            value["path_distribution"].update(
                market_target_ref=target_ref,
                conditioning_ref=condition,
                fill_weight_assumption_ref=original("assumed-weight-preservation"),
                scenarios=[
                    dict(scenario_id=label, probability=p, terminal_return_fraction=r)
                    for label, p, r in zip(
                        ("up-atom", "neutral-atom", "down-atom"),
                        probabilities,
                        ("0.01", "0", "-0.02"),
                        strict=True,
                    )
                ],
            )
            value["market_distribution"].update(
                market_target_ref=target_ref,
                event_scenario_ref=probability_ref,
                conditioning_ref=condition,
                direction_distribution=estimate(
                    target_ref,
                    dict(zip(("up", "neutral", "down"), probabilities, strict=True)),
                    condition=condition,
                ),
            )
            inputs[position] = resign(value)
        sample = inputs["flat"]
        branches.append(
            dict(
                branch_id="branch-" + scenario,
                scenario_id=scenario,
                comparison_inputs_by_position=inputs,
                condition_assumption_refs=[],
                applicability_domain=dict(
                    price_ratio_min="1",
                    price_ratio_max="1",
                    maximum_elapsed_seconds=0,
                    maximum_quote_age_seconds=0,
                    recomparison_allowed=False,
                    variables=[],
                    invalidation_refs=[],
                    valid_until=END,
                ),
                applicable_from=START,
                valid_until=END,
                cost_ref=sample["bindings"]["cost"],
                funding_ref=original("funding-zero"),
                margin_ref=None,
                fill_assumption_ref=sample["path_distribution"]["fill_weight_assumption_ref"],
                account_ref=None,
                unknown_reasons=["account_margin_unknown", "fill_model_unknown"],
            )
        )
        originals[sample["bindings"]["cost"]["ref"]] = deepcopy(sample["cost_policy"])
    first = branches[0]["comparison_inputs_by_position"]["flat"]
    context = dict(
        owner_id="synthetic-owner",
        account_kind="user_declared",
        account_id="unknown",
        instrument_id=first["instrument_id"],
        scope_id=first["scope_id"],
        profile_ref=original("profile"),
        horizon="4h",
        instrument_ref=first["bindings"]["instrument"],
        settings_ref=first["bindings"]["settings"],
        account_ref=None,
        position_ref=None,
        utility_ref=first["bindings"]["utility"],
    )
    marginal_target = deepcopy(first["target"])
    marginal_target.update(
        target_id="synthetic-marginal-given-event",
        conditioning_ref=event_definition,
        target_family_ref=original("family-marginal"),
    )
    draft = dict(
        schema_version="event-scenario-action-plan-draft.v1",
        plan_id="synthetic-plan",
        context=context,
        as_of=AS_OF,
        known_at_max=AS_OF,
        freeze_before="2026-01-01T00:00:30Z",
        event=event,
        occurrence=occurrence,
        outcome_given_occurrence=outcomes,
        scenario_predicates=predicates,
        branches=branches,
        marginal_target=marginal_target,
        marginal_target_ref=original("marginal-target", marginal_target),
        marginal_fill_assumption_ref=original("assumed-weight-preservation"),
        method_ref=method_ref,
        method_code_hash=method["method_code_hash"],
        qualification="research_trial",
    )
    return draft, originals


def selection_inputs(plan):
    def reference(name, body):
        return {"ref": "synthetic:" + name, "hash": content_hash(body)}
    branch = next(
        (row for row in plan["branches"] if row["scenario_id"] == "B"), plan["branches"][0]
    )
    realization = dict(
        schema_version="event-scenario-realization.v1",
        event=deepcopy(plan["event"]),
        released_at="2026-01-01T00:01:00Z",
        public_at="2026-01-01T00:01:00Z",
        received_at="2026-01-01T00:01:01Z",
        known_at="2026-01-01T00:01:01Z",
        metrics=[
            dict(
                metric_id="synthetic-release",
                unit="pp",
                reference_period="2025-12",
                stage_id="release",
                value="0.1",
            )
        ],
        source_ref={"ref": "synthetic:actual-source", "hash": "c" * 64},
    )
    scope = dict(
        schema_version="event-scenario-review-scope.v1",
        plan_ref=reference("plan", plan),
        method_ref=plan["method_ref"],
        method_code_hash=plan["method_code_hash"],
        reviewed_at="2026-01-01T00:00:10Z",
        freeze_before=plan["freeze_before"],
        valid_until=END,
        branches=[
            dict(
                branch_ref=row["branch_ref"],
                candidate_refs=[
                    ref
                    for refs in row["reviewed_candidate_refs_by_position"].values()
                    for ref in refs
                ],
                applicable_from=row["applicable_from"],
                valid_until=row["valid_until"],
            )
            for row in plan["branches"]
        ],
        verdict="approved",
    )
    quote = dict(
        quote_ref={"ref": "synthetic:quote", "hash": "d" * 64},
        observed_at=START,
        known_at=START,
        forecast_price_ref=branch["comparison_inputs_by_position"]["flat"]["price_bridge"][
            "forecast_price_ref"
        ],
        comparison_to_forecast_price_ratio="1",
    )
    verified = dict(
        schema_version="event-scenario-selection-inputs.v1",
        plan_ref=reference("plan", plan),
        review_scope=scope,
        review_authorization_ref=reference("review", scope),
        realization_ref=reference("realization", realization),
        position_branch="flat",
        quote=quote,
        cost_ref=branch["cost_ref"],
        funding_ref=branch["funding_ref"],
        margin_ref=branch["margin_ref"],
        fill_assumption_ref=branch["fill_assumption_ref"],
        account_ref=branch["account_ref"],
        settings_ref=plan["context"]["settings_ref"],
        position_ref=plan["context"]["position_ref"],
        domain_values=[],
        requires_conditional_fill=False,
    )
    return realization, verified


def rows(comparison):
    return {row["candidate_id"]: row for row in comparison["candidate_results"]}


def test_numerical_marginal_flat_realized_b_selects_frozen_short_with_originals_unchanged():
    draft, originals = fixture_plan()
    before = content_hash([draft, originals])
    plan = core().build_event_scenario_action_plan(draft, originals)
    assert content_hash([draft, originals]) == before
    assert plan["marginal_distribution"]["probabilities"] == [
        {"outcome_id": "up", "value": "0.5"},
        {"outcome_id": "neutral", "value": "0.24"},
        {"outcome_id": "down", "value": "0.26"},
    ]
    marginal = plan["marginal_distribution"]["comparisons_by_position"]["flat"]["comparison"]
    assert marginal["selected_action_id"] == "no_change"
    assert [
        rows(marginal)[key]["unit_expected_net_if_assumed_fills_usdt"]
        for key in ("open_long", "open_short", "no_change")
    ] == ["-0.22", "-0.18", "0"]
    a, b = plan["branches"]
    assert [
        rows(a["comparisons_by_position"]["flat"]["comparison"])[key][
            "unit_expected_net_if_assumed_fills_usdt"
        ]
        for key in ("open_long", "open_short")
    ] == ["0.3", "-0.7"]
    assert [
        rows(b["comparisons_by_position"]["flat"]["comparison"])[key][
            "unit_expected_net_if_assumed_fills_usdt"
        ]
        for key in ("open_long", "open_short")
    ] == ["-1", "0.6"]
    # Held long no_change retains the whole original holding's -0.8 gross and exit cost.
    assert (
        rows(b["comparisons_by_position"]["long"]["comparison"])["no_change"][
            "unit_expected_net_if_assumed_fills_usdt"
        ]
        == "-0.9"
    )
    realization, verified = selection_inputs(plan)
    frozen_hash = content_hash(plan)
    selection = core().evaluate_event_scenario_action_plan(
        plan, realization, [], verified, as_of=START
    )
    assert selection["status"] == "selected_preapproved_branch"
    assert selection["branch_ref"] == b["branch_ref"]
    assert selection["selected_action_id"] == "open_short"
    assert (
        selection["selected_comparison_ref"]
        == b["comparisons_by_position"]["flat"]["comparison_ref"]
    )
    assert selection["unit_expected_net_if_assumed_fills_usdt"] == "0.6"
    assert selection["original_forecast_cutoff"] == AS_OF
    assert selection["execution_authorized"] is False
    assert selection["fill_probability"] is selection["opportunity_expected_net_usdt"] is None
    assert selection["account_feasibility"] == "unknown"
    assert selection["executable_contracts"] is None
    assert content_hash(plan) == frozen_hash
    ports = importlib.import_module(PACKAGE + "ports.event_scenario_action_plan")
    frozen_method = originals[draft["method_ref"]["ref"]]
    method_body, method_ref, method_code_hash = ports.event_scenario_method_material(
        base_method_ref=frozen_method["base_method_ref"],
        base_method_code_hash=frozen_method["base_method_code_hash"],
        implementation_dependencies=frozen_method["implementation_dependencies"],
    )
    assert method_body == frozen_method
    assert method_ref["ref"] == "decision-method:event-scenario-assumed-unit-mean-net.v1"
    assert method_code_hash == draft["method_code_hash"]
    assert ports.prepare_event_scenario_action_plan(draft, originals) == plan
    assert (
        ports.select_event_scenario_action_plan(
            plan,
            realization,
            [],
            verified,
            as_of=START,
        )
        == selection
    )


def test_conditional_fill_counterexample_never_borrows_conditional_q_as_fill_weights():
    draft, originals = fixture_plan()
    plan = core().build_event_scenario_action_plan(draft, originals)
    realization, verified = selection_inputs(plan)
    verified["requires_conditional_fill"] = True
    selected = core().evaluate_event_scenario_action_plan(
        plan, realization, [], verified, as_of=START
    )
    assert selected["status"] == "unsupported_fill_distribution"
    assert selected["selected_candidate_ref"] is selected["selected_comparison_ref"] is None
    assert selected["fill_probability"] is selected["opportunity_expected_net_usdt"] is None
    assert selected["unit_expected_net_if_assumed_fills_usdt"] is None


def test_required_invalidation_evidence_unknown_stays_pending_and_confirmed_invalidation_wins():
    draft, originals = fixture_plan()
    body = {"synthetic_condition": "reviewed-material-domain", "known_at": AS_OF}
    guard = {"ref": "synthetic:guard", "hash": content_hash(body)}
    originals[guard["ref"]] = body
    draft["branches"][1]["applicability_domain"]["invalidation_refs"] = [guard]
    plan = core().build_event_scenario_action_plan(draft, originals)
    realization, verified = selection_inputs(plan)
    assert (
        core().evaluate_event_scenario_action_plan(
            plan,
            realization,
            [],
            verified,
            as_of=START,
        )["status"]
        == "pending_evidence"
    )
    evaluation = dict(
        evaluation_ref={"ref": "synthetic:evaluation", "hash": "f" * 64},
        assumption_ref=guard,
        state="invalidated",
        evaluated_at=START,
        known_at=START,
        evidence_refs=[],
    )
    assert (
        core().evaluate_event_scenario_action_plan(
            plan,
            realization,
            [evaluation],
            verified,
            as_of=START,
        )["status"]
        == "invalidated"
    )


def test_pre_event_quote_cannot_supply_entry_price_for_realized_post_event_selection():
    draft, originals = fixture_plan()
    plan = core().build_event_scenario_action_plan(draft, originals)
    realization, verified = selection_inputs(plan)
    verified["quote"]["known_at"] = AS_OF
    selected = core().evaluate_event_scenario_action_plan(
        plan, realization, [], verified, as_of=START
    )
    assert selected["status"] == "pending_evidence"
    assert selected["selected_candidate_ref"] is None


def test_exact_unrepresentable_marginal_keeps_valid_conditional_branches_without_rounding():
    draft, originals = fixture_plan()
    draft["outcome_given_occurrence"]["probabilities"] = [
        {"outcome_id": "A", "value": "0.600000000000000001"},
        {"outcome_id": "B", "value": "0.399999999999999999"},
    ]
    for branch in draft["branches"]:
        for inputs in branch["comparison_inputs_by_position"].values():
            inputs["event_probability"]["outcome_given_occurrence"] = deepcopy(
                draft["outcome_given_occurrence"]
            )
            resign(inputs)
            inputs["market_distribution"]["event_scenario_ref"] = deepcopy(
                inputs["bindings"]["event_probability"]
            )
            resign(inputs)
    plan = core().build_event_scenario_action_plan(draft, originals)
    assert len(plan["branches"]) == 2
    assert plan["marginal_distribution"] is None
    assert "marginal_exact_decimal_unrepresentable" in plan["unknown_reasons"]


def test_canonical_port_retains_lexical_utc_wire_hashes_across_prepare_and_select():
    draft, originals = fixture_plan()
    for branch in draft["branches"]:
        for inputs in branch["comparison_inputs_by_position"].values():
            for key in ("window_start", "window_end"):
                inputs["target"][key] = inputs["target"][key].replace("Z", ".000000Z")
            target_ref = deepcopy(inputs["bindings"]["target"])
            target_ref["hash"] = content_hash(inputs["target"])
            inputs["bindings"]["target"] = target_ref
            inputs["path_distribution"]["market_target_ref"] = deepcopy(target_ref)
            inputs["market_distribution"]["market_target_ref"] = deepcopy(target_ref)
            inputs["market_distribution"]["direction_distribution"]["target_ref"] = deepcopy(
                target_ref
            )
            resign(inputs)
    # Marginal has a different lexical clock and is therefore deliberately unavailable.
    plan = core().build_event_scenario_action_plan(draft, originals)
    realization, verified = selection_inputs(plan)
    ports = importlib.import_module(PACKAGE + "ports.event_scenario_action_plan")
    prepared = ports.prepare_event_scenario_action_plan(draft, originals)
    prepared_wire = prepared if isinstance(prepared, dict) else prepared.model_dump(mode="json")
    assert prepared_wire == plan
    selected = ports.select_event_scenario_action_plan(plan, realization, [], verified, as_of=START)
    selected_wire = selected if isinstance(selected, dict) else selected.model_dump(mode="json")
    assert selected_wire["status"] == "selected_preapproved_branch"


def test_missing_branch_stays_explicit_and_tampered_q_is_rejected_before_selection():
    draft, originals = fixture_plan(omit_b=True)
    plan = core().build_event_scenario_action_plan(draft, originals)
    assert plan["marginal_distribution"] is None
    assert "scenario_branch_unavailable:B" in plan["unknown_reasons"]
    assert plan["outcome_given_occurrence"]["probabilities"][1]["value"] == "0.40"
    realization, verified = selection_inputs(plan)
    assert (
        core().evaluate_event_scenario_action_plan(
            plan,
            realization,
            [],
            verified,
            as_of=START,
        )["status"]
        == "uncovered_requires_research"
    )
    draft, originals = fixture_plan()
    for inputs in draft["branches"][1]["comparison_inputs_by_position"].values():
        inputs["market_distribution"]["direction_distribution"]["probabilities"] = [
            {"outcome_id": "up", "value": "0.50"},
            {"outcome_id": "neutral", "value": "0.24"},
            {"outcome_id": "down", "value": "0.26"},
        ]
        resign(inputs)
    with pytest.raises(ValueError, match="comparison_incoherent_distribution"):
        core().build_event_scenario_action_plan(draft, originals)


def test_realization_identity_expiry_and_elapsed_domain_cannot_activate_uncovered_branch():
    draft, originals = fixture_plan()
    plan = core().build_event_scenario_action_plan(draft, originals)
    realization, verified = selection_inputs(plan)
    for field in ("family", "stage_id", "partition_ref"):
        actual, inputs = deepcopy(realization), deepcopy(verified)
        actual["event"][field] = (
            {"ref": "synthetic:wrong-partition", "hash": "0" * 64}
            if field.endswith("_ref")
            else "other"
        )
        inputs["realization_ref"]["hash"] = content_hash(actual)
        assert (
            core().evaluate_event_scenario_action_plan(
                plan,
                actual,
                [],
                inputs,
                as_of=START,
            )["status"]
            == "uncovered_requires_research"
        )
    late = deepcopy(verified)
    late["quote"].update(observed_at="2026-01-01T00:02:01Z", known_at="2026-01-01T00:02:01Z")
    assert (
        core().evaluate_event_scenario_action_plan(
            plan,
            realization,
            [],
            late,
            as_of="2026-01-01T00:02:01Z",
        )["status"]
        == "uncovered_requires_research"
    )
    old_review = deepcopy(verified)
    old_review["review_scope"]["valid_until"] = "2026-01-01T00:01:59Z"
    assert (
        core().evaluate_event_scenario_action_plan(
            plan,
            realization,
            [],
            old_review,
            as_of=START,
        )["status"]
        == "invalidated"
    )


def test_predicate_partition_rejects_overlap_and_endpoint_gap_without_renormalization():
    draft, _ = fixture_plan()
    predicates = deepcopy(draft["scenario_predicates"])
    predicates[0]["predicate"]["any_of"][0]["all_of"][0]["operator"] = "le"
    with pytest.raises(ValueError, match="event_plan_partition_overlap"):
        core().validate_event_scenario_partition(predicates, stage_id="release")
    predicates[1]["predicate"]["any_of"][0]["all_of"][0]["operator"] = "gt"
    predicates[0]["predicate"]["any_of"][0]["all_of"][0]["operator"] = "lt"
    with pytest.raises(ValueError, match="event_plan_partition_gap"):
        core().validate_event_scenario_partition(predicates, stage_id="release")
