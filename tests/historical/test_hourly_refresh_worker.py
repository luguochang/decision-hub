from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.hub_worker.historical import HistoricalRefreshWorker
from apps.hub_worker.main import _historical_sleep_seconds
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.historical import (
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    HistoricalDatasetBatch,
)

NOW = datetime(2026, 2, 2, 4, 37, tzinfo=UTC)


def test_historical_sleep_aligns_to_next_completed_utc_hour() -> None:
    now = datetime(2026, 2, 2, 4, 37, 12, tzinfo=UTC)

    assert _historical_sleep_seconds(3600, now=now) == pytest.approx(1373.0)


def test_historical_sleep_preserves_non_hourly_verification_cadence() -> None:
    assert _historical_sleep_seconds(5.0, now=NOW) == 5.0


class _Loader:
    def __init__(self) -> None:
        self.calls = 0

    async def load(
        self, dataset_id: str, start: datetime, end: datetime
    ) -> HistoricalDatasetBatch:
        self.calls += 1
        rows = [
            {
                "timestamp": (start + timedelta(hours=offset)).isoformat(),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume_base": "1",
                "volume_currency": "100",
                "volume_quote": "100",
                "confirmed": True,
            }
            for offset in range(int((end - start).total_seconds() // 3600))
        ]
        return HistoricalDatasetBatch(
            dataset_id=dataset_id,
            source_id="okx-public",
            rows=rows,
            unit="usdt_and_btc",
            frequency="1h",
            instrument="BTC-USDT",
            venue="okx",
            contract_type="spot",
            publication_time_precision="millisecond",
            revision_policy="immutable",
            gaps=[],
        )


def _worker(tmp_path: Path) -> tuple[HistoricalRefreshWorker, _Loader, DataJobService]:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'history.sqlite3'}")
    database.create_all()
    jobs = DataJobService(database, clock=lambda: NOW)
    loader = _Loader()
    runner = HistoricalDataJobRunner(
        jobs,
        HistoricalDataArchive(tmp_path / "assets"),
        {"crypto.btc_usdt.ohlcv_1h": loader},
        clock=lambda: NOW,
    )
    return HistoricalRefreshWorker(jobs, runner, clock=lambda: NOW), loader, jobs


@pytest.mark.asyncio
async def test_hourly_refresh_uses_completed_hour_and_is_idempotent(tmp_path: Path) -> None:
    worker, loader, jobs = _worker(tmp_path)

    first = await worker.tick()
    second = await worker.tick()

    assert first.status == "completed"
    assert second.status == "already_complete"
    assert first.window_end_at == datetime(2026, 2, 2, 4, tzinfo=UTC)
    assert first.window_start_at == datetime(2025, 11, 4, 4, tzinfo=UTC)
    assert first.idempotency_key == "live-okx-btc-1h-20260202T0400Z-v1"
    assert loader.calls == 1
    assert len(jobs.list()) == 1


@pytest.mark.asyncio
async def test_hourly_refresh_reports_partial_data_without_research_side_effect(
    tmp_path: Path,
) -> None:
    worker, loader, jobs = _worker(tmp_path)

    # A loader returning no rows is a real, visible data failure.  The trigger
    # must not turn it into a successful refresh or create a Research Run.
    class _EmptyLoader:
        async def load(
            self, dataset_id: str, start: datetime, end: datetime
        ) -> HistoricalDatasetBatch:
            del start, end
            return HistoricalDatasetBatch(
                dataset_id=dataset_id,
                source_id="okx-public",
                rows=[],
                unit="usdt_and_btc",
                frequency="1h",
                instrument="BTC-USDT",
                venue="okx",
                contract_type="spot",
                publication_time_precision="millisecond",
                revision_policy="immutable",
                gaps=["provider_empty"],
            )

    worker.runner.loaders["crypto.btc_usdt.ohlcv_1h"] = _EmptyLoader()
    result = await worker.tick()

    assert result.status == "failed"
    assert result.error == "crypto.btc_usdt.ohlcv_1h:provider_empty"
    assert loader.calls == 0
    assert jobs.list()[0].status == "failed"
    assert jobs.list()[0].partition_refs == []


@pytest.mark.asyncio
async def test_daily_refresh_uses_previous_closed_utc_day_for_strategy_history(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'daily.sqlite3'}")
    database.create_all()
    jobs = DataJobService(database, clock=lambda: NOW)

    class _DailyLoader:
        async def load(
            self, dataset_id: str, start: datetime, end: datetime
        ) -> HistoricalDatasetBatch:
            rows = [
                {
                    "timestamp": (start + timedelta(days=offset)).isoformat(),
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100",
                    "volume_base": "1",
                    "confirmed": True,
                }
                for offset in range((end - start).days)
            ]
            return HistoricalDatasetBatch(
                dataset_id=dataset_id,
                source_id="okx-public",
                rows=rows,
                unit="usdt_and_btc",
                frequency="1d",
                instrument="BTC-USDT",
                venue="okx",
                contract_type="spot",
                publication_time_precision="millisecond",
                revision_policy="immutable",
                gaps=[],
            )

    runner = HistoricalDataJobRunner(
        jobs,
        HistoricalDataArchive(tmp_path / "assets"),
        {"crypto.btc_usdt.ohlcv_1d": _DailyLoader()},
        clock=lambda: NOW,
    )
    worker = HistoricalRefreshWorker(jobs, runner, clock=lambda: NOW)

    result = await worker.tick_daily()

    assert result.status == "completed"
    assert result.window_end_at == datetime(2026, 2, 2, tzinfo=UTC)
    assert result.window_start_at == datetime(2025, 1, 28, tzinfo=UTC)
    assert result.idempotency_key == (
        "live-history-crypto.btc_usdt.ohlcv_1d-20250128T0000Z-20260202T0000Z-v2"
    )
    assert len(jobs.list()) == 1
