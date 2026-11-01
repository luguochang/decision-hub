"""Pure, time-bounded features for the frozen spot Action catalog.

Callers authenticate candle/capture originals before invoking this module. A
valid calculation is not a grant of data authenticity or trading eligibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from statistics import median
from typing import Any

from ..ports.market_path import (
    MarketCandle,
    MarketPathScope,
    aware_utc,
    canonical_hash,
    decimal_text,
    scope_matches,
    spot_scope,
)
from .action_policy import ActionFeatureValue
from .action_research_policy import research_action_catalog

FEATURE_INTERVAL_SECONDS = {
    "close": 60, "box_high_6h": 60, "box_low_6h": 60, "return_6h": 60,
    "risk_unit_20h": 3600, "volume_ratio_1h": 3600,
}
ACTION_MONITOR_MANIFEST_REF = "action-monitor-manifest.v1"


def action_feature_manifest(instrument: str) -> dict[str, Any]:
    return {
        "calculator_ref": "action-features.v1",
        "catalog_hash": canonical_hash(research_action_catalog(instrument)),
        "computed_decimal_places": 18, "rounding": "ROUND_HALF_EVEN",
        "source_prices": "exact", "interval_seconds": dict(FEATURE_INTERVAL_SECONDS),
        "window_anchor": "latest_original_accepted_closed_bar_at_or_before_as_of",
    }


def condition_sample_interval_seconds(feature_names: tuple[str, ...]) -> int:
    if not feature_names or any(name not in FEATURE_INTERVAL_SECONDS for name in feature_names):
        raise ValueError("action_feature_cadence_undefined")
    return min(FEATURE_INTERVAL_SECONDS[name] for name in feature_names)


@dataclass(frozen=True)
class ActionFeatureSnapshot:
    scope_id: str
    as_of: datetime
    closed_bar_at: datetime | None
    features: tuple[ActionFeatureValue, ...]
    manifest_hash: str
    input_hash: str
    payload_json: str
    gaps: tuple[str, ...]

    def feature_map(self) -> dict[tuple[str, str], ActionFeatureValue]:
        return {(value.feature, value.scope_id): value for value in self.features}


def action_feature_window(as_of: datetime) -> tuple[datetime, datetime]:
    """At most 1,379 minutes; an extra hour covers acquisition's ordinary lag."""
    end = aware_utc(as_of).replace(second=0, microsecond=0)
    return end.replace(minute=0) - timedelta(hours=22), end


def build_action_features(
    scope: MarketPathScope, candles: tuple[MarketCandle, ...], *, as_of: datetime,
) -> ActionFeatureSnapshot:
    cutoff = aware_utc(as_of)
    if scope != spot_scope(scope.instrument) or scope.instrument not in {"BTC-USDT", "ETH-USDT"}:
        raise ValueError("action_feature_scope_not_admitted")
    if len(candles) > 1500:
        raise ValueError("action_feature_window_too_large")
    catalog = research_action_catalog(scope.instrument)
    calculation = action_feature_manifest(scope.instrument)
    manifest_hash = canonical_hash(calculation)
    start, end = action_feature_window(cutoff)
    indexed: dict[datetime, MarketCandle] = {}
    for bar in candles:
        if not scope_matches(scope, bar):
            raise ValueError("action_feature_scope_mismatch")
        if bar.known_at > cutoff or bar.close_at > end or bar.open_at < start:
            raise ValueError("action_feature_outside_known_window")
        if bar.close_at in indexed:
            raise ValueError("action_feature_duplicate_bar")
        if not bar.capture_ref or not bar.capture_hash or bar.row_index is None:
            raise ValueError("action_feature_original_reference_missing")
        indexed[bar.close_at] = bar

    def window(last: datetime, minutes: int) -> tuple[MarketCandle, ...]:
        selected = tuple(indexed.get(last - timedelta(minutes=i))
                         for i in range(minutes - 1, -1, -1))
        return tuple(item for item in selected if item is not None) if all(
            item is not None for item in selected
        ) else ()

    closed_bar_at = max(indexed) if indexed else None
    # Capture and monitor are independent. A just-closed bar may not yet have
    # been received. Keep the most recent accepted observation and its true
    # age; the condition, not the minute boundary, determines freshness.
    if closed_bar_at is not None:
        end = closed_bar_at
    last = indexed.get(end)
    definitions = {row["feature"]: row["unit"] for row in catalog["features"]}
    features: list[ActionFeatureValue] = []
    gaps: list[str] = []
    feature_rows: list[dict[str, Any]] = []

    def emit(name: str, value: Decimal | None, basis: tuple[MarketCandle, ...],
             reason: str | None = None) -> None:
        # Fixed numeric representation for reproducible ratios. This precision
        # policy belongs to the calculation, never to order size or fill prices.
        if value is not None and name in {"return_6h", "risk_unit_20h", "volume_ratio_1h"}:
            with localcontext() as ctx:
                ctx.prec = 50
                value = value.quantize(Decimal("0.000000000000000001"))
            if name == "risk_unit_20h" and value <= 0:
                value, reason = None, "positive_true_range_required"
        observed = max((bar.close_at for bar in basis), default=end)
        known = max((bar.known_at for bar in basis), default=cutoff)
        refs = [{"revision_hash": canonical_hash(bar.semantic_payload()),
                 "known_at": bar.known_at.isoformat(), "capture_ref": bar.capture_ref,
                 "capture_hash": bar.capture_hash, "row_index": bar.row_index}
                for bar in basis]
        body = {"feature": name, "scope_id": scope.scope_id, "unit": definitions[name],
                "value": decimal_text(value) if value is not None else None,
                "observed_at": observed.isoformat(), "known_at": known.isoformat(),
                "manifest_hash": manifest_hash, "basis": refs, "gap": reason}
        digest = canonical_hash(body)
        ref = "action-feature:" + digest
        features.append(ActionFeatureValue(
            feature=name, scope_id=scope.scope_id, unit=definitions[name], value=value,
            observed_at=observed, known_at=known,
            quality="accepted" if value is not None else "unknown",
            source_ref=ref, source_hash=digest,
        ))
        feature_rows.append({**body, "source_ref": ref, "source_hash": digest})
        if reason:
            gaps.append(f"{name}:{reason}")

    emit("close", last.close if last else None, (last,) if last else (),
         None if last else "latest_closed_bar_missing")
    box = window(end, 360)
    emit("box_high_6h", max((bar.high for bar in box), default=None), box,
         None if box else "continuous_360_bars_required")
    emit("box_low_6h", min((bar.low for bar in box), default=None), box,
         None if box else "continuous_360_bars_required")
    returns = window(end, 361)
    with localcontext() as ctx:
        ctx.prec = 50
        change = returns[-1].close / returns[0].close - 1 if returns else None
    emit("return_6h", change, returns, None if returns else "continuous_361_bars_required")
    hour_end = end.replace(minute=0)
    hours = tuple(window(hour_end - timedelta(hours=i), 60) for i in range(20, -1, -1))
    if all(hours):
        hour_bars = tuple(bar for hour in hours for bar in hour)
        ranges = tuple(max(max(bar.high for bar in hour) - min(bar.low for bar in hour),
                           abs(max(bar.high for bar in hour) - hours[i][-1].close),
                           abs(min(bar.low for bar in hour) - hours[i][-1].close))
                       for i, hour in enumerate(hours[1:]))
        unit = median(ranges)
        emit("risk_unit_20h", unit if unit > 0 else None, hour_bars,
             None if unit > 0 else "positive_true_range_required")
        if any(bar.volume_base is None for bar in hour_bars):
            emit("volume_ratio_1h", None, hour_bars, "base_volume_missing")
        else:
            volumes = tuple(sum((bar.volume_base for bar in hour if bar.volume_base is not None),
                                Decimal(0)) for hour in hours)
            base = median(volumes[:-1])
            with localcontext() as ctx:
                ctx.prec = 50
                ratio = volumes[-1] / base if base > 0 else None
            emit("volume_ratio_1h", ratio, hour_bars,
                 None if base > 0 else "positive_previous_volume_required")
    else:
        emit("risk_unit_20h", None, (), "continuous_21_complete_hours_required")
        emit("volume_ratio_1h", None, (), "continuous_21_complete_hours_required")
    body = {"schema_version": "action-feature-snapshot.v1", "scope_id": scope.scope_id,
            "as_of": cutoff.isoformat(), "manifest_hash": manifest_hash,
            "calculation": calculation,
            "closed_bar_at": closed_bar_at.isoformat() if closed_bar_at else None,
            "features": feature_rows, "gaps": gaps}
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ActionFeatureSnapshot(
        scope.scope_id, cutoff, closed_bar_at, tuple(features), manifest_hash,
        canonical_hash(body), payload, tuple(gaps),
    )
