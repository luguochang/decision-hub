from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.provider_adapters.research.documents import (
    DocumentFetcher,
    FetchedDocument,
    HttpDocumentResearchAdapter,
)
from packages.provider_adapters.research.source_registry import (
    ResearchSourceRegistry,
    ResearchSourceRegistryError,
)


class OfficialDocumentResearchAdapter(HttpDocumentResearchAdapter):
    """Fetch an approved official document and emit deterministic event facts."""

    def __init__(
        self,
        *,
        fetcher: DocumentFetcher | None = None,
        source_registry: ResearchSourceRegistry | None = None,
    ) -> None:
        super().__init__(
            capability_id="official.macro",
            kind="official",
            authority="official",
            source_id="official-macro-document",
            fetcher=fetcher,
            source_registry=source_registry,
        )

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        canonical_requirement_id = (
            self.source_registry.canonical_requirement_id(query.requirement_id)
            if self.source_registry is not None
            else query.requirement_id
        )
        if canonical_requirement_id not in {"event_identity", "event.identity"}:
            raise ResearchCapabilityError(
                "research_capability_requirement_unsupported",
                "official-event.v1 only produces typed event identity facts",
                retryable=False,
                origin="gateway",
                cause_code="official_event_v1",
            )
        if self.source_registry is None:
            return await super().execute(query)
        if query.target_url is None:
            raise ResearchCapabilityError(
                "research_target_required", "official capability requires a target URL"
            )
        target_url = str(query.target_url)
        try:
            self.source_registry.require(
                target_url, usage="fetch", requirement_id=query.requirement_id
            )
        except ResearchSourceRegistryError as exc:
            raise ResearchCapabilityError(
                exc.error_code, str(exc), retryable=False, origin="gateway"
            ) from exc
        try:
            document = await self.fetcher(target_url)
        except ResearchCapabilityError:
            raise
        except Exception as exc:
            raise ResearchCapabilityError(
                "search_provider_failed",
                "official document request failed",
                retryable=True,
                origin="transport",
                cause_code=type(exc).__name__.lower(),
            ) from exc
        try:
            policy = self.source_registry.require(
                document.source_url, usage="fetch", requirement_id=query.requirement_id
            )
        except ResearchSourceRegistryError as exc:
            raise ResearchCapabilityError(
                exc.error_code, str(exc), retryable=False, origin="gateway"
            ) from exc
        parsed = _parse_official_event(document)
        if parsed is None:
            raise ResearchCapabilityError(
                "official_event_identity_incomplete",
                "official document has no deterministic actor and publication time",
                retryable=False,
                origin="provider",
            )
        source_url, excerpt, actor, event_time, revision_status = parsed
        try:
            final_policy = self.source_registry.require(
                source_url, usage="evidence", requirement_id=query.requirement_id
            )
        except ResearchSourceRegistryError as exc:
            raise ResearchCapabilityError(
                exc.error_code, str(exc), retryable=False, origin="gateway"
            ) from exc
        if final_policy.source_ref != policy.source_ref:
            raise ResearchCapabilityError(
                "research_source_not_approved",
                "official document resolved to a different registered source",
                retryable=False,
                origin="gateway",
            )
        source_id = final_policy.source_ref
        structured_ref = f"source-registry://{source_id}"
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="official",
            authority="official",
            source_id=source_id,
            source_url=source_url,
            published_at=event_time,
            excerpt=excerpt,
            structured_payload_ref=structured_ref,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind="official",
            authority="official",
            source_id=source_id,
            source_url=AnyUrl(source_url),
            published_at=event_time,
            observed_at=document.observed_at,
            received_at=document.received_at,
            content_hash=content_hash,
            excerpt=excerpt[:4000],
            structured_payload_ref=structured_ref,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        facts = [
            _event_fact(query, candidate, "event_actor", actor, "text"),
            _event_fact(query, candidate, "event_time", event_time.isoformat(), "datetime"),
            _event_fact(query, candidate, "revision_status", revision_status, "text"),
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=source_id,
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=document.received_at,
        )


def _parse_official_event(
    document: FetchedDocument,
) -> tuple[str, str, str, datetime, str] | None:
    """Parse RSS/Atom metadata or a direct official page metadata pair."""

    # Federal Reserve RSS currently includes a UTF-8 BOM before the XML
    # declaration. Normalize only this transport marker; retain all body text
    # for hashing and evidence excerpts.
    text = document.text.lstrip("\ufeff").strip()
    if text.startswith("<"):
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            root = None
        if root is not None:
            item = next(
                (node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}),
                None,
            )
            if item is not None:
                values = {_local_name(child.tag): (child.text or "").strip() for child in item}
                title = values.get("title", "")
                link_node = next(
                    (child for child in item if _local_name(child.tag) == "link"), None
                )
                source_url = values.get("link") or (
                    link_node.attrib.get("href") if link_node is not None else ""
                )
                published = _parse_date(
                    values.get("pubDate") or values.get("published") or values.get("updated")
                )
                if title and source_url and published is not None:
                    excerpt = "\n".join(
                        value for value in (title, values.get("description", "")) if value
                    )
                    actor = _actor_from_title(title)
                    if actor:
                        return source_url, excerpt, actor, published, _revision_status(excerpt)
    published = document.published_at
    if published is None:
        return None
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    actor = _actor_from_title(first_line)
    if not actor:
        return None
    return document.source_url, text[:4000], actor, published, _revision_status(text)


def _event_fact(
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    field: str,
    value: str,
    unit: str,
) -> FactEnvelope:
    event_id = query.event_id or f"event:{candidate.evidence_id}"
    attributes: dict[str, float | str | bool | None] = {
        "event_id": event_id,
        "parser_ref": "official-event.v1",
    }
    payload_schema_ref = "official.event-identity.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family="event.identity",
        field=field,
        instrument=None,
        venue=None,
        value=value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset="t0",
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family="event.identity",
        field=field,
        instrument=None,
        venue=None,
        value=value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset="t0",
        observed_at=candidate.observed_at,
        received_at=candidate.received_at,
        published_at=candidate.published_at,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )


def _actor_from_title(title: str) -> str | None:
    candidate = title.split(",", 1)[0].strip()
    if not candidate or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,80}", candidate):
        return None
    return candidate


def _revision_status(text: str) -> str:
    lowered = text.casefold()
    return (
        "updated"
        if any(token in lowered for token in ("revised", "updated", "correction"))
        else "original"
    )


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
