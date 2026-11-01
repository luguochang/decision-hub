"""Actual publication gates with frozen tasks and decision snapshots, without a provider."""
# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchReview, ResearchSessionResult
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application import commit as commit_module
from packages.kernel.decision_hub_kernel.application.calibration import StrategyCalibrationService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_method import canonical_recipe
from packages.kernel.decision_hub_kernel.application.research_review import (
    independent_review_receipt_hash,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.application.research_task import ResearchTaskService
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from packages.kernel.decision_hub_kernel.decision.horizon_contract import HORIZON_CONTRACT_VERSION
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalOutlookRecord,
    ArtifactRecord,
    Database,
    ForecastRecord,
    GateDecisionRecord,
    PublicationDecisionRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StrategyCalibrationRecord,
)
from tests.evolution.test_research_worker import (
    NOW,
    PACK_ROOT,
    ResearchRequestFactory,
    _admitted_run,
    _result,
)
from tests.research.test_calibration_receipts import _passed_report


def _attest(
    database: Database, run_id: str, result: ResearchSessionResult
) -> ResearchSessionResult:
    assert result.causal_case
    refs = [result.evidence_candidates[0].evidence_id]
    challenge = "The event may be insufficient for an executable directional conclusion."
    digest = independent_review_receipt_hash(
        role="independent_reviewer",
        target_claim=result.causal_case.thesis,
        evidence_refs=refs,
        challenge=challenge,
        resolution="accepted",
        limitations=[],
    )
    review = ResearchReview(
        review_id=f"review_{digest[:32]}",
        role="independent_reviewer",
        target_claim=result.causal_case.thesis,
        evidence_refs=refs,
        result_hash=research_result_content_hash(result),
        challenge=challenge,
        resolution="accepted",
        limitations=[],
        created_at=NOW,
    )
    database.save_role_output(
        run_id,
        "independent_reviewer",
        review.model_dump(mode="json"),
        "research-review.native-attested.v1",
    )
    return result.model_copy(update={"reviews": [review]})


def _seed(
    tmp_path: Path,
    *,
    freeze: bool = True,
    mode: Literal["live", "replay"] = "live",
    cutoff: datetime = NOW,
):
    database, event_id, run_id = _admitted_run(tmp_path)
    request = ResearchRequestFactory(
        database,
        pack_root=PACK_ROOT,
        clock=lambda: NOW,
        execution_mode=mode,
    ).build(event_id, run_id)
    result = _result(request)
    result = result.model_copy(
        update={
            "evidence_candidates": [
                item.model_copy(update={"quality": "accepted"})
                for item in result.evidence_candidates
            ],
            "final_coverage": result.final_coverage.model_copy(
                update={
                    "status": "sufficient",
                    "hard_coverage_ratio": 1.0,
                    "gaps": [],
                    "conflicts": [],
                }
            ),
            "horizons": [
                item.model_copy(update={"action": "no_trade"}) for item in result.horizons
            ],
            "reviews": [],
        }
    )
    if freeze:
        ResearchTaskService(database, clock=lambda: NOW).freeze(request)
        # The actual snapshot service binds the admitted trigger; this test
        # isolates publication requirements rather than making Provider calls.
        ResearchEvidenceService(database).freeze_decision_snapshot(
            run_id=run_id,
            evidence_refs=[],
            cutoff_at=cutoff,
        )
    return database, event_id, run_id, request, result


@pytest.mark.parametrize("invalid", ["reference_permutation", "expired"])
def test_actual_commit_rejects_invalid_window_contract_with_native_review(
    tmp_path: Path,
    invalid: str,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        if invalid == "reference_permutation":
            first = result.horizons[0]
            horizons = [
                first.model_copy(
                    update={
                        "horizon": name,
                        "evidence_refs": ["one", "two"] if index % 2 == 0 else ["two", "one"],
                    }
                )
                for index, name in enumerate(("30m", "24h", "72h"))
            ]
        else:
            horizons = [
                item.model_copy(
                    update={
                        "next_review_at": NOW - timedelta(seconds=1),
                        "expires_at": NOW + timedelta(minutes=20),
                    }
                )
                for item in result.horizons
            ]
        result = _attest(database, run_id, result.model_copy(update={"horizons": horizons}))
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == "research_only"
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
            run = session.get(RunRecord, run_id)
            assert run and run.status == "degraded"
    finally:
        database.engine.dispose()


def _contract_decision(database: Database, artifact_id: str):
    with database.session() as session:
        return (
            session.query(GateDecisionRecord)
            .filter_by(
                artifact_id=artifact_id,
                rule_id=HORIZON_CONTRACT_VERSION,
            )
            .one()
        )


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_business_cutoff_not_wall_clock_preserves_valid_no_trade_report(
    tmp_path: Path,
    mode: Literal["live", "replay"],
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path, mode=mode)
    try:
        result = _attest(database, run_id, result)
        artifact_id, gate = CommitDecisionService(
            database,
            clock=lambda: NOW + timedelta(days=10),
        ).commit_research_result(run_id, event_id, result)
        assert gate.status == GateStatus.publish
        decision = _contract_decision(database, artifact_id)
        assert decision.status == "pass" and decision.reason_code == "ok"
        assert len(decision.input_hash) == 64
        with database.session() as session:
            stored = session.get(PublicationDecisionRecord, artifact_id)
            assert stored and stored.status == "publish"
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_task", "research_task_request_unavailable"),
        ("task_json", "research_task_request_invalid"),
        ("task_hash", "research_task_request_invalid"),
        ("missing_snapshot", "research_decision_snapshot_unavailable"),
        ("snapshot_hash", "research_decision_snapshot_hash_mismatch"),
        ("snapshot_cutoff", "research_decision_snapshot_hash_mismatch"),
        ("snapshot_evidence", "research_decision_snapshot_hash_mismatch"),
        ("snapshot_json", "research_decision_snapshot_invalid"),
        ("snapshot_run", "research_decision_snapshot_identity_mismatch"),
        ("snapshot_event", "research_decision_snapshot_identity_mismatch"),
        ("snapshot_parent", "research_decision_snapshot_identity_mismatch"),
        ("snapshot_type", "research_decision_snapshot_identity_mismatch"),
        ("request_id", "research_publication_request_mismatch"),
    ],
)
def test_missing_or_corrupt_proof_keeps_material_with_explicit_failed_receipt(
    tmp_path: Path,
    change: str,
    reason: str,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run and run.decision_snapshot_id
            task = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=run_id,
                    role="research_task_request",
                )
                .one()
            )
            snapshot = session.get(SnapshotRecord, run.decision_snapshot_id)
            assert snapshot
            if change == "missing_task":
                session.delete(task)
            elif change == "task_json":
                task.payload_json = "{}"
            elif change == "task_hash":
                task.payload_hash = "d" * 64
            elif change == "missing_snapshot":
                session.delete(snapshot)
            elif change == "snapshot_hash":
                snapshot.snapshot_hash = "e" * 64
            elif change == "snapshot_cutoff":
                snapshot.cutoff_at += timedelta(seconds=1)
            elif change == "snapshot_evidence":
                body = json.loads(snapshot.evidence_json)
                body[0]["text"] = "Changed admitted trigger"
                snapshot.evidence_json = json.dumps(body)
            elif change == "snapshot_json":
                snapshot.evidence_json = "{}"
            elif change == "snapshot_run":
                snapshot.run_id = "other-run"
            elif change == "snapshot_event":
                snapshot.event_id = "other-event"
            elif change == "snapshot_parent":
                snapshot.parent_snapshot_id = "other-trigger"
            elif change == "snapshot_type":
                snapshot.snapshot_type = "trigger"
            else:
                result = result.model_copy(update={"request_id": "other-request"})
            before_task = task.payload_json if change != "missing_task" else None
        result = _attest(database, run_id, result)
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        decision = _contract_decision(database, artifact_id)
        assert decision.status == "fail" and decision.reason_code == reason
        with database.session() as session:
            task_after = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=run_id,
                    role="research_task_request",
                )
                .one_or_none()
            )
            assert (task_after.payload_json if task_after else None) == before_task
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


def test_live_uses_later_decision_snapshot_instead_of_original_trigger(tmp_path: Path) -> None:
    cutoff = NOW + timedelta(seconds=45)
    database, event_id, run_id, _request, result = _seed(tmp_path, cutoff=cutoff)
    try:
        result = result.model_copy(
            update={
                "horizons": [
                    item.model_copy(update={"next_review_at": NOW + timedelta(seconds=30)})
                    for item in result.horizons
                ]
            }
        )
        result = _attest(database, run_id, result)
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        assert (
            _contract_decision(database, artifact_id).reason_code
            == "horizon_review_not_after_reference"
        )
    finally:
        database.engine.dispose()


@pytest.mark.parametrize(
    "mode,offset", [("live", timedelta(days=1)), ("replay", timedelta(seconds=1))]
)
def test_real_frozen_snapshot_cannot_exceed_frozen_task_time_boundary(
    tmp_path: Path,
    mode: Literal["live", "replay"],
    offset: timedelta,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path, mode=mode, cutoff=NOW + offset)
    try:
        result = _attest(database, run_id, result)
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        assert (
            _contract_decision(database, artifact_id).reason_code
            == "research_decision_cutoff_outside_task"
        )
    finally:
        database.engine.dispose()


def test_changed_full_task_at_final_write_rolls_back_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, result)
        original = commit_module._forecast_audit_context

        def drift_task(passed_db: Database, source_run_id: str, forecasted_at: datetime):
            with database.session() as session:
                task = (
                    session.query(RunRoleOutputRecord)
                    .filter_by(
                        run_id=run_id,
                        role="research_task_request",
                    )
                    .one()
                )
                body = json.loads(task.payload_json)
                body["request"]["execution_budget"]["total_deadline_seconds"] += 1
                body["request_hash"] = hashlib.sha256(
                    canonical_recipe(body["request"]).encode(),
                ).hexdigest()
                task.payload_json = canonical_recipe(body)
                task.payload_hash = hashlib.sha256(task.payload_json.encode()).hexdigest()
            return original(passed_db, source_run_id, forecasted_at)

        monkeypatch.setattr(commit_module, "_forecast_audit_context", drift_task)
        with pytest.raises(PermissionError, match="research_horizon_binding_changed"):
            CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
                run_id,
                event_id,
                result,
            )
        with database.session() as session:
            assert session.query(ArtifactRecord).count() == 0
            assert session.query(ForecastRecord).count() == 0
            assert session.query(PublicationDecisionRecord).count() == 0
            run = session.get(RunRecord, run_id)
            assert run and run.artifact_id is None
    finally:
        database.engine.dispose()


def test_existing_artifact_returns_original_gate_without_new_contract_backfill(
    tmp_path: Path,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, result)
        service = CommitDecisionService(database, clock=lambda: NOW)
        first_id, first_gate = service.commit_research_result(run_id, event_id, result)
        assert first_gate.status == GateStatus.publish
        with database.session() as session:
            # Simulate an old Artifact whose ledger predates this checker.
            session.query(GateDecisionRecord).filter_by(
                artifact_id=first_id,
                rule_id=HORIZON_CONTRACT_VERSION,
            ).delete()
            task = (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=run_id,
                    role="research_task_request",
                )
                .one()
            )
            session.delete(task)
            before = session.get(ArtifactRecord, first_id)
            assert before
            fingerprint = (before.gate_status, before.payload_json, before.created_at)
        second_id, second_gate = service.commit_research_result(run_id, event_id, result)
        assert second_id == first_id and second_gate.status == GateStatus.publish
        assert HORIZON_CONTRACT_VERSION not in {item["rule_id"] for item in second_gate.decisions}
        with database.session() as session:
            after = session.get(ArtifactRecord, first_id)
            assert (
                after and (after.gate_status, after.payload_json, after.created_at) == fingerprint
            )
            assert session.query(ArtifactRecord).count() == 1
            assert session.query(ForecastRecord).count() == 3
            assert (
                session.query(RunRoleOutputRecord)
                .filter_by(
                    run_id=run_id,
                    role="research_task_request",
                )
                .count()
                == 0
            )
    finally:
        database.engine.dispose()


def test_direct_publish_gate_cannot_replace_missing_frozen_publication_proof(
    tmp_path: Path,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path, freeze=False)
    try:
        result = _attest(database, run_id, result)
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {"direction": "long", "probability": 0.6, "facts": ["fact"], "citations": ["ref"]},
            GateResult(status=GateStatus.publish, decisions=[]),
            horizons=list(result.horizons),
            research_result=result,
        )
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == "research_only"
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


def test_direct_gate_rechecks_kernel_probability_cap_and_replaces_supplied_pass(
    tmp_path: Path,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(update={"subjective_probability": 0.66})
                        for item in result.horizons
                    ]
                }
            ),
        )
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {
                "direction": "no_trade",
                "probability": 0.66,
                "facts": ["fact"],
                "citations": ["ref"],
                "counter_thesis": "counter",
                "trigger": "trigger",
                "invalidation": "invalidation",
                "research_complete": True,
            },
            GateResult(
                status=GateStatus.publish,
                decisions=[
                    {
                        "rule_id": "probability_cap",
                        "status": "pass",
                        "reason_code": "ok",
                        "input_hash": "f" * 64,
                    }
                ],
            ),
            research_result=result,
        )
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == "research_only"
            decision = (
                session.query(GateDecisionRecord)
                .filter_by(
                    artifact_id=artifact_id,
                    rule_id="probability_cap",
                )
                .one()
            )
            assert decision.status == "fail"
            assert decision.reason_code == "uncalibrated_probability_capped"
            assert decision.input_hash != "f" * 64
    finally:
        database.engine.dispose()


def test_alternate_projection_cannot_publish_unverified_horizons(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, result)
        other = [item.model_copy(update={"action": "long"}) for item in result.horizons]
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {"direction": "long", "probability": 0.6, "facts": ["fact"], "citations": ["ref"]},
            GateResult(status=GateStatus.publish, decisions=[]),
            horizons=other,
            research_result=result,
        )
        decision = _contract_decision(database, artifact_id)
        assert decision.reason_code == "research_horizon_projection_mismatch"
        with database.session() as session:
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


def test_replay_snapshot_must_equal_frozen_pit_even_if_before_deadline(tmp_path: Path) -> None:
    database, event_id, run_id, request, result = _seed(tmp_path, freeze=False, mode="replay")
    try:
        request = request.model_copy(update={"pit_cutoff_at": NOW + timedelta(seconds=30)})
        ResearchTaskService(database, clock=lambda: NOW).freeze(request)
        ResearchEvidenceService(database).freeze_decision_snapshot(
            run_id=run_id,
            evidence_refs=[],
            cutoff_at=NOW,
        )
        result = _attest(database, run_id, result)
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        assert (
            _contract_decision(database, artifact_id).reason_code
            == "research_replay_cutoff_mismatch"
        )
    finally:
        database.engine.dispose()


def test_real_time_distinction_and_review_equal_expiry_remain_publishable(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        first = result.horizons[0]
        horizons = [
            first.model_copy(
                update={
                    "horizon": name,
                    "trigger": "same trigger",
                    "invalidation": "same invalidation",
                    "expires_at": NOW + timedelta(minutes=index + 1),
                    "next_review_at": NOW + timedelta(minutes=index + 1),
                }
            )
            for index, name in enumerate(("30m", "24h", "72h"))
        ]
        result = _attest(database, run_id, result.model_copy(update={"horizons": horizons}))
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.publish
        assert _contract_decision(database, artifact_id).status == "pass"
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("change", ["deleted", "changed"])
def test_final_transaction_rejects_lost_native_review_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(database, run_id, result)
        original = commit_module._forecast_audit_context

        def drift_review(passed_db: Database, source_run_id: str, forecasted_at: datetime):
            with database.session() as session:
                review = (
                    session.query(RunRoleOutputRecord)
                    .filter_by(
                        run_id=run_id,
                        role="independent_reviewer",
                    )
                    .one()
                )
                if change == "deleted":
                    session.delete(review)
                else:
                    body = json.loads(review.payload_json)
                    body["resolution"] = "bounded"
                    review.payload_json = canonical_recipe(body)
                    review.payload_hash = hashlib.sha256(review.payload_json.encode()).hexdigest()
            return original(passed_db, source_run_id, forecasted_at)

        monkeypatch.setattr(commit_module, "_forecast_audit_context", drift_review)
        with pytest.raises(PermissionError, match="research_publication_review_binding_changed"):
            CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
                run_id,
                event_id,
                result,
            )
        with database.session() as session:
            assert session.query(ArtifactRecord).count() == 0
            assert session.query(ForecastRecord).count() == 0
            assert session.query(PublicationDecisionRecord).count() == 0
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("change", ["deleted", "failed"])
def test_final_transaction_rejects_lost_calibration_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(
                            update={"action": "short", "probability_status": "calibrated"}
                        )
                        for item in result.horizons
                    ]
                }
            ),
        )
        calibration = StrategyCalibrationService(database)
        receipts = {
            name: calibration.persist(_passed_report(name)) for name in ("30m", "24h", "72h")
        }
        original = commit_module._forecast_audit_context

        def drift_calibration(passed_db: Database, source_run_id: str, forecasted_at: datetime):
            with database.session() as session:
                receipt = session.get(StrategyCalibrationRecord, receipts["72h"])
                assert receipt
                if change == "deleted":
                    session.delete(receipt)
                else:
                    receipt.passed = False
            return original(passed_db, source_run_id, forecasted_at)

        monkeypatch.setattr(commit_module, "_forecast_audit_context", drift_calibration)
        with pytest.raises(
            PermissionError, match="research_publication_calibration_binding_changed"
        ):
            CommitDecisionService(database, clock=lambda: NOW).commit_research_result(
                run_id,
                event_id,
                result,
                forecast_strategy_version="btc-oos.v1",
                calibration_receipt_ids=receipts,
            )
        with database.session() as session:
            assert session.query(ArtifactRecord).count() == 0
            assert session.query(ForecastRecord).count() == 0
    finally:
        database.engine.dispose()


def test_caller_calibration_boolean_cannot_authorize_missing_receipts(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(
                            update={"action": "short", "probability_status": "calibrated"}
                        )
                        for item in result.horizons
                    ]
                }
            ),
        )
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {
                "direction": "short",
                "probability": 0.6,
                "facts": ["fact"],
                "citations": ["ref"],
                "counter_thesis": "counter",
                "trigger": "trigger",
                "invalidation": "invalidation",
            },
            GateResult(status=GateStatus.publish, decisions=[]),
            research_result=result,
            directional_calibration_attested=True,
        )
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == "research_only"
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


def test_trigger_cutoff_column_cannot_move_original_input_before_publication(
    tmp_path: Path,
) -> None:
    database, event_id, run_id, request, result = _seed(tmp_path, freeze=False)
    try:
        ResearchTaskService(database, clock=lambda: NOW).freeze(request)
        with database.session() as session:
            run = session.get(RunRecord, run_id)
            assert run and run.snapshot_id
            trigger = session.get(SnapshotRecord, run.snapshot_id)
            assert trigger
            # The original JSON/hash/Task retain the actual received_at=NOW.
            # Only the column changes before the real decision writer runs.
            trigger.cutoff_at = NOW - timedelta(minutes=1)
        ResearchEvidenceService(database).freeze_decision_snapshot(
            run_id=run_id,
            evidence_refs=[],
            cutoff_at=NOW - timedelta(seconds=30),
        )
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(update={"next_review_at": NOW - timedelta(seconds=10)})
                        for item in result.horizons
                    ]
                }
            ),
        )
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
        )
        assert gate.status == GateStatus.research_only
        assert (
            _contract_decision(database, artifact_id).reason_code
            == "research_trigger_snapshot_invalid"
        )
    finally:
        database.engine.dispose()


def test_final_kernel_gate_suppresses_directional_forecasts_above_cap(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(
                            update={
                                "action": "short",
                                "probability_status": "calibrated",
                                "subjective_probability": 0.66,
                            }
                        )
                        for item in result.horizons
                    ]
                }
            ),
        )
        calibration = StrategyCalibrationService(database)
        receipts = {
            name: calibration.persist(_passed_report(name)) for name in ("30m", "24h", "72h")
        }
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
            forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        assert gate.status == GateStatus.research_only
        with database.session() as session:
            cap = (
                session.query(GateDecisionRecord)
                .filter_by(
                    artifact_id=artifact_id,
                    rule_id="probability_cap",
                )
                .one()
            )
            assert cap.status == "fail" and cap.reason_code == "uncalibrated_probability_capped"
            forecasts = session.query(ForecastRecord).filter_by(artifact_id=artifact_id).all()
            assert len(forecasts) == 3
            assert {row.direction for row in forecasts} == {"no_trade"}
            assert {row.probability for row in forecasts} == {0.5}
            outlooks = (
                session.query(AnalyticalOutlookRecord).filter_by(artifact_id=artifact_id).all()
            )
            assert {row.action for row in outlooks} == {"short"}
            assert {row.subjective_probability for row in outlooks} == {0.66}
    finally:
        database.engine.dispose()


def test_valid_calibrated_direction_at_kernel_cap_remains_published(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(
                            update={
                                "action": "short",
                                "probability_status": "calibrated",
                                "subjective_probability": 0.65,
                            }
                        )
                        for item in result.horizons
                    ]
                }
            ),
        )
        calibration = StrategyCalibrationService(database)
        receipts = {
            name: calibration.persist(_passed_report(name)) for name in ("30m", "24h", "72h")
        }
        artifact_id, gate = CommitDecisionService(
            database, clock=lambda: NOW
        ).commit_research_result(
            run_id,
            event_id,
            result,
            forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        assert gate.status == GateStatus.publish
        with database.session() as session:
            forecasts = session.query(ForecastRecord).filter_by(artifact_id=artifact_id).all()
            assert len(forecasts) == 3
            assert {row.direction for row in forecasts} == {"short"}
            assert {row.probability for row in forecasts} == {0.65}
            assert {row.calibration_receipt_id for row in forecasts} == set(receipts.values())
            outlooks = (
                session.query(AnalyticalOutlookRecord).filter_by(artifact_id=artifact_id).all()
            )
            assert {row.action for row in outlooks} == {"short"}
            assert {row.subjective_probability for row in outlooks} == {0.65}
    finally:
        database.engine.dispose()


def test_direct_candidate_cannot_hide_typed_probability_or_direction(tmp_path: Path) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        result = _attest(
            database,
            run_id,
            result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(
                            update={
                                "action": "short",
                                "probability_status": "calibrated",
                                "subjective_probability": 0.66,
                            }
                        )
                        for item in result.horizons
                    ]
                }
            ),
        )
        calibration = StrategyCalibrationService(database)
        receipts = {
            name: calibration.persist(_passed_report(name)) for name in ("30m", "24h", "72h")
        }
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {
                "direction": "no_trade",
                "probability": 0.5,
                "facts": ["fact"],
                "citations": ["ref"],
                "counter_thesis": "counter",
                "trigger": "trigger",
                "invalidation": "invalidation",
            },
            GateResult(status=GateStatus.publish, decisions=[]),
            research_result=result,
            forecast_strategy_version="btc-oos.v1",
            calibration_receipt_ids=receipts,
        )
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == "research_only"
            cap = (
                session.query(GateDecisionRecord)
                .filter_by(
                    artifact_id=artifact_id,
                    rule_id="probability_cap",
                )
                .one()
            )
            assert cap.status == "fail"
            assert {row.direction for row in session.query(ForecastRecord).all()} == {"no_trade"}
    finally:
        database.engine.dispose()


@pytest.mark.parametrize("valid", [False, True])
def test_supplied_horizon_rule_is_replaced_by_one_actual_audit_decision(
    tmp_path: Path, valid: bool
) -> None:
    database, event_id, run_id, _request, result = _seed(tmp_path)
    try:
        if not valid:
            result = result.model_copy(
                update={
                    "horizons": [
                        item.model_copy(update={"next_review_at": NOW - timedelta(seconds=1)})
                        for item in result.horizons
                    ]
                }
            )
        result = _attest(database, run_id, result)
        supplied_status = "fail" if valid else "pass"
        original_other = {
            "rule_id": "caller_other_audit",
            "status": "pass",
            "reason_code": "original_other_reason",
            "input_hash": "d" * 64,
        }
        artifact_id = CommitDecisionService(database, clock=lambda: NOW).commit(
            run_id,
            event_id,
            {
                "direction": "no_trade",
                "probability": 0.5,
                "facts": ["fact"],
                "citations": ["ref"],
                "counter_thesis": "counter",
                "trigger": "trigger",
                "invalidation": "invalidation",
            },
            GateResult(
                status=GateStatus.publish,
                decisions=[
                    {
                        "rule_id": HORIZON_CONTRACT_VERSION,
                        "status": supplied_status,
                        "reason_code": "caller_original_reason",
                        "input_hash": "c" * 64,
                    },
                    original_other,
                ],
            ),
            research_result=result,
        )
        with database.session() as session:
            decisions = (
                session.query(GateDecisionRecord)
                .filter_by(artifact_id=artifact_id, rule_id=HORIZON_CONTRACT_VERSION)
                .all()
            )
            assert len(decisions) == 1
            actual = decisions[0]
            assert actual.status == ("pass" if valid else "fail")
            assert actual.reason_code != "caller_original_reason"
            assert actual.input_hash != "c" * 64 and len(actual.input_hash) == 64
            other = (
                session.query(GateDecisionRecord)
                .filter_by(artifact_id=artifact_id, rule_id="caller_other_audit")
                .one()
            )
            assert other.status == original_other["status"]
            assert other.reason_code == original_other["reason_code"]
            assert other.input_hash == original_other["input_hash"]
            artifact = session.get(ArtifactRecord, artifact_id)
            assert artifact and artifact.gate_status == ("publish" if valid else "research_only")
    finally:
        database.engine.dispose()
