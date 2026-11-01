"""O04: one hundred local projections over real stored capture-backed market paths."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.market_risk import MarketRiskQuery


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    database = Database(f"sqlite+pysqlite:///{args.data_root / 'live.sqlite3'}")
    query = MarketRiskQuery(database, args.data_root, enabled=True)
    latencies, observed, failures = [], {}, []
    try:
        for index in range(100):
            scope_id = tuple(query.scopes)[index % 2]
            started = time.perf_counter()
            try:
                result = query.summary(scope_id)
                # Serialization is part of projection cost, including strict DTO validation.
                result.model_dump_json()
                if result.quote is None or result.window is None:
                    raise ValueError("real_capture_projection_missing")
                observed[scope_id] = result.model_dump(mode="json")
            except Exception as exc:
                failures.append({"index": index, "error": type(exc).__name__, "message": str(exc)})
            finally:
                latencies.append(time.perf_counter() - started)
    finally:
        database.engine.dispose()
    latencies.sort()
    p95, p99 = latencies[94], latencies[98]
    receipt = {
        "schema_version": "market-risk-query-probe.v1",
        "at": datetime.now(UTC).isoformat(),
        "environment": "isolated_real_capture_backed_store",
        "attempts": 100,
        "network_calls": 0,
        "model_calls": 0,
        "failures": failures,
        "p95_seconds": p95,
        "p99_seconds": p99,
        "passed": not failures and p95 <= 0.5,
        "scopes": observed,
        "data_root": str(args.data_root.resolve()),
        "formal_api_latency_claim": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: receipt[key]
                for key in ("attempts", "p95_seconds", "p99_seconds", "passed", "failures")
            },
            ensure_ascii=False,
        )
    )
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
