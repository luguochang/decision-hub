"""Server assumptions and existing unit calculator, no balances/rates/order claims."""

from decimal import Decimal

import pytest

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)
from packages.kernel.decision_hub_kernel.decision.perpetual_accounting import AssumedScenario


def original():
    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        perpetual_cost_assumptions_original,
    )

    return perpetual_cost_assumptions_original()


def test_original_is_fresh_exact_policy_with_explicit_unknowns_and_cash_units():
    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        read_perpetual_cost_assumptions,
    )

    value = original()
    body = value.body
    assert body["schema_version"] == "perpetual-cost-assumptions.v1"
    assert body["purpose"] == "research_unit_only" and body["unit_notional_usdt"] == "100"
    assert body["account_fee_rate_verified"] is False
    assert body["future_actual_funding_cash_usdt"] is None
    assert body["funding_settlement_coverage"] == "not_claimed"
    assert body["prohibits"] == ["execution", "account_size", "fill_authority", "verified_fee_tier"]
    policy = value.base_cost_policy
    assert policy.entry_trade_cost_usdt == policy.exit_trade_cost_usdt == "0.07"
    assert policy.long_funding_cash_usdt == "0" and policy.trade_cost_multiplier == "1"
    assert body["exit_cost_basis"] == "fixed_100_usdt_reference_notional_not_terminal_notional"
    assert "actual_exit_cost_depends_on_fill_price_and_quantity" in body["limitations"]
    body["unit_notional_usdt"] = "9999"
    with pytest.raises(ValueError, match="frozen"):
        policy.entry_trade_cost_usdt = "9999"
    with pytest.raises(ValueError, match="frozen"):
        value.reference.hash = "f" * 64
    assert read_perpetual_cost_assumptions(original().reference).body_json == original().body_json
    assert original().body["unit_notional_usdt"] == "100"
    assert original().base_cost_policy.entry_trade_cost_usdt == "0.07"
    for reference in (
        DecisionSourceRefV1(ref=original().reference.ref, hash="f" * 64),
        DecisionSourceRefV1(ref="cost:caller-policy", hash=original().reference.hash),
    ):
        with pytest.raises(ValueError, match="perpetual_cost_assumptions_original_mismatch"):
            read_perpetual_cost_assumptions(reference)


def test_assumed_cost_and_funding_stresses_reuse_existing_unit_math_and_change_selection():
    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        compare_cost_assumption_sensitivity,
    )

    scenarios = (AssumedScenario("up", Decimal("1"), Decimal("0.002")),)
    result = compare_cost_assumption_sensitivity(
        reference=original().reference,
        position_branch="flat",
        scenarios=scenarios,
        assumptions_ref="assumed-complete-path:explicit-fixture",
    )
    cases = {case.case_id: case.comparison for case in result.cases}
    assert cases["base"].selected_candidate_id == "open_long"
    assert cases["double_trade_cost"].selected_candidate_id == "no_change"
    assert cases["long_pays_funding"].selected_candidate_id == "no_change"
    assert cases["long_receives_funding"].selected_candidate_id == "open_long"
    expected = {
        row.candidate_id: row.expected_net_if_assumed_fills_usdt
        for row in cases["base"].candidate_results
    }
    assert expected == {
        "open_long": Decimal(".06"),
        "open_short": Decimal("-.34"),
        "no_change": Decimal("0"),
    }
    pays = {
        row.candidate_id: row.expected_net_if_assumed_fills_usdt
        for row in cases["long_pays_funding"].candidate_results
    }
    assert pays["open_long"] == Decimal("-.04") and pays["open_short"] == Decimal("-.24")
    assert all(
        case.account_feasibility == "unknown"
        and case.executable_contracts is None
        and case.fill_probability is None
        and case.opportunity_expected_net_usdt is None
        for case in cases.values()
    )
    assert result.scope == "unconstrained_assumed_unit_mean_net_only"


def test_switch_boundaries_are_calculator_crossings_not_direction_probability():
    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        compare_cost_assumption_sensitivity,
    )

    result = compare_cost_assumption_sensitivity(
        reference=original().reference,
        position_branch="flat",
        scenarios=(AssumedScenario("up", Decimal("1"), Decimal("0.002")),),
        assumptions_ref="assumed-path:fixed-terminal-amplitude",
    )
    cost = [point for point in result.switch_boundaries if point.axis == "trade_cost_multiplier"]
    assert (
        len(cost) == 1
        and cost[0].selected_below == "open_long"
        and cost[0].selected_above == "no_change"
    )
    assert abs(Decimal(cost[0].value) - Decimal(10) / Decimal(7)) < Decimal("1e-26")
    funding = {
        point.value: (point.selected_below, point.selected_above)
        for point in result.switch_boundaries
        if point.axis == "long_funding_cash_usdt"
    }
    assert funding == {"-0.34": ("open_short", "no_change"), "-0.06": ("no_change", "open_long")}


def test_held_position_does_not_recharge_entry_or_turn_assumed_unit_into_account():
    from packages.kernel.decision_hub_kernel.application.perpetual_cost_assumptions import (
        compare_cost_assumption_sensitivity,
    )

    result = compare_cost_assumption_sensitivity(
        reference=original().reference,
        position_branch="long",
        scenarios=(AssumedScenario("up", Decimal("1"), Decimal("0.002")),),
        assumptions_ref="assumed-path:held-unit",
    )
    base = next(case.comparison for case in result.cases if case.case_id == "base")
    values = {
        row.candidate_id: row.expected_net_if_assumed_fills_usdt for row in base.candidate_results
    }
    assert values == {
        "no_change": Decimal(".13"),
        "reduce_half": Decimal(".03"),
        "close": Decimal("-.07"),
    }
    assert base.unit_notional_usdt == Decimal("100") and base.executable_contracts is None
