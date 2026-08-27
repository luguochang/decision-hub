from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunStepRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


@dataclass(frozen=True)
class StepHandle:
    step_id: str
    started_at: datetime
    started_monotonic: float


class RunStepService:
    """Projects LangGraph node attempts into the durable run inspector."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start(self, run_id: str, step_name: str) -> StepHandle:
        started_at = utcnow()
        step_id = f"step_{uuid.uuid4().hex}"
        with self.database.session() as session:
            previous = (
                session.query(RunStepRecord)
                .filter_by(run_id=run_id, step_name=step_name, status="running")
                .all()
            )
            for row in previous:
                row.status = "interrupted"
                row.finished_at = started_at
                row.error_code = "step_interrupted"
            attempt = (
                session.query(RunStepRecord)
                .filter_by(run_id=run_id, step_name=step_name)
                .count()
                + 1
            )
            session.add(
                RunStepRecord(
                    step_id=step_id,
                    run_id=run_id,
                    step_name=step_name,
                    status="running",
                    attempt=attempt,
                    started_at=started_at,
                )
            )
        return StepHandle(step_id, started_at, time.perf_counter())

    def complete(self, handle: StepHandle) -> None:
        self._finish(handle, status="succeeded", error_code=None)

    def fail(self, handle: StepHandle, error: BaseException) -> None:
        error_code = (
            error.error_code
            if isinstance(error, AgentExecutionError)
            else "step_execution_failed"
        )
        self._finish(handle, status="failed", error_code=error_code)

    def _finish(
        self, handle: StepHandle, *, status: str, error_code: str | None
    ) -> None:
        finished_at = utcnow()
        with self.database.session() as session:
            row = session.get(RunStepRecord, handle.step_id)
            if not row:
                raise KeyError(handle.step_id)
            row.status = status
            row.finished_at = finished_at
            row.latency_ms = round(
                (time.perf_counter() - handle.started_monotonic) * 1000
            )
            row.error_code = error_code
