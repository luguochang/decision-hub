from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from packages.contracts_py.decision_hub_contracts.models import (
    ObservationCreate,
    RunStatus,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.kernel.decision_hub_kernel.ports.workflow import (
    DecisionExecutionRequest,
    DecisionWorkflowExecutor,
)


class AnalyzeTextService:
    def __init__(
        self,
        database: Database,
        executor: DecisionWorkflowExecutor,
        *,
        run_timeout_seconds: float = 180,
        strategy_version: str = "baseline.v1",
        admission_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.admission = AdmissionService(
            database, **({"clock": admission_clock} if admission_clock else {})
        )
        self.runs = RunService(database)
        self.executor = executor
        self.run_timeout_seconds = run_timeout_seconds
        self.strategy_version = strategy_version

    async def submit_and_run(self, request: ObservationCreate) -> tuple[str, str, bool]:
        event_id, envelope, admitted = self.admission.admit(request)
        if not admitted:
            existing_run = self.runs.for_event(event_id)
            if existing_run:
                return event_id, existing_run, False
        run_id, _ = self.runs.create(event_id, strategy_version=self.strategy_version)
        if admitted:
            await self.run_admitted(event_id, run_id)
        else:
            self.runs.set_status(run_id, RunStatus.degraded, error_code="duplicate_observation")
        return event_id, run_id, admitted

    def queue_research(
        self,
        request: ObservationCreate,
        idempotency_key: str,
        *,
        strategy_version: str = "research.v1",
    ) -> tuple[str, str, bool]:
        """Admit a candidate research Run for the durable research worker.

        This method intentionally does not execute a runtime. The API can
        acknowledge the durable queue while the separate research composition
        owns DSH/LangGraph execution and recovery.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key_required")
        event_id, _envelope, _admitted = self.admission.admit(request)
        run_id, created = self.runs.create(
            event_id,
            idempotency_key,
            strategy_version=strategy_version,
            admission_origin="manual",
        )
        return event_id, run_id, created

    async def run_admitted(
        self, event_id: str, run_id: str, _envelope: TextEnvelope | None = None
    ) -> bool:
        if not self.runs.claim_for_execution(run_id):
            return False
        deadline = datetime.now(UTC) + timedelta(seconds=self.run_timeout_seconds)
        request = DecisionExecutionRequest(
            run_id=run_id, event_id=event_id, deadline_at=deadline
        )
        try:
            async with asyncio.timeout(self.run_timeout_seconds):
                await self.executor.execute(request)
        except TimeoutError as exc:
            self.runs.set_status(run_id, RunStatus.failed, error_code="run_deadline_exceeded")
            raise AgentExecutionError(
                "run_deadline_exceeded", "decision run exceeded its deadline"
            ) from exc
        except AgentExecutionError as exc:
            self.runs.set_status(run_id, RunStatus.failed, error_code=exc.error_code)
            raise
        except ValueError as exc:
            if str(exc) != "pit_future_information":
                self.runs.set_status(run_id, RunStatus.failed, error_code="run_execution_failed")
                raise AgentExecutionError("run_execution_failed", "decision run failed") from exc
            self.runs.set_status(run_id, RunStatus.failed, error_code="pit_future_information")
            raise AgentExecutionError(
                "pit_future_information",
                "observation contains information newer than its received timestamp",
            ) from exc
        except Exception as exc:
            self.runs.set_status(run_id, RunStatus.failed, error_code="run_execution_failed")
            raise AgentExecutionError("run_execution_failed", "decision run failed") from exc
        return True

    async def resume(self, run_id: str) -> None:
        run = self.database.get_run_record(run_id)
        if not run:
            raise KeyError(run_id)
        async with asyncio.timeout(self.run_timeout_seconds):
            await self.executor.resume(run_id)
