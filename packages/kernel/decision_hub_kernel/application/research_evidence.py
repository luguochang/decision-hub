from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import unquote, urlsplit

from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
    ProviderAttempt,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchSnapshotManifest,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchEvidenceRecord,
    RunRecord,
    SnapshotRecord,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter

ADAPTER_RETURN_MAX_UTF8_BYTES = 64 * 1024 * 1024
ADAPTER_QUERY_MAX_UTF8_BYTES = 64 * 1024 * 1024
_CAPABILITY_FINALIZATION_RESERVE_MAX_SECONDS = 0.5
_CAPABILITY_FINALIZATION_RESERVE_FRACTION = 0.1


@dataclass(frozen=True)
class CapabilityAdapterReturn:
    """Original adapter typed JSON; this is not a Provider byte/time witness."""

    effective_query_json: str
    result_json: str
    server_received_at: datetime

    @property
    def result_hash(self) -> str:
        return hashlib.sha256(self.result_json.encode("utf-8")).hexdigest()

    def result(self) -> ResearchCapabilityResult:
        return ResearchCapabilityResult.model_validate_json(self.result_json)


@dataclass
class CapabilityAdapterReturnCollector:
    returned: bool = False
    observation: CapabilityAdapterReturn | None = None

    def capture(
        self,
        query: ResearchCapabilityQuery,
        result: ResearchCapabilityResult,
        server_received_at: datetime,
    ) -> ResearchCapabilityResult:
        if self.returned:
            raise ValueError("research_adapter_return_observation_conflict")
        # Mark the physical return before serialization: a snapshot error cannot
        # make the already returned call an unexecuted, failed/free retry.
        self.returned = True
        raw = _adapter_snapshot_json(result.model_dump(mode="json"), ADAPTER_RETURN_MAX_UTF8_BYTES)
        actual_query = _adapter_snapshot_json(
            query.model_dump(mode="json"), ADAPTER_QUERY_MAX_UTF8_BYTES
        )
        self.observation = CapabilityAdapterReturn(actual_query, raw, server_received_at)
        # Validate the original checks against an independent snapshot, never a
        # mutable object still owned by the adapter or an archive resolver.
        return self.observation.result()


_ADAPTER_RETURN_COLLECTOR: ContextVar[CapabilityAdapterReturnCollector | None] = ContextVar(
    "research_capability_adapter_return", default=None
)


@contextmanager
def observe_capability_adapter_return() -> Generator[CapabilityAdapterReturnCollector]:
    """Borrow no DB resources; scope/reset a single trusted Kernel return hook."""
    collector = CapabilityAdapterReturnCollector()
    token = _ADAPTER_RETURN_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _ADAPTER_RETURN_COLLECTOR.reset(token)


def _adapter_snapshot_json(value: object, maximum: int) -> str:
    # This cap is checked after a non-streaming, in-memory typed projection. It
    # bounds persisted UTF8 bytes, not Python peak allocation or Provider bytes.
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("research_adapter_return_snapshot_too_large")
    return encoded


class ResearchCapabilityError(RuntimeError):
    """Stable capability failure with enough provenance for retry and diagnosis."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
        origin: str = "gateway",
        cause_code: str | None = None,
        capability_id: str | None = None,
        tool_call_id: str | None = None,
        deadline_ms: int | None = None,
        provider_attempts: Iterable[ProviderAttempt] = (),
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.origin = origin
        self.cause_code = cause_code
        self.capability_id = capability_id
        self.tool_call_id = tool_call_id
        self.deadline_ms = deadline_ms
        self.provider_attempts = tuple(provider_attempts)
        # Internal recovery evidence, never added to the public error DTO.
        self.adapter_return_observation: CapabilityAdapterReturn | None = None

    def with_context(
        self,
        *,
        capability_id: str | None = None,
        deadline_ms: int | None = None,
        tool_call_id: str | None = None,
    ) -> ResearchCapabilityError:
        return ResearchCapabilityError(
            self.error_code,
            str(self),
            retryable=self.retryable,
            origin=self.origin,
            cause_code=self.cause_code,
            capability_id=self.capability_id or capability_id,
            tool_call_id=self.tool_call_id or tool_call_id,
            deadline_ms=self.deadline_ms if self.deadline_ms is not None else deadline_ms,
            provider_attempts=self.provider_attempts,
        )

    def with_provider_attempts(
        self,
        provider_attempts: Iterable[ProviderAttempt],
    ) -> ResearchCapabilityError:
        return ResearchCapabilityError(
            self.error_code,
            str(self),
            retryable=self.retryable,
            origin=self.origin,
            cause_code=self.cause_code,
            capability_id=self.capability_id,
            tool_call_id=self.tool_call_id,
            deadline_ms=self.deadline_ms,
            provider_attempts=provider_attempts,
        )

    def provenance(self) -> ErrorProvenance:
        return ErrorProvenance(
            error_code=self.error_code,
            origin=self.origin,  # type: ignore[arg-type]
            cause_code=self.cause_code,
            capability_id=self.capability_id,
            tool_call_id=self.tool_call_id,
            retryable=self.retryable,
            deadline_ms=self.deadline_ms,
            provider_attempts=list(self.provider_attempts),
        )


def research_evidence_content_hash(
    *,
    requirement_id: str,
    kind: str,
    authority: str,
    source_id: str,
    source_url: str | None,
    published_at: datetime | None,
    excerpt: str,
    structured_payload_ref: str | None,
) -> str:
    semantic = {
        "authority": authority,
        "excerpt": excerpt,
        "kind": kind,
        "published_at": published_at.isoformat() if published_at is not None else None,
        "requirement_id": requirement_id,
        "source_id": source_id,
        "source_url": source_url,
        "structured_payload_ref": structured_payload_ref,
    }
    encoded = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def research_evidence_instance_id(
    *,
    content_hash: str,
    research_session_id: str,
    content_ref: str | None = None,
    acquisition_receipt_id: str | None = None,
) -> str:
    """Scope evidence to its session and immutable original acquisition.

    Equal excerpts can come from different original archives or acquisitions.
    Keep those identities distinct while preserving legacy IDs without a receipt.
    Re-reading or reusing the same original keeps the same identity in a session.
    """

    encoded = f"{research_session_id}:{content_hash}".encode()
    if content_ref is not None or acquisition_receipt_id is not None:
        encoded = json.dumps(
            [
                "research-evidence-instance.v2",
                research_session_id,
                content_hash,
                content_ref,
                acquisition_receipt_id,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    return f"ev_{hashlib.sha256(encoded).hexdigest()[:32]}"


class ResearchCapabilityGatewayService:
    """Deny-by-default execution gate shared by DSH, replay and product orchestration."""

    def __init__(
        self,
        manifests: Iterable[ResearchCapabilityManifest],
        adapters: Iterable[ResearchCapabilityAdapter],
        *,
        enabled_capabilities: Iterable[str],
        clock: Callable[[], datetime] = utcnow,
        archived_observation_resolver: Callable[[], Mapping[str, datetime]] | None = None,
    ) -> None:
        self._manifests = _unique_by_id(manifests, "capability_id")
        self._adapters = _unique_by_id(adapters, "capability_id")
        self._enabled = frozenset(enabled_capabilities)
        self._clock = clock
        self._archived_observation_resolver = archived_observation_resolver

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        server_received_at = _server_time(self._clock)
        manifest = self._manifests.get(query.capability_id)
        adapter = self._adapters.get(query.capability_id)
        if query.capability_id not in self._enabled or manifest is None or adapter is None:
            raise ResearchCapabilityError(
                "research_capability_not_enabled",
                "research capability is not explicitly enabled with an adapter",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if manifest.license_status != "approved" or manifest.audit_status != "approved":
            raise ResearchCapabilityError(
                "research_capability_not_audited",
                "research capability license and audit must be approved before execution",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if query.mode not in adapter.supported_modes:
            raise ResearchCapabilityError(
                "research_capability_mode_denied",
                "research capability adapter does not support the requested execution mode",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        if query.mode == "live" and server_received_at > query.cutoff_at:
            raise ResearchCapabilityError(
                "research_pit_violation",
                "server received the live capability request after its PIT cutoff",
                origin="pit",
                cause_code="live_request_after_cutoff",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
            )
        try:
            effective_domains = _effective_domains(query, manifest)
            _validate_target_url(
                str(query.target_url) if query.target_url is not None else None,
                effective_domains,
            )
        except asyncio.CancelledError:
            raise
        except ResearchCapabilityError as exc:
            raise exc.with_context(
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(manifest.timeout_seconds * 1000),
            ) from exc.__cause__
        execution_timeout_seconds = _capability_execution_timeout_seconds(
            query,
            manifest_timeout_seconds=float(manifest.timeout_seconds),
            server_received_at=server_received_at,
        )
        try:
            if execution_timeout_seconds <= 0:
                raise ResearchCapabilityError(
                    "research_capability_timeout",
                    "research capability had no remaining execution time",
                    retryable=True,
                    origin="transport",
                    cause_code="capability_deadline",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                    deadline_ms=0,
                )
            effective_update: dict[str, object] = {
                "requested_observed_at": query.observed_at,
                "observed_at": server_received_at,
            }
            if bool(getattr(adapter, "uses_capability_deadline", False)):
                # Routers must finish before the Gateway's audited outer timeout
                # so a completed venue and its attempt trail can be returned and
                # admitted even when a later venue consumes its local budget.
                reserve_seconds = _capability_finalization_reserve_seconds(
                    execution_timeout_seconds
                )
                effective_update["deadline_at"] = server_received_at + timedelta(
                    seconds=execution_timeout_seconds - reserve_seconds
                )
            effective_query = query.model_copy(
                update=effective_update
            )
            async with asyncio.timeout(execution_timeout_seconds):
                result = await adapter.execute(effective_query)
                collector = _ADAPTER_RETURN_COLLECTOR.get()
                if collector is not None:
                    try:
                        result = collector.capture(effective_query, result, server_received_at)
                    except Exception as exc:
                        raise ResearchCapabilityError(
                            "research_adapter_return_snapshot_unavailable",
                            "Already returned adapter output could not be snapshotted",
                            origin="gateway",
                            cause_code=type(exc).__name__.lower(),
                            capability_id=query.capability_id,
                            tool_call_id=query.request_id,
                        ) from exc
            server_completed_at = _server_time(self._clock)
        except asyncio.CancelledError:
            # Owner cancellation and worker shutdown are control flow, not a
            # provider failure. Preserve the cancellation signal so callers
            # can release their task/lease without recording a retryable error.
            raise
        except TimeoutError as exc:
            raise ResearchCapabilityError(
                "research_capability_timeout",
                "research capability exceeded its audited timeout",
                retryable=True,
                origin="transport",
                cause_code="capability_deadline",
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(execution_timeout_seconds * 1000),
            ) from exc
        except ResearchCapabilityError as exc:
            raise exc.with_context(
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(execution_timeout_seconds * 1000),
            ) from exc.__cause__
        except Exception as exc:
            error_code = getattr(exc, "error_code", None)
            if not isinstance(error_code, str) or not error_code:
                error_code = "search_provider_failed"
            raise ResearchCapabilityError(
                error_code,
                "research capability adapter failed",
                retryable=bool(getattr(exc, "retryable", True)),
                origin=str(getattr(exc, "origin", "provider")),
                cause_code=getattr(exc, "cause_code", type(exc).__name__.lower()),
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(execution_timeout_seconds * 1000),
            ) from exc
        if query.mode == "live":
            if server_completed_at > query.cutoff_at:
                raise ResearchCapabilityError(
                    "research_pit_violation",
                    "server received the live capability result after its PIT cutoff",
                    origin="pit",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                    deadline_ms=round(execution_timeout_seconds * 1000),
                )
            try:
                result = _normalize_live_result(
                    result,
                    server_completed_at,
                    self._archived_observation_resolver()
                    if self._archived_observation_resolver
                    else {},
                )
            except ResearchCapabilityError as exc:
                raise exc.with_context(
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                    deadline_ms=round(execution_timeout_seconds * 1000),
                ) from exc.__cause__
        try:
            self._validate_result(query, result, effective_domains)
        except ResearchCapabilityError as exc:
            raise exc.with_context(
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(execution_timeout_seconds * 1000),
            ) from exc.__cause__
        return result

    @staticmethod
    def _validate_result(
        query: ResearchCapabilityQuery,
        result: ResearchCapabilityResult,
        effective_domains: frozenset[str],
    ) -> None:
        if result.request_id != query.request_id or result.capability_id != query.capability_id:
            raise ResearchCapabilityError(
                "research_lineage_mismatch", "capability result does not match its request"
            )
        if len(result.evidence_candidates) > query.max_results:
            raise ResearchCapabilityError(
                "research_result_limit_exceeded", "capability returned too many candidates"
            )
        if result.completed_at > query.cutoff_at:
            raise ResearchCapabilityError(
                "research_pit_violation", "capability completed after the PIT cutoff"
            )
        if query.max_cost_usd is not None:
            if result.cost_usd is None:
                raise ResearchCapabilityError(
                    "research_cost_unknown", "bounded capability did not report its cost"
                )
            if result.cost_usd > query.max_cost_usd:
                raise ResearchCapabilityError(
                    "research_budget_exceeded", "capability exceeded the requested cost budget"
                )
        evidence_ids: set[str] = set()
        for candidate in result.evidence_candidates:
            if candidate.evidence_id in evidence_ids:
                raise ResearchCapabilityError(
                    "research_evidence_duplicate", "capability returned duplicate evidence ids"
                )
            evidence_ids.add(candidate.evidence_id)
            _validate_candidate(query, candidate, effective_domains)
        fact_ids: set[str] = set()
        candidates_by_id = {item.evidence_id: item for item in result.evidence_candidates}
        for fact in result.facts or []:
            if fact.fact_id in fact_ids:
                raise ResearchCapabilityError(
                    "research_fact_duplicate", "capability returned duplicate fact ids"
                )
            fact_ids.add(fact.fact_id)
            _validate_fact(query, fact, candidates_by_id)


def _source_public_time_unknown_v2(candidate: EvidenceCandidate) -> bool:
    """A fetched current FRED CSV does not establish its public release time.

    This predicate only classifies future admission/checker execution. It does
    not mutate a stored FactVersion or turn a date into a publication instant.
    Other market sources retain their existing observation-time policy.
    """
    return candidate.published_at is None and (
        candidate.source_id == "fred-public" or candidate.kind != "market"
    )


class ResearchEvidenceService:
    """Persist validated evidence revisions and freeze an immutable decision snapshot."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def accept_candidates(
        self,
        *,
        run_id: str,
        capability_id: str,
        candidates: Iterable[EvidenceCandidate],
        requirements: Mapping[str, EvidenceRequirement],
        cutoff_at: datetime,
        facts: Iterable[FactEnvelope] = (),
    ) -> list[EvidenceCandidate]:
        with self.database.session() as session:
            return self.accept_candidates_in_session(
                session,
                run_id=run_id,
                capability_id=capability_id,
                candidates=candidates,
                requirements=requirements,
                cutoff_at=cutoff_at,
                facts=facts,
            )

    def accept_candidates_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        capability_id: str,
        candidates: Iterable[EvidenceCandidate],
        requirements: Mapping[str, EvidenceRequirement],
        cutoff_at: datetime,
        facts: Iterable[FactEnvelope] = (),
    ) -> list[EvidenceCandidate]:
        """Persist candidates using a caller-owned transaction.

        The durable gateway uses this method together with
        ``ResearchFactStore.accept_facts_in_session`` so evidence and typed
        facts either commit together or roll back together.
        """
        facts = tuple(facts)
        facts_by_evidence: dict[str, list[FactEnvelope]] = defaultdict(list)
        fact_offsets_by_evidence: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
        for fact in facts:
            facts_by_evidence[fact.evidence_id].append(fact)
            fact_offsets_by_evidence[fact.evidence_id].add((fact.requirement_id, fact.event_offset))
        accepted: list[EvidenceCandidate] = []
        if session.get(RunRecord, run_id) is None:
            raise KeyError(run_id)
        for candidate in candidates:
            requirement = requirements.get(candidate.requirement_id)
            if requirement is None:
                raise ValueError("research_requirement_not_found")
            _validate_candidate_pit(candidate, cutoff_at)
            _validate_acquisition_receipt(session, candidate)
            freshness_anchor = candidate.published_at or candidate.observed_at
            age_seconds = (cutoff_at - freshness_anchor).total_seconds()
            offsets = fact_offsets_by_evidence.get(candidate.evidence_id, set())
            is_required_baseline = offsets == {
                (candidate.requirement_id, "baseline")
            } and "baseline" in (requirement.required_event_offsets or [])
            freshness = _yahoo_daily_freshness(
                candidate,
                facts_by_evidence[candidate.evidence_id],
                cutoff_at,
                requirement.freshness_seconds,
            ) if candidate.source_id == "yahoo-finance-public" else (
                "unknown"
                if _source_public_time_unknown_v2(candidate)
                else "fresh"
                if is_required_baseline or 0 <= age_seconds <= requirement.freshness_seconds
                else "stale"
            )
            # Search results are locators, not read/verified source content.
            # Keep them visible for follow-up fetches but never let a fresh
            # snippet satisfy coverage or become accepted claim evidence.
            quality = (
                "candidate"
                if candidate.authority == "search_derived" or freshness == "unknown"
                else "accepted"
                if freshness == "fresh"
                else "stale"
            )
            normalized = candidate.model_copy(
                update={"freshness_status": freshness, "quality": quality}
            )
            existing = session.get(ResearchEvidenceRecord, normalized.evidence_id)
            if existing is not None:
                if (
                    existing.content_hash != normalized.content_hash
                    or existing.run_id != run_id
                    or existing.content_ref != normalized.content_ref
                    or existing.acquisition_receipt_id != normalized.acquisition_receipt_id
                    or existing.source_content_hash != normalized.source_content_hash
                    or existing.parser_ref != normalized.parser_ref
                ):
                    raise ValueError("research_evidence_identity_conflict")
                accepted.append(_evidence_model(existing))
                continue
            session.add(
                ResearchEvidenceRecord(
                    evidence_id=normalized.evidence_id,
                    run_id=run_id,
                    capability_id=capability_id,
                    requirement_id=normalized.requirement_id,
                    kind=normalized.kind,
                    authority=normalized.authority,
                    source_id=normalized.source_id,
                    source_url=str(normalized.source_url) if normalized.source_url else None,
                    published_at=normalized.published_at,
                    observed_at=normalized.observed_at,
                    received_at=normalized.received_at,
                    content_hash=normalized.content_hash,
                    excerpt=normalized.excerpt,
                    content_ref=normalized.content_ref,
                    acquisition_receipt_id=normalized.acquisition_receipt_id,
                    source_content_hash=normalized.source_content_hash,
                    parser_ref=normalized.parser_ref,
                    structured_payload_ref=normalized.structured_payload_ref,
                    tool_call_id=normalized.tool_call_id,
                    research_session_id=normalized.research_session_id,
                    round=normalized.round,
                    quality=normalized.quality,
                    freshness_status=normalized.freshness_status,
                    conflict_group=normalized.conflict_group,
                    accepted_at=utcnow(),
                )
            )
            accepted.append(normalized)
        return accepted

    def list_run_evidence(self, run_id: str) -> list[EvidenceCandidate]:
        with self.database.session() as session:
            return self.list_run_evidence_in_session(session, run_id)

    @staticmethod
    def list_run_evidence_in_session(session: Session, run_id: str) -> list[EvidenceCandidate]:
        rows = (
            session.query(ResearchEvidenceRecord)
            .filter_by(run_id=run_id)
            .order_by(ResearchEvidenceRecord.accepted_at, ResearchEvidenceRecord.evidence_id)
            .populate_existing()
            .all()
        )
        return [_evidence_model(row) for row in rows]

    def get_session_evidence(
        self,
        *,
        evidence_id: str,
        research_session_id: str,
    ) -> EvidenceCandidate:
        """Resolve Evidence only inside its owning durable research session."""

        with self.database.session() as session:
            row = session.get(ResearchEvidenceRecord, evidence_id)
            if row is None:
                raise ValueError("research_evidence_not_found")
            if row.research_session_id != research_session_id:
                raise ValueError("research_evidence_scope_denied")
            return _evidence_model(row)

    def freeze_decision_snapshot(
        self,
        *,
        run_id: str,
        evidence_refs: Iterable[str],
        cutoff_at: datetime,
    ) -> ResearchSnapshotManifest:
        with self.database.session() as session:
            return self.freeze_decision_snapshot_in_session(
                session,
                run_id=run_id,
                evidence_refs=evidence_refs,
                cutoff_at=cutoff_at,
            )

    def freeze_decision_snapshot_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        evidence_refs: Iterable[str],
        cutoff_at: datetime,
    ) -> ResearchSnapshotManifest:
        selected_refs = tuple(dict.fromkeys(evidence_refs))
        run = session.get(RunRecord, run_id, populate_existing=True)
        if run is None:
            raise KeyError(run_id)
        if run.snapshot_id is None:
            raise ValueError("trigger_snapshot_required")
        trigger = session.get(SnapshotRecord, run.snapshot_id, populate_existing=True)
        if trigger is None:
            raise KeyError(run.snapshot_id)
        trigger_cutoff = as_utc(trigger.cutoff_at)
        assert trigger_cutoff is not None
        if cutoff_at < trigger_cutoff:
            raise ValueError("decision_snapshot_before_trigger")
        trigger_evidence = json.loads(trigger.evidence_json)
        if not isinstance(trigger_evidence, list) or not trigger_evidence:
            raise ValueError("trigger_snapshot_evidence_invalid")
        trigger_refs = [
            str(item["evidence_id"])
            for item in trigger_evidence
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
        ]
        if len(trigger_refs) != len(trigger_evidence):
            raise ValueError("trigger_snapshot_evidence_invalid")
        combined_refs = tuple(dict.fromkeys((*trigger_refs, *selected_refs)))
        if run.decision_snapshot_id:
            frozen = session.get(SnapshotRecord, run.decision_snapshot_id, populate_existing=True)
            if frozen is None:
                raise KeyError(run.decision_snapshot_id)
            manifest = _snapshot_manifest(frozen, run_id)
            if set(manifest.evidence_refs) != set(combined_refs):
                raise ValueError("decision_snapshot_already_frozen")
            return manifest

        rows = [
            session.get(ResearchEvidenceRecord, item, populate_existing=True)
            for item in selected_refs
        ]
        if any(row is None for row in rows):
            raise ValueError("decision_snapshot_evidence_not_found")
        evidence_rows = [row for row in rows if row is not None]
        if any(row.run_id != run_id for row in evidence_rows):
            raise ValueError("decision_snapshot_evidence_run_mismatch")
        if any((as_utc(row.received_at) or cutoff_at) > cutoff_at for row in evidence_rows):
            raise ValueError("pit_future_information")

        research_evidence = [
            item.model_dump(mode="json")
            for item in sorted(
                (_evidence_model(row) for row in evidence_rows),
                key=lambda item: item.evidence_id,
            )
        ]
        evidence = [*trigger_evidence, *research_evidence]
        semantic = {
            "cutoff_at": cutoff_at.isoformat(),
            "event_id": run.event_id,
            "evidence": evidence,
            "generation": 2,
            "parent_snapshot_id": trigger.snapshot_id,
            "run_id": run_id,
            "snapshot_type": "decision",
        }
        snapshot_hash = hashlib.sha256(
            json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        snapshot = SnapshotRecord(
            snapshot_id=f"snap_dec_{snapshot_hash[:28]}",
            event_id=run.event_id,
            cutoff_at=cutoff_at,
            snapshot_hash=snapshot_hash,
            evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            pack_version=trigger.pack_version,
            snapshot_type="decision",
            run_id=run_id,
            parent_snapshot_id=trigger.snapshot_id,
            generation=2,
            created_at=utcnow(),
        )
        session.add(snapshot)
        session.flush()
        run.decision_snapshot_id = snapshot.snapshot_id
        run.updated_at = utcnow()
        return _snapshot_manifest(snapshot, run_id)


def _unique_by_id[IdentityItem](
    items: Iterable[IdentityItem], field: str
) -> dict[str, IdentityItem]:
    result: dict[str, IdentityItem] = {}
    for item in items:
        identity = getattr(item, field)
        if not isinstance(identity, str) or identity in result:
            raise ValueError(f"duplicate_or_invalid_{field}")
        result[identity] = item
    return result


def _effective_domains(
    query: ResearchCapabilityQuery, manifest: ResearchCapabilityManifest
) -> frozenset[str]:
    allowed = frozenset(_normalize_domain(item) for item in manifest.allowed_domains)
    requested = frozenset(_normalize_domain(item) for item in query.allowed_domains)
    broad = "search:broad" in manifest.permissions
    if (
        requested
        and not broad
        and not all(
            any(_domain_matches(domain, candidate) for candidate in allowed) for domain in requested
        )
    ):
        raise ResearchCapabilityError(
            "research_domain_denied",
            "requested domains exceed the capability allowlist",
            cause_code="requested_domain_not_allowlisted",
        )
    if (
        not requested
        and not allowed
        and not broad
        and manifest.kind != "replay"
        and "network:https" in manifest.permissions
    ):
        raise ResearchCapabilityError(
            "research_broad_permission_required", "broad network capability was not audited"
        )
    return requested or allowed


def _normalize_domain(value: str) -> str:
    normalized = value.strip().lower().rstrip(".")
    if not normalized or "://" in normalized or "/" in normalized or ":" in normalized:
        raise ResearchCapabilityError(
            "research_domain_invalid", "allowed domains must be plain DNS names"
        )
    return normalized.removeprefix(".")


def _domain_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith(f".{allowed}")


def _validate_target_url(url: str | None, domains: frozenset[str]) -> None:
    if url is None:
        return
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host or (domains and not any(_domain_matches(host, item) for item in domains)):
        raise ResearchCapabilityError(
            "research_target_domain_denied",
            "target URL is outside the effective allowlist",
            cause_code="target_domain_not_allowlisted",
        )


def _validate_candidate(
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    domains: frozenset[str],
) -> None:
    if (
        candidate.requirement_id != query.requirement_id
        or candidate.research_session_id != query.research_session_id
        or candidate.round != query.round
    ):
        raise ResearchCapabilityError(
            "research_lineage_mismatch", "evidence candidate lineage does not match the query"
        )
    _validate_target_url(str(candidate.source_url) if candidate.source_url else None, domains)
    _validate_candidate_pit(candidate, query.cutoff_at)
    document_lineage = (
        candidate.content_ref,
        candidate.source_content_hash,
        candidate.parser_ref,
    )
    if any(value is not None for value in document_lineage) and not all(
        value is not None for value in document_lineage
    ):
        raise ResearchCapabilityError(
            "research_document_lineage_incomplete",
            "document content_ref, body hash and parser_ref must be supplied together",
        )
    if candidate.content_ref is not None and not candidate.content_ref.startswith(
        "document://sha256/"
    ):
        raise ResearchCapabilityError(
            "research_document_content_ref_invalid",
            "document content_ref is not an approved opaque reference",
        )
    expected_hash = research_evidence_content_hash(
        requirement_id=candidate.requirement_id,
        kind=candidate.kind,
        authority=candidate.authority,
        source_id=candidate.source_id,
        source_url=str(candidate.source_url) if candidate.source_url else None,
        published_at=candidate.published_at,
        excerpt=candidate.excerpt,
        structured_payload_ref=candidate.structured_payload_ref,
    )
    if candidate.content_hash != expected_hash:
        raise ResearchCapabilityError(
            "research_content_hash_mismatch", "evidence candidate content hash is invalid"
        )


def _validate_candidate_pit(candidate: EvidenceCandidate, cutoff_at: datetime) -> None:
    if candidate.published_at is not None and candidate.published_at > candidate.observed_at:
        raise ResearchCapabilityError("research_pit_violation", "published_at is after observed_at")
    if candidate.observed_at > candidate.received_at or candidate.received_at > cutoff_at:
        raise ResearchCapabilityError(
            "research_pit_violation", "candidate timestamps violate PIT ordering"
        )


def _validate_acquisition_receipt(session: Session, candidate: EvidenceCandidate) -> None:
    """Require a successful, same-source acquisition before admitting browser evidence.

    The receipt is deliberately a Kernel admission concern.  A browser worker
    can collect artifacts, but it cannot make them research evidence by merely
    posting a JSON payload.  This prevents unverified DOM/screenshot claims
    from silently entering the decision graph.
    """

    receipt_id = candidate.acquisition_receipt_id
    if receipt_id is None:
        return
    receipt = session.get(SourceAcquisitionRecord, receipt_id)
    if receipt is None:
        raise ValueError("acquisition_receipt_not_found")
    if receipt.status != "succeeded":
        raise ValueError("acquisition_receipt_not_succeeded")
    if receipt.source_id != candidate.source_id:
        raise ValueError("acquisition_receipt_source_mismatch")


def _validate_fact(
    query: ResearchCapabilityQuery,
    fact: FactEnvelope,
    candidates_by_id: Mapping[str, EvidenceCandidate],
) -> None:
    evidence = candidates_by_id.get(fact.evidence_id)
    if evidence is None:
        raise ResearchCapabilityError(
            "research_fact_evidence_not_found",
            "typed fact does not reference evidence from the same capability result",
        )
    if (
        fact.requirement_id != query.requirement_id
        or evidence.requirement_id != fact.requirement_id
        or evidence.source_id != fact.source_id
    ):
        raise ResearchCapabilityError(
            "research_fact_lineage_mismatch",
            "typed fact lineage does not match its query and evidence",
        )
    if fact.published_at is not None and fact.published_at > fact.observed_at:
        raise ResearchCapabilityError(
            "research_fact_pit_violation", "fact published_at is after observed_at"
        )
    if fact.observed_at > fact.received_at or fact.received_at > query.cutoff_at:
        raise ResearchCapabilityError(
            "research_fact_pit_violation", "fact timestamps violate PIT ordering"
        )
    if (fact.window_start_at is None) != (fact.window_end_at is None):
        raise ResearchCapabilityError(
            "research_fact_window_invalid", "fact window must contain both boundaries"
        )
    if fact.window_start_at is not None and fact.window_end_at is not None:
        if fact.window_start_at > fact.window_end_at or fact.window_end_at > fact.observed_at:
            raise ResearchCapabilityError(
                "research_fact_window_invalid", "fact window ordering is invalid"
            )
    expected_hash = research_fact_payload_hash(
        requirement_id=fact.requirement_id,
        metric_family=fact.metric_family,
        field=fact.field,
        instrument=fact.instrument,
        venue=fact.venue,
        value=fact.value,
        unit=fact.unit,
        window_start_at=fact.window_start_at,
        window_end_at=fact.window_end_at,
        event_offset=fact.event_offset,
        published_at=fact.published_at,
        source_id=fact.source_id,
        independence_group=fact.independence_group,
        delay_class=fact.delay_class,
        payload_schema_ref=fact.payload_schema_ref,
        attributes=fact.attributes,
    )
    if fact.payload_hash != expected_hash:
        raise ResearchCapabilityError(
            "research_fact_hash_mismatch", "typed fact payload hash is invalid"
        )


_YAHOO_COMPLETION_CONTRACT = "yahoo-provider-declared-daily.v2"
_YAHOO_COMPLETION_FIELDS = (
    "completion_contract", "daily_bar_start_at", "daily_completion_not_after_at",
    "daily_completion_basis", "daily_snapshot_requested_at",
)


def _daily_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("research_daily_completion_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("research_daily_completion_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("research_daily_completion_invalid")
    return parsed.astimezone(UTC)


def _yahoo_daily_freshness(
    candidate: EvidenceCandidate,
    facts: list[FactEnvelope],
    cutoff_at: datetime,
    freshness_seconds: int,
) -> Literal["fresh", "stale", "unknown"]:
    """Integrity of the trusted adapter's interval, not an independent reparser."""
    try:
        excerpt = json.loads(candidate.excerpt)
    except (ValueError, TypeError):
        excerpt = None
    fact_declares_v2 = any(
        fact.attributes.get("completion_contract") == _YAHOO_COMPLETION_CONTRACT
        for fact in facts
    )
    if not isinstance(excerpt, dict) or (
        excerpt.get("completion_contract") != _YAHOO_COMPLETION_CONTRACT
    ):
        if fact_declares_v2:
            raise ValueError("research_daily_completion_identity_mismatch")
        # Old/unknown daily metadata cannot borrow fresh receive time or the
        # ordinary required-baseline exception to become a completed interval.
        return "unknown"
    if not facts:
        raise ValueError("research_daily_completion_fact_missing")
    coordinates = {key: excerpt.get(key) for key in _YAHOO_COMPLETION_FIELDS}
    start = _daily_time(coordinates["daily_bar_start_at"])
    completion = _daily_time(coordinates["daily_completion_not_after_at"])
    sampled = _daily_time(coordinates["daily_snapshot_requested_at"])
    basis = coordinates["daily_completion_basis"]
    if (
        basis not in {"provider_current_regular_end", "next_daily_bar_start"}
        or not start < completion <= sampled <= candidate.observed_at
        or candidate.published_at is not None
        or candidate.kind != "market"
        or candidate.authority != "audited_aggregator"
        or excerpt.get("provider") != candidate.source_id
    ):
        raise ValueError("research_daily_completion_invalid")
    expected_content_hash = research_evidence_content_hash(
        requirement_id=candidate.requirement_id, kind=candidate.kind,
        authority=candidate.authority, source_id=candidate.source_id,
        source_url=str(candidate.source_url) if candidate.source_url else None,
        published_at=candidate.published_at, excerpt=candidate.excerpt,
        structured_payload_ref=candidate.structured_payload_ref,
    )
    expected_evidence_id = research_evidence_instance_id(
        content_hash=expected_content_hash, research_session_id=candidate.research_session_id,
        content_ref=candidate.content_ref, acquisition_receipt_id=candidate.acquisition_receipt_id,
    )
    if (
        candidate.content_hash != expected_content_hash
        or candidate.evidence_id != expected_evidence_id
    ):
        raise ValueError("research_daily_completion_identity_mismatch")
    url = urlsplit(str(candidate.source_url))
    for fact in facts:
        attributes = fact.attributes
        if (
            fact.evidence_id != candidate.evidence_id
            or fact.requirement_id != candidate.requirement_id
            or fact.source_id != candidate.source_id
            or fact.published_at is not None
            or fact.observed_at != candidate.observed_at
            or fact.received_at != candidate.received_at
            or fact.instrument != excerpt.get("instrument")
            or fact.independence_group != f"yahoo-market:{fact.instrument}"
            or fact.payload_schema_ref != "yahoo.chart.daily.v8"
            or attributes.get("completed_daily_only") is not True
            or any(attributes.get(key) != value for key, value in coordinates.items())
            or url.scheme != "https" or url.hostname != "query1.finance.yahoo.com"
            or unquote(url.path) != f"/v8/finance/chart/{attributes.get('provider_symbol')}"
            or (fact.field != "level" and fact.window_end_at != start)
        ):
            raise ValueError("research_daily_completion_identity_mismatch")
        expected_hash = research_fact_payload_hash(
            requirement_id=fact.requirement_id, metric_family=fact.metric_family,
            field=fact.field, instrument=fact.instrument, venue=fact.venue,
            value=fact.value, unit=fact.unit, window_start_at=fact.window_start_at,
            window_end_at=fact.window_end_at, event_offset=fact.event_offset,
            published_at=fact.published_at, source_id=fact.source_id,
            independence_group=fact.independence_group, delay_class=fact.delay_class,
            payload_schema_ref=fact.payload_schema_ref, attributes=attributes,
        )
        if fact.payload_hash != expected_hash or fact.fact_id != research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=expected_hash
        ):
            raise ValueError("research_daily_completion_identity_mismatch")
    # A historical next start is only a completion upper bound. Use the entire
    # possible completion-age interval; a holiday gap must not refresh a value.
    oldest_completion = completion if basis == "provider_current_regular_end" else start
    if 0 <= (cutoff_at - oldest_completion).total_seconds() <= freshness_seconds:
        return "fresh"
    if (cutoff_at - completion).total_seconds() > freshness_seconds:
        return "stale"
    return "unknown"


def _capability_execution_timeout_seconds(
    query: ResearchCapabilityQuery,
    *,
    manifest_timeout_seconds: float,
    server_received_at: datetime,
) -> float:
    """Return the one physical budget shared by the Gateway and its Router.

    The durable Run ceiling can only shorten the audited capability timeout.
    A Router receives a slightly earlier deadline below; the outer timeout
    retains the rest solely for returning and freezing a partial result.
    """

    timeout_seconds = max(0.0, manifest_timeout_seconds)
    if query.deadline_at is None:
        return timeout_seconds
    deadline_at = query.deadline_at
    if deadline_at.tzinfo is None or deadline_at.utcoffset() is None:
        raise ValueError("research_capability_deadline_must_be_aware")
    remaining_seconds = (deadline_at.astimezone(UTC) - server_received_at).total_seconds()
    return max(0.0, min(timeout_seconds, remaining_seconds))


def _capability_finalization_reserve_seconds(execution_timeout_seconds: float) -> float:
    if execution_timeout_seconds <= 0:
        return 0.0
    return min(
        _CAPABILITY_FINALIZATION_RESERVE_MAX_SECONDS,
        execution_timeout_seconds * _CAPABILITY_FINALIZATION_RESERVE_FRACTION,
        execution_timeout_seconds / 2,
    )


def _server_time(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("server clock must return an aware datetime")
    return value.astimezone(UTC)


def _normalize_live_result(
    result: ResearchCapabilityResult,
    received_at: datetime,
    archived_observations: Mapping[str, datetime] | None = None,
) -> ResearchCapabilityResult:
    """Replace provider/model timestamps with the gateway receive instant.

    Provider publication time remains useful as ``published_at``; only the
    product-owned observation and receipt timestamps are authoritative for PIT.
    Trusted archive observations are supplied by composition after a verified
    cache hit, never by a query or model-controlled result field.
    """

    facts = result.facts or []
    if (
        result.completed_at > received_at
        or any(
            candidate.observed_at > received_at or candidate.received_at > received_at
            for candidate in result.evidence_candidates
        )
        or any(fact.observed_at > received_at or fact.received_at > received_at for fact in facts)
    ):
        raise ResearchCapabilityError(
            "research_pit_violation",
            "provider supplied a timestamp later than the server receive instant",
            origin="pit",
            cause_code="provider_timestamp_future",
        )
    archived = archived_observations or {}
    if any(value > received_at for value in archived.values()):
        raise ResearchCapabilityError("research_pit_violation", "archived observation is future")
    candidates = [
        candidate.model_copy(
            update={
                "observed_at": archived.get(candidate.evidence_id, received_at),
                "received_at": received_at,
            }
        )
        for candidate in result.evidence_candidates
    ]
    normalized_facts = [
        fact.model_copy(
            update={
                "observed_at": archived.get(fact.fact_id, received_at),
                "received_at": received_at,
            }
        )
        for fact in facts
    ]
    return result.model_copy(
        update={
            "evidence_candidates": candidates,
            "facts": normalized_facts,
            "completed_at": received_at,
        }
    )


def _evidence_model(row: ResearchEvidenceRecord) -> EvidenceCandidate:
    return EvidenceCandidate.model_validate(
        {
            "evidence_id": row.evidence_id,
            "requirement_id": row.requirement_id,
            "kind": row.kind,
            "authority": row.authority,
            "source_id": row.source_id,
            "source_url": row.source_url,
            "published_at": as_utc(row.published_at),
            "observed_at": as_utc(row.observed_at),
            "received_at": as_utc(row.received_at),
            "content_hash": row.content_hash,
            "excerpt": row.excerpt,
            "content_ref": row.content_ref,
            "acquisition_receipt_id": row.acquisition_receipt_id,
            "source_content_hash": row.source_content_hash,
            "parser_ref": row.parser_ref,
            "structured_payload_ref": row.structured_payload_ref,
            "tool_call_id": row.tool_call_id,
            "research_session_id": row.research_session_id,
            "round": row.round,
            "quality": row.quality,
            "freshness_status": row.freshness_status,
            "conflict_group": row.conflict_group,
        }
    )


def _snapshot_manifest(snapshot: SnapshotRecord, run_id: str) -> ResearchSnapshotManifest:
    evidence = json.loads(snapshot.evidence_json)
    if not isinstance(evidence, list):
        raise ValueError("snapshot_evidence_invalid")
    evidence_refs = [
        str(item["evidence_id"])
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    ]
    if not evidence_refs:
        raise ValueError("snapshot_evidence_invalid")
    return ResearchSnapshotManifest.model_validate(
        {
            "schema_version": "research-snapshot-manifest.v1",
            "snapshot_id": snapshot.snapshot_id,
            "run_id": run_id,
            "event_id": snapshot.event_id,
            "snapshot_type": snapshot.snapshot_type,
            "generation": snapshot.generation,
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "cutoff_at": as_utc(snapshot.cutoff_at),
            "snapshot_hash": snapshot.snapshot_hash,
            "evidence_refs": evidence_refs,
            "pack_version": snapshot.pack_version,
            "created_at": as_utc(snapshot.created_at),
        }
    )
