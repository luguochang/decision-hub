"""Internal assumed-unit comparison adapter; no I/O, clock or authority.

Inputs and output are internal dictionaries with canonical-shaped fields.
Before production use, callers must parse the generated canonical DTOs and
independently authenticate frozen source/settings/account/forecast originals.
Only explicit complete paths and assumed fixed unit costs are supported; a
market direction probability cannot supply amplitudes, fills or account size.
"""

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from .perpetual_accounting import AssumedScenario, compare_assumed_unit_branch

D = Decimal
SECONDS = {"4h": 14400, "24h": 86400, "72h": 259200}


def content_hash(value):
    """Canonical JSON content identity only; never source authentication."""
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _number(value, *, nonnegative=False):
    if not isinstance(value, str) or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?",
        value,
    ):
        raise ValueError("comparison_decimal_text_required")
    number = D(value)
    if nonnegative and number < 0:
        raise ValueError("comparison_negative_amount")
    return number


def _wire(value):
    # Preserve comparisons at accounting precision; only presentation is rounded.
    with localcontext() as context:
        context.prec = 80
        value = value.quantize(D("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
        text = format(value.normalize() if value else D("0"), "f")
        _number(text)
        return text


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z",
        value,
    ):
        raise ValueError("comparison_UTC_required")
    return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(UTC)


def _ref(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"ref", "hash"}
        or not isinstance(value["ref"], str)
        or not 0 < len(value["ref"]) <= 256
        or value["ref"].strip() != value["ref"]
        or not isinstance(value["hash"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["hash"])
    ):
        raise ValueError("comparison_ref_hash_required")
    return value


def _partition(estimate, as_of):
    _ref(estimate["target_ref"])
    if estimate["source_ref"] is not None:
        _ref(estimate["source_ref"])
    if estimate["as_of"] != as_of:
        raise ValueError("comparison_probability_cutoff_mismatch")
    if estimate["kind"] not in {"subjective", "model_estimate", "calibrated", "unavailable"}:
        raise ValueError("comparison_probability_kind_invalid")
    if estimate["kind"] == "calibrated" and estimate["calibration_ref"] is None:
        raise ValueError("comparison_calibration_original_required")
    values = estimate["probabilities"]
    if values is None:
        if estimate["kind"] != "unavailable":
            raise ValueError("comparison_unknown_probability_kind")
        return None
    if estimate["source_ref"] is None:
        raise ValueError("comparison_probability_source_required")
    if not values or len({row["outcome_id"] for row in values}) != len(values):
        raise ValueError("comparison_probability_partition_invalid")
    result = {row["outcome_id"]: _number(row["value"], nonnegative=True) for row in values}
    if any(value > 1 for value in result.values()) or sum(result.values(), D(0)) != 1:
        raise ValueError("comparison_probability_mass_not_one")
    return result


def _policy_limit(limit, equity):
    if limit is None:
        return None
    amount = _number(limit["value"], nonnegative=True)
    if limit["unit"] == "USDT":
        return amount
    if limit["unit"] != "equity_fraction" or amount > 1:
        raise ValueError("comparison_risk_limit_unit_invalid")
    if equity is None or equity <= 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return equity * amount


def compare_action_candidates(snapshot: dict) -> dict:
    """Versioned DecisionMethodInputs JSON -> ActionComparisonV2, unit research.

    Schema/codegen validation and authenticated original readers belong before
    this pure call. References, hashes, scope/target/cutoff and arithmetic are
    checked here; equality/hashing cannot establish ownership or qualification.
    Unknown fill/account never receive a numeric fill probability/order size.
    """
    version = snapshot["schema_version"]
    if version not in {"decision-method-inputs.v1", "decision-method-inputs.v2"}:
        raise ValueError("comparison_version_required")
    bindings, target = snapshot["bindings"], snapshot["target"]
    for reference in bindings.values():
        if reference is not None:
            _ref(reference)
    for name, body in (
        ("target", target),
        ("event_probability", snapshot["event_probability"]),
        ("market_distribution", snapshot["market_distribution"]),
        ("paths", snapshot["path_distribution"]),
        ("cost", snapshot["cost_policy"]),
    ):
        if bindings[name]["hash"] != content_hash(body):
            raise ValueError("comparison_input_body_hash_mismatch:" + name)
    horizon = target["horizon"]
    instrument = snapshot["instrument_id"]
    if (
        horizon not in SECONDS
        or instrument not in {"okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"}
        or snapshot["scope_id"] != "crypto:perp:" + instrument + ":trade:1m"
        or (
            version == "decision-method-inputs.v1"
            and (target.get("schema_version") != "target-spec.v1"
                 or target.get("kind") != "market_return" or target["price_basis"] != "trade")
        )
        or (
            version == "decision-method-inputs.v2"
            and (target.get("schema_version") != "publication-bound-target.v1"
                 or target["price_basis"] != "trade_1m_close"
                 or target.get("duration_seconds") != SECONDS[horizon])
        )
        or target["instrument_ref"] != bindings["instrument"]
    ):
        raise ValueError("comparison_new_perp_target_required")
    forecast_as_of, as_of = snapshot["forecast_as_of"], snapshot["comparison_as_of"]
    start, end = _utc(target["window_start"]), _utc(target["window_end"])
    if version == "decision-method-inputs.v2":
        for name in ("candidate_target_ref", "anchor_rule_ref", "clock_policy_ref", "method_ref"):
            _ref(target[name])
        assigned = _utc(target["assigned_at"])
        if start != assigned.replace(second=0, microsecond=0) + timedelta(seconds=60):
            raise ValueError("comparison_bound_target_clock_invalid")
    if (
        _utc(snapshot["forecast_known_at_max"]) > _utc(forecast_as_of)
        or _utc(snapshot["comparison_known_at_max"]) > _utc(as_of)
        or not _utc(forecast_as_of) <= _utc(as_of) < end
        or _utc(forecast_as_of) > start
        or (end - start).total_seconds() != SECONDS[horizon]
    ):
        raise ValueError("comparison_target_clock_mismatch")
    event, market = snapshot["event_probability"], snapshot["market_distribution"]
    _partition(event["occurrence_distribution"], forecast_as_of)
    _partition(event["outcome_given_occurrence"], forecast_as_of)
    direction = _partition(market["direction_distribution"], forecast_as_of)
    if direction is not None and set(direction) != {"up", "neutral", "down"}:
        raise ValueError("comparison_direction_partition_invalid")
    if (
        market["instrument_ref"] != bindings["instrument"]
        or market["market_target_ref"] != bindings["target"]
        or market["path_distribution_ref"] != bindings["paths"]
        or market["event_scenario_ref"] != bindings["event_probability"]
        or market["direction_distribution"]["target_ref"] != bindings["target"]
        or market["direction_distribution"]["conditioning_ref"] != target["conditioning_ref"]
        or market["conditioning_ref"] != target["conditioning_ref"]
    ):
        raise ValueError("comparison_market_target_binding_mismatch")
    paths = snapshot["path_distribution"]
    if (
        paths["kind"] != "assumed_complete_paths"
        or paths["market_target_ref"] != bindings["target"]
        or paths["conditioning_ref"] != target["conditioning_ref"]
    ):
        raise ValueError("comparison_explicit_path_assumption_required")
    _ref(paths["fill_weight_assumption_ref"])
    forecast_scenarios = tuple(
        AssumedScenario(
            row["scenario_id"],
            _number(row["probability"], nonnegative=True),
            _number(row["terminal_return_fraction"]),
        )
        for row in paths["scenarios"]
    )
    # The existing calculator rejects duplicate IDs, bad mass and invalid prices.
    costs, utility = snapshot["cost_policy"], snapshot["utility_policy"]
    utility_body = {key: value for key, value in utility.items() if key != "hash"}
    if (
        utility["policy_ref"] != bindings["utility"]["ref"]
        or utility["hash"] != bindings["utility"]["hash"]
        or content_hash(utility_body) != utility["hash"]
    ):
        raise ValueError("comparison_frozen_utility_mismatch")
    if utility["objective"] != "mean_net" or _number(utility["risk_penalty"]) != 0:
        raise ValueError("comparison_utility_objective_unsupported")
    account, equity = snapshot["account_snapshot"], None
    if account is not None:
        if bindings["account"] is None or content_hash(account) != bindings["account"]["hash"]:
            raise ValueError("comparison_account_snapshot_mismatch")
        if _utc(account["as_of"]) > _utc(as_of):
            raise ValueError("comparison_account_not_known_at_cutoff")
        if account["source"] != "unknown" and account["equity"] is not None:
            equity = _number(account["equity"])
    # V2 distinguishes the authenticated snapshot projection from its underlying
    # ledger original. Settings bind utility to that original, not a hash of the
    # whole projection. Keep the established V1 contract unchanged.
    utility_account = (
        account["original"] if account is not None else None
    ) if version == "decision-method-inputs.v2" else bindings["account"]
    if utility["account_ref"] != utility_account:
        raise ValueError("comparison_utility_account_mismatch")
    bridge = snapshot["price_bridge"]
    _ref(bridge["forecast_price_ref"])
    _ref(bridge["comparison_price_ref"])
    ratio = _number(bridge["comparison_to_forecast_price_ratio"], nonnegative=True)
    if (
        ratio <= 0
        or _utc(bridge["known_at"]) > _utc(as_of)
        or (ratio != 1 and bridge["forecast_price_ref"] == bridge["comparison_price_ref"])
    ):
        raise ValueError("comparison_price_bridge_invalid")
    # Ratio 1 may reuse the original authenticated price ref for settings/cost
    # changes. No fresh quote/source is demanded merely because time advanced.
    with localcontext() as context:
        context.prec = 50
        scenarios = tuple(
            AssumedScenario(
                row.scenario_id,
                row.probability,
                (1 + row.terminal_return_fraction) / ratio - 1,
            )
            for row in forecast_scenarios
        )
    if costs["kind"] != "assumed_fixed_unit_cash":
        raise ValueError("comparison_fixed_unit_cost_model_required")
    result = compare_assumed_unit_branch(
        position_branch=snapshot["position_branch"],
        unit_notional_usdt=_number(snapshot["unit_notional_usdt"], nonnegative=True),
        scenarios=scenarios,
        assumptions_ref=bindings["paths"]["ref"],
        entry_trade_cost_usdt=_number(costs["entry_trade_cost_usdt"], nonnegative=True),
        exit_trade_cost_usdt=_number(costs["exit_trade_cost_usdt"], nonnegative=True),
        long_funding_cash_usdt=_number(costs["long_funding_cash_usdt"]),
        max_gross_notional_usdt=_policy_limit(utility["max_gross_notional"], equity),
        max_listed_path_loss_usdt=_policy_limit(utility["max_planned_loss"], equity),
        trade_cost_multiplier=_number(costs["trade_cost_multiplier"], nonnegative=True),
    )
    if paths["supports_market_direction"]:
        threshold = _number(target["threshold_fraction"], nonnegative=True)
        grouped = {"up": D(0), "neutral": D(0), "down": D(0)}
        for row in forecast_scenarios:
            label = (
                "up"
                if row.terminal_return_fraction > threshold
                else "down"
                if row.terminal_return_fraction < -threshold
                else "neutral"
            )
            grouped[label] += row.probability
        if direction is None or grouped != direction:
            raise ValueError("comparison_incoherent_distribution")
    ids = {row.candidate_id for row in result.candidate_results}
    if set(snapshot["action_target_refs"]) != ids:
        raise ValueError("comparison_action_target_set_mismatch")
    if len(
        {tuple(sorted(_ref(value).items())) for value in snapshot["action_target_refs"].values()}
    ) != len(ids):
        raise ValueError("comparison_action_targets_not_distinct")
    rows, excluded = [], set()
    unknown = list(result.unknown_constraints)
    allowed_sides = utility["allowed_sides"]
    if allowed_sides is None:
        unknown.append("allowed_sides_unknown")
    if utility["tail_constraints"]:
        unknown.append("tail_constraint_reader_required")
    if utility["margin_buffer_policy"] is not None:
        unknown.append("margin_buffer_reader_required")
    leverage = snapshot["candidate_leverage"]
    if utility["allowed_leverage"] is not None and leverage is None:
        unknown.append("candidate_leverage_unknown")
    if utility["position_scenario"] not in {"unknown", snapshot["position_branch"]}:
        unknown.append("alternative_position_scenario_not_account_order")
    for row in result.candidate_results:
        violations = list(row.unit_constraint_violations)
        protective = row.candidate_id in {"reduce_half", "close"}
        if (
            allowed_sides is not None
            and row.target_side not in allowed_sides
            and row.post_action_gross_notional_usdt > 0
            and not protective
        ):
            violations.append("target_side_not_allowed")
        if (
            utility["allowed_leverage"] is not None
            and leverage is not None
            and row.candidate_id.startswith("open_")
            and _number(leverage, nonnegative=True)
            not in {_number(value, nonnegative=True) for value in utility["allowed_leverage"]}
        ):
            violations.append("candidate_leverage_not_allowed")
        if violations:
            excluded.add(row.candidate_id)
        action_target = _ref(snapshot["action_target_refs"][row.candidate_id])
        unchanged_flat = snapshot["position_branch"] == "flat" and row.candidate_id == "no_change"
        conditional = (
            None
            if unchanged_flat
            else dict(
                net_profit=_wire(row.positive_probability_if_assumed_fills),
                zero=_wire(
                    1
                    - row.positive_probability_if_assumed_fills
                    - row.loss_probability_if_assumed_fills
                ),
                net_loss=_wire(row.loss_probability_if_assumed_fills),
            )
        )
        rows.append(
            dict(
                candidate_id=row.candidate_id,
                action="open"
                if row.candidate_id.startswith("open_")
                else "reduce"
                if row.candidate_id == "reduce_half"
                else row.candidate_id,
                target_side=row.target_side,
                action_target_ref=action_target,
                feasibility="infeasible" if violations else "unknown",
                rejection_codes=violations,
                unit_expected_net_if_assumed_fills_usdt=_wire(
                    row.expected_net_if_assumed_fills_usdt
                ),
                worst_listed_loss_usdt=_wire(row.worst_listed_loss_usdt),
                expected_downside_usdt=_wire(row.expected_downside_usdt),
                expected_turnover_usdt=_wire(row.expected_turnover_usdt),
                scenario_net_pnls_usdt=[_wire(value) for value in row.path_net_pnls_usdt],
                outcome=dict(
                    schema_version="action-outcome-distribution.v1",
                    target_ref=action_target,
                    source_ref=deepcopy(bindings["paths"]),
                    kind="assumed",
                    qualification="research_trial",
                    conditioning_ref=deepcopy(target["conditioning_ref"]),
                    fill_probability=None,
                    trigger_probability=None,
                    no_fill_mass=None,
                    conditional_on_fill=conditional,
                    conditional_basis_ref=deepcopy(paths["fill_weight_assumption_ref"]),
                    opportunity_net_profit_probability=None,
                    opportunity_expected_net_usdt=None,
                    liquidation_probability=None,
                    unknown_reasons=["fill_model_unknown", "account_margin_unknown"],
                ),
            )
        )
    ranked = [key for key in result.ranked_candidate_ids if key not in excluded]
    output = dict(
        schema_version="action-comparison.v2",
        forecast_version=snapshot["forecast_version"],
        opportunity_id=snapshot["opportunity_id"],
        scope_id=snapshot["scope_id"],
        as_of=as_of,
        forecast_as_of=forecast_as_of,
        horizon=horizon,
        price_bridge=deepcopy(bridge),
        target_ref=deepcopy(bindings["target"]),
        bindings=deepcopy(bindings),
        binding_hash=content_hash(snapshot),
        position_branch=result.position_branch,
        unit_notional_usdt=snapshot["unit_notional_usdt"],
        selection_kind="assumed_unit_mean_net",
        selected_action_id=ranked[0] if ranked else None,
        alternative_action_ids=ranked[1:],
        candidate_results=rows,
        event_probability=deepcopy(event),
        market_distribution=deepcopy(market),
        account_feasibility="unknown",
        executable_contracts=None,
        unknown_reasons=unknown,
        cross_horizon_use="mutually_exclusive_alternatives",
        rounding_policy="decimal18_half_even",
        qualification="research_trial",
        financial_qualified=False,
    )
    output["payload_hash"] = content_hash(output)
    return output
