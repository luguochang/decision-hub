from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import DataJobCreate, DataJobView
from packages.kernel.decision_hub_kernel.application.data_jobs import (
    DATA_JOB_LEASE_SECONDS,
    DataJobService,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.provider_adapters.historical import HistoricalDataJobRunner

HISTORY_DATASET_ID = "crypto.btc_usdt.ohlcv_1h"
DAILY_HISTORY_DATASET_ID = "crypto.btc_usdt.ohlcv_1d"
ETH_DAILY_HISTORY_DATASET_ID = "crypto.eth_usdt.ohlcv_1d"
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_DAILY_LOOKBACK_DAYS = 370
QUEUED_JOBS_PER_TICK = 1


@dataclass(frozen=True)
class HistoricalRefreshReport:
    """The bounded result of one hourly history trigger.

    This is deliberately a data-job report rather than a Research Run.  A
    refresh can therefore be retried or skipped idempotently without creating
    an observation, notification, or research side effect.
    """

    status: Literal["completed", "partial", "failed", "already_complete", "in_progress"]
    job_id: str | None
    idempotency_key: str
    window_start_at: datetime
    window_end_at: datetime
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "job_id": self.job_id,
            "idempotency_key": self.idempotency_key,
            "window_start_at": self.window_start_at.isoformat(),
            "window_end_at": self.window_end_at.isoformat(),
            "error": self.error,
        }


class HistoricalRefreshWorker:
    """Trigger rolling immutable hourly and daily OKX history DataJobs."""

    def __init__(
        self,
        jobs: DataJobService,
        runner: HistoricalDataJobRunner,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        daily_lookback_days: int = DEFAULT_DAILY_LOOKBACK_DAYS,
        clock: Callable[[], datetime] = utcnow,
        daily_dataset_ids: tuple[str, ...] = (DAILY_HISTORY_DATASET_ID,),
    ) -> None:
        if lookback_days < 1 or lookback_days > 370:
            raise ValueError("historical_refresh_lookback_invalid")
        if daily_lookback_days < 1 or daily_lookback_days > 370:
            raise ValueError("historical_refresh_daily_lookback_invalid")
        self.jobs = jobs
        self.runner = runner
        self.lookback_days = lookback_days
        self.daily_lookback_days = daily_lookback_days
        self.clock = clock
        if (
            not daily_dataset_ids
            or len(set(daily_dataset_ids)) != len(daily_dataset_ids)
            or any(
                item not in {DAILY_HISTORY_DATASET_ID, ETH_DAILY_HISTORY_DATASET_ID}
                for item in daily_dataset_ids
            )
        ):
            raise ValueError("historical_refresh_daily_datasets_invalid")
        self.daily_dataset_ids = tuple(sorted(daily_dataset_ids))

    async def tick(self) -> HistoricalRefreshReport:
        self.jobs.recover_expired()
        # A bounded queue batch leaves the fixed hourly/daily schedule able to
        # advance. The queued-only CAS also fences a consumer with a stale list.
        for queued in self.jobs.oldest_queued(limit=QUEUED_JOBS_PER_TICK):
            try:
                await self.runner.run(queued.job_id, queued_only=True)
            except ValueError as exc:
                if str(exc) != "data_job_not_queued":
                    raise
        start, end = self._window()
        idempotency_key = _idempotency_key(end)
        request = DataJobCreate(
            idempotency_key=idempotency_key,
            requested_by="owner",
            dataset_ids=[HISTORY_DATASET_ID],
            window_start_at=start,
            window_end_at=end,
        )
        job, created = self.jobs.create(request)
        if not created and job.status == "completed":
            return self._report("already_complete", job, idempotency_key, start, end)
        if (
            not created
            and job.status == "running"
            and (self.clock() - job.updated_at).total_seconds() < DATA_JOB_LEASE_SECONDS
        ):
            return self._report("in_progress", job, idempotency_key, start, end)
        try:
            completed = await self.runner.run(job.job_id, queued_only=job.status == "queued")
        except ValueError as exc:
            # Another process may have claimed the same hour between create and
            # start.  Treat that as a harmless overlap; all other errors remain
            # visible to the process manager and the owner.
            if str(exc) in {"data_job_already_running", "data_job_not_queued"}:
                current = self.jobs.get(job.job_id) or job
                return self._overlap_report(current, idempotency_key, start, end)
            return self._report("failed", job, idempotency_key, start, end, error=str(exc))
        if completed.status not in {"completed", "partial", "failed"}:
            return self._report(
                "failed",
                completed,
                idempotency_key,
                start,
                end,
                error="historical_runner_non_terminal_status",
            )
        status = cast(Literal["completed", "partial", "failed"], completed.status)
        return self._report(status, completed, idempotency_key, start, end)

    async def tick_daily(self) -> HistoricalRefreshReport:
        """Refresh the completed daily window used by the seven-day outlook."""

        now = _aware(self.clock())
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=self.daily_lookback_days)
        idempotency_key = _daily_idempotency_key(start, end, self.daily_dataset_ids)
        request = DataJobCreate(
            idempotency_key=idempotency_key,
            requested_by="owner",
            dataset_ids=list(self.daily_dataset_ids),
            window_start_at=start,
            window_end_at=end,
        )
        job, created = self.jobs.create(request)
        if not created and job.status == "completed":
            return self._report("already_complete", job, idempotency_key, start, end)
        if (
            not created
            and job.status == "running"
            and (self.clock() - job.updated_at).total_seconds() < DATA_JOB_LEASE_SECONDS
        ):
            return self._report("in_progress", job, idempotency_key, start, end)
        try:
            completed = await self.runner.run(job.job_id, queued_only=job.status == "queued")
        except ValueError as exc:
            if str(exc) in {"data_job_already_running", "data_job_not_queued"}:
                current = self.jobs.get(job.job_id) or job
                return self._overlap_report(current, idempotency_key, start, end)
            return self._report("failed", job, idempotency_key, start, end, error=str(exc))
        if completed.status not in {"completed", "partial", "failed"}:
            return self._report(
                "failed",
                completed,
                idempotency_key,
                start,
                end,
                error="historical_runner_non_terminal_status",
            )
        status = cast(Literal["completed", "partial", "failed"], completed.status)
        return self._report(status, completed, idempotency_key, start, end)

    def _window(self) -> tuple[datetime, datetime]:
        now = _aware(self.clock())
        # The end is the beginning of the current UTC hour, so every row in the
        # requested range is a completed candle and can never come from the
        # future.  A rolling window lets a missed tick backfill itself on the
        # next run without requiring a second scheduler or queue.
        end = now.replace(minute=0, second=0, microsecond=0)
        return end - timedelta(days=self.lookback_days), end

    @staticmethod
    def _overlap_report(
        job: DataJobView, idempotency_key: str, start: datetime, end: datetime
    ) -> HistoricalRefreshReport:
        status: Literal["already_complete", "partial", "failed", "in_progress"]
        if job.status == "completed":
            status = "already_complete"
        elif job.status in {"partial", "failed"}:
            status = job.status
        else:
            status = "in_progress"
        return HistoricalRefreshWorker._report(status, job, idempotency_key, start, end)

    @staticmethod
    def _report(
        status: Literal["completed", "partial", "failed", "already_complete", "in_progress"],
        job: DataJobView,
        idempotency_key: str,
        start: datetime,
        end: datetime,
        *,
        error: str | None = None,
    ) -> HistoricalRefreshReport:
        return HistoricalRefreshReport(
            status=status,
            job_id=job.job_id,
            idempotency_key=idempotency_key,
            window_start_at=start,
            window_end_at=end,
            error=error or (job.errors[0] if status == "failed" and job.errors else None),
        )


def _idempotency_key(end: datetime) -> str:
    return f"live-okx-btc-1h-{end.astimezone(UTC):%Y%m%dT%H%MZ}-v1"


def _daily_idempotency_key(start: datetime, end: datetime, datasets: tuple[str, ...]) -> str:
    return (
        f"live-history-{','.join(sorted(datasets))}-"
        f"{start.astimezone(UTC):%Y%m%dT%H%MZ}-{end.astimezone(UTC):%Y%m%dT%H%MZ}-v2"
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
