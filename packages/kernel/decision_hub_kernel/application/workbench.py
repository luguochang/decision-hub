from __future__ import annotations

import hashlib
import json

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    FeedbackCreate,
    FeedbackView,
    ResearchMemoCreate,
    ResearchMemoView,
    WorkbenchOverviewView,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    CapabilityRecord,
    Database,
    ExperienceRecord,
    ExperimentRecord,
    FailurePatternRecord,
    FeedbackRecord,
    ResearchMemoRecord,
    RunRecord,
    SnapshotRecord,
    as_utc,
    utcnow,
)


def _stable_id(prefix: str, request_id: str) -> str:
    return f"{prefix}_{hashlib.sha256(request_id.encode()).hexdigest()[:24]}"


class WorkbenchAssetService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_memo(self, request: ResearchMemoCreate) -> ResearchMemoView:
        with self.database.session() as session:
            existing = (
                session.query(ResearchMemoRecord)
                .filter_by(request_id=request.request_id)
                .one_or_none()
            )
            if existing:
                expected = self._memo_content(request)
                if existing.content_json != expected or existing.created_by != request.created_by:
                    raise ValueError("workbench_request_reused")
                return self._memo_view(existing)
            if request.run_id and session.get(RunRecord, request.run_id) is None:
                raise ValueError("workbench_run_not_found")
            if request.snapshot_id and session.get(SnapshotRecord, request.snapshot_id) is None:
                raise ValueError("workbench_snapshot_not_found")
            if request.run_id and request.snapshot_id:
                run = session.get(RunRecord, request.run_id)
                if run is not None and run.snapshot_id not in {None, request.snapshot_id}:
                    raise ValueError("workbench_snapshot_mismatch")
            snapshot_id = request.snapshot_id
            if snapshot_id is None and request.run_id:
                run = session.get(RunRecord, request.run_id)
                snapshot_id = run.snapshot_id if run is not None else None
            if snapshot_id:
                snapshot = session.get(SnapshotRecord, snapshot_id)
                assert snapshot is not None
                evidence = json.loads(snapshot.evidence_json)
                evidence_ids = {
                    str(item.get("evidence_id"))
                    for item in evidence
                    if isinstance(item, dict) and item.get("evidence_id")
                }
                if not set(request.evidence_refs).issubset(evidence_ids):
                    raise ValueError("workbench_evidence_not_found")
            if not request.run_id and not request.snapshot_id:
                raise ValueError("workbench_reference_required")
            now = utcnow()
            record = ResearchMemoRecord(
                memo_id=_stable_id("memo", request.request_id),
                request_id=request.request_id,
                run_id=request.run_id,
                snapshot_id=request.snapshot_id,
                domain_pack_ref=request.domain_pack_ref,
                created_by=request.created_by,
                status="submitted",
                content_json=self._memo_content(request),
                created_at=now,
            )
            session.add(record)
            session.flush()
            return self._memo_view(record)

    def list_memos(self, limit: int = 100) -> list[ResearchMemoView]:
        with self.database.session() as session:
            rows = (
                session.query(ResearchMemoRecord)
                .order_by(ResearchMemoRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._memo_view(row) for row in rows]

    def create_feedback(self, request: FeedbackCreate) -> FeedbackView:
        with self.database.session() as session:
            existing = (
                session.query(FeedbackRecord).filter_by(request_id=request.request_id).one_or_none()
            )
            if existing:
                if any(
                    getattr(existing, field) != getattr(request, field)
                    for field in ("target_type", "target_id", "created_by", "verdict", "notes")
                ):
                    raise ValueError("workbench_request_reused")
                return self._feedback_view(existing)
            target = {
                "run": RunRecord,
                "memo": ResearchMemoRecord,
                "experiment": ExperimentRecord,
                "candidate": CandidateVersionRecord,
                "experience": ExperienceRecord,
                "failure_pattern": FailurePatternRecord,
            }.get(request.target_type)
            if target is None or session.get(target, request.target_id) is None:
                raise ValueError("workbench_target_not_found")
            record = FeedbackRecord(
                feedback_id=_stable_id("feedback", request.request_id),
                created_at=utcnow(),
                **request.model_dump(),
            )
            session.add(record)
            session.flush()
            return self._feedback_view(record)

    def register_capability(self, manifest: CapabilityManifest) -> CapabilityManifest:
        if manifest.status not in {"discovered", "audited"}:
            raise PermissionError("capability_owner_enable_required")
        with self.database.session() as session:
            record = session.get(CapabilityRecord, manifest.capability_id)
            if record is None:
                record = CapabilityRecord(capability_id=manifest.capability_id)
                session.add(record)
            record.version = manifest.version
            record.status = manifest.status
            record.manifest_json = manifest.model_dump_json()
            record.updated_at = utcnow()
            session.flush()
        return manifest

    def set_capability_status(self, capability_id: str, status: str) -> CapabilityManifest:
        """Owner-only lifecycle transition; execution remains adapter-owned."""
        if status not in {"audited", "enabled", "shadow", "rejected", "retired"}:
            raise ValueError("capability_status_invalid")
        with self.database.session() as session:
            record = session.get(CapabilityRecord, capability_id)
            if record is None:
                raise ValueError("capability_not_found")
            manifest = CapabilityManifest.model_validate_json(record.manifest_json)
            updated = manifest.model_copy(update={"status": status})
            record.status = status
            record.manifest_json = updated.model_dump_json()
            record.updated_at = utcnow()
            return updated

    def list_capabilities(self) -> list[CapabilityManifest]:
        with self.database.session() as session:
            rows = session.query(CapabilityRecord).order_by(CapabilityRecord.capability_id).all()
            return [CapabilityManifest.model_validate_json(row.manifest_json) for row in rows]

    def can_execute(self, capability_id: str) -> bool:
        with self.database.session() as session:
            record = session.get(CapabilityRecord, capability_id)
            return record is not None and record.status in {"enabled", "shadow"}

    def require_enabled(self, capability_id: str) -> CapabilityManifest:
        with self.database.session() as session:
            record = session.get(CapabilityRecord, capability_id)
            if record is None or record.status not in {"enabled", "shadow"}:
                raise PermissionError("capability_not_enabled")
            return CapabilityManifest.model_validate_json(record.manifest_json)

    def list_feedback(self, limit: int = 100) -> list[FeedbackView]:
        with self.database.session() as session:
            rows = (
                session.query(FeedbackRecord)
                .order_by(FeedbackRecord.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._feedback_view(row) for row in rows]

    def overview(self, *, limit: int = 100) -> WorkbenchOverviewView:
        """Return the bounded, typed workbench read model without raw session state."""
        bounded_limit = max(1, min(limit, 500))
        return WorkbenchOverviewView(
            memos=self.list_memos(limit=bounded_limit),
            feedback=self.list_feedback(limit=bounded_limit),
            capabilities=self.list_capabilities(),
        )

    @staticmethod
    def _memo_content(request: ResearchMemoCreate) -> str:
        return json.dumps(
            request.model_dump(
                include={
                    "claims",
                    "evidence_refs",
                    "counterpoints",
                    "uncertainties",
                    "follow_up_questions",
                }
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _memo_view(record: ResearchMemoRecord) -> ResearchMemoView:
        created_at = as_utc(record.created_at)
        assert created_at is not None
        return ResearchMemoView.model_validate(
            {
                "memo_id": record.memo_id,
                "request_id": record.request_id,
                "run_id": record.run_id,
                "snapshot_id": record.snapshot_id,
                "domain_pack_ref": record.domain_pack_ref,
                "created_by": record.created_by,
                "status": record.status,
                "created_at": created_at,
                **json.loads(record.content_json),
            }
        )

    @staticmethod
    def _feedback_view(record: FeedbackRecord) -> FeedbackView:
        created_at = as_utc(record.created_at)
        assert created_at is not None
        return FeedbackView.model_validate(
            {
                "feedback_id": record.feedback_id,
                "request_id": record.request_id,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "created_by": record.created_by,
                "verdict": record.verdict,
                "notes": record.notes,
                "created_at": created_at,
            }
        )
