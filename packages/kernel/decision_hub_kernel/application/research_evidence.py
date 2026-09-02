from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

from packages.contracts_py.decision_hub_contracts import (
    ErrorProvenance,
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchSnapshotManifest,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ResearchEvidenceRecord,
    RunRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter


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
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.origin = origin
        self.cause_code = cause_code
        self.capability_id = capability_id
        self.tool_call_id = tool_call_id
        self.deadline_ms = deadline_ms

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


def research_evidence_instance_id(*, content_hash: str, research_session_id: str) -> str:
    """Return an idempotent evidence identity scoped to one research session."""

    encoded = f"{research_session_id}:{content_hash}".encode()
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
    ) -> None:
        self._manifests = _unique_by_id(manifests, "capability_id")
        self._adapters = _unique_by_id(adapters, "capability_id")
        self._enabled = frozenset(enabled_capabilities)
        self._clock = clock

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
        try:
            effective_query = query.model_copy(
                update={
                    "requested_observed_at": query.observed_at,
                    "observed_at": server_received_at,
                }
            )
            async with asyncio.timeout(manifest.timeout_seconds):
                result = await adapter.execute(effective_query)
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
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(manifest.timeout_seconds * 1000),
            ) from exc
        except ResearchCapabilityError as exc:
            raise exc.with_context(
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(manifest.timeout_seconds * 1000),
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
                deadline_ms=round(manifest.timeout_seconds * 1000),
            ) from exc
        if query.mode == "live":
            if server_completed_at > query.cutoff_at:
                raise ResearchCapabilityError(
                    "research_pit_violation",
                    "server received the live capability result after its PIT cutoff",
                    origin="pit",
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                    deadline_ms=round(manifest.timeout_seconds * 1000),
                )
            try:
                result = _normalize_live_result(result, server_completed_at)
            except ResearchCapabilityError as exc:
                raise exc.with_context(
                    capability_id=query.capability_id,
                    tool_call_id=query.request_id,
                    deadline_ms=round(manifest.timeout_seconds * 1000),
                ) from exc.__cause__
        try:
            self._validate_result(query, result, effective_domains)
        except ResearchCapabilityError as exc:
            raise exc.with_context(
                capability_id=query.capability_id,
                tool_call_id=query.request_id,
                deadline_ms=round(manifest.timeout_seconds * 1000),
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
    ) -> list[EvidenceCandidate]:
        accepted: list[EvidenceCandidate] = []
        with self.database.session() as session:
            if session.get(RunRecord, run_id) is None:
                raise KeyError(run_id)
            for candidate in candidates:
                requirement = requirements.get(candidate.requirement_id)
                if requirement is None:
                    raise ValueError("research_requirement_not_found")
                _validate_candidate_pit(candidate, cutoff_at)
                freshness_anchor = candidate.published_at or candidate.observed_at
                age_seconds = (cutoff_at - freshness_anchor).total_seconds()
                freshness = (
                    "fresh" if 0 <= age_seconds <= requirement.freshness_seconds else "stale"
                )
                quality = "accepted" if freshness == "fresh" else "stale"
                normalized = candidate.model_copy(
                    update={"freshness_status": freshness, "quality": quality}
                )
                existing = session.get(ResearchEvidenceRecord, normalized.evidence_id)
                if existing is not None:
                    if (
                        existing.content_hash != normalized.content_hash
                        or existing.run_id != run_id
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
            rows = (
                session.query(ResearchEvidenceRecord)
                .filter_by(run_id=run_id)
                .order_by(ResearchEvidenceRecord.accepted_at, ResearchEvidenceRecord.evidence_id)
                .all()
            )
            return [_evidence_model(row) for row in rows]

    def freeze_decision_snapshot(
        self,
        *,
        run_id: str,
        evidence_refs: Iterable[str],
        cutoff_at: datetime,
    ) -> ResearchSnapshotManifest:
        selected_refs = tuple(dict.fromkeys(evidence_refs))
        with self.database.session() as session:
            run = session.get(RunRecord, run_id)
            if run is None:
                raise KeyError(run_id)
            if run.snapshot_id is None:
                raise ValueError("trigger_snapshot_required")
            trigger = session.get(SnapshotRecord, run.snapshot_id)
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
                frozen = session.get(SnapshotRecord, run.decision_snapshot_id)
                if frozen is None:
                    raise KeyError(run.decision_snapshot_id)
                manifest = _snapshot_manifest(frozen, run_id)
                if set(manifest.evidence_refs) != set(combined_refs):
                    raise ValueError("decision_snapshot_already_frozen")
                return manifest

            rows = [session.get(ResearchEvidenceRecord, item) for item in selected_refs]
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
                json.dumps(
                    semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
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
            "research_domain_denied", "requested domains exceed the capability allowlist"
        )
    if not requested and not allowed and not broad and manifest.kind != "replay":
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
            "research_target_domain_denied", "target URL is outside the effective allowlist"
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


def _server_time(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise ValueError("server clock must return an aware datetime")
    return value.astimezone(UTC)


def _normalize_live_result(
    result: ResearchCapabilityResult,
    received_at: datetime,
) -> ResearchCapabilityResult:
    """Replace provider/model timestamps with the gateway receive instant.

    Provider publication time remains useful as ``published_at``; only the
    product-owned observation and receipt timestamps are authoritative for PIT.
    """

    if result.completed_at > received_at or any(
        candidate.observed_at > received_at or candidate.received_at > received_at
        for candidate in result.evidence_candidates
    ):
        raise ResearchCapabilityError(
            "research_pit_violation",
            "provider supplied a timestamp later than the server receive instant",
            origin="pit",
            cause_code="provider_timestamp_future",
        )
    candidates = [
        candidate.model_copy(
            update={"observed_at": received_at, "received_at": received_at}
        )
        for candidate in result.evidence_candidates
    ]
    return result.model_copy(
        update={"evidence_candidates": candidates, "completed_at": received_at}
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
