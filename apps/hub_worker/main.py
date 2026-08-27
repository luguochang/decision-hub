from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.scheduler import RealtimeScheduler
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    RunTarget,
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import (
    PilotSettings,
    build_notification_adapters,
    build_readiness_service,
)
from packages.provider_adapters.market.okx import OKXPublicMarketAdapter
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def _readiness_report(database: Database, registry: SourceRegistry):
    settings = PilotSettings.from_env()
    return build_readiness_service(
        database,
        registry.manifests(),
        settings=settings,
    ).report()


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one scheduler tick")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="print the redacted pilot readiness report and exit",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="require readiness to pass before starting the long-running worker",
    )
    args = parser.parse_args()
    database = Database()
    database.initialize()
    registry = SourceRegistry()
    if os.getenv("DECISION_HUB_SOURCES_ENABLED", "0") == "1":
        for source in official_source_presets():
            registry.register(source)
    if args.preflight or args.pilot:
        try:
            report = _readiness_report(database, registry)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "not_ready",
                        "error_code": "configuration_invalid",
                        "detail": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        print(report.model_dump_json(indent=2))
        if args.preflight or report.status != "ready":
            return 0 if report.status == "ready" else 1
    ingestion = SourceIngestionService(database, registry, admission=AdmissionService(database))
    analyzer = AnalyzeTextService(database, LangGraphAgentRuntime())

    async def execute_target(target: RunTarget) -> None:
        await analyzer.run_admitted(target.event_id, target.run_id)

    async def tick() -> int:
        export_dir = os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")
        from packages.kernel.decision_hub_kernel.application.outbox import NotificationDispatcher
        from packages.kernel.decision_hub_kernel.application.outcome_due import DueOutcomeService

        settings = PilotSettings.from_env()
        dispatcher = NotificationDispatcher(
            database,
            build_notification_adapters(settings, Path(export_dir)),
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
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(run())
