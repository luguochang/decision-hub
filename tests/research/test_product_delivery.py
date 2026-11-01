from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.hub_api.delivery_routes import delivery_router
from packages.contracts_py.decision_hub_contracts import FeedbackCreate, ObservationCreate
from packages.contracts_py.decision_hub_contracts.generated.product_delivery import (
    ReadReceiptCommand,
    StudyTrackingCommand,
)
from packages.contracts_py.decision_hub_contracts.models import GateStatus
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.commit import CommitDecisionService
from packages.kernel.decision_hub_kernel.application.product_delivery import ProductDeliveryService
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.decision.gate import GateResult
from packages.kernel.decision_hub_kernel.persistence.db import (
    FeedbackRecord,
    OutboxRecord,
    ProductCommandRecord,
    ProductReadReceiptRecord,
    ReportRevisionRecord,
    ReviewScheduleRecord,
    RunRecord,
    StudyRecord,
)
from tests.research.test_study_identity import NOW, setup


def published(tmp_path):
    db = setup(tmp_path)
    event, _, _ = AdmissionService(db, clock=lambda: NOW).admit(
        ObservationCreate(text="FOMC report delivery audit", source_id="manual")
    )
    runs = RunService(db, clock=lambda: NOW)
    for n in range(2):
        run, _ = runs.create(event, f"delivery-{n}", strategy_version="research.v1")
        CommitDecisionService(db, clock=lambda n=n: NOW + timedelta(minutes=n)).commit(
            run, event, {"summary": f"version {n}"}, GateResult(GateStatus.research_only, [])
        )
    with db.session() as session:
        revisions = (
            session.query(ReportRevisionRecord).order_by(ReportRevisionRecord.created_at).all()
        )
    return db, event, revisions


def client_for(db):
    def owner(value):
        if not value:
            raise HTTPException(403, "owner_identity_required")
        return value

    app = FastAPI()
    app.include_router(delivery_router(db, owner))
    return TestClient(app)


def test_receipts_are_explicit_owner_and_revision_scoped_and_idempotent(tmp_path):
    db, _, revisions = published(tmp_path)
    service = ProductDeliveryService(db, clock=lambda: NOW + timedelta(hours=1))
    revision = revisions[-1]
    with db.session() as session:
        session.add(
            OutboxRecord(
                artifact_id=revision.artifact_id,
                channel="local",
                dedupe_key="local-receipt",
                sent_at=NOW,
            )
        )
    page = service.list_reports("owner")
    assert page.total == 2 and page.items[0].local_written_at == NOW
    assert all(item.read_at is None and item.external_dispatch_at is None for item in page.items)
    command = ReadReceiptCommand(request_id="read-1", read=True)
    first = service.mark_read(revision.revision_id, "owner", command)
    assert service.mark_read(revision.revision_id, "owner", command) == first
    assert service.list_reports("owner", unread_only=True).total == 1
    assert service.list_reports("another", unread_only=True).total == 2
    assert service.list_reports("owner", run_id=revisions[0].source_run_id).items[0].read_at is None
    for target, owner, changed in [
        (revisions[0].revision_id, "owner", command),
        (revision.revision_id, "another", command),
        (revision.revision_id, "owner", command.model_copy(update={"read": False})),
    ]:
        with pytest.raises(ValueError, match="product_request_reused"):
            service.mark_read(target, owner, changed)
    restored = service.mark_read(
        revision.revision_id, "owner", ReadReceiptCommand(request_id="undo", read=False)
    )
    assert restored.read_at is None
    with db.session() as session:
        assert session.query(ProductCommandRecord).count() == 2
        assert session.query(ProductReadReceiptRecord).count() == 1
        assert session.query(ReportRevisionRecord).count() == 2


def test_api_authorization_conflicts_and_pagination(tmp_path):
    db, _, revisions = published(tmp_path)
    client = client_for(db)
    path = f"/v1/product/reports/{revisions[0].revision_id}/read"
    payload = {"request_id": "request", "read": True}
    assert client.get("/v1/product/reports").status_code == 403
    assert client.post(path, json=payload).status_code == 403
    headers = {"X-Owner-Id": "owner", "Idempotency-Key": "request"}
    assert client.post(path, json=payload, headers={"X-Owner-Id": "owner"}).status_code == 400
    assert (
        client.post("/v1/product/reports/missing/read", json=payload, headers=headers).status_code
        == 404
    )
    assert client.post(path, json=payload, headers=headers).status_code == 200
    assert client.post(path, json={**payload, "read": False}, headers=headers).status_code == 409
    result = client.get("/v1/product/reports?limit=1&offset=1", headers=headers).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    assert result["items"][0]["revision_id"] == revisions[0].revision_id
    assert client.get("/v1/product/reports?limit=101", headers=headers).status_code == 422
    assert client.get("/v1/product/reports?offset=-1", headers=headers).status_code == 422
    assert client.get("/v1/product/reports", headers={"X-Owner-Id": "x" * 129}).status_code == 422


def test_concurrent_same_read_command_has_one_audit_receipt(tmp_path):
    db, _, revisions = published(tmp_path)
    barrier = Barrier(4)
    service = ProductDeliveryService(db, clock=lambda: NOW)

    def mark(_):
        barrier.wait()
        return service.mark_read(
            revisions[0].revision_id, "owner", ReadReceiptCommand(request_id="same", read=True)
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(mark, range(4)))
    assert all(item == results[0] for item in results)
    with db.session() as session:
        assert session.query(ProductCommandRecord).count() == 1


def test_pause_cancels_queued_automatic_preserves_manual_running_and_audit(tmp_path):
    db, event, revisions = published(tmp_path)
    runs = RunService(db, clock=lambda: NOW)
    automatic, _ = runs.create(
        event, "automatic", strategy_version="research.v1", admission_origin="automatic"
    )
    executing, _ = runs.create(
        event, "running", strategy_version="research.v1", admission_origin="scheduled_recheck"
    )
    manual, _ = runs.create(
        event, "manual", strategy_version="research.v1", admission_origin="manual"
    )
    assert runs.claim_for_execution(executing)
    scheduler = ReviewScheduleService(db, clock=lambda: NOW)
    scheduler._ensure_plan(
        parent_run_id=revisions[-1].source_run_id,
        horizon="24h",
        trigger_type="next_review",
        trigger_ref="pending",
        trigger_at=NOW,
        now=NOW,
    )
    service = ProductDeliveryService(db, clock=lambda: NOW)
    command = StudyTrackingCommand(request_id="pause", status="paused", expected_status="active")
    receipt = service.track(revisions[0].study_id, "owner", command)
    assert receipt.cancelled_runs == 1 and receipt.retired_plans == 1
    assert service.track(revisions[0].study_id, "owner", command) == receipt
    with db.session() as session:
        assert session.get(RunRecord, automatic).error_code == "study_tracking_paused"
        assert session.get(RunRecord, manual).status == "admitted"
        assert session.get(RunRecord, executing).status == "running"
        assert session.query(ReviewScheduleRecord).one().failure_code == "study_tracking_paused"
    assert scheduler._admit_due(NOW) == ()
    with pytest.raises(ValueError, match="study_not_active"):
        runs.create(event, "new-auto", strategy_version="research.v1", admission_origin="automatic")
    with pytest.raises(ValueError, match="study_status_changed"):
        service.track(
            revisions[0].study_id, "owner", command.model_copy(update={"request_id": "stale"})
        )
    service.track(
        revisions[0].study_id,
        "owner",
        StudyTrackingCommand(request_id="resume", status="active", expected_status="paused"),
    )
    assert scheduler._admit_due(NOW) == ()  # old skipped plans never replay


@pytest.mark.parametrize("scope", ["legacy_unknown", "gold"])
def test_unqualified_scope_cannot_be_activated(tmp_path, scope):
    db, _, revisions = published(tmp_path)
    with db.session() as session:
        study = session.get(StudyRecord, revisions[0].study_id)
        study.status = "paused"
        if scope == "gold":
            study.market, study.instrument = "metal", "XAU-USD"
        else:
            study.identity_basis = scope
    with pytest.raises(ValueError, match="study_scope_not_enabled"):
        ProductDeliveryService(db).track(
            revisions[0].study_id,
            "owner",
            StudyTrackingCommand(request_id="activate", status="active", expected_status="paused"),
        )


def test_pause_and_scheduler_race_leaves_no_admitted_auto_run(tmp_path):
    db, _, revisions = published(tmp_path)
    scheduler = ReviewScheduleService(db, clock=lambda: NOW)
    scheduler._ensure_plan(
        parent_run_id=revisions[-1].source_run_id,
        horizon="24h",
        trigger_type="next_review",
        trigger_ref="race",
        trigger_at=NOW,
        now=NOW,
    )
    barrier = Barrier(2)

    def pause():
        barrier.wait()
        return ProductDeliveryService(db, clock=lambda: NOW).track(
            revisions[0].study_id,
            "owner",
            StudyTrackingCommand(request_id="pause", status="paused", expected_status="active"),
        )

    def schedule():
        barrier.wait()
        return scheduler._admit_due(NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pause), pool.submit(schedule)]
        for future in futures:
            future.result()
    with db.session() as session:
        assert (
            session.query(RunRecord)
            .filter_by(admission_origin="scheduled_recheck", status="admitted")
            .count()
            == 0
        )
        assert session.get(StudyRecord, revisions[0].study_id).status == "paused"
        assert session.query(ReviewScheduleRecord).one().status == "skipped"


def test_revision_feedback_changes_only_its_run_value_and_is_idempotent(tmp_path):
    db, _, revisions = published(tmp_path)
    values = ResearchValueEvaluationService(
        db, pack_root=Path("packs/crypto_macro"), clock=lambda: NOW
    )
    before = values.evaluate_current(revisions[0].source_run_id)
    command = FeedbackCreate(
        request_id="feedback",
        target_type="report_revision",
        target_id=revisions[0].revision_id,
        created_by="owner",
        verdict="not_useful",
        notes="测试：缺少事件背景",
    )
    workbench = WorkbenchAssetService(db)
    feedback = workbench.create_feedback(command)
    assert workbench.create_feedback(command) == feedback
    after = values.evaluate_current(revisions[0].source_run_id)
    assert after.evaluation_version != before.evaluation_version
    assert after.usefulness == "not_useful"
    assert values.evaluate_current(revisions[1].source_run_id).usefulness == "unlabeled"
    delivery = ProductDeliveryService(db)
    restored = delivery.list_reports("owner", run_id=revisions[0].source_run_id).items[0]
    assert restored.latest_feedback.feedback_id == feedback.feedback_id
    assert restored.latest_feedback.notes == command.notes
    assert (
        delivery.list_reports("another", run_id=revisions[0].source_run_id).items[0].latest_feedback
        is None
    )
    with pytest.raises(ValueError, match="workbench_request_reused"):
        workbench.create_feedback(
            command.model_copy(update={"target_id": revisions[1].revision_id})
        )
    with pytest.raises(ValueError, match="workbench_target_not_found"):
        workbench.create_feedback(
            command.model_copy(update={"request_id": "missing", "target_id": "missing"})
        )
    with db.session() as session:
        assert session.query(FeedbackRecord).count() == 1
        assert (
            session.get(ReportRevisionRecord, revisions[0].revision_id).artifact_id
            == revisions[0].artifact_id
        )
