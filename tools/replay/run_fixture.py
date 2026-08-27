from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    OutcomeCreate,
)
from packages.kernel.decision_hub_kernel.application.outcome import OutcomeService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRuntime
from packages.orchestration.langgraph import build_analyze_text_service
from packages.runtime_adapters.replay_runtime.runtime import ReplayAgentRuntime


class ReplayOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon: str
    return_pct: float
    direction_correct: bool
    available_at: datetime
    fees: float = 0
    slippage: float = 0
    quality_status: str = "fixture"


class ReplayFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_id: str
    split: str = "holdout"
    received_at: datetime
    observation: ObservationCreate
    outcomes: list[ReplayOutcome] = Field(default_factory=list)


class ReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "replay-report.v1"
    fixture_id: str
    split: str
    snapshot_cutoff_at: str | None
    snapshot_hash: str | None
    strategy_version: str
    runtime_id: str
    runtime_version: str
    pack_version: str
    status: str
    gate_status: str | None
    latency_ms: int | None
    cost_usd: float | None = None
    cost_status: str
    call_count: int
    evaluation_count: int
    mean_brier_score: float | None
    net_return_pct: float | None
    forecast_horizons: list[str]
    error_codes: list[str]
    artifact_fingerprint: str | None
    stage: str = "replay"
    event_family: str = "unknown"
    evidence_coverage: float | None = None
    directional_accuracy: float | None = None
    failure_counts: dict[str, int] = Field(default_factory=dict)


def _artifact_fingerprint(artifact: Any) -> str | None:
    if artifact is None:
        return None
    payload = {
        "gate_status": artifact.gate_status.value,
        "headline": artifact.headline,
        "summary": artifact.summary,
        "facts": artifact.facts,
        "inferences": artifact.inferences,
        "counter_thesis": artifact.counter_thesis,
        "uncertainty": artifact.uncertainty,
        "transmission_chain": artifact.transmission_chain,
        "citations": artifact.citations,
        "forecasts": [
            {
                "horizon": item.horizon,
                "direction": item.direction.value,
                "probability": item.probability,
                "trigger": item.trigger,
                "invalidation": item.invalidation,
            }
            for item in artifact.forecasts
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def run_fixture(
    fixture: Path,
    database_path: Path,
    *,
    strategy_version: str = "baseline.v1",
    runtime: AgentRuntime | None = None,
) -> dict[str, object]:
    data = ReplayFixture.model_validate_json(fixture.read_text())
    database = Database(f"sqlite+pysqlite:///{database_path}")
    database.create_all()
    selected_runtime = runtime or ReplayAgentRuntime()
    event_id, run_id, admitted = await build_analyze_text_service(
        database,
        selected_runtime,
        strategy_version=strategy_version,
        admission_clock=lambda: data.received_at,
    ).submit_and_run(data.observation)
    run = database.get_run_view(run_id)
    inspector = database.get_run_inspector(run_id)
    if run is None or inspector is None:
        raise RuntimeError("replay did not produce a run inspector")
    forecasts_by_horizon = {
        item.horizon: item for item in inspector.artifact.forecasts
    } if inspector.artifact else {}
    outcomes = OutcomeService(database)
    for label in data.outcomes:
        forecast = forecasts_by_horizon.get(label.horizon)
        if forecast is None:
            raise ValueError(f"fixture outcome has no forecast horizon: {label.horizon}")
        outcomes.record(
            OutcomeCreate(
                forecast_id=forecast.forecast_id,
                return_pct=label.return_pct,
                direction_correct=label.direction_correct,
                fees=label.fees,
                slippage=label.slippage,
                quality_status=label.quality_status,
            )
        )
    inspector = database.get_run_inspector(run_id)
    if inspector is None:
        raise RuntimeError("replay inspector disappeared after outcome evaluation")
    mean_brier_score = (
        sum(item.brier_score for item in inspector.evaluations)
        / len(inspector.evaluations)
        if inspector.evaluations
        else None
    )
    net_return_pct = (
        sum(item.net_return_pct for item in inspector.evaluations)
        if inspector.evaluations
        else None
    )
    report = ReplayReport(
        fixture_id=data.fixture_id,
        split=data.split,
        snapshot_cutoff_at=(
            inspector.snapshot_cutoff_at.isoformat()
            if inspector.snapshot_cutoff_at
            else None
        ),
        snapshot_hash=inspector.snapshot_hash,
        strategy_version=strategy_version,
        runtime_id=getattr(selected_runtime, "runtime_id", "unknown"),
        runtime_version=getattr(selected_runtime, "runtime_version", "unknown"),
        pack_version="crypto_macro.v1",
        status=run.status.value,
        gate_status=run.gate_status.value if run.gate_status else None,
        latency_ms=run.latency_ms,
        cost_usd=run.cost_usd,
        cost_status=(
            "estimated"
            if run.cost_usd is not None
            else "unknown"
        ),
        call_count=len(inspector.calls),
        evaluation_count=inspector.evaluation_count,
        mean_brier_score=mean_brier_score,
        net_return_pct=net_return_pct,
        forecast_horizons=(
            [item.horizon for item in inspector.artifact.forecasts]
            if inspector.artifact
            else []
        ),
        error_codes=sorted(
            {
                item.error_code for item in inspector.calls if item.error_code is not None
            }
        ),
        artifact_fingerprint=_artifact_fingerprint(inspector.artifact),
        stage=data.split if data.split in {"replay", "holdout", "shadow"} else "replay",
        event_family=data.observation.event_hint or "unknown",
        evidence_coverage=(
            min(
                1.0,
                len(inspector.artifact.citations)
                / max(1, len(inspector.artifact.facts)),
            )
            if inspector.artifact
            else None
        ),
        directional_accuracy=(
            sum(1 for item in inspector.evaluations if item.direction_correct)
            / len(inspector.evaluations)
            if inspector.evaluations
            else None
        ),
        failure_counts={
            code: sum(1 for item in inspector.calls if item.error_code == code)
            for code in sorted(
                {item.error_code for item in inspector.calls if item.error_code is not None}
            )
        },
    )
    return {
        "fixture_id": data.fixture_id,
        "event_id": event_id,
        "run_id": run_id,
        "admitted": admitted,
        "status": run.status.value,
        "gate_status": run.gate_status.value if run.gate_status else None,
        "runtime_id": report.runtime_id,
        "runtime_version": report.runtime_version,
        "latency_ms": run.latency_ms,
        "cost_status": "unknown",
        "cost_usd": report.cost_usd,
        "call_count": len(inspector.calls),
        "strategy_version": strategy_version,
        "snapshot_cutoff_at": report.snapshot_cutoff_at,
        "snapshot_hash": report.snapshot_hash,
        "forecast_horizons": report.forecast_horizons,
        "evaluation_count": report.evaluation_count,
        "mean_brier_score": report.mean_brier_score,
        "net_return_pct": report.net_return_pct,
        "artifact_fingerprint": report.artifact_fingerprint,
        "stage": report.stage,
        "event_family": report.event_family,
        "evidence_coverage": report.evidence_coverage,
        "directional_accuracy": report.directional_accuracy,
        "failure_counts": report.failure_counts,
        "report": report.model_dump(mode="json"),
    }


async def compare_fixture(
    fixture: Path,
    baseline_database: Path,
    candidate_database: Path,
) -> dict[str, object]:
    if baseline_database.resolve() == candidate_database.resolve():
        raise ValueError("baseline and candidate replay require independent databases")
    baseline = await run_fixture(fixture, baseline_database, strategy_version="baseline.v1")
    candidate = await run_fixture(fixture, candidate_database, strategy_version="candidate.v1")
    baseline_report = ReplayReport.model_validate(baseline["report"])
    candidate_report = ReplayReport.model_validate(candidate["report"])
    return {
        "schema_version": "replay-comparison.v1",
        "fixture_id": baseline_report.fixture_id,
        "split": baseline_report.split,
        "baseline": baseline_report.model_dump(mode="json"),
        "candidate": candidate_report.model_dump(mode="json"),
        "comparison": {
            "same_snapshot": baseline_report.snapshot_hash == candidate_report.snapshot_hash,
            "same_forecast_horizons": baseline_report.forecast_horizons
            == candidate_report.forecast_horizons,
            "candidate_overwrote_baseline": False,
            "mean_brier_delta": (
                candidate_report.mean_brier_score - baseline_report.mean_brier_score
                if candidate_report.mean_brier_score is not None
                and baseline_report.mean_brier_score is not None
                else None
            ),
            "net_return_delta_pct": (
                candidate_report.net_return_pct - baseline_report.net_return_pct
                if candidate_report.net_return_pct is not None
                and baseline_report.net_return_pct is not None
                else None
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic Decision Hub PIT fixture")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--database", type=Path, default=Path("tmp/replay.sqlite3"))
    parser.add_argument("--compare-candidate", type=Path)
    parser.add_argument("--baseline-database", type=Path)
    args = parser.parse_args()
    if args.compare_candidate:
        baseline_database = args.baseline_database or args.database
        result = asyncio.run(
            compare_fixture(args.fixture, baseline_database, args.compare_candidate)
        )
    else:
        result = asyncio.run(run_fixture(args.fixture, args.database))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
