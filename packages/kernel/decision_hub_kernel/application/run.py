from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, or_, update

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunStatus,
    utcnow,
)

RUN_ADMISSION_PRIORITIES = {
    "legacy": 0,
    "scheduled_recheck": 10,
    "automatic": 50,
    "manual": 100,
}


class RunClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    strategy_version: str
    worker_id: str
    lease_expires_at: datetime
    recovered: bool = False


class RunService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.database = database
        self.clock = clock

    def create(
        self,
        event_id: str,
        idempotency_key: str | None = None,
        *,
        strategy_version: str = "baseline.v1",
        available_at: datetime | None = None,
        parent_run_id: str | None = None,
        admission_origin: str = "manual",
        priority: int | None = None,
    ) -> tuple[str, bool]:
        expected_priority = RUN_ADMISSION_PRIORITIES.get(admission_origin)
        if expected_priority is None:
            raise ValueError("run_admission_origin_invalid")
        if priority is not None and priority != expected_priority:
            raise ValueError("run_priority_origin_mismatch")
        effective_priority = expected_priority
        if idempotency_key:
            with self.database.session() as session:
                existing = (
                    session.query(RunRecord).filter_by(idempotency_key=idempotency_key).first()
                )
                if existing:
                    return existing.run_id, False
        run_id = f"run_{uuid.uuid4().hex}"
        now = self.clock()
        if available_at is not None and available_at.tzinfo is None:
            raise ValueError("run_available_at_timezone_required")
        with self.database.session() as session:
            if parent_run_id is not None:
                parent = session.get(RunRecord, parent_run_id)
                if parent is None:
                    raise ValueError("parent_run_not_found")
                if parent.event_id != event_id:
                    raise ValueError("parent_run_event_mismatch")
            session.add(
                RunRecord(
                    run_id=run_id,
                    event_id=event_id,
                    idempotency_key=idempotency_key,
                    strategy_version=strategy_version,
                    available_at=available_at,
                    parent_run_id=parent_run_id,
                    admission_origin=admission_origin,
                    priority=effective_priority,
                    created_at=now,
                    updated_at=now,
                )
            )
        return run_id, True

    def for_event(
        self, event_id: str, *, strategy_version: str | None = None
    ) -> str | None:
        with self.database.session() as session:
            query = session.query(RunRecord).filter_by(event_id=event_id)
            if strategy_version is not None:
                query = query.filter_by(strategy_version=strategy_version)
            existing = query.order_by(RunRecord.created_at.desc()).first()
            return existing.run_id if existing else None

    def claim_for_execution(
        self,
        run_id: str,
        *,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
        strategy_version: str | None = None,
    ) -> bool:
        """Claim an admitted Run, or reclaim an expired durable lease.

        The no-worker form preserves the synchronous R0/R1 compatibility path;
        durable workers must provide both identity and a positive lease.
        """
        if (worker_id is None) != (lease_seconds is None):
            raise ValueError("run_lease_configuration_required")
        if lease_seconds is not None and lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        predicates = [
            RunRecord.run_id == run_id,
            RunRecord.status == RunStatus.admitted.value,
            or_(RunRecord.available_at.is_(None), RunRecord.available_at <= now),
        ]
        if strategy_version is not None:
            predicates.append(RunRecord.strategy_version == strategy_version)
        values: dict[str, object] = {"status": RunStatus.running.value, "updated_at": now}
        if worker_id is not None and lease_seconds is not None:
            predicates[1] = or_(
                RunRecord.status == RunStatus.admitted.value,
                and_(
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_expires_at <= now,
                ),
            )
            values.update(
                lease_owner=worker_id,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        with self.database.session() as session:
            result = session.execute(
                update(RunRecord)
                .where(*predicates)
                .values(**values)
            )
            claimed = cast(Any, result).rowcount
        return claimed == 1

    def claim_next(
        self,
        *,
        strategy_version: str,
        worker_id: str,
        lease_seconds: int,
    ) -> RunClaim | None:
        if not strategy_version:
            raise ValueError("run_strategy_required")
        if not worker_id:
            raise ValueError("run_worker_identity_required")
        if lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        expires = now + timedelta(seconds=lease_seconds)
        with self.database.session() as session:
            candidates = (
                session.query(RunRecord)
                .filter(
                    RunRecord.strategy_version == strategy_version,
                    or_(RunRecord.available_at.is_(None), RunRecord.available_at <= now),
                    or_(
                        RunRecord.status == RunStatus.admitted.value,
                        and_(
                            RunRecord.status == RunStatus.running.value,
                            RunRecord.lease_expires_at <= now,
                        ),
                    ),
                )
                .order_by(
                    RunRecord.priority.desc(),
                    RunRecord.created_at.asc(),
                    RunRecord.run_id.asc(),
                )
                .limit(10)
                .all()
            )
            for candidate in candidates:
                was_running = candidate.status == RunStatus.running.value
                changed = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.run_id == candidate.run_id,
                        RunRecord.strategy_version == strategy_version,
                        or_(
                            RunRecord.status == RunStatus.admitted.value,
                            and_(
                                RunRecord.status == RunStatus.running.value,
                                RunRecord.lease_expires_at <= now,
                            ),
                        ),
                    )
                    .values(
                        status=RunStatus.running.value,
                        lease_owner=worker_id,
                        lease_expires_at=expires,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(changed, "rowcount", 0)) == 1:
                    return RunClaim(
                        event_id=candidate.event_id,
                        run_id=candidate.run_id,
                        strategy_version=strategy_version,
                        worker_id=worker_id,
                        lease_expires_at=expires,
                        recovered=was_running,
                    )
        return None

    def renew_lease(self, run_id: str, worker_id: str, *, lease_seconds: int) -> bool:
        if not worker_id:
            raise ValueError("run_worker_identity_required")
        if lease_seconds <= 0:
            raise ValueError("run_lease_invalid")
        now = self.clock()
        with self.database.session() as session:
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.status == RunStatus.running.value,
                    RunRecord.lease_owner == worker_id,
                    or_(RunRecord.lease_expires_at.is_(None), RunRecord.lease_expires_at > now),
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
            )
            return int(getattr(changed, "rowcount", 0)) == 1

    def admitted_targets(
        self, *, strategy_version: str | None = None
    ) -> list[tuple[str, str]]:
        """Return durable event/run pairs that still need their first execution.

        A strategy filter keeps independent worker roles from claiming each
        other's admitted runs. The unfiltered form preserves the R0/R1
        scheduler behavior.
        """
        with self.database.session() as session:
            query = session.query(RunRecord).filter(RunRecord.status == RunStatus.admitted.value)
            query = query.filter(
                or_(RunRecord.available_at.is_(None), RunRecord.available_at <= self.clock())
            )
            if strategy_version is not None:
                query = query.filter(RunRecord.strategy_version == strategy_version)
            rows = query.order_by(
                RunRecord.priority.desc(),
                RunRecord.created_at.asc(),
                RunRecord.run_id.asc(),
            ).all()
            return [(row.event_id, row.run_id) for row in rows]

    def set_status(self, run_id: str, status: RunStatus, *, error_code: str | None = None) -> None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            run.status = status.value
            run.updated_at = self.clock()
            run.error_code = error_code
            if status in {
                RunStatus.completed,
                RunStatus.degraded,
                RunStatus.failed,
                RunStatus.cancelled,
            }:
                run.finished_at = run.updated_at
                run.lease_owner = None
                run.lease_expires_at = None
