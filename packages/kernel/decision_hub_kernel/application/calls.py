from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunCallRecord,
    RunRecord,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentResult,
    AgentRuntime,
)


@dataclass(frozen=True)
class CallHandle:
    call_id: str
    started_at: datetime
    started_monotonic: float


class RunCallService:
    """Projects normalized model-call metadata into the business read model."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        run_id: str,
        role: str,
        *,
        attempt: int = 1,
        runtime: AgentRuntime | None = None,
    ) -> CallHandle:
        started_at = utcnow()
        call_id = f"call_{uuid.uuid4().hex}"
        provider_config = getattr(runtime, "provider_config", None)
        with self.database.session() as session:
            session.add(
                RunCallRecord(
                    call_id=call_id,
                    run_id=run_id,
                    role=role,
                    status="running",
                    attempt=attempt,
                    started_at=started_at,
                    runtime_id=getattr(runtime, "runtime_id", None),
                    runtime_version=getattr(runtime, "runtime_version", None),
                    provider_id=getattr(provider_config, "provider_id", None),
                    model=getattr(provider_config, "model", None),
                    api_mode=getattr(runtime, "api_mode", None),
                    schema_version="agent-payload.v1",
                )
            )
        return CallHandle(call_id, started_at, time.perf_counter())

    def next_attempt(self, run_id: str, role: str) -> int:
        with self.database.session() as session:
            count = session.query(RunCallRecord).filter_by(run_id=run_id, role=role).count()
        return count + 1

    def complete(self, handle: CallHandle, result: AgentResult) -> None:
        finished_at = utcnow()
        with self.database.session() as session:
            row = session.get(RunCallRecord, handle.call_id)
            if not row:
                raise KeyError(handle.call_id)
            row.status = "succeeded"
            row.finished_at = finished_at
            row.latency_ms = round((time.perf_counter() - handle.started_monotonic) * 1000)
            row.runtime_id = result.runtime_id
            row.runtime_version = result.runtime_version
            row.provider_id = result.provider_id
            row.model = result.model
            row.api_mode = result.api_mode
            row.schema_version = result.schema_version
            row.prompt_tokens = result.usage.prompt_tokens
            row.completion_tokens = result.usage.completion_tokens
            row.total_tokens = result.usage.total_tokens
            row.cost_usd = result.usage.cost_usd
            row.cost_status = result.usage.cost_status
            row.pricing_version = result.usage.pricing_version
            run = session.get(RunRecord, row.run_id)
            if run:
                run.runtime_version = result.runtime_version
                session.flush()
                rows = session.query(RunCallRecord).filter_by(run_id=row.run_id).all()
                costs = self._known_costs(rows)
                run.cost_usd = sum(costs) if costs is not None else None

    def enforce_budget(self, run_id: str, budget: float | None) -> None:
        if budget is None:
            return
        with self.database.session() as session:
            rows = session.query(RunCallRecord).filter_by(run_id=run_id).all()
            costs = self._known_costs(rows)
            if costs is None:
                raise AgentExecutionError(
                    "budget_accounting_unavailable",
                    "cost budget is enabled but provider usage cannot be priced",
                )
            total = sum(costs)
        if total > budget:
            raise AgentExecutionError(
                "budget_exhausted",
                "run cost exceeded its configured budget",
            )

    @staticmethod
    def _known_costs(rows: list[RunCallRecord]) -> list[float] | None:
        if not rows:
            return []
        costs: list[float] = []
        for item in rows:
            if (
                item.cost_usd is None
                or item.cost_status not in {"known", "estimated"}
            ):
                return None
            costs.append(item.cost_usd)
        return costs

    def fail(self, handle: CallHandle, error: BaseException) -> None:
        finished_at = utcnow()
        if isinstance(error, AgentExecutionError):
            error_code = error.error_code
            retryable = error.retryable
            provider_id = error.provider_id
            model = error.model
        else:
            error_code = "unknown_runtime_error"
            retryable = False
            provider_id = None
            model = None
        with self.database.session() as session:
            row = session.get(RunCallRecord, handle.call_id)
            if not row:
                raise KeyError(handle.call_id)
            row.status = "failed"
            row.finished_at = finished_at
            row.latency_ms = round((time.perf_counter() - handle.started_monotonic) * 1000)
            row.error_code = error_code
            row.retryable = retryable
            row.provider_id = provider_id
            row.model = model
