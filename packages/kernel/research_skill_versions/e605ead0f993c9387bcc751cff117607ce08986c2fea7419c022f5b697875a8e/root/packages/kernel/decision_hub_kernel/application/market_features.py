"""Fixed deterministic features; known-at filtering is independent of LLM execution."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal

from packages.kernel.decision_hub_kernel.decision.path_risk import verified_bars
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    MarketCandle,
    MarketPathScope,
    aware_utc,
    path_hashes,
)


class MarketFeatureSnapshot(FrozenContract):
    schema_version: Literal["market-feature-snapshot.v1"] = "market-feature-snapshot.v1"
    scope: MarketPathScope
    as_of: datetime
    data_as_of: datetime | None = None
    known_at: datetime | None = None
    latest_close_at: datetime | None = None
    last_price: Decimal | None = None
    return_30m: Decimal | None = None
    realized_vol_30m: Decimal | None = None
    return_24h: Decimal | None = None
    volume_change_30m: Decimal | None = None
    spread_fraction: Decimal | None = None
    status: Literal["fresh", "stale", "unknown"]
    metric_statuses: dict[str, str]
    reasons: tuple[str, ...] = ()
    semantic_hash: str
    manifest_hash: str


def build_market_features(
    scope: MarketPathScope,
    candles: tuple[MarketCandle, ...],
    as_of: datetime,
    *,
    stale_after_seconds: int = 180,
) -> MarketFeatureSnapshot:
    clock = aware_utc(as_of)
    if stale_after_seconds <= 0:
        raise ValueError("market_features_stale_threshold_invalid")
    bars = verified_bars(scope, candles, clock)
    semantic, manifest = path_hashes(scope, bars)
    metrics = {
        name: "unknown"
        for name in (
            "return_30m",
            "realized_vol_30m",
            "return_24h",
            "volume_change_30m",
            "spread_fraction",
        )
    }
    base = {
        "scope": scope,
        "as_of": clock,
        "metric_statuses": metrics,
        "semantic_hash": semantic,
        "manifest_hash": manifest,
    }
    if not bars:
        return MarketFeatureSnapshot(status="unknown", reasons=("no_known_closed_bars",), **base)
    latest = bars[-1]
    lookup = {item.close_at: item for item in bars}
    stale = (clock - latest.close_at).total_seconds() > stale_after_seconds
    values = {
        "data_as_of": latest.close_at,
        "known_at": max(item.known_at for item in bars),
        "latest_close_at": latest.close_at,
        "last_price": latest.close,
    }
    times = tuple(latest.close_at - timedelta(minutes=i) for i in range(30, -1, -1))
    if all(value in lookup for value in times):
        selected = [lookup[value] for value in times]
        values["return_30m"] = latest.close / selected[0].close - 1
        returns = [
            math.log(float(right.close / left.close))
            for left, right in zip(selected, selected[1:], strict=False)
        ]
        values["realized_vol_30m"] = Decimal(
            str(math.sqrt(sum(value * value for value in returns)))
        )
        metrics["return_30m"] = metrics["realized_vol_30m"] = "verified"
    previous = lookup.get(latest.close_at - timedelta(hours=24))
    if previous:
        values["return_24h"] = latest.close / previous.close - 1
        metrics["return_24h"] = "verified_endpoints_only"
    # Current 30m vs preceding 48 complete 30m intervals needs 24h30m volume.
    volume_times = [latest.close_at - timedelta(minutes=i) for i in range(1470)]
    if all(value in lookup and lookup[value].volume_base is not None for value in volume_times):
        volumes = [lookup[value].volume_base for value in volume_times]
        current = sum(volumes[:30], Decimal(0))
        groups = [sum(volumes[i : i + 30], Decimal(0)) for i in range(30, 1470, 30)]
        denominator = median(groups)
        if denominator > 0:
            values["volume_change_30m"] = current / denominator - 1
            metrics["volume_change_30m"] = "verified"
    reasons = ["trade_candles_do_not_supply_bid_ask_spread"]
    if stale:
        reasons.append("latest_closed_bar_stale")
    if metrics["return_30m"] != "verified":
        reasons.append("rolling_30m_path_incomplete")
    status = "stale" if stale else "fresh" if metrics["return_30m"] == "verified" else "unknown"
    return MarketFeatureSnapshot(status=status, reasons=tuple(reasons), **base, **values)
