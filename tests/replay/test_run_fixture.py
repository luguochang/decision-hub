from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
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
    assert result["net_return_pct"] == pytest.approx(-2.4)


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
    fixture = json.loads(
        Path("fixtures/replay/powell-higher-for-longer.json").read_text()
    )
    fixture["fixture_id"] = "future-information.v1"
    fixture["observation"]["observed_at"] = "2026-08-25T09:00:03Z"
    future_fixture = tmp_path / "future-information.json"
    future_fixture.write_text(json.dumps(fixture))

    with pytest.raises(AgentExecutionError) as captured:
        asyncio.run(run_fixture(future_fixture, tmp_path / "future.sqlite3"))

    assert captured.value.error_code == "pit_future_information"
