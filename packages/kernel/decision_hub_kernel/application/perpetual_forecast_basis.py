"""Read-only probability comparison from already authenticated publication originals."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext
from typing import Any

from ..ports.market_path import canonical_hash

OUTCOMES = {"up", "neutral", "down"}


def _probabilities(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    values = {row["outcome_id"]: Decimal(row["value"]) for row in rows}
    if (len(rows) != 3 or set(values) != OUTCOMES
            or any(not value.is_finite() or not 0 <= value <= 1 for value in values.values())
            or sum(values.values()) != 1):
        raise ValueError("perpetual_forecast_basis_probabilities_invalid")
    return values


def compare_probabilities(
    baseline: list[dict[str, Any]], model: list[dict[str, Any]] | None,
) -> tuple[str, str | None]:
    """Exact decimal equality is a numeric fact, never a model-value assessment."""
    with localcontext() as context:
        context.prec = max(100, *(len(row["value"]) + 10 for row in [*baseline, *(model or [])]))
        base = _probabilities(baseline)
        if model is None:
            return "unknown", None
        predicted = _probabilities(model)
        difference = max(abs(base[key] - predicted[key]) for key in OUTCOMES) * 100
        text = format(difference, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return ("equal" if difference == 0 else "different"), text


def forecast_basis(
    *, run_id: str, horizon: str, materials: dict[str, Any],
    materials_ref: dict[str, Any], derivation: dict[str, Any],
    target: dict[str, Any], market: dict[str, Any], model_ref: dict[str, Any],
) -> dict[str, Any] | None:
    """No DB, current data, historical recomputation or frozen-original mutation."""
    baseline = materials.get("direction_base_rate")
    if baseline is None or baseline.get("status") != "available":
        return None
    window = baseline.get("window_90d")
    if window is None or window.get("status") != "available":
        return None
    context = materials["context"]
    if (context["horizon"] != horizon or target["horizon"] != horizon
            or baseline["horizon_seconds"] != target["duration_seconds"]
            or window["horizon_seconds"] != target["duration_seconds"]
            or baseline["inst_id"] != context["instrument_id"].removeprefix("okx:")
            or Decimal(baseline["threshold_fraction"]) != Decimal(target["threshold_fraction"])
            or Decimal(window["threshold_fraction"]) != Decimal(target["threshold_fraction"])
            or materials.get("direction_base_rate_ref") != derivation["admission_ref"]
            or materials.get("direction_base_rate_path") != "/direction_base_rate"
            or not datetime.fromisoformat(window["window_start"])
            < datetime.fromisoformat(window["window_end"])
            <= datetime.fromisoformat(baseline["as_of"])
            <= datetime.fromisoformat(market["direction_distribution"]["as_of"])
            or canonical_hash(materials) != materials_ref["hash"]
            or derivation["research_materials_ref"] != materials_ref
            or canonical_hash(market) != model_ref["hash"]):
        raise ValueError("perpetual_forecast_basis_binding_mismatch")
    model = market["direction_distribution"]["probabilities"]
    relation, difference = compare_probabilities(window["probabilities"], model)
    return dict(
        schema_version="perpetual-forecast-basis.v1", run_id=run_id, horizon=horizon,
        research_materials_ref=materials_ref, research_materials=materials,
        derivation=derivation, market_distribution_ref=model_ref,
        baseline_window="window_90d", baseline_probabilities=window["probabilities"],
        model_probabilities=model, relation=relation,
        max_absolute_difference_pp=difference,
        target_window=dict(window_start=target["window_start"], window_end=target["window_end"],
                           threshold_fraction=target["threshold_fraction"]),
    )
