from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    ClaimReviewCommand,
    DocumentBlock,
    DocumentCorrection,
    DocumentParseReceipt,
    DocumentQuality,
    DocumentUpload,
    DocumentUploadIntent,
    DocumentUploadReceipt,
    ResearchDocument,
    ResearchDocumentVersion,
    ResearchRequest,
    ResearchRunScope,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalystClaimRecord,
    Database,
    ResearchDocumentBlockRecord,
    ResearchDocumentCorrectionRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    ResearchRunScopeRecord,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.documents import (
    AnalystClaimExtractorPort,
    DocumentCorrectionPort,
    DocumentInput,
    DocumentParserPort,
    ParsedDocument,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

from .research_library_provenance import (KNOWN_TEST_HASHES, DocumentProvenanceView, provenance_for, require_market_document)

MAX_DOCUMENT_FILE_SIZE_BYTES = 20 * 1024 * 1024

if TYPE_CHECKING:
    from .public_opinion import PublicOpinionImportService


def validate_document_upload_bytes(data: bytes) -> None:
    if len(data) > MAX_DOCUMENT_FILE_SIZE_BYTES:
        raise ValueError("document_file_too_large")
    if not data:
        raise ValueError("document_empty_file")


def _block_order(block: DocumentBlock) -> tuple[int, int, int, str]:
    """Existing parser IDs have an integer ordinal, not lexical ordering."""
    ordinal = re.search(r":block:(\d+)$", block.block_id)
    return (block.page_no, 0 if ordinal else 1, int(ordinal[1]) if ordinal else 0, block.block_id)


def _require_lease(row: ResearchDocumentVersionRecord, owner: str | None) -> None:
    if owner is not None and (
        row.lease_owner != owner or (as_utc(row.lease_until) or utcnow()) <= utcnow()
    ):
        raise PermissionError("document_processing_lease_lost")


def _require_document_quality(row: ResearchDocumentVersionRecord) -> None:
    if _quality_from_json(row.quality_json).quality_status in {"blocked_low_quality", "failed"}:
        raise ValueError("document_quality_blocked")


def _require_correction_quality(row: ResearchDocumentCorrectionRecord) -> None:
    if row.quality_status in {"blocked_low_quality", "failed"}:
        raise ValueError("document_quality_blocked")


class ResearchLibraryService:
    """Application boundary for immutable research documents.

    The service owns state transitions and never lets parsed or corrected text
    replace the uploaded bytes. Slow parsing/correction can be driven by the
    durable worker through ``process_next``; the API only records commands.
    """

    def __init__(
        self,
        database: Database,
        store: DocumentBundleStore,
        parser: DocumentParserPort,
        correction: DocumentCorrectionPort,
        claims: AnalystClaimExtractorPort,
        acquisition_root: Path | None = None,
    ) -> None:
        self.database = database
        self.store = store
        self.parser = parser
        self.correction = correction
        self.claims = claims
        self.acquisition_root = acquisition_root.resolve() if acquisition_root else None
        self.public_opinion_importer: PublicOpinionImportService | None = None

    def upload(
        self,
        payload: DocumentUpload,
        *,
        filename: str,
        data: bytes,
        owner: str = "library-legacy-internal",
    ) -> ResearchDocumentVersion:
        receipt, _ = self.accept_legacy_upload(payload, filename=filename, data=data, owner=owner)
        return receipt.version

    def accept_legacy_upload(
        self,
        payload: DocumentUpload,
        *,
        filename: str,
        data: bytes,
        owner: str,
    ) -> tuple[DocumentUploadReceipt, bool]:
        # Old rows do not retain notes, the filename, or the submitting owner.
        # Only a complete persisted intent can establish an upload duplicate.
        from .research_library_commands import ResearchLibraryCommands

        validate_document_upload_bytes(data)
        intent = DocumentUploadIntent(
            schema_version="document-upload-intent.v1",
            owner_id=owner,
            file_name=filename,
            file_size_bytes=len(data),
            file_sha256=hashlib.sha256(data).hexdigest(),
            metadata=payload,
        )
        commands = ResearchLibraryCommands(self)
        digest = commands.upload_intent_digest(intent)
        return commands.upload(
            owner,
            f"legacy-upload:{digest}",
            intent,
            filename=filename,
            data=data,
        )

    def record_upload_in_session(
        self,
        session: Session,
        payload: DocumentUpload,
        *,
        filename: str,
        data: bytes,
    ) -> ResearchDocumentVersion:
        """Called inside the command's serialized document/receipt transaction."""
        validate_document_upload_bytes(data)
        original_ref, digest = self.store.write_original(data, suffix=Path(filename).suffix)
        now = utcnow()
        document_id = f"doc_{uuid.uuid4().hex}"
        version_id = f"docver_{uuid.uuid4().hex}"
        document = ResearchDocumentRecord(
            document_id=document_id,
            title=payload.title,
            source_type=payload.source_type,
            source_profile_id=payload.source_profile_id,
            author=payload.author,
            market_scopes_json=json.dumps(payload.market_scopes, ensure_ascii=False),
            instrument_scopes_json=json.dumps(payload.instrument_scopes, ensure_ascii=False),
            privacy_mode=payload.privacy_mode,
            visibility=payload.visibility,
            created_at=now,
            latest_version_id=version_id,
        )
        version = ResearchDocumentVersionRecord(
            version_id=version_id,
            document_id=document_id,
            original_file_ref=original_ref,
            original_file_hash=digest,
            status="received",
            correction_status="not_requested",
            published_at=payload.published_at,
            observed_at=now,
            received_at=now,
            supersedes_version_id=payload.supersedes_version_id,
            block_count=0,
            claim_count=0,
            quality_json=json.dumps(_empty_quality().model_dump(mode="json"), ensure_ascii=False),
            attempt=0,
            created_at=now,
        )
        session.add(document)
        session.add(version)
        return _version_view(version)

    def import_acquisition(
        self, receipt_id: str, payload: DocumentUpload
    ) -> ResearchDocumentVersion:
        """Materialize one validated browser artifact into this library."""
        if self.public_opinion_importer is not None and payload.source_type in {"public_web", "official"}:
            return self.public_opinion_importer.import_acquisition(
                receipt_id, market_scopes=tuple(payload.market_scopes),
                instrument_scopes=tuple(payload.instrument_scopes or ()),
            )
        if self.acquisition_root is None:
            raise ValueError("acquisition_artifact_root_not_configured")
        with self.database.session() as session:
            receipt = session.get(SourceAcquisitionRecord, receipt_id)
            if receipt is None:
                raise KeyError("acquisition_receipt_not_found")
            if receipt.status != "succeeded":
                raise ValueError("acquisition_receipt_not_succeeded")
            artifacts = json.loads(receipt.artifacts_json)
            if not artifacts:
                raise ValueError("acquisition_receipt_without_artifact")
            relative_path = str(artifacts[0].get("path", ""))
        path = (self.acquisition_root / relative_path).resolve()
        try:
            path.relative_to(self.acquisition_root)
        except ValueError as exc:
            raise ValueError("acquisition_artifact_outside_root") from exc
        return self.upload(payload, filename=path.name, data=path.read_bytes())

    def list_documents(self, *, limit: int = 100) -> list[ResearchDocument]:
        with self.database.session() as session:
            rows = (
                session.query(ResearchDocumentRecord)
                .order_by(ResearchDocumentRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            result: list[ResearchDocument] = []
            for row in rows:
                count = (
                    session.query(ResearchDocumentVersionRecord)
                    .filter_by(document_id=row.document_id)
                    .count()
                )
                result.append(_document_view(row, count))
            return result

    def document_provenance(self, version_id: str) -> DocumentProvenanceView | None:
        """Derived exact-version content classification; never an origin credential."""
        with self.database.session() as session:
            version = session.get(ResearchDocumentVersionRecord, version_id)
            if version is None:
                return None
            return provenance_for(version, self.store)

    def get_document(self, document_id: str) -> ResearchDocument | None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentRecord, document_id)
            if row is None:
                return None
            count = (
                session.query(ResearchDocumentVersionRecord)
                .filter_by(document_id=document_id)
                .count()
            )
            return _document_view(row, count)

    def get_version(self, version_id: str) -> ResearchDocumentVersion | None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            return _version_view(row) if row is not None else None

    def original_path(self, version_id: str) -> Path | None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            if row is None:
                return None
            return self.store.path_for(row.original_file_ref)

    def get_blocks(self, version_id: str) -> list[DocumentBlock]:
        with self.database.session() as session:
            rows = (
                session.query(ResearchDocumentBlockRecord)
                .filter_by(version_id=version_id)
                .order_by(ResearchDocumentBlockRecord.page_no, ResearchDocumentBlockRecord.block_id)
                .all()
            )
            return sorted((_block_view(row) for row in rows), key=_block_order)

    def get_correction(self, version_id: str) -> DocumentCorrection | None:
        with self.database.session() as session:
            row = (
                session.query(ResearchDocumentCorrectionRecord)
                .filter_by(version_id=version_id)
                .order_by(ResearchDocumentCorrectionRecord.created_at.desc())
                .first()
            )
            return _correction_view(row) if row is not None else None

    def get_claims(self, version_id: str) -> list[AnalystClaim]:
        with self.database.session() as session:
            rows = (
                session.query(AnalystClaimRecord)
                .filter_by(version_id=version_id)
                .order_by(AnalystClaimRecord.created_at)
                .all()
            )
            return [_claim_view(row) for row in rows]

    def request_correction(self, version_id: str) -> ResearchDocumentVersion:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            if row is None:
                raise KeyError("document_version_not_found")
            _require_document_quality(row)
            if row.status not in {"parsed", "review"}:
                raise ValueError("document_not_ready_for_correction")
            row.status = "correcting"
            row.correction_status = "running"
            row.attempt += 1
            return _version_view(row)

    async def accept_correction(self, version_id: str) -> ResearchDocumentVersion:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            if row is None:
                raise KeyError("document_version_not_found")
            _require_document_quality(row)
            correction = (
                session.query(ResearchDocumentCorrectionRecord)
                .filter_by(version_id=version_id)
                .order_by(ResearchDocumentCorrectionRecord.created_at.desc())
                .first()
            )
            if correction is None:
                raise ValueError("document_correction_not_found")
            _require_correction_quality(correction)
            if row.status == "ready" and row.correction_status == "accepted":
                return _version_view(row)
            if row.status != "review":
                raise ValueError("document_not_ready_for_review")
            correction_id = correction.correction_id
        await self._extract_claims(version_id, correction_id=correction_id)
        return self.get_version(version_id)  # type: ignore[return-value]

    def review_claim(self, claim_id: str, command: ClaimReviewCommand) -> AnalystClaim:
        with self.database.session() as session:
            row = session.get(AnalystClaimRecord, claim_id)
            if row is None:
                raise KeyError("claim_not_found")
            row.status = {"accept": "accepted", "reject": "rejected", "supersede": "superseded"}[
                command.decision
            ]
            row.review_reason = command.reason
            row.reviewed_at = utcnow()
            return _claim_view(row)

    def select_context(
        self, request: ResearchRequest, *, run_id: str, session: Session | None = None
    ) -> dict[str, Any]:
        """Freeze selection and consent in the same transaction as admission."""
        with self.database.session() if session is None else nullcontext(session) as session:
            request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
            previous = session.get(ResearchRunScopeRecord, run_id)
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise ValueError("research_library_context_immutable")
                return {
                    "run_id": run_id,
                    "research_family": previous.research_family,
                    "selected_version_ids": json.loads(previous.selected_version_ids_json),
                    "library_context_mode": previous.library_context_mode,
                }
            selected = list(request.selected_version_ids)
            if request.library_context_mode == "none":
                selected = []
            elif request.library_context_mode == "auto":
                rows = (
                    session.query(ResearchDocumentVersionRecord)
                    .join(
                        ResearchDocumentRecord,
                        ResearchDocumentRecord.document_id
                        == ResearchDocumentVersionRecord.document_id,
                    )
                    .filter(
                        ResearchDocumentRecord.market_scopes_json.like(
                            f"%{request.research_family}%"
                        ),
                        ResearchDocumentVersionRecord.status == "ready",
                        ResearchDocumentVersionRecord.original_file_hash.notin_(KNOWN_TEST_HASHES),
                    )
                    .order_by(ResearchDocumentVersionRecord.received_at.desc())
                    .limit(32)
                    .all()
                )
                selected = [row.version_id for row in rows]
            else:
                rows = (
                    session.query(ResearchDocumentVersionRecord)
                    .join(
                        ResearchDocumentRecord,
                        ResearchDocumentRecord.document_id
                        == ResearchDocumentVersionRecord.document_id,
                    )
                    .filter(ResearchDocumentVersionRecord.version_id.in_(selected))
                    .filter(ResearchDocumentVersionRecord.status == "ready")
                    .filter(
                        ResearchDocumentRecord.market_scopes_json.like(
                            f"%{request.research_family}%"
                        )
                    )
                    .all()
                )
                if len(rows) != len(set(selected)):
                    raise ValueError("selected_document_version_not_found")
            for version_id in selected:
                version = session.get(ResearchDocumentVersionRecord, version_id)
                _require_document_quality(version)
                require_market_document(version, self.store)
                document = session.get(ResearchDocumentRecord, version.document_id)
                scopes = json.loads(document.instrument_scopes_json)
                if scopes and not set(request.instrument_scope).issubset(scopes):
                    raise ValueError("document_instrument_scope_mismatch")
                if document.privacy_mode != "remote_allowed" and not (
                    request.library_context_mode == "selected"
                    and request.allow_remote_library_content
                ):
                    raise ValueError("document_remote_consent_required")
            scope = ResearchRunScopeRecord(
                run_id=run_id,
                research_family=request.research_family,
                domain_pack_ref=f"{request.research_family}.research",
                instrument_scope_json=json.dumps(request.instrument_scope, ensure_ascii=False),
                library_context_mode=request.library_context_mode,
                selected_version_ids_json=json.dumps(selected, ensure_ascii=False),
                allow_remote_library_content=request.allow_remote_library_content,
                request_hash=request_hash,
                created_at=utcnow(),
            )
            session.add(scope)
            return {
                "run_id": run_id,
                "research_family": request.research_family,
                "selected_version_ids": selected,
                "library_context_mode": request.library_context_mode,
            }

    def get_context(self, run_id: str) -> ResearchRunScope | None:
        """Read the version selection ledger; the research context manifest holds Evidence IDs."""
        with self.database.session() as session:
            scope = session.get(ResearchRunScopeRecord, run_id)
            if scope is None:
                return None
            return ResearchRunScope(
                schema_version="research-run-scope.v1",
                run_id=scope.run_id,
                research_family=scope.research_family,
                library_context_mode=scope.library_context_mode,
                selected_version_ids=json.loads(scope.selected_version_ids_json),
                allow_remote_library_content=scope.allow_remote_library_content,
                context_inputs=json.loads(scope.input_receipts_json),
            )

    async def process_next(self, *, worker_id: str = "documents") -> str | None:
        token = f"{worker_id[:64]}:{uuid.uuid4().hex}"
        claimed = self._claim_next(token)
        if claimed is None:
            return None
        version_id, claimed_status = claimed
        try:
            async with asyncio.timeout(300):
                if claimed_status == "received":
                    await asyncio.to_thread(self._parse_version, version_id, token)
                elif claimed_status == "correcting":
                    await self._correct_version(version_id, token)
            return version_id
        except (Exception, asyncio.CancelledError) as exc:
            with self.database.session() as session:
                row = session.get(ResearchDocumentVersionRecord, version_id)
                if row is not None and row.lease_owner == token:
                    row.status = "failed"
                    row.correction_status = (
                        "failed" if row.correction_status == "running" else row.correction_status
                    )
                    quality = _quality_from_json(row.quality_json)
                    phase = "parse" if claimed_status == "received" else "correction"
                    issue = f"document_processing_failed:{phase}:{type(exc).__name__}"
                    if issue not in quality.issues:
                        quality.issues.append(issue)
                    row.quality_json = quality.model_dump_json()
                    row.lease_owner = None
                    row.lease_until = None
            raise

    def _claim_next(self, worker_id: str) -> tuple[str, str] | None:
        now = utcnow()
        with self.database.session() as session:
            rows = (
                session.query(ResearchDocumentVersionRecord)
                .filter(
                    ResearchDocumentVersionRecord.status.in_(["received", "parsing", "correcting"]),
                    or_(
                        ResearchDocumentVersionRecord.lease_until.is_(None),
                        ResearchDocumentVersionRecord.lease_until <= now,
                    ),
                )
                .order_by(ResearchDocumentVersionRecord.received_at)
                .all()
            )
            for row in rows:
                next_status = "correcting" if row.status == "correcting" else "parsing"
                changed = session.execute(
                    update(ResearchDocumentVersionRecord)
                    .where(
                        ResearchDocumentVersionRecord.version_id == row.version_id,
                        ResearchDocumentVersionRecord.status == row.status,
                        or_(
                            ResearchDocumentVersionRecord.lease_until.is_(None),
                            ResearchDocumentVersionRecord.lease_until <= now,
                        ),
                    )
                    .values(
                        lease_owner=worker_id,
                        lease_until=datetime.fromtimestamp(now.timestamp() + 900, tz=UTC),
                        status=next_status,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(getattr(changed, "rowcount", 0)) == 1:
                    return row.version_id, "received" if next_status == "parsing" else "correcting"
            return None

    def _parse_version(self, version_id: str, lease_owner: str | None = None) -> None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            document = session.get(ResearchDocumentRecord, row.document_id) if row else None
            if row is None or document is None:
                raise KeyError("document_version_not_found")
            input_data = DocumentInput(
                version_id=version_id,
                file_path=self.store.path_for(row.original_file_ref),
                original_file_ref=row.original_file_ref,
                original_file_hash=row.original_file_hash,
                title=document.title,
                privacy_mode=document.privacy_mode,
            )
        parsed = self.parser.parse(input_data)
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            if row is None:
                raise KeyError("document_version_not_found")
            _require_lease(row, lease_owner)
            row.status = "parsed"
            row.correction_status = "not_requested"
            row.parse_receipt_ref = self.store.write_json(
                parsed.receipt.model_dump(mode="json"),
                name="parse-receipt.json",
            )
            row.block_count = len(parsed.blocks)
            row.quality_json = json.dumps(
                parsed.receipt.quality.model_dump(mode="json"), ensure_ascii=False
            )
            row.lease_owner = None
            row.lease_until = None
            for block in parsed.blocks:
                session.merge(_block_record(block))

    async def _correct_version(self, version_id: str, lease_owner: str | None = None) -> None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            document = session.get(ResearchDocumentRecord, row.document_id) if row else None
            if row is None or document is None or not row.parse_receipt_ref:
                raise ValueError("document_parse_required")
            blocks = tuple(
                _block_view(item)
                for item in session.query(ResearchDocumentBlockRecord)
                .filter_by(version_id=version_id)
                .order_by(ResearchDocumentBlockRecord.block_id)
                .all()
            )
            blocks = tuple(sorted(blocks, key=_block_order))
            receipt = DocumentParseReceipt.model_validate(
                json.loads(self.store.read(row.parse_receipt_ref))
            )
            parsed = ParsedDocument(
                receipt=receipt,
                blocks=blocks,
                normalized_text=self.store.read(receipt.parsed_bundle_ref).decode("utf-8"),
                normalized_content_ref=receipt.parsed_bundle_ref,
            )
            market_scopes = tuple(json.loads(document.market_scopes_json))
            instrument_scopes = tuple(json.loads(document.instrument_scopes_json))
            privacy_mode = document.privacy_mode
        result = await self.correction.correct(
            parsed,
            title=document.title,
            market_scopes=market_scopes,
            instrument_scopes=instrument_scopes,
            privacy_mode=privacy_mode,
        )
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            if row is None:
                raise KeyError("document_version_not_found")
            _require_lease(row, lease_owner)
            row.status = "review"
            row.correction_status = (
                "needs_review" if result.correction.quality_status == "needs_review" else "ready"
            )
            row.corrected_content_ref = result.correction.corrected_content_ref
            row.lease_owner = None
            row.lease_until = None
            session.merge(_correction_record(result.correction))

    async def _extract_claims(self, version_id: str, *, correction_id: str) -> None:
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            document = session.get(ResearchDocumentRecord, row.document_id) if row else None
            correction = (
                session.query(ResearchDocumentCorrectionRecord)
                .filter_by(version_id=version_id)
                .order_by(ResearchDocumentCorrectionRecord.created_at.desc())
                .first()
                if row
                else None
            )
            blocks = (
                tuple(
                    _block_view(item)
                    for item in session.query(ResearchDocumentBlockRecord)
                    .filter_by(version_id=version_id)
                    .order_by(ResearchDocumentBlockRecord.block_id)
                    .all()
                )
                if row
                else ()
            )
            if row is None or document is None or correction is None:
                raise ValueError("document_correction_required")
            if correction.correction_id != correction_id:
                raise ValueError("document_correction_changed")
            _require_document_quality(row)
            _require_correction_quality(correction)
            market_scopes = tuple(json.loads(document.market_scopes_json))
            instrument_scopes = tuple(json.loads(document.instrument_scopes_json))
            # A corrected paragraph can contain newlines. Match the audited
            # changes by immutable block ID; never zip document lines to blocks.
            changes = {change.block_id: change for change in _correction_view(correction).changes}
            corrected = []
            for block in sorted(blocks, key=_block_order):
                change = changes.get(block.block_id)
                if change is not None and change.original != block.content:
                    raise ValueError("document_correction_source_mismatch")
                corrected.append(
                    block.model_copy(update={"content": change.corrected}) if change else block
                )
            corrected_blocks = tuple(corrected)
        claims = await self.claims.extract(
            version_id=version_id,
            corrected_blocks=corrected_blocks,
            market_scopes=market_scopes,
            instrument_scopes=instrument_scopes,
            author=document.author,
            published_at=document.created_at,
        )
        with self.database.session() as session:
            row = session.get(ResearchDocumentVersionRecord, version_id)
            correction = session.get(ResearchDocumentCorrectionRecord, correction_id)
            if row is None or correction is None or row.status != "review":
                raise ValueError("document_review_state_changed")
            _require_document_quality(row)
            _require_correction_quality(correction)
            for claim in claims:
                session.merge(_claim_record(claim))
            row.claim_count = len(claims)
            correction.accepted_at = utcnow()
            correction.quality_status = "ready_for_correction"
            row.correction_status = "accepted"
            row.status = "ready"


def _empty_quality() -> DocumentQuality:
    return DocumentQuality(
        layout_coverage=0,
        text_readability=0,
        table_integrity=0,
        image_coverage=0,
        numeric_suspicion=0,
        encoding_errors=0,
        locator_completeness=0,
        quality_status="blocked_low_quality",
        issues=["not_parsed"],
    )


def _quality_from_json(value: str) -> DocumentQuality:
    return DocumentQuality.model_validate(json.loads(value or "{}"))


def _document_view(row: ResearchDocumentRecord, count: int) -> ResearchDocument:
    return ResearchDocument(
        schema_version="research-document.v1",
        document_id=row.document_id,
        title=row.title,
        source_type=row.source_type,
        source_profile_id=row.source_profile_id,
        author=row.author,
        market_scopes=json.loads(row.market_scopes_json),
        instrument_scopes=json.loads(row.instrument_scopes_json),
        privacy_mode=row.privacy_mode,
        visibility=row.visibility,
        created_at=row.created_at.replace(tzinfo=UTC)
        if row.created_at.tzinfo is None
        else row.created_at,
        latest_version_id=row.latest_version_id,
        version_count=count,
    )


def _version_view(row: ResearchDocumentVersionRecord) -> ResearchDocumentVersion:
    return ResearchDocumentVersion(
        schema_version="research-document-version.v1",
        version_id=row.version_id,
        document_id=row.document_id,
        original_file_ref=row.original_file_ref,
        original_file_hash=row.original_file_hash,
        status=row.status,
        correction_status=row.correction_status,
        published_at=_aware(row.published_at),
        observed_at=_aware(row.observed_at),
        received_at=_aware(row.received_at),
        supersedes_version_id=row.supersedes_version_id,
        parse_receipt_ref=row.parse_receipt_ref,
        corrected_content_ref=row.corrected_content_ref,
        block_count=row.block_count,
        claim_count=row.claim_count,
        quality=_quality_from_json(row.quality_json),
        created_at=_aware(row.created_at),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _block_record(block: DocumentBlock) -> ResearchDocumentBlockRecord:
    return ResearchDocumentBlockRecord(
        block_id=block.block_id,
        version_id=block.version_id,
        block_type=block.block_type,
        page_no=block.page_no,
        content=block.content,
        content_hash=block.content_hash,
        locator=block.locator,
        bbox_json=json.dumps(block.bbox) if block.bbox else None,
        confidence=block.confidence,
        has_numeric_content=block.has_numeric_content,
        needs_review=block.needs_review,
    )


def _block_view(row: ResearchDocumentBlockRecord) -> DocumentBlock:
    return DocumentBlock(
        schema_version="document-block.v1",
        block_id=row.block_id,
        version_id=row.version_id,
        block_type=row.block_type,
        page_no=row.page_no,
        content=row.content,
        content_hash=row.content_hash,
        locator=row.locator,
        bbox=json.loads(row.bbox_json) if row.bbox_json else None,
        confidence=row.confidence,
        has_numeric_content=row.has_numeric_content,
        needs_review=row.needs_review,
    )


def _correction_record(correction: DocumentCorrection) -> ResearchDocumentCorrectionRecord:
    return ResearchDocumentCorrectionRecord(
        correction_id=correction.correction_id,
        version_id=correction.version_id,
        correction_model_ref=correction.correction_model_ref,
        prompt_ref=correction.prompt_ref,
        corrected_content_ref=correction.corrected_content_ref,
        quality_status=correction.quality_status,
        changes_json=json.dumps(
            [item.model_dump(mode="json") for item in correction.changes], ensure_ascii=False
        ),
        unresolved_json=json.dumps(
            [item.model_dump(mode="json") for item in correction.unresolved_fragments],
            ensure_ascii=False,
        ),
        created_at=correction.created_at,
        accepted_at=correction.accepted_at,
    )


def _correction_view(row: ResearchDocumentCorrectionRecord) -> DocumentCorrection:
    return DocumentCorrection.model_validate(
        {
            "schema_version": "document-correction.v1",
            "correction_id": row.correction_id,
            "version_id": row.version_id,
            "correction_model_ref": row.correction_model_ref,
            "prompt_ref": row.prompt_ref,
            "corrected_content_ref": row.corrected_content_ref,
            "quality_status": row.quality_status,
            "changes": json.loads(row.changes_json),
            "unresolved_fragments": json.loads(row.unresolved_json),
            "created_at": _aware(row.created_at),
            "accepted_at": _aware(row.accepted_at),
        }
    )


def _claim_record(claim: AnalystClaim) -> AnalystClaimRecord:
    return AnalystClaimRecord(
        claim_id=claim.claim_id,
        version_id=claim.version_id,
        claim_type=claim.claim_type,
        stance=claim.stance,
        claim_text=claim.claim_text,
        supporting_quote=claim.supporting_quote,
        locator_refs_json=json.dumps(claim.locator_refs, ensure_ascii=False),
        market_scope=claim.market_scope,
        instrument_scope_json=json.dumps(claim.instrument_scope, ensure_ascii=False),
        horizon=claim.horizon,
        conditions_json=json.dumps(claim.conditions, ensure_ascii=False),
        invalidation_conditions_json=json.dumps(claim.invalidation_conditions, ensure_ascii=False),
        status=claim.status,
        attribution_confidence=claim.attribution_confidence,
        requires_independent_evidence=claim.requires_independent_evidence,
        created_at=claim.created_at,
        reviewed_at=claim.reviewed_at,
    )


def _claim_view(row: AnalystClaimRecord) -> AnalystClaim:
    return AnalystClaim.model_validate(
        {
            "schema_version": "analyst-claim.v1",
            "claim_id": row.claim_id,
            "version_id": row.version_id,
            "claim_type": row.claim_type,
            "stance": row.stance,
            "claim_text": row.claim_text,
            "supporting_quote": row.supporting_quote,
            "locator_refs": json.loads(row.locator_refs_json),
            "market_scope": row.market_scope,
            "instrument_scope": json.loads(row.instrument_scope_json),
            "horizon": row.horizon,
            "conditions": json.loads(row.conditions_json),
            "invalidation_conditions": json.loads(row.invalidation_conditions_json),
            "status": row.status,
            "attribution_confidence": row.attribution_confidence,
            "requires_independent_evidence": row.requires_independent_evidence,
            "created_at": _aware(row.created_at),
            "reviewed_at": _aware(row.reviewed_at),
        }
    )
