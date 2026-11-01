"""Finite F2/O02 storage probe with explicit synthetic inputs, never a forecast test."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    RiskAlertRecord,
    RiskConditionEvaluationRecord,
)
from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
    RiskComparisonV1,
    RiskConditionV1,
    RiskMetricV1,
    RiskMonitorInput,
)


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


async def run(database: Database) -> dict[str, object]:
    scope = "crypto:spot:okx:BTC-USDT:trade:1m"
    clock = [datetime.now(UTC)]

    class SyntheticSnapshots:
        async def snapshot(self, scope_id: str, *, as_of: datetime) -> RiskMonitorInput:
            return RiskMonitorInput(
                scope_id=scope_id,
                as_of=as_of,
                data_as_of=as_of,
                window_seconds=1800,
                close_return=Decimal("0.001"),
                data_state="fresh",
                input_ref="fixture://storage-performance-only",
                input_hash="a" * 64,
                manifest_hash="b" * 64,
            )

    service = RiskMonitorService(database, SyntheticSnapshots(), clock=lambda: clock[0])
    for i in range(100):
        service.ensure_plan(
            RiskConditionV1(
                condition_id=f"synthetic-load-{i}.v1",
                scope_id=scope,
                expression=RiskComparisonV1(left=RiskMetricV1(), right=Decimal("0.015")),
            )
        )
    timings, failures, counts = [], [], []
    for i in range(30):
        started = time.perf_counter()
        try:
            counts.append(await service.process_due(limit=100))
        except Exception as exc:
            failures.append({"tick": i, "error_type": type(exc).__name__})
        timings.append(time.perf_counter() - started)
        clock[0] += timedelta(seconds=60)
    with database.session() as session:
        evaluations = session.query(RiskConditionEvaluationRecord).count()
        alerts = session.query(RiskAlertRecord).count()
    p95, p99 = quantile(timings, 0.95), quantile(timings, 0.99)
    return {
        "scope": "candidate isolated synthetic DB load; no network, model, PC or market claims",
        "plans": 100,
        "ticks": 30,
        "simulation_clock": True,
        "elapsed_measurements": timings,
        "processed_counts": counts,
        "failures": failures,
        "evaluation_count": evaluations,
        "alert_count": alerts,
        "p95_seconds": p95,
        "p99_seconds": p99,
        "pass": not failures
        and counts == [100] * 30
        and evaluations == 3000
        and alerts == 0
        and p95 <= 5
        and p99 <= 15,
        "O05_real_60s_slots": "not_run",
        "formal_runtime": "not_upgraded",
    }


def main() -> int:
    folder = (
        Path(__file__).resolve().parents[2] / "artifacts/market-risk-implementation-20261007/B0"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("F2-load-" + uuid4().hex[:8] + ".sqlite3")
    database = Database(f"sqlite+pysqlite:///{path}")
    try:
        database.create_all()
        receipt = asyncio.run(run(database))
    finally:
        database.engine.dispose()
    output = folder / "F2-load01.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in receipt.items()
                if key not in ("elapsed_measurements", "processed_counts")
            },
            ensure_ascii=False,
        )
    )
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
