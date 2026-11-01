from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlparse
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.models import SourceAcquisition
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    SourceAcquisitionRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.sources import SourceRegistryPort

if TYPE_CHECKING:
    from packages.provider_adapters.research.opinion_capture import OpinionCaptureRegistry


class SourceAcquisitionIngestResult(BaseModel):
    """Result of an idempotent receipt admission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt: SourceAcquisition
    created: bool
    receipt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SourceAcquisitionService:
    """Admit browser/HTTP receipts without treating them as Evidence.

    The worker owns browser lifecycle and raw artifacts.  This service owns the
    product-side identity, source-domain policy and durable receipt ledger.
    A later normalizer/Evidence admission may reference this receipt, but a
    receipt alone can never authorize a claim.
    """

    def __init__(
        self,
        database: Database,
        registry: SourceRegistryPort,
        *,
        artifact_root: Path | None = None,
        receipt_only_sources: Mapping[str, tuple[str, ...]] | None = None,
        opinion_captures: OpinionCaptureRegistry | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.opinion_captures = opinion_captures
        self.artifact_root = artifact_root.resolve() if artifact_root is not None else None
        # Trusted composition-only policies do not become polling connectors.
        self.receipt_only_sources = dict(receipt_only_sources or {})
        for source_id, urls in self.receipt_only_sources.items():
            if not source_id or not urls or any(
                urlparse(url).scheme != "https" or not urlparse(url).hostname
                or urlparse(url).username or urlparse(url).password
                or urlparse(url).query or urlparse(url).fragment for url in urls
            ):
                raise ValueError("acquisition_receipt_only_policy_invalid")

    def ingest(self, receipt: SourceAcquisition) -> SourceAcquisitionIngestResult:
        canonical_urls = self.receipt_only_sources.get(receipt.source_id)
        partial = self.opinion_captures is not None and receipt.source_id in self.opinion_captures.permissions
        if partial:
            assert self.opinion_captures is not None
            self.opinion_captures.require(receipt.source_id, str(receipt.url))
        elif canonical_urls is None:
            source = self.registry.get(receipt.source_id)
            self._validate_source_url(source.manifest.allowed_domains, str(receipt.url))
        elif str(receipt.url) not in canonical_urls:
            raise ValueError("acquisition_receipt_only_url_denied")
        self._validate_artifact_paths(receipt.artifacts, root=self.artifact_root)
        if partial:
            if (self.artifact_root is None or len(receipt.artifacts) != 1
                    or receipt.artifacts[0].kind != "json" or receipt.access_mode != "http"
                    or receipt.status != "succeeded" or receipt.status_code != 200
                    or receipt.request_started_at is None or receipt.request_completed_at is None):
                raise ValueError("opinion_capture_acquisition_shape_invalid")
            artifact = receipt.artifacts[0]
            path = (self.artifact_root / artifact.path).resolve()
            capture_bytes = path.read_bytes()
            if hashlib.sha256(capture_bytes).hexdigest() != artifact.content_hash:
                raise ValueError("opinion_capture_acquisition_hash_mismatch")
            capture = self.opinion_captures.validate(capture_bytes, source_id=receipt.source_id, url=str(receipt.url))
            if capture.observed_at != receipt.observed_at:
                raise ValueError("opinion_capture_acquisition_time_mismatch")
        if canonical_urls is not None:
            if self.artifact_root is None:
                raise ValueError("acquisition_receipt_only_root_required")
            self._validate_receipt_only(receipt)
        if receipt.status == "succeeded" and not receipt.artifacts:
            raise ValueError("acquisition_succeeded_without_artifacts")
        if receipt.status == "succeeded" and receipt.observed_at is None:
            raise ValueError("acquisition_succeeded_without_observation")
        started = receipt.request_started_at
        completed = receipt.request_completed_at
        if (started is None) != (completed is None):
            raise ValueError("acquisition_request_time_incomplete")
        if started is not None and completed is not None:
            if completed < started:
                raise ValueError("acquisition_request_time_invalid")
            if receipt.observed_at is not None and not (
                started <= receipt.observed_at <= completed
            ):
                raise ValueError("acquisition_observation_time_invalid")
        payload = receipt.model_dump(mode="json")
        # Hub receive time is server-owned and must not change idempotency.
        for field in ("request_started_at", "request_completed_at", "received_at"):
            if payload.get(field) is None:
                payload.pop(field, None)
        payload.pop("received_at", None)
        canonical = _canonical_json(payload)
        receipt_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.database.session() as session:
            existing = session.get(SourceAcquisitionRecord, receipt.receipt_id)
            if existing is not None:
                if existing.receipt_hash != receipt_hash:
                    raise ValueError("acquisition_receipt_conflict")
                return SourceAcquisitionIngestResult(
                    receipt=_record_to_receipt(existing),
                    created=False,
                    receipt_hash=receipt_hash,
                )
            duplicate = (
                session.query(SourceAcquisitionRecord).filter_by(receipt_hash=receipt_hash).first()
            )
            if duplicate is not None:
                return SourceAcquisitionIngestResult(
                    receipt=_record_to_receipt(duplicate),
                    created=False,
                    receipt_hash=receipt_hash,
                )
            received_at = utcnow()
            if partial and completed is not None and completed > received_at:
                raise ValueError("opinion_capture_acquisition_future")
            if canonical_urls is not None and (
                started is None or completed is None or completed > received_at
                or not 0 <= (received_at - started).total_seconds() <= 20
            ):
                raise ValueError("acquisition_receipt_only_deadline_elapsed")
            session.add(
                SourceAcquisitionRecord(
                    receipt_id=receipt.receipt_id,
                    source_id=receipt.source_id,
                    url=str(receipt.url),
                    access_mode=receipt.access_mode,
                    status=receipt.status,
                    request_started_at=started,
                    request_completed_at=completed,
                    observed_at=receipt.observed_at,
                    received_at=received_at,
                    status_code=receipt.status_code,
                    artifacts_json=_canonical_json(
                        [item.model_dump(mode="json") for item in receipt.artifacts]
                    ),
                    error_code=receipt.error_code,
                    receipt_hash=receipt_hash,
                    created_at=received_at,
                )
            )
        return SourceAcquisitionIngestResult(
            receipt=receipt.model_copy(update={"received_at": received_at}),
            created=True,
            receipt_hash=receipt_hash,
        )

    @staticmethod
    def _validate_receipt_only(receipt: SourceAcquisition) -> None:
        started, completed, observed = (
            receipt.request_started_at, receipt.request_completed_at, receipt.observed_at
        )
        if (receipt.access_mode != "playwright" or started is None or completed is None
                or started.tzinfo is None or completed.tzinfo is None
                or not 0 <= (completed - started).total_seconds() <= 20):
            raise ValueError("acquisition_receipt_only_time_invalid")
        if receipt.status == "succeeded":
            dom = [item for item in receipt.artifacts if item.kind == "dom"]
            if (observed is None or receipt.status_code != 200 or receipt.error_code is not None
                    or observed.tzinfo is None
                    or not started <= observed <= completed or len(dom) != 1):
                raise ValueError("acquisition_receipt_only_dom_invalid")
            if Path(dom[0].path).stat().st_size > 2 * 1024 * 1024:
                raise ValueError("acquisition_receipt_only_dom_size_invalid")

    def list(self, *, limit: int = 100) -> list[SourceAcquisition]:
        if limit < 1 or limit > 500:
            raise ValueError("acquisition_list_limit_invalid")
        with self.database.session() as session:
            rows = (
                session.query(SourceAcquisitionRecord)
                .order_by(
                    SourceAcquisitionRecord.created_at.desc(),
                    SourceAcquisitionRecord.receipt_id.desc(),
                )
                .limit(limit)
                .all()
            )
            return [_record_to_receipt(row) for row in rows]

    @staticmethod
    def _validate_source_url(allowed_domains: Iterable[str], url: str) -> None:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise ValueError("acquisition_url_invalid")
        domains = tuple(domain.lower().lstrip(".") for domain in allowed_domains)
        if domains and not any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in domains
        ):
            raise ValueError("acquisition_url_outside_allowed_domains")

    @staticmethod
    def _validate_artifact_paths(
        artifacts: Iterable[object], *, root: Path | None = None
    ) -> None:
        for item in artifacts:
            path = str(getattr(item, "path", ""))
            parts = path.replace("\\", "/").split("/")
            if not path or ".." in parts:
                raise ValueError("acquisition_artifact_path_invalid")
            if root is None:
                continue
            resolved = Path(path).resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError("acquisition_artifact_outside_root") from exc
            if not resolved.is_file():
                raise ValueError("acquisition_artifact_missing")
            expected_hash = str(getattr(item, "content_hash", ""))
            actual_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError("acquisition_artifact_hash_mismatch")


def _record_to_receipt(row: SourceAcquisitionRecord) -> SourceAcquisition:
    return SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": row.receipt_id,
            "source_id": row.source_id,
            "url": row.url,
            "access_mode": row.access_mode,
            "status": row.status,
            "request_started_at": as_utc(row.request_started_at),
            "request_completed_at": as_utc(row.request_completed_at),
            "observed_at": as_utc(row.observed_at),
            "received_at": as_utc(row.received_at or row.created_at),
            "status_code": row.status_code,
            "artifacts": json.loads(row.artifacts_json),
            "error_code": row.error_code,
        }
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
