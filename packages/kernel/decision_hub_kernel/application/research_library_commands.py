"""Audited local recovery and review commands for document processing."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    ClaimReviewCommand,
    DocumentUploadIntent,
    DocumentUploadReceipt,
    ResearchDocumentVersion,
)
from packages.kernel.decision_hub_kernel.application.research_library import (
    ResearchLibraryService,
    _claim_view,
    _empty_quality,
    _version_view,
    validate_document_upload_bytes,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    AnalystClaimRecord,
    ProductCommandRecord,
    ResearchDocumentRecord,
    ResearchDocumentVersionRecord,
    utcnow,
)


class DocumentRecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["document-recovery-command.v1"]
    request_id: str = Field(min_length=8, max_length=128)
    original_file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResearchLibraryCommands:
    def __init__(self, library: ResearchLibraryService):
        self.library = library
        self.database = library.database

    @staticmethod
    def _digest(owner, target, kind, payload):
        value = json.dumps(
            [owner, target, kind, payload.model_dump(mode="json")],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _existing(session, request_id, owner, target, kind, digest, model):
        row = session.get(ProductCommandRecord, request_id)
        if row is None:
            return None
        if (row.owner_id, row.target_id, row.kind, row.payload_hash) != (
            owner,
            target,
            kind,
            digest,
        ):
            raise ValueError("product_request_reused")
        return model.model_validate_json(row.result_json)

    @staticmethod
    def _save(session, owner, target, kind, command, digest, result):
        session.add(
            ProductCommandRecord(
                request_id=command.request_id,
                owner_id=owner,
                target_id=target,
                kind=kind,
                payload_hash=digest,
                result_json=result.model_dump_json(),
                created_at=utcnow(),
            )
        )

    @staticmethod
    def upload_intent_digest(intent: DocumentUploadIntent) -> str:
        value = json.dumps(
            intent.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _validate_upload_receipt(
        receipt: DocumentUploadReceipt,
        intent: DocumentUploadIntent,
    ) -> None:
        if receipt.intent != intent or receipt.version.original_file_hash != intent.file_sha256:
            raise ValueError("document_upload_receipt_invalid")

    def upload(
        self,
        owner: str,
        request_id: str,
        intent: DocumentUploadIntent,
        *,
        filename: str,
        data: bytes,
    ) -> tuple[DocumentUploadReceipt, bool]:
        """Return the frozen acceptance receipt and whether an archive was created.

        File bytes and every metadata field participate in the intent. SQLite's
        write reservation covers lookup, document creation, and receipt commit.
        Original assets are shared: a failed transaction must never delete them.
        """
        validate_document_upload_bytes(data)
        if not 1 <= len(request_id) <= 128:
            raise ValueError("document_upload_request_id_invalid")
        if owner != intent.owner_id:
            raise ValueError("owner_identity_mismatch")
        if filename != intent.file_name:
            raise ValueError("document_upload_filename_mismatch")
        if len(data) != intent.file_size_bytes:
            raise ValueError("document_upload_size_mismatch")
        if hashlib.sha256(data).hexdigest() != intent.file_sha256:
            raise ValueError("document_upload_hash_mismatch")
        kind = "library_upload"
        digest = self.upload_intent_digest(intent)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            previous = self._existing(
                session,
                request_id,
                owner,
                digest,
                kind,
                digest,
                DocumentUploadReceipt,
            )
            if previous is not None:
                self._validate_upload_receipt(previous, intent)
                if previous.request_id != request_id:
                    raise ValueError("document_upload_receipt_invalid")
                return previous, False
            duplicate = (
                session.query(ProductCommandRecord)
                .filter_by(owner_id=owner, target_id=digest, kind=kind, payload_hash=digest)
                .order_by(ProductCommandRecord.created_at, ProductCommandRecord.request_id)
                .first()
            )
            if duplicate is not None:
                original_receipt = DocumentUploadReceipt.model_validate_json(duplicate.result_json)
                self._validate_upload_receipt(original_receipt, intent)
                version = original_receipt.version
            else:
                version = self.library.record_upload_in_session(
                    session,
                    intent.metadata,
                    filename=filename,
                    data=data,
                )
            receipt = DocumentUploadReceipt(
                schema_version="document-upload-receipt.v1",
                request_id=request_id,
                intent=intent,
                version=version,
                deduplicated=duplicate is not None,
                created_at=utcnow(),
            )
            self._save(session, owner, digest, kind, receipt, digest, receipt)
            return receipt, duplicate is None

    def upload_receipt(self, request_id: str, owner: str) -> DocumentUploadReceipt:
        with self.database.session() as session:
            row = session.get(ProductCommandRecord, request_id)
            if row is None or row.owner_id != owner or row.kind != "library_upload":
                raise KeyError("document_upload_receipt_not_found")
            receipt = DocumentUploadReceipt.model_validate_json(row.result_json)
            digest = self.upload_intent_digest(receipt.intent)
            self._validate_upload_receipt(receipt, receipt.intent)
            if (
                receipt.request_id != request_id
                or receipt.intent.owner_id != owner
                or row.target_id != digest
                or row.payload_hash != digest
            ):
                raise ValueError("document_upload_receipt_invalid")
            return receipt

    def recover(self, version_id: str, owner: str, command: DocumentRecoveryCommand):
        kind = "library_recovery"
        digest = self._digest(owner, version_id, kind, command)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            previous = self._existing(
                session,
                command.request_id,
                owner,
                version_id,
                kind,
                digest,
                ResearchDocumentVersion,
            )
            if previous:
                if previous.supersedes_version_id != version_id:
                    raise ValueError("document_recovery_receipt_invalid")
                return previous
            failed = session.get(ResearchDocumentVersionRecord, version_id)
            if failed is None:
                raise KeyError("document_version_not_found")
            if failed.status != "failed":
                raise ValueError("document_recovery_requires_failed_version")
            document = session.get(ResearchDocumentRecord, failed.document_id)
            if document is None or document.latest_version_id != version_id:
                raise ValueError("document_recovery_latest_version_changed")
            if command.original_file_hash != failed.original_file_hash:
                raise ValueError("document_recovery_original_changed")
            original = self.library.store.read(failed.original_file_ref)
            if hashlib.sha256(original).hexdigest() != failed.original_file_hash:
                raise ValueError("document_recovery_original_changed")
            # Source dates remain the dates of the uploaded bytes. A new processing
            # time must not make the same source look newly published or observed.
            now = utcnow()
            recovered = ResearchDocumentVersionRecord(
                version_id=f"docver_{uuid.uuid4().hex}",
                document_id=failed.document_id,
                original_file_ref=failed.original_file_ref,
                original_file_hash=failed.original_file_hash,
                status="received",
                correction_status="not_requested",
                published_at=failed.published_at,
                observed_at=failed.observed_at,
                received_at=failed.received_at,
                supersedes_version_id=version_id,
                parse_receipt_ref=None,
                corrected_content_ref=None,
                block_count=0,
                claim_count=0,
                attempt=0,
                created_at=now,
                quality_json=_empty_quality().model_dump_json(),
            )
            session.add(recovered)
            document.latest_version_id = recovered.version_id
            result = _version_view(recovered)
            self._save(session, owner, version_id, kind, command, digest, result)
            return result

    def review_claim(self, claim_id: str, owner: str, command: ClaimReviewCommand):
        kind = "claim_review"
        digest = self._digest(owner, claim_id, kind, command)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            previous = self._existing(
                session,
                command.request_id,
                owner,
                claim_id,
                kind,
                digest,
                AnalystClaim,
            )
            if previous:
                if previous.claim_id != claim_id:
                    raise ValueError("claim_review_receipt_invalid")
                return previous
            row = session.get(AnalystClaimRecord, claim_id)
            if row is None:
                raise KeyError("claim_not_found")
            version = session.get(ResearchDocumentVersionRecord, row.version_id)
            if version is None or version.status != "ready":
                raise ValueError("claim_version_not_ready")
            target = {"accept": "accepted", "reject": "rejected", "supersede": "superseded"}[
                command.decision
            ]
            allowed = row.status == "candidate" or (
                command.decision == "supersede" and row.status in {"accepted", "rejected"}
            )
            if not allowed:
                raise ValueError("claim_review_state_changed")
            row.status, row.review_reason, row.reviewed_at = target, command.reason, utcnow()
            result = _claim_view(row)
            self._save(session, owner, claim_id, kind, command, digest, result)
            return result
