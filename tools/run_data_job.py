from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import DataJobCreate
from packages.kernel.decision_hub_kernel.application.data_jobs import DataJobService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.historical import (
    FredHistoricalDataLoader,
    HistoricalDataArchive,
    HistoricalDataJobRunner,
    OKXHistoricalDataLoader,
)

DATASET_IDS = [
    "crypto.btc_usdt.ohlcv_1h",
    "crypto.btc_usdt_swap.funding_rate",
    "crypto.btc_usdt_swap.open_interest",
    "macro.fred.dgs2",
    "macro.fred.dgs10",
    "macro.fred.dtwexbgs",
]


async def main() -> int:
    parser = argparse.ArgumentParser(description="Create and run one owner historical DataJob.")
    parser.add_argument("--idempotency-key", default="core-data-last-3-complete-months-v1")
    parser.add_argument(
        "--data-dir", default=os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")
    )
    parser.add_argument("--create-only", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    database = Database(f"sqlite+pysqlite:///{data_dir / 'db' / 'decision_hub.sqlite3'}")
    database.initialize()
    end = _month_start(datetime.now(UTC))
    start = _shift_months(end, -3)
    jobs = DataJobService(database)
    job, created = jobs.create(
        DataJobCreate.model_validate(
            {
                "idempotency_key": args.idempotency_key,
                "requested_by": "owner",
                "dataset_ids": DATASET_IDS,
                "window_start_at": start,
                "window_end_at": end,
            }
        )
    )
    if args.create_only:
        print(
            json.dumps(
                {"created": created, "job": job.model_dump(mode="json")},
                ensure_ascii=False,
            )
        )
        database.engine.dispose()
        return 0

    okx = OKXHistoricalDataLoader()
    fred = FredHistoricalDataLoader()
    loaders = {
        dataset_id: (fred if dataset_id.startswith("macro.fred") else okx)
        for dataset_id in DATASET_IDS
    }
    result = await HistoricalDataJobRunner(
        jobs,
        HistoricalDataArchive(data_dir / "historical"),
        loaders,
    ).run(job.job_id)
    print(
        json.dumps(
            {"created": created, "job": result.model_dump(mode="json")},
            ensure_ascii=False,
        )
    )
    database.engine.dispose()
    return 0 if result.status in {"completed", "partial"} else 1


def _month_start(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(value: datetime, amount: int) -> datetime:
    index = value.year * 12 + value.month - 1 + amount
    return value.replace(year=index // 12, month=index % 12 + 1)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
