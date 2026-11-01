from __future__ import annotations

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchOutlook, ResearchStrategyReceipt
from packages.kernel.decision_hub_kernel.decision.daily_strategy import (
    DailyStrategyInputError,
    compute_daily_strategy,
)


def _bars(values: list[float]) -> list[dict[str, object]]:
    return [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": value - 0.5,
            "high": value + 1.0,
            "low": value - 1.0,
            "close": value,
            "volume": 1000 + index,
        }
        for index, value in enumerate(values)
    ]


def test_daily_strategy_requires_closed_history() -> None:
    with pytest.raises(DailyStrategyInputError, match="need_60"):
        compute_daily_strategy(_bars([100 + index for index in range(59)]))


def test_daily_strategy_emits_a_recomputable_bullish_outlook() -> None:
    result = compute_daily_strategy(
        _bars([100 + index for index in range(80)]), evidence_refs=["ev-1"]
    )

    assert result.outlook == "bullish"
    assert "持有倾向" in result.action_candidate
    assert "条件已成立" in result.trigger
    assert result.probability is None
    assert result.probability_status == "unavailable"
    assert result.evidence_refs == ("ev-1",)
    assert len(result.data_hash) == 64
    assert result.as_dict()["features"]["sma_short"] > result.as_dict()["features"]["sma_long"]


def test_daily_strategy_emits_a_bearish_outlook() -> None:
    result = compute_daily_strategy(_bars([200 - index for index in range(80)]))

    assert result.outlook == "bearish"
    assert "降低多头暴露" in result.action_candidate
    assert "SMA60" in result.invalidation


def test_daily_strategy_keeps_mixed_trend_as_range() -> None:
    values = [100 + index for index in range(60)] + [150 - index for index in range(20)]

    result = compute_daily_strategy(_bars(values))

    assert result.outlook == "range"
    assert "趋势分歧" in result.action_candidate
    assert "每个 UTC 日收盘" in result.trigger
    assert result.features["return_2d_pct"] < 0


def test_daily_strategy_hash_is_stable_and_order_is_required() -> None:
    values = _bars([100 + index for index in range(80)])
    first = compute_daily_strategy(values)
    second = compute_daily_strategy(values)
    assert first.data_hash == second.data_hash

    with pytest.raises(DailyStrategyInputError, match="dates_not_strictly_increasing"):
        compute_daily_strategy([*values[:10], *reversed(values[10:])])


def test_daily_strategy_receipt_fits_research_outlook_contract() -> None:
    receipt = ResearchStrategyReceipt.model_validate(
        compute_daily_strategy(_bars([100 + index for index in range(80)])).as_dict()
    )
    outlook = ResearchOutlook(
        schema_version="research-outlook.v1",
        horizon="7d",
        outlook=receipt.outlook,
        basis="rule_based",
        action_candidate=receipt.action_candidate,
        trigger=receipt.trigger,
        counter_case=receipt.counter_case,
        rationale=receipt.rationale,
        invalidation=receipt.invalidation,
        evidence_refs=list(receipt.evidence_refs),
        missing_impact=[],
        probability=receipt.probability,
        probability_status=receipt.probability_status,
        strategy_status="baseline_only",
        execution_permission=False,
        strategy_receipt=receipt,
    )

    assert outlook.horizon == "7d"
    assert outlook.execution_permission is False
    assert outlook.strategy_receipt is not None
