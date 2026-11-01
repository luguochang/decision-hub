from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update

from packages.contracts_py.decision_hub_contracts import DataJobCreate, DataJobView
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    DataJobRecord,
    as_utc,
    utcnow,
)

DATA_JOB_LEASE_SECONDS = 600


class DataJobService:
    """Persist explicit owner data work without creating Research Runs."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.database = database
        self.clock = clock

    def create(self, request: DataJobCreate) -> tuple[DataJobView, bool]:
        start = request.window_start_at.astimezone(UTC)
        end = request.window_end_at.astimezone(UTC)
        if start >= end:
            raise ValueError("data_job_window_invalid")
        if end - start > timedelta(days=370):
            raise ValueError("data_job_window_too_large")
        dataset_ids = list(dict.fromkeys(request.dataset_ids))
        if len(dataset_ids) != len(request.dataset_ids):
            raise ValueError("data_job_dataset_duplicate")
        request_hash = _request_hash(request, dataset_ids)
        now = self.clock()
        with self.database.session() as session:
            existing = (
                session.query(DataJobRecord)
                .filter_by(idempotency_key=request.idempotency_key)
                .one_or_none()
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ValueError("data_job_idempotency_conflict")
                return _view(existing), False
            row = DataJobRecord(
                job_id=f"datajob_{uuid.uuid4().hex}",
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                requested_by=request.requested_by,
                dataset_ids_json=_json(dataset_ids),
                window_start_at=start,
                window_end_at=end,
                status="queued",
                attempt=0,
                partition_refs_json="[]",
                coverage_manifest_ref=None,
                errors_json="[]",
                created_at=now,
                updated_at=now,
                finished_at=None,
            )
            session.add(row)
            session.flush()
            return _view(row), True

    def start(self, job_id: str, *, queued_only: bool = False) -> DataJobView:
        with self.database.session() as session:
            row = session.get(DataJobRecord, job_id)
            if row is None:
                raise KeyError(job_id)
            now = self.clock()
            claimable = (
                DataJobRecord.status == "queued"
                if queued_only
                else or_(
                    DataJobRecord.status != "running",
                    DataJobRecord.updated_at <= now - timedelta(seconds=DATA_JOB_LEASE_SECONDS),
                )
            )
            changed = session.execute(
                update(DataJobRecord)
                .where(
                    DataJobRecord.job_id == job_id,
                    claimable,
                )
                .values(
                    status="running",
                    attempt=DataJobRecord.attempt + 1,
                    updated_at=now,
                    finished_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(changed, "rowcount", 0)) != 1:
                raise ValueError(
                    "data_job_not_queued" if queued_only else "data_job_already_running"
                )
            session.refresh(row)
            return _view(row)

    def recover_expired(self) -> int:
        now = self.clock()
        cutoff = now - timedelta(seconds=DATA_JOB_LEASE_SECONDS)
        recovered = 0
        with self.database.session() as session:
            rows = (
                session.query(DataJobRecord)
                .filter(
                    DataJobRecord.status == "running",
                    DataJobRecord.updated_at <= cutoff,
                )
                .all()
            )
            for row in rows:
                errors = list(dict.fromkeys([*json.loads(row.errors_json), "data_job_interrupted"]))
                changed = session.execute(
                    update(DataJobRecord)
                    .where(
                        DataJobRecord.job_id == row.job_id,
                        DataJobRecord.attempt == row.attempt,
                        DataJobRecord.status == "running",
                        DataJobRecord.updated_at <= cutoff,
                    )
                    .values(
                        status="failed", errors_json=_json(errors), updated_at=now, finished_at=now
                    )
                    .execution_options(synchronize_session=False)
                )
                recovered += int(getattr(changed, "rowcount", 0))
        return recovered

    def finish(
        self,
        job_id: str,
        *,
        status: str,
        partition_refs: Iterable[str],
        coverage_manifest_ref: str | None,
        errors: Iterable[str],
        expected_attempt: int | None = None,
    ) -> DataJobView:
        if status not in {"partial", "failed", "completed"}:
            raise ValueError("data_job_terminal_status_invalid")
        refs = list(dict.fromkeys(partition_refs))
        messages = list(dict.fromkeys(errors))
        now = self.clock()
        with self.database.session() as session:
            row = session.get(DataJobRecord, job_id)
            if row is None:
                raise KeyError(job_id)
            if row.status != "running":
                raise ValueError("data_job_not_running")
            conditions = [DataJobRecord.job_id == job_id, DataJobRecord.status == "running"]
            if expected_attempt is not None:
                conditions.append(DataJobRecord.attempt == expected_attempt)
            changed = session.execute(
                update(DataJobRecord)
                .where(*conditions)
                .values(
                    status=status,
                    partition_refs_json=_json(refs),
                    coverage_manifest_ref=coverage_manifest_ref,
                    errors_json=_json(messages),
                    updated_at=now,
                    finished_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(changed, "rowcount", 0)) != 1:
                raise ValueError("data_job_lease_lost")
            session.refresh(row)
            return _view(row)

    def get(self, job_id: str) -> DataJobView | None:
        with self.database.session() as session:
            row = session.get(DataJobRecord, job_id)
            return _view(row) if row is not None else None

    def list(self, *, limit: int = 100) -> list[DataJobView]:
        return self.list_page(limit=limit)[0]

    def oldest_queued(self, *, limit: int = 1) -> list[DataJobView]:
        if limit < 1 or limit > 500:
            raise ValueError("data_job_page_invalid")
        with self.database.session() as session:
            rows = (
                session.query(DataJobRecord)
                .filter(DataJobRecord.status == "queued")
                .order_by(DataJobRecord.created_at, DataJobRecord.job_id)
                .limit(limit)
                .all()
            )
            return [_view(row) for row in rows]

    def list_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        dataset: str | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[DataJobView], int]:
        if limit < 1 or limit > 500 or offset < 0:
            raise ValueError("data_job_page_invalid")
        if status is not None and status not in {
            "queued",
            "running",
            "partial",
            "failed",
            "completed",
        }:
            raise ValueError("data_job_status_invalid")
        if created_before is not None and created_before.utcoffset() is None:
            raise ValueError("data_job_cutoff_timezone_required")
        with self.database.session() as session:
            query = session.query(DataJobRecord)
            if status is not None:
                query = query.filter(DataJobRecord.status == status)
            if dataset:
                # Match the complete JSON string, not a prefix or a LIKE wildcard.
                query = query.filter(
                    DataJobRecord.dataset_ids_json.contains(
                        json.dumps(dataset, ensure_ascii=False), autoescape=True
                    )
                )
            if created_before is not None:
                query = query.filter(DataJobRecord.created_at <= created_before.astimezone(UTC))
            total = query.count()
            rows = (
                query.order_by(DataJobRecord.created_at.desc(), DataJobRecord.job_id)
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [_view(row) for row in rows], total


def _request_hash(request: DataJobCreate, dataset_ids: Sequence[str]) -> str:
    payload = {
        "dataset_ids": dataset_ids,
        "requested_by": request.requested_by,
        "window_start_at": request.window_start_at.astimezone(UTC).isoformat(),
        "window_end_at": request.window_end_at.astimezone(UTC).isoformat(),
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _view(row: DataJobRecord) -> DataJobView:
    return DataJobView.model_validate(
        {
            "schema_version": "data-job.v1",
            "job_id": row.job_id,
            "idempotency_key": row.idempotency_key,
            "request_hash": row.request_hash,
            "requested_by": row.requested_by,
            "dataset_ids": json.loads(row.dataset_ids_json),
            "window_start_at": as_utc(row.window_start_at),
            "window_end_at": as_utc(row.window_end_at),
            "status": row.status,
            "attempt": row.attempt,
            "partition_refs": json.loads(row.partition_refs_json),
            "coverage_manifest_ref": row.coverage_manifest_ref,
            "errors": json.loads(row.errors_json),
            "created_at": as_utc(row.created_at),
            "updated_at": as_utc(row.updated_at),
            "finished_at": as_utc(row.finished_at),
        }
    )
