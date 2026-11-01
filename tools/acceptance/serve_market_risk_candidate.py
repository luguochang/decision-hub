"""PC layout acceptance on a private DB and real public prices, without model execution.

This candidate preview is not normal release/DSH acceptance. It never loads the
formal database or credentials; the standard installed product keeps running.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "artifacts/market-risk-implementation-20261007/B2/pc-candidate01/data"
DATA.mkdir(parents=True, exist_ok=True)
for key, value in {
    "DECISION_HUB_DATA_DIR": str(DATA),
    "DECISION_HUB_DATABASE_URL": f"sqlite+pysqlite:///{DATA / 'candidate.sqlite3'}",
    "DECISION_HUB_DB_URL": f"sqlite+pysqlite:///{DATA / 'candidate.sqlite3'}",
    "DECISION_HUB_RISK_ENABLED": "1", "DECISION_HUB_SPOT_RESEARCH_ENABLED": "0",
    "DECISION_HUB_LLM_ENABLED": "0", "DECISION_HUB_SOURCES_ENABLED": "0",
    "DECISION_HUB_MARKET_ENABLED": "0", "DECISION_HUB_RETAINED_RECIPE_RECEIPT": "",
    "DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256": "",
}.items():
    os.environ[key] = value

import uvicorn

from apps.hub_api.main import create_app
from apps.hub_worker.market_risk_loops import PathShadowTick
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.risk_monitor import RiskMonitorService
from packages.kernel.decision_hub_kernel.application.risk_path_ledger import RiskPathLedger
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.risk_monitor import (
    RiskComparisonV1, RiskConditionV1, RiskMetricV1,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive

database = Database(os.environ["DECISION_HUB_DATABASE_URL"])
database.initialize()
data = MarketRiskDataService(database, RawBodyArchive(DATA / "raw-captures"),
                             DATA / "features", enabled=True)
monitor = RiskMonitorService(database, data)
for scope in data.scopes.values():
    monitor.ensure_plan(RiskConditionV1(
        condition_id=f"{scope.instrument.lower()}-spot-move30.v1", scope_id=scope.scope_id,
        expression=RiskComparisonV1(left=RiskMetricV1(),
                                    right=Decimal("0.015" if scope.instrument == "BTC-USDT"
                                                  else "0.02")),
    ))
shadow = PathShadowTick(RiskPathLedger(database, data.store), owner_id="pc-candidate")
app = create_app(database)
original_lifespan = app.router.lifespan_context


async def capture():
    while True:
        try:
            acquired = await data.refresh_due()
            evaluated = await monitor.process_due()
            print(json.dumps({"schema_version": "pc-candidate-capture.v1",
                              "at": datetime.now(UTC).isoformat(), "acquired": acquired,
                              "evaluated": evaluated, "model_submissions": 0}), flush=True)
        except Exception as error:
            print(json.dumps({"capture_error": type(error).__name__, "detail": str(error)[:160]}),
                  flush=True)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    async with original_lifespan(app):
        task = asyncio.create_task(capture())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


app.router.lifespan_context = lifespan
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8134, log_level="warning")
