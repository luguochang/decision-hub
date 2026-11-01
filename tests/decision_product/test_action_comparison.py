"""Synthetic unit arithmetic only: no source authority or financial qualification."""

import hashlib
import importlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

D = Decimal
PACKAGE = "packages.kernel.decision_hub_kernel.decision."


def content_hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def compare(value):
    return importlib.import_module(PACKAGE + "action_comparison").compare_action_candidates(value)


def ref(name):
    return {"ref": "synthetic:" + name, "hash": hashlib.sha256(name.encode()).hexdigest()}


def estimate(target, probabilities, *, condition=None):
    return dict(
        target_ref=target,
        as_of="2026-01-01T00:00:00Z",
        probabilities=None
        if probabilities is None
        else [{"outcome_id": key, "value": value} for key, value in probabilities.items()],
        kind="unavailable" if probabilities is None else "subjective",
        qualification="research_trial",
        source_ref=None if probabilities is None else ref("subjective-assessment"),
        model_ref=None,
        calibration_ref=None,
        conditioning_ref=condition,
        gaps=["not_calibrated"] if probabilities is not None else ["occurrence_not_estimated"],
    )


def resign(value):
    if "utility_policy" in value:
        utility = value["utility_policy"]
        utility["hash"] = content_hash(
            {key: item for key, item in utility.items() if key != "hash"}
        )
        value["bindings"]["utility"] = {"ref": utility["policy_ref"], "hash": utility["hash"]}
    for key, body_key in (
        ("target", "target"),
        ("event_probability", "event_probability"),
        ("paths", "path_distribution"),
        ("cost", "cost_policy"),
    ):
        value["bindings"][key]["hash"] = content_hash(value[body_key])
    value["market_distribution"]["path_distribution_ref"] = deepcopy(value["bindings"]["paths"])
    value["bindings"]["market_distribution"]["hash"] = content_hash(value["market_distribution"])
    return value


def fixture(horizon="4h"):
    condition, instrument = ref("given-defined-event-E"), ref("BTC-contract-rule")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    hours = {"4h": 4, "24h": 24, "72h": 72}[horizon]
    target = dict(
        schema_version="target-spec.v1",
        target_id="synthetic-market-" + horizon,
        kind="market_return",
        horizon=horizon,
        anchor="scheduled_pre_release",
        window_start="2026-01-01T00:00:00Z",
        window_end=(start + timedelta(hours=hours)).isoformat().replace("+00:00", "Z"),
        instrument_ref=instrument,
        price_basis="trade",
        threshold_fraction="0.005",
        conditioning_ref=condition,
        target_family_ref=ref("new-perp-family-" + horizon),
        label_rule_ref=ref("terminal-return-three-class"),
        protocol_ref=ref("new-" + horizon + "-research-trial"),
    )
    target_ref = {"ref": "synthetic:target-" + horizon, "hash": content_hash(target)}
    paths = dict(
        kind="assumed_complete_paths",
        market_target_ref=target_ref,
        conditioning_ref=condition,
        supports_market_direction=True,
        fill_weight_assumption_ref=ref("weights-preserved-if-filled-" + horizon),
        scenarios=[
            dict(scenario_id="up-path", probability="0.435", terminal_return_fraction="0.02"),
            dict(scenario_id="neutral-path", probability="0.295", terminal_return_fraction="0"),
            dict(scenario_id="down-path", probability="0.270", terminal_return_fraction="-0.04"),
        ],
    )
    event = dict(
        schema_version="event-probability.v1",
        event_definition_ref=condition,
        occurrence_target_ref=ref("occurrence-target"),
        outcome_target_ref=ref("event-outcome-target"),
        occurrence_distribution=estimate(ref("occurrence-target"), None),
        outcome_given_occurrence=estimate(
            ref("event-outcome-target"),
            {"cool": "0.5", "neutral": "0.3", "hot": "0.2"},
            condition=condition,
        ),
        joint_partition_ref=None,
        observation_rule_ref=ref("first-release-rule"),
    )
    value = dict(
        schema_version="decision-method-inputs.v1",
        opportunity_id="synthetic-demo-01",
        forecast_version=1,
        instrument_id="okx:BTC-USDT-SWAP",
        scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
        forecast_as_of="2026-01-01T00:00:00Z",
        comparison_as_of="2026-01-01T00:00:00Z",
        forecast_known_at_max="2025-12-31T23:59:59Z",
        comparison_known_at_max="2025-12-31T23:59:59Z",
        target=target,
        event_probability=event,
        path_distribution=paths,
        position_branch="flat",
        unit_notional_usdt="100",
        bindings={
            name: ref(name + "-" + horizon)
            for name in [
                "forecast",
                "settings",
                "instrument",
                "utility",
                "cost",
                "target",
                "event_probability",
                "market_distribution",
                "paths",
                "method",
            ]
        },
        action_target_refs={
            key: ref("net-" + horizon + "-" + key)
            for key in ("open_long", "open_short", "no_change")
        },
        cost_policy=dict(
            kind="assumed_fixed_unit_cash",
            entry_trade_cost_usdt="0.06",
            exit_trade_cost_usdt="0.06",
            long_funding_cash_usdt="-0.03",
            trade_cost_multiplier="1",
        ),
        account_snapshot=None,
        candidate_leverage=None,
        price_bridge=dict(
            forecast_price_ref=ref("original-price-basis"),
            comparison_price_ref=ref("original-price-basis"),
            comparison_to_forecast_price_ratio="1",
            known_at="2025-12-31T23:59:59Z",
        ),
    )
    value["bindings"].update(account=None, position=None, instrument=instrument, target=target_ref)
    value["utility_policy"] = dict(
        schema_version="utility-policy.v2",
        policy_ref=value["bindings"]["utility"]["ref"],
        hash="0" * 64,
        source="research_default",
        objective="mean_net",
        risk_penalty="0",
        max_gross_notional={"unit": "USDT", "value": "100"},
        max_planned_loss=None,
        tail_constraints=None,
        position_scenario="flat",
        margin_buffer_policy=None,
        allowed_leverage=None,
        allowed_sides=["flat", "long", "short"],
        account_ref=None,
    )
    value["market_distribution"] = dict(
        schema_version="conditional-market-distribution.v1",
        instrument_ref=instrument,
        market_target_ref=target_ref,
        event_scenario_ref=deepcopy(value["bindings"]["event_probability"]),
        conditioning_ref=condition,
        path_distribution_ref=deepcopy(value["bindings"]["paths"]),
        direction_distribution=estimate(
            target_ref, {"up": "0.435", "neutral": "0.295", "down": "0.270"}, condition=condition
        ),
    )
    resign(value)
    value["market_distribution"]["event_scenario_ref"] = deepcopy(
        value["bindings"]["event_probability"]
    )
    return resign(value)


def test_synthetic_unit_net_ev_selects_short_despite_more_likely_up_direction():
    result = compare(fixture())
    rows = {row["candidate_id"]: row for row in result["candidate_results"]}
    assert result["selected_action_id"] == "open_short"
    assert result["alternative_action_ids"] == ["no_change", "open_long"]
    assert [
        rows[key]["unit_expected_net_if_assumed_fills_usdt"]
        for key in ("open_long", "open_short", "no_change")
    ] == ["-0.36", "0.12", "0"]
    assert rows["open_short"]["outcome"]["conditional_on_fill"]["net_profit"] == "0.27"
    assert (
        result["event_probability"]["outcome_given_occurrence"]["probabilities"][0]["value"]
        == "0.5"
    )
    assert (
        result["market_distribution"]["direction_distribution"]["probabilities"][0]["value"]
        == "0.435"
    )
    assert result["financial_qualified"] is False


@pytest.mark.parametrize(
    "restriction,reason",
    [
        ("cost", None),
        ("risk", "max_listed_path_loss"),
        ("side", "target_side_not_allowed"),
        ("leverage", "candidate_leverage_not_allowed"),
    ],
)
def test_frozen_cost_or_known_constraint_can_exclude_positive_short(restriction, reason):
    value = fixture()
    if restriction == "cost":
        value["cost_policy"]["trade_cost_multiplier"] = "2"
    elif restriction == "risk":
        value["utility_policy"]["max_planned_loss"] = {"unit": "USDT", "value": "2.08"}
    elif restriction == "side":
        value["utility_policy"]["allowed_sides"] = ["long", "flat"]
    else:
        value["utility_policy"]["allowed_leverage"] = ["1"]
        value["candidate_leverage"] = "2"
    output = compare(resign(value))
    assert output["selected_action_id"] == "no_change"
    row = next(row for row in output["candidate_results"] if row["candidate_id"] == "open_short")
    if reason:
        assert reason in row["rejection_codes"]
    else:
        assert row["unit_expected_net_if_assumed_fills_usdt"] == "0"
    if restriction == "risk":
        assert row["worst_listed_loss_usdt"] == "2.09"


@pytest.mark.parametrize("horizon", ["4h", "24h", "72h"])
def test_unit_advice_preserves_unknown_account_fill_and_uncalibrated_probability(horizon):
    value = fixture(horizon)
    value["utility_policy"]["max_gross_notional"] = {"unit": "equity_fraction", "value": "0.25"}
    output = compare(resign(value))
    assert output["horizon"] == horizon and output["selected_action_id"] == "open_short"
    assert output["account_feasibility"] == "unknown" and output["executable_contracts"] is None
    assert "max_gross_notional" in output["unknown_reasons"]
    assert output["event_probability"]["occurrence_distribution"]["probabilities"] is None
    assert output["market_distribution"]["direction_distribution"]["kind"] == "subjective"
    for row in output["candidate_results"]:
        for field in (
            "fill_probability",
            "no_fill_mass",
            "opportunity_net_profit_probability",
            "opportunity_expected_net_usdt",
            "liquidation_probability",
        ):
            assert row["outcome"][field] is None
    flat = next(row for row in output["candidate_results"] if row["candidate_id"] == "no_change")
    assert flat["outcome"]["conditional_on_fill"] is None


@pytest.mark.parametrize("invalid", ["mass", "duplicate"])
def test_path_partition_rejects_bad_mass_and_duplicate_ids(invalid):
    value = fixture()
    scenarios = value["path_distribution"]["scenarios"]
    if invalid == "mass":
        scenarios[0]["probability"] = "0.5"
    else:
        scenarios[1]["scenario_id"] = scenarios[0]["scenario_id"]
    with pytest.raises(ValueError, match="scenario_mass_not_one|scenario_partition_invalid"):
        compare(resign(value))


def test_in_window_cost_reevaluation_reuses_original_price_without_rewriting_forecast():
    value = fixture()
    frozen = deepcopy(
        {
            key: value[key]
            for key in (
                "target",
                "event_probability",
                "market_distribution",
                "path_distribution",
                "price_bridge",
            )
        }
    )
    value["comparison_as_of"] = "2026-01-01T02:00:00Z"
    value["comparison_known_at_max"] = "2026-01-01T01:59:59Z"
    value["cost_policy"]["trade_cost_multiplier"] = "2"
    output = compare(resign(value))
    assert output["selected_action_id"] == "no_change"
    assert output["forecast_as_of"] == "2026-01-01T00:00:00Z"
    assert output["as_of"] == "2026-01-01T02:00:00Z"
    for key, expected in frozen.items():
        assert value[key] == expected
    assert output["bindings"]["settings"] == value["bindings"]["settings"]
    assert output["binding_hash"] == content_hash(value)


@pytest.mark.parametrize(
    "invalid,reason",
    [
        ("hash", "input_body_hash_mismatch"),
        ("old_horizon", "new_perp_target_required"),
        ("spot", "new_perp_target_required"),
        ("future", "target_clock_mismatch"),
        ("expired", "target_clock_mismatch"),
    ],
)
def test_frozen_refs_new_scope_and_independent_cutoffs_are_enforced(invalid, reason):
    value = fixture()
    if invalid == "hash":
        value["bindings"]["cost"]["hash"] = "f" * 64
    elif invalid == "old_horizon":
        value["target"]["horizon"] = "30m"
        resign(value)
    elif invalid == "spot":
        value["scope_id"] = "crypto:spot:okx:BTC-USDT:trade:1m"
    elif invalid == "future":
        value["comparison_known_at_max"] = "2026-01-01T00:00:01Z"
    else:
        value["comparison_as_of"] = "2026-01-01T04:00:00Z"
    with pytest.raises(ValueError, match=reason):
        compare(value)


@pytest.mark.parametrize(
    "side,want_gross,want_funding,want_net",
    [
        ("long", "0.2", "-0.02", "0.1599"),
        ("short", "-0.2", "0.02", "-0.2001"),
    ],
)
def test_explicit_linear_contract_dimensions_two_fee_legs_and_funding(
    side, want_gross, want_funding, want_net
):
    accounting = importlib.import_module(PACKAGE + "perpetual_accounting")
    spec = accounting.LinearContractSpec(
        "BTC",
        "USDT",
        "USDT",
        D("0.01"),
        D("2"),
        "BTC",
        D("1"),
        D("1"),
        D("0.1"),
    )
    assert accounting.linear_base_quantity(spec, D("10")) == D("0.2")
    assert accounting.contracts_for_notional(
        spec,
        notional_usdt=D("20"),
        price_usdt_per_base=D("100"),
    ) == D("10")
    gross = accounting.linear_gross_pnl_usdt(
        spec,
        side=side,
        contracts=D("10"),
        entry_fill_price=D("100"),
        exit_fill_price=D("101"),
    )
    fees = [
        accounting.trade_fee_usdt(spec, contracts=D("10"), fill_price=price, fee_rate=D("0.0005"))
        for price in (D("100"), D("101"))
    ]
    funding = accounting.funding_cashflow_usdt(
        spec,
        side=side,
        held_contracts=D("10"),
        settlement_mark=D("100"),
        settlement_rate=D("0.001"),
        held_at_settlement=True,
    )
    assert gross == D(want_gross) and fees == [D("0.01"), D("0.0101")]
    assert funding == D(want_funding)
    assert accounting.net_pnl_usdt(
        gross_pnl=gross, entry_fee=fees[0], exit_fee=fees[1], funding_cash=funding
    ) == D(want_net)
    unknown = accounting.funding_cashflow_usdt(
        spec,
        side=side,
        held_contracts=D("10"),
        settlement_mark=None,
        settlement_rate=None,
        held_at_settlement=None,
    )
    assert unknown is None
    assert (
        accounting.net_pnl_usdt(
            gross_pnl=gross, entry_fee=fees[0], exit_fee=fees[1], funding_cash=unknown
        )
        is None
    )
    with pytest.raises(ValueError, match="slippage_double_count"):
        accounting.net_pnl_usdt(
            gross_pnl=gross,
            entry_fee=fees[0],
            exit_fee=fees[1],
            funding_cash=funding,
            unembedded_slippage=D("0.01"),
        )
    with pytest.raises(ValueError, match="finite_decimal_required"):
        accounting.LinearContractSpec(
            "BTC", "USDT", "USDT", D("0.01"), None, "BTC", D("1"), D("1"), D("0.1")
        )
