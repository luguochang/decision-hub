"""New scoring controls: synthetic probabilities, never mature market samples."""

import importlib
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

MAIN = Path("E:/project/study/codex/dsh/decision-hub")
CANDIDATE = Path(__file__).resolve().parents[2]


def candidate(relative: str) -> Any:
    sys.path.insert(0, str(MAIN))
    path = CANDIDATE / relative
    assert path.is_file(), "the original forecast chain has no explicit cross-horizon scoring v2"
    name = relative.removesuffix(".py").replace("/", ".")
    importlib.import_module(name.rpartition(".")[0])
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == path
    return module


def metrics() -> Any:
    return candidate("packages/kernel/decision_hub_kernel/ports/perpetual_forecast_scoring.py")


def estimate(up: Any = "0.7", neutral: Any = "0.2", down: Any = "0.1"):
    return [
        dict(outcome_id=k, value=v)
        for k, v in zip(("up", "neutral", "down"), (up, neutral, down), strict=True)
    ]


@pytest.mark.parametrize(
    "horizon,minutes,pages", [("4h", 240, 1), ("24h", 1440, 5), ("72h", 4320, 15)]
)
def test_new_policy_requires_every_boundary_minute_without_changing_old_policy(
    horizon: Any, minutes: Any, pages: Any
):
    value = metrics().horizon_policy(horizon)
    assert value["minutes"] == minutes
    assert value["required_closes"] == minutes + 1
    assert value["max_pages"] == pages
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import MAX_PAGES

    assert MAX_PAGES == 6


def test_scores_retain_exact_probability_and_reliability_observations():
    result = metrics().score_forecast(estimate(), "neutral")
    assert Decimal(result["multiclass_brier"]) == Decimal("1.14")
    assert result["log_loss"]["status"] == "finite"
    assert abs(Decimal(result["log_loss"]["value"]) + Decimal("0.2").ln()) < Decimal("1e-27")
    assert result["reliability_observations"] == [
        {"outcome_id": "up", "probability": "0.7", "observed": 0},
        {"outcome_id": "neutral", "probability": "0.2", "observed": 1},
        {"outcome_id": "down", "probability": "0.1", "observed": 0},
    ]


def test_zero_true_probability_remains_infinite_without_epsilon_or_fake_score():
    result = metrics().score_forecast(estimate("1", "0", "0"), "down")
    assert result["log_loss"] == {
        "status": "infinite",
        "value": None,
        "reason": "observed_outcome_probability_zero",
    }
    assert result["multiclass_brier"] == "2"


def test_unknown_probabilities_are_unscored_and_do_not_manufacture_reliability_rows():
    result = metrics().score_forecast(None, "up")
    assert result["multiclass_brier"] is None
    assert result["log_loss"] == {
        "status": "unavailable",
        "value": None,
        "reason": "direction_probabilities_unavailable",
    }
    assert result["reliability_observations"] == []


@pytest.mark.parametrize(
    "probabilities,category",
    [
        (estimate("0.8", "0.2", "0.1"), "up"),
        (estimate("NaN", "0", "1"), "up"),
        (estimate(), "flat"),
    ],
)
def test_invalid_probability_partition_or_class_is_rejected(probabilities: Any, category: Any):
    with pytest.raises(ValueError):
        metrics().score_forecast(probabilities, category)


@pytest.mark.parametrize("horizon,minutes", [("4h", 240), ("24h", 1440), ("72h", 4320)])
def test_frozen_windows_require_exact_endpoint_minute_coverage(horizon: Any, minutes: Any):
    policy = metrics().horizon_policy(horizon)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=minutes)
    assert metrics().expected_closes(start, end, horizon) == tuple(
        start + timedelta(minutes=i) for i in range(policy["required_closes"])
    )


@pytest.mark.parametrize("change", ["wrong_duration", "not_minute", "unsupported"])
def test_window_contract_rejects_wrong_horizon_or_boundary(change: Any):
    module = metrics()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=4)
    if change == "wrong_duration":
        end += timedelta(minutes=1)
    if change == "not_minute":
        start += timedelta(seconds=1)
    with pytest.raises(ValueError):
        module.expected_closes(start, end, "1h" if change == "unsupported" else "4h")
