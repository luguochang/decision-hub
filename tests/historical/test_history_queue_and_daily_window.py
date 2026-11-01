from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.hub_worker.historical import HistoricalRefreshWorker
from packages.contracts_py.decision_hub_contracts import DataJobCreate
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.historical import (
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    HistoricalDatasetBatch,
)

NOW = datetime(2026, 10, 20, 4, 37, tzinfo=UTC)
HOURLY = "crypto.btc_usdt.ohlcv_1h"
DAILY = "crypto.btc_usdt.ohlcv_1d"
ETH_DAILY = "crypto.eth_usdt.ohlcv_1d"


class _Loader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime]] = []

    async def load(
        self, dataset_id: str, start: datetime, end: datetime
    ) -> HistoricalDatasetBatch:
        self.calls.append((dataset_id, start, end))
        await asyncio.sleep(0)
        step = timedelta(days=1) if dataset_id.endswith("1d") else timedelta(hours=1)
        return HistoricalDatasetBatch(
            dataset_id=dataset_id,
            source_id="gateio-public" if dataset_id.endswith("1d") else "okx-public",
            rows=[
                {
                    "timestamp": (start + offset * step).isoformat(),
                    "open": "100", "high": "101", "low": "99", "close": "100",
                    "volume_base": "1", "confirmed": True,
                }
                for offset in range((end - start) // step)
            ],
            unit="usdt_and_eth" if dataset_id == ETH_DAILY else "usdt_and_btc",
            frequency="1d" if dataset_id.endswith("1d") else "1h",
            instrument="ETH-USDT" if dataset_id == ETH_DAILY else "BTC-USDT",
            venue="gateio" if dataset_id.endswith("1d") else "okx",
            contract_type="spot", publication_time_precision="second",
            revision_policy="immutable", gaps=[],
        )


def _setup(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'history.sqlite3'}")
    database.create_all()
    jobs = DataJobService(database, clock=lambda: NOW)
    loader = _Loader()
    runner = HistoricalDataJobRunner(
        jobs, HistoricalDataArchive(tmp_path / "assets"),
        {item: loader for item in (HOURLY, DAILY, ETH_DAILY)}, clock=lambda: NOW,
    )
    return database, jobs, loader, runner


def _request(key: str, *, days: int = 2, datasets: tuple[str, ...] = (DAILY,)):
    end = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    return DataJobCreate(
        idempotency_key=key, requested_by="owner", dataset_ids=list(datasets),
        window_start_at=end - timedelta(days=days), window_end_at=end,
    )


@pytest.mark.asyncio
async def test_two_queued_consumers_late_claim_runs_once_but_manual_rerun_remains(tmp_path):
    database, jobs, loader, runner = _setup(tmp_path)
    job, _ = jobs.create(_request("owner-queued"))
    other_jobs = DataJobService(database, clock=lambda: NOW)
    other_runner = HistoricalDataJobRunner(
        other_jobs, runner.archive, runner.loaders, clock=lambda: NOW,
    )
    # Both consumers observed queued before the first finished; the second's
    # stale snapshot must not authorize a new attempt after completion.
    first_snapshot = jobs.list_page(status="queued")[0][0]
    late_snapshot = other_jobs.list_page(status="queued")[0][0]
    first = await runner.run(first_snapshot.job_id, queued_only=True)
    with pytest.raises(ValueError, match="^data_job_not_queued$"):
        await other_runner.run(late_snapshot.job_id, queued_only=True)
    assert first.status == "completed"
    assert len(loader.calls) == 1
    assert jobs.get(job.job_id).attempt == 1

    rerun = await other_runner.run(job.job_id)
    assert rerun.status == "completed"
    assert rerun.attempt == 2
    assert len(loader.calls) == 2

    next_job, _ = jobs.create(_request("two-live-consumers"))
    outcomes = await asyncio.gather(
        runner.run(next_job.job_id, queued_only=True),
        other_runner.run(next_job.job_id, queued_only=True),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ValueError) and str(item) == "data_job_not_queued"
               for item in outcomes) == 1
    assert jobs.get(next_job.job_id).status == "completed"
    assert jobs.get(next_job.job_id).attempt == 1
    assert len(loader.calls) == 3


@pytest.mark.asyncio
async def test_normal_tick_consumes_one_oldest_queued_and_advances_fixed_schedules(tmp_path):
    database, jobs, loader, runner = _setup(tmp_path)
    del database
    first, _ = jobs.create(_request("owner-first"))
    second, _ = jobs.create(_request("owner-second"))
    # Stable created_at ties use job_id; select the same FIFO tie order.
    expected, pending = sorted([first, second], key=lambda item: item.job_id)
    worker = HistoricalRefreshWorker(jobs, runner, clock=lambda: NOW)

    hourly = await worker.tick()
    assert jobs.get(expected.job_id).status == "completed"
    assert jobs.get(pending.job_id).status == "queued"
    assert hourly.status == "completed"
    assert hourly.window_end_at - hourly.window_start_at == timedelta(days=90)
    assert len(loader.calls) == 2
    daily = await worker.tick_daily()
    assert daily.status == "completed"
    assert jobs.get(pending.job_id).status == "queued"
    again = await worker.tick()
    assert again.status == "already_complete"
    assert jobs.get(pending.job_id).status == "completed"
    assert len(loader.calls) == 4


@pytest.mark.asyncio
async def test_daily_370_complete_days_does_not_collide_with_existing_90_day_key(tmp_path):
    database, jobs, loader, runner = _setup(tmp_path)
    del database
    old_key = "live-gateio-btc-eth-1d-20261020T0000Z-v1"
    old_request = _request(old_key, days=90, datasets=(DAILY, ETH_DAILY))
    old, _ = jobs.create(old_request)
    worker = HistoricalRefreshWorker(
        jobs, runner, clock=lambda: NOW, daily_dataset_ids=(DAILY, ETH_DAILY),
    )
    daily = await worker.tick_daily()
    assert daily.status == "completed"
    assert daily.window_end_at == datetime(2026, 10, 20, tzinfo=UTC)
    assert daily.window_end_at - daily.window_start_at == timedelta(days=370)
    assert daily.job_id != old.job_id
    assert daily.idempotency_key != old_key
    assert DAILY in daily.idempotency_key and ETH_DAILY in daily.idempotency_key
    assert "20251015T0000Z" in daily.idempotency_key
    assert "20261020T0000Z" in daily.idempotency_key
    unchanged = jobs.get(old.job_id)
    assert unchanged.idempotency_key == old_key
    assert unchanged.request_hash == old.request_hash
    assert unchanged.window_start_at == old_request.window_start_at
    assert unchanged.status == "queued" and unchanged.attempt == 0
    assert len(loader.calls) == 2
    assert all(end - start == timedelta(days=370) for _, start, end in loader.calls)
    # Dataset order is not part of the new identity's meaning.
    reordered = HistoricalRefreshWorker(
        jobs, runner, clock=lambda: NOW, daily_dataset_ids=(ETH_DAILY, DAILY),
    )
    repeated = await reordered.tick_daily()
    assert repeated.status == "already_complete"
    assert repeated.job_id == daily.job_id
    assert len(loader.calls) == 2
