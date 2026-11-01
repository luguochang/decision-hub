"""Deterministic path labels, with explicit OHLC timing and incomplete-path limits."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    MarketCandle,
    MarketPathScope,
    aware_utc,
    path_hashes,
    scope_matches,
)


class RiskCrossing(FrozenContract):
    direction: Literal["long", "short"]
    threshold: Decimal
    event_presence: bool | None
    firstness: Literal["verified", "unknown", "not_applicable"]
    interval_start_at: datetime | None = None
    interval_end_at: datetime | None = None
    resolution_seconds: Literal[60] = 60


class PathRiskResult(FrozenContract):
    schema_version: Literal["path-risk-result.v1"] = "path-risk-result.v1"
    scope: MarketPathScope
    window_start_at: datetime
    window_end_at: datetime
    as_of: datetime
    anchor_at: datetime
    anchor_price: Decimal | None
    anchor_status: Literal["verified", "unknown"]
    window_status: Literal["pending", "complete", "partial", "unknown"]
    observed_count: int
    expected_count: int
    pending_count: int
    missing_open_at: tuple[datetime, ...]
    missing_ranges: tuple[tuple[datetime, datetime], ...]
    terminal_return: Decimal | None = None
    mae_long: Decimal | None = None
    mae_short: Decimal | None = None
    mdd_close: Decimal | None = None
    quality: Literal["observed", "observed_lower_bound", "unknown"] = "unknown"
    crossings: tuple[RiskCrossing, ...] = ()
    same_bar_order_ambiguous: bool = False
    reasons: tuple[str, ...] = ()
    semantic_hash: str
    manifest_hash: str


def missing_ranges(times: tuple[datetime, ...]) -> tuple[tuple[datetime, datetime], ...]:
    ranges: list[tuple[datetime, datetime]] = []
    for value in times:
        if ranges and ranges[-1][1] == value:
            ranges[-1] = (ranges[-1][0], value + timedelta(minutes=1))
        else:
            ranges.append((value, value + timedelta(minutes=1)))
    return tuple(ranges)


def verified_bars(
    scope: MarketPathScope,
    candles: tuple[MarketCandle, ...],
    as_of: datetime,
) -> tuple[MarketCandle, ...]:
    selected: dict[datetime, MarketCandle] = {}
    for candle in candles:
        if not scope_matches(scope, candle):
            raise ValueError("market_path_scope_mismatch")
        if candle.known_at > as_of or candle.close_at > as_of:
            continue
        old = selected.get(candle.open_at)
        if old and old.semantic_payload() != candle.semantic_payload():
            raise ValueError("market_path_conflicting_duplicate")

        def receipt_key(item):
            return (
                item.known_at,
                item.received_at,
                item.capture_hash or "",
                item.capture_ref or "",
                item.row_index if item.row_index is not None else -1,
            )

        if old is None or receipt_key(candle) < receipt_key(old):
            selected[candle.open_at] = candle
    return tuple(selected[key] for key in sorted(selected))


def compute_path_risk(
    scope: MarketPathScope,
    candles: tuple[MarketCandle, ...],
    window_start_at: datetime,
    end_at: datetime,
    *,
    anchor_policy: str = "scheduled_bar_open",
    as_of: datetime,
    thresholds: tuple[Decimal, ...] = (Decimal("0.02"), Decimal("0.05")),
) -> PathRiskResult:
    start, end, clock = map(aware_utc, (window_start_at, end_at, as_of))
    if anchor_policy != "scheduled_bar_open":
        raise ValueError("market_path_anchor_policy_unsupported")
    seconds = (end - start).total_seconds()
    if start.timestamp() % 60 or end.timestamp() % 60 or not 0 < seconds <= 86400:
        raise ValueError("market_path_window_invalid")
    if any(not item.is_finite() or not 0 < item < 1 for item in thresholds):
        raise ValueError("market_path_threshold_invalid")
    available = verified_bars(scope, candles, clock)
    bars = tuple(item for item in available if start <= item.open_at < end)
    semantic, manifest = path_hashes(scope, bars)
    expected = tuple(start + timedelta(minutes=i) for i in range(int(seconds // 60)))
    by_time = {item.open_at: item for item in bars}
    due = tuple(item for item in expected if item + timedelta(minutes=1) <= clock)
    gaps = tuple(item for item in due if item not in by_time)
    pending = len(expected) - len(due)
    anchor = by_time.get(start)
    complete = pending == 0 and not gaps and len(bars) == len(expected)
    status = "complete" if complete else "pending" if pending else "partial" if bars else "unknown"
    reasons = []
    if pending:
        reasons.append("window_not_due")
    if gaps:
        reasons.append("path_missing_bars")
    if anchor is None:
        reasons.append("scheduled_anchor_missing")
    values = {}
    crossings: list[RiskCrossing] = []
    if anchor:
        price = anchor.open
        mae_long = max(Decimal(0), 1 - min(item.low for item in bars) / price)
        mae_short = max(Decimal(0), max(item.high for item in bars) / price - 1)
        peak, drawdown = price, Decimal(0)
        for item in bars:
            peak = max(peak, item.close)
            drawdown = max(drawdown, 1 - item.close / peak)
        values = {"mae_long": mae_long, "mae_short": mae_short, "mdd_close": drawdown}
        if complete:
            values["terminal_return"] = bars[-1].close / price - 1
        for direction in ("long", "short"):
            for threshold in thresholds:
                hits = [
                    item
                    for item in bars
                    if (
                        item.low <= price * (1 - threshold)
                        if direction == "long"
                        else item.high >= price * (1 + threshold)
                    )
                ]
                first = hits[0] if hits else None
                earlier_gap = first is not None and any(value < first.open_at for value in gaps)
                crossings.append(
                    RiskCrossing(
                        direction=direction,
                        threshold=threshold,
                        event_presence=True if first else False if complete else None,
                        firstness="unknown"
                        if earlier_gap or not complete
                        else ("verified" if first else "not_applicable"),
                        interval_start_at=first.open_at if first else None,
                        interval_end_at=first.close_at if first else None,
                    )
                )
    starts = {
        item.interval_start_at
        for item in crossings
        if item.direction == "long" and item.interval_start_at is not None
    }
    ambiguous = any(
        item.interval_start_at in starts
        for item in crossings
        if item.direction == "short" and item.interval_start_at is not None
    )
    return PathRiskResult(
        scope=scope,
        window_start_at=start,
        window_end_at=end,
        as_of=clock,
        anchor_at=start,
        anchor_price=anchor.open if anchor else None,
        anchor_status="verified" if anchor else "unknown",
        window_status=status,
        observed_count=len(bars),
        expected_count=len(expected),
        pending_count=pending,
        missing_open_at=gaps,
        missing_ranges=missing_ranges(gaps),
        quality="observed"
        if complete and anchor
        else "observed_lower_bound"
        if anchor
        else "unknown",
        crossings=tuple(crossings),
        same_bar_order_ambiguous=ambiguous,
        reasons=tuple(reasons),
        semantic_hash=semantic,
        manifest_hash=manifest,
        **values,
    )
