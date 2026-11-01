"""Pure frozen event-plan composition/selection. No I/O or authentication authority.

Application readers must authenticate original provenance and actual native
prepare/reviewer history before passing inputs here. Local equality and hashes
prove identity only. Outputs are assumed unit research, never execution consent.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from itertools import product
from math import prod

from ..ports.exact_decimal_text import number
from .action_comparison import (
    _number,
    _partition,
    _ref,
    _utc,
    compare_action_candidates,
    content_hash,
)

POSITIONS = ("flat", "long", "short")
DIRECTIONS = ("up", "neutral", "down")
METRIC_KEYS = ("metric_id", "unit", "reference_period", "stage_id")
METHOD_ID = "event-scenario-assumed-unit-mean-net.v1"


class _ExactDecimalUnrepresentable(ValueError):
    """Exact mixture exceeds the shared wire's decimal precision."""


def _reference(name, body):
    return {"ref": "event-plan:" + name, "hash": content_hash(body)}


def _original(reference, originals):
    _ref(reference)
    body = originals.get(reference["ref"])
    if body is None or content_hash(body) != reference["hash"]:
        raise ValueError("event_plan_original_body_mismatch:" + reference["ref"])
    return body


def _decimal(value):
    text = number(value)
    try:
        _number(text)
    except ValueError as error:
        raise _ExactDecimalUnrepresentable from error
    return text


def _key(metric):
    return tuple(metric[name] for name in METRIC_KEYS)


def _matches(predicate, values):
    def atom(term):
        left, right = values[_key(term)], _number(term["value"])
        return {
            "eq": left == right,
            "lt": left < right,
            "le": left <= right,
            "gt": left > right,
            "ge": left >= right,
        }[term["operator"]]

    return any(all(atom(term) for term in clause["all_of"]) for clause in predicate["any_of"])


def validate_event_scenario_partition(rows: list[dict], *, stage_id: str) -> None:
    """Prove an exact bounded numeric partition, including every threshold point."""
    if not rows or len({row["scenario_id"] for row in rows}) != len(rows):
        raise ValueError("event_plan_scenario_ids_invalid")
    thresholds = {}
    metric_ids = {}
    for row in rows:
        clauses = row["predicate"]["any_of"]
        if not 1 <= len(clauses) <= 16:
            raise ValueError("event_plan_predicate_bound")
        for clause in clauses:
            if not 1 <= len(clause["all_of"]) <= 16:
                raise ValueError("event_plan_predicate_bound")
            for term in clause["all_of"]:
                identity = _key(term)
                if term["stage_id"] != stage_id or term["operator"] not in {
                    "eq",
                    "lt",
                    "le",
                    "gt",
                    "ge",
                }:
                    raise ValueError("event_plan_predicate_identity")
                if term["metric_id"] in metric_ids and metric_ids[term["metric_id"]] != identity:
                    raise ValueError("event_plan_metric_unit_period_conflict")
                metric_ids[term["metric_id"]] = identity
                thresholds.setdefault(identity, set()).add(_number(term["value"]))
    cells = []
    with localcontext() as context:
        context.prec = 100
        for bounds in thresholds.values():
            ordered = sorted(bounds)
            cells.append(
                [
                    ordered[0] - 1,
                    *ordered,
                    *(
                        (left + right) / 2
                        for left, right in zip(ordered, ordered[1:], strict=False)
                    ),
                    ordered[-1] + 1,
                ]
            )
        if prod(len(axis) for axis in cells) > 4096:
            raise ValueError("event_plan_partition_proof_bound")
        for cell in product(*cells):
            values = dict(zip(thresholds, cell, strict=True))
            count = sum(_matches(row["predicate"], values) for row in rows)
            if count != 1:
                raise ValueError(
                    "event_plan_partition_overlap" if count > 1 else "event_plan_partition_gap"
                )


def _comparison_bundle(inputs, name):
    comparison = compare_action_candidates(inputs)
    return dict(
        inputs_ref=_reference(name + ":inputs", inputs),
        comparison=comparison,
        comparison_ref=_reference(name + ":comparison", comparison),
    )


def _candidate_refs(comparison, name):
    return [
        _reference(name + ":" + row["candidate_id"], row) for row in comparison["candidate_results"]
    ]


def _target_shape(target):
    return {
        key: value
        for key, value in target.items()
        if key not in {"target_id", "conditioning_ref", "target_family_ref"}
    }


def _marginal(draft, branches, probabilities):
    target = draft["marginal_target"]
    if target is None or draft["marginal_fill_assumption_ref"] is None:
        return None, "marginal_target_or_fill_assumption_unavailable"
    by_scenario = {row["scenario_id"]: row for row in branches}
    if set(by_scenario) != set(probabilities) or len(branches) != len(probabilities):
        return None, "marginal_missing_or_ambiguous_scenario_branch"
    if target["conditioning_ref"] != draft["event"]["event_definition_ref"]:
        raise ValueError("event_plan_marginal_conditioning_mismatch")
    if any(_target_shape(row["target"]) != _target_shape(target) for row in branches):
        return None, "marginal_targets_incompatible"
    # Even unit assumptions need one common frozen economic basis for a mixture.
    for position in POSITIONS:
        source = branches[0]["comparison_inputs_by_position"][position]
        if any(
            any(
                row["comparison_inputs_by_position"][position][key] != source[key]
                for key in (
                    "cost_policy",
                    "unit_notional_usdt",
                    "utility_policy",
                    "price_bridge",
                    "account_snapshot",
                    "candidate_leverage",
                )
            )
            for row in branches
        ):
            return None, "marginal_economic_basis_incompatible"
    with localcontext() as context:
        context.prec = 100
        direction = dict.fromkeys(DIRECTIONS, Decimal(0))
        atoms = []
        for branch in branches:
            q = _partition(branch["market_distribution"]["direction_distribution"], draft["as_of"])
            if q is None:
                return None, "marginal_conditional_q_unavailable"
            weight = probabilities[branch["scenario_id"]]
            for key in DIRECTIONS:
                direction[key] += weight * q[key]
            for atom in branch["path_distribution"]["scenarios"]:
                atoms.append(
                    dict(
                        scenario_id=branch["scenario_id"] + ":" + atom["scenario_id"],
                        probability=_decimal(weight * _number(atom["probability"])),
                        terminal_return_fraction=atom["terminal_return_fraction"],
                    )
                )
        values = [dict(outcome_id=key, value=_decimal(direction[key])) for key in DIRECTIONS]
    derivation = dict(
        basis="conditional_on_event",
        outcome_given_occurrence=draft["outcome_given_occurrence"],
        branch_refs=[row["branch_ref"] for row in branches],
        marginal_fill_assumption_ref=draft["marginal_fill_assumption_ref"],
    )
    derivation_ref = _reference(draft["plan_id"] + ":marginal-derivation", derivation)
    comparisons = {}
    for position in POSITIONS:
        inputs = deepcopy(branches[0]["comparison_inputs_by_position"][position])
        inputs["target"] = deepcopy(target)
        inputs["bindings"]["target"] = deepcopy(draft["marginal_target_ref"])
        paths = inputs["path_distribution"]
        paths.update(
            market_target_ref=deepcopy(draft["marginal_target_ref"]),
            conditioning_ref=deepcopy(target["conditioning_ref"]),
            scenarios=deepcopy(atoms),
            fill_weight_assumption_ref=deepcopy(draft["marginal_fill_assumption_ref"]),
        )
        inputs["bindings"]["paths"] = _reference(draft["plan_id"] + ":marginal-paths", paths)
        market = inputs["market_distribution"]
        market.update(
            market_target_ref=deepcopy(draft["marginal_target_ref"]),
            conditioning_ref=deepcopy(target["conditioning_ref"]),
            path_distribution_ref=deepcopy(inputs["bindings"]["paths"]),
        )
        market["direction_distribution"].update(
            target_ref=deepcopy(draft["marginal_target_ref"]),
            conditioning_ref=deepcopy(target["conditioning_ref"]),
            probabilities=deepcopy(values),
            source_ref=derivation_ref,
        )
        inputs["bindings"]["market_distribution"] = _reference(
            draft["plan_id"] + ":marginal-q", market
        )
        comparisons[position] = _comparison_bundle(
            inputs, draft["plan_id"] + ":marginal:" + position
        )
    return dict(
        target=deepcopy(target),
        target_ref=deepcopy(draft["marginal_target_ref"]),
        conditioning_ref=deepcopy(target["conditioning_ref"]),
        basis="conditional_on_event",
        probabilities=values,
        derivation_ref=derivation_ref,
        comparisons_by_position=comparisons,
    ), None


def build_event_scenario_action_plan(draft: dict, authenticated_originals: dict[str, dict]) -> dict:
    """Compose exact identities and unit comparisons; caller authenticates originals."""
    if draft["schema_version"] != "event-scenario-action-plan-draft.v1":
        raise ValueError("event_plan_draft_version")
    as_of, known, freeze = (_utc(draft[key]) for key in ("as_of", "known_at_max", "freeze_before"))
    event, context = draft["event"], draft["context"]
    scheduled = _utc(event["scheduled_at"])
    if not known <= as_of < freeze < scheduled:
        raise ValueError("event_plan_freeze_clock")
    validate_event_scenario_partition(draft["scenario_predicates"], stage_id=event["stage_id"])
    for key in ("schedule_ref", "event_definition_ref", "observation_rule_ref", "partition_ref"):
        _original(event[key], authenticated_originals)
    for row in draft["scenario_predicates"]:
        if _original(row["definition_ref"], authenticated_originals) != row["predicate"]:
            raise ValueError("event_plan_predicate_original_mismatch")
    method = _original(draft["method_ref"], authenticated_originals)
    if (
        method.get("schema_version") != "event-scenario-comparison-method.v1"
        or method.get("method_id") != METHOD_ID
        or method.get("method_code_hash") != draft["method_code_hash"]
        or method["method_code_hash"]
        != content_hash(
            {
                "base_method_code_hash": method["base_method_code_hash"],
                "implementation_dependencies": method["implementation_dependencies"],
            }
        )
    ):
        raise ValueError("event_plan_method_identity")
    occurrence = _partition(draft["occurrence"], draft["as_of"])
    probabilities = _partition(draft["outcome_given_occurrence"], draft["as_of"])
    if occurrence is not None and set(occurrence) != {"occurs", "not_occurs"}:
        raise ValueError("event_plan_occurrence_partition")
    scenarios = {row["scenario_id"]: row for row in draft["scenario_predicates"]}
    if probabilities is None or set(probabilities) != set(scenarios):
        raise ValueError("event_plan_scenario_probability_partition")
    if draft["outcome_given_occurrence"]["conditioning_ref"] != event["event_definition_ref"]:
        raise ValueError("event_plan_p_conditioning")
    if len({row["branch_id"] for row in draft["branches"]}) != len(draft["branches"]):
        raise ValueError("event_plan_branch_ids")
    branches = []
    for raw in draft["branches"]:
        branch = deepcopy(raw)
        scenario = scenarios.get(branch["scenario_id"])
        if scenario is None:
            raise ValueError("event_plan_branch_scenario_unknown")
        condition = scenario["definition_ref"]
        sample = branch["comparison_inputs_by_position"]["flat"]
        target, bindings = sample["target"], sample["bindings"]
        anchor = target["anchor"]
        if anchor not in {"scheduled_pre_release", "post_release_response"}:
            raise ValueError("event_plan_fixed_event_clock_required")
        start = scheduled + timedelta(seconds=-60 if anchor == "scheduled_pre_release" else 60)
        if _utc(target["window_start"]) != start:
            raise ValueError("event_plan_event_anchor_mismatch")
        domain = branch["applicability_domain"]
        if not (
            start
            <= _utc(branch["applicable_from"])
            < _utc(branch["valid_until"])
            <= _utc(domain["valid_until"])
            <= _utc(target["window_end"])
        ):
            raise ValueError("event_plan_branch_applicability_clock")
        if _number(domain["price_ratio_min"]) > _number(domain["price_ratio_max"]) or (
            domain["maximum_elapsed_seconds"] and not domain["recomparison_allowed"]
        ):
            raise ValueError("event_plan_bridge_domain_invalid")
        if target["conditioning_ref"] != condition:
            raise ValueError("event_plan_branch_conditioning_mismatch")
        comparisons, candidates = {}, {}
        for position in POSITIONS:
            inputs = branch["comparison_inputs_by_position"][position]
            if (
                inputs["schema_version"] != "decision-method-inputs.v1"
                or inputs["target"] != target
                or inputs["position_branch"] != position
                or inputs["forecast_as_of"] != draft["as_of"]
                or inputs["comparison_as_of"] != draft["as_of"]
                or _utc(inputs["forecast_known_at_max"]) > known
                or _utc(inputs["comparison_known_at_max"]) > known
                or inputs["instrument_id"] != context["instrument_id"]
                or inputs["scope_id"] != context["scope_id"]
                or target["horizon"] != context["horizon"]
                or any(
                    inputs["bindings"][key] != context[key + "_ref"]
                    for key in ("instrument", "settings", "account", "position", "utility")
                )
                or inputs["bindings"]["method"] != method["base_method_ref"]
                or inputs["event_probability"]["occurrence_distribution"] != draft["occurrence"]
                or inputs["event_probability"]["outcome_given_occurrence"]
                != draft["outcome_given_occurrence"]
                or inputs["event_probability"]["event_definition_ref"]
                != event["event_definition_ref"]
                or inputs["event_probability"]["observation_rule_ref"]
                != event["observation_rule_ref"]
                or inputs["event_probability"]["joint_partition_ref"] != event["partition_ref"]
                or inputs["bindings"]["cost"] != branch["cost_ref"]
                or inputs["bindings"]["account"] != branch["account_ref"]
                or inputs["path_distribution"]["fill_weight_assumption_ref"]
                != branch["fill_assumption_ref"]
                or inputs["market_distribution"] != sample["market_distribution"]
                or inputs["path_distribution"] != sample["path_distribution"]
            ):
                raise ValueError("event_plan_branch_inputs_mismatch")
            name = draft["plan_id"] + ":" + branch["branch_id"] + ":" + position
            bundle = _comparison_bundle(inputs, name)
            comparisons[position] = bundle
            candidates[position] = _candidate_refs(bundle["comparison"], name)
        for key in ("cost_ref", "funding_ref", "margin_ref", "fill_assumption_ref", "account_ref"):
            if branch[key] is not None:
                _original(branch[key], authenticated_originals)
        for reference in branch["condition_assumption_refs"] + domain["invalidation_refs"]:
            _original(reference, authenticated_originals)
        branch.update(
            conditioning_ref=deepcopy(condition),
            target=deepcopy(target),
            target_ref=deepcopy(bindings["target"]),
            market_distribution=deepcopy(sample["market_distribution"]),
            market_distribution_ref=deepcopy(bindings["market_distribution"]),
            path_distribution=deepcopy(sample["path_distribution"]),
            path_distribution_ref=deepcopy(bindings["paths"]),
            comparison_inputs_ref=_reference(
                draft["plan_id"] + ":" + branch["branch_id"] + ":inputs",
                branch["comparison_inputs_by_position"],
            ),
            comparisons_by_position=comparisons,
            reviewed_candidate_refs_by_position=candidates,
        )
        branch["branch_ref"] = _reference(draft["plan_id"] + ":" + branch["branch_id"], branch)
        branches.append(branch)
    if draft["marginal_target"] is not None:
        if (
            _original(draft["marginal_target_ref"], authenticated_originals)
            != draft["marginal_target"]
        ):
            raise ValueError("event_plan_marginal_target_original")
    if draft["marginal_fill_assumption_ref"] is not None:
        _original(draft["marginal_fill_assumption_ref"], authenticated_originals)
    try:
        marginal, reason = _marginal(draft, branches, probabilities)
    except _ExactDecimalUnrepresentable:
        marginal, reason = None, "marginal_exact_decimal_unrepresentable"
    output = {
        key: deepcopy(value)
        for key, value in draft.items()
        if key
        not in {
            "schema_version",
            "branches",
            "marginal_target",
            "marginal_target_ref",
            "marginal_fill_assumption_ref",
        }
    }
    output.update(
        schema_version="event-scenario-action-plan.v1",
        branches=branches,
        marginal_distribution=marginal,
        unknown_reasons=[] if reason is None else [reason],
    )
    for scenario in set(scenarios) - {row["scenario_id"] for row in branches}:
        output["unknown_reasons"].append("scenario_branch_unavailable:" + scenario)
    output["unknown_reasons"].sort()
    return output


def evaluate_event_scenario_action_plan(
    plan: dict,
    realization: dict | None,
    condition_evaluations: list[dict],
    verified_market_account_cost_inputs: dict,
    *,
    as_of: str,
) -> dict:
    """Select within an authenticated review's frozen domain; never authenticates it."""
    inputs, now = verified_market_account_cost_inputs, _utc(as_of)
    _ref(inputs["plan_ref"])
    if inputs["plan_ref"]["hash"] != content_hash(plan):
        raise ValueError("event_plan_selection_plan_hash_mismatch")
    scope = inputs["review_scope"]
    output = dict(
        schema_version="scenario-branch-selection.v1",
        selection_key="0" * 64,
        plan_ref=deepcopy(inputs["plan_ref"]),
        branch_ref=None,
        realization_ref=deepcopy(inputs["realization_ref"]),
        review_authorization_ref=deepcopy(inputs["review_authorization_ref"]),
        condition_evaluation_refs=[
            deepcopy(row["evaluation_ref"]) for row in condition_evaluations
        ],
        quote_ref=deepcopy(inputs["quote"]["quote_ref"]) if inputs["quote"] is not None else None,
        **{
            key: deepcopy(inputs[key])
            for key in (
                "account_ref",
                "cost_ref",
                "funding_ref",
                "margin_ref",
                "fill_assumption_ref",
            )
        },
        domain_input_refs=[deepcopy(row["input_ref"]) for row in inputs["domain_values"]],
        selected_candidate_ref=None,
        selected_comparison_ref=None,
        selected_action_id=None,
        recomparison=None,
        unit_expected_net_if_assumed_fills_usdt=None,
        original_forecast_cutoff=plan["as_of"],
        observed_at=as_of,
        unknown_reasons=[],
        status="pending_evidence",
        execution_authorized=False,
        fill_probability=None,
        opportunity_expected_net_usdt=None,
        executable_contracts=None,
        account_feasibility="unknown",
        qualification="research_trial",
    )

    def finish(status, reason=None):
        output["status"] = status
        if reason is not None:
            output["unknown_reasons"].append(reason)
        identity = {
            key: value
            for key, value in output.items()
            if key
            not in {"selection_key", "observed_at", "recomparison", "selected_comparison_ref"}
        }
        output["selection_key"] = content_hash(identity)
        return output

    if (
        scope["plan_ref"] != inputs["plan_ref"]
        or scope["method_ref"] != plan["method_ref"]
        or scope["method_code_hash"] != plan["method_code_hash"]
        or scope["freeze_before"] != plan["freeze_before"]
        or not _utc(plan["as_of"]) <= _utc(scope["reviewed_at"]) < _utc(plan["freeze_before"])
    ):
        return finish("uncovered_requires_research", "review_scope_or_freeze_mismatch")
    if scope["verdict"] != "approved" or now >= _utc(scope["valid_until"]):
        return finish("invalidated", "review_rejected_or_expired")
    if realization is None:
        return finish("pending_evidence", "authenticated_realization_unavailable")
    if inputs["realization_ref"] is None or inputs["realization_ref"]["hash"] != content_hash(
        realization
    ):
        raise ValueError("event_plan_realization_hash_mismatch")
    if realization["event"] != plan["event"]:
        return finish(
            "uncovered_requires_research", "event_family_stage_partition_or_definition_mismatch"
        )
    release, public, received, known = (
        _utc(realization[key]) for key in ("released_at", "public_at", "received_at", "known_at")
    )
    if not release <= public <= received <= known <= now:
        return finish("pending_evidence", "realization_not_known_at_selection")
    if release <= _utc(plan["freeze_before"]):
        return finish("invalidated", "event_released_before_plan_freeze")
    if release != _utc(plan["event"]["scheduled_at"]):
        return finish("uncovered_requires_research", "actual_release_changed_fixed_event_clock")
    values = {_key(row): _number(row["value"]) for row in realization["metrics"]}
    if len(values) != len(realization["metrics"]):
        return finish("uncovered_requires_research", "duplicate_realization_metric")
    needed = {
        _key(term)
        for row in plan["scenario_predicates"]
        for clause in row["predicate"]["any_of"]
        for term in clause["all_of"]
    }
    if not needed <= values.keys():
        if any(row["metric_id"] in {key[0] for key in needed} for row in realization["metrics"]):
            return finish("uncovered_requires_research", "realization_metric_identity_mismatch")
        return finish("pending_evidence", "required_realization_metrics_unavailable")
    matching = [
        row["scenario_id"]
        for row in plan["scenario_predicates"]
        if _matches(row["predicate"], values)
    ]
    branches = [row for row in plan["branches"] if row["scenario_id"] in matching]
    if len(matching) != 1 or len(branches) != 1:
        return finish("uncovered_requires_research", "scenario_branch_missing_or_conflicting")
    branch = branches[0]
    output["branch_ref"] = deepcopy(branch["branch_ref"])
    if branch["branch_ref"]["hash"] != content_hash(
        {key: value for key, value in branch.items() if key != "branch_ref"}
    ):
        raise ValueError("event_plan_branch_hash_mismatch")
    domain = branch["applicability_domain"]
    if now >= min(_utc(branch["valid_until"]), _utc(domain["valid_until"])):
        return finish("invalidated", "branch_expired")
    scope_rows = [row for row in scope["branches"] if row["branch_ref"] == branch["branch_ref"]]
    if (
        len(scope_rows) != 1
        or _utc(scope["valid_until"]) < _utc(branch["valid_until"])
        or _utc(scope_rows[0]["valid_until"]) < _utc(branch["valid_until"])
        or _utc(scope_rows[0]["applicable_from"]) > _utc(branch["applicable_from"])
    ):
        return finish("uncovered_requires_research", "review_does_not_cover_branch_window")
    required = branch["condition_assumption_refs"] + domain["invalidation_refs"]
    for condition in required:
        evaluations = [
            row
            for row in condition_evaluations
            if row["assumption_ref"] == condition
            and _utc(row["known_at"]) <= now
            and _utc(row["evaluated_at"]) <= now
        ]
        if any(row["state"] == "invalidated" for row in evaluations):
            return finish("invalidated", "confirmed_condition_invalidation")
    for condition in required:
        evaluations = [
            row
            for row in condition_evaluations
            if row["assumption_ref"] == condition
            and _utc(row["known_at"]) <= now
            and _utc(row["evaluated_at"]) <= now
        ]
        if len(evaluations) != 1 or evaluations[0]["state"] != "satisfied":
            return finish("pending_evidence", "required_condition_unknown_or_conflicting")
    if now < _utc(branch["applicable_from"]):
        return finish("pending_evidence", "reviewed_target_not_started")
    if any(
        inputs[key] != branch[key]
        for key in ("cost_ref", "funding_ref", "margin_ref", "fill_assumption_ref", "account_ref")
    ) or any(inputs[key] != plan["context"][key] for key in ("settings_ref", "position_ref")):
        return finish("uncovered_requires_research", "material_economic_domain_changed")
    if inputs["requires_conditional_fill"]:
        return finish(
            "unsupported_fill_distribution", "conditional_entry_fill_distribution_unavailable"
        )
    quote = inputs["quote"]
    if (
        quote is None
        or not _utc(quote["observed_at"]) <= _utc(quote["known_at"]) <= now
        or _utc(quote["observed_at"]) < _utc(branch["target"]["window_start"])
        or (now - _utc(quote["observed_at"])).total_seconds() > domain["maximum_quote_age_seconds"]
    ):
        return finish("pending_evidence", "required_quote_unavailable")
    elapsed = (now - _utc(branch["target"]["window_start"])).total_seconds()
    ratio = _number(quote["comparison_to_forecast_price_ratio"])
    if (
        elapsed < 0
        or elapsed > domain["maximum_elapsed_seconds"]
        or not _number(domain["price_ratio_min"]) <= ratio <= _number(domain["price_ratio_max"])
        or ((elapsed > 0 or ratio != 1) and not domain["recomparison_allowed"])
    ):
        return finish(
            "uncovered_requires_research", "elapsed_time_or_price_bridge_outside_reviewed_domain"
        )
    for variable in domain["variables"]:
        rows = [
            row
            for row in inputs["domain_values"]
            if all(row[key] == variable[key] for key in ("category", "name", "unit"))
        ]
        if len(rows) != 1 or _utc(rows[0]["known_at"]) > now:
            return finish("pending_evidence", "required_domain_input_unavailable")
        value = _number(rows[0]["value"])
        if (
            now >= _utc(variable["valid_until"])
            or variable["minimum"] is not None
            and value < _number(variable["minimum"])
            or variable["maximum"] is not None
            and value > _number(variable["maximum"])
        ):
            return finish("uncovered_requires_research", "reviewed_domain_bound_exceeded")
    position = inputs["position_branch"]
    original_inputs = branch["comparison_inputs_by_position"][position]
    bundle = branch["comparisons_by_position"][position]
    if (
        bundle["inputs_ref"]["hash"] != content_hash(original_inputs)
        or bundle["comparison_ref"]["hash"] != content_hash(bundle["comparison"])
        or compare_action_candidates(original_inputs) != bundle["comparison"]
    ):
        raise ValueError("event_plan_comparison_original_mismatch")
    if quote["forecast_price_ref"] != original_inputs["price_bridge"]["forecast_price_ref"]:
        return finish("uncovered_requires_research", "forecast_price_bridge_original_changed")
    comparison = bundle["comparison"]
    if elapsed > 0 or ratio != 1:
        updated = deepcopy(original_inputs)
        updated.update(comparison_as_of=as_of, comparison_known_at_max=quote["known_at"])
        updated["price_bridge"] = dict(
            forecast_price_ref=deepcopy(quote["forecast_price_ref"]),
            comparison_price_ref=deepcopy(quote["quote_ref"]),
            comparison_to_forecast_price_ratio=_decimal(ratio),
            known_at=quote["known_at"],
        )
        comparison = compare_action_candidates(updated)
        output["recomparison"] = comparison
        output["selected_comparison_ref"] = _reference(
            branch["branch_id"] + ":recomparison", comparison
        )
    else:
        output["selected_comparison_ref"] = deepcopy(bundle["comparison_ref"])
    action = comparison["selected_action_id"]
    original_rows = bundle["comparison"]["candidate_results"]
    candidates = branch["reviewed_candidate_refs_by_position"][position]
    candidate = next(
        (
            reference
            for row, reference in zip(original_rows, candidates, strict=True)
            if row["candidate_id"] == action and reference["hash"] == content_hash(row)
        ),
        None,
    )
    if candidate is None or candidate not in scope_rows[0]["candidate_refs"]:
        output["selected_comparison_ref"] = None
        output["recomparison"] = None
        return finish("uncovered_requires_research", "selected_candidate_not_reviewed")
    row = next(row for row in comparison["candidate_results"] if row["candidate_id"] == action)
    output.update(
        selected_candidate_ref=deepcopy(candidate),
        selected_action_id=action,
        unit_expected_net_if_assumed_fills_usdt=row["unit_expected_net_if_assumed_fills_usdt"],
        unknown_reasons=sorted(set(branch["unknown_reasons"] + comparison["unknown_reasons"])),
    )
    return finish("selected_preapproved_branch")
