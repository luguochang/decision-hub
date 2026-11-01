"""Physical SQLite controls for tracking; no Provider or HTTP is invoked."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts import ResearchRunCommand, ReviewScheduleView

# Fixtures use the exact publication projection rather than a second implementation.
from packages.kernel.decision_hub_kernel.application.commit import (
    _project_research_candidate,  # pyright: ignore[reportPrivateUsage]
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchCommandService,
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_tracking import (
    research_admission_guard_in_session,
    tracking_basis_in_session,
)
from packages.kernel.decision_hub_kernel.application.review_schedule import ReviewScheduleService
from packages.kernel.decision_hub_kernel.application.study import ensure_study, record_revision
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    DshSessionLinkRecord,
    EventRecord,
    ProductCommandRecord,
    ReportRevisionRecord,
    ResearchResultRecord,
    ReviewScheduleRecord,
    RunEventRecord,
    RunRecord,
    as_utc,
)

# Reuse the unchanged historical report fixture for compatibility controls.
from tests.research.test_review_schedule import (
    _result as original_result,  # pyright: ignore[reportPrivateUsage]
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    db = Database(f"sqlite+pysqlite:///{tmp_path / 'tracking.sqlite3'}")
    db.create_all()
    try:
        yield db
    finally:
        db.engine.dispose()


def _publish(
    db: Database,
    run_id: str,
    finished_at: datetime,
    *,
    all_next_minutes: int | None = None,
) -> None:
    template = original_result()
    windows = {"30m": timedelta(minutes=30), "24h": timedelta(hours=24), "72h": timedelta(hours=72)}
    next_times = {
        "30m": timedelta(minutes=10),
        "24h": timedelta(hours=3),
        "72h": timedelta(hours=15),
    }
    result = template.model_copy(
        update={
            "request_id": f"request:{run_id}",
            "research_session_id": f"session:{run_id}",
            "started_at": finished_at - timedelta(minutes=2),
            "finished_at": finished_at,
            "horizons": [
                item.model_copy(
                    update={
                        "expires_at": finished_at + windows[item.horizon],
                        "next_review_at": finished_at
                        + (
                            timedelta(minutes=all_next_minutes)
                            if all_next_minutes is not None
                            else next_times[item.horizon]
                        ),
                    }
                )
                for item in template.horizons
            ],
        }
    )
    with db.session() as session:
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.status, run.finished_at, run.updated_at = "completed", finished_at, finished_at
        run.lease_owner, run.lease_expires_at = None, None
        projected = _project_research_candidate(result, False)
        artifact = ArtifactRecord(
            artifact_id=f"artifact:{run_id}",
            run_id=run_id,
            event_id=run.event_id,
            gate_status="research_only",
            headline=str(projected["headline"]),
            summary=str(projected["summary"]),
            payload_json=json.dumps(
                {
                    field: projected[field]
                    for field in (
                        "facts",
                        "inferences",
                        "counter_thesis",
                        "uncertainty",
                        "transmission_chain",
                        "citations",
                    )
                }
            ),
            created_at=finished_at,
        )
        session.add(artifact)
        run.artifact_id = artifact.artifact_id
        ResearchObservabilityService(db, clock=lambda: finished_at).save_result_in_session(
            session, run_id, result
        )
        record_revision(session, run, artifact, input_fingerprint="f" * 64, now=finished_at)


def _root(db: Database, *, finished_at: datetime = NOW - timedelta(hours=1)) -> tuple[str, str]:
    with db.session() as session:
        study_id = ensure_study(
            session,
            question_key="btc-tracking",
            title="BTC 持续研究",
            market="crypto",
            instrument="BTC-USDT-SWAP",
            now=finished_at - timedelta(minutes=3),
        )
        session.add(
            EventRecord(
                event_id="event:tracking",
                event_type="crypto_market_snapshot",
                occurred_at=finished_at - timedelta(minutes=3),
                received_at=finished_at - timedelta(minutes=3),
            )
        )
        session.add(
            RunRecord(
                run_id="run:root",
                event_id="event:tracking",
                study_id=study_id,
                status="completed",
                strategy_version="research.v1",
                runtime_version="controlled",
                admission_origin="manual",
                priority=100,
                created_at=finished_at - timedelta(minutes=3),
                updated_at=finished_at,
            )
        )
    _publish(db, "run:root", finished_at)
    return "run:root", study_id


def _update(
    db: Database,
    source: str,
    request_id: str,
    *,
    accepted_at: datetime,
    finished_at: datetime | None,
    all_next_minutes: int | None = None,
) -> str:
    result = ResearchCommandService(db, clock=lambda: accepted_at).apply(
        source,
        ResearchRunCommand(
            schema_version="research-run-command.v1",
            request_id=request_id,
            command="recheck",
            reason="更新公开证据",
        ),
        owner_id="owner:tracking",
    )
    assert result.status == "accepted" and result.target_run_id is not None
    if finished_at is not None:
        _publish(db, result.target_run_id, finished_at, all_next_minutes=all_next_minutes)
    return result.target_run_id


def _report_bytes(db: Database, run_id: str) -> str:
    with db.session() as session:
        records = [session.get(ResearchResultRecord, run_id)]
        records += session.query(ArtifactRecord).filter_by(run_id=run_id).all()
        records += session.query(ReportRevisionRecord).filter_by(source_run_id=run_id).all()
        values = [
            {column.name: str(getattr(row, column.name)) for column in row.__table__.columns}
            for row in records
            if row is not None
        ]
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def test_manual_update_transfers_future_plans_once_without_changing_original_report(
    database: Database,
) -> None:
    root, study_id = _root(database)
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Discover separately so the test can inspect the original plans before admission.
    service._discover_plans(  # pyright: ignore[reportPrivateUsage]
        NOW - timedelta(minutes=59)
    )
    before = _report_bytes(database, root)
    child = _update(
        database,
        root,
        "update:one",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
    )
    assert service.plan_due() == ()
    assert service.plan_due() == ()
    with database.session() as session:
        basis = tracking_basis_in_session(session, study_id=study_id, now=NOW)
        assert basis is not None and basis.run.run_id == child
        old = session.query(ReviewScheduleRecord).filter_by(parent_run_id=root).all()
        new = session.query(ReviewScheduleRecord).filter_by(parent_run_id=child).all()
        assert len(new) == 3
        for horizon in ("24h", "72h"):
            original = next(item for item in old if item.horizon == horizon)
            successor = next(item for item in new if item.horizon == horizon)
            assert original.status == "skipped" and successor.status == "planned"
            assert as_utc(successor.trigger_at) == as_utc(original.trigger_at)
        assert (
            session.query(RunEventRecord)
            .filter_by(run_id=child, event_type="research.review_basis.activated")
            .count()
            == 3
        )
    associations = service.list_for_run(root)
    assert all(
        item.replacement_run_id == child and item.replacement_review_plan_id
        for item in associations
        if item.horizon in {"24h", "72h"}
    )
    assert _report_bytes(database, root) == before


def test_late_suggestion_keeps_active_24h_72h_future_slots_and_never_replays_expired_30m(
    database: Database,
) -> None:
    root, _study_id = _root(database, finished_at=NOW - timedelta(hours=2))
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Freeze the original future slots before the later accepted update.
    service._discover_plans(  # pyright: ignore[reportPrivateUsage]
        NOW - timedelta(minutes=119)
    )
    child = _update(
        database,
        root,
        "update:late",
        accepted_at=NOW - timedelta(minutes=45),
        finished_at=NOW - timedelta(minutes=40),
        all_next_minutes=10,
    )
    assert service.plan_due() == ()
    with database.session() as session:
        plans = session.query(ReviewScheduleRecord).filter_by(parent_run_id=child).all()
        assert {item.horizon for item in plans} == {"24h", "72h"}
        assert all(item.status == "planned" and as_utc(item.trigger_at) > NOW for item in plans)
        old = session.query(ReviewScheduleRecord).filter_by(parent_run_id=root).all()
        for plan in plans:
            assert plan.trigger_at == next(
                item.trigger_at for item in old if item.horizon == plan.horizon
            )
        events = (
            session.query(RunEventRecord)
            .filter_by(run_id=child, event_type="research.review_basis.activated")
            .all()
        )
        assert all(json.loads(item.payload_json)["late_activation"] is True for item in events)


def test_due_admission_is_atomic_single_active_and_auto_child_cannot_extend_authority(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, study_id = _root(database)
    child = _update(
        database,
        root,
        "update:due",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
        all_next_minutes=1,
    )
    before = _report_bytes(database, root)
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Split phases solely to inject a failure within the actual admission writer.
    service._discover_plans(NOW)  # pyright: ignore[reportPrivateUsage]
    with database.session() as session:
        session.query(ReviewScheduleRecord).filter_by(parent_run_id=child).update(
            {"trigger_at": NOW}
        )
    original = service._record_parent_event  # pyright: ignore[reportPrivateUsage]

    def fault(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected-before-audit")

    monkeypatch.setattr(service, "_record_parent_event", fault)
    with pytest.raises(RuntimeError, match="injected-before-audit"):
        service._admit_due(NOW)  # pyright: ignore[reportPrivateUsage]
    with database.session() as session:
        assert session.query(RunRecord).filter_by(admission_origin="scheduled_recheck").count() == 0
        assert (
            session.query(ReviewScheduleRecord)
            .filter_by(parent_run_id=child, status="planned")
            .count()
            == 3
        )
    monkeypatch.setattr(service, "_record_parent_event", original)
    admitted = service._admit_due(NOW)  # pyright: ignore[reportPrivateUsage]
    assert len(admitted) == 1
    assert service._admit_due(NOW) == ()  # pyright: ignore[reportPrivateUsage]
    with database.session() as session:
        assert (
            session.query(RunEventRecord)
            .filter_by(run_id=child, event_type="research.scheduled_recheck.admitted")
            .count()
            == 1
        )
        waiting = (
            session.query(ReviewScheduleRecord)
            .filter_by(parent_run_id=child, status="planned")
            .all()
        )
        assert len(waiting) == 2 and all(
            item.failure_code == "study_research_active" for item in waiting
        )
    _publish(database, admitted[0].child_run_id, NOW + timedelta(minutes=1))
    with database.session() as session:
        basis = tracking_basis_in_session(
            session, study_id=study_id, now=NOW + timedelta(minutes=1)
        )
        assert basis is not None and basis.run.run_id == child
    service.plan_due(now=NOW + timedelta(minutes=1))
    assert service.list_for_run(admitted[0].child_run_id) == []
    assert _report_bytes(database, root) == before


def test_acceptance_intent_wins_over_late_finish_and_active_manual_update_defers(
    database: Database,
) -> None:
    root, study_id = _root(database)
    older = _update(
        database, root, "update:older", accepted_at=NOW - timedelta(minutes=6), finished_at=NOW
    )
    newer = _update(
        database,
        root,
        "update:newer",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
    )
    pending = _update(database, root, "update:pending", accepted_at=NOW, finished_at=None)
    with database.session() as session:
        basis = tracking_basis_in_session(session, study_id=study_id, now=NOW)
        assert basis is not None and basis.run.run_id == newer and basis.run.run_id != older
        guard = research_admission_guard_in_session(session, study_id=study_id, now=NOW)
        assert guard.reason_code == "study_research_active"
        pending_run = session.get(RunRecord, pending)
        assert pending_run is not None and pending_run.status == "admitted"


@pytest.mark.parametrize(
    "corruption", ["command_hash", "result_hash", "revision_scope", "reject", "artifact_body"]
)
def test_corrupt_or_rejected_child_cannot_gain_authority(
    database: Database,
    corruption: str,
) -> None:
    root, study_id = _root(database)
    child = _update(
        database,
        root,
        "update:proof",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
    )
    with database.session() as session:
        if corruption == "command_hash":
            command = session.get(ProductCommandRecord, "update:proof")
            assert command is not None
            command.payload_hash = "0" * 64
        elif corruption == "result_hash":
            result = session.get(ResearchResultRecord, child)
            assert result is not None
            result.payload_hash = "0" * 64
        elif corruption == "revision_scope":
            revision = session.query(ReportRevisionRecord).filter_by(source_run_id=child).one()
            revision.study_id = "other-study"
        elif corruption == "reject":
            session.query(ArtifactRecord).filter_by(run_id=child).one().gate_status = "reject"
        else:
            artifact = session.query(ArtifactRecord).filter_by(run_id=child).one()
            artifact.payload_json = json.dumps({"facts": ["invented unrelated result"]})
    with database.session() as session:
        basis = tracking_basis_in_session(session, study_id=study_id, now=NOW)
        assert basis is not None and basis.run.run_id == root


def test_shared_budget_counts_both_origins_without_treating_delay_as_run_failure(
    database: Database,
) -> None:
    root, study_id = _root(database)
    with database.session() as session:
        for number, origin in enumerate(("automatic", "scheduled_recheck")):
            session.add(
                RunRecord(
                    run_id=f"run:budget:{number}",
                    event_id="event:tracking",
                    study_id=study_id,
                    status="failed",
                    strategy_version="research.v1",
                    runtime_version="controlled",
                    admission_origin=origin,
                    priority=50 if origin == "automatic" else 10,
                    created_at=NOW - timedelta(minutes=number + 1),
                    updated_at=NOW,
                    finished_at=NOW,
                    error_code="controlled-failure",
                )
            )
    with database.session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        guard = research_admission_guard_in_session(session, study_id=study_id, now=NOW)
        assert guard.reason_code == "research_admission_budget_exhausted"
        assert guard.details["daily_count"] == guard.details["study_hour_count"] == 2
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Seed one isolated due plan to verify the shared admission guard directly.
    service._ensure_plan(  # pyright: ignore[reportPrivateUsage]
        parent_run_id=root,
        horizon="24h",
        trigger_type="next_review",
        trigger_ref="budget-control",
        trigger_at=NOW,
        now=NOW,
    )
    assert service._admit_due(NOW) == ()  # pyright: ignore[reportPrivateUsage]
    with database.session() as session:
        plan = session.query(ReviewScheduleRecord).filter_by(trigger_ref="budget-control").one()
        assert (
            plan.status == "planned" and plan.failure_code == "research_admission_budget_exhausted"
        )
        assert plan.child_run_id is None


def test_two_writers_freeze_one_plan_per_horizon(database: Database) -> None:
    root, _study_id = _root(database)
    child = _update(
        database,
        root,
        "update:concurrent",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
    )
    barrier = Barrier(2)

    def discover() -> None:
        barrier.wait()
        # Two actual writers compete for the same frozen plan identities.
        service = ReviewScheduleService(database, clock=lambda: NOW)
        service._discover_plans(NOW)  # pyright: ignore[reportPrivateUsage]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(discover), pool.submit(discover)
        first.result()
        second.result()
    with database.session() as session:
        assert session.query(ReviewScheduleRecord).filter_by(parent_run_id=child).count() == 3
        assert (
            session.query(RunEventRecord)
            .filter_by(run_id=child, event_type="research.review_basis.activated")
            .count()
            == 3
        )


@pytest.mark.parametrize("status", ["admitted", "running", "failed"])
def test_dispatched_old_work_is_never_cancelled_and_unknown_host_blocks_admission(
    database: Database,
    status: str,
) -> None:
    root, study_id = _root(database)
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Preserve an already dispatched older plan while transferring report authority.
    service._ensure_plan(  # pyright: ignore[reportPrivateUsage]
        parent_run_id=root,
        horizon="24h",
        trigger_type="next_review",
        trigger_ref="old-dispatched",
        trigger_at=NOW + timedelta(hours=1),
        now=NOW,
    )
    with database.session() as session:
        plan = session.query(ReviewScheduleRecord).filter_by(trigger_ref="old-dispatched").one()
        plan.status, plan.child_run_id = "admitted", "run:old-dispatched"
        session.add(
            RunRecord(
                run_id=plan.child_run_id,
                event_id="event:tracking",
                study_id=study_id,
                status=status,
                strategy_version="research.v1",
                runtime_version="controlled",
                admission_origin="scheduled_recheck",
                priority=10,
                parent_run_id=root,
                created_at=NOW - timedelta(minutes=8),
                updated_at=NOW - timedelta(minutes=7),
                idempotency_key=f"research-review-plan:{plan.review_plan_id}",
                finished_at=NOW - timedelta(minutes=7) if status == "failed" else None,
                error_code="controlled-failure" if status == "failed" else None,
            )
        )
        session.add(
            DshSessionLinkRecord(
                run_id=plan.child_run_id,
                dsh_session_id="session:old-dispatched",
                request_hash="a" * 64,
                state="accepted",
                generation=1,
                upstream_identity_json="{}",
                plugin_build_hash="b" * 64,
                deadline_at=NOW + timedelta(hours=1),
                max_tool_calls=1,
                accepted_at=NOW - timedelta(minutes=8),
                created_at=NOW - timedelta(minutes=8),
                updated_at=NOW - timedelta(minutes=7),
            )
        )
    child = _update(
        database,
        root,
        f"update:host:{status}",
        accepted_at=NOW - timedelta(minutes=4),
        finished_at=NOW - timedelta(minutes=1),
        all_next_minutes=1,
    )
    assert service.plan_due() == ()
    with database.session() as session:
        old_run = session.get(RunRecord, "run:old-dispatched")
        assert old_run is not None and old_run.status == status
        guard = research_admission_guard_in_session(session, study_id=study_id, now=NOW)
        expected = "study_host_terminal_unknown" if status == "failed" else "study_research_active"
        assert guard.reason_code == expected
        plans = session.query(ReviewScheduleRecord).filter_by(parent_run_id=child).all()
        assert len(plans) == 3 and all(
            item.status == "planned" and item.failure_code == expected for item in plans
        )


def test_legacy_schedule_contract_defaults_and_corrupt_handoff_are_not_invented(
    database: Database,
) -> None:
    root, _study_id = _root(database)
    service = ReviewScheduleService(database, clock=lambda: NOW)
    # Use a legacy plan with no real handoff to prove the DTO does not invent one.
    service._ensure_plan(  # pyright: ignore[reportPrivateUsage]
        parent_run_id=root,
        horizon="24h",
        trigger_type="next_review",
        trigger_ref="legacy",
        trigger_at=NOW,
        now=NOW,
    )
    old = service.list_for_run(root)[0]
    legacy = ReviewScheduleView.model_validate(
        old.model_dump(
            exclude={
                "replacement_run_id",
                "replacement_review_plan_id",
                "retry_of_plan_id",
                "retry_attempt",
            }
        )
    )
    assert legacy.replacement_run_id is None and legacy.replacement_review_plan_id is None
    assert legacy.retry_of_plan_id is None and legacy.retry_attempt == 0
    with database.session() as session:
        plan = session.get(ReviewScheduleRecord, old.review_plan_id)
        assert plan is not None
        plan.status, plan.failure_code = "skipped", "superseded_by_newer_tracking_basis"
        session.add(
            RunEventRecord(
                run_id=root,
                sequence_no=1,
                event_type="research.review_basis.transferred",
                occurred_at=NOW,
                payload_json=json.dumps(
                    {
                        "old_parent_run_id": root,
                        "old_review_plan_id": old.review_plan_id,
                        "new_parent_run_id": root,
                        "new_review_plan_id": old.review_plan_id,
                        "horizon": "24h",
                        "reason_code": plan.failure_code,
                    }
                ),
            )
        )
    assert service.list_for_run(root)[0].replacement_run_id is None
