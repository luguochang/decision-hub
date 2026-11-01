"""Explicit server research-unit cost assumptions; no actual fee/funding authority.

The existing comparison calculator accepts fixed CASH costs. Both legs below
use a 100 USDT reference, including the future exit. This is deliberately a
declared approximation, not an exit-price-dependent fee model or account size.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import combinations
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ComparisonCostPolicyV1,
    DecisionSourceRefV1,
)

from ..decision.perpetual_accounting import (
    AssumedScenario,
    UnitComparison,
    compare_assumed_unit_branch,
)
from ..ports.exact_decimal_text import number
from ..ports.market_path import canonical_hash

SCHEMA_VERSION = "perpetual-cost-assumptions.v1"
POLICY_REF = "decision-cost-assumptions:perpetual-fixed-reference-unit.v1"
_UNIT = "100"
_TAKER = "0.0005"
_SLIPPAGE = "0.0002"
# Cash for an ENTIRE assumed path, not a funding rate or an 8h schedule.
_FUNDING_STRESS_CASH = "0.1"
_CASES = (
    ("base", "1", "0"),
    ("double_trade_cost", "2", "0"),
    ("long_pays_funding", "1", "-0.1"),
    ("long_receives_funding", "1", "0.1"),
)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _context():
    return localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN))


def _cost_body(multiplier: str, funding_cash: str) -> dict:
    with _context():
        # Dimensional conversion of declared reference-unit rates to fixed cash
        # inputs; actual fees/PnL remain the existing accounting implementation.
        cash = Decimal(_UNIT) * (Decimal(_TAKER) + Decimal(_SLIPPAGE))
    return dict(
        kind="assumed_fixed_unit_cash",
        entry_trade_cost_usdt=number(cash),
        exit_trade_cost_usdt=number(cash),
        long_funding_cash_usdt=funding_cash,
        trade_cost_multiplier=multiplier,
    )


def _body() -> dict:
    kernel = Path(__file__).resolve().parents[1]
    files = (
        "application/perpetual_cost_assumptions.py",
        "decision/action_comparison.py",
        "decision/perpetual_accounting.py",
        "ports/exact_decimal_text.py",
    )
    dependencies = {
        path: hashlib.sha256((kernel / path).read_bytes()).hexdigest() for path in files
    }
    base = _cost_body("1", "0")
    return dict(
        schema_version=SCHEMA_VERSION,
        policy_ref=POLICY_REF,
        purpose="research_unit_only",
        qualification="research_trial",
        financial_qualified=False,
        unit_notional_usdt=_UNIT,
        unit_basis="comparison_reference_not_balance_or_order_quantity",
        assumed_taker_rate=_TAKER,
        assumed_slippage_fraction_per_leg=_SLIPPAGE,
        rate_status="explicit_server_assumption_not_authenticated_account_fee_tier",
        account_fee_rate_verified=False,
        actual_account_fee_rate=None,
        entry_cost_basis="fixed_100_usdt_reference_notional",
        exit_cost_basis="fixed_100_usdt_reference_notional_not_terminal_notional",
        per_leg_assumed_fee_usdt="0.05",
        per_leg_assumed_slippage_usdt="0.02",
        slippage_accounting="additional_fixed_cash_not_embedded_in_assumed_price_paths",
        funding_baseline="explicit_zero_cash_assumption_not_observed_zero",
        future_actual_funding_cash_usdt=None,
        funding_settlement_coverage="not_claimed",
        funding_sensitivity=dict(
            long_pays_cash_usdt="-" + _FUNDING_STRESS_CASH,
            long_receives_cash_usdt=_FUNDING_STRESS_CASH,
            basis="entire_declared_path_per_100_usdt_reference_unit",
            interpretation="positive_cash_is_long_receipt_short_has_inverse",
            settlement_frequency=None,
            assumed_settlement_rate=None,
            actual_future_funding="unknown_no_current_rate_extrapolation",
        ),
        base_cost_policy=base,
        base_cost_ref=dict(ref=POLICY_REF + "#base-cost", hash=canonical_hash(base)),
        sensitivity_cases=[
            dict(case_id=key, cost_policy=_cost_body(multiplier, funding))
            for key, multiplier, funding in _CASES
        ],
        switch_boundary_scope="unconstrained_assumed_unit_mean_net_only",
        switch_boundary_method="affine_expected_net_crossings_probed_with_existing_unit_calculator",
        switch_boundary_axes=["trade_cost_multiplier", "long_funding_cash_usdt"],
        decimal_precision=50,
        decimal_rounding="ROUND_HALF_EVEN",
        prohibits=["execution", "account_size", "fill_authority", "verified_fee_tier"],
        limitations=[
            "actual_exit_cost_depends_on_fill_price_and_quantity",
            "fixed_reference_exit_cash_does_not_model_terminal_notional_dependence",
            "held_reduce_and_close_keep_existing_fixed_exit_cash_model",
            "actual_fill_weights_account_margin_liquidation_and_future_funding_unknown",
            "sensitivity_boundaries_do_not_apply_account_or_utility_constraints",
            "direction_probability_does_not_supply_path_amplitude_or_net_profit",
        ],
        explanatory_source_urls=[
            "https://www.okx.com/en-eu/help/how-to-calculate-the-contract-transaction-fee",
            "https://www.okx.com/docs-v5/en/",
        ],
        explanatory_sources_authorize_account_rate=False,
        calculator_dependencies=dependencies,
    )


@dataclass(frozen=True)
class PerpetualCostAssumptionsOriginal:
    body_json: str

    @property
    def body(self) -> dict:
        return json.loads(self.body_json)

    @property
    def schema_version(self) -> str:
        return SCHEMA_VERSION

    @property
    def reference(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1(ref=POLICY_REF, hash=canonical_hash(self.body))

    @property
    def base_cost_policy(self) -> ComparisonCostPolicyV1:
        return ComparisonCostPolicyV1.model_validate(self.body["base_cost_policy"])

    @property
    def base_cost_ref(self) -> DecisionSourceRefV1:
        return DecisionSourceRefV1.model_validate(self.body["base_cost_ref"])


def perpetual_cost_assumptions_original() -> PerpetualCostAssumptionsOriginal:
    """Fresh immutable bytes; caller DTO/dict mutation cannot change server policy."""
    return PerpetualCostAssumptionsOriginal(_canonical(_body()))


def read_perpetual_cost_assumptions(
    reference: DecisionSourceRefV1,
) -> PerpetualCostAssumptionsOriginal:
    value = perpetual_cost_assumptions_original()
    declared = DecisionSourceRefV1.model_validate(reference)
    if declared != value.reference:
        raise ValueError("perpetual_cost_assumptions_original_mismatch")
    return value


@dataclass(frozen=True)
class CostSensitivityCase:
    case_id: str
    cost_policy_json: str
    comparison: UnitComparison


@dataclass(frozen=True)
class CostSwitchBoundary:
    axis: str
    value: str
    selected_below: str | None
    selected_above: str | None
    # Exact Decimal-derived linear equation also retained: first_x - delta/slope.
    first_x: str
    expected_difference_at_first: str
    difference_slope: str


@dataclass(frozen=True)
class CostAssumptionSensitivity:
    original_ref: str
    original_hash: str
    cases: tuple[CostSensitivityCase, ...]
    switch_boundaries: tuple[CostSwitchBoundary, ...]
    scope: str = "unconstrained_assumed_unit_mean_net_only"


def compare_cost_assumption_sensitivity(
    *,
    reference: DecisionSourceRefV1,
    position_branch: str,
    scenarios: tuple[AssumedScenario, ...],
    assumptions_ref: str,
) -> CostAssumptionSensitivity:
    """Reuse existing accounting for declared paths, funding signs and switches.

    No direction-only probability input, balance, order quantity or account risk
    feasibility is accepted. The caller must authenticate the actual path and
    preserve its explicit assumed-fill weights before using research results.
    """
    original = read_perpetual_cost_assumptions(reference)

    def compare(multiplier: Decimal, funding_cash: Decimal) -> UnitComparison:
        with _context():
            return compare_assumed_unit_branch(
                position_branch=position_branch,
                unit_notional_usdt=Decimal(_UNIT),
                scenarios=scenarios,
                assumptions_ref=assumptions_ref,
                entry_trade_cost_usdt=Decimal(original.base_cost_policy.entry_trade_cost_usdt),
                exit_trade_cost_usdt=Decimal(original.base_cost_policy.exit_trade_cost_usdt),
                long_funding_cash_usdt=funding_cash,
                trade_cost_multiplier=multiplier,
                max_gross_notional_usdt=None,
                max_listed_path_loss_usdt=None,
            )

    cases = tuple(
        CostSensitivityCase(
            key,
            _canonical(_cost_body(multiplier, funding)),
            compare(Decimal(multiplier), Decimal(funding)),
        )
        for key, multiplier, funding in _CASES
    )
    boundaries = []
    with _context():
        for axis, first_x, second_x in (
            ("trade_cost_multiplier", Decimal(1), Decimal(2)),
            ("long_funding_cash_usdt", Decimal(0), Decimal("0.1")),
        ):

            def at(value, selected_axis=axis):
                return (
                    compare(value, Decimal(0))
                    if selected_axis == "trade_cost_multiplier"
                    else compare(Decimal(1), value)
                )

            first, second = at(first_x), at(second_x)
            before = {
                row.candidate_id: row.expected_net_if_assumed_fills_usdt
                for row in first.candidate_results
            }
            after = {
                row.candidate_id: row.expected_net_if_assumed_fills_usdt
                for row in second.candidate_results
            }
            seen = set()
            for left, right in combinations(sorted(before), 2):
                delta = before[left] - before[right]
                slope = ((after[left] - after[right]) - delta) / (second_x - first_x)
                if slope == 0:
                    continue
                point = first_x - delta / slope
                if point in seen or (axis == "trade_cost_multiplier" and point <= 0):
                    continue
                seen.add(point)
                step = max(Decimal(1), abs(point)) * Decimal("1e-18")
                if axis == "trade_cost_multiplier":
                    step = min(step, point / 2)
                low, high = (
                    at(point - step).selected_candidate_id,
                    at(point + step).selected_candidate_id,
                )
                if low != high:
                    boundaries.append(
                        CostSwitchBoundary(
                            axis,
                            number(point),
                            low,
                            high,
                            number(first_x),
                            number(delta),
                            number(slope),
                        )
                    )
    return CostAssumptionSensitivity(
        original.reference.ref,
        original.reference.hash,
        cases,
        tuple(sorted(boundaries, key=lambda item: (item.axis, Decimal(item.value)))),
    )
