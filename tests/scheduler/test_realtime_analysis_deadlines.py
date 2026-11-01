from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from packages.evals import assessment_basis
from packages.kernel.decision_hub_kernel.application.analytical_assessment import (
    AnalyticalAssessmentService,
)
from packages.kernel.decision_hub_kernel.application.calibration import (
    KernelCalibrationProducer,
)
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalyticalAssessmentJobRecord,
    Database,
    StrategyCalibrationJobRecord,
    as_utc,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

NOW = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'deadline.sqlite3'}")
    database.create_all()
    return database


def test_source_identity_memo_keeps_one_capture_but_fresh_check_rejects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    scope = root / "scope"
    gateway = root / "packages" / "workbench_adapters" / "durable_research_gateway.py"
    scope.mkdir(parents=True)
    gateway.parent.mkdir(parents=True)
    source = scope / "worker.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    gateway.write_text("GATEWAY = 1\n", encoding="utf-8")
    monkeypatch.setattr(assessment_basis, "_ROOT", root)
    monkeypatch.setattr(assessment_basis, "_SOURCE_DIRS", ("scope",))

    checkpoints: list[int] = []
    def checkpoint() -> None:
        checkpoints.append(len(checkpoints))

    loaded = assessment_basis.assessment_manifest_hash(checkpoint=checkpoint)
    monkeypatch.setattr(assessment_basis, "_LOADED_MANIFEST_HASH", loaded)
    memo: dict[Any, Any] = {}
    assert assessment_basis.loaded_assessment_manifest_hash(
        memo=memo, checkpoint=checkpoint,
    ) == loaded

    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert assessment_basis.loaded_assessment_manifest_hash(
        memo=memo, checkpoint=checkpoint,
    ) == loaded
    with pytest.raises(ValueError, match="assessment_loaded_verifier_source_changed"):
        assessment_basis.loaded_assessment_manifest_hash(checkpoint=checkpoint)
    assert len(checkpoints) >= 6


def test_assessment_retry_claim_is_exact_and_clears_real_sqlite_lease(tmp_path: Path) -> None:
    database = _database(tmp_path)
    service = AnalyticalAssessmentService(database, clock=lambda: NOW)
    with database.session() as session:
        session.add(AnalyticalAssessmentJobRecord(
            outlook_id="outlook_deadline",
            status="running",
            attempt=3,
            next_attempt_at=NOW,
            lease_owner="assessment_owner",
            lease_expires_at=NOW + timedelta(minutes=2),
            verifier_version="manifest",
        ))

    assert service._retry_claim(  # pyright: ignore[reportPrivateUsage]
        "outlook_deadline", "assessment_owner", 3,
        error="assessment_deadline_elapsed",
    )
    with database.session() as session:
        row = session.get(AnalyticalAssessmentJobRecord, "outlook_deadline")
        assert row is not None
        assert row.status == "retry"
        assert row.lease_owner is None and row.lease_expires_at is None
        assert row.error_code == "assessment_deadline_elapsed"
        assert as_utc(row.next_attempt_at) == NOW + timedelta(seconds=120)
        row.status = "running"
        row.lease_owner = "new_owner"
        row.lease_expires_at = NOW + timedelta(minutes=2)
        row.attempt = 4

    assert not service._retry_claim(  # pyright: ignore[reportPrivateUsage]
        "outlook_deadline", "assessment_owner", 3,
        error="assessment_deadline_elapsed",
    )


def test_calibration_retry_claim_is_exact_and_clears_real_sqlite_lease(tmp_path: Path) -> None:
    database = _database(tmp_path)
    producer = KernelCalibrationProducer(
        database, archive=RawBodyArchive(tmp_path / "raw"), clock=lambda: NOW,
    )
    with database.session() as session:
        session.add(StrategyCalibrationJobRecord(
            job_id="caljob_deadline",
            request_id="request_deadline",
            plan_json='{"plan":"original"}',
            plan_hash="a" * 64,
            registered_at=NOW,
            status="running",
            attempt=2,
            next_attempt_at=NOW,
            lease_owner="calibration_owner",
            lease_expires_at=NOW + timedelta(minutes=30),
        ))

    assert producer._retry_claim(  # pyright: ignore[reportPrivateUsage]
        job_id="caljob_deadline",
        owner="calibration_owner",
        attempt=2,
        plan_hash="a" * 64,
        error_code="calibration_deadline_elapsed",
    )
    with database.session() as session:
        row = session.get(StrategyCalibrationJobRecord, "caljob_deadline")
        assert row is not None
        assert row.status == "retry"
        assert row.lease_owner is None and row.lease_expires_at is None
        assert row.error_code == "calibration_deadline_elapsed"
        assert as_utc(row.next_attempt_at) == NOW + timedelta(minutes=5)
        row.status = "running"
        row.lease_owner = "new_owner"
        row.lease_expires_at = NOW + timedelta(minutes=30)
        row.attempt = 3

    assert not producer._retry_claim(  # pyright: ignore[reportPrivateUsage]
        job_id="caljob_deadline",
        owner="calibration_owner",
        attempt=2,
        plan_hash="a" * 64,
        error_code="calibration_deadline_elapsed",
    )


@pytest.mark.asyncio
async def test_assessment_deadline_joins_worker_and_releases_batch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AnalyticalAssessmentService(cast(Any, None))
    entered = threading.Event()
    exited = threading.Event()

    def blocked(*, limit: int, checkpoint: Any) -> int:
        assert limit == 1
        entered.set()
        try:
            while True:
                checkpoint()
                time.sleep(0.002)
        finally:
            exited.set()

    monkeypatch.setattr(service, "_process_batch", blocked)
    loop = asyncio.get_running_loop()
    with pytest.raises(TimeoutError, match="assessment_deadline_elapsed"):
        await service.process_due(limit=1, deadline_at=loop.time() + 0.04)
    assert entered.is_set() and exited.is_set()
    assert not service._batch_lock.locked()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_assessment_success_and_scheduler_forward_the_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AnalyticalAssessmentService(cast(Any, None))
    calls: list[int] = []

    def completed(*, limit: int, checkpoint: Any) -> int:
        checkpoint()
        calls.append(limit)
        return limit

    monkeypatch.setattr(service, "_process_batch", completed)
    loop = asyncio.get_running_loop()
    assert await service.process_due(limit=1, deadline_at=loop.time() + 1) == 1
    assert calls == [1]

    class Assessments:
        received: float | None = None

        async def process_due(self, *, deadline_at: float | None = None) -> int:
            self.received = deadline_at
            return 3

    assessments = Assessments()
    scheduler = RealtimeScheduler(
        cast(Any, object()), analytical_assessments=cast(Any, assessments),
    )
    assert await scheduler._assessments(deadline_at=123.25) == 3  # pyright: ignore[reportPrivateUsage]
    assert assessments.received == 123.25
