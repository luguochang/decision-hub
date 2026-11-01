"""Pure linear USDT perpetual arithmetic and assumed unit comparisons.

Finite Decimal only: contracts, base coins and USDT cash remain distinct.
Internal value records are calculation inputs/results, not wire contracts.
No I/O, account default, source authentication or execution authority lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Literal

D = Decimal
ZERO = D("0")
ONE = D("1")
Side = Literal["long", "short"]
Branch = Literal["flat", "long", "short"]


def _decimal(value: Decimal, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("perp_finite_decimal_required")
    if positive and value <= ZERO or nonnegative and value < ZERO:
        raise ValueError("perp_amount_invalid")
    return value


def _direction(side: Side) -> Decimal:
    if side not in {"long", "short"}:
        raise ValueError("perp_side_invalid")
    return ONE if side == "long" else -ONE


@dataclass(frozen=True)
class LinearContractSpec:
    """An authenticated InstrumentContractV1 must supply these exact dimensions.

    ct_val * ct_mult has units base/contract only when ct_val_ccy == base.
    This draft rejects inverse, quote-valued, non-USDT and non-perpetual cases.
    Values in the companion examples are SYNTHETIC, not current OKX metadata.
    """

    base: str
    quote: str
    settle: str
    ct_val: Decimal
    ct_mult: Decimal
    ct_val_ccy: str
    lot_sz: Decimal
    min_sz: Decimal
    tick_sz: Decimal
    instrument_kind: str = "perpetual"
    linear_or_inverse: str = "linear"

    def __post_init__(self) -> None:
        if (
            self.instrument_kind != "perpetual"
            or self.linear_or_inverse != "linear"
            or not self.base
            or self.quote != "USDT"
            or self.settle != "USDT"
            or self.ct_val_ccy != self.base
        ):
            raise ValueError("perp_contract_dimension_unsupported")
        for value in (self.ct_val, self.ct_mult, self.lot_sz, self.min_sz, self.tick_sz):
            _decimal(value, positive=True)


def linear_base_quantity(spec: LinearContractSpec, contracts: Decimal) -> Decimal:
    """Unsigned contracts -> base coins. Does not authorize an order."""
    _decimal(contracts, nonnegative=True)
    with localcontext() as ctx:
        ctx.prec = 50
        return contracts * spec.ct_val * spec.ct_mult


def floor_contracts(spec: LinearContractSpec, contracts: Decimal) -> Decimal:
    """Floor contracts to lot_sz; return 0 when the result is below min_sz."""
    _decimal(contracts, nonnegative=True)
    with localcontext() as ctx:
        ctx.prec = 50
        quantity = (contracts / spec.lot_sz).to_integral_value(rounding=ROUND_FLOOR) * spec.lot_sz
        return quantity if quantity >= spec.min_sz else ZERO


def contracts_for_notional(
    spec: LinearContractSpec, *, notional_usdt: Decimal, price_usdt_per_base: Decimal
) -> Decimal:
    """Explicit notional budget -> rounded contracts; never invents a balance.

    Fees/margin headroom are caller-owned bounds, not included in this budget.
    Unit advice MUST NOT turn this result into an executable account quantity.
    """
    _decimal(notional_usdt, nonnegative=True)
    _decimal(price_usdt_per_base, positive=True)
    with localcontext() as ctx:
        ctx.prec = 50
        return floor_contracts(
            spec, notional_usdt / (linear_base_quantity(spec, ONE) * price_usdt_per_base)
        )


def paper_fill_contracts(
    spec: LinearContractSpec,
    *,
    requested_contracts: Decimal,
    opposing_size_contracts: Decimal,
    already_consumed_contracts: Decimal = ZERO,
    participation: Decimal = Decimal("0.10"),
) -> Decimal:
    """min(request, residual quote participation capacity), lot-floor, min-check.

    Both sizes are CONTRACTS, not base or USDT. The application must reserve and
    consume shared account/quote capacity atomically; this function does no CAS.
    """
    for value in (requested_contracts, opposing_size_contracts, already_consumed_contracts):
        _decimal(value, nonnegative=True)
    if not ZERO < _decimal(participation) <= ONE:
        raise ValueError("perp_participation_invalid")
    with localcontext() as ctx:
        ctx.prec = 50
        residual = max(ZERO, opposing_size_contracts * participation - already_consumed_contracts)
        return floor_contracts(spec, min(requested_contracts, residual))


def execution_price_usdt(
    spec: LinearContractSpec,
    *,
    order_side: Literal["buy", "sell"],
    bid: Decimal,
    ask: Decimal,
    slippage_bps: Decimal,
) -> Decimal:
    """Adverse simulated fill: buy ask+slip tick-up, sell bid-slip tick-down.

    Long entry/short exit BUY; short entry/long exit SELL. Spread and modelled
    slippage are already inside the returned fill price and must not be charged
    again in net_pnl_usdt. A last-trade or mark price cannot replace this quote.
    """
    if _decimal(bid, positive=True) > _decimal(ask, positive=True):
        raise ValueError("perp_quote_crossed")
    if not ZERO <= _decimal(slippage_bps) < D("10000"):
        raise ValueError("perp_slippage_invalid")
    if order_side not in {"buy", "sell"}:
        raise ValueError("perp_order_side_invalid")
    with localcontext() as ctx:
        ctx.prec = 50
        slip = slippage_bps / D("10000")
        raw = ask * (ONE + slip) if order_side == "buy" else bid * (ONE - slip)
        rounding = ROUND_CEILING if order_side == "buy" else ROUND_FLOOR
        result = (raw / spec.tick_sz).to_integral_value(rounding=rounding) * spec.tick_sz
        return _decimal(result, positive=True)


def trade_fee_usdt(
    spec: LinearContractSpec, *, contracts: Decimal, fill_price: Decimal, fee_rate: Decimal
) -> Decimal:
    """base * actual fill USDT/base * fee fraction, for EACH execution leg.

    Positive fee is a debit; an explicitly sourced negative rebate is a credit.
    No live maker/taker rate is assumed by this calculator.
    """
    _decimal(fill_price, positive=True)
    if not -ONE < _decimal(fee_rate) < ONE:
        raise ValueError("perp_fee_rate_invalid")
    with localcontext() as ctx:
        ctx.prec = 50
        return linear_base_quantity(spec, contracts) * fill_price * fee_rate


def linear_gross_pnl_usdt(
    spec: LinearContractSpec,
    *,
    side: Side,
    contracts: Decimal,
    entry_fill_price: Decimal,
    exit_fill_price: Decimal,
) -> Decimal:
    """One fixed-quantity closed segment: direction * base * (exit - entry)."""
    direction = _direction(side)
    _decimal(entry_fill_price, positive=True)
    _decimal(exit_fill_price, positive=True)
    with localcontext() as ctx:
        ctx.prec = 50
        return (
            direction * linear_base_quantity(spec, contracts) * (exit_fill_price - entry_fill_price)
        )


def funding_cashflow_usdt(
    spec: LinearContractSpec,
    *,
    side: Side,
    held_contracts: Decimal,
    settlement_mark: Decimal | None,
    settlement_rate: Decimal | None,
    held_at_settlement: bool | None,
) -> Decimal | None:
    """One genuine/explicitly assumed settlement; positive cash is a receipt.

    No 8h schedule, current-rate extrapolation, or timestamp tie is inferred.
    The caller proves holdings/order at the settlement and labels actual versus
    assumed inputs. Missing ownership/mark/rate remains None, not zero funding.
    """
    direction = _direction(side)
    base = linear_base_quantity(spec, held_contracts)
    if held_at_settlement is False or base == ZERO:
        return ZERO
    if held_at_settlement is not True or settlement_mark is None or settlement_rate is None:
        return None
    _decimal(settlement_mark, positive=True)
    _decimal(settlement_rate)
    with localcontext() as ctx:
        ctx.prec = 50
        return -direction * base * settlement_mark * settlement_rate


def net_pnl_usdt(
    *,
    gross_pnl: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
    funding_cash: Decimal | None,
    unembedded_slippage: Decimal = ZERO,
    fill_prices_include_modelled_slippage: bool = True,
    liquidation_cost: Decimal = ZERO,
) -> Decimal | None:
    """gross - both fees - additional nonembedded slip + funding - liquidation.

    Default mode consumes executable fill prices. Supplying a second slippage
    debit in that mode is rejected. Unknown funding makes this net incomplete.
    """
    for value in (gross_pnl, entry_fee, exit_fee):
        _decimal(value)
    _decimal(unembedded_slippage, nonnegative=True)
    _decimal(liquidation_cost, nonnegative=True)
    if fill_prices_include_modelled_slippage and unembedded_slippage != ZERO:
        raise ValueError("perp_slippage_double_count")
    if funding_cash is None:
        return None
    _decimal(funding_cash)
    with localcontext() as ctx:
        ctx.prec = 50
        return (
            gross_pnl - entry_fee - exit_fee - unembedded_slippage + funding_cash - liquidation_cost
        )


def isolated_margin_breached(
    *,
    margin_equity_usdt: Decimal | None,
    quantity_base: Decimal,
    mark_usdt_per_base: Decimal,
    tier_mmr: Decimal | None,
    close_fee_buffer: Decimal,
) -> bool | None:
    """Only the frozen single-leg isolated-rule inequality from approved §5.5.

    Not an OKX liquidation-price calculator or a complete liquidation replay.
    Caller includes realized/unrealized PnL and funding in margin equity once.
    """
    _decimal(quantity_base, nonnegative=True)
    _decimal(mark_usdt_per_base, positive=True)
    _decimal(close_fee_buffer, nonnegative=True)
    if quantity_base == ZERO:
        return False
    if margin_equity_usdt is None or tier_mmr is None:
        return None
    _decimal(margin_equity_usdt)
    _decimal(tier_mmr, nonnegative=True)
    with localcontext() as ctx:
        ctx.prec = 50
        maintenance = quantity_base * mark_usdt_per_base * (tier_mmr + close_fee_buffer)
        return margin_equity_usdt <= maintenance


@dataclass(frozen=True)
class AssumedScenario:
    scenario_id: str
    probability: Decimal
    terminal_return_fraction: Decimal


@dataclass(frozen=True)
class UnitCandidateResult:
    candidate_id: str
    target_side: Branch
    path_net_pnls_usdt: tuple[Decimal, ...]
    expected_net_if_assumed_fills_usdt: Decimal
    positive_probability_if_assumed_fills: Decimal | None
    loss_probability_if_assumed_fills: Decimal | None
    worst_listed_loss_usdt: Decimal
    expected_downside_usdt: Decimal
    expected_turnover_usdt: Decimal
    post_action_gross_notional_usdt: Decimal
    unit_constraint_violations: tuple[str, ...]


@dataclass(frozen=True)
class UnitComparison:
    position_branch: Branch
    unit_notional_usdt: Decimal
    assumptions_ref: str
    scenario_ids: tuple[str, ...]
    unknown_constraints: tuple[str, ...]
    candidate_results: tuple[UnitCandidateResult, ...]
    ranked_candidate_ids: tuple[str, ...]
    selected_candidate_id: str | None
    selection_kind: str = "assumed_unit_mean_net"
    account_feasibility: str = "unknown"
    executable_contracts: None = None
    fill_probability: None = None
    opportunity_expected_net_usdt: None = None
    liquidation_probability: None = None


def compare_assumed_unit_branch(
    *,
    position_branch: Branch,
    unit_notional_usdt: Decimal,
    scenarios: tuple[AssumedScenario, ...],
    assumptions_ref: str,
    entry_trade_cost_usdt: Decimal,
    exit_trade_cost_usdt: Decimal,
    long_funding_cash_usdt: Decimal,
    max_gross_notional_usdt: Decimal | None,
    max_listed_path_loss_usdt: Decimal | None,
    trade_cost_multiplier: Decimal = ONE,
) -> UnitComparison:
    """Declared fixed-cost/complete-path UNIT scenario, never an account order.

    Flat compares open long/open short/no_change. Held long/short compares
    no_change/reduce_half/close from a common current equity baseline; historical
    entry costs are sunk and not charged again. No add/reversal is authorized.
    Funding is per full unit for the entire declared path; short gets its inverse.
    The §9 fixture assumes fills preserve these scenario weights; actual fill and
    opportunity EV stay None. A q classification alone is insufficient input.
    """
    if position_branch not in {"flat", "long", "short"} or not assumptions_ref:
        raise ValueError("perp_unit_branch_invalid")
    _decimal(unit_notional_usdt, positive=True)
    _decimal(entry_trade_cost_usdt, nonnegative=True)
    _decimal(exit_trade_cost_usdt, nonnegative=True)
    _decimal(long_funding_cash_usdt)
    _decimal(trade_cost_multiplier, positive=True)
    for limit in (max_gross_notional_usdt, max_listed_path_loss_usdt):
        if limit is not None:
            _decimal(limit, nonnegative=True)
    if not scenarios or len({s.scenario_id for s in scenarios}) != len(scenarios):
        raise ValueError("perp_scenario_partition_invalid")
    for scenario in scenarios:
        if not scenario.scenario_id or not ZERO <= _decimal(scenario.probability) <= ONE:
            raise ValueError("perp_scenario_probability_invalid")
        if _decimal(scenario.terminal_return_fraction) <= -ONE:
            raise ValueError("perp_terminal_price_not_positive")
    with localcontext() as ctx:
        ctx.prec = 50
        if sum((s.probability for s in scenarios), ZERO) != ONE:
            raise ValueError("perp_scenario_mass_not_one")

        # id, target side, retained unit fraction, fixed trade costs, future gross exposure.
        if position_branch == "flat":
            definitions = (
                (
                    "open_long",
                    "long",
                    ONE,
                    entry_trade_cost_usdt + exit_trade_cost_usdt,
                    unit_notional_usdt,
                ),
                (
                    "open_short",
                    "short",
                    ONE,
                    entry_trade_cost_usdt + exit_trade_cost_usdt,
                    unit_notional_usdt,
                ),
                ("no_change", "flat", ZERO, ZERO, ZERO),
            )
        else:
            definitions = (
                ("no_change", position_branch, ONE, exit_trade_cost_usdt, unit_notional_usdt),
                (
                    "reduce_half",
                    position_branch,
                    D("0.5"),
                    exit_trade_cost_usdt,
                    unit_notional_usdt / 2,
                ),
                ("close", "flat", ZERO, exit_trade_cost_usdt, ZERO),
            )
        results = []
        for candidate_id, side, retained, trade_cost, gross_exposure in definitions:
            direction = ZERO if side == "flat" else _direction(side)
            pnls = tuple(
                direction
                * retained
                * (unit_notional_usdt * s.terminal_return_fraction + long_funding_cash_usdt)
                - trade_cost * trade_cost_multiplier
                for s in scenarios
            )
            expected = sum((s.probability * p for s, p in zip(scenarios, pnls, strict=True)), ZERO)
            worst = max(ZERO, -min(pnls))
            downside = sum(
                (s.probability * max(ZERO, -p) for s, p in zip(scenarios, pnls, strict=True)), ZERO
            )
            untouched_flat = position_branch == "flat" and candidate_id == "no_change"
            positive = (
                None
                if untouched_flat
                else sum(
                    (s.probability for s, p in zip(scenarios, pnls, strict=True) if p > ZERO), ZERO
                )
            )
            loss = (
                None
                if untouched_flat
                else sum(
                    (s.probability for s, p in zip(scenarios, pnls, strict=True) if p < ZERO), ZERO
                )
            )
            if position_branch == "flat":
                turnover = (
                    ZERO
                    if untouched_flat
                    else unit_notional_usdt
                    * (
                        ONE
                        + sum(
                            (s.probability * (ONE + s.terminal_return_fraction) for s in scenarios),
                            ZERO,
                        )
                    )
                )
            else:
                turnover = unit_notional_usdt * (
                    ONE
                    - retained
                    + retained
                    * sum(
                        (s.probability * (ONE + s.terminal_return_fraction) for s in scenarios),
                        ZERO,
                    )
                )
            violations = []
            if max_gross_notional_usdt is not None and gross_exposure > max_gross_notional_usdt:
                violations.append("max_gross_notional")
            if max_listed_path_loss_usdt is not None and worst > max_listed_path_loss_usdt:
                violations.append("max_listed_path_loss")
            results.append(
                UnitCandidateResult(
                    candidate_id,
                    side,
                    pnls,
                    expected,
                    positive,
                    loss,
                    worst,
                    downside,
                    turnover,
                    gross_exposure,
                    tuple(violations),
                )
            )
        # Listed-path loss is this assumed fixture's risk metric, not VaR/ES.
        ranked = sorted(
            (r for r in results if not r.unit_constraint_violations),
            key=lambda r: (
                -r.expected_net_if_assumed_fills_usdt,
                r.worst_listed_loss_usdt,
                r.expected_turnover_usdt,
                r.candidate_id,
            ),
        )
        ids = tuple(r.candidate_id for r in ranked)
        unknown = tuple(
            name
            for name, value in (
                ("max_gross_notional", max_gross_notional_usdt),
                ("max_listed_path_loss", max_listed_path_loss_usdt),
            )
            if value is None
        ) + ("account_margin", "quote_liquidity", "validity_and_source_authority")
        return UnitComparison(
            position_branch=position_branch,
            unit_notional_usdt=unit_notional_usdt,
            assumptions_ref=assumptions_ref,
            scenario_ids=tuple(s.scenario_id for s in scenarios),
            unknown_constraints=unknown,
            candidate_results=tuple(results),
            ranked_candidate_ids=ids,
            selected_candidate_id=ids[0] if ids else None,
        )
