from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from tools.replay.run_fixture import compare_fixture, run_fixture


def test_replay_fixture_produces_versioned_inspector(tmp_path: Path) -> None:
    result = asyncio.run(
        run_fixture(
            Path("fixtures/replay/powell-higher-for-longer.json"),
            tmp_path / "replay.sqlite3",
        )
    )

    assert result["fixture_id"] == "powell-higher-for-longer.v1"
    assert result["runtime_version"] == "replay.v1"
    assert result["call_count"] == 3
    assert result["cost_status"] == "unknown"
    assert result["evaluation_count"] == 3
    assert result["mean_brier_score"] == pytest.approx(0.22973333333333334)
    assert result["net_return_pct"] == pytest.approx(1.2)


def test_replay_comparison_is_deterministic_and_independent(tmp_path: Path) -> None:
    result = asyncio.run(
        compare_fixture(
            Path("fixtures/replay/powell-higher-for-longer.json"),
            tmp_path / "baseline.sqlite3",
            tmp_path / "candidate.sqlite3",
        )
    )
    comparison = cast(dict[str, Any], result["comparison"])
    baseline = cast(dict[str, Any], result["baseline"])
    candidate = cast(dict[str, Any], result["candidate"])

    assert comparison["same_snapshot"] is True
    assert comparison["same_forecast_horizons"] is True
    assert comparison["candidate_overwrote_baseline"] is False
    assert baseline["strategy_version"] == "baseline.v1"
    assert candidate["strategy_version"] == "candidate.v1"
    assert baseline["evaluation_count"] == 3
    assert candidate["evaluation_count"] == 3


def test_replay_rejects_future_information(tmp_path: Path) -> None:
    fixture = json.loads(Path("fixtures/replay/powell-higher-for-longer.json").read_text())
    fixture["fixture_id"] = "future-information.v1"
    fixture["observation"]["observed_at"] = "2026-08-25T09:00:03Z"
    future_fixture = tmp_path / "future-information.json"
    future_fixture.write_text(json.dumps(fixture))

    with pytest.raises(AgentExecutionError) as captured:
        asyncio.run(run_fixture(future_fixture, tmp_path / "future.sqlite3"))

    assert captured.value.error_code == "pit_future_information"


class DirectionRuntime(FakeAgentRuntime):
    def __init__(self, direction):
        self.direction = direction
        self.calls = 0

    async def execute(self, request):
        self.calls += 1
        result = await super().execute(request)
        if request.role == "decision_synthesis":
            return replace(result, payload={**result.payload, "direction": self.direction})
        return result


@pytest.mark.parametrize("market_return", [-2, 0, 2])
def test_opposite_predictions_use_common_market_return_not_shared_correctness(
    tmp_path, market_return
):
    payload = json.loads(Path("fixtures/replay/powell-higher-for-longer.json").read_text())
    for item in payload["outcomes"]:
        item.update(return_pct=market_return, direction_correct=True)
    fixture = tmp_path / "common-market.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    for direction, sign in [("long", 1), ("short", -1)]:
        result = asyncio.run(
            run_fixture(
                fixture,
                tmp_path / f"{direction}.sqlite3",
                runtime=DirectionRuntime(direction),
            )
        )
        correct = market_return * sign > 0
        assert result["evaluation_count"] == 3
        assert result["directional_accuracy"] == float(correct)
        assert result["mean_brier_score"] == pytest.approx((0.58 - int(correct)) ** 2)
        assert result["net_return_pct"] == pytest.approx(3 * (sign * market_return - 0.2))
        assert result["scorer_version"] == "replay.direction.v2"
        assert all(x["market_return_pct"] == market_return for x in result["scored_outcomes"])


@pytest.mark.parametrize("direction", ["neutral", "no_trade"])
def test_abstention_has_no_directional_evaluation_or_assumed_return(tmp_path, direction):
    result = asyncio.run(
        run_fixture(
            Path("fixtures/replay/powell-higher-for-longer.json"),
            tmp_path / "abstention.sqlite3",
            runtime=DirectionRuntime(direction),
        )
    )
    assert result["evaluation_count"] == 0
    assert result["abstention_count"] == 3
    assert result["mean_brier_score"] is result["net_return_pct"] is None
    assert result["directional_accuracy"] is None
    assert all(x["direction_correct"] is None for x in result["scored_outcomes"])


@pytest.mark.parametrize("invalid", ["unknown_basis", "duplicate", "nonfinite", "negative_fee"])
def test_invalid_market_labels_rejected_before_model_execution(tmp_path, invalid):
    payload = json.loads(Path("fixtures/replay/powell-higher-for-longer.json").read_text())
    if invalid == "unknown_basis":
        payload["outcomes"][0].pop("return_basis")
    elif invalid == "duplicate":
        payload["outcomes"].append(payload["outcomes"][0])
    elif invalid == "nonfinite":
        payload["outcomes"][0]["return_pct"] = float("nan")
    else:
        payload["outcomes"][0]["fees"] = -1
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    runtime = DirectionRuntime("long")
    with pytest.raises(ValueError):
        asyncio.run(run_fixture(fixture, tmp_path / "unused.sqlite3", runtime=runtime))
    assert runtime.calls == 0
    assert not (tmp_path / "unused.sqlite3").exists()
