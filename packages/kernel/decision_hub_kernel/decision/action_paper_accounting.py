"""Pure paper-account arithmetic. No quote authenticity or fill-time authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, Decimal, localcontext

D = Decimal
ZERO = D(0)
STEP = D("0.000000000001")


def _number(value: Decimal, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("paper_decimal_required")
    if value < ZERO or positive and value == ZERO:
        raise ValueError("paper_amount_invalid")
    return value


def _scope(value: str) -> None:
    if value not in {
        "crypto:spot:okx:BTC-USDT:trade:1m",
        "crypto:spot:okx:ETH-USDT:trade:1m",
    }:
        raise ValueError("paper_scope_unsupported")


def _floor(value: Decimal) -> Decimal:
    return (value / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP


@dataclass(frozen=True)
class Costs:
    fee: Decimal = D("0.001")
    slippage: Decimal = D("0.0005")

    def __post_init__(self) -> None:
        if _number(self.fee) >= 1 or _number(self.slippage) >= 1:
            raise ValueError("paper_cost_invalid")


DEFAULT_COSTS = Costs()


@dataclass(frozen=True)
class Quote:
    scope: str
    bid: Decimal
    ask: Decimal
    unit: str = "USDT"

    def __post_init__(self) -> None:
        _scope(self.scope)
        if self.unit != "USDT" or _number(self.bid, positive=True) > _number(
            self.ask, positive=True
        ):
            raise ValueError("paper_quote_invalid")


@dataclass(frozen=True)
class Account:
    scope: str
    cash: Decimal
    quantity: Decimal = ZERO
    raw_basis: Decimal = ZERO
    entry_fees: Decimal = ZERO
    entry_slippage: Decimal = ZERO
    realized_gross: Decimal = ZERO
    realized_cost: Decimal = ZERO
    realized_net: Decimal = ZERO

    def __post_init__(self) -> None:
        _scope(self.scope)
        for value in (
            self.cash,
            self.quantity,
            self.raw_basis,
            self.entry_fees,
            self.entry_slippage,
            self.realized_cost,
        ):
            _number(value)
        for value in (self.realized_gross, self.realized_net):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("paper_decimal_required")
        if not self.quantity and any((self.raw_basis, self.entry_fees, self.entry_slippage)):
            raise ValueError("paper_empty_position_basis")
        if self.quantity and not self.raw_basis:
            raise ValueError("paper_position_basis_missing")


@dataclass(frozen=True)
class Fill:
    side: str
    quantity: Decimal
    raw_price: Decimal
    execution_price: Decimal
    fee: Decimal
    slippage: Decimal
    cash_delta: Decimal
    gross: Decimal | None
    cost: Decimal | None
    net: Decimal | None


def inherit(scope: str, *, equity: Decimal, reference_price: Decimal) -> Account:
    """Common T0 paper scenario. This is an opening balance, not a trade."""
    _number(equity, positive=True)
    _number(reference_price, positive=True)
    with localcontext() as ctx:
        ctx.prec = 50
        quantity = _floor(equity / reference_price)
        basis = quantity * reference_price
        return Account(scope, equity - basis, quantity, basis)


def _binding(account: Account, quote: Quote) -> None:
    if account.scope != quote.scope:
        raise ValueError("paper_quote_scope_mismatch")


def size_entry(
    account: Account,
    quote: Quote,
    *,
    equity: Decimal,
    stop_price: Decimal,
    costs: Costs = DEFAULT_COSTS,
) -> Decimal:
    """Maximum additional quantity under the frozen trial risk assumptions."""
    _binding(account, quote)
    _number(equity, positive=True)
    if not ZERO < _number(stop_price, positive=True) < quote.ask:
        raise ValueError("paper_long_stop_invalid")
    with localcontext() as ctx:
        ctx.prec = 50
        entry = quote.ask * (1 + costs.slippage)
        stop_exit = stop_price * (1 - costs.slippage)
        unit_risk = quote.ask - stop_price + (quote.ask + stop_price) * costs.slippage
        unit_risk += (entry + stop_exit) * costs.fee
        space = max(ZERO, equity * D("0.25") - account.quantity * entry)
        return _floor(
            min(
                equity * D("0.005") / unit_risk,
                space / entry,
                account.cash / (entry * (1 + costs.fee)),
            )
        )


def buy(
    account: Account,
    quote: Quote,
    *,
    quantity: Decimal,
    equity: Decimal,
    stop_price: Decimal,
    costs: Costs = DEFAULT_COSTS,
) -> tuple[Account, Fill]:
    """Book a paper entry only within the declared current risk/cash bounds."""
    _binding(account, quote)
    _number(quantity, positive=True)
    if quantity > size_entry(account, quote, equity=equity, stop_price=stop_price, costs=costs):
        raise ValueError("paper_entry_limit_exceeded")
    with localcontext() as ctx:
        ctx.prec = 50
        if _floor(quantity) != quantity:
            raise ValueError("paper_quantity_step_invalid")
        price = quote.ask * (1 + costs.slippage)
        raw = quantity * quote.ask
        slip = quantity * (price - quote.ask)
        fee = quantity * price * costs.fee
        spent = raw + slip + fee
        if spent > account.cash:
            raise ValueError("paper_cash_insufficient")
        updated = replace(
            account,
            cash=account.cash - spent,
            quantity=account.quantity + quantity,
            raw_basis=account.raw_basis + raw,
            entry_fees=account.entry_fees + fee,
            entry_slippage=account.entry_slippage + slip,
        )
        return updated, Fill("buy", quantity, quote.ask, price, fee, slip, -spent, None, None, None)


def sell(
    account: Account, quote: Quote, *, quantity: Decimal, costs: Costs = DEFAULT_COSTS
) -> tuple[Account, Fill]:
    _binding(account, quote)
    _number(quantity, positive=True)
    if quantity > account.quantity:
        raise ValueError("paper_position_insufficient")
    with localcontext() as ctx:
        ctx.prec = 50
        if _floor(quantity) != quantity:
            raise ValueError("paper_quantity_step_invalid")
        # The final exit takes the exact residuals, avoiding allocation tails.
        basis, entry_fee, entry_slip = (
            value if quantity == account.quantity else value * quantity / account.quantity
            for value in (account.raw_basis, account.entry_fees, account.entry_slippage)
        )
        price = quote.bid * (1 - costs.slippage)
        raw = quantity * quote.bid
        slip = quantity * (quote.bid - price)
        fee = quantity * price * costs.fee
        received = raw - slip - fee
        gross = raw - basis
        cost = entry_fee + entry_slip + slip + fee
        net = gross - cost
        updated = replace(
            account,
            cash=account.cash + received,
            quantity=account.quantity - quantity,
            raw_basis=account.raw_basis - basis,
            entry_fees=account.entry_fees - entry_fee,
            entry_slippage=account.entry_slippage - entry_slip,
            realized_gross=account.realized_gross + gross,
            realized_cost=account.realized_cost + cost,
            realized_net=account.realized_net + net,
        )
        return updated, Fill(
            "sell", quantity, quote.bid, price, fee, slip, received, gross, cost, net
        )


def reduce_quantity(account: Account) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return _floor(account.quantity / 2)


def marked_equity(account: Account, quote: Quote) -> Decimal:
    """Bid marked equity, with no hypothetical future exit fees or fake fills."""
    _binding(account, quote)
    with localcontext() as ctx:
        ctx.prec = 50
        return account.cash + account.quantity * quote.bid
