"""Deterministic base rate on a synthetic hourly close grid; no network or model."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.kernel.decision_hub_kernel.ports.perpetual_base_rate import (
    direction_base_rate,
    score_against_base_rate,
)

AS_OF = datetime(2026, 10, 23, 5, 30, tzinfo=UTC)
END = datetime(2026, 10, 23, 5, tzinfo=UTC)


def grid(days=90, pattern=("100", "101", "100", "99")):
    count = days * 24 + 1
    return [
        (END - timedelta(hours=count - 1 - index), pattern[index % len(pattern)])
        for index in range(count)
    ]


def probabilities(result):
    return {row["outcome_id"]: Decimal(row["value"]) for row in result["probabilities"]}


def test_flat_market_is_mostly_neutral_and_sums_to_one():
    result = direction_base_rate(
        closes=grid(pattern=("100",)),
        threshold_fraction="0.003",
        horizon_seconds=14400,
        as_of=AS_OF,
    )
    values = probabilities(result)
    assert result["status"] == "available"
    assert result["samples"] == 90 * 24 - 4 + 1
    assert result["counts"] == {"up": 0, "neutral": result["samples"], "down": 0}
    assert sum(values.values()) == 1
    assert values["neutral"] > Decimal("0.99") and values["up"] > 0 and values["down"] > 0
    assert result["terminal_return_quantiles"]["up"] is None


def test_rising_market_counts_up_and_reports_amplitudes():
    closes = [
        (stamp, str(Decimal("100") * Decimal("1.001") ** index))
        for index, (stamp, _) in enumerate(grid())
    ]
    result = direction_base_rate(
        closes=closes, threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
    )
    assert result["counts"]["up"] == result["samples"]
    p50 = Decimal(result["terminal_return_quantiles"]["up"]["p50"])
    assert abs(p50 - (Decimal("1.001") ** 4 - 1)) < Decimal("1e-15")


def test_future_closes_are_ignored():
    closes = grid() + [(END + timedelta(hours=1), "500")]
    base = direction_base_rate(
        closes=grid(), threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
    )
    later = direction_base_rate(
        closes=closes, threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
    )
    assert base == later


def test_missing_closes_make_it_unavailable_without_padding():
    closes = [row for index, row in enumerate(grid()) if index % 10]
    result = direction_base_rate(
        closes=closes, threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
    )
    assert result["status"] == "unavailable" and result["reason"] == "closes_missing"


@pytest.mark.parametrize(
    "closes, error",
    [
        ([(END - timedelta(minutes=30), "100")], "perp_base_rate_close_unaligned"),
        ([(END, "100"), (END, "101")], "perp_base_rate_conflicting_close"),
        ([(END, "0")], "perp_base_rate_price_invalid"),
    ],
)
def test_invalid_closes_are_rejected(closes, error):
    with pytest.raises(ValueError, match=error):
        direction_base_rate(
            closes=closes, threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
        )


def test_skill_is_negative_for_symmetric_40_20_40_on_neutral_outcome():
    base = direction_base_rate(
        closes=grid(), threshold_fraction="0.003", horizon_seconds=14400, as_of=AS_OF
    )
    model = [
        {"outcome_id": "up", "value": "0.4"},
        {"outcome_id": "neutral", "value": "0.2"},
        {"outcome_id": "down", "value": "0.4"},
    ]
    score = score_against_base_rate(model, base, "neutral")
    assert score["reason"] is None and Decimal(score["brier_skill"]) < 0
    assert score_against_base_rate(model, None, "down")["reason"] == "base_rate_unavailable"
