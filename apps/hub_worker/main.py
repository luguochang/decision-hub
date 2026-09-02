from __future__ import annotations

import argparse
import asyncio
import json
import os

from apps.hub_worker.composition import (
    build_evolution_worker,
    build_realtime_worker,
    build_research_worker,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import PilotSettings, build_readiness_service
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def _registry(settings: PilotSettings) -> SourceRegistry:
    registry = SourceRegistry()
    if settings.sources_enabled:
        include_calendar = os.getenv(
            "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        for source in official_source_presets(include_calendar=include_calendar):
            registry.register(source)
    return registry


def _readiness_report(
    database: Database,
    registry: SourceRegistry,
    settings: PilotSettings,
):
    return build_readiness_service(
        database,
        registry.manifests(),
        settings=settings,
    ).report()


async def _run_loop(
    *,
    database: Database,
    registry: SourceRegistry,
    settings: PilotSettings,
    role: str,
    interval: float,
    once: bool,
) -> int:
    if role == "realtime":
        realtime = build_realtime_worker(
            database,
            registry=registry,
            settings=settings,
            heartbeat_interval_seconds=max(1.0, interval),
        )
        while True:
            result = await realtime.tick()
            if once:
                actions = (
                    len(result.polled)
                    + result.runs_started
                    + result.outcomes_processed
                    + result.notifications_delivered
                )
                print(f"hub-worker realtime completed {actions} scheduler actions")
                return 0
            await asyncio.sleep(interval)
    if role == "research":
        research = build_research_worker(
            database,
            heartbeat_interval_seconds=max(1.0, interval),
        )
        try:
            while True:
                result = await research.tick()
                if once:
                    status = result.status if result is not None else "no_run"
                    print(f"hub-worker research completed status={status}")
                    return 0 if status != "failed" else 1
                await asyncio.sleep(interval)
        finally:
            await research.close()
    evolution = build_evolution_worker(
        database,
        heartbeat_interval_seconds=max(1.0, interval),
    )
    while True:
        result = await evolution.tick()
        if once:
            status = result.status if result is not None else "no_job"
            print(f"hub-worker evolution completed status={status}")
            return 0
        await asyncio.sleep(interval)


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=("realtime", "research", "evolution"),
        default="realtime",
        help="select the durable worker composition root",
    )
    parser.add_argument("--once", action="store_true", help="run one worker tick")
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
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    database = Database()
    database.initialize()
    settings = PilotSettings.from_env()
    registry = _registry(settings)
    if args.preflight or args.pilot:
        try:
            report = _readiness_report(database, registry, settings)
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
    return asyncio.run(
        _run_loop(
            database=database,
            registry=registry,
            settings=settings,
            role=args.role,
            interval=args.interval,
            once=args.once,
        )
    )


if __name__ == "__main__":
    raise SystemExit(run())
