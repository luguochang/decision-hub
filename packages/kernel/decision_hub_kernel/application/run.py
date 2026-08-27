from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import update

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunStatus,
    utcnow,
)


class RunService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        event_id: str,
        idempotency_key: str | None = None,
        *,
        strategy_version: str = "baseline.v1",
    ) -> tuple[str, bool]:
        if idempotency_key:
            with self.database.session() as session:
                existing = (
                    session.query(RunRecord).filter_by(idempotency_key=idempotency_key).first()
                )
                if existing:
                    return existing.run_id, False
        run_id = f"run_{uuid.uuid4().hex}"
        now = utcnow()
        with self.database.session() as session:
            session.add(
                RunRecord(
                    run_id=run_id,
                    event_id=event_id,
                    idempotency_key=idempotency_key,
                    strategy_version=strategy_version,
                    created_at=now,
                    updated_at=now,
                )
            )
        return run_id, True

    def for_event(self, event_id: str) -> str | None:
        with self.database.session() as session:
            existing = (
                session.query(RunRecord)
                .filter_by(event_id=event_id)
                .order_by(RunRecord.created_at.desc())
                .first()
            )
            return existing.run_id if existing else None

    def claim_for_execution(self, run_id: str) -> bool:
        """Atomically claim an admitted Run before a scheduler/API executes its graph."""
        now = utcnow()
        with self.database.session() as session:
            result = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.run_id == run_id,
                    RunRecord.status == RunStatus.admitted.value,
                )
                .values(status=RunStatus.running.value, updated_at=now)
            )
            claimed = cast(Any, result).rowcount
        return claimed == 1

    def admitted_targets(self) -> list[tuple[str, str]]:
        """Return durable event/run pairs that still need their first execution."""
        with self.database.session() as session:
            rows = (
                session.query(RunRecord)
                .filter(RunRecord.status == RunStatus.admitted.value)
                .order_by(RunRecord.created_at.asc())
                .all()
            )
            return [(row.event_id, row.run_id) for row in rows]

    def set_status(self, run_id: str, status: RunStatus, *, error_code: str | None = None) -> None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if not run:
                raise KeyError(run_id)
            run.status = status.value
            run.updated_at = utcnow()
            run.error_code = error_code
            if status in {
                RunStatus.completed,
                RunStatus.degraded,
                RunStatus.failed,
                RunStatus.cancelled,
            }:
                run.finished_at = run.updated_at
