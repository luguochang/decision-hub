from __future__ import annotations

import argparse
import asyncio
import os
import time

from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.market.okx import OKXPublicMarketAdapter
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one scheduler tick")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    database = Database()
    database.initialize()
    registry = SourceRegistry()
    if os.getenv("DECISION_HUB_SOURCES_ENABLED", "0") == "1":
        for source in official_source_presets():
            registry.register(source)
    ingestion = SourceIngestionService(database, registry, admission=AdmissionService(database))
    analyzer = AnalyzeTextService(database, LangGraphAgentRuntime())

    async def execute_target(target: RunTarget) -> None:
        await analyzer.run_admitted(target.event_id, target.run_id)

    async def tick() -> int:
        export_dir = os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")
        from pathlib import Path

        from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
        from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService

        dispatcher = NotificationDispatcher(
            database,
            {
                "local": LocalNotificationAdapter(
                    Path(export_dir) / "exports" / "notifications.jsonl"
                )
            },
        )
        scheduler = RealtimeScheduler(
            ingestion,
            run_executor=execute_target,
            outcomes=(
                DueOutcomeService(database, OKXPublicMarketAdapter())
                if os.getenv("DECISION_HUB_MARKET_ENABLED", "0") == "1"
                else None
            ),
            notifications=dispatcher,
        )
        report = await scheduler.tick()
        return len(report.polled) + report.outcomes_processed + report.notifications_delivered

    while True:
        drained = asyncio.run(tick())
        if args.once:
            print(f"hub-worker completed {drained} scheduler actions")
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    run()
