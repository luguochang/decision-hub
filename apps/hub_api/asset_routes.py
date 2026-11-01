"""Read-only projections for source history and verified data-job inputs."""

import os
from datetime import UTC, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func

from packages.contracts_py.decision_hub_contracts.generated.event_asset import EventAssetView
from packages.contracts_py.decision_hub_contracts.generated.research_fact import (
    ArchivedInputView,
    RawCaptureView,
)
from packages.contracts_py.decision_hub_contracts.generated.research_product_view import (
    ResearchUsageSnapshot,
    ResearchUsageSnapshotPage,
    ResearchUsageView,
    UsageCollectionView,
)
from packages.contracts_py.decision_hub_contracts.generated.source_history import (
    SourceDayView,
    SourceHistoryView,
)
from packages.contracts_py.decision_hub_contracts.models import RunStatus, RunView
from packages.evals.prospective_labels import ProspectiveLabelService
from packages.evals.prospective_observations import ProspectiveInputSummary, prospective_summary
from packages.evals.prospective_pairs import ProspectivePairService
from packages.evals.prospective_trials import ProspectiveTrialService
from packages.kernel.decision_hub_kernel.persistence.db import (
    DataJobRecord,
    ProspectivePairRecord,
    RawCaptureRecord,
    RunRecord,
    SourcePollAttemptRecord,
    UsageCollectionRecord,
    as_utc,
    utcnow,
)
from packages.query_views.event_assets import event_assets
from packages.query_views.evolution_readiness import EvolutionReadiness, evolution_readiness
from packages.query_views.research_usage import usage_view
from packages.query_views.usage_queue import (
    UsageQueueFilter,
    UsageQueuePage,
    UsageQueueSummary,
    usage_queue_page,
    usage_queue_summary,
)
from packages.workbench_adapters.fact_assets import FactAssetResolver
from packages.workbench_adapters.usage_ledger import capture_usage, usage_history


class UsageCaptureCommand(BaseModel):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")


def asset_router(database, data_root: Path, *, clock=utcnow):
    router = APIRouter(tags=["asset-views"])
    trials = ProspectiveTrialService(database, artifact_root=data_root / "evolution", clock=clock)
    pairs = ProspectivePairService(database, trials, clock=clock)
    labels = ProspectiveLabelService(database, pairs, None, data_root, clock=clock)

    @router.get("/v1/evolution/prospective-trials")
    def prospective_trials():
        items = []
        for view in trials.list(limit=100):
            request = view.definition["request"]
            items.append({"trial_id": view.trial_id, "registered_at": view.registered_at,
                          "starts_at": request["starts_at"], "ends_at": request["ends_at"],
                          "instrument": request["instrument"], "horizon": request["horizon"],
                          "baseline_version": view.definition["baseline"]["candidate_version"],
                          "candidate_version": view.definition["candidate"]["candidate_version"]})
        return items

    @router.get("/v1/evolution/prospective-pairs")
    def prospective_pairs(trial_id: str, offset: int = Query(0, ge=0),
                          limit: int = Query(20, ge=1, le=100)):
        try:
            trials.get(trial_id)
            with database.session() as session:
                identities = [row[0] for row in session.query(
                    ProspectivePairRecord.pair_id,
                ).filter_by(trial_id=trial_id).order_by(
                    ProspectivePairRecord.source_recorded_at, ProspectivePairRecord.pair_id,
                ).offset(offset).limit(limit + 1)]
            items = []
            for identity in identities[:limit]:
                view, label = pairs.get(identity), labels.get(identity)
                items.append({"pair_id": identity, "status": view["status"],
                              "admitted_at": view["admitted_at"],
                              "window_start_at": view["deadline_at"],
                              "window_end_at": view["window_end_at"],
                              "exclusion_reason": view["exclusion_reason"],
                              "arms": [{"arm": arm["arm"], "status": arm["status"],
                                        "direction": (arm["result"] or {}).get("payload", {})
                                        .get("direction"), "error_code": arm["error_code"]}
                                       for arm in view["arms"]], "label": label})
            return {"items": items, "has_more": len(identities) > limit, "offset": offset}
        except ValueError as exc:
            raise HTTPException(409, "prospective_receipt_unavailable") from exc

    @router.get("/v1/evolution/prospective-pairs/{pair_id}")
    def prospective_pair_receipt(pair_id: str):
        try:
            return {"pair": pairs.get(pair_id), "label": labels.get(pair_id)}
        except ValueError as exc:
            raise HTTPException(409, "prospective_receipt_unavailable") from exc

    @router.get("/v1/evolution/observation-input-audit")
    def evolution_observation_input_audit():
        from packages.evals.prospective_observations import audit_observation_inputs
        return audit_observation_inputs(database, data_root, now=clock())

    @router.get("/v1/evolution/observation-inputs", response_model=ProspectiveInputSummary)
    def evolution_observation_inputs():
        return prospective_summary(database, clock())

    @router.get("/v1/evolution/readiness", response_model=EvolutionReadiness)
    def evolution_inputs():
        return evolution_readiness(database, clock())

    @router.get("/v1/operations/usage-collection", response_model=UsageQueueSummary)
    def usage_queue():
        return usage_queue_summary(database, clock())

    @router.get("/v1/operations/usage-collection/jobs", response_model=UsageQueuePage)
    def usage_jobs(filter: UsageQueueFilter = "all", offset: int = Query(0, ge=0),
                   limit: int = Query(20, ge=1, le=100)):
        return usage_queue_page(database, clock(), filter=filter, offset=offset, limit=limit)

    @router.get("/v1/decision-desk/runs", response_model=list[RunView])
    def ledger_runs(
        response: Response,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        created_before: Annotated[AwareDatetime | None, Query()] = None,
        status: Annotated[RunStatus | None, Query()] = None,
        kind: Literal["research", "baseline", "other"] | None = Query(None),
        q: str | None = Query(None, max_length=200),
    ):
        cutoff = created_before or clock()
        items, total = database.ledger_runs(
            limit=limit,
            offset=offset,
            created_before=cutoff,
            status=status.value if status else None,
            kind=kind,
            q=q,
        )
        response.headers["X-Total-Count"] = str(total)
        response.headers["X-Has-More"] = str(offset + len(items) < total).lower()
        return items

    @router.get("/v1/research/runs/{run_id}/usage", response_model=ResearchUsageView)
    def research_usage(run_id: str):
        root = Path(os.getenv("DECISION_HUB_DSH_HOME", "data/dsh-live")) / "sessions"
        try:
            return usage_view(database, root, run_id)
        except KeyError as exc:
            raise HTTPException(404, "research_run_not_found") from exc

    @router.get(
        "/v1/research/runs/{run_id}/usage-collection", response_model=list[UsageCollectionView]
    )
    def usage_collection(run_id: str):
        with database.session() as session:
            if session.get(RunRecord, run_id) is None:
                raise HTTPException(404, "research_run_not_found")
            rows = session.query(UsageCollectionRecord).filter_by(run_id=run_id).order_by(
                UsageCollectionRecord.generation.desc()
            ).limit(100).all()
            return [{
                "run_id": row.run_id, "session_id": row.session_id,
                "generation": row.generation, "status": row.status,
                "checked_at": as_utc(row.checked_at), "next_check_at": as_utc(row.next_check_at),
                "attempts": row.attempts, "last_snapshot_id": row.last_snapshot_id,
                "error_code": row.error_code,
            } for row in rows]

    @router.post(
        "/v1/research/runs/{run_id}/usage-snapshots", response_model=ResearchUsageSnapshot,
        response_model_exclude_unset=True,
    )
    def record_usage(run_id: str, command: UsageCaptureCommand):
        root = Path(os.getenv("DECISION_HUB_DSH_HOME", "data/dsh-live")) / "sessions"
        try:
            return capture_usage(database, root, run_id, command.request_id)
        except KeyError as exc:
            raise HTTPException(404, "research_run_not_found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get(
        "/v1/research/runs/{run_id}/usage-snapshots", response_model=ResearchUsageSnapshotPage,
        response_model_exclude_unset=True,
    )
    def usage_snapshots(
        run_id: str, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        try:
            return usage_history(database, run_id, limit=limit, offset=offset)
        except KeyError as exc:
            raise HTTPException(404, "research_run_not_found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/v1/macro-events/{macro_event_id}/archive", response_model=EventAssetView)
    def event_archive(
        macro_event_id: str,
        vintage_offset: int = Query(0, ge=0),
        sample_offset: int = Query(0, ge=0),
        run_offset: int = Query(0, ge=0),
    ):
        try:
            return event_assets(
                database,
                macro_event_id,
                vintage_offset=vintage_offset,
                sample_offset=sample_offset,
                run_offset=run_offset,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc

    @router.get("/v1/sources/history", response_model=SourceHistoryView)
    def source_history(days: int = Query(14, ge=1, le=31)):
        now = clock().astimezone(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        with database.session() as session:
            day = func.date(SourcePollAttemptRecord.started_at)
            rows = (
                session.query(
                    SourcePollAttemptRecord.source_id,
                    day,
                    SourcePollAttemptRecord.status,
                    func.count(),
                    func.sum(SourcePollAttemptRecord.accepted_count),
                    func.sum(SourcePollAttemptRecord.revision_count),
                )
                .filter(
                    SourcePollAttemptRecord.started_at >= start,
                    SourcePollAttemptRecord.started_at <= now,
                )
                .group_by(SourcePollAttemptRecord.source_id, day, SourcePollAttemptRecord.status)
                .all()
            )
            return SourceHistoryView(
                start_at=start,
                end_at=now,
                items=[
                    SourceDayView(
                        source_id=r[0],
                        day=r[1],
                        status=r[2],
                        attempts=r[3],
                        accepted=r[4] or 0,
                        revisions=r[5] or 0,
                    )
                    for r in rows
                ],
            )

    @router.get(
        "/v1/source-poll-attempts/{attempt_id}/captures", response_model=list[RawCaptureView]
    )
    def poll_captures(attempt_id: str):
        with database.session() as session:
            if session.get(SourcePollAttemptRecord, attempt_id) is None:
                raise HTTPException(404, "source_attempt_not_found")
            rows = session.query(RawCaptureRecord).filter_by(context_id=f"source:{attempt_id}")
            return [
                RawCaptureView(
                    capture_id=r.capture_id,
                    url=r.url,
                    body_ref=r.body_ref,
                    body_hash=r.body_hash,
                    status_code=r.status_code,
                    byte_count=r.byte_count,
                    media_type=r.media_type,
                    started_at=as_utc(r.started_at),
                    completed_at=as_utc(r.completed_at),
                )
                for r in rows.order_by(RawCaptureRecord.started_at, RawCaptureRecord.capture_id)
            ]

    @router.get("/v1/data-jobs/{job_id}/coverage", response_model=ArchivedInputView)
    def job_coverage(job_id: str, partition: int | None = Query(None, ge=0)):
        with database.session() as session:
            row = session.get(DataJobRecord, job_id)
            if row is None:
                raise HTTPException(404, "data_job_not_found")
            ref = row.coverage_manifest_ref
        if not ref:
            raise HTTPException(404, "data_job_coverage_not_recorded")
        try:
            return FactAssetResolver(data_root).read({"structured_payload_ref": ref}, partition)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "data_job_coverage_archive_unavailable") from exc

    return router
