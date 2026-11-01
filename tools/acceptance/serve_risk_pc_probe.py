"""PC candidate: live risk APIs plus read-only access to existing product views.

Usage: python -m tools.acceptance.serve_risk_pc_probe --data-root ... --port 8124
Uses the same risk services and scheduler as the product. It never proxies writes
to the formal API and never submits research. Stop the temporary process after QA.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

from apps.hub_api.risk_routes import risk_router
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
    RiskComparisonV1,
    RiskConditionV1,
    RiskMetricV1,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.research.raw_capture import RawBodyArchive
from packages.source_adapters.registry import SourceRegistry


def make_app(root: Path) -> FastAPI:
    database = Database(f"sqlite+pysqlite:///{root / 'live.sqlite3'}")
    database.create_all()
    data = MarketRiskDataService(
        database, RawBodyArchive(root / "raw-captures"), root / "features", enabled=True
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
    scheduler = RealtimeScheduler(
        SourceIngestionService(database, SourceRegistry()),
        risk_data=data,
        risk_monitor=monitors,
        notifications=NotificationDispatcher(
            database, {"local": LocalNotificationAdapter(root / "pc-notifications.jsonl")}
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(scheduler.run_forever(60))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            database.engine.dispose()

    app = FastAPI(lifespan=lifespan)

    def owner(value):
        if value != "owner":
            raise HTTPException(403, "owner_required")
        return value

    app.include_router(risk_router(database, root, owner, enabled=True))

    @app.get("/health/ready")
    def health():
        return {"status": "ready", "environment": "isolated_pc_candidate", "formal_install": False}

    @app.get("/v1/{path:path}")
    async def existing_read_views(path: str, request: Request):
        if path.startswith("risk/"):
            raise HTTPException(404, "risk_object_not_found")
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            reply = await client.get(
                "http://127.0.0.1:8047/v1/" + path, params=request.query_params
            )
        return Response(
            reply.content,
            status_code=reply.status_code,
            media_type=reply.headers.get("content-type", "application/json"),
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8124)
    args = parser.parse_args()
    uvicorn.run(
        make_app(args.data_root.resolve()), host="127.0.0.1", port=args.port, access_log=False
    )


if __name__ == "__main__":
    main()
