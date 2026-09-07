from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import yaml
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    CoverageAssessment,
    DomainPackManifest,
    DshBusinessStatus,
    ErrorProvenance,
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
    RequirementReadinessItem,
    ResearchCostBreakdown,
    ResearchCostComponent,
    ResearchInboxItem,
    ResearchInboxView,
    ResearchObservabilityView,
    ResearchPlan,
    ResearchRound,
    ResearchRunDetailView,
    ResearchRunView,
    ResearchSessionResult,
    ResearchSnapshotManifest,
    ResearchStopReason,
    ResearchTask,
    ResearchTraceEvent,
    ResearchValueEvaluation,
    ResearchVersionLineage,
    SourceAttemptView,
    ToolInvocation,
    ToolResultSummary,
)
from packages.kernel.decision_hub_kernel.application.research_observability import (
    ResearchObservabilityService,
)
from packages.kernel.decision_hub_kernel.application.research_value import (
    ResearchValueEvaluationService,
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
    SnapshotRecord,
    as_utc,
    utcnow,
)
from packages.provider_adapters.research import CryptoMacroFactPack
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry


class ResearchQueryService:
    """Build human-readable research views from canonical durable facts."""

    def __init__(self, database: Database, *, pack_root: Path) -> None:
        self.database = database
        self.pack_root = pack_root
        self.pack = DomainPackManifest.model_validate(
            yaml.safe_load((pack_root / "pack.yaml").read_text(encoding="utf-8"))
        )
        self.evidence_requirements = (
            CryptoMacroFactPack.from_pack(pack_root).all_contract_requirements()
        )
        self.observability = ResearchObservabilityService(database)
        bindings = yaml.safe_load(
            (pack_root / "tools" / "bindings.yaml").read_text(encoding="utf-8")
        )
        self.capability_manifests = {
            item["capability_id"]: item
            for item in bindings.get("capabilities", [])
            if isinstance(item, dict) and isinstance(item.get("capability_id"), str)
        }
        self.source_registry = ResearchSourceRegistry.from_pack(pack_root).contract
        self.value_evaluations = ResearchValueEvaluationService(
            database, pack_root=pack_root
        )

    def list(self, *, limit: int = 100) -> list[ResearchRunView]:
        bounded = max(1, min(limit, 500))
        with self.database.session() as session:
            rows = (
                session.query(RunRecord)
                .filter(RunRecord.strategy_version == "research.v1")
                .order_by(RunRecord.created_at.desc(), RunRecord.run_id.desc())
                .limit(bounded)
                .all()
            )
            return [self._run_view(session, row) for row in rows]

    def inbox(self, *, limit: int = 100) -> ResearchInboxView:
        """Project watched events and research Runs into one canonical Inbox."""

        bounded = max(1, min(limit, 500))
        with self.database.session() as session:
            runs = (
                session.query(RunRecord)
                .filter(RunRecord.strategy_version == "research.v1")
                .order_by(RunRecord.updated_at.desc(), RunRecord.run_id.desc())
                .all()
            )
            watches = (
                session.query(EventWatchRecord)
                .order_by(EventWatchRecord.updated_at.desc(), EventWatchRecord.watch_id.desc())
                .all()
            )
            watches_by_event = {item.event_id: item for item in watches}
            run_event_ids = {item.event_id for item in runs}
            items = [
                self._inbox_run_item(session, run, watches_by_event.get(run.event_id))
                for run in runs
            ]
            items.extend(
                self._inbox_watch_item(session, watch)
                for watch in watches
                if watch.event_id not in run_event_ids
            )
            items.sort(key=lambda item: (item.updated_at, item.event_id), reverse=True)
            return ResearchInboxView(
                schema_version="research-inbox-view.v1",
                items=items[:bounded],
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
            assessed_at = as_utc(run.finished_at or run.updated_at)
            if assessed_at is None:  # pragma: no cover - database integrity guard
                raise ValueError("research_run_timestamp_missing")
            coverage = (
                result.final_coverage
                if result is not None
                else assess_evidence_sufficiency(
                    self.evidence_requirements,
                    evidence,
                    cutoff_at=assessed_at,
                    assessed_at=assessed_at,
                    facts=facts,
                )
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
                else run.runtime_version or "unknown"
            )
            trajectory_ref = (
                result.trace_ref
                if result is not None
                else dsh_link.trace_ref if dsh_link is not None else None
            )
            return ResearchObservabilityView(
                schema_version="research-observability-view.v1",
                run_id=run_id,
                versions=ResearchVersionLineage(
                    domain_pack_ref=f"{self.pack.pack_id}.v{self.pack.version.split('.')[0]}",
                    domain_pack_version=self.pack.version,
                    role_profile_ref=(
                        result.profile_ref if result is not None else "crypto_macro.manager.v1"
                    ),
                    runtime_id=runtime_id,
                    runtime_version=runtime_version,
                    capability_versions=[
                        f"{capability_id}@{manifest.get('version', 'unknown')}"
                        for capability_id, manifest in sorted(self.capability_manifests.items())
                    ],
                    gate_policy_ref=self.pack.gate_policy_ref,
                    source_registry_ref=(
                        f"{self.source_registry.pack_id}@{self.source_registry.version}"
                    ),
                ),
                readiness=self._readiness(coverage, facts, attempts),
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
            domain_pack_ref=f"{self.pack.pack_id}.v{self.pack.version.split('.')[0]}",
            role_profile_ref="crypto_macro.manager.v1",
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
            domain_pack_ref=f"{self.pack.pack_id}.v{self.pack.version.split('.')[0]}",
            role_profile_ref="crypto_macro.manager.v1",
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
                        service_tier=(
                            "replay" if provider.startswith("replay") else "unknown"
                        ),
                        status="succeeded",
                        latency_ms=_duration_ms(row.reserved_at, row.completed_at),
                        cost_usd=(
                            float(cost)
                            if isinstance(cost, (int, float))
                            else 0.0 if known_free else None
                        ),
                        cost_status=(
                            "known"
                            if isinstance(cost, (int, float)) or known_free
                            else "unknown"
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
    ) -> list[RequirementReadinessItem]:
        gaps = {item.requirement_id: item for item in coverage.gaps}
        fact_fields: dict[str, set[str]] = {}
        for fact in facts:
            fact_fields.setdefault(fact.requirement_id, set()).add(fact.field)
        attempted_all = [item.capability_id for item in attempts]
        result: list[RequirementReadinessItem] = []
        for requirement in self.evidence_requirements:
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
                    missing_fields=[
                        item for item in required_fields if item not in present_fields
                    ],
                    required_event_offsets=list(requirement.required_event_offsets or []),
                    attempted_capabilities=attempted,
                    next_capability=_next_capability(requirement, attempted),
                )
            )
        return result

    def _cost_breakdown(
        self,
        run: RunRecord,
        result: ResearchSessionResult | None,
        attempts: list[SourceAttemptView],
    ) -> ResearchCostBreakdown:
        model_cost = result.estimated_cost_usd if result is not None else run.cost_usd
        search_attempts = [
            item for item in attempts if item.capability_id.startswith("web.search")
        ]
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
            result_row = session.get(ResearchResultRecord, run_id)
            result = (
                ResearchSessionResult.model_validate_json(result_row.payload_json)
                if result_row is not None
                else None
            )
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
            fallback_coverage = _fallback_coverage(
                run,
                traces,
                self.evidence_requirements,
                evidence_views,
                fact_views,
            )
            fallback_rounds = _fallback_rounds(
                run,
                traces,
                evidence,
                evidence_views,
                fallback_coverage,
                self.evidence_requirements,
            )
            return ResearchRunDetailView(
                schema_version="research-run-detail-view.v1",
                run=self._run_view(session, run),
                trigger_snapshot=_snapshot_view(
                    session.get(SnapshotRecord, run.snapshot_id) if run.snapshot_id else None
                ),
                decision_snapshot=_snapshot_view(
                    session.get(SnapshotRecord, run.decision_snapshot_id)
                    if run.decision_snapshot_id
                    else None
                ),
                evidence=evidence_views,
                rounds=result.rounds if result is not None else fallback_rounds,
                causal_case=result.causal_case if result is not None else None,
                horizons=result.horizons if result is not None else [],
                trace=trace_views,
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
            )

    def trace(self, run_id: str, *, after: int = 0) -> list[ResearchTraceEvent]:
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None or run.strategy_version != "research.v1":
                raise KeyError(run_id)
        return self.observability.list_trace(run_id, after=after)

    def business_status(self, run_id: str) -> DshBusinessStatus | None:
        """Project the Hub-owned Gate and research facts for the DSH client slot."""
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
                                    invocation.error.origin
                                    if invocation.error
                                    else "orchestration"
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
                self.evidence_requirements,
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
                        else fallback_coverage.status if fallback_coverage is not None else None
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
                        else _latest_failure_summary(trace_rows)
                    ),
                    "failures": failures[:16],
                }
            )

    def _run_view(self, session: Session, run: RunRecord) -> ResearchRunView:
        result_row = session.get(ResearchResultRecord, run.run_id)
        result = (
            ResearchSessionResult.model_validate_json(result_row.payload_json)
            if result_row is not None
            else None
        )
        trace_rows = (
            session.query(ResearchTraceRecord)
            .filter_by(run_id=run.run_id)
            .order_by(ResearchTraceRecord.sequence_no.asc())
            .all()
        )
        latest_trace = trace_rows[-1] if trace_rows else None
        trace_views = [_trace_view(item) for item in trace_rows]
        latest_sequence = latest_trace.sequence_no if latest_trace is not None else 0
        observation = (
            session.query(ObservationRecord)
            .filter_by(event_id=run.event_id)
            .order_by(ObservationRecord.received_at.asc())
            .first()
        )
        artifact = session.get(ArtifactRecord, run.artifact_id) if run.artifact_id else None
        fallback_evidence = [
            _evidence_view(item)
            for item in (
                session.query(ResearchEvidenceRecord)
                .filter_by(run_id=run.run_id)
                .order_by(ResearchEvidenceRecord.round, ResearchEvidenceRecord.evidence_id)
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
            self.evidence_requirements,
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
                    else _observed_runtime_id(session, run.run_id, trace_rows)
                ),
                "profile_ref": (
                    result.profile_ref if result is not None else "crypto_macro.manager.v1"
                ),
                "current_round": (
                    len(result.rounds)
                    if result is not None
                    else _observed_round(trace_views)
                ),
                "budget": self.pack.execution_budget,
                "coverage": result.final_coverage if result is not None else fallback_coverage,
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


def _event_title(observation: ObservationRecord | None) -> str:
    if observation is None:
        return "Untitled research event"
    first_line = next(
        (line.strip() for line in observation.text.splitlines() if line.strip()),
        observation.text,
    )
    title = " ".join(first_line.split())
    return title if len(title) <= 160 else f"{title[:157]}..."


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


def _inbox_status(run: RunRecord, artifact: ArtifactRecord | None) -> Literal[
    "watching", "queued", "researching", "report_ready", "research_only",
    "rejected", "failed", "cancelled",
]:
    if artifact is not None:
        if artifact.gate_status == "research_only":
            return "research_only"
        if artifact.gate_status == "reject":
            return "rejected"
        return "report_ready"
    return cast(Literal[
        "watching", "queued", "researching", "report_ready", "research_only",
        "rejected", "failed", "cancelled",
    ], {
        "admitted": "queued",
        "running": "researching",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(run.status, "failed"))


def _baseline_status(watch: EventWatchRecord | None) -> Literal[
    "not_applicable", "pending", "ready", "unavailable",
]:
    if watch is None:
        return "not_applicable"
    allowed = {"pending", "ready", "unavailable"}
    return cast(
        Literal["not_applicable", "pending", "ready", "unavailable"],
        watch.baseline_status if watch.baseline_status in allowed else "unavailable",
    )


def _notification_status(outbox: OutboxRecord | None) -> Literal[
    "not_applicable", "pending", "retry_wait", "delivered", "failed",
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
                if isinstance(raw_latency, (int, float, str))
                and not isinstance(raw_latency, bool)
                else 0
            ),
            "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
            "cost_status": "known" if isinstance(cost, (int, float)) else "unknown",
            "error_code": (
                str(attempt["error_code"]) if attempt.get("error_code") else None
            ),
            "retryable": bool(attempt.get("retryable", False)),
        }
    )


def _attempt_status(error_code: str) -> Literal["failed", "denied", "timed_out"]:
    if "timeout" in error_code or "deadline" in error_code:
        return "timed_out"
    if "denied" in error_code or "permission" in error_code:
        return "denied"
    return "failed"


def _readiness_status(reason_code: str) -> Literal[
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


def _observed_runtime_id(
    session: Session,
    run_id: str,
    traces: list[ResearchTraceRecord],
) -> str:
    if session.get(DshSessionLinkRecord, run_id) is not None:
        return "dsh"
    session_ids = {item.research_session_id for item in traces}
    if any(
        item.startswith(("dsh_", "dsh-", "decision-research-"))
        for item in session_ids
    ):
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
    if record is None:
        return None
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
    return ResearchTraceEvent.model_validate(
        {
            "schema_version": "research-trace-event.v1",
            "run_id": record.run_id,
            "research_session_id": record.research_session_id,
            "sequence_no": record.sequence_no,
            "event_type": record.event_type,
            "occurred_at": as_utc(record.occurred_at),
            "stage": record.stage,
            "summary": record.summary,
            "reference_type": record.reference_type,
            "reference_id": record.reference_id,
            "status": record.status,
            "error_code": record.error_code,
            "error": (
                json.loads(record.error_provenance_json)
                if record.error_provenance_json
                else None
            ),
        }
    )


def _failure_view(
    run: RunRecord, traces: ResearchTraceRecord | list[ResearchTraceRecord] | None
) -> ErrorProvenance | None:
    rows = (
        []
        if traces is None
        else traces
        if isinstance(traces, list)
        else [traces]
    )
    meaningful = _meaningful_trace_failures(rows)
    if meaningful:
        return meaningful[-1][1]
    if not run.error_code:
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


def _trace_failures(
    run: RunRecord, traces: list[ResearchTraceRecord]
) -> list[ErrorProvenance]:
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
    if run.error_code:
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
        (record, failure)
        for record in traces
        if (failure := _trace_failure(record)) is not None
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
        and failure.cause_code
        in {"decision_hub_research", "research_capability_execute"}
    )


def _trace_failure(record: ResearchTraceRecord) -> ErrorProvenance | None:
    if (
        record.event_type not in {"tool_failed", "session_stopped", "subagent_completed"}
        and not record.error_code
    ):
        return None
    if record.error_provenance_json:
        try:
            return ErrorProvenance.model_validate(json.loads(record.error_provenance_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
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
    requirements: Iterable[EvidenceRequirement],
    evidence: list[EvidenceCandidate],
    facts: list[FactEnvelope],
) -> CoverageAssessment | None:
    """Expose a conservative coverage state while no final result exists."""

    if not traces and run.status in {"admitted", "queued"}:
        return None
    assessed_at = (
        as_utc(traces[-1].occurred_at)
        if traces
        else max((item.received_at for item in evidence), default=as_utc(run.updated_at))
    )
    if assessed_at is None:
        return None
    return assess_evidence_sufficiency(
        requirements,
        evidence,
        cutoff_at=assessed_at,
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
            detail="Research was cancelled by the owner before a final result was committed.",
            bounded=True,
            remaining_hard_gaps=[],
        )
    if run.status != "failed":
        return None
    detail = _latest_failure_summary(traces) or (
        "Research stopped before a final result was committed."
    )
    failure = _failure_view(run, traces)
    # A synthesis attestation failure means the runtime did work and trusted
    # progress exists, but directional semantics are unsafe to publish. Keep
    # that distinction visible instead of collapsing it into runtime outage.
    code = (
        "critical_data_unavailable"
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
    coverage: CoverageAssessment | None,
    requirements: Iterable[EvidenceRequirement],
) -> list[ResearchRound]:
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
    projected: list[ResearchRound] = []
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
        round_coverage = coverage
        if round_coverage is None:
            round_coverage = assess_evidence_sufficiency(
                requirement_list,
                round_evidence,
                cutoff_at=_required_time(finished_at),
                assessed_at=_required_time(finished_at),
            )
        projected.append(
            ResearchRound(
                round=round_number,
                plan=plan,
                tool_invocations=invocations,
                tool_results=tool_results,
                new_evidence_refs=[item.evidence_id for item in round_evidence],
                coverage=round_coverage,
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
                for item in reversed(rows)
                if item.event_type in {"tool_completed", "tool_failed"}
            ),
            None,
        )
        status = "running" if terminal is None else (
            "failed" if terminal.event_type == "tool_failed" else "succeeded"
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
                        _required_time(finished_at)
                        - _required_time(as_utc(started.occurred_at))
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


def _latest_failure_summary(traces: list[ResearchTraceRecord]) -> str | None:
    meaningful = _meaningful_trace_failures(traces)
    return meaningful[-1][0].summary if meaningful else None
