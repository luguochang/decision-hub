from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from apps.hub_worker.cancelled_sessions import (
    cancelled_session_recovery,
    cancelled_session_recovery_time_budget,
    recover_cancelled_sessions_once,
    run_cancelled_session_recovery,
)
from apps.hub_worker.composition import (
    build_document_worker,
    build_evolution_worker,
    build_historical_worker,
    build_history_observed_worker,
    build_realtime_worker,
    build_research_worker,
)
from apps.hub_worker.documents import DocumentPipelineWorker
from apps.hub_worker.dsh_activity import build_dsh_activity_collector
from apps.hub_worker.heartbeat import keep_heartbeat
from apps.hub_worker.installed_harness_bootstrap import InstalledHarnessBootstrapRequest
from apps.hub_worker.usage_collection import UsageCollector
from apps.hub_worker.work_admission import HeavyWorkAdmission
from packages.kernel.decision_hub_kernel.application.live_observation import ServiceHeartbeatService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import PilotSettings, build_readiness_service
from packages.runtime_adapters.development_runtime import development_runtime_from_environment
from packages.source_adapters.official_feeds import official_source_presets
from packages.source_adapters.registry import SourceRegistry


def _configure_retained_recipe_registry_from_normal_launch() -> None:
    """Consume the normal launcher's guarded release identity before DB startup.

    This process-local input grants historical reads only. It is independent
    of optional method comparison configuration and never changes a Run.
    """
    receipt_name = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", "")
    receipt_sha256 = os.getenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", "")
    if not receipt_name and not receipt_sha256:
        return
    if not receipt_name or not receipt_sha256:
        raise ValueError("research_history_startup_receipt_pair_required")
    import hashlib

    from packages.kernel.decision_hub_kernel.application.product_release_provenance import (
        resolve_product_receipt,
    )
    from packages.kernel.decision_hub_kernel.application.research_graph_history_versions import (
        _managed_read,
        configure_retained_recipe_registry_from_validated_release,
    )

    try:
        if (len(receipt_sha256) != 64
                or any(character not in "0123456789abcdef" for character in receipt_sha256)):
            raise ValueError("invalid receipt identity")
        receipt_path = Path(receipt_name)
        if not receipt_path.is_absolute():
            raise ValueError("invalid receipt path")
        root = Path(__file__).resolve().parents[2]
        relative = receipt_path.absolute().relative_to(root).as_posix()
        raw = _managed_read(relative)
        if hashlib.sha256(raw).hexdigest() != receipt_sha256:
            raise ValueError("changed receipt")
        receipt = dict(resolve_product_receipt(
            raw, expected_root=root, read_resource=_managed_read,
            expected_sha256=receipt_sha256,
        ).value)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        raise ValueError("research_history_startup_receipt_invalid") from error
    # A valid release made before automatic retention keeps the three manually
    # registered readers. Missing archive evidence is never synthesized.
    if "retained_recipe_archives_v1" not in receipt["evidence"]:
        return
    configure_retained_recipe_registry_from_validated_release(
        receipt_path=receipt_path, receipt_sha256=receipt_sha256,
    )
    if os.getenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "0") == "1":
        from packages.kernel.decision_hub_kernel.application.research_graph_recipe_v3 import (
            configure_graph_recipe_v3_from_validated_release,
        )
        configure_graph_recipe_v3_from_validated_release(
            receipt_path=receipt_path, receipt_sha256=receipt_sha256,
        )


def _registry(settings: PilotSettings) -> SourceRegistry:
    registry = SourceRegistry()
    if settings.sources_enabled:
        include_calendar = os.getenv(
            "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        for source in official_source_presets(include_calendar=include_calendar):
            registry.register(source)
    return registry


def _research_heartbeat_interval(loop_interval: float) -> float:
    """Keep health cadence independent from a one-second claim loop.

    Research ticks can spend minutes inside DSH.  Using the claim poll
    interval as the heartbeat interval makes a healthy worker appear offline
    whenever a synchronous storage call briefly delays the background beat.
    The launcher can set a stable cadence while tests retain the old default.
    """

    configured = os.getenv("DECISION_HUB_HEARTBEAT_INTERVAL", "").strip()
    if not configured:
        return max(1.0, loop_interval)
    try:
        value = float(configured)
    except ValueError as exc:
        raise ValueError("heartbeat_interval_invalid") from exc
    if value <= 0:
        raise ValueError("heartbeat_interval_invalid")
    return value


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


async def _run_documents_loop(
    documents: DocumentPipelineWorker, *, once: bool, interval: float,
) -> int:
    while True:
        try:
            result = await documents.tick()
        except Exception as exc:
            # Processing failures are recorded on the claimed version. A
            # heartbeat or claim error may happen before any version is owned.
            # Keep the consumer available; --once still reports a failed tick.
            print(json.dumps({"role": "documents", "status": "tick_failed",
                              "error_code": f"document_processing_failed:{type(exc).__name__}"}))
            if once:
                return 1
        else:
            if once:
                print(f"hub-worker documents completed version={result or 'no_document'}")
                return 0
        await asyncio.sleep(interval)


def _research_once_outcome(status: str) -> tuple[int, str]:
    """Report a single call without treating unfinished research as completed."""
    if status in {"publish", "degraded", "research_only", "reject"}:
        return 0, "本次调用结束"
    if status == "no_run":
        return 0, "当前无待执行研究"
    if status == "failed":
        return 1, "本次执行失败"
    if status == "preparation_pending":
        return 2, "原研究尚未结束；等待状态已保存；等待恢复核对"
    if status == "preparation_storage_unavailable":
        return 1, "原研究尚未结束；等待状态保存未确认；原任务保持；需核对存储"
    if status == "maintenance_pending":
        return 2, "研究维护暂时等待数据库；可重试；尚未开始新研究"
    if status == "terminal_storage_pending":
        return 2, "原研究执行已停止；失败回执尚未落库；可重试保存；不表示研究完成"
    if status == "cancelled":
        return 0, "本次执行已取消；不表示研究完成"
    if status == "lease_lost":
        return 0, "本次执行控制已结束；租约已失效；不表示研究完成"
    return 1, "状态未确认；请核对原任务"


async def _run_loop(
    *,
    database: Database,
    registry: SourceRegistry,
    settings: PilotSettings,
    role: str,
    interval: float,
    once: bool,
    harness_install_request: InstalledHarnessBootstrapRequest | None = None,
    history_registration_id: str | None = None,
) -> int:
    development = development_runtime_from_environment(Path(__file__).resolve().parents[2])
    if development is not None and harness_install_request is not None:
        raise ValueError("development_release_features_forbidden")
    if role == "history-observed":
        if not history_registration_id:
            raise ValueError("history_observed_registration_id_required")
        commands = build_history_observed_worker(
            database, owner_id=f"history-observed:{os.getpid()}:{uuid4().hex}",
        )
        # Starting this role never registers/activates a fixed task set or fits.
        # Only the explicitly activated registration can produce HTTP attempts.
        while True:
            processed = await commands.process_one(history_registration_id)
            if once:
                status = commands.status(history_registration_id)
                print(json.dumps({
                    "role": role, "registration_id": status.registration_id,
                    "processed_one": processed, "state_counts": dict(status.state_counts),
                    "attempts": status.attempts, "metadata_only": True,
                    "ready_for_real_fit": False,
                }))
                return 0
            await asyncio.sleep(interval)
    if role == "realtime":
        realtime = build_realtime_worker(
            database,
            registry=registry,
            settings=settings,
            heartbeat_interval_seconds=_research_heartbeat_interval(interval),
        )
        from apps.hub_api.history_product_bootstrap41 import configured_history_product41
        from apps.hub_api.history_outcome_bootstrap41 import configured_history_outcome41
        from apps.hub_worker.history_hourly39 import install_history_hourly39_on_risk_lane
        from apps.hub_worker.history_hourly41 import install_history_hourly41_on_risk_lane
        from apps.hub_worker.history_outcome_runtime40 import install_history_outcome40_on_risk_lane

        base_enhancement_tick = realtime.scheduler.risk_enhancement_tick
        history_bootstrap41 = configured_history_product41(database)
        if history_bootstrap41 is None:
            install_history_hourly39_on_risk_lane(realtime, database)
            history_outcome_bootstrap = None
        else:
            install_history_hourly41_on_risk_lane(realtime, database, bootstrap=history_bootstrap41)
            history_outcome_bootstrap = configured_history_outcome41(database, history41=history_bootstrap41)
        if history_outcome_bootstrap is None and os.getenv("DECISION_HUB_HISTORY_OUTCOME_PRIVATE_CONFIG"):
            raise ValueError("history_outcome41_explicit_qualification_required")
        install_history_outcome40_on_risk_lane(realtime, bootstrap=history_outcome_bootstrap)
        if history_bootstrap41 is not None:
            from apps.hub_worker.history_cadence48 import install_history_cadence48_on_realtime

            install_history_cadence48_on_realtime(realtime, enhancement_tick=base_enhancement_tick)
        from apps.hub_worker.event_scenario_tick import install_event_scenario_on_realtime
        install_event_scenario_on_realtime(realtime, database)
        if not once:
            await realtime.run_forever(interval)
            return 0
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
        recovery_budget = cancelled_session_recovery_time_budget()
        heavy_work_admission = HeavyWorkAdmission()
        research = build_research_worker(
            database,
            heartbeat_interval_seconds=_research_heartbeat_interval(interval),
            harness_install_request=harness_install_request,
            heavy_work_admission=heavy_work_admission,
        )
        # Normal launch has already bound its guarded registry. The factory
        # checks migration and issuer qualification; no default issuer exists.
        from packages.kernel.decision_hub_kernel.application.research_publication_runtime48 import (
            create_managed_publication_bundle_service,
        )

        publication_data_dir = os.getenv("DECISION_HUB_PUBLICATION_DATA_DIR")
        if development is not None:
            publication_service = None
        elif publication_data_dir is None:
            publication_service = (
                create_managed_publication_bundle_service(
                    database=database, repository_root=Path(__file__).resolve().parents[2],
                ) if research.worker.min_priority is None else None
            )
        else:
            publication_service = (
                create_managed_publication_bundle_service(
                    database=database, repository_root=Path(__file__).resolve().parents[2],
                    publication_data_root=Path(publication_data_dir),
                ) if research.worker.min_priority is None else None
            )
        def confirmed_report_snapshot_in_session(session: Session, run_id: str):
            if publication_service is None:
                raise ValueError("development_formal_publication_unavailable")
            # Same caller snapshot; reject an external fence created during load.
            snapshot = publication_service.lifecycle._confirmed_report_snapshot(session, run_id)
            if publication_service.lifecycle.archive.fenced(run_id):
                raise ValueError("report_snapshot_commit_unconfirmed")
            return snapshot

        if publication_service is not None or development is not None:
            research.worker.review_schedule.bind_confirmed_report_reader(
                confirmed_report_snapshot_in_session,
            )
        from apps.hub_worker.research_skill_callbacks import ResearchSkillCallbackWorker
        callback_service = getattr(research.worker, "skill_callback_service", None)
        callback_worker = (
            ResearchSkillCallbackWorker(callback_service, heavy_work_admission)
            if callback_service is not None and research.worker.min_priority is None else None
        )
        callback_task = (
            asyncio.create_task(callback_worker.run_forever(interval), name="research-skill-callbacks")
            if callback_worker is not None else None
        )
        publication_stop = asyncio.Event()

        async def publication_loop() -> None:
            assert publication_service is not None
            while not publication_stop.is_set():
                async with heavy_work_admission.admitted():
                    # run_once drains the real native Future on cancellation before releasing admission.
                    await publication_service.execution.run_once(publication_service.worker.process_one)
                if not publication_stop.is_set():
                    try:
                        await asyncio.wait_for(publication_stop.wait(), timeout=interval)
                    except TimeoutError:
                        pass

        usage = UsageCollector(
            database, Path(os.getenv("DECISION_HUB_DSH_HOME", "data/dsh-live")) / "sessions"
        )
        usage_task = (
            asyncio.create_task(usage.run_forever(heavy_work_admission=heavy_work_admission))
            if research.worker.min_priority is None and not once else None
        )
        # The general lane maintains cancelled sessions independently, even
        # while a new research graph is running. Priority workers retain their
        # local projection path without duplicating maintenance Host requests.
        recovery = (
            cancelled_session_recovery(research.worker.runtime)
            if research.worker.min_priority is None else None
        )
        recovery_task = (
            asyncio.create_task(
                run_cancelled_session_recovery(recovery, time_budget_seconds=recovery_budget),
                name="research-cancel-recovery",
            ) if recovery is not None and not once else None
        )
        activity = (
            build_dsh_activity_collector(database, research.worker.runtime)
            if research.worker.min_priority is None else None
        )
        activity_task = (
            asyncio.create_task(activity.run_forever(), name="research-host-activity")
            if activity is not None and not once else None
        )
        publication_task = (
            asyncio.create_task(publication_loop(), name="research-publication-bundles")
            if publication_service is not None and not once else None
        )
        heartbeat_task = (
            asyncio.create_task(research._heartbeat_while_running())
            if research.worker.min_priority is None and not once else None
        )
        try:
            while True:
                if callback_task is not None and callback_task.done():
                    callback_task.result()
                if publication_task is not None and publication_task.done():
                    publication_task.result()
                result = await research.tick()
                if once:
                    if publication_service is not None:
                        await publication_service.execution.run_once(publication_service.worker.process_one)
                    if recovery is not None:
                        await recover_cancelled_sessions_once(
                            recovery, time_budget_seconds=recovery_budget,
                        )
                    if activity is not None:
                        try:
                            await activity.collect_once()
                        except Exception:
                            # Activity remains unknown; a maintenance fault
                            # cannot rewrite this research invocation's status.
                            pass
                    status = result.status if result is not None else "no_run"
                    exit_code, summary = _research_once_outcome(status)
                    print(f"hub-worker research status={status} {summary}")
                    return exit_code
                await asyncio.sleep(interval)
        finally:
            if callback_worker is not None:
                callback_worker.request_stop()
            if callback_task is not None:
                # Drain native preparation; old attempts remain writer-fenced.
                await asyncio.gather(callback_task, return_exceptions=True)
            publication_stop.set()
            if publication_service is not None:
                publication_service.execution.request_stop()
            # Cancellation requests stop; drain the actual native preparation
            # before closing its lifecycle connections.
            if publication_task is not None:
                await asyncio.gather(publication_task, return_exceptions=True)
            if publication_service is not None:
                publication_service.close()
            maintenance = [
                task for task in (usage_task, recovery_task, activity_task, heartbeat_task) if task is not None
            ]
            for task in maintenance:
                task.cancel()
            await asyncio.gather(*maintenance, return_exceptions=True)
            if activity is not None:
                await activity.close()
            await research.close()
    if role == "historical":
        historical = build_historical_worker(database)
        heartbeats = ServiceHeartbeatService(database)
        started_at = datetime.now(UTC)

        def beat_history():
            heartbeats.beat(
                service_id="hub-historical-worker",
                role="historical_worker",
                instance_id=f"historical:{os.getpid()}",
                version="0.1.0",
                mode="provider",
                interval_seconds=10,
                started_at=started_at,
            )

        async with keep_heartbeat(beat_history):
            while True:
                result = await historical.tick()
                daily_result = await historical.tick_daily()
                if once:
                    print(
                        json.dumps(
                            {"hourly": result.as_dict(), "daily": daily_result.as_dict()},
                            ensure_ascii=False,
                        )
                    )
                    return 0 if result.status != "failed" and daily_result.status != "failed" else 1
                await asyncio.sleep(_historical_sleep_seconds(interval))
    if role == "documents":
        documents = build_document_worker(database)
        return await _run_documents_loop(documents, once=once, interval=interval)
    if role == "prospective":
        from apps.hub_worker.prospective import build_prospective_worker

        worker = build_prospective_worker(
            database, Path(os.getenv("DECISION_HUB_DATA_DIR", "data/decision-hub")) / "evolution",
            os.getenv("DECISION_HUB_PROSPECTIVE_TRIAL_ID", ""),
        )
        heartbeats = ServiceHeartbeatService(database)
        started_at = datetime.now(UTC)

        def beat_comparison():
            heartbeats.beat(
                service_id=f"hub-prospective:{worker.trial_id}", role="evolution_worker",
                instance_id=f"prospective:{os.getpid()}", version="prospective-worker.v1",
                mode="provider", interval_seconds=10, started_at=started_at,
            )

        async with keep_heartbeat(beat_comparison):
            async def collect_labels():
                labels = worker.labels
                if labels is None:
                    return
                while True:
                    await labels.process_due(worker.trial_id, limit=1)
                    await asyncio.sleep(10)

            labels_task = (asyncio.create_task(collect_labels())
                           if worker.labels is not None and not once else None)
            try:
                while True:
                    if labels_task and labels_task.done():
                        # Do not silently keep running after a collector crash.
                        labels_task.result()
                    result = await worker.tick()
                    if once:
                        if worker.labels is not None:
                            result["labels_completed"] = await worker.labels.process_due(
                                worker.trial_id, limit=1,
                            )
                        print(json.dumps(result))
                        return 1 if result["status"] in {"incomplete", "excluded"} else 0
                    await asyncio.sleep(interval)
            finally:
                if labels_task:
                    labels_task.cancel()
                    await asyncio.gather(labels_task, return_exceptions=True)
    evolution = build_evolution_worker(
        database,
        heartbeat_interval_seconds=max(1.0, interval),
    )
    while True:
        result = await evolution.tick()
        if once:
            status = result.status if result is not None else "no_job"
            if status == "maintenance_pending":
                print("hub-worker evolution status=maintenance_pending "
                      "维护等待数据库；可重试；未开始新评估")
                return 2
            print(f"hub-worker evolution completed status={status}")
            return 0
        await asyncio.sleep(interval)


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=(
            "realtime", "research", "historical", "documents", "evolution", "prospective",
            "history-observed",
        ),
        default="realtime",
        help="select the durable worker composition root",
    )
    parser.add_argument("--once", action="store_true", help="run one worker tick")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument(
        "--registration-id", metavar="REGISTRATION_ID",
        help="required existing fixed registration for the opt-in history-observed role",
    )
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
    parser.add_argument(
        "--harness-release-baseline", metavar="VALIDATED_BUILD_RECEIPT",
        help="explicitly capture and consume the validated installed release baseline",
    )
    parser.add_argument(
        "--harness-install-sha256", metavar="SHA256",
        help="expected SHA-256 of that immutable validated installation receipt",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    if args.role == "history-observed":
        if not args.registration_id or len(args.registration_id) > 80:
            parser.error("history-observed requires --registration-id (1..80 characters)")
    elif args.registration_id is not None:
        parser.error("--registration-id is only valid with the history-observed role")
    development = development_runtime_from_environment(Path(__file__).resolve().parents[2])
    if development is not None and args.harness_release_baseline:
        raise ValueError("development_release_features_forbidden")
    harness_install_request = None
    if bool(args.harness_release_baseline) != bool(args.harness_install_sha256):
        parser.error("both harness baseline receipt and SHA-256 are required")
    if args.harness_release_baseline:
        if args.role != "research" or args.preflight:
            parser.error("harness baseline opt-in requires the research worker role")
        harness_install_request = InstalledHarnessBootstrapRequest(
            receipt_path=args.harness_release_baseline,
            receipt_sha256=args.harness_install_sha256,
        )
    _configure_retained_recipe_registry_from_normal_launch()
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
            harness_install_request=harness_install_request,
            history_registration_id=args.registration_id,
        )
    )


def _historical_sleep_seconds(interval: float, *, now: datetime | None = None) -> float:
    """Return the next historical tick delay.

    The production hourly cadence is aligned to the next UTC hour plus a small
    settling delay so the just-closed candle is available. Other intervals
    remain useful for local verification and retain the caller's cadence.
    """

    if interval != 3600:
        return interval
    current = (now or datetime.now(UTC)).astimezone(UTC)
    next_hour = (current + timedelta(hours=1)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return max(1.0, (next_hour - current).total_seconds() + 5.0)


if __name__ == "__main__":
    raise SystemExit(run())
