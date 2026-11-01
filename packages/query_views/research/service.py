from __future__ import annotations

import base64
import hashlib
import json
from binascii import Error as BinasciiError
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import ValidationError
from sqlalchemy import and_, case, exists, or_, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    DshBusinessStatus,
    DshUpstreamIdentity,
    ErrorProvenance,
    EvidenceCandidate,
    EvidenceRequirement,
    ExecutionBudget,
    ExecutionBudgetV2,
    FactEnvelope,
    RequirementReadinessItem,
    ResearchActionSummary,
    ResearchCostBreakdown,
    ResearchCostComponent,
    ResearchExecutionView,
    ResearchInboxItem,
    ResearchInboxView,
    ResearchMarketView,
    ResearchObservabilityView,
    ResearchOutlook,
    ResearchPlan,
    ResearchProcessStep,
    ResearchReview,
    ResearchRound,
    ResearchRoundView,
    ResearchRunComparisonView,
    ResearchRunDetailView,
    ResearchRunView,
    ResearchSessionResult,
    ResearchSnapshotManifest,
    ResearchStopReason,
    ResearchStrategyReceipt,
    ResearchTask,
    ResearchTraceEvent,
    ResearchValueEvaluation,
    ResearchVersionLineage,
    SourceAttemptView,
    ToolInvocation,
    ToolResultSummary,
)
from packages.kernel.decision_hub_kernel.application.research_workflow_checkpoints import (
    CHECKPOINT_WARNING, checkpoint_trace_call_ids,
)
from packages.contracts_py.decision_hub_contracts.generated.agentic_research import (
    ResearchNativeExecutionFailure,
)
from packages.query_views.research.native_failure import native_failure_projection_in_session
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
    read_trace_projection,
)
from packages.kernel.decision_hub_kernel.application.research_pack import (
    ResearchPackService,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    has_valid_accepted_independent_review,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
)
from packages.kernel.decision_hub_kernel.application.research_value_basis import (
    SavedCoverageBasis,
    saved_coverage_basis,
    saved_submission_basis,
)
from packages.kernel.decision_hub_kernel.application.study import research_profile_for_study
from packages.kernel.decision_hub_kernel.decision.horizon_contract import (
    validate_horizon_contract,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    DshSessionLinkRecord,
    EventWatchRecord,
    ObservationRecord,
    OutboxRecord,
    ResearchEvidenceRecord,
    ResearchFactRecord,
    ResearchResultRecord,
    ResearchToolCallReservationRecord,
    ResearchTraceRecord,
    RunRecord,
    RunRoleOutputRecord,
    SnapshotRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.query_views.research.configuration import saved_configuration
from packages.query_views.research.decision_graph import build_decision_graph
from packages.query_views.research.role_packets import build_role_packets

# A market snapshot can produce a daily direction without granting permission
# to enter or lever it.  These requirements are therefore evaluated again at
# the execution boundary.  ``derivatives_crowding`` is intentionally present
# even when the Pack marks it soft for the 7-day direction view.
_EXECUTION_GAP_REQUIREMENTS = frozenset({"derivatives_crowding"})


@dataclass(frozen=True)
class ResearchRunListPage:
    items: list[ResearchRunView]
    next_cursor: str | None
    has_more: bool
    total_count: int


@dataclass(frozen=True)
class ResearchHomeReportPreview:
    run_id: str
    artifact_id: str
    kind: Literal["synthesis", "perpetual", "legacy", "unverified", "unavailable"]
    text: str


@dataclass(frozen=True)
class ResearchHomeView:
    latest_report: ResearchRunView | None
    latest_report_at: datetime | None
    latest_report_headline: str | None
    latest_run: ResearchRunView | None
    active_runs: list[ResearchRunView]
    active_count: int
    generated_at: datetime
    latest_report_preview: ResearchHomeReportPreview | None = None


@dataclass(frozen=True)
class _SavedResearchResult:
    result: ResearchSessionResult | None
    issue_code: str | None = None


@dataclass(frozen=True)
class _RunViewReadRows:
    """Raw rows from one bounded list read; never a permission or validated basis."""

    run_id: str
    saved: _SavedResearchResult
    traces: list[ResearchTraceRecord]
    observation: ObservationRecord | None
    artifact: ArtifactRecord | None
    evidence: list[ResearchEvidenceRecord]
    facts: list[ResearchFactRecord]
    link: DshSessionLinkRecord | None


class ResearchQueryService:
    """Build human-readable research views from canonical durable facts."""

    def __init__(self, database: Database, *, pack_root: Path) -> None:
        self.database = database
        self.observability = ResearchObservabilityService(database)
        bindings = yaml.safe_load(
            (pack_root / "tools" / "bindings.yaml").read_text(encoding="utf-8")
        )
        self.capability_manifests = {
            item["capability_id"]: item
            for item in bindings.get("capabilities", [])
            if isinstance(item, dict) and isinstance(item.get("capability_id"), str)
        }
        self.value_evaluations = ResearchValueEvaluationService(database, pack_root=pack_root)

    def home(self) -> ResearchHomeView:
        """Select the saved report and active work directly across the research ledger."""
        with self.database.session() as session:
            query = session.query(RunRecord).filter(RunRecord.strategy_version == "research.v1")
            report = (
                query.join(ArtifactRecord, and_(
                    ArtifactRecord.artifact_id == RunRecord.artifact_id,
                    ArtifactRecord.run_id == RunRecord.run_id,
                ))
                .filter(RunRecord.status.in_(
                    ("completed", "degraded", "research_only", "rejected")
                ))
                .order_by(ArtifactRecord.created_at.desc(), RunRecord.run_id.desc())
                .first()
            )
            active = query.filter(RunRecord.status.in_(
                ("admitted", "queued", "running", "researching", "retry_wait")
            ))
            active_count = active.count()
            active_rows = active.order_by(
                RunRecord.updated_at.desc(), RunRecord.run_id.desc()
            ).limit(3)
            latest = query.order_by(RunRecord.updated_at.desc(), RunRecord.run_id.desc()).first()
            artifact = session.get(ArtifactRecord, report.artifact_id) if report else None
            report_view = self._run_view(session, report) if report else None
            perpetual_source = (
                session.scalar(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.run_id == report.run_id,
                        RunRoleOutputRecord.role == "research_publication_source",
                    )
                )
                if report is not None
                and report_view is not None
                and report_view.research_profile == "perpetual_decision_v2"
                else None
            )
            return ResearchHomeView(
                latest_report=report_view,
                latest_report_at=as_utc(artifact.created_at) if artifact else None,
                latest_report_headline=artifact.headline if artifact else None,
                latest_report_preview=(
                    _home_report_preview(
                        report_view, artifact, _saved_research_result(session, report.run_id),
                        session.get(SnapshotRecord, report.decision_snapshot_id)
                        if report.decision_snapshot_id else None,
                        perpetual_publication_verified=(
                            _perpetual_home_publication_reference_verified(
                                report_view, artifact, perpetual_source,
                            )
                            if report_view.research_profile == "perpetual_decision_v2"
                            else None
                        ),
                    )
                    if report is not None and report_view is not None and artifact is not None
                    else None
                ),
                latest_run=self._run_view(session, latest) if latest else None,
                active_runs=[self._run_view(session, row) for row in active_rows],
                active_count=active_count,
                generated_at=utcnow(),
            )

    def list(self, *, limit: int = 100) -> list[ResearchRunView]:
        """Keep the original first-page list contract for existing callers."""

        return self.list_page(limit=limit).items

    def list_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        cursor: str | None = None,
        status: str | None = None,
        source_id: str | None = None,
        origin: str | None = None,
        q: str | None = None,
    ) -> ResearchRunListPage:
        """Page only research.v1 Runs using the existing business ordering.

        A cursor encodes the last sort tuple, so a newly inserted Run ahead of
        the page does not shift the next page. Mutated Run status/timestamp can
        still move an item; the durable Run detail remains the audit authority.
        """

        if offset < 0 or (cursor is not None and offset):
            raise ValueError("research_run_page_arguments_invalid")
        bounded = max(1, min(limit, 500))
        normalized_q = " ".join(q.split()) if q else None
        fingerprint = _run_page_filter_fingerprint(status, source_id, origin, normalized_q)
        after = _decode_run_cursor(cursor, fingerprint) if cursor is not None else None
        status_rank = case(
            (
                RunRecord.status.in_(
                    ("running", "researching", "admitted", "queued", "retry_wait")
                ),
                0,
            ),
            (RunRecord.status.in_(("completed", "research_only", "degraded")), 1),
            (RunRecord.status.in_(("failed", "rejected")), 2),
            (RunRecord.status == "cancelled", 3),
            else_=4,
        )
        artifact_rank = case((RunRecord.artifact_id.is_(None), 1), else_=0)
        visible_status = case(
            (ArtifactRecord.gate_status == "research_only", "research_only"),
            (ArtifactRecord.gate_status == "reject", "rejected"),
            (RunRecord.status == "admitted", "queued"),
            (RunRecord.status == "running", "researching"),
            else_=RunRecord.status,
        )
        with self.database.session() as session:
            query = (
                session.query(RunRecord)
                .outerjoin(ArtifactRecord, RunRecord.artifact_id == ArtifactRecord.artifact_id)
                .filter(RunRecord.strategy_version == "research.v1")
            )
            if status is not None:
                query = query.filter(visible_status == status)
            if source_id is not None:
                query = query.filter(
                    exists().where(
                        and_(
                            ObservationRecord.event_id == RunRecord.event_id,
                            ObservationRecord.source_id == source_id,
                        )
                    )
                )
            if origin is not None:
                query = query.filter(RunRecord.admission_origin == origin)
            if normalized_q:
                pattern = f"%{_escape_like(normalized_q)}%"
                query = query.filter(
                    or_(
                        RunRecord.run_id.ilike(pattern, escape="\\"),
                        RunRecord.event_id.ilike(pattern, escape="\\"),
                        ArtifactRecord.headline.ilike(pattern, escape="\\"),
                        ArtifactRecord.summary.ilike(pattern, escape="\\"),
                        exists().where(
                            and_(
                                ObservationRecord.event_id == RunRecord.event_id,
                                ObservationRecord.text.ilike(pattern, escape="\\"),
                            )
                        ),
                    )
                )
            total_count = query.count()
            if after is not None:
                rank, artifact, updated_at, run_id = after
                query = query.filter(
                    or_(
                        status_rank > rank,
                        and_(status_rank == rank, artifact_rank > artifact),
                        and_(
                            status_rank == rank,
                            artifact_rank == artifact,
                            RunRecord.updated_at < updated_at,
                        ),
                        and_(
                            status_rank == rank,
                            artifact_rank == artifact,
                            RunRecord.updated_at == updated_at,
                            RunRecord.run_id > run_id,
                        ),
                    )
                )
            rows = (
                query.order_by(
                    status_rank.asc(),
                    artifact_rank.asc(),
                    RunRecord.updated_at.desc(),
                    RunRecord.run_id.asc(),
                )
                .offset(offset)
                .limit(bounded + 1)
                .all()
            )
            has_more = len(rows) > bounded
            rows = rows[:bounded]
            next_cursor = (
                _encode_run_cursor(rows[-1], fingerprint, _run_status_rank(rows[-1].status))
                if has_more and rows
                else None
            )
            return ResearchRunListPage(
                items=[self._run_view(session, row) for row in rows],
                next_cursor=next_cursor,
                has_more=has_more,
                total_count=total_count,
            )

    def inbox(self, *, limit: int = 100) -> ResearchInboxView:
        """Project watched events and research Runs into one canonical Inbox."""

        bounded = max(1, min(limit, 500))
        with self.database.session() as session:
            # Select each half of the merged top-N before constructing detail views.
            # Match the final stable ordering, including ties across events/runs.
            runs = (
                session.query(RunRecord)
                .filter(RunRecord.strategy_version == "research.v1")
                .order_by(
                    RunRecord.updated_at.desc(), RunRecord.event_id.desc(), RunRecord.run_id.desc()
                )
                .limit(bounded)
                .all()
            )
            watches = (
                session.query(EventWatchRecord)
                .filter(~exists().where(and_(
                    RunRecord.event_id == EventWatchRecord.event_id,
                    RunRecord.strategy_version == "research.v1",
                )))
                .order_by(EventWatchRecord.updated_at.desc(), EventWatchRecord.event_id.desc())
                .limit(bounded)
                .all()
            )
            selected = sorted(
                [*runs, *watches], key=lambda item: (item.updated_at, item.event_id), reverse=True
            )[:bounded]
            selected_events = {item.event_id for item in selected if isinstance(item, RunRecord)}
            watches_by_event = {
                item.event_id: item for item in session.query(EventWatchRecord).filter(
                    EventWatchRecord.event_id.in_(selected_events)
                )
            } if selected_events else {}
            items = [
                self._inbox_run_item(session, item, watches_by_event.get(item.event_id))
                if isinstance(item, RunRecord) else self._inbox_watch_item(session, item)
                for item in selected
            ]
            return ResearchInboxView(
                schema_version="research-inbox-view.v1",
                items=items,
                generated_at=utcnow(),
            )

    def observability_view(self, run_id: str) -> ResearchObservabilityView | None:
        """Explain versions, readiness, attempts and cost without raw payloads."""

        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                return None
            result_row = session.get(ResearchResultRecord, run_id)
            result = (
                ResearchSessionResult.model_validate_json(result_row.payload_json)
                if result_row is not None
                else None
            )
            evidence = [
                _evidence_view(item)
                for item in session.query(ResearchEvidenceRecord).filter_by(run_id=run_id).all()
            ]
            facts = [
                _fact_view(item)
                for item in session.query(ResearchFactRecord).filter_by(run_id=run_id).all()
            ]
            traces = (
                session.query(ResearchTraceRecord)
                .filter_by(run_id=run_id)
                .order_by(ResearchTraceRecord.sequence_no)
                .all()
            )
            basis = self._coverage_basis_for_run(session, run)
            requirements = tuple(basis.request.evidence_requirements) if basis.request else ()
            coverage = (
                result.final_coverage if result is not None
                else _fallback_coverage(run, traces, basis, evidence, facts)
            )
            attempts = self._source_attempts(session, run_id)
            dsh_link = session.get(DshSessionLinkRecord, run_id)
            runtime_id = (
                result.runtime_id
                if result is not None
                else _observed_runtime_id(session, run_id, traces)
            )
            runtime_version = (
                result.runtime_version
                if result is not None
                else _observed_runtime_version(run, dsh_link)
            )
            trajectory_ref = (
                result.trace_ref
                if result is not None
                else dsh_link.trace_ref
                if dsh_link is not None
                else None
            )
            return ResearchObservabilityView(
                schema_version="research-observability-view.v1",
                **native_failure_projection_in_session(session, run_id),
                run_id=run_id,
                versions=ResearchVersionLineage(
                    **saved_configuration(
                        session, run, self._submission_basis_for_run(session, run),
                        pack_service=ResearchPackService(self.database),
                    ),
                    runtime_id=runtime_id,
                    runtime_version=runtime_version,
                ),
                coverage_basis=cast(
                    Literal[
                        "saved_result", "saved_request", "saved_task_request", "saved_checkpoint",
                        "round_request_only", "unavailable", "invalid",
                    ],
                    "saved_result" if result is not None else basis.status,
                ),
                coverage_cutoff_at=basis.cutoff_at if result is None else None,
                readiness=(
                    self._readiness(coverage, facts, attempts, requirements)
                    if coverage is not None else []
                ),
                source_attempts=attempts,
                cost=self._cost_breakdown(run, result, attempts),
                trajectory_ref=trajectory_ref,
                telemetry_ref=_telemetry_ref(trajectory_ref),
                ledger_ref=f"decision-hub://runs/{run_id}",
                generated_at=utcnow(),
            )

    def value_evaluation(self, run_id: str) -> ResearchValueEvaluation | None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                return None
        return self.value_evaluations.latest(run_id)

    def _inbox_run_item(
        self,
        session: Session,
        run: RunRecord,
        watch: EventWatchRecord | None,
    ) -> ResearchInboxItem:
        observation = _observation_for_event(session, run.event_id)
        artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        dsh_link = session.get(DshSessionLinkRecord, run.run_id)
        recheck = (
            session.query(RunRecord)
            .filter(
                RunRecord.parent_run_id == run.run_id,
                RunRecord.available_at.is_not(None),
            )
            .order_by(RunRecord.available_at.asc())
            .first()
        )
        outbox = (
            session.query(OutboxRecord)
            .filter_by(artifact_id=run.artifact_id)
            .order_by(OutboxRecord.id.desc())
            .first()
            if run.artifact_id
            else None
        )
        configuration = saved_configuration(
            session, run, self._submission_basis_for_run(session, run),
            pack_service=ResearchPackService(self.database),
        )
        return ResearchInboxItem(
            schema_version="research-inbox-item.v1",
            event_id=run.event_id,
            event_title=_event_title(observation),
            event_family=watch.event_family if watch is not None else _event_family(observation),
            watch_id=watch.watch_id if watch is not None else None,
            run_id=run.run_id,
            dsh_session_id=dsh_link.dsh_session_id if dsh_link is not None else None,
            artifact_id=run.artifact_id,
            parent_run_id=run.parent_run_id,
            admission_origin=_inbox_origin(run.admission_origin),
            status=_inbox_status(run, artifact),
            baseline_status=_baseline_status(watch),
            gate_status=cast(
                Literal["publish", "degraded", "research_only", "reject"] | None,
                artifact.gate_status if artifact is not None else None,
            ),
            report_available=artifact is not None,
            headline=artifact.headline if artifact is not None else None,
            summary=artifact.summary if artifact is not None else None,
            scheduled_at=as_utc(watch.scheduled_at) if watch is not None else None,
            next_recheck_at=as_utc(recheck.available_at) if recheck is not None else None,
            notification_status=_notification_status(outbox),
            domain_pack_ref=configuration["domain_pack_ref"],
            role_profile_ref=configuration["role_profile_ref"],
            created_at=_required_time(as_utc(run.created_at)),
            updated_at=_required_time(as_utc(run.updated_at)),
        )

    def _inbox_watch_item(
        self,
        session: Session,
        watch: EventWatchRecord,
    ) -> ResearchInboxItem:
        observation = _observation_for_event(session, watch.event_id)
        return ResearchInboxItem(
            schema_version="research-inbox-item.v1",
            event_id=watch.event_id,
            event_title=_event_title(observation),
            event_family=watch.event_family,
            watch_id=watch.watch_id,
            run_id=None,
            dsh_session_id=None,
            artifact_id=None,
            parent_run_id=None,
            admission_origin="automatic",
            status="watching",
            baseline_status=_baseline_status(watch),
            gate_status=None,
            report_available=False,
            headline=None,
            summary=None,
            scheduled_at=as_utc(watch.scheduled_at),
            next_recheck_at=None,
            notification_status="not_applicable",
            domain_pack_ref=None,
            role_profile_ref=None,
            created_at=_required_time(as_utc(watch.created_at)),
            updated_at=_required_time(as_utc(watch.updated_at)),
        )

    def _source_attempts(self, session: Session, run_id: str) -> list[SourceAttemptView]:
        rows = (
            session.query(ResearchToolCallReservationRecord)
            .filter_by(run_id=run_id)
            .order_by(ResearchToolCallReservationRecord.reserved_at)
            .all()
        )
        projected: list[SourceAttemptView] = []
        for row in rows:
            payload = row.result_json if row.status == "completed" else row.error_json
            if not payload:
                if row.status in {"failed", "cancelled"}:
                    projected.append(
                        SourceAttemptView(
                            capability_id=row.capability_id,
                            provider_id="unresolved",
                            route_role="direct",
                            service_tier="unknown",
                            status="failed" if row.status == "failed" else "skipped",
                            latency_ms=_duration_ms(row.reserved_at, row.completed_at),
                            cost_usd=None,
                            cost_status="unknown",
                            error_code=(
                                "research_tool_cancelled"
                                if row.status == "cancelled"
                                else "research_tool_failed"
                            ),
                            retryable=False,
                        )
                    )
                continue
            raw = json.loads(payload)
            raw_attempts = raw.get("provider_attempts") or []
            if raw_attempts:
                projected.extend(
                    _source_attempt(row.capability_id, attempt)
                    for attempt in raw_attempts
                    if isinstance(attempt, dict)
                )
                continue
            if row.status == "completed":
                provider = str(raw.get("provider") or "direct")
                cost = raw.get("cost_usd")
                known_free = self._known_free(row.capability_id, provider)
                projected.append(
                    SourceAttemptView(
                        capability_id=row.capability_id,
                        provider_id=provider,
                        route_role="direct",
                        service_tier=("replay" if provider.startswith("replay") else "unknown"),
                        status="succeeded",
                        latency_ms=_duration_ms(row.reserved_at, row.completed_at),
                        cost_usd=(
                            float(cost)
                            if isinstance(cost, (int, float))
                            else 0.0
                            if known_free
                            else None
                        ),
                        cost_status=(
                            "known" if isinstance(cost, (int, float)) or known_free else "unknown"
                        ),
                        error_code=None,
                        retryable=False,
                    )
                )
            else:
                projected.append(
                    SourceAttemptView(
                        capability_id=row.capability_id,
                        provider_id="unresolved",
                        route_role="direct",
                        service_tier="unknown",
                        status=_attempt_status(str(raw.get("error_code") or "")),
                        latency_ms=_duration_ms(row.reserved_at, row.completed_at),
                        cost_usd=None,
                        cost_status="unknown",
                        error_code=(str(raw["error_code"]) if raw.get("error_code") else None),
                        retryable=bool(raw.get("retryable", False)),
                    )
                )
        return projected

    def _known_free(self, capability_id: str, provider_id: str) -> bool:
        manifest = self.capability_manifests.get(capability_id, {})
        free_policies = {"known-free-public.v1", "no-external-cost.v1"}
        if manifest.get("cost_policy_ref") in free_policies:
            return True
        return any(
            route.get("provider_id") == provider_id
            and route.get("cost_policy_ref") in free_policies
            for route in manifest.get("provider_routes", [])
            if isinstance(route, dict)
        )

    def _readiness(
        self,
        coverage: CoverageAssessment,
        facts: list[FactEnvelope],
        attempts: list[SourceAttemptView],
        requirements: Iterable[EvidenceRequirement],
    ) -> list[RequirementReadinessItem]:
        gaps = {item.requirement_id: item for item in coverage.gaps}
        fact_fields: dict[str, set[str]] = {}
        for fact in facts:
            fact_fields.setdefault(fact.requirement_id, set()).add(fact.field)
        attempted_all = [item.capability_id for item in attempts]
        result: list[RequirementReadinessItem] = []
        for requirement in requirements:
            gap = gaps.get(requirement.requirement_id)
            attempted: list[str] = list(
                dict.fromkeys(
                    [
                        *(gap.attempted_capabilities if gap is not None else []),
                        *attempted_all,
                    ]
                )
            )
            required_fields = list(requirement.required_fields or [])
            present_fields = fact_fields.get(requirement.requirement_id, set())
            result.append(
                RequirementReadinessItem(
                    requirement_id=requirement.requirement_id,
                    importance=requirement.importance,
                    status="ready" if gap is None else _readiness_status(gap.reason_code),
                    reason_code=gap.reason_code if gap is not None else None,
                    required_fields=required_fields,
                    missing_fields=[item for item in required_fields if item not in present_fields],
                    required_event_offsets=list(requirement.required_event_offsets or []),
                    attempted_capabilities=attempted,
                    next_capability=_next_capability(requirement, attempted),
                )
            )
        return result

    def _coverage_basis_for_run(self, session: Session, run: RunRecord) -> SavedCoverageBasis:
        assessed_at = as_utc(run.finished_at or run.updated_at)
        if run.status in {"admitted", "queued", "running", "retry_wait"}:
            assessed_at = utcnow()
        return saved_coverage_basis(session, run, assessed_at)

    def _submission_basis_for_run(
        self, session: Session, run: RunRecord,
    ) -> SavedCoverageBasis:
        assessed_at = as_utc(run.finished_at or run.updated_at)
        if run.status in {"admitted", "queued", "running", "retry_wait"}:
            assessed_at = utcnow()
        return saved_submission_basis(session, run, assessed_at)

    def _requirements_for_run(
        self, session: Session, run: RunRecord
    ) -> tuple[EvidenceRequirement, ...]:
        basis = self._coverage_basis_for_run(session, run)
        return tuple(basis.request.evidence_requirements) if basis.request is not None else ()

    def _cost_breakdown(
        self,
        run: RunRecord,
        result: ResearchSessionResult | None,
        attempts: list[SourceAttemptView],
    ) -> ResearchCostBreakdown:
        model_cost = result.estimated_cost_usd if result is not None else run.cost_usd
        search_attempts = [item for item in attempts if item.capability_id.startswith("web.search")]
        provider_attempts = [
            item for item in attempts if not item.capability_id.startswith("web.search")
        ]
        components = [
            ResearchCostComponent(
                component="model",
                amount_usd=model_cost,
                status="known" if model_cost is not None else "unknown",
                policy_ref=None,
            ),
            _attempt_cost_component("search", search_attempts),
            _attempt_cost_component("typed_provider", provider_attempts),
            ResearchCostComponent(
                component="subscription",
                amount_usd=None,
                status="unknown",
                policy_ref=None,
            ),
        ]
        known = [item.amount_usd for item in components if item.amount_usd is not None]
        unknown = [item.component for item in components if item.status == "unknown"]
        status = "unknown" if not known else "partial" if unknown else "known"
        subtotal = sum(known) if known else None
        return ResearchCostBreakdown(
            status=status,
            known_subtotal_usd=subtotal,
            total_usd=subtotal if status == "known" else None,
            currency="USD",
            unknown_components=unknown,
            components=components,
        )

    def get(self, run_id: str) -> ResearchRunDetailView | None:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                return None
            return self._detail_in_session(session, run)

    def get_for_signed_harness_review(self, run_id: str, service) -> ResearchRunDetailView | None:
        """Internal authenticated bridge read of one current signed arm.

        This never grants public report access or inherits the source Run's
        Result, traces or root proof. The API supplies its installed service;
        neither user query parameters nor this projection provide authority.
        """
        from packages.kernel.decision_hub_kernel.application.research_harness_frozen_run import (
            STRATEGY, read_assignment, read_consumption,
        )
        from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import HarnessFrozenRunCaseV1
        from packages.kernel.decision_hub_kernel.application.research_binding import FrozenResearchBundle, read_research_bundle_in_session
        from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
        from packages.kernel.decision_hub_kernel.application.research_harness_execution import (
            _fence, RecordedRootConsumption, WITNESS_SCHEMA,
            verify_harness_method_in_session, require_harness_root_witness_in_session,
        )
        from packages.kernel.decision_hub_kernel.application.model_dispatch import ModelDispatchService
        from packages.kernel.decision_hub_kernel.persistence.db import DshSessionPromptRecord
        if service.database is not self.database:
            raise ValueError("harness_frozen_reader_database_mismatch")
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN")
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None or run.strategy_version != STRATEGY:
                return None
            found = read_assignment(session, run_id=run_id)
            if found is None:
                raise ValueError("harness_frozen_assignment_missing")
            assignment, _ = found
            service._verify_owned(session, assignment)
            case = HarnessFrozenRunCaseV1.model_validate_json(assignment.case_json)
            if (run.event_id != case.source_event_id or run.study_id != case.source_study_id
                    or run.parent_run_id != case.source_run_id):
                raise ValueError("harness_frozen_reader_run_identity_invalid")
            link = _fence(session, run_id, utcnow())
            prepared = read_prepared_execution_in_session(session, run_id, link.generation)
            bundle = read_research_bundle_in_session(session, run_id)
            if not isinstance(bundle, FrozenResearchBundle) or read_consumption(session, bundle) is None:
                raise ValueError("harness_frozen_consumption_missing")
            if verify_harness_method_in_session(session, bundle) is None:
                raise ValueError("harness_method_v4_required")
            dispatch_row = session.scalar(select(RunRoleOutputRecord).filter_by(
                run_id=run_id, role="research_model_dispatch"))
            if dispatch_row is None:
                raise ValueError("harness_frozen_current_dispatch_required")
            dispatch = ModelDispatchService._read(dispatch_row)
            if (dispatch.run_id != run_id or dispatch.dsh_session_id != link.dsh_session_id
                    or dispatch.request_hash != link.request_hash or not 1 <= dispatch.generation <= link.generation
                    or dispatch.plugin_build_hash != link.plugin_build_hash
                    or dispatch.purpose != "conversation"
                    or dispatch.execution_session_id is not None or dispatch.parent_session_id is not None):
                raise ValueError("harness_frozen_current_dispatch_mismatch")
            # ModelDispatch.bind preserves the first immutable raw generation;
            # latest Prepared above revalidates every original execution turn.
            # It must remain this same Run/session/method, never a future turn.
            original_prompt = session.get(DshSessionPromptRecord, (run_id, dispatch.generation))
            if (original_prompt is None or original_prompt.generation != dispatch.generation
                    or original_prompt.dsh_session_id != dispatch.dsh_session_id
                    or original_prompt.request_hash != dispatch.request_hash):
                raise ValueError("harness_frozen_current_dispatch_mismatch")
            rows = session.scalars(select(RunRoleOutputRecord).filter_by(run_id=run_id)
                .where(RunRoleOutputRecord.role.startswith(f"harness_root:{link.generation}:"))
                .order_by(RunRoleOutputRecord.created_at).limit(9)).all()
            if len(rows) > 8:
                raise ValueError("harness_frozen_current_root_ambiguous")
            witness = None
            for row in rows:
                if row.schema_version != WITNESS_SCHEMA:
                    raise ValueError("harness_frozen_current_root_corrupt")
                original = RecordedRootConsumption.model_validate_json(row.payload_json)
                payload = original.payload
                if (payload.run_id == run_id and payload.dsh_session_id == link.dsh_session_id
                        and payload.request_id == prepared.prompt.request_id
                        and payload.request_hash == link.request_hash and payload.generation == link.generation):
                    require_harness_root_witness_in_session(session, run_id, row.payload_hash, prepared=prepared)
                    witness = row.payload_hash
                    break
            if witness is None:
                raise ValueError("harness_frozen_current_root_required")
            view = self._detail_in_session(session, run)
            current = _fence(session, run_id, utcnow())
            if (current.generation != link.generation or current.dsh_session_id != link.dsh_session_id
                    or current.request_hash != link.request_hash):
                raise ValueError("harness_frozen_reader_generation_changed")
            return view

    def _detail_in_session(self, session: Session, run: RunRecord) -> ResearchRunDetailView:
        run_id = run.run_id
        saved_result = _saved_research_result(session, run_id)
        result = saved_result.result
        evidence = (
            session.query(ResearchEvidenceRecord)
            .filter_by(run_id=run_id)
            .order_by(ResearchEvidenceRecord.round, ResearchEvidenceRecord.evidence_id)
            .all()
        )
        traces = (
            session.query(ResearchTraceRecord)
            .filter_by(run_id=run_id)
            .order_by(ResearchTraceRecord.sequence_no)
            .all()
        )
        scheduled = (
            session.query(RunRecord)
            .filter(
                RunRecord.parent_run_id == run_id,
                RunRecord.admission_origin == "scheduled_recheck",
                RunRecord.available_at.is_not(None),
            )
            .order_by(RunRecord.available_at.asc())
            .first()
        )
        trace_views = [_trace_view(row) for row in traces]
        evidence_views = [_evidence_view(row) for row in evidence]
        fact_views = [
            _fact_view(row)
            for row in (
                session.query(ResearchFactRecord)
                .filter_by(run_id=run_id)
                .order_by(ResearchFactRecord.accepted_at, ResearchFactRecord.fact_id)
                .all()
            )
        ]
        dsh_link = session.get(DshSessionLinkRecord, run_id)
        artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        # Validate the original task/bundle once per detail projection. Its
        # coverage and round requirements share this same caller Session;
        # each new public read still revalidates the durable original rows.
        basis = self._coverage_basis_for_run(session, run)
        run_view = self._run_view(session, run, coverage_basis=basis)
        native_review_attested = _durable_native_review_matches_in_session(
            session, run_id, result
        )
        action_summary = _action_summary(
            run_view,
            artifact,
            result,
            evidence_views,
            native_review_attested=native_review_attested,
            scope_instrument=_daily_scope_instrument_in_session(session, run, basis),
            scheduled_recheck_at=as_utc(scheduled.available_at) if scheduled else None,
        )
        trace_limits = list(dict.fromkeys(
            item.error_verification for item in trace_views
            if item.error_verification is not None
        ))
        if trace_limits:
            action_summary = action_summary.model_copy(update={
                "warning_codes": list(dict.fromkeys([
                    *action_summary.warning_codes, *trace_limits,
                ])),
            })
        if saved_result.issue_code is not None:
            action_summary = action_summary.model_copy(update={
                "recommendation": "unavailable",
                "reliability": "unavailable",
                "headline": "这版已保存的研究结果暂不可核验",
                "rationale": "原研究结果的格式或保存身份不一致，请核对原始记录；"
                "页面未将其作为可用综合判断。",
                "warning_codes": [*action_summary.warning_codes, saved_result.issue_code],
            })
        fallback_coverage = _fallback_coverage(
            run,
            traces,
            basis,
            evidence_views,
            fact_views,
        )
        fallback_rounds = _fallback_rounds(
            run,
            traces,
            evidence,
            evidence_views,
            fact_views,
            fallback_coverage,
            tuple(basis.request.evidence_requirements) if basis.request is not None else (),
        )
        effective_coverage = result.final_coverage if result is not None else fallback_coverage
        display_rounds = (
            _merge_durable_tool_activity(
                result.rounds, fallback_rounds,
                timing_traces=[
                    row for row in traces
                    if row.run_id == run_id
                    and row.research_session_id == result.research_session_id
                    and row.reference_type == "dsh_tool_call"
                ],
            )
            if result is not None
            else fallback_rounds
        )
        decision_graph = build_decision_graph(
            run_view=run_view,
            evidence=evidence_views,
            causal_case=result.causal_case if result is not None else None,
            horizons=result.horizons if result is not None else [],
            action_summary=action_summary,
            coverage=effective_coverage,
            result=result,
            reviews=(result.reviews or []) if result is not None else [],
        )
        role_packets, claim_transitions = build_role_packets(
            run_id=run_id,
            rounds=display_rounds,
            evidence=evidence_views,
            coverage=effective_coverage,
            causal_case=result.causal_case if result is not None else None,
            action_summary=action_summary,
            reviews=(result.reviews or []) if result is not None else [],
            trace=trace_views,
        )
        return ResearchRunDetailView(
            schema_version="research-run-detail-view.v1",
            run=run_view,
            research_focus=_research_focus(_observation_for_event(session, run.event_id)),
            native_execution_failure=_native_execution_failure(run_id, dsh_link),
            **native_failure_projection_in_session(session, run_id),
            trigger_snapshot=_snapshot_view(
                session.get(SnapshotRecord, run.snapshot_id) if run.snapshot_id else None
            ),
            decision_snapshot=_snapshot_view(
                session.get(SnapshotRecord, run.decision_snapshot_id)
                if run.decision_snapshot_id
                else None
            ),
            evidence=evidence_views,
            rounds=display_rounds,
            causal_case=result.causal_case if result is not None else None,
            horizons=result.horizons if result is not None else [],
            trace=trace_views,
            action_summary=action_summary,
            process_steps=_process_steps(
                run_view,
                display_rounds,
                result,
                trace_views,
                action_summary,
                native_review_attested=native_review_attested,
            ),
            total_tool_calls=(
                dsh_link.tool_calls_started
                if dsh_link is not None
                else result.total_tool_calls
                if result is not None
                else _business_tool_count(trace_views)
            ),
            total_subagents=(
                result.total_subagents
                if result is not None
                else _trace_count(trace_views, "subagent_started")
            ),
            total_tokens=result.total_tokens if result is not None else None,
            estimated_cost_usd=result.estimated_cost_usd if result is not None else None,
            scheduled_recheck_at=as_utc(scheduled.available_at) if scheduled else None,
            decision_graph=decision_graph,
            context_manifest=result.context_manifest if result is not None else None,
            reviews=result.reviews if result is not None else [],
            role_packets=role_packets,
            claim_transitions=claim_transitions,
        )

    def trace_status(self, run_id: str) -> str | None:
        """Read only stream existence and lifecycle, never report authority.

        The borrowed connection closes before the caller emits or waits.
        Detail views retain their complete original validation path.
        """
        with self.database.session() as session:
            return session.scalar(
                select(RunRecord.status).where(
                    RunRecord.run_id == run_id,
                    RunRecord.strategy_version == "research.v1",
                )
            )

    def trace(self, run_id: str, *, after: int = 0) -> list[ResearchTraceEvent]:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                raise KeyError(run_id)
        return self.observability.list_trace(run_id, after=after)

    def comparison(self, run_id: str) -> ResearchRunComparisonView | None:
        """Compare a Run with the immediately preceding research version."""

        with self.database.session() as session:
            current = session.get(RunRecord, run_id)
            if current is None or current.strategy_version != "research.v1":
                return None
            current_observation = (
                session.query(ObservationRecord)
                .filter_by(event_id=current.event_id)
                .order_by(ObservationRecord.received_at.desc())
                .first()
            )
            previous_query = session.query(RunRecord).filter(
                RunRecord.strategy_version == current.strategy_version,
                RunRecord.created_at < current.created_at,
            )
            # Latest-advice submissions intentionally create a fresh event per
            # idempotency window. Compare those versions by the stable source
            # and event hint; ordinary event research remains event-scoped.
            if (
                current_observation
                and current_observation.source_id == "decision-desk-latest-advice"
            ):
                previous_query = previous_query.join(
                    ObservationRecord,
                    ObservationRecord.event_id == RunRecord.event_id,
                ).filter(
                    ObservationRecord.source_id == current_observation.source_id,
                    ObservationRecord.event_hint == current_observation.event_hint,
                )
            else:
                previous_query = previous_query.filter(RunRecord.event_id == current.event_id)
            previous = previous_query.order_by(
                RunRecord.created_at.desc(), RunRecord.run_id.desc()
            ).first()
        current_detail = self.get(run_id)
        previous_detail = self.get(previous.run_id) if previous is not None else None
        if current_detail is None:  # pragma: no cover - guarded by current lookup
            return None

        def direction(detail: ResearchRunDetailView | None) -> str | None:
            if detail is None:
                return None
            outlook = detail.action_summary.research_outlook
            if outlook is not None:
                return outlook.outlook
            return detail.horizons[0].action if detail.horizons else None

        current_graph = (
            current_detail.decision_graph.graph_status
            if current_detail.decision_graph
            else "unavailable"
        )
        previous_graph = (
            previous_detail.decision_graph.graph_status
            if previous_detail and previous_detail.decision_graph
            else None
        )
        current_direction = direction(current_detail)
        previous_direction = direction(previous_detail)
        changed: list[str] = []
        if previous_detail is None:
            changed.append("no_previous_run")
        else:
            if current_direction != previous_direction:
                changed.append("direction")
            if current_graph != previous_graph:
                changed.append("graph_status")
            if len(current_detail.evidence) != len(previous_detail.evidence):
                changed.append("evidence_count")
            if current_detail.action_summary.headline != previous_detail.action_summary.headline:
                changed.append("headline")
        return ResearchRunComparisonView(
            schema_version="research-run-comparison-view.v1",
            run_id=run_id,
            previous_run_id=previous.run_id if previous is not None else None,
            same_event=previous is not None,
            current_status=current_detail.run.status,
            previous_status=previous_detail.run.status if previous_detail else None,
            current_evidence_count=len(current_detail.evidence),
            previous_evidence_count=len(previous_detail.evidence) if previous_detail else None,
            current_graph_status=current_graph,
            previous_graph_status=previous_graph,
            current_direction=current_direction,
            previous_direction=previous_direction,
            changed_fields=changed,
            compared_at=utcnow(),
        )

    def business_status_for_signed_harness(self, run_id: str, service) -> DshBusinessStatus | None:
        """Authenticated lifecycle read, including pre-model and terminal arms.

        Current signed inputs remain mandatory. Lease/root fences belong to
        execution and report authority; requiring them here would hide failed
        arms from native activity checks. This grants neither native quiescence
        nor a Gate, coverage, Result or deployment proof.
        """
        from packages.kernel.decision_hub_kernel.application.research_harness_frozen_run import (
            STRATEGY, read_assignment, read_consumption,
        )
        from packages.contracts_py.decision_hub_contracts.generated.harness_comparison import HarnessFrozenRunCaseV1
        from packages.kernel.decision_hub_kernel.application.research_binding import FrozenResearchBundle, read_research_bundle_in_session
        from packages.kernel.decision_hub_kernel.application.research_execution_binding import read_prepared_execution_in_session
        from packages.kernel.decision_hub_kernel.application.research_harness_execution import verify_harness_method_in_session
        if service.database is not self.database:
            raise ValueError("harness_frozen_reader_database_mismatch")
        with self.database.session() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN")
            run = session.get(RunRecord, run_id, populate_existing=True)
            if run is None or run.strategy_version != STRATEGY:
                return None
            found = read_assignment(session, run_id=run_id)
            if found is None:
                raise ValueError("harness_frozen_assignment_missing")
            assignment, _ = found
            service._verify_owned(session, assignment)
            case = HarnessFrozenRunCaseV1.model_validate_json(assignment.case_json)
            if (run.event_id != case.source_event_id or run.study_id != case.source_study_id
                    or run.parent_run_id != case.source_run_id):
                raise ValueError("harness_frozen_reader_run_identity_invalid")
            link = session.get(DshSessionLinkRecord, run_id, populate_existing=True)
            if link is None:
                raise ValueError("harness_frozen_current_link_required")
            prepared = read_prepared_execution_in_session(session, run_id, link.generation)
            if (prepared.method.run_id != run_id or prepared.execution.run_id != run_id
                    or prepared.execution.generation != link.generation
                    or prepared.link.dsh_session_id != link.dsh_session_id
                    or prepared.link.request_hash != link.request_hash
                    or prepared.link.upstream_identity.plugin_build_hash != link.plugin_build_hash):
                raise ValueError("harness_frozen_current_link_mismatch")
            bundle = read_research_bundle_in_session(session, run_id)
            if not isinstance(bundle, FrozenResearchBundle) or read_consumption(session, bundle) is None:
                raise ValueError("harness_frozen_consumption_missing")
            if verify_harness_method_in_session(session, bundle) is None:
                raise ValueError("harness_method_v4_required")
            status = "queued" if run.status == "dispatched" else _view_status(run, None)
            if status not in {"queued", "researching", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"}:
                raise ValueError("harness_frozen_business_status_invalid")
            return DshBusinessStatus.model_validate({
                "schema_version": "dsh-business-status.v1", "status": status,
                "gate_status": None, "coverage_status": None, "hard_coverage_ratio": None,
                "stop_reason_code": run.error_code, "stop_reason_detail": None, "failures": [],
            })

    def business_status(
        self, run_id: str, *, lifecycle_only: bool = False,
    ) -> DshBusinessStatus | None:
        """Project the Hub-owned Gate and research facts for the DSH client slot."""
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                return None
            if lifecycle_only:
                # Host activity only needs the current durable lifecycle. This
                # conveys no report/Gate/coverage or physical-quiescence authority.
                status = "queued" if run.status == "dispatched" else _view_status(run, None)
                return DshBusinessStatus.model_validate({
                    "schema_version": "dsh-business-status.v1", "status": status,
                    "gate_status": None, "coverage_status": None, "hard_coverage_ratio": None,
                    "stop_reason_code": (
                        run.error_code if status in {"failed", "cancelled"} else None
                    ),
                    "stop_reason_detail": None, "failures": [],
                })
            result_row = session.get(ResearchResultRecord, run_id)
            result = (
                ResearchSessionResult.model_validate_json(result_row.payload_json)
                if result_row is not None
                else None
            )
            artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
            failures: list[dict[str, object]] = []
            seen: set[tuple[str, str]] = set()
            if result is not None:
                for round_item in result.rounds:
                    for invocation in round_item.tool_invocations:
                        if invocation.status not in {"failed", "denied", "timed_out"}:
                            continue
                        error_code = (
                            (invocation.error.error_code if invocation.error else None)
                            or invocation.error_code
                            or "capability_failed"
                        )
                        key = (invocation.capability_id, error_code)
                        if key in seen:
                            continue
                        seen.add(key)
                        failures.append(
                            {
                                "capability_id": invocation.capability_id,
                                "error_code": error_code,
                                "origin": (
                                    invocation.error.origin if invocation.error else "orchestration"
                                ),
                                "cause_code": (
                                    invocation.error.cause_code if invocation.error else None
                                ),
                                "retryable": (
                                    invocation.error.retryable if invocation.error else False
                                ),
                            }
                        )
            trace_rows = (
                session.query(ResearchTraceRecord)
                .filter_by(run_id=run.run_id)
                .order_by(ResearchTraceRecord.sequence_no.asc())
                .all()
            )
            fallback_evidence = [
                _evidence_view(item)
                for item in (
                    session.query(ResearchEvidenceRecord)
                    .filter_by(run_id=run.run_id)
                    .order_by(
                        ResearchEvidenceRecord.round,
                        ResearchEvidenceRecord.evidence_id,
                    )
                    .all()
                )
            ]
            fallback_facts = [
                _fact_view(item)
                for item in (
                    session.query(ResearchFactRecord)
                    .filter_by(run_id=run.run_id)
                    .order_by(ResearchFactRecord.accepted_at, ResearchFactRecord.fact_id)
                    .all()
                )
            ]
            fallback_coverage = _fallback_coverage(
                run,
                trace_rows,
                self._coverage_basis_for_run(session, run),
                fallback_evidence,
                fallback_facts,
            )
            for trace_failure in _trace_failures(run, trace_rows):
                capability_id = trace_failure.capability_id or "research.runtime"
                key = (capability_id, trace_failure.error_code)
                if key in seen:
                    continue
                seen.add(key)
                failures.append(_failure_payload(trace_failure, capability_id))
            status = _view_status(run, artifact)
            return DshBusinessStatus.model_validate(
                {
                    "schema_version": "dsh-business-status.v1",
                    "status": status,
                    "gate_status": artifact.gate_status if artifact is not None else None,
                    # A missing final result is an incomplete, conservative
                    # state. It must remain visible as insufficient rather
                    # than looking like an empty/unknown successful run.
                    "coverage_status": (
                        result.final_coverage.status
                        if result is not None
                        else fallback_coverage.status
                        if fallback_coverage is not None
                        else None
                    ),
                    "hard_coverage_ratio": (
                        result.final_coverage.hard_coverage_ratio
                        if result is not None
                        else fallback_coverage.hard_coverage_ratio
                        if fallback_coverage is not None
                        else None
                    ),
                    "stop_reason_code": (
                        result.stop_reason.code
                        if result is not None
                        else (run.error_code if status in {"failed", "cancelled"} else None)
                    ),
                    "stop_reason_detail": (
                        result.stop_reason.detail
                        if result is not None
                        else _latest_failure_summary(trace_rows, run=run)
                    ),
                    "failures": failures[:16],
                }
            )

    def _run_view(
        self, session: Session, run: RunRecord,
        *, coverage_basis: SavedCoverageBasis | None = None,
        _read_rows: _RunViewReadRows | None = None,
    ) -> ResearchRunView:
        if _read_rows is not None and _read_rows.run_id != run.run_id:
            raise ValueError("research_run_view_read_identity_mismatch")
        result = (_read_rows.saved if _read_rows is not None else (
            _saved_research_result(session, run.run_id)
        )).result
        if _read_rows is not None:
            trace_rows = _read_rows.traces
            latest_trace = trace_rows[-1] if trace_rows else None
        else:
            traces = session.query(ResearchTraceRecord).filter_by(run_id=run.run_id)
            if result is not None and run.artifact_id and run.status in {"completed", "degraded"} \
                    and not run.error_code:
                # Failure projection is empty for these saved reports; only the latest
                # trace supplies the list stage/action. Full detail/audit keeps all events.
                latest_trace = traces.order_by(ResearchTraceRecord.sequence_no.desc()).first()
                trace_rows = [latest_trace] if latest_trace is not None else []
            else:
                trace_rows = traces.order_by(ResearchTraceRecord.sequence_no.asc()).all()
                latest_trace = trace_rows[-1] if trace_rows else None
        trace_views = [_trace_view(item) for item in trace_rows] if result is None else []
        latest_sequence = latest_trace.sequence_no if latest_trace is not None else 0
        observation = _read_rows.observation if _read_rows is not None else (
            session.query(ObservationRecord)
            .filter_by(event_id=run.event_id)
            .order_by(ObservationRecord.received_at.asc())
            .first()
        )
        artifact = _read_rows.artifact if _read_rows is not None else (
            session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        )
        fallback_coverage = fallback_stop_reason = None
        basis = coverage_basis if coverage_basis is not None else (
            self._coverage_basis_for_run(session, run)
        )
        submitted = self._submission_basis_for_run(session, run)
        if result is None:
            fallback_evidence = [
                _evidence_view(item)
                for item in (_read_rows.evidence if _read_rows is not None else (
                    session.query(ResearchEvidenceRecord)
                    .filter_by(run_id=run.run_id)
                    .order_by(ResearchEvidenceRecord.round, ResearchEvidenceRecord.evidence_id)
                    .all()
                ))
            ]
            fallback_facts = [
                _fact_view(item)
                for item in (_read_rows.facts if _read_rows is not None else (
                    session.query(ResearchFactRecord)
                    .filter_by(run_id=run.run_id)
                    .order_by(ResearchFactRecord.accepted_at, ResearchFactRecord.fact_id)
                    .all()
                ))
            ]
            fallback_coverage = _fallback_coverage(
                run,
                trace_rows,
                basis,
                fallback_evidence,
                fallback_facts,
            )
            fallback_stop_reason = _fallback_stop_reason(run, trace_rows, fallback_coverage)
        return ResearchRunView.model_validate(
            {
                "schema_version": "research-run-view.v2",
                "run_id": run.run_id,
                "event_id": run.event_id,
                "event_title": _event_title(observation),
                "admission_origin": run.admission_origin,
                "priority": _priority(run.priority),
                "status": _view_status(run, artifact),
                "stage": _stage(run, latest_trace),
                "runtime_id": (
                    result.runtime_id
                    if result is not None
                    else (_observed_runtime_id_from_link(_read_rows.link, trace_rows)
                          if _read_rows is not None else (
                              _observed_runtime_id(session, run.run_id, trace_rows)
                          ))
                ),
                "profile_ref": (
                    result.profile_ref if result is not None else (
                        submitted.request.role_profile_ref if submitted.request else None
                    )
                ),
                "research_profile": research_profile_for_study(
                    session.get(StudyRecord, run.study_id) if run.study_id else None
                ),
                "current_round": (
                    len(result.rounds) if result is not None else _observed_round(trace_views)
                ),
                "budget": (
                    _display_execution_budget(submitted.request.execution_budget)
                    if submitted.request else None
                ),
                "budget_basis": submitted.status,
                "coverage": result.final_coverage if result is not None else fallback_coverage,
                "coverage_basis": "saved_result" if result is not None else basis.status,
                "coverage_cutoff_at": basis.cutoff_at if basis else None,
                "current_action": latest_trace.summary if latest_trace is not None else None,
                "stop_reason": result.stop_reason if result is not None else fallback_stop_reason,
                "failure": _failure_view(run, trace_rows),
                "latest_sequence_no": latest_sequence,
                "artifact_id": run.artifact_id,
                "available_at": as_utc(run.available_at),
                "parent_run_id": run.parent_run_id,
                "created_at": as_utc(run.created_at),
                "updated_at": as_utc(run.updated_at),
            }
        )



def _display_execution_budget(
    budget: ExecutionBudget | ExecutionBudgetV2,
) -> ExecutionBudget:
    """Keep the public v2 view ABI; frozen Task policy remains authoritative."""
    return ExecutionBudget.model_validate({
        name: getattr(budget, name) for name in ExecutionBudget.model_fields
    })


def _saved_research_result(session: Session, run_id: str) -> _SavedResearchResult:
    """Reject only broken saved result identities; database failures still propagate."""
    row = session.get(ResearchResultRecord, run_id)
    return _validated_saved_research_result(row, run_id)


def _validated_saved_research_result(
    row: ResearchResultRecord | None, run_id: str,
) -> _SavedResearchResult:
    """Validate each freshly read Result with the original hash/schema/parser rules."""
    if row is None:
        return _SavedResearchResult(None)
    if row.run_id != run_id or row.schema_version != "research-session-result.v1":
        return _SavedResearchResult(None, "saved_research_result_schema_invalid")
    if hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest() != row.payload_hash:
        return _SavedResearchResult(None, "saved_research_result_hash_mismatch")
    try:
        result = ResearchSessionResult.model_validate_json(row.payload_json)
    except ValidationError:
        return _SavedResearchResult(None, "saved_research_result_payload_invalid")
    return _SavedResearchResult(result)


def _home_report_preview(
    run: ResearchRunView,
    artifact: ArtifactRecord,
    saved: _SavedResearchResult,
    decision_snapshot: SnapshotRecord | None,
    *,
    perpetual_publication_verified: bool | None = None,
) -> ResearchHomeReportPreview:
    """Preview saved report windows, preserving the old headline as reference text.

    These read checks grant neither publication nor execution. In particular the
    historical business cutoff is used, never today's clock or result finish time.
    """
    result = saved.result
    kind: Literal["synthesis", "perpetual", "legacy", "unverified", "unavailable"]
    if artifact.run_id != run.run_id or artifact.event_id != run.event_id:
        kind = "unverified"
        main = "已保存的报告与研究任务身份不一致，综合判断暂不可核验。"
    elif run.status in {"failed", "rejected", "cancelled"} or artifact.gate_status == "reject":
        kind = "unavailable"
        main = "这份报告未通过审核或研究已终止，不能作为可用的综合判断。"
    elif artifact.gate_status not in {"publish", "research_only", "degraded"}:
        kind = "unverified"
        main = "旧版本报告：发布状态不可核验，原始内容仅作参考。"
    elif saved.issue_code is not None:
        kind = "unverified"
        main = "已保存的研究结果暂不可核验，原始内容仅作参考。"
    elif run.research_profile == "perpetual_decision_v2":
        if perpetual_publication_verified:
            kind = "perpetual"
            main = "已保存永续研究，请打开原报告核对结论、场景概率、独立审核与适用限制。"
        else:
            kind = "unverified"
            main = "已保存永续研究记录，但发布引用暂不可核验；请打开报告页核对原件。"
    elif result is None:
        kind = "legacy"
        main = "旧版本报告：未保存可核对的综合判断，原始内容仅作参考。"
    elif (
        result.status not in {"completed", "degraded"}
        or result.synthesis_failure_code is not None
    ):
        kind = "unavailable"
        main = "本次研究未形成可核对的综合判断，原始内容仅作参考。"
    elif result.causal_case is None or not result.causal_case.thesis.strip():
        kind = "unavailable"
        main = "这版报告未形成完整的综合研究解释，原始内容仅作参考。"
    elif (
        decision_snapshot is None
        or decision_snapshot.run_id != run.run_id
        or decision_snapshot.event_id != run.event_id
        or decision_snapshot.snapshot_type != "decision"
    ):
        kind = "unverified"
        main = "旧版本报告：三窗口判断的原截止时间不可核验，原始内容仅作参考。"
    elif not validate_horizon_contract(
        result.horizons, reference_at=_required_time(as_utc(decision_snapshot.cutoff_at)),
    ).valid:
        kind = "unverified"
        main = "这版报告的三窗口判断不完整或不可核验，原始内容仅作参考。"
    else:
        kind = "synthesis"
        horizon_labels = {"30m": "30 分钟", "24h": "24 小时", "72h": "72 小时"}
        direction_labels = {
            "long": "偏多", "short": "偏空", "neutral": "中性观察", "no_trade": "未形成方向",
        }
        # Preserve the report's saved order, directions and full conditions.
        windows = "；".join(
            f"{horizon_labels[item.horizon]}：{direction_labels[item.action]}"
            for item in result.horizons
        )
        main = f"本次 AI 综合判断：{windows}。仅供研究；确认与失效条件见报告。"
    return ResearchHomeReportPreview(
        run_id=run.run_id, artifact_id=artifact.artifact_id, kind=kind, text=main,
    )


def _perpetual_home_publication_reference_verified(
    run: ResearchRunView,
    artifact: ArtifactRecord,
    source: RunRoleOutputRecord | None,
) -> bool | None:
    """Verify a cheap V5 publication reference without replaying archived originals."""
    from packages.kernel.decision_hub_kernel.application.commit import _artifact_body
    from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
        observation_sha,
    )

    if run.research_profile != "perpetual_decision_v2":
        return None
    if (
        source is None
        or source.run_id != run.run_id
        or source.role != "research_publication_source"
        or source.schema_version != "research-publication-source.v5"
        or source.payload_hash
        != hashlib.sha256(source.payload_json.encode("utf-8")).hexdigest()
        or source.output_id != "publication_" + source.payload_hash[:40]
    ):
        return False
    try:
        body = json.loads(source.payload_json)
    except (TypeError, ValueError):
        return False
    return bool(
        isinstance(body, dict)
        and body.get("schema_version") == "research-publication-source.v5"
        and body.get("run_id") == run.run_id
        and body.get("event_id") == run.event_id
        and body.get("artifact_id") == artifact.artifact_id
        and body.get("artifact_hash") == observation_sha(_artifact_body(artifact))
        and artifact.run_id == run.run_id
        and artifact.event_id == run.event_id
    )


def _action_summary(
    run: ResearchRunView,
    artifact: ArtifactRecord | None,
    result: ResearchSessionResult | None,
    evidence: list[EvidenceCandidate],
    *,
    native_review_attested: bool = False,
    scope_instrument: str | None = None,
    scheduled_recheck_at: datetime | None,
) -> ResearchActionSummary:
    """Build the money-safe user conclusion from durable facts, never model authority."""

    accepted = [
        item for item in evidence if item.quality == "accepted" and item.freshness_status == "fresh"
    ]
    stale = [
        item for item in evidence if item.quality == "stale" or item.freshness_status == "stale"
    ]
    rejected = [item for item in evidence if item.quality in {"rejected", "conflicted"}]
    coverage = run.coverage
    gaps = list(coverage.gaps) if coverage is not None else []
    # The field is consumed by the user-facing "blocked" callout.  Soft
    # contextual gaps must remain visible in diagnostics, but calling them
    # blocking contradicts the deterministic coverage decision and makes a
    # completed report look as if it failed the evidence Gate.
    blocking_gap_ids = list(
        dict.fromkeys(
            item.requirement_id
            for item in gaps
            if item.importance == "hard" or item.blocks_directional_output
        )
    )
    supplemental_gap_ids = list(
        dict.fromkeys(
            item.requirement_id for item in gaps if item.requirement_id not in blocking_gap_ids
        )
    )
    hard_conflict = bool(coverage and any(item.severity == "hard" for item in coverage.conflicts))
    reviews = list(result.reviews or []) if result is not None else []
    accepted_review = bool(
        result
        and has_valid_accepted_independent_review(
            result, native_provenance_verified=native_review_attested
        )
    )
    automated_check_only = _automated_check_only(reviews)
    unresolved_review = any(
        item.resolution in {"bounded", "requires_new_evidence", "rejected"} for item in reviews
    )
    horizons = list(result.horizons) if result is not None else []
    uncalibrated = any(item.probability_status != "calibrated" for item in horizons)
    all_non_directional = bool(horizons) and all(
        item.action in {"neutral", "no_trade"} for item in horizons
    )

    artifact_status: Literal["pending", "saved", "limited", "rejected"]
    if artifact is None:
        artifact_status = (
            "rejected" if run.status in {"failed", "rejected", "cancelled"} else "pending"
        )
    elif artifact.gate_status == "publish":
        artifact_status = "saved"
    elif artifact.gate_status == "reject":
        artifact_status = "rejected"
    else:
        artifact_status = "limited"

    data_status: Literal["insufficient", "partial", "sufficient"]
    if coverage is None or coverage.hard_coverage_ratio < 1 or coverage.status != "sufficient":
        data_status = "insufficient"
    elif gaps:
        data_status = "partial"
    else:
        data_status = "sufficient"

    warning_codes: list[str] = []
    if run.failure is not None:
        warning_codes.append(run.failure.error_code)
    if result is not None and result.synthesis_failure_code:
        warning_codes.append(result.synthesis_failure_code)
    if result is not None and result.stop_reason.code != "sufficient":
        warning_codes.append(f"stop:{result.stop_reason.code}")
    warning_codes.extend(f"gap:{item.requirement_id}:{item.reason_code}" for item in gaps)
    if horizons and uncalibrated:
        warning_codes.append("probability_uncalibrated")
    if horizons and (uncalibrated or all_non_directional):
        warning_codes.append("directional_value_unvalidated")
    if result is not None and not accepted_review:
        warning_codes.append(
            "independent_review_not_run"
            if automated_check_only
            else "independent_review_not_accepted"
        )
    if automated_check_only:
        warning_codes.append("automated_evidence_check_bounded")
    warning_codes.extend(
        f"review:{item.resolution}" for item in reviews if item.resolution != "accepted"
    )
    warning_codes = list(dict.fromkeys(warning_codes))

    # A daily strategy receipt is an independently attested *research view*.
    # It must be projected before the execution gate is evaluated.  Previously
    # this projection happened at the end of the function, after every
    # incomplete review/data condition had already selected the generic
    # ``do_not_trade`` headline.  That made a real direction (for example a
    # 7-day bullish daily baseline) look as if no direction had been formed.
    # Keep the execution gate fail-closed, but let the query view report the
    # directional research result and its limitations separately.
    accepted_ids = {item.evidence_id for item in accepted}
    accepted_facts = (
        [fact for fact in (result.facts or []) if fact.evidence_id in accepted_ids]
        if result is not None else []
    )
    research_outlook = _research_outlook_from_facts(
        accepted_facts, scope_instrument=scope_instrument
    )
    unbound_daily = research_outlook is None and any(
        fact.attributes.get("daily_strategy_receipt") is not None
        or fact.attributes.get("daily_strategy_receipt_json") is not None
        for fact in accepted_facts
    )
    if unbound_daily:
        warning_codes.append(
            "daily_outlook_asset_mismatch" if scope_instrument
            else "daily_outlook_scope_unverified"
        )
    has_directional_outlook = bool(
        research_outlook is not None and research_outlook.outlook != "unresolved"
    )
    if research_outlook is not None and gaps:
        # Preserve the baseline direction while making the effect of missing
        # evidence explicit.  A missing derivatives feed limits leverage and
        # crowding claims; it does not erase an independently computed daily
        # price-rule outlook.
        gap_impacts = {
            "derivatives_crowding": (
                "衍生品与盘口组合核验未全部通过：已取得的有效事实仍可引用，不支持杠杆或追涨判断"
            ),
            "macro_transmission": "宏观传导未核验：方向尚未纳入利率/美元预期差",
            "cross_asset_confirmation": "跨资产确认未核验：不能把日频方向升级为事件交易信号",
            "market_narrative": "公开叙事未核验：反方信息覆盖有限",
            "crypto_spot_confirmation": (
                "现货交叉确认不足：日线规则仅作背景，当前报价和行动候选待核验"
            ),
        }
        missing_impact = list(research_outlook.missing_impact)
        for gap in gaps:
            impact = gap_impacts.get(gap.requirement_id)
            if impact and impact not in missing_impact:
                missing_impact.append(impact)
        research_outlook = research_outlook.model_copy(update={"missing_impact": missing_impact})

    fatal = (
        run.status in {"failed", "rejected", "cancelled"}
        or artifact_status == "rejected"
        or (result is not None and result.synthesis_failure_code is not None)
    )
    blocked = (
        data_status == "insufficient"
        or hard_conflict
        or (result is not None and not accepted_review)
        or unresolved_review
        or artifact_status == "limited"
    )
    if fatal:
        recommendation = "unavailable"
        headline = "无法形成可靠判断，不应据此交易"
        reliability = "unavailable"
    elif result is None and artifact is None:
        recommendation = "unavailable"
        headline = "研究进行中，暂不能形成判断"
        reliability = "unavailable"
    elif has_directional_outlook:
        # ``manual_review_required`` is intentionally used as the existing
        # contract value for "a direction exists, but the execution gate is
        # closed".  It is not an order signal and trade_authorized remains
        # the literal false value enforced by the contract.
        outlook_label = {
            "bullish": "偏多",
            "bearish": "偏空",
            "range": "区间/趋势分歧",
        }.get(research_outlook.outlook if research_outlook else "", "已形成")
        recommendation = "manual_review_required"
        headline = f"7日价格规则倾向：{outlook_label}；交易权限未授权"
        reliability = "limited" if blocked or gaps else "reviewable"
    elif blocked:
        recommendation = "do_not_trade"
        headline = "证据或复核未通过，不应据此交易"
        reliability = "low"
    elif uncalibrated or all_non_directional or gaps:
        recommendation = "wait_for_confirmation"
        headline = "研究报告已完成，当前未形成经过验证的方向优势"
        reliability = "limited" if accepted_review else "low"
    else:
        recommendation = "manual_review_required"
        headline = "方向情景已形成，仍需人工复核"
        reliability = "reviewable"

    if fatal:
        rationale_lead = "报告没有通过合成或保存检查。"
    elif result is None and artifact is None:
        rationale_lead = "研究仍在运行，等待可核验的证据、复核和确定性检查。"
    elif has_directional_outlook and research_outlook is not None:
        outlook_label = {
            "bullish": "偏多",
            "bearish": "偏空",
            "range": "区间/趋势分歧",
        }.get(research_outlook.outlook, "已形成")
        rationale_lead = (
            f"已形成未来7日的日频价格规则观点（{outlook_label}），"
            "它是可复算的基线，不是经过样本外校准的交易概率；交易权限仍关闭。"
        )
    elif result is not None and result.stop_reason.code == "no_progress":
        rationale_lead = "连续两轮没有新增可接受证据或充分度变化，研究已按无增益规则停止。"
    elif result is not None and result.stop_reason.code == "no_actionable_path":
        rationale_lead = "剩余根因或 reviewer 缺口没有可执行的已声明数据路径。"
    elif result is not None and result.stop_reason.code == "synthesis_incomplete":
        rationale_lead = (
            "证据覆盖虽已达到门槛，但根因链或 30m/24h/72h 输出不完整，不能形成可靠判断。"
        )
    elif result is not None and result.stop_reason.code == "mixed":
        rationale_lead = "关键证据仍相互冲突，系统保留 mixed 结论并停止扩大检索。"
    elif coverage is None:
        rationale_lead = "原始证据要求无法核验，暂不能确认保留材料的覆盖程度。"
    elif data_status == "insufficient":
        rationale_lead = "必需市场证据尚未收齐。"
    elif artifact_status == "limited":
        rationale_lead = "必需市场证据检查通过，但报告仍受数据或复核限制。"
    elif all_non_directional:
        rationale_lead = (
            "必需市场证据和独立复核已经通过，但当前只有未校准的模型判断，没有样本外方向校准器。"
        )
    elif gaps:
        rationale_lead = "必需市场证据检查通过，仍有不阻断报告的补充信息缺口。"
    else:
        rationale_lead = "证据、复核和报告检查已经完成。"
    rationale_parts = [
        rationale_lead,
        "日线参考未与原任务资产核对，未作为本次方向" if unbound_daily else None,
        (
            "仍缺少：" + "、".join(_requirement_label(item) for item in blocking_gap_ids)
            if blocking_gap_ids
            else None
        ),
        (
            "补充信息缺口：" + "、".join(_requirement_label(item) for item in supplemental_gap_ids)
            if supplemental_gap_ids
            else None
        ),
        "概率未校准，不能用于仓位决策" if uncalibrated else None,
        (
            "仅完成自动证据检查，尚无可验证的 reviewer 回执"
            if automated_check_only
            else "reviewer 审查仍有保留意见"
            if result is not None and not accepted_review
            else None
        ),
    ]
    rationale = "；".join(str(item).strip().rstrip("。；; ") for item in rationale_parts if item)
    if not rationale:
        rationale = "研究仍在运行，等待可核验的 Evidence、复核和 Gate 结果。"
    else:
        rationale += "。"

    result_refs: list[str] = []
    if result is not None and result.causal_case is not None:
        result_refs.extend(result.causal_case.evidence_refs)
    if result is not None:
        result_refs.extend(ref for item in result.horizons for ref in item.evidence_refs)
    supporting_refs = list(dict.fromkeys(ref for ref in result_refs if ref in accepted_ids))
    # Report suggestions come from the saved report. A child's admission time
    # is execution history, including manual commands; durable review plans
    # supply their own trigger_at through the separate review-plans projection.
    # Keep the scheduled_recheck_at argument for existing projection callers.
    review_times = [item.next_review_at for item in horizons]

    # Native v1 decision views.  The legacy ``research_outlook`` remains in
    # the payload for compatibility, but these fields are the durable semantic
    # boundary: a market direction may exist while the execution view stays
    # blocked/manual-review.  Do not derive a direction from an untrusted model
    # horizon when no accepted daily strategy receipt exists.
    market_view: ResearchMarketView | None = None
    if research_outlook is not None:
        market_basis = (
            "calibrated"
            if research_outlook.probability_status == "calibrated"
            else "rule_based"
            if research_outlook.basis == "rule_based"
            else "composite"
        )
        market_view = ResearchMarketView(
            schema_version="market-view.v1",
            horizon="7d",
            direction=research_outlook.outlook,
            basis=market_basis,
            rationale=research_outlook.rationale,
            evidence_refs=list(research_outlook.evidence_refs),
            probability=research_outlook.probability,
            probability_status=research_outlook.probability_status,
            action_candidate=research_outlook.action_candidate,
            counter_case=research_outlook.counter_case,
            invalidation=research_outlook.invalidation,
            missing_impact=list(research_outlook.missing_impact),
        )
    if market_view is None:
        missing_impact = [
            f"{_requirement_label(item.requirement_id)}：{item.reason_code}" for item in gaps
        ]
        market_view = ResearchMarketView(
            schema_version="market-view.v1",
            horizon="7d",
            direction="unresolved",
            basis="composite",
            rationale=(
                "当前证据链没有形成可验证的方向性优势；保留已采纳来源和开放缺口，"
                "不把缺失数据解释成看多或看空。"
            ),
            evidence_refs=list(accepted_ids),
            probability=None,
            probability_status="unavailable",
            action_candidate="不形成交易方向，等待新的可核验证据",
            counter_case="相反方向与主要传导链尚未完成独立证据复核。",
            invalidation="任何新一轮证据、来源冲突或截止时间变化都需要重新建立快照。",
            missing_impact=missing_impact,
        )

    execution_gap_ids = list(
        dict.fromkeys(
            item.requirement_id
            for item in gaps
            if item.importance == "hard"
            or item.blocks_directional_output
            or item.requirement_id in _EXECUTION_GAP_REQUIREMENTS
        )
    )
    execution_reasons: list[str] = []
    trade_authorized = False
    if not trade_authorized:
        execution_reasons.append("交易权限未授权")
    if execution_gap_ids:
        execution_reasons.extend(
            f"缺少执行级证据：{_requirement_label(item)}" for item in execution_gap_ids
        )
    if uncalibrated:
        execution_reasons.append("概率未校准，不能升级为仓位或杠杆建议")
    if result is not None and not accepted_review:
        execution_reasons.append("独立 reviewer 尚未形成可验证的 accepted 回执")
    execution_reasons = list(dict.fromkeys(execution_reasons))
    execution_action = (
        research_outlook.action_candidate
        if research_outlook is not None
        else "当前没有可执行的方向候选"
    )
    execution_status = (
        "blocked"
        if fatal or data_status == "insufficient" or execution_gap_ids
        else "manual_review"
    )
    execution_view = ResearchExecutionView(
        schema_version="execution-view.v1",
        status=execution_status,
        action_candidate=execution_action,
        reasons=execution_reasons or ["仍需人工复核，当前不授予执行权限"],
        blocking_gap_ids=execution_gap_ids,
        trade_authorized=trade_authorized,
    )

    return ResearchActionSummary(
        schema_version="research-action-summary.v1",
        recommendation=recommendation,
        headline=headline,
        rationale=_bounded_view_text(rationale, 2000),
        reliability=reliability,
        trade_authorized=False,
        artifact_status=artifact_status,
        data_status=data_status,
        accepted_evidence_count=len(accepted),
        stale_evidence_count=len(stale),
        rejected_evidence_count=len(rejected),
        blocking_gap_ids=blocking_gap_ids,
        warning_codes=warning_codes,
        supporting_evidence_refs=supporting_refs,
        next_review_at=min(review_times) if review_times else None,
        research_outlook=research_outlook,
        market_view=market_view,
        execution_view=execution_view,
    )


def _daily_scope_instrument_in_session(
    session: Session, run: RunRecord, basis: SavedCoverageBasis,
) -> str | None:
    """Use the same read's validated immutable Task, never today's title/Study."""
    if basis.status != "saved_task_request" or basis.request is None:
        return None
    from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
        ResearchTaskReceiptV2,
    )
    from packages.kernel.decision_hub_kernel.application.research_binding import (
        TASK_ROLE, _read_receipt,
    )

    row = session.scalar(select(RunRoleOutputRecord).filter_by(run_id=run.run_id, role=TASK_ROLE))
    if row is None or row.schema_version != "research-task-request.v2":
        return None
    try:
        task = _read_receipt(row, TASK_ROLE, ResearchTaskReceiptV2)
        if (
            task.run_id != run.run_id or task.scope.event_id != run.event_id
            or task.scope.study_id != run.study_id
            or task.request_hash != basis.reference
            or task.request.model_dump(mode="json") != basis.request.model_dump(mode="json")
            or task.scope.market != "crypto"
            or task.scope.instrument != task.factory_source.study.instrument
            or task.factory_source.study.identity_basis == "legacy_unknown"
        ):
            return None
        return task.scope.instrument
    except (ValueError, KeyError, TypeError):
        return None


def _research_outlook_from_facts(
    facts: Iterable[FactEnvelope], *, scope_instrument: str | None = None,
) -> ResearchOutlook | None:
    """Project an attested daily strategy receipt without inventing a forecast.

    The strategy adapter may attach the receipt to a typed Fact's attributes.
    Invalid or absent receipts stay invisible; the query layer must never build
    a direction from a missing value or from a model-authored probability.
    """

    if not scope_instrument or scope_instrument == "unknown":
        return None
    aliases = {"BTCUSDT": "BTC-USDT", "ETHUSDT": "ETH-USDT"}
    expected = aliases.get(scope_instrument, scope_instrument)
    for fact in facts:
        if fact.quality != "accepted" or aliases.get(fact.instrument, fact.instrument) != expected:
            continue
        attributes = fact.attributes
        raw = attributes.get("daily_strategy_receipt")
        if raw is None:
            raw = attributes.get("daily_strategy_receipt_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if not isinstance(raw, dict):
            continue
        try:
            receipt = ResearchStrategyReceipt.model_validate(raw)
        except Exception:
            continue
        refs = list(dict.fromkeys(receipt.evidence_refs or [fact.evidence_id]))
        if refs != [fact.evidence_id]:
            continue
        missing_impact = (
            ["日线由已归档小时线聚合，最新覆盖可能滞后；只作为日频背景"]
            if attributes.get("derived_from_hourly") is True
            else []
        )
        if (
            attributes.get("derived_from_hourly") is True
            and receipt.strategy_version == "daily-trend-v0"
        ):
            missing_impact.append(
                "旧版聚合未校验每日24根小时线；此历史报告的收盘和阈值待重算，不作为当前建议"
            )
        return ResearchOutlook(
            schema_version="research-outlook.v1",
            horizon="7d",
            outlook=receipt.outlook,
            basis=(
                "rule_based"
                if receipt.strategy_version.startswith("daily-trend-")
                else "statistical"
            ),
            action_candidate=receipt.action_candidate,
            trigger=receipt.trigger,
            counter_case=receipt.counter_case,
            rationale=receipt.rationale,
            invalidation=receipt.invalidation,
            evidence_refs=refs,
            missing_impact=missing_impact,
            probability=receipt.probability,
            probability_status=receipt.probability_status,
            strategy_status="baseline_only"
            if receipt.probability_status != "calibrated"
            else "calibrated",
            execution_permission=False,
            strategy_receipt=receipt,
        )
    return None


def _requirement_label(requirement_id: str) -> str:
    return {
        "macro_transmission": "实时宏观传导数据",
        "cross_asset_confirmation": "实时跨资产确认",
        "crypto_spot_confirmation": "双交易所现货确认",
        "derivatives_crowding": "衍生品与拥挤度确认",
        "market_regime_context": "日线趋势背景",
    }.get(requirement_id, requirement_id)


def _automated_check_only(reviews: list[ResearchReview]) -> bool:
    return bool(reviews) and all(
        review.review_id.startswith("review:")
        and any("Automated evidence check only" in item for item in review.limitations)
        for review in reviews
    )


def _durable_native_review_matches(
    database: Database,
    run_id: str,
    result: ResearchSessionResult | None,
) -> bool:
    if result is None or result.reviews is None or len(result.reviews) != 1:
        return False
    with database.session() as session:
        return _durable_native_review_matches_in_session(session, run_id, result)


def _durable_native_review_matches_in_session(
    session: Session,
    run_id: str,
    result: ResearchSessionResult | None,
) -> bool:
    """Verify the original review using the caller's already held connection."""
    if result is None or result.reviews is None or len(result.reviews) != 1:
        return False
    row = (
        session.query(RunRoleOutputRecord)
        .filter_by(run_id=run_id, role="independent_reviewer")
        .populate_existing()
        .first()
    )
    if row is None or (
        row.run_id != run_id
        or row.role != "independent_reviewer"
        or row.schema_version != "research-review.native-attested.v1"
        or row.payload_hash != hashlib.sha256(row.payload_json.encode("utf-8")).hexdigest()
    ):
        return False
    try:
        payload = json.loads(row.payload_json)
    except (ValueError, TypeError):
        return False
    return payload == result.reviews[0].model_dump(mode="json")


def _process_steps(
    run: ResearchRunView,
    rounds: list[ResearchRoundView],
    result: ResearchSessionResult | None,
    trace: list[ResearchTraceEvent],
    action: ResearchActionSummary,
    *,
    native_review_attested: bool = False,
) -> list[ResearchProcessStep]:
    terminal = run.status in {
        "completed",
        "degraded",
        "research_only",
        "rejected",
        "failed",
        "cancelled",
    }
    invocations = [item for round_item in rounds for item in round_item.tool_invocations]
    tool_refs = [item.tool_call_id for item in invocations]
    checkpoint_refs = checkpoint_trace_call_ids(
        trace, run_id=run.run_id,
        session_id=result.research_session_id if result is not None else None,
    )
    checkpoint_refs = [ref for ref in checkpoint_refs if any(
        item.tool_call_id == ref and item.status == "failed" and item.error is not None
        and item.error.tool_call_id == ref and item.error.error_code == "dsh_tool_failed"
        and item.error.cause_code == "decision_hub_synthesis_submit" for item in invocations
    )]
    tool_warnings = list(
        dict.fromkeys(
            (item.error.error_code if item.error else item.error_code or "capability_failed")
            for item in invocations
            if item.status in {"failed", "denied", "timed_out"}
        )
    )
    new_evidence = list(
        dict.fromkeys(ref for round_item in rounds for ref in round_item.new_evidence_refs)
    )
    coverage = run.coverage
    reviews = list(result.reviews or []) if result is not None else []
    replan_events = [item for item in trace if item.event_type == "replan"]
    round_delta_events = [item for item in trace if item.event_type == "round_completed"]
    accepted_review = bool(
        result
        and has_valid_accepted_independent_review(
            result, native_provenance_verified=native_review_attested
        )
    )
    automated_check_only = _automated_check_only(reviews)

    plan_status = (
        "completed"
        if rounds
        else "failed"
        if terminal
        else "running"
        if run.stage == "planning"
        else "waiting"
    )
    acquire_status = (
        "limited"
        if rounds and tool_warnings
        else "completed"
        if rounds
        else "failed"
        if terminal
        else "running"
        if run.stage == "acquiring_evidence"
        else "waiting"
    )
    assess_status = (
        "limited"
        if coverage is not None and coverage.gaps
        else "completed"
        if coverage is not None
        else "failed"
        if terminal
        else "running"
        if run.stage == "assessing_sufficiency"
        else "waiting"
    )
    review_status = (
        "completed" if accepted_review else "limited" if reviews or terminal else "waiting"
    )
    if replan_events or len(rounds) > 1:
        replan_status = "completed"
        visible_deltas = [
            *(item.summary for item in replan_events[-2:]),
            *(item.summary for item in round_delta_events[-2:]),
        ]
        replan_outcome = "；".join(visible_deltas) or (
            f"已执行 {max(len(rounds) - 1, 1)} 次补证计划。"
        )
        replan_at = replan_events[-1].occurred_at if replan_events else rounds[-1].started_at
    elif terminal:
        replan_status = "completed" if accepted_review else "limited"
        replan_outcome = _no_replan_reason(run, coverage, accepted_review)
        replan_at = result.finished_at if result is not None else run.updated_at
    else:
        replan_status = "waiting"
        replan_outcome = "等待充分度和独立复核后决定是否补证。"
        replan_at = None
    # The current core product publishes an attested 7d daily outlook through
    # ``research_outlook``.  The legacy session contract still carries the
    # optional 30m/24h/72h horizon list, so an otherwise valid daily report
    # must not be shown as a failed synthesis merely because those legacy
    # horizons are absent.
    synth_ready = bool(
        (result and result.causal_case and result.horizons) or action.research_outlook is not None
    )
    synth_status = (
        "completed"
        if synth_ready
        else "failed"
        if terminal
        else "running"
        if run.stage == "synthesis"
        else "waiting"
    )
    gate_status = (
        "failed"
        if action.artifact_status == "rejected"
        else "limited"
        if action.artifact_status == "limited"
        else "completed"
        if action.artifact_status == "saved"
        else "running"
        if not terminal and run.stage == "gate"
        else "waiting"
        if not terminal
        else "failed"
    )
    checkpoint_steps = (
        [
            ResearchProcessStep(
                step_id="workflow-checkpoints",
                order=6,
                kind="synthesize",
                label="候选冻结检查点",
                status=(
                    "completed"
                    if native_review_attested
                    else "limited"
                    if terminal
                    else "waiting"
                ),
                objective="记录实际复核流程检查点；不改变原失败账或复核结论。",
                outcome=(
                    f"调用失败账中包括 {len(checkpoint_refs)} 次审查流程检查点。"
                    + (
                        "后续原生审查已匹配；是否接受仍看原审查结论。"
                        if native_review_attested
                        else "已冻结待复核候选，独立复核尚未完成。"
                    )
                    + "原调用仍记为失败；精确错误原文和配对来源保留在流程追踪原始记录。"
                ),
                evidence_refs=[],
                tool_call_refs=checkpoint_refs,
                warning_codes=[CHECKPOINT_WARNING],
                occurred_at=next(
                    item.occurred_at
                    for item in trace
                    if item.reference_id in checkpoint_refs
                    and item.reference_type == "workflow_checkpoint.final_review_required"
                ),
            )
        ]
        if checkpoint_refs
        else []
    )
    checkpoint_offset = len(checkpoint_steps)

    return [
        ResearchProcessStep(
            step_id="intake",
            order=1,
            kind="intake",
            label="接收问题",
            status="completed",
            objective="冻结用户问题、触发时间和数据截止点。",
            outcome=f"研究任务 {run.run_id} 已受理。",
            evidence_refs=[],
            tool_call_refs=[],
            warning_codes=[],
            occurred_at=run.created_at,
        ),
        ResearchProcessStep(
            step_id="plan",
            order=2,
            kind="plan",
            label="形成计划",
            status=plan_status,
            objective="把问题拆成声明过的 Evidence requirement 和能力调用。",
            outcome=(
                f"形成 {len(rounds)} 轮计划，共 "
                f"{sum(len(item.plan.tasks) for item in rounds)} 项任务。"
                if rounds
                else "尚未形成可回读计划。"
            ),
            evidence_refs=[],
            tool_call_refs=[],
            warning_codes=[],
            occurred_at=rounds[0].plan.created_at if rounds else None,
        ),
        ResearchProcessStep(
            step_id="acquire",
            order=3,
            kind="acquire",
            label="取得证据",
            status=acquire_status,
            objective="调用受限能力取得带来源、时间和内容指纹的 Evidence。",
            outcome=f"执行 {len(invocations)} 次工具调用，新增 {len(new_evidence)} 条 Evidence。",
            evidence_refs=new_evidence,
            tool_call_refs=tool_refs,
            warning_codes=tool_warnings,
            occurred_at=rounds[-1].finished_at if rounds else None,
        ),
        ResearchProcessStep(
            step_id="assess",
            order=4,
            kind="assess",
            label="检查充分度",
            status=assess_status,
            objective="确定性检查 freshness、authority、字段、venue、独立性和冲突。",
            outcome=(
                f"hard {coverage.hard_coverage_ratio:.0%}，"
                f"soft {coverage.soft_coverage_ratio:.0%}，"
                f"仍有 {len(coverage.gaps)} 个缺口。"
                if coverage is not None
                else "尚无充分度结果。"
            ),
            evidence_refs=[],
            tool_call_refs=[],
            warning_codes=(
                [f"gap:{item.requirement_id}:{item.reason_code}" for item in coverage.gaps]
                if coverage is not None
                else []
            ),
            occurred_at=coverage.assessed_at if coverage is not None else None,
        ),
        ResearchProcessStep(
            step_id="synthesize",
            order=5,
            kind="synthesize",
            label="形成研究情景",
            status=synth_status,
            objective="生成主因果链、反向假设和分周期条件，不授予交易权限。",
            outcome=(
                f"已形成 {len(result.horizons)} 个分周期情景。"
                if result is not None and result.horizons
                else "已形成 1 个 7 日日频研究观点；旧版 30m/24h/72h 情景未发布。"
                if action.research_outlook is not None
                else "尚未形成可信综合结果。"
            ),
            evidence_refs=action.supporting_evidence_refs,
            tool_call_refs=[],
            warning_codes=[result.synthesis_failure_code]
            if result and result.synthesis_failure_code
            else [],
            occurred_at=result.finished_at if result is not None else None,
        ),
        *checkpoint_steps,
        ResearchProcessStep(
            step_id="review",
            order=6 + checkpoint_offset,
            kind="review",
            label=(
                "独立复核"
                if accepted_review
                else "自动证据检查"
                if automated_check_only
                else "审查（有保留）"
            ),
            status=review_status,
            objective="独立挑战主结论、引用和相反路径。",
            outcome=(
                "；".join(f"{item.role}: {item.resolution} — {item.challenge}" for item in reviews)
                if reviews
                else "没有可接受的独立复核记录。"
            ),
            evidence_refs=list(
                dict.fromkeys(ref for item in reviews for ref in item.evidence_refs)
            ),
            tool_call_refs=[],
            warning_codes=[
                f"review:{item.resolution}" for item in reviews if item.resolution != "accepted"
            ],
            occurred_at=reviews[-1].created_at if reviews else None,
        ),
        ResearchProcessStep(
            step_id="replan",
            order=7 + checkpoint_offset,
            kind="replan",
            label="决定是否补证",
            status=replan_status,
            objective="根据缺口和 reviewer challenge 决定是否进入下一轮。",
            outcome=replan_outcome,
            evidence_refs=[],
            tool_call_refs=[],
            warning_codes=[],
            occurred_at=replan_at,
        ),
        ResearchProcessStep(
            step_id="gate",
            order=8 + checkpoint_offset,
            kind="gate",
            label="形成行动边界",
            status=gate_status,
            objective="把报告保存状态、数据限制和资金权限分开。",
            outcome=f"{action.headline}；交易权限：未授权。",
            evidence_refs=action.supporting_evidence_refs,
            tool_call_refs=[],
            warning_codes=action.warning_codes,
            occurred_at=run.updated_at,
        ),
    ]


def _no_replan_reason(
    run: ResearchRunView,
    coverage: CoverageAssessment | None,
    accepted_review: bool,
) -> str:
    if run.stop_reason is not None:
        mapping = {
            "round_budget": "未继续补证：已达到轮次预算。",
            "tool_budget": "未继续补证：已达到工具预算。",
            "deadline": "未继续补证：已达到截止时间。",
            "runtime_unavailable": "未继续补证：运行时不可用。",
            "critical_data_unavailable": "未继续补证：已声明的数据路径不可用或已穷尽。",
        }
        if run.stop_reason.code in mapping:
            return mapping[run.stop_reason.code]
    if coverage is not None and coverage.status == "sufficient" and accepted_review:
        return "未进入下一轮：声明的 hard requirements 已满足，独立复核已接受。"
    return "未进入下一轮：没有可验证的继续路径；结果保持受限。"


def _bounded_view_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 3]}..."


def _event_title(observation: ObservationRecord | None) -> str:
    if observation is None:
        return "Untitled research event"
    if getattr(observation, "source_id", None) == "decision-desk-latest-advice":
        # The expanded acquisition template is execution context, not a title.
        note = observation.text.partition("\n用户补充关注点：")[2].strip()
        return "BTC 最新操作建议" + (f" · {_bounded_view_text(note, 60)}" if note else "")
    first_line = next(
        (line.strip() for line in observation.text.splitlines() if line.strip()),
        observation.text,
    )
    title = " ".join(first_line.split())
    return title if len(title) <= 160 else f"{title[:157]}..."


def _research_focus(observation: ObservationRecord | None) -> str | None:
    if observation is None or observation.source_id != "decision-desk-latest-advice":
        return None
    return observation.text.partition("\n用户补充关注点：")[2].strip()


def _observation_for_event(session: Session, event_id: str) -> ObservationRecord | None:
    return (
        session.query(ObservationRecord)
        .filter_by(event_id=event_id)
        .order_by(ObservationRecord.received_at.asc(), ObservationRecord.observation_id.asc())
        .first()
    )


def _event_family(observation: ObservationRecord | None) -> str | None:
    return observation.event_hint if observation is not None else None


def _inbox_origin(value: str) -> Literal["manual", "automatic", "scheduled_recheck", "legacy"]:
    allowed = {"manual", "automatic", "scheduled_recheck", "legacy"}
    return cast(
        Literal["manual", "automatic", "scheduled_recheck", "legacy"],
        value if value in allowed else "legacy",
    )


def _inbox_status(
    run: RunRecord, artifact: ArtifactRecord | None
) -> Literal[
    "watching",
    "queued",
    "researching",
    "report_ready",
    "research_only",
    "rejected",
    "failed",
    "cancelled",
]:
    if artifact is not None:
        if artifact.gate_status == "research_only":
            return "research_only"
        if artifact.gate_status == "reject":
            return "rejected"
        return "report_ready"
    return cast(
        Literal[
            "watching",
            "queued",
            "researching",
            "report_ready",
            "research_only",
            "rejected",
            "failed",
            "cancelled",
        ],
        {
            "admitted": "queued",
            "running": "researching",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(run.status, "failed"),
    )


def _baseline_status(
    watch: EventWatchRecord | None,
) -> Literal[
    "not_applicable",
    "pending",
    "ready",
    "unavailable",
]:
    if watch is None:
        return "not_applicable"
    allowed = {"pending", "ready", "unavailable"}
    return cast(
        Literal["not_applicable", "pending", "ready", "unavailable"],
        watch.baseline_status if watch.baseline_status in allowed else "unavailable",
    )


def _notification_status(
    outbox: OutboxRecord | None,
) -> Literal[
    "not_applicable",
    "pending",
    "retry_wait",
    "delivered",
    "failed",
]:
    if outbox is None:
        return "not_applicable"
    if outbox.sent_at is not None:
        return "delivered"
    if outbox.failed_at is not None:
        return "failed"
    if outbox.next_attempt_at is not None or outbox.attempts > 0:
        return "retry_wait"
    return "pending"


def _duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    if finished_at is None:
        return None
    return max(0, round((finished_at - started_at).total_seconds() * 1000))


def _source_attempt(capability_id: str, attempt: dict[str, object]) -> SourceAttemptView:
    cost = attempt.get("cost_usd")
    raw_latency = attempt.get("latency_ms")
    raw_status = str(attempt.get("status") or "skipped")
    status = (
        _attempt_status(str(attempt.get("error_code") or ""))
        if raw_status == "failed"
        else raw_status
    )
    route_role = str(attempt.get("route_role") or "direct")
    service_tier = str(attempt.get("service_tier") or "unknown")
    return SourceAttemptView.model_validate(
        {
            "capability_id": capability_id,
            "provider_id": str(attempt.get("provider_id") or "unresolved"),
            "route_role": route_role,
            "service_tier": service_tier,
            "status": cast(
                Literal["succeeded", "failed", "skipped", "denied", "timed_out"],
                status,
            ),
            "latency_ms": (
                int(raw_latency)
                if isinstance(raw_latency, (int, float, str)) and not isinstance(raw_latency, bool)
                else 0
            ),
            "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
            "cost_status": "known" if isinstance(cost, (int, float)) else "unknown",
            "error_code": (str(attempt["error_code"]) if attempt.get("error_code") else None),
            "retryable": bool(attempt.get("retryable", False)),
        }
    )


def _attempt_status(error_code: str) -> Literal["failed", "denied", "timed_out"]:
    if "timeout" in error_code or "deadline" in error_code:
        return "timed_out"
    if "denied" in error_code or "permission" in error_code:
        return "denied"
    return "failed"


def _readiness_status(
    reason_code: str,
) -> Literal[
    "stale",
    "conflict",
    "no_baseline",
    "window_missing",
    "tool_unavailable",
    "rejected",
    "missing",
]:
    return cast(
        Literal[
            "stale",
            "conflict",
            "no_baseline",
            "window_missing",
            "tool_unavailable",
            "rejected",
            "missing",
        ],
        {
            "stale": "stale",
            "conflict": "conflict",
            "no_baseline": "no_baseline",
            "window_missing": "window_missing",
            "tool_unavailable": "tool_unavailable",
            "semantic_mismatch": "rejected",
            "low_authority": "rejected",
        }.get(reason_code, "missing"),
    )


def _next_capability(requirement: EvidenceRequirement, attempted: list[str]) -> str | None:
    ladder = [*requirement.preferred_capabilities, *requirement.allowed_fallbacks]
    return next((item for item in ladder if item not in attempted), None)


def _attempt_cost_component(
    component: Literal["search", "typed_provider"],
    attempts: list[SourceAttemptView],
) -> ResearchCostComponent:
    if not attempts:
        return ResearchCostComponent(
            component=component,
            amount_usd=None,
            status="unknown",
            policy_ref=None,
        )
    known_costs = [item.cost_usd for item in attempts if item.cost_usd is not None]
    amount = sum(known_costs) if known_costs else None
    if any(item.cost_status == "unknown" for item in attempts):
        return ResearchCostComponent(
            component=component,
            amount_usd=amount,
            status="unknown",
            policy_ref=None,
        )
    assert amount is not None
    return ResearchCostComponent(
        component=component,
        amount_usd=amount,
        status="known_free" if amount == 0 else "known",
        policy_ref="known-free-public.v1" if amount == 0 else None,
    )


def _telemetry_ref(trajectory_ref: str | None) -> str | None:
    if trajectory_ref and trajectory_ref.startswith(("otel://", "loongsuite://")):
        return trajectory_ref
    return None


def _priority(value: int) -> str:
    if value >= 100:
        return "high"
    if value >= 50:
        return "normal"
    return "low"


def _run_status_rank(status: str) -> int:
    if status in {"running", "researching", "admitted", "queued", "retry_wait"}:
        return 0
    if status in {"completed", "research_only", "degraded"}:
        return 1
    if status in {"failed", "rejected"}:
        return 2
    if status == "cancelled":
        return 3
    return 4


def _run_page_filter_fingerprint(
    status: str | None, source_id: str | None, origin: str | None, q: str | None
) -> str:
    payload = json.dumps([status, source_id, origin, q], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode_run_cursor(run: RunRecord, fingerprint: str, rank: int) -> str:
    payload = {
        "v": 1,
        "f": fingerprint,
        "r": rank,
        "a": 0 if run.artifact_id else 1,
        "t": run.updated_at.isoformat(),
        "id": run.run_id,
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_run_cursor(cursor: str, fingerprint: str) -> tuple[int, int, datetime, str]:
    try:
        if (
            not cursor
            or len(cursor) > 1024
            or any(
                character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                for character in cursor
            )
        ):
            raise ValueError
        decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(decoded)
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("f") != fingerprint
            or type(payload.get("r")) is not int
            or payload["r"] not in range(5)
            or type(payload.get("a")) is not int
            or payload["a"] not in (0, 1)
            or not isinstance(payload.get("t"), str)
            or not isinstance(payload.get("id"), str)
            or not payload["id"]
        ):
            raise ValueError
        updated_at = datetime.fromisoformat(payload["t"])
        return payload["r"], payload["a"], updated_at, payload["id"]
    except (BinasciiError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("research_run_cursor_invalid") from exc


def _observed_runtime_version(run: RunRecord, link: DshSessionLinkRecord | None) -> str:
    # The durable Host reservation exists before submission. Never infer the
    # executing build from today's process environment or a legacy ORM default.
    if link is not None:
        try:
            identity = DshUpstreamIdentity.model_validate_json(link.upstream_identity_json)
            if identity.plugin_build_hash != link.plugin_build_hash:
                return "unknown"
            return f"dsh-web-{identity.source_version}+{identity.plugin_build_hash[:12]}"
        except ValueError:
            return "unknown"
    if run.runtime_version in (None, "fake.v1", "pending"):
        return "pending" if run.status == "admitted" else "unknown"
    return run.runtime_version


def _observed_runtime_id(
    session: Session,
    run_id: str,
    traces: list[ResearchTraceRecord],
) -> str:
    return _observed_runtime_id_from_link(session.get(DshSessionLinkRecord, run_id), traces)


def _observed_runtime_id_from_link(
    link: DshSessionLinkRecord | None, traces: list[ResearchTraceRecord],
) -> str:
    if link is not None:
        return "dsh"
    session_ids = {item.research_session_id for item in traces}
    if any(item.startswith(("dsh_", "dsh-", "decision-research-")) for item in session_ids):
        return "dsh"
    if any(item.startswith("fixed:") for item in session_ids):
        return "fixed"
    return "pending"


def _view_status(run: RunRecord, artifact: ArtifactRecord | None) -> str:
    if artifact is not None and artifact.gate_status in {"research_only", "reject"}:
        return "research_only" if artifact.gate_status == "research_only" else "rejected"
    return {
        "admitted": "queued",
        "running": "researching",
    }.get(run.status, run.status)


def _stage(run: RunRecord, latest: ResearchTraceRecord | None) -> str:
    if run.status in {"completed", "degraded", "failed", "cancelled"}:
        return "done"
    if latest is not None and latest.stage in {
        "admission",
        "planning",
        "acquiring_evidence",
        "assessing_sufficiency",
        "synthesis",
        "gate",
        "monitoring",
        "done",
    }:
        return latest.stage
    return "planning" if run.status == "running" else "admission"


def _snapshot_view(record: SnapshotRecord | None) -> ResearchSnapshotManifest | None:
    """Keep an unparseable original snapshot unavailable in the read projection."""
    if record is None:
        return None
    try:
        raw = json.loads(record.evidence_json)
        evidence_refs = [
            str(item["evidence_id"])
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
        ]
        return ResearchSnapshotManifest.model_validate(
            {
                "schema_version": "research-snapshot-manifest.v1",
                "snapshot_id": record.snapshot_id,
                "run_id": record.run_id or "legacy",
                "event_id": record.event_id,
                "snapshot_type": record.snapshot_type,
                "generation": record.generation,
                "parent_snapshot_id": record.parent_snapshot_id,
                "cutoff_at": as_utc(record.cutoff_at),
                "snapshot_hash": record.snapshot_hash,
                "evidence_refs": evidence_refs,
                "pack_version": record.pack_version,
                "created_at": as_utc(record.created_at),
            }
        )
    except (ValueError, TypeError):
        # JSON/contract failures are historical-data limitations. Do not
        # fabricate evidence refs, hashes, timestamps, or a replacement snapshot.
        return None


def _evidence_view(record: ResearchEvidenceRecord) -> EvidenceCandidate:
    return EvidenceCandidate.model_validate(
        {
            "evidence_id": record.evidence_id,
            "requirement_id": record.requirement_id,
            "kind": record.kind,
            "authority": record.authority,
            "source_id": record.source_id,
            "source_url": record.source_url,
            "published_at": as_utc(record.published_at),
            "observed_at": as_utc(record.observed_at),
            "received_at": as_utc(record.received_at),
            "content_hash": record.content_hash,
            "excerpt": record.excerpt,
            "content_ref": record.content_ref,
            "acquisition_receipt_id": record.acquisition_receipt_id,
            "source_content_hash": record.source_content_hash,
            "parser_ref": record.parser_ref,
            "structured_payload_ref": record.structured_payload_ref,
            "tool_call_id": record.tool_call_id,
            "research_session_id": record.research_session_id,
            "round": record.round,
            "quality": record.quality,
            "freshness_status": record.freshness_status,
            "conflict_group": record.conflict_group,
        }
    )


def _fact_view(record: ResearchFactRecord) -> FactEnvelope:
    return FactEnvelope.model_validate(
        {
            "schema_version": "fact-envelope.v1",
            "fact_id": record.fact_id,
            "evidence_id": record.evidence_id,
            "requirement_id": record.requirement_id,
            "metric_family": record.metric_family,
            "field": record.field,
            "instrument": record.instrument,
            "venue": record.venue,
            "value": json.loads(record.value_json),
            "unit": record.unit,
            "window_start_at": as_utc(record.window_start_at),
            "window_end_at": as_utc(record.window_end_at),
            "event_offset": record.event_offset,
            "observed_at": as_utc(record.observed_at),
            "received_at": as_utc(record.received_at),
            "published_at": as_utc(record.published_at),
            "source_id": record.source_id,
            "independence_group": record.independence_group,
            "quality": record.quality,
            "delay_class": record.delay_class,
            "payload_schema_ref": record.payload_schema_ref,
            "payload_hash": record.payload_hash,
            "attributes": json.loads(record.attributes_json),
        }
    )


def _trace_view(record: ResearchTraceRecord) -> ResearchTraceEvent:
    return read_trace_projection(record)


def _native_execution_failure(
    run_id: str, link: DshSessionLinkRecord | None
) -> ResearchNativeExecutionFailure | None:
    # Read only the current Run's Link. Never infer a native cause from missing
    # synthesis, copy private transport payloads or rewrite a saved Result.
    if (
        link is None
        or link.run_id != run_id
        or link.state != "failed"
        or not link.error_code
        or link.terminal_at is None
    ):
        return None
    terminal_at = as_utc(link.terminal_at)
    assert terminal_at is not None
    return ResearchNativeExecutionFailure(
        schema_version="research-native-execution-failure.v1",
        run_id=run_id,
        dsh_session_id=link.dsh_session_id,
        generation=link.generation,
        error_code=link.error_code,
        terminal_at=terminal_at,
    )


def _primary_failure_record(
    run: RunRecord, traces: list[ResearchTraceRecord]
) -> tuple[ResearchTraceRecord, ErrorProvenance] | None:
    """Keep the first recorded terminal cause ahead of later rejected work.

    Run status and session_stopped are saved separately; a hard finished_at
    cutoff could discard the genuine stop by a few milliseconds. Only a saved
    typed stop matching this Run's terminal state/error establishes this anchor.
    """
    meaningful = sorted(
        (pair for pair in _meaningful_trace_failures(traces) if pair[0].run_id == run.run_id),
        key=lambda pair: pair[0].sequence_no,
    )
    if run.status in {"failed", "cancelled"} and run.error_code:
        stopped = [
            pair for pair in meaningful
            if pair[0].event_type == "session_stopped"
            and pair[0].status == run.status
            and pair[1].error_code == run.error_code
        ]
        if stopped:
            return stopped[0]
    # Preserve ordinary/legacy selection if no matching terminal anchor exists.
    return meaningful[-1] if meaningful else None


def _failure_view(
    run: RunRecord, traces: ResearchTraceRecord | list[ResearchTraceRecord] | None
) -> ErrorProvenance | None:
    # Once an Artifact is committed, earlier retryable capability failures are
    # already represented in the trace and must not make a successful or
    # fail-closed report look like a failed run. A run-level error_code still
    # wins because it is an explicit terminal failure recorded by RunService.
    if run.artifact_id and run.status in {"completed", "degraded"} and not run.error_code:
        return None
    rows = [] if traces is None else traces if isinstance(traces, list) else [traces]
    primary = _primary_failure_record(run, rows)
    if primary is not None:
        return primary[1]
    if not run.error_code:
        return None
    if any(read_trace_projection(row).error_verification is not None
           and row.error_code == run.error_code for row in rows):
        return None
    return ErrorProvenance(
        error_code=run.error_code,
        origin="orchestration",
        cause_code=None,
        capability_id=None,
        tool_call_id=None,
        retryable=run.status in {"running", "admitted"},
        deadline_ms=None,
    )


def _trace_failures(run: RunRecord, traces: list[ResearchTraceRecord]) -> list[ErrorProvenance]:
    """Return every durable capability/runtime failure, preserving provenance.

    A terminal ``session_stopped`` event can follow a capability failure. Both
    are useful for audit, but the client receives a de-duplicated list by
    capability and error code. This avoids the old "latest trace wins" bug.
    """

    result: list[ErrorProvenance] = []
    seen: set[tuple[str | None, str]] = set()
    for _record, failure in _meaningful_trace_failures(traces):
        key = (failure.capability_id, failure.error_code)
        if key in seen:
            continue
        seen.add(key)
        result.append(failure)
    if run.error_code and not any(
        read_trace_projection(row).error_verification is not None
        and row.error_code == run.error_code for row in traces
    ):
        fallback = ErrorProvenance(
            error_code=run.error_code,
            origin="orchestration",
            cause_code=None,
            capability_id=None,
            tool_call_id=None,
            retryable=run.status in {"running", "admitted"},
            deadline_ms=None,
        )
        key = (fallback.capability_id, fallback.error_code)
        if key not in seen:
            result.append(fallback)
    return result


def _meaningful_trace_failures(
    traces: list[ResearchTraceRecord],
) -> list[tuple[ResearchTraceRecord, ErrorProvenance]]:
    """Keep business failures while preserving raw DSH wrapper events for audit.

    The native ``decision_hub_research`` call and the durable Gateway capability
    call describe the same product action. If the Gateway already persisted a
    typed capability failure, a later generic DSH wrapper failure adds no
    provenance and must not become a second business failure. The raw trace is
    left untouched and remains available in DSH Trajectory/JSONL.
    """

    parsed = [
        (record, failure) for record in traces if (failure := _trace_failure(record)) is not None
    ]
    has_capability_failure = any(
        record.reference_type == "capability_call" and failure.capability_id is not None
        for record, failure in parsed
    )
    if not has_capability_failure:
        return parsed
    return [
        (record, failure)
        for record, failure in parsed
        if not _is_generic_native_wrapper_failure(record, failure)
    ]


def _is_generic_native_wrapper_failure(
    record: ResearchTraceRecord, failure: ErrorProvenance
) -> bool:
    return (
        record.reference_type == "dsh_tool_call"
        and failure.capability_id is None
        and failure.error_code == "dsh_tool_failed"
        and failure.cause_code in {"decision_hub_research", "research_capability_execute"}
    )


def _trace_failure(record: ResearchTraceRecord) -> ErrorProvenance | None:
    # Evidence rejection is an item-level quality decision, not a run failure.
    # A completed run may legitimately retain stale or semantically mismatched
    # candidates while publishing a conservative report. Only lifecycle/tool
    # failure events should populate the run-level failure provenance banner.
    if record.event_type not in {"tool_failed", "session_stopped", "subagent_completed"}:
        return None
    if record.error_provenance_json:
        return read_trace_projection(record).error
    if not record.error_code:
        return None
    return ErrorProvenance(
        error_code=record.error_code,
        origin="gateway" if record.event_type == "tool_failed" else "orchestration",
        cause_code=None,
        capability_id=None,
        tool_call_id=record.reference_id if record.event_type == "tool_failed" else None,
        retryable=record.status in {"running", "degraded"},
        deadline_ms=None,
    )


def _failure_payload(failure: ErrorProvenance, capability_id: str) -> dict[str, object]:
    return {
        "capability_id": capability_id,
        "error_code": failure.error_code,
        "origin": failure.origin,
        "cause_code": failure.cause_code,
        "retryable": failure.retryable,
    }


def _trace_count(traces: list[ResearchTraceEvent], event_type: str) -> int:
    return sum(item.event_type == event_type for item in traces)


def _business_tool_count(traces: list[ResearchTraceEvent]) -> int:
    capability_calls = sum(
        item.event_type == "tool_started" and item.reference_type == "capability_call"
        for item in traces
    )
    return capability_calls or _trace_count(traces, "tool_started")


def _observed_round(traces: list[ResearchTraceEvent]) -> int:
    completed = _trace_count(traces, "round_completed")
    if completed:
        return completed
    return (
        1
        if any(
            item.event_type in {"tool_started", "model_step_started", "plan_created"}
            for item in traces
        )
        else 0
    )


def _fallback_coverage(
    run: RunRecord,
    traces: list[ResearchTraceRecord],
    basis: SavedCoverageBasis,
    evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
) -> CoverageAssessment | None:
    """Project retained material against the original request and information boundary."""
    if basis.request is None or basis.cutoff_at is None:
        return None
    if not traces and run.status in {"admitted", "queued"}:
        return None
    assessed_at = as_utc(run.finished_at or run.updated_at)
    return assess_evidence_sufficiency(
        basis.request.evidence_requirements,
        evidence,
        cutoff_at=basis.cutoff_at,
        assessed_at=assessed_at,
        facts=facts,
    )


def _fallback_stop_reason(
    run: RunRecord,
    traces: list[ResearchTraceRecord],
    coverage: CoverageAssessment | None = None,
) -> ResearchStopReason | None:
    if run.status == "cancelled":
        return ResearchStopReason(
            code="cancelled",
            detail={
                "cancelled_by_owner": "用户主动停止了这次研究，原任务与已取得材料保留。",
                "scheduled_recheck_superseded": "这项旧复查已被更新的研究替代，原记录保留。",
                "stale_review_queue_cleanup": "这项过期复查已从等待队列停止，原记录保留。",
                "study_tracking_paused": "主题暂停自动复查后，这项自动排队任务已停止。",
            }.get(run.error_code or "", "这次研究已停止，原任务记录保留；未记录具体停止原因。"),
            bounded=True,
            remaining_hard_gaps=[],
        )
    if run.status != "failed":
        return None
    detail = _latest_failure_summary(traces, run=run) or (
        "Research stopped before a final result was committed."
    )
    failure = _failure_view(run, traces)
    # Use only the already verified typed failure. A Worker terminal is not a
    # failed native DSH Link, and arbitrary exception messages stay private.
    if failure is not None and failure.origin == "orchestration":
        detail = {
            ("provider_timeout", "dsh_web_deadline_elapsed"):
                "本次研究的原执行时限已到，尚未形成可提交的完整结果；"
                "已收集材料与失败记录保留。",
            ("research_method_binding_failed", "original_preparation_readset_changed"):
                "研究执行期间的原件核对发现变化，未能确认继续执行的身份；"
                "已保留原任务、材料与失败记录，尚未形成完整报告。"
                "请先核对原记录及运行状态。",
            ("research_method_binding_failed", "cancellation_projection_wait_elapsed"):
                "本次原件核验等待已达到时限，未确认可以继续研究；"
                "原任务、已取得材料与失败记录保留。",
            ("research_status_storage_pending", "dsh_status_initial_writer_busy"):
                "初始数据库写锁繁忙，研究运行状态未能在本次等待时限内落库确认。"
                "本次未生成可提交的完整报告，原任务、证据与失败记录保留；"
                "请先核对原执行和存储状态。",
            ("research_worker_failed", "original_preparation_deadline_elapsed"):
                "报告原件核验未能在原任务时限内完成，本次未发布完整报告；"
                "原任务、已取得材料和失败记录保留。",
        }.get((failure.error_code, failure.cause_code), detail)
    # A synthesis attestation failure means the runtime did work and trusted
    # progress exists, but directional semantics are unsafe to publish. Keep
    # that distinction visible instead of collapsing it into runtime outage.
    code = (
        "deadline"
        if failure is not None and failure.origin == "orchestration"
        and (failure.error_code, failure.cause_code) == ("provider_timeout", "dsh_web_deadline_elapsed")
        else "critical_data_unavailable"
        if failure is not None
        and failure.error_code in {"dsh_evidence_unattested", "structured_output_invalid"}
        else "runtime_unavailable"
    )
    return ResearchStopReason(
        code=code,
        detail=detail,
        bounded=True,
        remaining_hard_gaps=(
            [item.requirement_id for item in coverage.gaps if item.importance == "hard"]
            if coverage is not None
            else []
        ),
    )


def _fallback_rounds(
    run: RunRecord,
    traces: list[ResearchTraceRecord],
    evidence_rows: list[ResearchEvidenceRecord],
    evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
    coverage: CoverageAssessment | None,
    requirements: Iterable[EvidenceRequirement],
) -> list[ResearchRoundView]:
    """Expose durable research progress when no final result was committed.

    DSH trace events do not carry a round number, while accepted Evidence does.
    This projection builds only an auditable plan shell and tool records from
    those durable facts. It never infers a causal case, horizon, or synthesis.
    """
    progress_types = {"plan_created", "model_step_started", "tool_started", "round_completed"}
    if not evidence and not any(item.event_type in progress_types for item in traces):
        return []
    trace_views = [_trace_view(item) for item in traces]
    observed_round = _observed_round(trace_views)
    round_numbers = {item.round for item in evidence if item.round >= 1}
    if not round_numbers:
        round_numbers.add(max(1, observed_round))
    round_count = len(round_numbers)
    requirement_list = list(requirements)
    projected: list[ResearchRoundView] = []
    for round_number in sorted(round_numbers):
        round_traces = _round_trace_rows(traces, round_number, round_count)
        invocations, tool_results = _fallback_tool_records(
            round_traces,
            evidence_rows,
            evidence,
            round_number,
        )
        round_evidence = [item for item in evidence if item.round == round_number]
        round_evidence_rows = [item for item in evidence_rows if item.round == round_number]
        if not round_evidence and round_number == max(round_numbers):
            round_evidence = evidence
        started_at = _first_trace_time(round_traces, run)
        finished_at = _last_trace_time(round_traces, run)
        capability_ids = list(
            dict.fromkeys(
                [item.capability_id for item in round_evidence_rows]
                + [
                    item.error.capability_id
                    for item in invocations
                    if item.error is not None and item.error.capability_id
                ]
            )
        )
        if not capability_ids:
            capability_ids = ["dsh.research"]
        tasks = [
            ResearchTask(
                task_id=f"projection-round-{round_number}-{index}",
                requirement_id=_projected_requirement_id(
                    capability_id, requirement_list, round_evidence_rows
                ),
                capability_id=capability_id,
                objective=f"Observe durable progress from {capability_id}.",
                question="What evidence did the capability return before the run stopped?",
                input_evidence_refs=[item.evidence_id for item in evidence],
                output_schema_ref="evidence-candidate.v1",
                depends_on=[],
                success_condition="Retain validated Evidence or explicit failure provenance.",
                priority=max(1, min(100, 100 - index)),
            )
            for index, capability_id in enumerate(capability_ids)
        ]
        plan_trace = next(
            (item for item in round_traces if item.event_type == "plan_created"),
            None,
        )
        plan = ResearchPlan(
            plan_id=f"projection-plan-{run.run_id}-{round_number}",
            objective=(
                plan_trace.summary
                if plan_trace is not None
                else "Durably observed research progress before terminal stop."
            ),
            tasks=tasks,
            required_capabilities=capability_ids,
            created_at=_required_time(started_at),
        )
        projected.append(
            ResearchRoundView(
                round=round_number,
                plan=plan,
                tool_invocations=invocations,
                tool_results=tool_results,
                new_evidence_refs=[item.evidence_id for item in round_evidence],
                coverage=coverage,
                started_at=_required_time(started_at),
                finished_at=_required_time(finished_at),
            )
        )
    return projected


def _projected_requirement_id(
    capability_id: str,
    requirements: list[EvidenceRequirement],
    evidence_rows: list[ResearchEvidenceRecord],
) -> str:
    observed = next(
        (item.requirement_id for item in evidence_rows if item.capability_id == capability_id),
        None,
    )
    if observed is not None:
        return observed
    declared = next(
        (
            item.requirement_id
            for item in requirements
            if capability_id in (*item.preferred_capabilities, *item.allowed_fallbacks)
        ),
        None,
    )
    return declared or "unresolved_requirement"


def _round_trace_rows(
    traces: list[ResearchTraceRecord], round_number: int, round_count: int
) -> list[ResearchTraceRecord]:
    if round_count <= 1:
        return traces
    partitions: list[list[ResearchTraceRecord]] = [[]]
    for row in traces:
        partitions[-1].append(row)
        if row.event_type == "round_completed" and len(partitions) < round_count:
            partitions.append([])
    return partitions[min(round_number - 1, len(partitions) - 1)]


def _merge_durable_tool_activity(
    frozen: list[ResearchRound], observed: list[ResearchRoundView],
    *, timing_traces: list[ResearchTraceRecord] | None = None,
) -> list[ResearchRoundView]:
    """Keep subagent activity visible after commit without rewriting the result."""
    by_round = {
        item.round: ResearchRoundView.model_validate(item.model_dump(mode="json"))
        for item in frozen
    }
    if timing_traces:
        # Correct display timing from the original native call span; never rewrite the result.
        timing_calls, _ = _fallback_tool_records(timing_traces, [], [], 1)
        timing_by_call = {call.tool_call_id: call for call in timing_calls}
        for base in by_round.values():
            corrected: dict[str, tuple[datetime, datetime]] = {}
            calls: list[ToolInvocation] = []
            for call in base.tool_invocations:
                timing = timing_by_call.get(call.tool_call_id)
                if (
                    timing is not None and timing.finished_at is not None
                    and call.finished_at is not None
                    and timing.started_at <= timing.finished_at <= call.finished_at
                    and timing.started_at == call.started_at and timing.status == call.status
                ):
                    corrected[call.tool_call_id] = (call.finished_at, timing.finished_at)
                    call = call.model_copy(update={
                        "finished_at": timing.finished_at, "latency_ms": timing.latency_ms,
                    })
                calls.append(call)
            base.tool_invocations = calls
            base.tool_results = [
                value.model_copy(update={"received_at": corrected[value.tool_call_id][1]})
                if value.tool_call_id in corrected
                and value.received_at == corrected[value.tool_call_id][0]
                else value
                for value in base.tool_results
            ]
    known_calls = {call.tool_call_id for item in frozen for call in item.tool_invocations}
    for item in observed:
        additional = [
            call for call in item.tool_invocations if call.tool_call_id not in known_calls
        ]
        if not additional:
            continue
        ids = {call.tool_call_id for call in additional}
        known_calls.update(ids)
        base = by_round.get(item.round)
        if base is None:
            by_round[item.round] = item.model_copy(
                update={
                    "tool_invocations": additional,
                    "tool_results": [
                        value for value in item.tool_results if value.tool_call_id in ids
                    ],
                }
            )
        else:
            by_round[item.round] = base.model_copy(
                update={
                    "tool_invocations": sorted(
                        [*base.tool_invocations, *additional], key=lambda call: call.started_at
                    ),
                    "tool_results": [
                        *base.tool_results,
                        *(value for value in item.tool_results if value.tool_call_id in ids),
                    ],
                }
            )
    return [by_round[number] for number in sorted(by_round)]


def _fallback_tool_records(
    traces: list[ResearchTraceRecord],
    evidence_rows: list[ResearchEvidenceRecord],
    evidence: list[EvidenceCandidate],
    round_number: int,
) -> tuple[list[ToolInvocation], list[ToolResultSummary]]:
    by_call: dict[str, list[ResearchTraceRecord]] = {}
    has_capability_calls = any(
        row.event_type == "tool_started" and row.reference_type == "capability_call"
        for row in traces
    )
    for row in traces:
        if row.event_type not in {"tool_started", "tool_completed", "tool_failed"}:
            continue
        if has_capability_calls and row.reference_type != "capability_call":
            continue
        if row.reference_id is not None:
            by_call.setdefault(row.reference_id, []).append(row)
    evidence_by_call: dict[str, list[str]] = {}
    for item in evidence:
        if item.round == round_number and item.tool_call_id:
            evidence_by_call.setdefault(item.tool_call_id, []).append(item.evidence_id)
    invocations: list[ToolInvocation] = []
    results: list[ToolResultSummary] = []
    for call_id, rows in by_call.items():
        started = next((item for item in rows if item.event_type == "tool_started"), rows[0])
        terminal = next(
            (
                item
                for item in rows
                if item.event_type in {"tool_completed", "tool_failed"}
            ),
            None,
        )
        status = (
            "running"
            if terminal is None
            else ("failed" if terminal.event_type == "tool_failed" else "succeeded")
        )
        error = _trace_failure(terminal) if terminal is not None else None
        capability_id = (
            error.capability_id
            if error is not None and error.capability_id
            else next(
                (item.capability_id for item in evidence_rows if item.tool_call_id == call_id),
                "dsh.research",
            )
        )
        finished_at = as_utc(terminal.occurred_at) if terminal is not None else None
        latency_ms = (
            max(
                0,
                round(
                    (
                        _required_time(finished_at) - _required_time(as_utc(started.occurred_at))
                    ).total_seconds()
                    * 1000
                ),
            )
            if finished_at is not None
            else None
        )
        invocations.append(
            ToolInvocation(
                tool_call_id=call_id,
                capability_id=capability_id,
                tool_name=capability_id,
                query_summary=started.summary,
                started_at=_required_time(as_utc(started.occurred_at)),
                finished_at=finished_at,
                status=status,
                attempt=1,
                latency_ms=latency_ms,
                cost_usd=None,
                error_code=error.error_code if error is not None else None,
                error=error,
            )
        )
        if terminal is not None:
            results.append(
                ToolResultSummary(
                    tool_call_id=call_id,
                    status="failed" if status == "failed" else "succeeded",
                    summary=terminal.summary,
                    evidence_refs=evidence_by_call.get(call_id, []),
                    content_ref=None,
                    content_hash=None,
                    received_at=_required_time(as_utc(terminal.occurred_at)),
                    error_code=error.error_code if error is not None else None,
                    error=error,
                )
            )
    return invocations, results


def _first_trace_time(rows: list[ResearchTraceRecord], run: RunRecord):
    return _required_time(as_utc(rows[0].occurred_at) if rows else as_utc(run.created_at))


def _last_trace_time(rows: list[ResearchTraceRecord], run: RunRecord):
    return _required_time(as_utc(rows[-1].occurred_at) if rows else as_utc(run.updated_at))


def _required_time(value: datetime | None) -> datetime:
    if value is None:
        raise ValueError("research_projection_timestamp_missing")
    return value


def _latest_failure_summary(
    traces: list[ResearchTraceRecord], *, run: RunRecord | None = None
) -> str | None:
    if run is not None:
        primary = _primary_failure_record(run, traces)
        return primary[0].summary if primary is not None else None
    meaningful = _meaningful_trace_failures(traces)
    return meaningful[-1][0].summary if meaningful else None
