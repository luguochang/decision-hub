"""Create one idempotent daily data refresh with original HTTP receipts; no model calls."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import DataJobCreate
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.historical import HistoricalDataArchive, HistoricalDataJobRunner
from packages.provider_adapters.historical.gate_daily import GateDailyDataLoader


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    args = parser.parse_args()
    path = args.data_root / 'db' / 'decision_hub.sqlite3'
    if not path.is_file():
        parser.error('existing database required')
    db = Database(f'sqlite+pysqlite:///{path.resolve().as_posix()}')
    try:
        end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        jobs = DataJobService(db)
        job, created = jobs.create(DataJobCreate(
            idempotency_key=f'original-daily-v1:{end.date().isoformat()}',
            requested_by='owner', dataset_ids=['crypto.btc_usdt.ohlcv_1d'],
            window_start_at=end - timedelta(days=90), window_end_at=end,
        ))
        # An existing in-flight or completed run is observed, never replaced.
        if created:
            job = await HistoricalDataJobRunner(jobs, HistoricalDataArchive(
                args.data_root / 'historical'), {
                    'crypto.btc_usdt.ohlcv_1d': GateDailyDataLoader(),
                }).run(job.job_id)
        print(json.dumps({'created': created, 'job': job.model_dump(mode='json')}))
        return 0 if job.status == 'completed' else 1
    finally:
        db.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
