"""Existing acquisition/library composition for attributed public opinions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    DocumentCorrection,
    DocumentUpload,
    EvidenceCandidate,
    ResearchInputEvidence,
)
from packages.provider_adapters.documents.parser import (
    _build_parsed_document,  # pyright: ignore[reportPrivateUsage]
)
from packages.provider_adapters.documents.public_opinion import (
    PARSER_REF,
    PublicOpinionText,
    exact_opinion_claims,
    parse_public_opinion,
)
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry
from packages.provider_adapters.research.opinion_capture import OpinionCaptureRegistry
from ..ports.opinion_capture import CAPTURE_PARSER, IMPORT_SCHEMA_V2, capture_summary

from ..persistence.db import (
    AnalystClaimRecord,
    ProductCommandRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from ..persistence.original_preparation import begin_immediate
from ..ports.documents import DocumentInput
from ..ports.market_path import aware_utc, canonical_hash
from ..ports.research_signal import ClaimSourceContext
from .research_library import (  # Existing library serializers keep one row/DTO mapping.
    _block_record,  # pyright: ignore[reportPrivateUsage]
    _claim_record,  # pyright: ignore[reportPrivateUsage]
    _correction_record,  # pyright: ignore[reportPrivateUsage]
    _version_view,  # pyright: ignore[reportPrivateUsage]
)
from .research_signal import FrozenClaimSignal, freeze_claim_signal_in_session
from .source_acquisition import (
    _canonical_json,  # pyright: ignore[reportPrivateUsage]
    _record_to_receipt,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from .research_library import ResearchLibraryService

IMPORT_KIND = "public_opinion_import"
IMPORT_SCHEMA = "public-opinion-import.v1"
IMPORT_OWNER = "system:public-opinion:v1"


def _body(row: ProductCommandRecord) -> dict[str, Any]:
    body = json.loads(row.result_json)
    if (
        row.kind != IMPORT_KIND
        or row.owner_id != IMPORT_OWNER
        or body.get("schema_version") not in {IMPORT_SCHEMA, IMPORT_SCHEMA_V2}
        or canonical_hash(body) != row.payload_hash
        or _canonical_json(body) != row.result_json
        or row.target_id != body.get("version_id")
    ):
        raise ValueError("public_opinion_import_receipt_invalid")
    return body


def _source_receipt(session: Session, receipt_id: str):
    row = session.get(SourceAcquisitionRecord, receipt_id, populate_existing=True)
    if row is None:
        raise ValueError("public_opinion_acquisition_missing")
    receipt = _record_to_receipt(row)
    value = receipt.model_dump(mode="json")
    for field in ("request_started_at", "request_completed_at", "received_at"):
        if value.get(field) is None:
            value.pop(field, None)
    value.pop("received_at", None)
    if canonical_hash(value) != row.receipt_hash:
        raise ValueError("public_opinion_acquisition_hash_invalid")
    if receipt.status != "succeeded" or receipt.status_code != 200 or receipt.observed_at is None:
        raise ValueError("public_opinion_acquisition_not_succeeded")
    return row, receipt


class PublicOpinionImportService:
    def __init__(self, library: ResearchLibraryService, source_registry: ResearchSourceRegistry,
                 *, capture_registry: OpinionCaptureRegistry | None = None):
        self.library, self.source_registry = library, source_registry
        self.capture_registry = capture_registry

    def import_acquisition(
        self, receipt_id: str, *, market_scopes: tuple[str, ...], instrument_scopes: tuple[str, ...]
    ):
        """No HTTP or model work: materialize one previously captured original."""
        if len(market_scopes) != 1:
            raise ValueError("public_opinion_single_research_family_required")
        root = self.library.acquisition_root
        if root is None:
            raise ValueError("acquisition_artifact_root_not_configured")
        with self.library.database.session() as session:
            row, receipt = _source_receipt(session, receipt_id)
            partial = len(receipt.artifacts) == 1 and receipt.artifacts[0].kind == "json"
            capture = None
            if partial:
                if self.capture_registry is None:
                    raise ValueError("public_opinion_capture_registry_required")
                _, policy = self.capture_registry.require(receipt.source_id, str(receipt.url))
                artifacts = list(receipt.artifacts)
            else:
                policy = self.source_registry.require(
                    str(receipt.url), usage="fetch", requirement_id="market.narrative"
                )
                if policy.retention_policy != "full_text":
                    raise ValueError("public_opinion_original_retention_not_approved")
                artifacts = [item for item in receipt.artifacts if item.kind in {"html", "dom"}]
            if len(artifacts) != 1:
                raise ValueError("public_opinion_original_ambiguous")
            artifact = artifacts[0]
            path = (root / artifact.path).resolve()
            if not path.is_relative_to(root):
                raise ValueError("acquisition_artifact_outside_root")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != artifact.content_hash:
                raise ValueError("public_opinion_original_hash_mismatch")
            if partial:
                assert self.capture_registry is not None
                capture = self.capture_registry.validate(raw, source_id=receipt.source_id, url=str(receipt.url))
                if capture.observed_at != receipt.observed_at:
                    raise ValueError("public_opinion_capture_time_mismatch")
                parsed = PublicOpinionText(capture.title, capture.author, capture.language,
                    capture.publication, "\n".join(item.text for item in capture.excerpts) or capture.title)
            else:
                parsed = parse_public_opinion(raw, str(receipt.url))
            parser_ref = CAPTURE_PARSER if capture is not None else PARSER_REF
            assert receipt.observed_at is not None  # Checked by _source_receipt.
            source = ClaimSourceContext(
                source_id=receipt.source_id,
                canonical_url=receipt.url,
                language=parsed.language,
                publication=parsed.publication,
                first_seen_at=receipt.observed_at,
                syndication_cluster=capture.syndication_cluster if capture else None,
                rights_ref=(f"opinion-capture-permission://{capture.permission_hash}" if capture else
                    f"source-registry://{policy.source_ref}/{canonical_hash(policy.model_dump(mode='json'))}"),
                extraction_version=parser_ref,
                content_kind="editorial",
            )
            binding = {
                "acquisition_receipt_id": receipt_id,
                "acquisition_receipt_hash": row.receipt_hash,
                "acquisition_received_at": as_utc(row.received_at or row.created_at).isoformat(),
                "acquisition_created_at": as_utc(row.created_at).isoformat(),
                "original_hash": artifact.content_hash,
                "source": source.model_dump(mode="json"),
                "author": parsed.author,
                "title": parsed.title,
                "market_scopes": list(market_scopes),
                "instrument_scopes": list(instrument_scopes),
            }
            if capture is not None:
                binding["capture"] = capture_summary(capture)
        request_id = "opimport_" + canonical_hash([receipt_id, market_scopes, instrument_scopes])
        with self.library.database.session() as session:
            begin_immediate(session)
            if capture is not None and hashlib.sha256(path.read_bytes()).hexdigest() != artifact.content_hash:
                raise ValueError("public_opinion_capture_changed_before_write")
            # Recheck the actual acquisition row inside the writer as well.
            current, _ = _source_receipt(session, receipt_id)
            if (
                current.receipt_hash != binding["acquisition_receipt_hash"]
                or as_utc(current.received_at or current.created_at).isoformat()
                != binding["acquisition_received_at"]
                or as_utc(current.created_at).isoformat() != binding["acquisition_created_at"]
            ):
                raise ValueError("public_opinion_acquisition_changed")
            previous = session.get(ProductCommandRecord, request_id, populate_existing=True)
            if previous is not None:
                body = _body(previous)
                if body["binding"] != binding:
                    raise ValueError("public_opinion_import_conflict")
                original = session.get(ResearchDocumentVersionRecord, body["version_id"])
                if original is None or original.original_file_hash != artifact.content_hash:
                    raise ValueError("public_opinion_version_changed")
                self.library.store.read(original.original_file_ref)
                return _version_view(original)
            upload = DocumentUpload(
                schema_version="document-upload.v1",
                title=parsed.title,
                source_type="public_web",
                source_profile_id=receipt.source_id,
                author=parsed.author,
                published_at=parsed.publication.published_at,
                market_scopes=cast(
                    list[Literal["crypto_macro", "cn_equity", "cross_asset"]], list(market_scopes)
                ),
                instrument_scopes=list(instrument_scopes),
                privacy_mode="remote_allowed",
                visibility="private",
            )
            version = self.library.record_upload_in_session(
                session, upload, filename="capture.json" if capture is not None else "article.html", data=raw
            )
            session.flush()
            document_input = DocumentInput(
                version.version_id,
                self.library.store.path_for(version.original_file_ref),
                version.original_file_ref,
                version.original_file_hash,
                parsed.title,
                "remote_allowed",
            )
            document = _build_parsed_document(
                document_input,
                text=parsed.text,
                parser_ref=parser_ref,
                parser_mode="pypdf_text",
                store=self.library.store,
                page_count=1,
            )
            if document.receipt.quality.quality_status != "ready_for_correction":
                raise ValueError("public_opinion_extraction_review_required")
            now = utcnow()
            claims = exact_opinion_claims(
                version_id=version.version_id,
                blocks=document.blocks,
                research_family=market_scopes[0],
                instrument_scopes=instrument_scopes,
                created_at=now,
            )
            if capture is not None:
                from .opinion_capture_provenance import claims_from_capture
                claims = claims_from_capture(capture, version_id=version.version_id,
                    blocks=document.blocks, research_family=market_scopes[0],
                    instrument_scopes=instrument_scopes, created_at=now)
            # This receipt accepts lossless extraction, never the author's thesis.
            correction = DocumentCorrection(
                schema_version="document-correction.v1",
                correction_id="corr_" + version.version_id.removeprefix("docver_"),
                version_id=version.version_id,
                correction_model_ref="source-verbatim.v1",
                prompt_ref="no-model-verbatim.v1",
                corrected_content_ref=document.normalized_content_ref,
                quality_status="ready_for_correction",
                changes=[],
                unresolved_fragments=[],
                created_at=now,
                accepted_at=now,
            )
            for block in document.blocks:
                session.add(_block_record(block))
            for claim in claims:
                session.add(_claim_record(claim))
            session.add(_correction_record(correction))
            original = session.get(ResearchDocumentVersionRecord, version.version_id)
            assert original is not None
            original.status, original.correction_status = "ready", "accepted"
            original.parse_receipt_ref = self.library.store.write_json(
                document.receipt.model_dump(mode="json"), name="parse-receipt.json"
            )
            original.corrected_content_ref = document.normalized_content_ref
            original.block_count, original.claim_count = len(document.blocks), len(claims)
            original.quality_json = _canonical_json(
                document.receipt.quality.model_dump(mode="json")
            )
            body = {
                "schema_version": IMPORT_SCHEMA_V2 if capture is not None else IMPORT_SCHEMA,
                "version_id": version.version_id,
                "document_id": version.document_id,
                "binding": binding,
                "claim_count": len(claims),
                "parsed_block_count": len(document.blocks),
                "extraction_acceptance": "verbatim_only_claims_remain_candidate",
            }
            session.add(
                ProductCommandRecord(
                    request_id=request_id,
                    owner_id=IMPORT_OWNER,
                    target_id=version.version_id,
                    kind=IMPORT_KIND,
                    payload_hash=canonical_hash(body),
                    result_json=_canonical_json(body),
                    created_at=now,
                )
            )
            session.flush()
            return _version_view(original)


class PublicOpinionResearchContext:
    def __init__(self, library: ResearchLibraryService):
        self.library = library

    def prepare_candidates_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        research_family: str,
        instrument_scopes: tuple[str, ...],
        cutoff: datetime,
    ) -> tuple[EvidenceCandidate, ...]:
        cutoff = aware_utc(cutoff)
        scope = session.get(ResearchRunScopeRecord, run_id, populate_existing=True)
        receipts = session.scalars(
            select(ProductCommandRecord)
            .where(
                ProductCommandRecord.kind == IMPORT_KIND,
                ProductCommandRecord.created_at <= cutoff,
            )
            .order_by(ProductCommandRecord.created_at.desc(), ProductCommandRecord.request_id)
            .limit(32)
        ).all()
        candidates = []
        for receipt in receipts:
            body = _body(receipt)
            binding = body["binding"]
            if (
                research_family not in binding["market_scopes"]
                or binding["instrument_scopes"]
                and not set(instrument_scopes).issubset(binding["instrument_scopes"])
            ):
                continue
            version = session.get(ResearchDocumentVersionRecord, body["version_id"])
            if (
                version is None
                or version.status != "ready"
                or version.original_file_hash != binding["original_hash"]
                or as_utc(version.received_at) > cutoff
            ):
                continue
            candidates.append((body, version))
        if scope is None and candidates:
            selected = [version.version_id for _, version in candidates]
            scope = ResearchRunScopeRecord(
                run_id=run_id,
                research_family=research_family,
                domain_pack_ref=research_family + ".research",
                instrument_scope_json=_canonical_json(list(instrument_scopes)),
                library_context_mode="auto",
                selected_version_ids_json=_canonical_json(selected),
                allow_remote_library_content=False,
                request_hash=canonical_hash(
                    {
                        "policy": "public-opinion-context.v1",
                        "versions": selected,
                        "run_id": run_id,
                        "family": research_family,
                        "instruments": instrument_scopes,
                    }
                ),
                created_at=cutoff,
            )
            session.add(scope)
            session.flush()
        if scope is None or scope.library_context_mode == "none":
            return ()
        selected = set(json.loads(scope.selected_version_ids_json))
        result = []
        for body, version in candidates:
            if version.version_id not in selected:
                continue
            source = ClaimSourceContext.model_validate(body["binding"]["source"])
            for claim in session.scalars(
                select(AnalystClaimRecord)
                .where(
                    AnalystClaimRecord.version_id == version.version_id,
                    AnalystClaimRecord.status.in_(("candidate", "accepted")),
                    AnalystClaimRecord.created_at <= cutoff,
                )
                .order_by(AnalystClaimRecord.claim_id)
            ):
                excerpt = (
                    f"Attributed opinion; claim_id={claim.claim_id}; "
                    f"author={body['binding']['author'] or 'unknown'}; "
                    f"publication_precision={source.publication.precision}; "
                    f"source={source.canonical_url}. Not an independently verified fact. "
                    "Preserve conditions and seek counterevidence. "
                    f"Locators: {'; '.join(json.loads(claim.locator_refs_json))}\n"
                    + claim.claim_text
                )
                capture_info = body["binding"].get("capture")
                if capture_info is not None:
                    excerpt = (f"coverage=partial; retained_quote_only; publication_date={capture_info['publication_date']}; "
                        "Historical/background view unless independently established as current; original article context is incomplete. "
                        + capture_info["attribution"] + "\n" + excerpt)
                if len(excerpt) > 4000:
                    continue
                result.append(
                    EvidenceCandidate(
                        evidence_id="library:" + claim.claim_id,
                        requirement_id="market_narrative",
                        kind="document",
                        authority="unverified",
                        source_id="research-library:" + version.version_id,
                        source_url=source.canonical_url,
                        published_at=source.publication.published_at,
                        observed_at=source.first_seen_at,
                        received_at=as_utc(claim.created_at),
                        content_hash=hashlib.sha256(claim.supporting_quote.encode()).hexdigest(),
                        excerpt=excerpt,
                        content_ref="research://claim/" + claim.claim_id,
                        acquisition_receipt_id=body["binding"]["acquisition_receipt_id"],
                        source_content_hash=version.original_file_hash,
                        parser_ref=source.extraction_version,
                        structured_payload_ref=None,
                        tool_call_id=None,
                        research_session_id=run_id,
                        round=1,
                        quality="candidate",
                        freshness_status="unknown",
                        conflict_group=None,
                    )
                )
        return tuple(result)

    def freeze_selected_in_session(
        self,
        session: Session,
        *,
        run_id: str,
        selected_inputs: list[ResearchInputEvidence],
        cutoff: datetime,
    ) -> tuple[FrozenClaimSignal, ...]:
        output = []
        for item in selected_inputs:
            if not item.evidence_id.startswith("library:claim_"):
                continue
            claim_id = item.evidence_id.removeprefix("library:")
            claim = session.get(AnalystClaimRecord, claim_id)
            if claim is None:
                raise ValueError("public_opinion_selected_claim_missing")
            receipts = session.scalars(
                select(ProductCommandRecord).where(
                    ProductCommandRecord.kind == IMPORT_KIND,
                    ProductCommandRecord.target_id == claim.version_id,
                )
            ).all()
            if not receipts:
                continue  # A private legacy library claim has no invented public-source binding.
            if len(receipts) != 1:
                raise ValueError("public_opinion_import_ambiguous")
            body = _body(receipts[0])
            if item.source_id != "research-library:" + body["version_id"]:
                raise ValueError("public_opinion_selected_version_mismatch")
            if (
                item.authority != "unverified"
                or item.kind != "document"
                or item.content_hash != hashlib.sha256(claim.supporting_quote.encode()).hexdigest()
                or claim.claim_text not in item.excerpt
            ):
                raise ValueError("public_opinion_selected_claim_projection_changed")
            output.append(
                freeze_claim_signal_in_session(
                    session,
                    run_id=run_id,
                    document_id=body["document_id"],
                    version_id=body["version_id"],
                    claim_id=claim_id,
                    source=ClaimSourceContext.model_validate(body["binding"]["source"]),
                    cutoff=cutoff,
                    store=self.library.store,
                    acquisition_receipt_id=body["binding"]["acquisition_receipt_id"],
                )
            )
        return tuple(output)
