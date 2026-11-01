"""Fixed paired Brier inference using whole UTC days as correlation blocks.

The caller authenticates prediction, label and matched-event originals. These
mathematical results are not PIT/calibration/trading or component-promotion proof.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from random import Random

from ..ports.market_path import aware_utc, canonical_hash

PROTOCOL = {
    "version": "utc-day-paired-percentile-bootstrap.v2",
    "confidence": "0.95",
    "seed": 271828,
    "replicates": 2000,
    "percentile": "nearest_rank_ceil",
    "block": "UTC-day-all-assets-together",
    "estimator": "equal_day_mean_baseline_minus_candidate_brier",
    "minimum_time_blocks": 100,
    "minimum_event_blocks": 30,
    "maximum_pairs": 4096,
    "decimal_precision": 28,
    "promotion_allowed": False,
}
PROTOCOL_HASH = canonical_hash(PROTOCOL)


@dataclass(frozen=True)
class PairedBrierLoss:
    scope_id: str
    cutoff_at: datetime
    baseline_loss: Decimal
    candidate_loss: Decimal
    input_known_at: datetime
    baseline_published_at: datetime
    candidate_published_at: datetime
    label_known_at: datetime
    window_end_at: datetime


@dataclass(frozen=True)
class MatchedEventBlock:
    """Only supplied by an authenticated matching-original reader, not an alert count."""

    block_id: str
    cutoff_at: datetime
    known_at: datetime
    original_ref: str
    original_hash: str
    scope_ids: tuple[str, ...] = ()


def paired_day_bootstrap(
    pairs: tuple[PairedBrierLoss, ...],
    *,
    expected_pairs: tuple[tuple[str, datetime], ...],
    event_blocks: tuple[MatchedEventBlock, ...] | None,
    as_of: datetime,
) -> dict:
    clock = aware_utc(as_of)
    expected = tuple((scope, aware_utc(cutoff)) for scope, cutoff in expected_pairs)
    if len(expected) > PROTOCOL["maximum_pairs"] or len(pairs) > PROTOCOL["maximum_pairs"]:
        raise ValueError("path_ci_pair_bound_exceeded")
    if len(set(expected)) != len(expected):
        raise ValueError("path_ci_expected_pair_duplicate")
    grouped, seen = defaultdict(list), set()
    for pair in pairs:
        cutoff = aware_utc(pair.cutoff_at)
        key = pair.scope_id, cutoff
        if key not in expected or key in seen:
            raise ValueError("path_ci_pair_identity_invalid")
        if cutoff != cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError("path_ci_utc_daily_cutoff_required")
        source, left, right, label, end = map(
            aware_utc,
            (
                pair.input_known_at,
                pair.baseline_published_at,
                pair.candidate_published_at,
                pair.label_known_at,
                pair.window_end_at,
            ),
        )
        if not (
            source <= cutoff <= min(left, right)
            and max(left, right) <= cutoff + timedelta(minutes=1)
            and end == cutoff + timedelta(days=1, minutes=1)
            and end <= label <= clock
        ):
            raise ValueError("path_ci_pair_clock_invalid")
        if any(
            not value.is_finite() or not 0 <= value <= 1
            for value in (pair.baseline_loss, pair.candidate_loss)
        ):
            raise ValueError("path_ci_loss_invalid")
        seen.add(key)
        grouped[cutoff.date().isoformat()].append(pair.baseline_loss - pair.candidate_loss)
    reasons = []
    if seen != set(expected):
        reasons.append("path_ci_complete_pair_census_missing")
    events = None
    if event_blocks is None:
        reasons.append("authenticated_matching_event_blocks_unavailable")
    else:
        events_by_id, originals = {}, {}
        valid_cutoffs = {cutoff for _, cutoff in seen}
        for event in event_blocks:
            cutoff, known = aware_utc(event.cutoff_at), aware_utc(event.known_at)
            if (
                cutoff not in valid_cutoffs
                or known > clock
                or known < cutoff
                or not event.block_id
                or not event.original_ref
                or len(event.original_hash) != 64
                or any(c not in "0123456789abcdef" for c in event.original_hash)
            ):
                raise ValueError("path_ci_event_original_binding_invalid")
            previous = events_by_id.setdefault(event.block_id, event)
            if previous != event:
                raise ValueError("path_ci_event_block_conflict")
            source = event.original_ref, event.original_hash
            prior_id = originals.setdefault(source, event.block_id)
            if prior_id != event.block_id:
                raise ValueError("path_ci_event_original_counted_twice")
        events = len(events_by_id)
        if events < PROTOCOL["minimum_event_blocks"]:
            reasons.append("fewer_than_30_independent_event_blocks")
    if len(grouped) < PROTOCOL["minimum_time_blocks"]:
        reasons.append("fewer_than_100_independent_day_blocks")
    result = {
        "status": "insufficient" if reasons else "available",
        "ci": None,
        "protocol": PROTOCOL["version"],
        "protocol_hash": PROTOCOL_HASH,
        "confidence": PROTOCOL["confidence"],
        "seed": PROTOCOL["seed"],
        "replicates": PROTOCOL["replicates"],
        "unit": "UTC-day",
        "estimator": PROTOCOL["estimator"],
        "expected_pairs": len(expected),
        "complete_pairs": len(seen),
        "independent_day_blocks": len(grouped),
        "event_blocks": events,
        "reasons": reasons,
        "promotion_allowed": False,
    }
    if reasons:
        return result
    with localcontext() as context:
        context.prec, context.rounding = PROTOCOL["decimal_precision"], ROUND_HALF_EVEN
        # One day receives one weight, even when both BTC and ETH contribute.
        days = tuple(sum(values, Decimal(0)) / len(values) for _, values in sorted(grouped.items()))
        estimate = sum(days, Decimal(0)) / len(days)
        random = Random(PROTOCOL["seed"])
        samples = sorted(
            sum((days[random.randrange(len(days))] for _ in days), Decimal(0)) / len(days)
            for _ in range(PROTOCOL["replicates"])
        )
        alpha = (Decimal(1) - Decimal(PROTOCOL["confidence"])) / 2
        low = math.ceil(alpha * len(samples)) - 1
        high = math.ceil((1 - alpha) * len(samples)) - 1
        result["ci"] = {
            "lower": str(samples[low]),
            "upper": str(samples[high]),
            "point_estimate": str(estimate),
            **{
                key: result[key]
                for key in (
                    "protocol",
                    "protocol_hash",
                    "confidence",
                    "seed",
                    "replicates",
                    "unit",
                    "estimator",
                    "independent_day_blocks",
                    "event_blocks",
                )
            },
        }
    return result
