"""Verify the isolated automatic event -> DSH research delivery path.

This canary intentionally attaches to an already running DSH Web/Hub pair so
the official Host can callback into the same durable database. It refuses any
non-temporary data directory and never changes an active runtime pointer.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from apps.hub_worker.research import DurableResearchWorker
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.outbox import (
    NotificationDispatcher,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    OutboxRecord,
    RunRecord,
)
from packages.provider_adapters.notifications.local import LocalNotificationAdapter
from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
from packages.runtime_adapters.dsh_runtime import (
    DshWebHostClient,
    DshWebHostConfig,
    DshWebResearchRuntime,
)
from packages.source_adapters.official_feeds import OfficialFeedSource, official_source_presets
from packages.source_adapters.official_feeds.adapter import FeedItem, parse_feed
from packages.source_adapters.registry import SourceRegistry

ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "packs" / "crypto_macro"


def _isolated_data_dir() -> Path:
    raw = os.getenv("DECISION_HUB_DATA_DIR")
    if not raw:
        raise RuntimeError("DECISION_HUB_DATA_DIR is required")
    data_dir = Path(raw).expanduser().resolve()
    temporary_roots = {
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
    }
    if not any(root in data_dir.parents for root in temporary_roots) or not (
        data_dir.name.startswith("decision-hub-g2af")
    ):
        raise RuntimeError("G2-AF canary requires a decision-hub-g2af* directory under /tmp")
    database_path = data_dir / "db" / "decision_hub.sqlite3"
    if not database_path.is_file():
        raise RuntimeError("isolated G2-AF database is not initialized")
    return data_dir


def _require_loopback_host(config: DshWebHostConfig) -> None:
    host = (urlparse(config.base_url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("G2-AF autonomous canary only accepts a loopback DSH Host")


def _cursor(item: FeedItem) -> str:
    timestamp = item.updated_at or item.published_at
    return f"{timestamp.isoformat() if timestamp else ''}|{item.item_id}"


def _used_native_search(result: object) -> bool:
    rounds = getattr(result, "rounds", ())
    return any(
        getattr(invocation, "capability_id", None) == "dsh.native.web_search"
        for round_item in rounds
        for invocation in getattr(round_item, "tool_invocations", ())
    )


async def _latest_fed_speech_source() -> tuple[OfficialFeedSource, str, str]:
    base = next(
        item
        for item in official_source_presets(include_calendar=False)
        if item.manifest.source_id == "fed-speeches"
    )
    if not isinstance(base, OfficialFeedSource):
        raise RuntimeError("fed-speeches connector has an unexpected implementation")
    status_code, body = await base.fetcher(base.endpoint)
    if status_code >= 400:
        raise RuntimeError("fed-speeches discovery request failed")
    items = sorted(parse_feed(body, base.format_hint), key=_cursor)
    if len(items) < 2:
        raise RuntimeError("fed-speeches feed does not provide a previous cursor")
    target = items[-1]
    source = OfficialFeedSource(
        base.manifest.model_copy(update={"max_batch": 1}),
        base.endpoint,
        format_hint=base.format_hint,
        include_document_body=True,
        bootstrap_latest=False,
    )
    return source, _cursor(items[-2]), target.title


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_G2AF_AUTONOMOUS_CANARY") != "1":
        raise RuntimeError(
            "set DECISION_HUB_G2AF_AUTONOMOUS_CANARY=1 to authorize external read-only use"
        )
    data_dir = _isolated_data_dir()
    host_config = DshWebHostConfig.from_env()
    _require_loopback_host(host_config)
    database = Database()
    source, previous_cursor, target_title = await _latest_fed_speech_source()
    registry = SourceRegistry()
    registry.register(source)
    policy = CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT)
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=policy,
    )
    database.update_source_success(
        source.manifest.source_id,
        previous_cursor,
        0,
        next_poll_at=datetime.now(UTC),
    )

    first_poll = await ingestion.poll_once(source.manifest.source_id)
    if len(first_poll.run_targets) != 1:
        raise RuntimeError("official feed did not admit exactly one baseline event")
    event_id = first_poll.run_targets[0].event_id
    run_id = RunService(database).for_event(event_id, strategy_version="research.v1")
    if run_id is None:
        raise RuntimeError("latest official event did not match research admission policy")

    runtime = DshWebResearchRuntime(
        DshSessionLinkService(database),
        DshWebHostClient(host_config),
        poll_interval_seconds=0.25,
        readiness_grace_seconds=10,
    )
    worker = DurableResearchWorker(
        database,
        runtime,
        pack_root=PACK_ROOT,
        checkpoint_path=data_dir / "checkpoints" / "g2af-autonomous-canary.sqlite3",
        worker_id="g2af-autonomous-canary",
        allowed_capabilities=(
            "official.macro",
            "market.cross_asset",
            "market.crypto_derivatives",
            "web.fetch",
        ),
        execution_mode="live",
    )
    try:
        worker_report = await worker.tick()
    finally:
        await runtime.close()
    if worker_report is None or worker_report.run_id != run_id:
        raise RuntimeError("research worker did not claim the automatic Run")

    run = database.get_run_record(run_id)
    artifact = (
        database.get_artifact_view(worker_report.artifact_id)
        if worker_report.artifact_id is not None
        else None
    )
    result = ResearchObservabilityService(database).get_result(run_id)
    evidence = ResearchEvidenceService(database).list_run_evidence(run_id)
    trace = ResearchObservabilityService(database).list_trace(run_id)
    notification_path = data_dir / "canary" / f"{run_id}.notifications.jsonl"
    dispatcher = NotificationDispatcher(
        database,
        {"local": LocalNotificationAdapter(notification_path)},
    )
    delivered_first = await dispatcher.dispatch_once()
    delivered_second = await dispatcher.dispatch_once()
    second_poll = await ingestion.poll_once(source.manifest.source_id)

    with database.session() as session:
        outbox = (
            session.query(OutboxRecord)
            .filter_by(artifact_id=worker_report.artifact_id)
            .one_or_none()
        )
        children = session.query(RunRecord).filter_by(parent_run_id=run_id).all()
        automatic_root_research_runs = (
            session.query(RunRecord)
            .filter_by(
                event_id=event_id,
                strategy_version="research.v1",
                admission_origin="automatic",
                parent_run_id=None,
            )
            .count()
        )

    checks = {
        "automatic_admission": run is not None and run.admission_origin == "automatic",
        "artifact_committed": artifact is not None,
        "canonical_result_committed": result is not None,
        "synthesis_attested": result is not None and result.synthesis_failure_code is None,
        "dsh_native_search_used": result is not None and _used_native_search(result),
        "evidence_retained": bool(evidence),
        "trace_retained": bool(trace),
        "outbox_created": outbox is not None,
        "notification_delivered_once": delivered_first == 1 and delivered_second == 0,
        "child_recheck_scheduled": len(children) == 1,
        "duplicate_poll_idempotent": (
            second_poll.accepted_count == 0
            and automatic_root_research_runs == 1
            and not second_poll.run_targets
        ),
    }
    status = "passed" if all(checks.values()) else "failed"
    return {
        "schema_version": "g2af-autonomous-flow-canary.v1",
        "status": status,
        "scope": "temporary_official_feed_to_dsh_artifact_outbox_recheck",
        "target_title": target_title,
        "event_id": event_id,
        "run_id": run_id,
        "run_status": run.status if run is not None else None,
        "gate_status": artifact.gate_status.value if artifact is not None else None,
        "research_status": result.status if result is not None else None,
        "stop_reason": result.stop_reason.code if result is not None else None,
        "hard_coverage_ratio": (
            result.final_coverage.hard_coverage_ratio if result is not None else None
        ),
        "rounds": len(result.rounds) if result is not None else 0,
        "evidence_count": len(evidence),
        "trace_count": len(trace),
        "duplicate_poll": {
            "accepted_count": second_poll.accepted_count,
            "duplicate_count": second_poll.duplicate_count,
            "run_target_count": len(second_poll.run_targets),
            "automatic_root_research_runs": automatic_root_research_runs,
        },
        "checks": checks,
        "note": (
            "A passed result proves the isolated technical delivery path, not financial "
            "accuracy, profitability, or permission to trade."
        ),
    }


def main() -> int:
    try:
        result = asyncio.run(_run())
    except Exception as exc:
        result = {
            "schema_version": "g2af-autonomous-flow-canary.v1",
            "status": "failed",
            "error_code": str(getattr(exc, "error_code", type(exc).__name__)),
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
