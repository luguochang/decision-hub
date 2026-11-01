"""Publication must cap later windows without granting calibration or trading."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application.calibration import StrategyCalibrationService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    ExecutionPermissionRecord,
    ForecastRecord,
    GateDecisionRecord,
    RunRoleOutputRecord,
)
from tests.kernel.test_research_publish_horizon_contract import NOW, _attest, _seed
from tests.research.test_calibration_receipts import _passed_report

Window = Literal["30m", "24h", "72h"]
Action = Literal["long", "short", "neutral", "no_trade"]


def _windows(
    result: ResearchSessionResult,
    *,
    later_window: Window,
    probability: float,
    action: Action,
    calibrated: bool,
) -> ResearchSessionResult:
    return result.model_copy(update={
        "horizons": [
            item.model_copy(update={
                "action": action,
                "subjective_probability": probability if item.horizon == later_window else 0.6,
                "probability_status": "calibrated" if calibrated else "uncalibrated",
            })
            for item in result.horizons
        ],
    })


@pytest.mark.parametrize("later_window", ["24h", "72h"])
@pytest.mark.parametrize("provenance", ["kernel", "model_claim", "uncalibrated"])
def test_later_window_over_cap_suppresses_forecasts_and_retains_research(
    tmp_path: Path, later_window: Window, provenance: str,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, _windows(
            result, later_window=later_window, probability=0.66, action="short",
            calibrated=provenance != "uncalibrated",
        ))
        original = result.model_dump_json()
        receipts = {
            name: StrategyCalibrationService(database).persist(_passed_report(name))
            for name in ("30m", "24h", "72h")
        } if provenance == "kernel" else None
        service = CommitDecisionService(database, clock=lambda: NOW)
        artifact_id, gate = service.commit_research_result(
            run_id, event_id, result, forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        assert gate.status == GateStatus.research_only
        assert result.model_dump_json() == original
        with database.session() as session:
            cap = session.query(GateDecisionRecord).filter_by(
                artifact_id=artifact_id, rule_id="probability_cap",
            ).one()
            assert cap.status == "fail"
            assert cap.reason_code == "uncalibrated_probability_capped"
            forecasts = session.query(ForecastRecord).filter_by(artifact_id=artifact_id).all()
            assert len(forecasts) == 3
            assert {row.direction for row in forecasts} == {"no_trade"}
            assert {row.probability for row in forecasts} == {0.5}
            outlooks = session.query(AnalyticalOutlookRecord).filter_by(
                artifact_id=artifact_id,
            ).all()
            assert len(outlooks) == 3
            assert {row.action for row in outlooks} == {"short"}
            assert {row.horizon: row.subjective_probability for row in outlooks} == {
                name: 0.66 if name == later_window else 0.6 for name in ("30m", "24h", "72h")
            }
            permission = session.get(ExecutionPermissionRecord, artifact_id)
            assert permission is not None and permission.allowed is False
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("later_window", ["24h", "72h"])
@pytest.mark.parametrize("action", ["neutral", "no_trade"])
def test_no_direction_still_obeys_the_existing_probability_ceiling(
    tmp_path: Path, later_window: Window, action: Action,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, _windows(
            result, later_window=later_window, probability=0.66, action=action, calibrated=False,
        ))
        service = CommitDecisionService(database, clock=lambda: NOW)
        artifact_id, gate = service.commit_research_result(
            run_id, event_id, result,
        )
        assert gate.status == GateStatus.research_only
        with database.session() as session:
            cap = session.query(GateDecisionRecord).filter_by(
                artifact_id=artifact_id, rule_id="probability_cap",
            ).one()
            assert cap.status == "fail"
            outlook = session.query(AnalyticalOutlookRecord).filter_by(
                artifact_id=artifact_id, horizon=later_window,
            ).one()
            assert outlook.action == action and outlook.subjective_probability == 0.66
            permission = session.get(ExecutionPermissionRecord, artifact_id)
            assert permission is not None and permission.allowed is False
    finally:
        database.engine.dispose()


@pytest.mark.parametrize(
    ("action", "provenance", "expected_status"),
    [
        ("short", "kernel", GateStatus.publish),
        ("short", "model_claim", GateStatus.research_only),
        ("short", "uncalibrated", GateStatus.research_only),
        ("neutral", "uncalibrated", GateStatus.publish),
        ("no_trade", "uncalibrated", GateStatus.publish),
    ],
)
def test_probability_at_cap_does_not_replace_calibration_or_execution_permission(
    tmp_path: Path, action: Action, provenance: str, expected_status: GateStatus,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, _windows(
            result, later_window="72h", probability=0.65, action=action,
            calibrated=provenance != "uncalibrated",
        ))
        receipts = {
            name: StrategyCalibrationService(database).persist(_passed_report(name))
            for name in ("30m", "24h", "72h")
        } if provenance == "kernel" else None
        service = CommitDecisionService(database, clock=lambda: NOW)
        artifact_id, gate = service.commit_research_result(
            run_id, event_id, result, forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        assert gate.status == expected_status
        with database.session() as session:
            cap = session.query(GateDecisionRecord).filter_by(
                artifact_id=artifact_id, rule_id="probability_cap",
            ).one()
            assert cap.status == "pass"
            forecasts = session.query(ForecastRecord).filter_by(artifact_id=artifact_id).all()
            assert len(forecasts) == 3
            if provenance == "kernel":
                assert {row.direction for row in forecasts} == {"short"}
                assert receipts is not None
                assert {row.calibration_receipt_id for row in forecasts} == set(receipts.values())
                assert {row.horizon: row.probability for row in forecasts} == {
                    "30m": 0.6, "24h": 0.6, "72h": 0.65,
                }
            elif action == "short":
                assert {row.direction for row in forecasts} == {"no_trade"}
                assert {row.probability for row in forecasts} == {0.5}
            permission = session.get(ExecutionPermissionRecord, artifact_id)
            assert permission is not None and permission.allowed is False
    finally:
        database.engine.dispose()


def test_safe_caller_projection_cannot_hide_later_window_or_replace_original_receipts(
    tmp_path: Path,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, _windows(
            result, later_window="24h", probability=0.9, action="short", calibrated=True,
        ))
        original = result.model_dump_json()
        with database.session() as session:
            original_rows = {
                row.output_id: (row.payload_json, row.payload_hash, row.created_at)
                for row in session.query(RunRoleOutputRecord).filter_by(run_id=run_id).all()
            }
        receipts = {
            name: StrategyCalibrationService(database).persist(_passed_report(name))
            for name in ("30m", "24h", "72h")
        }
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id, event_id,
            {
                "direction": "no_trade", "probability": 0.5, "facts": ["fact"],
                "citations": ["ref"], "counter_thesis": "counter", "trigger": "trigger",
                "invalidation": "invalidation", "research_complete": True,
            },
            GateResult(status=GateStatus.publish, decisions=[{
                "rule_id": "probability_cap", "status": "pass", "reason_code": "ok",
                "input_hash": "f" * 64,
            }]),
            research_result=result, forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        assert result.model_dump_json() == original
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact is not None and artifact.gate_status == "research_only"
            cap = session.query(GateDecisionRecord).filter_by(
                artifact_id=artifact_id, rule_id="probability_cap",
            ).one()
            assert cap.status == "fail" and cap.input_hash != "f" * 64
            for output_id, frozen in original_rows.items():
                row = session.get(RunRoleOutputRecord, output_id)
                assert row is not None
                assert (row.payload_json, row.payload_hash, row.created_at) == frozen
    finally:
        database.engine.dispose()
