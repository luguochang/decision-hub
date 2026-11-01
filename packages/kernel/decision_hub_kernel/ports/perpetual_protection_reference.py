"""Derived close-only research basis; no chosen risk distance or executable quote."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from packages.kernel.decision_hub_kernel.ports.action_quote import exact_price
from packages.kernel.decision_hub_kernel.ports.exact_decimal_text import number
from packages.kernel.decision_hub_kernel.ports.market_path import aware_utc

VERSION = "perpetual-close-protection-reference.v1"


def _prices(window: dict[str, Any]) -> list[Decimal]:
    series = window["price_series"]
    if (
        window["inst_id"] not in {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
        or series["schema_version"] != "confirmed-perpetual-close-series.v1"
        or series["grid_seconds"] != 60
        or len(series["prices"]) != 1441
        or len(series["row_locators"]) != 1441
        or re.fullmatch(r"[a-f0-9]{64}", window["window_hash"]) is None
    ):
        raise ValueError("perpetual_protection_reference_series_invalid")
    first, last, cutoff = (
        aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        for value in (series["first_close_at"], series["last_close_at"], window["as_of"])
    )
    if last - first != timedelta(days=1) or last > cutoff:
        raise ValueError("perpetual_protection_reference_time_invalid")
    return [exact_price(value) for value in series["prices"]]


def _range(prices: list[Decimal], minutes: int) -> dict[str, Any]:
    start, end = len(prices) - minutes - 1, len(prices) - 1
    values = prices[start:]
    minimum, maximum, last = min(values), max(values), prices[-1]
    with localcontext() as context:
        context.prec = 50
        lower, upper = (last - minimum) / last, (maximum - last) / last
    return dict(
        duration_seconds=minutes * 60,
        close_count=len(values),
        start_index=start,
        end_index=end,
        minimum_close=number(minimum),
        maximum_close=number(maximum),
        minimum_index=start + values.index(minimum),
        maximum_index=start + values.index(maximum),
        distance_to_min_fraction=number(lower),
        distance_to_max_fraction=number(upper),
    )


def derive_close_protection_reference(window: dict[str, Any]) -> dict[str, Any]:
    """Caller authenticated the original window; preserve all source identities."""
    prices = _prices(window)
    series = window["price_series"]
    return dict(
        schema_version=VERSION,
        instrument_id="okx:" + window["inst_id"],
        source_window_hash=window["window_hash"],
        purpose="research_distance_basis_only_not_current_entry_price",
        as_of_close_reference=dict(
            price=series["prices"][-1],
            price_index=1440,
            price_type="confirmed_perpetual_trade_1m_close",
            close_at=series["last_close_at"],
            as_of=window["as_of"],
            price_pointer="/price_series/prices/1440",
            row_locator_pointer="/price_series/row_locators/1440",
            fresh_executable_quote=False,
        ),
        sigma24=window["sigma24"],
        sigma24_source_pointer="/sigma24",
        close_ranges=[_range(prices, minutes) for minutes in (60, 240, 1440)],
        calculation=dict(
            decimal_precision=50,
            minimum="min(close[start_index:end_index+1])",
            maximum="max(close[start_index:end_index+1])",
            lower_distance="(last_close-minimum_close)/last_close",
            upper_distance="(maximum_close-last_close)/last_close",
            sigma24="authenticated parent sigma24; close-to-close log-return volatility",
        ),
        unsupported=[
            "chosen_d_SL",
            "chosen_d_TP",
            "ATR",
            "first_touch_probability",
            "current_entry_price",
            "execution_quote",
            "calibration",
        ],
    )
