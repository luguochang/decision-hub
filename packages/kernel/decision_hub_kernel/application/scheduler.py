from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from packages.kernel.decision_hub_kernel.application.analytical_assessment import (
    AnalyticalAssessmentService,
)
from packages.kernel.decision_hub_kernel.application.analytical_observation import (
    AnalyticalObservationService,
)
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
    SourceIngestResult,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.kernel.decision_hub_kernel.ports.sources import EventWindowSamplerPort

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.calibration import (
        KernelCalibrationProducer,
    )
    from packages.kernel.decision_hub_kernel.application.market_risk_data import (
        MarketRiskDataService,
    )
    from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
    from packages.kernel.decision_hub_kernel.application.study_effect_tracking import (
        StudyEffectTrackingService,
    )


async def _independent_channel(channel, name, action, cadence, timeout: float | None) -> None:
    """One owned loop keeps acquisition cadence outside synchronous source DB waits."""
    stopped, lock, active = threading.Event(), threading.Lock(), {}

    def run() -> None:
        async def owned() -> None:
            with lock:
                active.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
            try:
                if not stopped.is_set():
                    await channel(name, action, cadence, timeout, stop_event=stopped)
            except asyncio.CancelledError:
                if not stopped.is_set():
                    raise
            finally:
                # Clear while the loop is still live; shutdown can never cancel
                # a stale loop, and the outer owner still joins all loop cleanup.
                with lock:
                    active.clear()
        asyncio.run(owned())

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        stopped.set()
        with lock:
            if active:
                active['loop'].call_soon_threadsafe(active['task'].cancel)
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not worker.cancelled():
            worker.exception()
        raise


class SchedulerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    polled: tuple[SourceIngestResult, ...] = ()
    runs_started: int = 0
    runs_failed: int = 0
    outcomes_processed: int = 0
    analytical_observations_processed: int = 0
    analytical_assessments_processed: int = 0
    calibrations_processed: int = 0
    effect_tracking_requests_processed: int = 0
    market_risk_acquisitions_processed: int = 0
    market_risk_evaluations_processed: int = 0
    risk_research_admissions_processed: int = 0
    risk_shadow_jobs_processed: int = 0
    action_plans_evaluated: int = 0
    action_quotes_captured: int = 0
    action_executions_processed: int = 0
    market_cases_captured: int = 0
    market_enhancement_receipts_created: int = 0
    history_hourly_slots_processed: int = 0
    history_outcome_actions_processed: int = 0
    notifications_delivered: int = 0
    window_samples_attempted: int = 0
    window_samples_captured: int = 0
    window_samples_failed: int = 0
    generated_at: datetime


class RealtimeScheduler:
    """Small single-process coordinator; durable state remains in the Kernel database."""

    def __init__(
        self,
        ingestion: SourceIngestionService,
        *,
        run_executor: Callable[[RunTarget], Awaitable[None]] | None = None,
        outcomes: DueOutcomeService | None = None,
        analytical_observations: AnalyticalObservationService | None = None,
        analytical_assessments: AnalyticalAssessmentService | None = None,
        calibrations: KernelCalibrationProducer | None = None,
        effect_tracking: StudyEffectTrackingService | None = None,
        risk_data: MarketRiskDataService | None = None,
        risk_monitor: RiskMonitorService | None = None,
        risk_research_tick: Callable[[], Awaitable[int]] | None = None,
        risk_shadow_tick: Callable[[], Awaitable[int]] | None = None,
        risk_memory_tick: Callable[[], Awaitable[int]] | None = None,
        risk_enhancement_tick: Callable[[], Awaitable[int]] | None = None,
        action_monitor_tick: Callable[[], Awaitable[int]] | None = None,
        action_quote_tick: Callable[[], Awaitable[int]] | None = None,
        action_execution_tick: Callable[[], Awaitable[int]] | None = None,
        history_hourly_tick: Callable[[], Awaitable[int]] | None = None,
        history_outcome_tick: Callable[[], Awaitable[int]] | None = None,
        notifications: NotificationDispatcher | None = None,
        window_sampler: EventWindowSamplerPort | None = None,
        clock: Callable[[], datetime] = utcnow,
        run_timeout_seconds: float = 210,
        source_concurrency: int = 3,
        channel_observer: (
            Callable[[str, BaseException | None, float, float | None, bool], None] | None
        ) = None,
    ) -> None:
        self.ingestion = ingestion
        self.run_executor = run_executor
        self.outcomes = outcomes
        self.analytical_observations = analytical_observations
        self.analytical_assessments = analytical_assessments
        self.calibrations = calibrations
        self.effect_tracking = effect_tracking
        self.risk_data = risk_data
        self.risk_monitor = risk_monitor
        self.risk_research_tick = risk_research_tick
        self.risk_shadow_tick = risk_shadow_tick
        self.risk_memory_tick = risk_memory_tick
        self.risk_enhancement_tick = risk_enhancement_tick
        self.action_monitor_tick = action_monitor_tick
        self.action_quote_tick = action_quote_tick
        self.action_execution_tick = action_execution_tick
        self.history_hourly_tick = history_hourly_tick
        self.history_outcome_tick = history_outcome_tick
        self.notifications = notifications
        self.window_sampler = window_sampler
        self.clock = clock
        self.run_timeout_seconds = run_timeout_seconds
        self.source_concurrency = source_concurrency
        self.channel_errors: dict[str, str] = {}
        self.channel_observer = channel_observer

    async def tick(self) -> SchedulerReport:
        # One-shot acceptance preserves the poll -> execute dependency while
        # time-critical captures and delivery can already make progress.
        await self._discovery_recovery()
        polled_and_runs, window_report = await asyncio.gather(
            self._poll_and_execute(), self._windows()
        )
        outcomes = await self._outcomes()
        observations = await self._observations()
        assessments = await self._assessments()
        effect_requests = await self._effect_tracking_requests()
        calibrations = await self._calibrations()
        delivered = await self._notifications()
        # One-shot mode follows acquisition -> evaluation; continuous channels are independent.
        risk_acquisitions = await self._risk_acquisition()
        risk_evaluations = await self._risk_evaluations()
        action_evaluations = await self._action_monitor()
        action_quotes = await self._action_quotes()
        action_executions = await self._action_execution()
        research_admissions = await self._risk_research()
        shadow_jobs = await self._risk_shadow()
        captured_cases = await self._risk_memory()
        hourly_slots = await self._history_hourly()
        history_outcomes = await self._history_outcome()
        enhancement_receipts = await self._risk_enhancements()
        poll_results, (started, failed) = polled_and_runs
        return SchedulerReport(
            polled=poll_results,
            runs_started=started,
            runs_failed=failed,
            outcomes_processed=outcomes,
            analytical_observations_processed=observations,
            analytical_assessments_processed=assessments,
            calibrations_processed=calibrations,
            effect_tracking_requests_processed=effect_requests,
            market_risk_acquisitions_processed=risk_acquisitions,
            market_risk_evaluations_processed=risk_evaluations,
            risk_research_admissions_processed=research_admissions,
            risk_shadow_jobs_processed=shadow_jobs,
            action_plans_evaluated=action_evaluations,
            action_quotes_captured=action_quotes,
            action_executions_processed=action_executions,
            market_cases_captured=captured_cases,
            market_enhancement_receipts_created=enhancement_receipts,
            history_hourly_slots_processed=hourly_slots,
            history_outcome_actions_processed=history_outcomes,
            notifications_delivered=delivered,
            window_samples_attempted=window_report.attempted if window_report else 0,
            window_samples_captured=window_report.captured if window_report else 0,
            window_samples_failed=window_report.failed if window_report else 0,
            generated_at=self.clock(),
        )

    async def _discovery_recovery(self) -> None:
        # One local worker owns the bounded batch while other channels advance.
        worker = asyncio.create_task(
            asyncio.to_thread(self.ingestion.recover_discovery_admissions, limit=8)
        )
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # A thread cannot be cancelled. Join this owner before the channel
            # propagates timeout/cancellation or starts another recovery batch.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()
            raise

    async def _poll_sources(self) -> tuple[SourceIngestResult, ...]:
        semaphore = asyncio.Semaphore(self.source_concurrency)

        async def poll(source_id: str) -> SourceIngestResult:
            async with semaphore:
                return await self.ingestion.poll_due(source_id)

        return tuple(
            await asyncio.gather(
                *(poll(manifest.source_id) for manifest in self.ingestion.registry.manifests())
            )
        )

    async def _windows(self):
        window_report = (
            await self.ingestion.event_watches.capture_due(
                self.window_sampler,
                now=self.clock(),
            )
            if self.window_sampler is not None
            else None
        )
        if window_report is None:
            self.ingestion.event_watches.advance(now=self.clock())
        return window_report

    async def _poll_and_execute(self):
        polls = await self._poll_sources()
        return polls, await self._runs(polls)

    async def _runs(self, poll_results: tuple[SourceIngestResult, ...] = ()) -> tuple[int, int]:
        # Poll results are the fast path; durable admitted Runs cover a crash after
        # cursor commit and before graph execution.
        targets_by_run = {
            target.run_id: target for poll in poll_results for target in poll.run_targets
        }
        for event_id, run_id in self.ingestion.runs.admitted_targets(
            strategy_version="baseline.v1", recover_expired=True
        ):
            targets_by_run.setdefault(run_id, RunTarget(event_id=event_id, run_id=run_id))
        started = 0
        failed = 0
        if self.run_executor:
            for target in list(targets_by_run.values())[:8]:
                try:
                    async with asyncio.timeout(self.run_timeout_seconds):
                        await self.run_executor(target)
                    started += 1
                except TimeoutError:
                    self.ingestion.runs.fail_if_owned(
                        target.run_id, None, error_code="baseline_execution_timeout"
                    )
                    failed += 1
                except Exception:
                    failed += 1
        return started, failed

    async def _outcomes(self) -> int:
        return await self.outcomes.process_due(now=self.clock()) if self.outcomes else 0

    async def _observations(self) -> int:
        return (
            await self.analytical_observations.process_due(now=self.clock())
            if self.analytical_observations
            else 0
        )

    async def _notifications(self) -> int:
        return await self.notifications.dispatch_once() if self.notifications else 0

    async def _risk_acquisition(self, *, deadline_at: float | None = None) -> int:
        return await self.risk_data.refresh_due(deadline_at=deadline_at) if self.risk_data else 0

    async def _risk_evaluations(self) -> int:
        return await self.risk_monitor.process_due() if self.risk_monitor else 0

    async def _risk_research(self) -> int:
        return await self.risk_research_tick() if self.risk_research_tick else 0

    async def _risk_shadow(self) -> int:
        return await self.risk_shadow_tick() if self.risk_shadow_tick else 0

    async def _risk_memory(self) -> int:
        return await self.risk_memory_tick() if self.risk_memory_tick else 0

    async def _risk_enhancements(self) -> int:
        return await self.risk_enhancement_tick() if self.risk_enhancement_tick else 0

    async def _action_monitor(self) -> int:
        return await self.action_monitor_tick() if self.action_monitor_tick else 0

    async def _action_quotes(self) -> int:
        return await self.action_quote_tick() if self.action_quote_tick else 0

    async def _action_execution(self) -> int:
        return await self.action_execution_tick() if self.action_execution_tick else 0

    async def _history_hourly(self) -> int:
        return await self.history_hourly_tick() if self.history_hourly_tick else 0

    async def _history_outcome(self) -> int:
        return await self.history_outcome_tick() if self.history_outcome_tick else 0

    async def _assessments(self, *, deadline_at: float | None = None) -> int:
        if self.analytical_assessments is None:
            return 0
        if deadline_at is None:
            return await self.analytical_assessments.process_due()
        return await self.analytical_assessments.process_due(deadline_at=deadline_at)

    async def _calibrations(self, *, deadline_at: float | None = None) -> int:
        # The producer owns its durable plan claim and joins synchronous work
        # on cancellation; no unapproved plan is created by this scheduler.
        if self.calibrations is None:
            return 0
        if deadline_at is None:
            return await self.calibrations.process_due(limit=1)
        return await self.calibrations.process_due(limit=1, deadline_at=deadline_at)

    async def _effect_tracking_requests(self) -> int:
        return await self.effect_tracking.process_requests(limit=3) if self.effect_tracking else 0

    async def run_forever(self, interval_seconds: float = 30) -> None:
        async def channel(
            name: str, action: Callable[[], Awaitable[object]], cadence: float, timeout: float | None,
            *, stop_event: threading.Event | None = None,
        ) -> None:
            while True:
                started = perf_counter()
                deadline = None
                try:
                    async with asyncio.timeout(timeout) as deadline:
                        if name == 'calibrations':
                            await self._calibrations(deadline_at=deadline.when())
                        elif name == 'analytical_assessments':
                            await self._assessments(deadline_at=deadline.when())
                        elif name == 'risk_acquisition':
                            await self._risk_acquisition(deadline_at=deadline.when())
                        else:
                            await action()
                    self._observe_channel(name, None, started, timeout, deadline.expired())
                    self.channel_errors.pop(name, None)
                except Exception as exc:
                    self._observe_channel(name, exc, started, timeout,
                        deadline.expired() if deadline is not None else False)
                    self.channel_errors[name] = type(exc).__name__
                    logging.getLogger(__name__).error(
                        "realtime_channel_failed channel=%s error=%s", name, type(exc).__name__
                    )
                if stop_event is not None and stop_event.is_set():
                    return
                await asyncio.sleep(cadence)

        # Each channel has one owner in this process. Durable admission keys,
        # Run claims and sample IDs remain the authority after a restart.
        async with asyncio.TaskGroup() as group:
            for name, action, cadence, timeout in (
                ("sources", self._poll_sources, interval_seconds, 180),
                ("discovery_recovery", self._discovery_recovery, interval_seconds, 60),
                ("windows", self._windows, min(interval_seconds, 1), 60),
                ("runs", self._runs, interval_seconds, self.run_timeout_seconds * 8 + 5),
                ("outcomes", self._outcomes, interval_seconds, 60),
                ("analytical_observations", self._observations, interval_seconds, 60),
                ("analytical_assessments", self._assessments, min(interval_seconds, 1), 90),
                (
                    "effect_tracking_requests", self._effect_tracking_requests,
                    min(interval_seconds, 1), 180,
                ),
                ("calibrations", self._calibrations, interval_seconds, 180),
                ("notifications", self._notifications, interval_seconds, 60),
                ("risk_acquisition", self._risk_acquisition, 60, 90),
                ("risk_monitor", self._risk_evaluations, 60, 30),
                ("action_monitor", self._action_monitor, 60, 30),
                ("action_quotes", self._action_quotes, 5, 75),
                ("action_execution", self._action_execution, 5, 30),
                ("risk_research_admission", self._risk_research, 60, None),
                ("risk_shadow", self._risk_shadow, 1, 120),
                ("market_memory", self._risk_memory, 60, 120),
                ("market_enhancements", self._risk_enhancements, 60, 120),
                # Only the in-memory hour gate is polled at this cadence. Full
                # guarded producer work remains a finite two-scope batch.
                ("history_hourly_forecasts", self._history_hourly, 1, 120),
                ("history_outcome_publications", self._history_outcome, 60, 120),
            ):
                if name == "action_quotes" and self.action_quote_tick is None:
                    continue
                if name == "action_execution" and self.action_execution_tick is None:
                    continue
                if name == "action_monitor" and self.action_monitor_tick is None:
                    continue
                owned = (_independent_channel(channel, name, action, cadence, timeout)
                         if name == "risk_research_admission"
                          or (name == "risk_acquisition" and self.risk_data is not None)
                         else channel(name, action, cadence, timeout))
                group.create_task(owned, name=name)

    def _observe_channel(self, name: str, error: BaseException | None, started: float,
                         timeout: float | None, expired: bool) -> None:
        if self.channel_observer is not None:
            try:
                self.channel_observer(
                    name, error, (perf_counter() - started) * 1000, timeout, expired
                )
            except BaseException:
                pass  # Only the optional diagnostic callback is isolated.
