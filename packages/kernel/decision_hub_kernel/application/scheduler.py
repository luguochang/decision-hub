from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
    SourceIngestResult,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow


class SchedulerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    polled: tuple[SourceIngestResult, ...] = ()
    runs_started: int = 0
    runs_failed: int = 0
    outcomes_processed: int = 0
    notifications_delivered: int = 0
    generated_at: datetime


class RealtimeScheduler:
    """Small single-process coordinator; durable state remains in the Kernel database."""

    def __init__(
        self,
        ingestion: SourceIngestionService,
        *,
        run_executor: Callable[[RunTarget], Awaitable[None]] | None = None,
        outcomes: DueOutcomeService | None = None,
        notifications: NotificationDispatcher | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.ingestion = ingestion
        self.run_executor = run_executor
        self.outcomes = outcomes
        self.notifications = notifications
        self.clock = clock

    async def tick(self) -> SchedulerReport:
        poll_results = tuple(
            [
                await self.ingestion.poll_due(manifest.source_id)
                for manifest in self.ingestion.registry.manifests()
            ]
        )
        # Poll results are the fast path; durable admitted Runs cover a crash after
        # cursor commit and before graph execution.
        targets_by_run = {
            target.run_id: target
            for poll in poll_results
            for target in poll.run_targets
        }
        for event_id, run_id in self.ingestion.runs.admitted_targets():
            targets_by_run.setdefault(run_id, RunTarget(event_id=event_id, run_id=run_id))
        started = 0
        failed = 0
        if self.run_executor:
            for target in targets_by_run.values():
                try:
                    await self.run_executor(target)
                    started += 1
                except Exception:
                    failed += 1
        outcomes = await self.outcomes.process_due(now=self.clock()) if self.outcomes else 0
        delivered = await self.notifications.dispatch_once() if self.notifications else 0
        return SchedulerReport(
            polled=poll_results,
            runs_started=started,
            runs_failed=failed,
            outcomes_processed=outcomes,
            notifications_delivered=delivered,
            generated_at=self.clock(),
        )

    async def run_forever(self, interval_seconds: float = 30) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(interval_seconds)
