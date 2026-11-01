"""Deterministic historical direction base rate for the frozen perpetual target.

Counts how often the past terminal return over the same horizon fell above
+theta, inside the inclusive neutral band, or below -theta, using only closes
known at as_of. It carries no conditional information and is not calibrated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from .exact_decimal_text import number
from .market_path import aware_utc, canonical_hash
from .perpetual_forecast_experience import score_direction

VERSION = "perpetual-direction-base-rate.v1"
OUTCOMES = ("up", "neutral", "down")
QUANTUM = Decimal("1e-18")
POLICY = {
    "version": VERSION,
    "price": "OKX confirmed trade close on an aligned grid; 1H close == last 1m close",
    "return": "close[t+h]/close[t]-1 for every grid t with both closes present",
    "label": "up > theta; down < -theta; neutral inclusive; theta from perp-close-target-method.v1",
    "smoothing": "laplace: (count+1)/(n+3)",
    "amplitude": "per-outcome nearest-rank p25/p50/p75 of observed returns",
    "point_in_time": "close_at <= as_of only; no pad, no interpolation",
    "max_missing_fraction": "0.02",
    "min_samples": 100,
    "overlap": "overlapping horizons; frequencies are not independent samples",
}


def _quantile(values: list[Decimal], q: Decimal) -> str:
    rank = max(1, int((q * len(values)).to_integral_value(rounding="ROUND_CEILING")))
    return number(values[rank - 1].quantize(QUANTUM))


def _unavailable(reason: str, **values: object) -> dict:
    return {"schema_version": VERSION, "status": "unavailable", "reason": reason, **values}


def direction_base_rate(
    *,
    closes: Sequence[tuple[datetime, str]],
    threshold_fraction: str,
    horizon_seconds: int,
    as_of: datetime,
    window_days: int = 90,
    grid_seconds: int = 3600,
) -> dict:
    theta = Decimal(threshold_fraction)
    if (
        not theta.is_finite()
        or theta <= 0
        or horizon_seconds <= 0
        or grid_seconds <= 0
        or horizon_seconds % grid_seconds
        or window_days <= 0
    ):
        raise ValueError("perp_base_rate_request_invalid")
    cutoff = aware_utc(as_of)
    epoch = datetime(1970, 1, 1, tzinfo=cutoff.tzinfo)
    end = epoch + timedelta(
        seconds=(cutoff - epoch) // timedelta(seconds=grid_seconds) * grid_seconds
    )
    start = end - timedelta(days=window_days)
    prices: dict[datetime, Decimal] = {}
    for close_at, price in closes:
        stamp = aware_utc(close_at)
        if stamp > cutoff or stamp < start:
            continue
        if (stamp - epoch) % timedelta(seconds=grid_seconds):
            raise ValueError("perp_base_rate_close_unaligned")
        value = Decimal(price)
        if not value.is_finite() or value <= 0:
            raise ValueError("perp_base_rate_price_invalid")
        if stamp in prices and prices[stamp] != value:
            raise ValueError("perp_base_rate_conflicting_close")
        prices[stamp] = value
    step = timedelta(seconds=grid_seconds)
    expected = int((end - start) / step) + 1
    window = dict(
        window_start=start.isoformat(), window_end=end.isoformat(), expected_closes=expected
    )
    missing = expected - len(prices)
    if Decimal(missing) / Decimal(expected) > Decimal(POLICY["max_missing_fraction"]):
        return _unavailable("closes_missing", missing_closes=missing, **window)
    horizon = timedelta(seconds=horizon_seconds)
    buckets: dict[str, list[Decimal]] = {key: [] for key in OUTCOMES}
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        for stamp, first in sorted(prices.items()):
            last = prices.get(stamp + horizon)
            if last is None:
                continue
            value = last / first - 1
            key = "up" if value > theta else "down" if value < -theta else "neutral"
            buckets[key].append(value)
        n = sum(len(rows) for rows in buckets.values())
        if n < POLICY["min_samples"]:
            return _unavailable("samples_insufficient", samples=n, **window)
        up, neutral = (
            (Decimal(len(buckets[key]) + 1) / Decimal(n + 3)).quantize(QUANTUM)
            for key in ("up", "neutral")
        )
        probabilities = {"up": up, "neutral": neutral, "down": 1 - up - neutral}
        amplitudes = {}
        for key, rows in buckets.items():
            rows.sort()
            amplitudes[key] = (
                None
                if not rows
                else {
                    name: _quantile(rows, Decimal(q))
                    for name, q in (("p25", "0.25"), ("p50", "0.5"), ("p75", "0.75"))
                }
            )
    return {
        "schema_version": VERSION,
        "status": "available",
        "reason": None,
        "threshold_fraction": threshold_fraction,
        "horizon_seconds": horizon_seconds,
        "grid_seconds": grid_seconds,
        "samples": n,
        "counts": {key: len(rows) for key, rows in buckets.items()},
        "missing_closes": missing,
        "probabilities": [
            {"outcome_id": key, "value": number(probabilities[key])} for key in OUTCOMES
        ],
        "terminal_return_quantiles": amplitudes,
        "method_hash": canonical_hash(POLICY),
        **window,
    }


def score_against_base_rate(
    probabilities: Sequence[Mapping[str, str]] | None,
    base_rate: Mapping[str, object] | None,
    category: str,
) -> dict:
    """Brier skill vs the base rate: 1 - model/base; positive means better than base."""
    model = score_direction(probabilities, category)
    if base_rate is None or base_rate.get("status") != "available":
        return {
            "model_brier": model,
            "base_rate_brier": None,
            "brier_skill": None,
            "reason": "base_rate_unavailable",
        }
    base = score_direction(base_rate["probabilities"], category)
    if model is None:
        return {
            "model_brier": None,
            "base_rate_brier": base,
            "brier_skill": None,
            "reason": "direction_probabilities_unavailable",
        }
    with localcontext() as context:
        context.prec = 50
        skill = None if Decimal(base) == 0 else str(1 - Decimal(model) / Decimal(base))
    return {"model_brier": model, "base_rate_brier": base, "brier_skill": skill, "reason": None}
