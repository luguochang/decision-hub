"""Freeze attributed opinions using the existing library and immutable Run roles.

No second article store, model loop, Fact admission or financial qualification.
Acquisition verification authenticates captured URL/bytes/time only; author,
publication precision, rights and interpretation remain attributed metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

from ..persistence.db import (
    AnalystClaimRecord,
    ResearchDocumentBlockRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    ResearchEvidenceRecord,
    ResearchRunScopeRecord,
    RunRecord,
    RunRoleOutputRecord,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from ..ports.market_path import aware_utc
from ..ports.research_signal import ClaimSignal, ClaimSourceContext, claim_signal_from_library
from .research_evidence import research_evidence_content_hash
from .research_library import (
    _claim_view,  # pyright: ignore[reportPrivateUsage]
    _document_view,  # pyright: ignore[reportPrivateUsage]
    _version_view,  # pyright: ignore[reportPrivateUsage]
)
from .research_library_provenance import require_market_document
from .opinion_capture_provenance import read_capture_provenance
from .source_acquisition import (
    _canonical_json,  # pyright: ignore[reportPrivateUsage]
    _record_to_receipt,  # pyright: ignore[reportPrivateUsage]
)

SCHEMA = "claim-signal-freeze.v1"
SCHEMA_V2 = "claim-signal-freeze.v2"
ROLE_PREFIX = "claim_signal.v1:"


class _AcquisitionProof(TypedDict):
    kind: Literal["declared", "acquisition_verified"]
    receipt_id: str | None
    receipt_hash: str | None
    received_at: str | None
    recorded_at: str | None


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _role(claim_id: str) -> str:
    return ROLE_PREFIX + _hash({"claim_id": claim_id})[:40]


def _known(value: datetime | None, cutoff: datetime) -> None:
    if value is None or as_utc(value) > cutoff:
        raise ValueError("research_claim_after_cutoff")


@dataclass(frozen=True)
class FrozenClaimSignal:
    output_id: str
    payload_hash: str
    run_id: str
    cutoff: datetime
    signal: ClaimSignal
    source_attestation: Literal["declared", "acquisition_verified"]
    capture_provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClaimEvidenceLinks:
    claim_id: str
    snapshot_ref: str
    snapshot_hash: str
    support_refs: tuple[str, ...]
    counter_refs: tuple[str, ...]
    evidence_hashes: tuple[tuple[str, str], ...]
    independent_source_count: int
    gaps: tuple[str, ...]
    claim_truth: Literal["not_assessed"] = "not_assessed"


def _library_binding(
    session: Session,
    *,
    run_id: str,
    document_id: str,
    version_id: str,
    claim_id: str,
    source: ClaimSourceContext,
    cutoff: datetime,
    store: DocumentBundleStore,
    for_freeze: bool,
) -> tuple[ClaimSignal, dict[str, Any], ResearchDocumentRecord]:
    run = session.get(RunRecord, run_id, populate_existing=True)
    scope = session.get(ResearchRunScopeRecord, run_id, populate_existing=True)
    document = session.get(ResearchDocumentRecord, document_id, populate_existing=True)
    version = session.get(ResearchDocumentVersionRecord, version_id, populate_existing=True)
    claim = session.get(AnalystClaimRecord, claim_id, populate_existing=True)
    if (
        document is None
        or version is None
        or claim is None
        or claim.version_id != version_id
        or version.document_id != document_id
    ):
        raise ValueError("research_claim_document_version_mismatch")
    if (
        run is None
        or scope is None
        or scope.library_context_mode == "none"
        or version_id not in json.loads(scope.selected_version_ids_json)
    ):
        raise ValueError("research_claim_version_not_selected")
    if document.privacy_mode != "remote_allowed" and not (
        scope.library_context_mode == "selected" and scope.allow_remote_library_content
    ):
        raise ValueError("research_claim_remote_consent_required")
    if document.source_profile_id is not None and document.source_profile_id != source.source_id:
        raise ValueError("research_claim_source_mismatch")
    if claim.market_scope != scope.research_family or scope.research_family not in json.loads(
        document.market_scopes_json
    ):
        raise ValueError("research_claim_market_scope_mismatch")
    run_instruments = set(json.loads(scope.instrument_scope_json))
    for declared in (
        json.loads(document.instrument_scopes_json),
        json.loads(claim.instrument_scope_json),
    ):
        if declared and not run_instruments.issubset(declared):
            raise ValueError("research_claim_instrument_scope_mismatch")
    for value in (
        document.created_at,
        version.created_at,
        version.observed_at,
        version.received_at,
        claim.created_at,
        source.first_seen_at,
    ):
        _known(value, cutoff)
    if as_utc(version.observed_at) > as_utc(version.received_at):
        raise ValueError("research_claim_time_order_invalid")
    if version.supersedes_version_id is not None:
        prior = session.get(
            ResearchDocumentVersionRecord, version.supersedes_version_id, populate_existing=True
        )
        if (
            prior is None
            or prior.document_id != document_id
            or prior.version_id == version_id
            or as_utc(prior.created_at) > as_utc(version.created_at)
        ):
            raise ValueError("research_claim_supersession_invalid")
    published = source.publication
    if (published.published_at is not None and published.published_at > source.first_seen_at) or (
        published.window_start is not None and published.window_start > source.first_seen_at
    ):
        raise ValueError("research_claim_publication_after_first_seen")
    if for_freeze:
        if version.status != "ready" or claim.status not in {"candidate", "accepted"}:
            raise ValueError("research_claim_not_eligible")
        if claim.reviewed_at is not None:
            _known(claim.reviewed_at, cutoff)
    version_view = _version_view(version)
    if version_view.quality.quality_status in {"blocked_low_quality", "failed"}:
        raise ValueError("research_claim_quality_blocked")
    require_market_document(version, store)
    original = store.read(version.original_file_ref)
    if hashlib.sha256(original).hexdigest() != version.original_file_hash:
        raise ValueError("research_claim_original_hash_mismatch")
    claim_view = _claim_view(claim)
    signal = claim_signal_from_library(
        claim_view, version_view, _document_view(document, 0), source
    )
    blocks = session.scalars(
        select(ResearchDocumentBlockRecord)
        .filter_by(version_id=version_id)
        .execution_options(populate_existing=True)
    ).all()
    locators = {}
    for block in blocks:
        for key in (block.block_id, block.locator):
            if key in locators and locators[key].block_id != block.block_id:
                raise ValueError("research_claim_locator_ambiguous")
            locators[key] = block
    selected = []
    for locator in claim_view.locator_refs:
        block = locators.get(locator)
        if block is None:
            raise ValueError("research_claim_locator_invalid")
        if hashlib.sha256(block.content.encode()).hexdigest() != block.content_hash:
            raise ValueError("research_claim_block_hash_mismatch")
        selected.append(block)
    quote = " ".join(claim_view.supporting_quote.split())
    if not quote or not any(quote in " ".join(block.content.split()) for block in selected):
        raise ValueError("research_claim_quote_not_in_original_block")
    claim_core = claim_view.model_dump(mode="json", exclude={"status", "reviewed_at"})
    binding = {
        "document": _hash(
            _document_view(document, 0).model_dump(
                mode="json", exclude={"latest_version_id", "version_count"}
            )
        ),
        "version": _hash(
            version_view.model_dump(
                mode="json", exclude={"status", "correction_status", "block_count", "claim_count"}
            )
        ),
        "claim": _hash(claim_core),
        "locators": [
            {"id": row.block_id, "locator": row.locator, "content_hash": row.content_hash}
            for row in selected
        ],
    }
    return signal, binding, document


def _acquisition(
    session: Session,
    source: ClaimSourceContext,
    signal: ClaimSignal,
    receipt_id: str | None,
    cutoff: datetime,
    document: ResearchDocumentRecord,
) -> _AcquisitionProof:
    if receipt_id is None:
        return {
            "kind": "declared",
            "receipt_id": None,
            "receipt_hash": None,
            "received_at": None,
            "recorded_at": None,
        }
    row = session.get(SourceAcquisitionRecord, receipt_id, populate_existing=True)
    if row is None:
        raise ValueError("research_claim_acquisition_missing")
    receipt = _record_to_receipt(row)
    canonical = receipt.model_dump(mode="json")
    for field in ("request_started_at", "request_completed_at", "received_at"):
        if canonical.get(field) is None:
            canonical.pop(field, None)
    canonical.pop("received_at", None)
    if hashlib.sha256(_canonical_json(canonical).encode()).hexdigest() != row.receipt_hash:
        raise ValueError("research_claim_acquisition_hash_mismatch")
    if (
        receipt.status != "succeeded"
        or receipt.source_id != source.source_id
        or document.source_profile_id != source.source_id
        or str(receipt.url) != str(source.canonical_url)
        or receipt.observed_at != source.first_seen_at
        or not any(item.content_hash == signal.content_hash for item in receipt.artifacts)
    ):
        raise ValueError("research_claim_acquisition_binding_mismatch")
    for value in (receipt.observed_at, receipt.received_at, row.created_at):
        _known(value, cutoff)
    if (
        receipt.observed_at is None
        or receipt.received_at is None
        or receipt.observed_at > receipt.received_at
    ):
        raise ValueError("research_claim_acquisition_time_invalid")
    return {
        "kind": "acquisition_verified",
        "receipt_id": row.receipt_id,
        "receipt_hash": row.receipt_hash,
        "received_at": receipt.received_at.isoformat(),
        "recorded_at": as_utc(row.created_at).isoformat(),
    }


def freeze_claim_signal_in_session(
    session: Session,
    *,
    run_id: str,
    document_id: str,
    version_id: str,
    claim_id: str,
    source: ClaimSourceContext,
    cutoff: datetime,
    store: DocumentBundleStore,
    acquisition_receipt_id: str | None = None,
) -> FrozenClaimSignal:
    """Caller owns the write transaction; no hidden commit or model/network work."""
    cutoff = aware_utc(cutoff)
    connection = session.connection()
    native = connection.connection.driver_connection
    if connection.dialect.name == "sqlite" and (native is None or not native.in_transaction):
        raise ValueError("research_claim_writer_transaction_required")
    source = ClaimSourceContext.model_validate(source.model_dump(mode="json"))
    signal, binding, document = _library_binding(
        session,
        run_id=run_id,
        document_id=document_id,
        version_id=version_id,
        claim_id=claim_id,
        source=source,
        cutoff=cutoff,
        store=store,
        for_freeze=True,
    )
    attestation = _acquisition(session, source, signal, acquisition_receipt_id, cutoff, document)
    capture = read_capture_provenance(session, signal=signal, store=store,
        receipt_id=acquisition_receipt_id, cutoff=cutoff)
    schema = SCHEMA_V2 if capture is not None else SCHEMA
    body = {
        "schema_version": schema,
        "run_id": run_id,
        "cutoff": cutoff.isoformat(),
        "signal": signal.model_dump(mode="json"),
        "library_binding": binding,
        "source_attestation": attestation,
    }
    if capture is not None:
        body["capture_provenance"] = capture
    payload, digest, role = _json(body), _hash(body), _role(claim_id)
    old = session.scalar(
        select(RunRoleOutputRecord)
        .filter_by(run_id=run_id, role=role)
        .execution_options(populate_existing=True)
    )
    if old is not None:
        if (
            old.payload_hash != digest
            or old.payload_json != payload
            or old.schema_version != schema
        ):
            raise ValueError("research_claim_snapshot_immutable")
        return read_claim_signal_in_session(
            session,
            run_id=run_id,
            output_id=old.output_id,
            payload_hash=digest,
            cutoff=cutoff,
            store=store,
        )
    row = RunRoleOutputRecord(
        output_id="claim_snapshot_" + _hash({"run_id": run_id, "role": role}),
        run_id=run_id,
        role=role,
        payload_json=payload,
        payload_hash=digest,
        schema_version=schema,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return FrozenClaimSignal(row.output_id, digest, run_id, cutoff, signal, attestation["kind"], capture)


def read_claim_signal_in_session(
    session: Session,
    *,
    run_id: str,
    output_id: str,
    payload_hash: str,
    cutoff: datetime,
    store: DocumentBundleStore,
) -> FrozenClaimSignal:
    """Recheck original bytes and immutable row fields, never the latest pointer.

    A later claim review state is not substituted into a historical snapshot.
    Callers still apply current withdrawal/publication policy independently.
    """
    cutoff = aware_utc(cutoff)
    row = session.get(RunRoleOutputRecord, output_id, populate_existing=True)
    if (
        row is None
        or row.run_id != run_id
        or row.schema_version not in {SCHEMA, SCHEMA_V2}
        or row.payload_hash != payload_hash
        or hashlib.sha256(row.payload_json.encode()).hexdigest() != payload_hash
    ):
        raise ValueError("research_claim_snapshot_invalid")
    body = json.loads(row.payload_json)
    if (
        set(body)
        != ({"schema_version", "run_id", "cutoff", "signal", "library_binding", "source_attestation"}
            | ({"capture_provenance"} if row.schema_version == SCHEMA_V2 else set()))
        or body["schema_version"] != row.schema_version
        or body["run_id"] != run_id
        or _json(body) != row.payload_json
    ):
        raise ValueError("research_claim_snapshot_payload_invalid")
    frozen_cutoff = aware_utc(datetime.fromisoformat(body["cutoff"]))
    if frozen_cutoff > cutoff:
        raise ValueError("research_claim_snapshot_after_cutoff")
    signal = ClaimSignal.model_validate(body["signal"])
    if row.role != _role(signal.claim_id):
        raise ValueError("research_claim_snapshot_role_invalid")
    current, binding, document = _library_binding(
        session,
        run_id=run_id,
        document_id=signal.document_id,
        version_id=signal.version_id,
        claim_id=signal.claim_id,
        source=signal.source,
        cutoff=frozen_cutoff,
        store=store,
        for_freeze=False,
    )
    if binding != body["library_binding"] or current.model_dump(
        mode="json", exclude={"review_status"}
    ) != signal.model_dump(mode="json", exclude={"review_status"}):
        raise ValueError("research_claim_library_original_changed")
    proof = _acquisition(
        session,
        signal.source,
        signal,
        body["source_attestation"].get("receipt_id"),
        frozen_cutoff,
        document,
    )
    if proof != body["source_attestation"]:
        raise ValueError("research_claim_source_attestation_changed")
    capture = read_capture_provenance(session, signal=signal, store=store,
        receipt_id=proof["receipt_id"], cutoff=frozen_cutoff)
    if capture != body.get("capture_provenance") or (capture is not None) != (row.schema_version == SCHEMA_V2):
        raise ValueError("research_claim_capture_provenance_changed")
    return FrozenClaimSignal(output_id, payload_hash, run_id, frozen_cutoff, signal, proof["kind"], capture)


def resolve_claim_signal_in_session(
    session: Session,
    *,
    run_id: str,
    claim_id: str,
    cutoff: datetime,
    store: DocumentBundleStore,
) -> FrozenClaimSignal | None:
    """Resolve the existing AnalystClaim ID used by synthesis claim_refs."""
    row = session.scalar(
        select(RunRoleOutputRecord)
        .filter_by(run_id=run_id, role=_role(claim_id))
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    result = read_claim_signal_in_session(
        session,
        run_id=run_id,
        output_id=row.output_id,
        payload_hash=row.payload_hash,
        cutoff=cutoff,
        store=store,
    )
    if result.signal.claim_id != claim_id:
        raise ValueError("research_claim_snapshot_identity_mismatch")
    return result


def _host(url: str | None) -> str | None:
    hostname = urlsplit(url).hostname if url else None
    return hostname.lower().removeprefix("www.") if hostname else None


def claim_use_evidence_in_session(
    session: Session,
    *,
    frozen: FrozenClaimSignal,
    support_refs: tuple[str, ...],
    counter_refs: tuple[str, ...],
    cutoff: datetime,
    store: DocumentBundleStore,
) -> ClaimEvidenceLinks:
    """Return independently sourced admitted associations, never a truth verdict.

    Absence remains a gap for this claim; it cannot veto unrelated protective
    actions. Known syndication clusters and byte duplicates count as one source.
    """
    cutoff = aware_utc(cutoff)
    frozen = read_claim_signal_in_session(
        session,
        run_id=frozen.run_id,
        output_id=frozen.output_id,
        payload_hash=frozen.payload_hash,
        cutoff=cutoff,
        store=store,
    )
    if (
        len(set(support_refs)) != len(support_refs)
        or len(set(counter_refs)) != len(counter_refs)
        or set(support_refs) & set(counter_refs)
    ):
        raise ValueError("research_claim_evidence_links_ambiguous")
    signal = frozen.signal
    sources = set()
    hashes = []
    clusters = {}
    evidence_rows = []
    for ref in (*support_refs, *counter_refs):
        row = session.get(ResearchEvidenceRecord, ref, populate_existing=True)
        if (
            row is None
            or row.run_id != frozen.run_id
            or row.quality != "accepted"
            or row.freshness_status == "stale"
        ):
            raise ValueError("research_claim_evidence_not_admitted")
        evidence_rows.append(row)
    relevant_sources = {row.source_id for row in evidence_rows}
    rows = session.scalars(
        select(RunRoleOutputRecord)
        .where(
            RunRoleOutputRecord.run_id == frozen.run_id,
            RunRoleOutputRecord.role.startswith(ROLE_PREFIX),
        )
        .execution_options(populate_existing=True)
    ).all()
    for row in rows:
        # Only a routing hint: any relevant snapshot is then verified in full.
        # An unrelated missing opinion cannot veto this claim's evidence links.
        hint = json.loads(row.payload_json)
        if hint.get("signal", {}).get("source", {}).get("source_id") not in relevant_sources:
            continue
        other = read_claim_signal_in_session(
            session,
            run_id=frozen.run_id,
            output_id=row.output_id,
            payload_hash=row.payload_hash,
            cutoff=cutoff,
            store=store,
        )
        cluster = other.signal.source.syndication_cluster
        if cluster:
            clusters.setdefault(other.signal.source.source_id, set()).add(cluster)
    for row in evidence_rows:
        ref = row.evidence_id
        for value in (row.observed_at, row.received_at, row.accepted_at):
            _known(value, cutoff)
        if as_utc(row.observed_at) > as_utc(row.received_at):
            raise ValueError("research_claim_evidence_time_invalid")
        if row.published_at is not None and as_utc(row.published_at) > as_utc(row.observed_at):
            raise ValueError("research_claim_evidence_time_invalid")
        digest = research_evidence_content_hash(
            requirement_id=row.requirement_id,
            kind=row.kind,
            authority=row.authority,
            source_id=row.source_id,
            source_url=row.source_url,
            published_at=as_utc(row.published_at),
            excerpt=row.excerpt,
            structured_payload_ref=row.structured_payload_ref,
        )
        if row.content_hash != digest:
            raise ValueError("research_claim_evidence_hash_mismatch")
        if (
            row.source_id in {signal.source.source_id, "research-library:" + signal.version_id}
            or _host(row.source_url) == _host(str(signal.source.canonical_url))
            or signal.content_hash in {row.content_hash, row.source_content_hash}
            or row.content_ref == signal.content_ref
            or (
                signal.source.syndication_cluster is not None
                and signal.source.syndication_cluster in clusters.get(row.source_id, set())
            )
        ):
            raise ValueError("research_claim_evidence_not_independent")
        groups = clusters.get(row.source_id, set())
        key = tuple(sorted(groups)) if groups else (_host(row.source_url) or row.source_id,)
        sources.add(key)
        hashes.append((ref, row.content_hash))
    gaps = []
    if not support_refs:
        gaps.append("independent_support_missing")
    if not counter_refs:
        gaps.append("counterevidence_missing")
    if signal.source.content_kind == "promotion":
        gaps.append("promotion_not_fact")
    if frozen.source_attestation == "declared":
        gaps.append("source_acquisition_unverified")
    if frozen.capture_provenance is not None:
        gaps.append("partial_original_context")
    if signal.source.syndication_cluster is None:
        gaps.append("syndication_unknown")
    return ClaimEvidenceLinks(
        signal.claim_id,
        frozen.output_id,
        frozen.payload_hash,
        tuple(support_refs),
        tuple(counter_refs),
        tuple(hashes),
        len(sources),
        tuple(gaps),
    )


__all__ = [
    "FrozenClaimSignal",
    "ClaimEvidenceLinks",
    "freeze_claim_signal_in_session",
    "read_claim_signal_in_session",
    "resolve_claim_signal_in_session",
    "claim_use_evidence_in_session",
]
