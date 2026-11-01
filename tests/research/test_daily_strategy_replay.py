from __future__ import annotations

import pytest

from packages.kernel.decision_hub_kernel.decision.daily_strategy import (
    DailyStrategyInputError,
    compute_daily_strategy,
)


def _bars(values: list[float]) -> list[dict[str, object]]:
    return [
        {
            "date": f"2026-01-{index + 1:03d}",
            "open": value - 0.5,
            "high": value + 1.0,
            "low": value - 1.0,
            "close": value,
            "volume": 1000.0,
        }
        for index, value in enumerate(values)
    ]


@pytest.mark.parametrize(
    ("case_name", "values", "expected"),
    [
        ("bullish_trend", [100.0 + index for index in range(80)], "bullish"),
        ("bearish_trend", [200.0 - index for index in range(80)], "bearish"),
        ("range_no_edge", [100.0 + (index % 2) for index in range(80)], "range"),
    ],
)
def test_four_directional_replay_cases_keep_distinct_outputs(
    case_name: str, values: list[float], expected: str
) -> None:
    receipt = compute_daily_strategy(_bars(values), evidence_refs=[f"replay:{case_name}"])

    assert receipt.outlook == expected
    assert receipt.evidence_refs == (f"replay:{case_name}",)
    assert receipt.probability is None
    assert receipt.probability_status == "unavailable"
    assert receipt.as_dict()["strategy_version"] == "daily-trend-v0.1"


def test_insufficient_daily_replay_is_unresolved_without_a_direction() -> None:
    with pytest.raises(DailyStrategyInputError, match="need_60"):
        compute_daily_strategy(_bars([100.0 + index for index in range(40)]))
