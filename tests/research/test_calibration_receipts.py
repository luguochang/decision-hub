# pyright: reportPrivateUsage=false
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import ResearchReview
from packages.evals.forecast_calibration import (
    ForecastCalibrationReport,
    ForecastCalibrationSample,
    ForecastReliabilityBin,
    evaluate_forecast_calibration,
)
from packages.kernel.decision_hub_kernel.application.calibration import (
    StrategyCalibrationService,
)
from packages.kernel.decision_hub_kernel.application.commit import (
    CommitDecisionService,
    _remove_unattested_calibration,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.persistence.db import ForecastRecord
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    ResearchRequestFactory,
    _admitted_run,
    _result,
)


def _report(horizon: str, *, strong: bool = True):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    samples = []
    for index in range(180):
        outcome = index % 5 != 0
        raw = (0.82 if outcome else 0.18) if strong else 0.5
        samples.append(
            ForecastCalibrationSample(
                sample_id=f"{horizon}-{index}",
                as_of=start + timedelta(days=index),
                raw_probability=raw,
                outcome=outcome,
                realized_return_pct=1.0 if outcome else -1.0,
                trading_cost_pct=0.05,
                regime=("risk_on", "risk_off", "range")[index % 3],
            )
        )
    return evaluate_forecast_calibration(
        samples,
        strategy_version="btc-oos.v1",
        horizon=horizon,
        evaluated_at=NOW,
    )


def test_receipt_is_immutable_idempotent_and_exactly_versioned(tmp_path: Path) -> None:
    database, _event_id, _run_id = _admitted_run(tmp_path)
    service = StrategyCalibrationService(database)
    report = _report("24h")

    receipt_id = service.persist(report)

    assert service.persist(report) == receipt_id
    assert service.validates(receipt_id, strategy_version="btc-oos.v1", horizon="24h")
    assert not service.validates(receipt_id, strategy_version="btc-oos.v2", horizon="24h")
    assert not service.validates(receipt_id, strategy_version="btc-oos.v1", horizon="72h")
    assert service.list()[0].sample_count == 180
    assert service.list()[0].passed is True


def test_directional_calibration_requires_a_passed_receipt_for_every_horizon(
    tmp_path: Path,
) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    result = _result(request).model_copy(
        update={
            "horizons": [
                item.model_copy(update={"probability_status": "calibrated"})
                for item in _result(request).horizons
            ]
        }
    )
    service = StrategyCalibrationService(database)
    receipts = {horizon: service.persist(_report(horizon)) for horizon in ("30m", "24h", "72h")}

    normalized, attested = _remove_unattested_calibration(
        result,
        strategy_version="btc-oos.v1",
        calibration_receipt_ids=receipts,
        receipt_validates=service.validates,
    )
    assert attested is True
    assert {item.probability_status for item in normalized.horizons} == {"calibrated"}

    missing = dict(receipts)
    missing.pop("72h")
    normalized, attested = _remove_unattested_calibration(
        result,
        strategy_version="btc-oos.v1",
        calibration_receipt_ids=missing,
        receipt_validates=service.validates,
    )
    assert attested is False
    missing_horizon = next(item for item in normalized.horizons if item.horizon == "72h")
    assert missing_horizon.probability_status == "uncalibrated"


def test_passed_receipts_and_native_review_reach_directional_gate(tmp_path: Path) -> None:
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(database, pack_root=PACK_ROOT, clock=lambda: NOW).build(
        event_id, run_id
    )
    base = _result(request)
    evidence = [
        item.model_copy(update={"quality": "accepted"}) for item in base.evidence_candidates
    ]
    coverage = base.final_coverage.model_copy(
        update={
            "status": "sufficient",
            "covered_requirement_ids": ["event_identity"],
            "hard_coverage_ratio": 1.0,
            "gaps": [],
            "conflicts": [],
        }
    )
    candidate = base.model_copy(
        update={
            "evidence_candidates": evidence,
            "final_coverage": coverage,
            "horizons": [
                item.model_copy(update={"probability_status": "calibrated"})
                for item in base.horizons
            ],
            "reviews": [],
        }
    )
    target_claim = candidate.causal_case.thesis if candidate.causal_case else "direction"
    evidence_refs = [evidence[0].evidence_id]
    challenge = "The directional edge may be unstable outside the evaluation regimes."
    receipt_hash = independent_review_receipt_hash(
        role="independent_reviewer",
        target_claim=target_claim,
        evidence_refs=evidence_refs,
        challenge=challenge,
        resolution="accepted",
        limitations=[],
    )
    review = ResearchReview(
        review_id=f"review_{receipt_hash[:32]}",
        role="independent_reviewer",
        target_claim=target_claim,
        evidence_refs=evidence_refs,
        result_hash=research_result_content_hash(candidate),
        challenge=challenge,
        resolution="accepted",
        limitations=[],
        created_at=NOW,
    )
    candidate = candidate.model_copy(update={"reviews": [review]})
    ResearchTaskService(database, clock=lambda: NOW).freeze(request)
    ResearchEvidenceService(database).freeze_decision_snapshot(
        run_id=run_id,
        evidence_refs=[],
        cutoff_at=NOW,
    )
    database.save_role_output(
        run_id,
        "independent_reviewer",
        review.model_dump(mode="json"),
        "research-review.native-attested.v1",
    )
    calibration = StrategyCalibrationService(database)
    receipts = {
        horizon: calibration.persist(_passed_report(horizon)) for horizon in ("30m", "24h", "72h")
    }

    artifact_id, gate = CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
        run_id,
        event_id,
        candidate,
        forecast_strategy_version="btc-oos.v1",
        calibration_receipt_ids=receipts,
    )

    assert gate.status.value == "publish"
    with database.session() as session:
        forecasts = session.query(ForecastRecord).filter_by(artifact_id=artifact_id).all()
        assert {item.direction for item in forecasts} == {"short"}
        assert {item.strategy_version for item in forecasts} == {"btc-oos.v1"}
        assert {item.calibration_receipt_id for item in forecasts} == set(receipts.values())


def _passed_report(horizon: str) -> ForecastCalibrationReport:
    return ForecastCalibrationReport(
        strategy_version="btc-oos.v1",
        horizon=horizon,
        dataset_hash=(horizon.encode().hex() + "0" * 64)[:64],
        sample_count=180,
        evaluated_sample_count=150,
        fold_count=5,
        gap_samples=1,
        accuracy=0.66,
        accuracy_lower_bound=0.58,
        brier_score=0.20,
        baseline_brier_score=0.24,
        log_loss=0.60,
        net_return_pct=12.0,
        max_drawdown_pct=8.0,
        coverage_ratio=0.83,
        max_regime_share=0.40,
        calibration_slope=1.1,
        calibration_intercept=-0.05,
        reliability_bins=[
            ForecastReliabilityBin(
                lower_bound=0.6,
                upper_bound=0.8,
                count=150,
                mean_probability=0.66,
                outcome_rate=0.66,
            )
        ],
        passed=True,
        failure_reasons=[],
        evaluated_at=NOW,
    )
