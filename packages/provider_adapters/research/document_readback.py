from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchEvidenceService,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow

from .document_store import ContentAddressedDocumentStore
from .documents import relevant_excerpt


class DocumentReadbackResearchAdapter:
    """Select a new verbatim passage from a persisted original document."""

    capability_id = "document.readback"
    supported_modes = frozenset({"live", "replay"})
    evidence_ref_required = True
    usage_notes = (
        "Use an existing evidence_ref from this session with the same requirement_id "
        "and retained content_ref. Never invent ids. If the original is missing, fetch "
        "an approved source first; market snapshots may have no readable document."
    )

    def __init__(
        self,
        evidence: ResearchEvidenceService,
        document_store: ContentAddressedDocumentStore,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.evidence = evidence
        self.document_store = document_store
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not query.evidence_ref:
            raise ResearchCapabilityError(
                "research_evidence_ref_required",
                "document readback requires an evidence_ref",
                retryable=False,
                origin="gateway",
            )
        try:
            original = self.evidence.get_session_evidence(
                evidence_id=query.evidence_ref,
                research_session_id=query.research_session_id,
            )
        except ValueError as exc:
            raise ResearchCapabilityError(
                str(exc),
                "document evidence is unavailable in this research session",
                retryable=False,
                origin="gateway",
            ) from exc
        if original.requirement_id != query.requirement_id:
            raise ResearchCapabilityError(
                "research_evidence_requirement_mismatch",
                "document evidence belongs to another requirement",
                retryable=False,
                origin="gateway",
            )
        if not original.content_ref or not original.source_content_hash or not original.parser_ref:
            raise ResearchCapabilityError(
                "research_document_content_unavailable",
                "source retention policy did not preserve an original document",
                retryable=False,
                origin="source_registry",
            )
        if query.content_ref is not None and query.content_ref != original.content_ref:
            raise ResearchCapabilityError(
                "research_document_content_ref_mismatch",
                "content_ref does not match the selected evidence",
                retryable=False,
                origin="gateway",
            )
        try:
            document = self.document_store.load(
                original.content_ref,
                expected_source_content_hash=original.source_content_hash,
            )
        except ValueError as exc:
            raise ResearchCapabilityError(
                str(exc),
                "persisted original document failed integrity verification",
                retryable=False,
                origin="storage",
            ) from exc
        if (
            document.source_url != str(original.source_url)
            or document.parser_ref != original.parser_ref
        ):
            raise ResearchCapabilityError(
                "research_document_lineage_mismatch",
                "persisted document metadata does not match its evidence",
                retryable=False,
                origin="storage",
            )
        excerpt = relevant_excerpt(document.text, query.query)
        if not excerpt:
            raise ResearchCapabilityError(
                "research_document_empty",
                "persisted original document has no readable text",
                retryable=False,
                origin="storage",
            )
        completed_at = self.clock()
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind=original.kind,
            authority=original.authority,
            source_id=original.source_id,
            source_url=str(original.source_url) if original.source_url else None,
            published_at=original.published_at,
            excerpt=excerpt,
            structured_payload_ref=original.structured_payload_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
                content_ref=original.content_ref,
                acquisition_receipt_id=original.acquisition_receipt_id,
            ),
            requirement_id=query.requirement_id,
            kind=original.kind,
            authority=original.authority,
            source_id=original.source_id,
            source_url=original.source_url,
            published_at=original.published_at,
            observed_at=completed_at,
            received_at=completed_at,
            content_hash=content_hash,
            excerpt=excerpt,
            content_ref=original.content_ref,
            acquisition_receipt_id=original.acquisition_receipt_id,
            source_content_hash=original.source_content_hash,
            parser_ref=original.parser_ref,
            structured_payload_ref=original.structured_payload_ref,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=original.conflict_group,
        )
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="local-document-archive",
            evidence_candidates=[candidate],
            facts=[],
            cost_usd=0.0,
            completed_at=completed_at,
        )
