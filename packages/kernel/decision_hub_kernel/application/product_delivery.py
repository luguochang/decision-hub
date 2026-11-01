from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import and_, func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts.generated.product_delivery import (
    ReadReceiptCommand,
    ReadReceiptView,
    ReportDeliveryItem,
    ReportDeliveryPage,
    StudyTrackingCommand,
    StudyTrackingReceipt,
)
from packages.contracts_py.decision_hub_contracts.generated.read_command_receipt import (
    ReadCommandReceipt,
)
from packages.kernel.decision_hub_kernel.application.market_scope import require_research_scope
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    FeedbackRecord,
    OutboxRecord,
    ProductCommandRecord,
    ProductReadReceiptRecord,
    ReportRevisionRecord,
    ReviewScheduleRecord,
    RunRecord,
    StudyRecord,
    as_utc,
    utcnow,
)


class ProductDeliveryService:
    def __init__(self, database: Database, clock: Callable[[], datetime] = utcnow):
        self.database, self.clock = database, clock

    def list_reports(
        self, owner: str, *, limit: int = 12, offset: int = 0,
        unread_only: bool = False, run_id: str | None = None,
    ) -> ReportDeliveryPage:
        with self.database.session() as session:
            query = (
                session.query(ReportRevisionRecord, StudyRecord, ProductReadReceiptRecord)
                .join(StudyRecord, StudyRecord.study_id == ReportRevisionRecord.study_id)
                .outerjoin(
                    ProductReadReceiptRecord,
                    and_(
                        ProductReadReceiptRecord.revision_id == ReportRevisionRecord.revision_id,
                        ProductReadReceiptRecord.owner_id == owner,
                    ),
                )
            )
            if unread_only:
                query = query.filter(ProductReadReceiptRecord.read_at.is_(None))
            query = query.filter(ReportRevisionRecord.strategy_version == "research.v1")
            if run_id:
                query = query.filter(ReportRevisionRecord.source_run_id == run_id)
            total = query.count()
            rows = (
                query.order_by(
                    ReportRevisionRecord.created_at.desc(), ReportRevisionRecord.revision_id.desc()
                )
                .limit(limit)
                .offset(offset)
                .all()
            )
            artifact_ids = [r.artifact_id for r, _, _ in rows]
            feedbacks = (
                session.query(FeedbackRecord)
                .filter(
                    FeedbackRecord.target_type == "report_revision",
                    FeedbackRecord.target_id.in_([r.revision_id for r, _, _ in rows]),
                    FeedbackRecord.created_by == owner,
                )
                .order_by(FeedbackRecord.created_at.desc(), FeedbackRecord.feedback_id.desc())
                .all()
            )
            latest_feedback = {}
            for feedback in feedbacks:
                latest_feedback.setdefault(
                    feedback.target_id,
                    {
                        "feedback_id": feedback.feedback_id,
                        "verdict": feedback.verdict,
                        "notes": feedback.notes,
                        "created_at": as_utc(feedback.created_at),
                    },
                )
            dispatch = (
                session.query(
                    OutboxRecord.artifact_id, OutboxRecord.channel, func.max(OutboxRecord.sent_at)
                )
                .filter(OutboxRecord.artifact_id.in_(artifact_ids))
                .group_by(OutboxRecord.artifact_id, OutboxRecord.channel)
                .all()
            )
            local, external = {}, {}
            for artifact, channel, at in dispatch:
                if at is None:
                    continue
                target = local if channel == "local" else external
                target[artifact] = max(target.get(artifact, at), at)
            return ReportDeliveryPage(
                items=[
                    ReportDeliveryItem(
                        revision_id=r.revision_id,
                        study_id=r.study_id,
                        source_run_id=r.source_run_id,
                        title=s.title,
                        instrument=s.instrument,
                        strategy_version=r.strategy_version,
                        publication_status=r.publication_status,
                        created_at=as_utc(r.created_at),
                        local_written_at=as_utc(local.get(r.artifact_id)),
                        external_dispatch_at=as_utc(external.get(r.artifact_id)),
                        read_at=as_utc(receipt.read_at) if receipt else None,
                        latest_feedback=latest_feedback.get(r.revision_id),
                    )
                    for r, s, receipt in rows
                ],
                total=total,
                limit=limit,
                offset=offset,
            )

    @staticmethod
    def _fingerprint(
        owner: str, target: str, kind: str,
        payload: ReadReceiptCommand | StudyTrackingCommand,
    ) -> str:
        content = json.dumps(
            [owner, target, kind, payload.model_dump(mode="json")],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def _existing[ReceiptT: BaseModel](
        session: Session, request_id: str, digest: str, model: type[ReceiptT]
    ) -> ReceiptT | None:
        existing = session.get(ProductCommandRecord, request_id)
        if existing:
            if existing.payload_hash != digest:
                raise ValueError("product_request_reused")
            return model.model_validate_json(existing.result_json)
        return None

    def mark_read(
        self, revision_id: str, owner: str, command: ReadReceiptCommand
    ) -> ReadReceiptView:
        digest = self._fingerprint(owner, revision_id, "read_receipt", command)
        with self.database.session() as session:
            # Serialize commands before their idempotency read on the supported SQLite store.
            session.execute(text("BEGIN IMMEDIATE"))
            existing = self._existing(session, command.request_id, digest, ReadReceiptView)
            if existing:
                return existing
            if session.get(ReportRevisionRecord, revision_id) is None:
                raise KeyError("report_revision_not_found")
            now = self.clock()
            receipt = session.get(ProductReadReceiptRecord, (revision_id, owner))
            if receipt is None:
                receipt = ProductReadReceiptRecord(revision_id=revision_id, owner_id=owner)
                session.add(receipt)
            receipt.read_at = (receipt.read_at or now) if command.read else None
            receipt.updated_at = now
            result = ReadReceiptView(
                revision_id=revision_id,
                owner_id=owner,
                read_at=as_utc(receipt.read_at),
                updated_at=now,
            )
            self._audit(
                session, command.request_id, owner, revision_id, "read_receipt", digest, result, now
            )
            return result

    def get_read_command_receipt(
        self, revision_id: str, owner: str, command: ReadReceiptCommand
    ) -> ReadCommandReceipt | None:
        """Read the original command ACK without applying today's read state."""
        digest = self._fingerprint(owner, revision_id, "read_receipt", command)
        with self.database.session() as session:
            stored = session.get(
                ProductCommandRecord, command.request_id, populate_existing=True
            )
            if stored is None:
                return None
            if (
                stored.request_id != command.request_id
                or stored.owner_id != owner
                or stored.target_id != revision_id
                or stored.kind != "read_receipt"
                or stored.payload_hash != digest
            ):
                raise ValueError("product_request_reused")
            try:
                raw_result = json.loads(stored.result_json)
                result = ReadReceiptView.model_validate(raw_result)
                if (
                    result.revision_id != revision_id
                    or result.owner_id != owner
                    or (result.read_at is not None) != command.read
                ):
                    raise ValueError("product_read_command_receipt_invalid")
                return ReadCommandReceipt.model_validate(
                    {
                        "schema_version": "read-command-receipt.v1",
                        "request_id": command.request_id,
                        "revision_id": revision_id,
                        "owner_id": owner,
                        "command": command.model_dump(mode="json"),
                        "result": raw_result,
                        "payload_hash": stored.payload_hash,
                        "confirmation_basis": "stored_command_hash",
                    }
                )
            except ValueError as exc:
                raise ValueError("product_read_command_receipt_invalid") from exc

    def get_tracking_command_receipt(
        self, study_id: str, owner: str, request_id: str
    ) -> StudyTrackingReceipt | None:
        """Read a stored tracking ACK without using the current study state."""
        with self.database.session() as session:
            stored = session.get(ProductCommandRecord, request_id, populate_existing=True)
            if stored is None:
                return None
            if (
                stored.request_id != request_id
                or stored.owner_id != owner
                or stored.target_id != study_id
                or stored.kind != "study_tracking"
            ):
                raise ValueError("product_request_reused")
            try:
                result = StudyTrackingReceipt.model_validate_json(stored.result_json, strict=True)
                if (
                    result.request_id != request_id
                    or result.study_id != study_id
                    or result.recorded_at != as_utc(stored.created_at)
                ):
                    raise ValueError("study_tracking_command_receipt_invalid")
                command = StudyTrackingCommand.model_validate(
                    {
                        "request_id": result.request_id,
                        "expected_status": result.previous_status,
                        "status": result.status,
                    },
                    strict=True,
                )
            except ValueError as exc:
                raise ValueError("study_tracking_command_receipt_invalid") from exc
            digest = self._fingerprint(owner, study_id, "study_tracking", command)
            if stored.payload_hash != digest:
                raise ValueError("product_request_reused")
            return result

    def track(
        self, study_id: str, owner: str, command: StudyTrackingCommand
    ) -> StudyTrackingReceipt:
        digest = self._fingerprint(owner, study_id, "study_tracking", command)
        with self.database.session() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            existing = self._existing(session, command.request_id, digest, StudyTrackingReceipt)
            if existing:
                return existing
            study = session.get(StudyRecord, study_id)
            if study is None:
                raise KeyError("study_not_found")
            if study.status != command.expected_status:
                raise ValueError("study_status_changed")
            if command.status == "active":
                require_research_scope(study)
            now, previous = self.clock(), study.status
            study.status, study.updated_at = command.status, now
            cancelled, retired = 0, 0
            if command.status == "paused":
                # Running work finishes normally. Admission and pause use the same write lock.
                cancel_update = session.execute(
                    update(RunRecord)
                    .where(
                        RunRecord.study_id == study_id,
                        RunRecord.strategy_version == "research.v1",
                        RunRecord.admission_origin != "manual",
                        RunRecord.status == "admitted",
                    )
                    .values(
                        status="cancelled",
                        error_code="study_tracking_paused",
                        updated_at=now,
                        finished_at=now,
                    )
                )
                if not isinstance(cancel_update, CursorResult):
                    raise TypeError("study_tracking_update_result_invalid")
                cancelled = cancel_update.rowcount
                parents = select(RunRecord.run_id).where(RunRecord.study_id == study_id)
                plans = (
                    session.query(ReviewScheduleRecord)
                    .filter(
                        ReviewScheduleRecord.parent_run_id.in_(parents),
                        ReviewScheduleRecord.status.in_(["planned", "admitted"]),
                    )
                    .all()
                )
                for plan in plans:
                    child = session.get(RunRecord, plan.child_run_id) if plan.child_run_id else None
                    if child and child.status == "running":
                        continue
                    plan.status, plan.failure_code, plan.updated_at = (
                        "skipped",
                        "study_tracking_paused",
                        now,
                    )
                    retired += 1
            result = StudyTrackingReceipt(
                request_id=command.request_id,
                study_id=study_id,
                previous_status=previous,
                status=study.status,
                cancelled_runs=cancelled,
                retired_plans=retired,
                recorded_at=now,
            )
            self._audit(
                session, command.request_id, owner, study_id, "study_tracking", digest, result, now
            )
            return result

    @staticmethod
    def _audit(
        session: Session, request_id: str, owner: str, target: str, kind: str,
        digest: str, result: ReadReceiptView | StudyTrackingReceipt, now: datetime,
    ) -> None:
        session.add(
            ProductCommandRecord(
                request_id=request_id,
                owner_id=owner,
                target_id=target,
                kind=kind,
                payload_hash=digest,
                result_json=result.model_dump_json(),
                created_at=now,
            )
        )
