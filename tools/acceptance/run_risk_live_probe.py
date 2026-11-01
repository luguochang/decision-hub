"""Finite public-data observation. Run as python -m tools.acceptance.run_risk_live_probe.

Uses an isolated DB and local notification export. Never calls a model or sends email.
Actual wall-clock slots are not simulated; interruption remains a failed/partial receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
    RiskComparisonV1,
    RiskConditionV1,
    RiskMetricV1,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive


async def run(output: Path, slots: int) -> int:
    output.mkdir(parents=True, exist_ok=True)
    database = Database(f"sqlite+pysqlite:///{output / 'live.sqlite3'}")
    database.create_all()
    data = MarketRiskDataService(
        database,
        RawBodyArchive(output / "raw-captures"),
        output / "features",
        enabled=True,
    )
    monitors = RiskMonitorService(database, data)
    for scope in data.scopes.values():
        monitors.ensure_plan(
            RiskConditionV1(
                condition_id=f"{scope.instrument.lower()}-spot-move30.v1",
                scope_id=scope.scope_id,
                expression=RiskComparisonV1(
                    left=RiskMetricV1(),
                    right=Decimal(".015" if scope.instrument == "BTC-USDT" else ".02"),
                ),
            )
        )
    notifications = NotificationDispatcher(
        database, {"local": LocalNotificationAdapter(output / "notifications.jsonl")}
    )
    rows_path = output / "slots.jsonl"
    rows = (
        [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
        if rows_path.exists()
        else []
    )
    if len(rows) >= slots:
        raise ValueError("probe_already_has_requested_slots")
    started_at = datetime.fromisoformat(rows[0]["started_at"]) if rows else datetime.now(UTC)
    next_slot_at = (
        datetime.fromisoformat(rows[-1]["started_at"]).timestamp() + 60 if rows else time.time()
    )
    origin = time.monotonic() + max(0, next_slot_at - time.time())
    initial_count = len(rows)
    print(
        json.dumps({"status": "started", "slots": slots, "output": str(output.resolve())}),
        flush=True,
    )
    try:
        for index in range(initial_count, slots):
            await asyncio.sleep(max(0, origin + (index - initial_count) * 60 - time.monotonic()))
            slot_start, tick = time.monotonic(), datetime.now(UTC)
            acquired = await data.refresh_due()
            eval_start = time.monotonic()
            evaluated = await monitors.process_due()
            eval_seconds = time.monotonic() - eval_start
            as_of = datetime.now(UTC)
            samples = [await data.snapshot(scope, as_of=as_of) for scope in data.scopes]
            delivered = await notifications.dispatch_once()
            row = {
                "slot": index,
                "started_at": tick.isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "acquired_scopes": acquired,
                "evaluated_plans": evaluated,
                "evaluation_seconds": eval_seconds,
                "cycle_seconds": time.monotonic() - slot_start,
                "local_provider_accepted": delivered,
                "data": [
                    {
                        "scope_id": s.scope_id,
                        "state": s.data_state,
                        "reason": s.reason,
                        "age_seconds": (as_of - s.data_as_of).total_seconds()
                        if s.data_as_of
                        else None,
                        "input_ref": s.input_ref,
                        "input_hash": s.input_hash,
                    }
                    for s in samples
                ],
            }
            rows.append(row)
            with (output / "slots.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            if index == 0 or (index + 1) % 20 == 0:
                print(
                    json.dumps(
                        {
                            "slots_completed": index + 1,
                            "fresh": all(s.data_state == "fresh" for s in samples),
                        }
                    ),
                    flush=True,
                )
    finally:
        qualified = [
            row
            for row in rows
            if all(
                sample["state"] == "fresh"
                and sample["age_seconds"] is not None
                and sample["age_seconds"] <= 120
                for sample in row["data"]
            )
        ]
        latencies = sorted(row["evaluation_seconds"] for row in rows)
        result = {
            "schema_version": "market-risk-live-probe.v1",
            "environment": "isolated_real_public_http",
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "requested_slots": slots,
            "completed_slots": len(rows),
            "interval_seconds": 60,
            "o05_qualified_slots": len(qualified),
            "actual_restart_receipt": (output / "restart.json").exists(),
            "o05_pass": slots >= 10
            and len(rows) == slots
            and len(qualified) == slots
            and (output / "restart.json").exists(),
            "evaluation_p95_seconds": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
            if latencies
            else None,
            "evaluation_median_seconds": statistics.median(latencies) if latencies else None,
            "formal_worker_acceptance": False,
            "model_calls": 0,
            "forecast_gain_claim": False,
            "rows_ref": str((output / "slots.jsonl").resolve()),
        }
        (output / "receipt.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        database.engine.dispose()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["o05_pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slots", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.slots <= 60:
        parser.error("slots must be between 1 and 60; O05 requires ten actual slots and restart")
    return asyncio.run(run(args.output, args.slots))


if __name__ == "__main__":
    raise SystemExit(main())
