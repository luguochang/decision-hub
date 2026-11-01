"""Explicit zero-HTTP reconciliation; never renew or reset the original epoch."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from packages.provider_adapters.market.history_missing_transport_v2 import (
    CAPTURE_PRODUCER_V2,
    ORIGINAL_DECODER_V1,
    CapturedHistoryPageV2,
)

from ..persistence.db import RawCaptureRecord, as_utc
from ..persistence.history_dataset_v2_records import HistoryMissingV2AttemptRecord as Attempt
from ..persistence.history_missing_resume_v2_records import HistoryMissingResumeV2Record as Audit
from ..persistence.risk_records import MarketCandleRecord
from ..ports.market_path import canonical_hash, spot_scope
from .history_missing_acquisition_v2 import (
    CAPTURE_FIELDS,
    TRANSPORT_FAILURES,
    HistoryMissingAcquisitionV2,
    ReservedHistoryMissingV2,
    _dt,
    _json,
)
from .history_observed_dataset import HistoryObservedArchive, require_snapshot, snapshot_identity

RESUME_FILES = (
    "packages/kernel/decision_hub_kernel/application/history_missing_resume_v2.py",
    "packages/kernel/decision_hub_kernel/persistence/history_missing_resume_v2_records.py",
    "migrations/versions/0085_history_missing_resume_v2.py",
)
LOADED_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def resume_id(owner_claim, request_id):
    if (
        not owner_claim.strip()
        or not request_id.strip()
        or max(len(owner_claim), len(request_id)) > 128
    ):
        raise ValueError("history_v2_resume_identity_invalid")
    return "hresume_" + canonical_hash([owner_claim, request_id])[:48]


def _row(record):
    return {column.name: getattr(record, column.name) for column in record.__table__.columns}


class HistoryMissingResumeV2:
    def __init__(self, acquisition: HistoryMissingAcquisitionV2):
        if type(acquisition) is not HistoryMissingAcquisitionV2:
            raise ValueError("history_v2_resume_actual_acquisition_required")
        self.acquisition = acquisition
        self.commands, self.originals = acquisition.commands, acquisition.originals
        self.archive = HistoryObservedArchive(
            self.originals.data_directory / "history-missing-resume-v2"
        )

    def _sources(self):
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != LOADED_SHA:
            raise ValueError("history_v2_resume_loaded_source_changed")
        return (
            *self.acquisition._sources(),
            *(
                (name, hashlib.sha256((self.originals.source_root / name).read_bytes()).hexdigest())
                for name in RESUME_FILES
            ),
        )

    def _state(self, session, identity):
        value, epoch = self.acquisition._epoch(
            session, identity["epoch_id"], identity["owner_claim"]
        )
        if (
            value.plan_id != identity["plan_id"]
            or value.payload_hash != identity["expected_epoch_hash"]
            or value.request_id != identity["expected_epoch_request_id"]
        ):
            raise ValueError("history_v2_resume_original_identity_conflict")
        jobs = self.acquisition._jobs(session, value, epoch)
        attempts = list(
            session.scalars(
                select(Attempt)
                .where(Attempt.epoch_id == value.original_id)
                .order_by(Attempt.job_id, Attempt.generation)
            )
        )
        if not 0 <= epoch.attempts <= value.provenance["budget"] or any(
            sum(job.attempts for job in jobs if job.scope_id == scope)
            > value.provenance["per_scope_budget"]
            for scope in value.scope_ids
        ):
            raise ValueError("history_v2_resume_budget_accounting_invalid")
        snapshot = canonical_hash(
            json.loads(
                _json(
                    [
                        _row(epoch),
                        [_row(job) for job in jobs],
                        [_row(attempt) for attempt in attempts],
                    ]
                )
            )
        )
        return value, epoch, jobs, attempts, snapshot

    def _capture(self, session, attempt, value, job):
        if attempt.capture_id is None or attempt.result_json is None:
            raise ValueError("history_v2_resume_unknown_send_without_original")
        result = json.loads(attempt.result_json)
        witness = result["witness"]
        row = session.get(RawCaptureRecord, attempt.capture_id)
        if (
            canonical_hash(result) != attempt.result_hash
            or row is None
            or attempt.fixture_mode is not self.acquisition.fixture_mode
            or result["attempt_id"] != attempt.attempt_id
            or tuple(map(tuple, result["source_files"])) != value.source_files
            or witness["origin"] != "missing_v2"
            or witness["epoch_id"] != value.original_id
            or witness["attempt_id"] != attempt.attempt_id
            or witness["job_id"] != job.job_id
            or witness["scope_id"] != job.scope_id
            or _dt(witness["start_at"]) != as_utc(job.start_at)
            or _dt(witness["end_at"]) != as_utc(job.end_at)
            or witness["page"]["cursor"] != attempt.cursor
            or job.cursor != attempt.cursor
            or job.generation != attempt.generation
            or row.context_id != attempt.attempt_id
            or row.adapter_version != CAPTURE_PRODUCER_V2
            or tuple(map(tuple, witness["producer_sources"])) != value.source_files
        ):
            raise ValueError("history_v2_resume_capture_or_witness_identity_invalid")
        capture = {
            key: as_utc(getattr(row, key))
            if key in {"started_at", "completed_at", "recorded_at"}
            else getattr(row, key)
            for key in CAPTURE_FIELDS
        }
        for key in CAPTURE_FIELDS:
            actual = (
                as_utc(capture[key])
                if key in {"started_at", "completed_at", "recorded_at"}
                else capture[key]
            )
            claimed = (
                _dt(result["capture"][key])
                if key in {"started_at", "completed_at", "recorded_at"}
                else result["capture"][key]
            )
            if actual != claimed:
                raise ValueError("history_v2_resume_capture_metadata_changed")
        if (
            not as_utc(attempt.reserved_at)
            <= as_utc(row.started_at)
            <= as_utc(row.completed_at)
            <= as_utc(row.recorded_at)
            <= self.acquisition._now()
        ):
            raise ValueError("history_v2_resume_capture_clock_invalid")
        return result, capture

    def _reconcile(self, session, attempt, value, job):
        result, capture = self._capture(session, attempt, value, job)
        if (
            result["error_code"] is not None
            or result["cancelled"]
            or capture["error_code"] is not None
            or capture["status_code"] != 200
            or capture["body_ref"] is None
            or as_utc(capture["recorded_at"]) > _dt(value.provenance["deadline_at"])
        ):
            raise ValueError("history_v2_resume_unknown_send_without_success_original")
        body = self.originals.raw.read(capture["body_ref"])
        scope = next(
            spot_scope(asset)
            for asset in ("BTC-USDT", "ETH-USDT")
            if spot_scope(asset).scope_id == job.scope_id
        )
        page = asyncio.run(
            self.acquisition.transport._decode(
                body,
                as_utc(capture["completed_at"]),
                capture["body_ref"],
                scope,
                attempt.cursor,
                as_utc(job.start_at),
                as_utc(job.end_at),
            )
        )
        capture.update(
            original_decoder_version=ORIGINAL_DECODER_V1, fixture_mode=attempt.fixture_mode
        )
        produced = CapturedHistoryPageV2(
            capture,
            body,
            page,
            tuple(tuple(item) for item in result["rows"]),
            result["error_code"],
            result["cancelled"],
            tuple(map(tuple, result["capture_source_files"])),
        )
        if produced.source_files != self.acquisition.transport.source_files:
            raise ValueError("history_v2_resume_capture_producer_source_changed")
        reserved = ReservedHistoryMissingV2(
            attempt.attempt_id,
            value.original_id,
            job.job_id,
            value.owner_claim,
            attempt.generation,
            job.lease_owner or "expired_unknown",
            as_utc(attempt.reserved_at),
            as_utc(job.lease_expires_at)
            if job.lease_expires_at is not None
            else as_utc(attempt.reserved_at) + timedelta(seconds=60),
            job.scope_id,
            as_utc(job.start_at),
            as_utc(job.end_at),
            attempt.cursor,
            value.source_files,
        )
        verified_body, bars, rows = self.acquisition._verified_page(reserved, produced)
        if rows != produced.semantic_rows or result["next_cursor"] != page.next_cursor:
            raise ValueError("history_v2_resume_page_semantics_changed")
        digest = capture["body_hash"]
        raw_path = self.originals.raw.root / digest[:2] / digest
        return (
            attempt.attempt_id,
            page.next_cursor,
            bars,
            str(raw_path),
            hashlib.sha256(verified_body).hexdigest(),
        )

    def _assess(self, session, identity):
        value, epoch, jobs, attempts, snapshot = self._state(session, identity)
        now, reconciled, reason = self.acquisition._now(), [], None
        if epoch.state != "paused":
            reason = "history_v2_resume_epoch_not_paused"
        elif now >= _dt(value.provenance["deadline_at"]):
            reason = "history_v2_resume_original_deadline_elapsed"
        elif any(
            job.state == "running"
            and job.lease_expires_at is not None
            and as_utc(job.lease_expires_at) > now
            for job in jobs
        ):
            reason = "history_v2_resume_live_lease_forbids_reconcile"
        else:
            by_job = {job.job_id: job for job in jobs}
            unknown = [
                item
                for item in attempts
                if item.state
                in {"reserved", "send_unknown", "captured_pending", "admission_unknown"}
            ]
            try:
                for attempt in unknown:
                    reconciled.append(
                        self._reconcile(session, attempt, value, by_job[attempt.job_id])
                    )
                if not unknown and epoch.error_code not in TRANSPORT_FAILURES:
                    reason = "history_v2_resume_nontransport_pause_requires_review"
                if not unknown and reason is None:
                    for job in jobs:
                        latest = next(
                            (item for item in reversed(attempts) if item.job_id == job.job_id), None
                        )
                        if (
                            latest is not None
                            and latest.state == "failed"
                            and job.error_code in TRANSPORT_FAILURES
                        ):
                            result, _capture = self._capture(session, latest, value, job)
                            if result["error_code"] != job.error_code:
                                raise ValueError("history_v2_resume_failed_attempt_result_changed")
            except (
                ValueError,
                OSError,
                KeyError,
                TypeError,
                ArithmeticError,
                RecursionError,
            ) as error:
                reason = (
                    str(error)
                    if str(error).startswith("history_v2_")
                    else "history_v2_resume_original_unconfirmed"
                )
                reconciled = []
        return value, epoch, jobs, attempts, snapshot, reconciled, reason

    def read(self, *, owner_claim, request_id):
        with self.commands._read_session() as session:
            return self._read(session, resume_id(owner_claim, request_id), owner_claim)

    def _read(self, session, key, owner):
        row = session.get(Audit, key)
        if row is None or row.owner_claim != owner:
            raise ValueError("history_v2_resume_audit_missing_or_owner_conflict")
        body = self.archive.read(row.original_ref, row.original_hash)
        result = json.loads(body)
        source = json.loads(row.source_json)
        if (
            row.payload_json.encode() != body
            or canonical_hash(result) != row.payload_hash
            or hashlib.sha256(row.source_json.encode()).hexdigest() != row.source_hash
            or result["resume_id"] != row.resume_id
            or result["identity"]["owner_claim"] != owner
            or result["identity"]["request_id"] != row.request_id
            or result["identity"]["epoch_id"] != row.epoch_id
            or result["identity"]["plan_id"] != row.plan_id
            or _dt(result["recorded_at"]) != as_utc(row.recorded_at)
            or tuple(map(tuple, result["source_files"])) != self._sources()
            or tuple((item["path"], item["sha256"]) for item in source["files"]) != self._sources()
            or any(
                hashlib.sha256(base64.b64decode(item["bytes_base64"], validate=True)).hexdigest()
                != item["sha256"]
                for item in source["files"]
            )
        ):
            raise ValueError("history_v2_resume_audit_or_source_changed")
        return result

    def resume(
        self,
        *,
        owner_claim,
        request_id,
        epoch_id,
        plan_id,
        expected_epoch_hash,
        expected_epoch_request_id,
    ):
        identity = dict(
            owner_claim=owner_claim,
            request_id=request_id,
            epoch_id=epoch_id,
            plan_id=plan_id,
            expected_epoch_hash=expected_epoch_hash,
            expected_epoch_request_id=expected_epoch_request_id,
        )
        key = resume_id(owner_claim, request_id)
        with self.commands._read_session() as session:
            if session.get(Audit, key) is not None:
                first = self._read(session, key, owner_claim)
                if first["identity"] != identity:
                    raise ValueError("history_v2_resume_request_conflict")
                return first
            boundary = snapshot_identity(session)
            value, epoch, jobs, attempts, snapshot, reconciled, reason = self._assess(
                session, identity
            )
            sources = self._sources()
            source_body = self.originals._source_body(sources)
            before = {
                "epoch_state": epoch.state,
                "attempts": epoch.attempts,
                "transport_errors": epoch.consecutive_transport_errors,
                "budget": value.provenance["budget"],
                "per_scope_budget": value.provenance["per_scope_budget"],
                "deadline_at": value.provenance["deadline_at"],
                "jobs": [
                    {
                        key: _row(job)[key]
                        for key in (
                            "job_id",
                            "cursor",
                            "attempts",
                            "page_count",
                            "retry_used",
                            "generation",
                            "state",
                        )
                    }
                    for job in jobs
                ],
            }
            require_snapshot(session, boundary)
        with self.commands._write_session() as session:
            if session.get(Audit, key) is not None:
                first = self._read(session, key, owner_claim)
                if first["identity"] != identity:
                    raise ValueError("history_v2_resume_request_conflict")
                return first
            fresh = self._assess(session, identity)
            value, epoch, jobs, attempts, fresh_snapshot, fresh_reconciled, fresh_reason = fresh
            if fresh_snapshot != snapshot or fresh_reason != reason or self._sources() != sources:
                raise ValueError("history_v2_resume_changed_before_writer")
            recovered = []
            if reason is None:
                now = self.acquisition._now()
                for attempt_id, cursor, bars, path, digest in fresh_reconciled:
                    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                        raise ValueError("history_v2_resume_original_changed")
                    attempt = next(item for item in attempts if item.attempt_id == attempt_id)
                    job = next(item for item in jobs if item.job_id == attempt.job_id)
                    for bar in bars:
                        revision = canonical_hash(bar.semantic_payload())
                        candle_id = "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                        if session.get(MarketCandleRecord, candle_id) is None:
                            accepted = bar.model_copy(update={"accepted_at": now})
                            session.add(
                                MarketCandleRecord(
                                    candle_id=candle_id,
                                    scope_id=bar.scope_id,
                                    venue=bar.venue,
                                    instrument=bar.instrument,
                                    price_type=bar.price_type,
                                    interval_seconds=60,
                                    open_at=bar.open_at,
                                    close_at=bar.close_at,
                                    received_at=bar.received_at,
                                    known_at=max(now, bar.received_at),
                                    revision_hash=revision,
                                    candle_json=accepted.model_dump_json(),
                                    capture_ref=bar.capture_ref,
                                    capture_hash=bar.capture_hash,
                                )
                            )
                    attempt.state = "captured"
                    job.page_count += 1
                    job.cursor, job.retry_used = cursor, 0
                    job.state = "completed" if cursor is None else "pending"
                    job.lease_owner = job.lease_expires_at = None
                    job.error_code = None
                    recovered.append(attempt_id)
                    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                        raise ValueError("history_v2_resume_original_changed_at_exit")
                eligible = [
                    job
                    for job in jobs
                    if job.state in {"pending", "retry"}
                    and job.page_count < job.max_pages
                    and job.attempts < 2 * job.max_pages
                    and sum(item.attempts for item in jobs if item.scope_id == job.scope_id)
                    < value.provenance["per_scope_budget"]
                ]
                epoch.state = (
                    "active"
                    if eligible and epoch.attempts < value.provenance["budget"]
                    else "completed"
                    if all(job.state == "completed" for job in jobs)
                    else "partial"
                )
                epoch.consecutive_transport_errors, epoch.error_code = 0, None
            result = {
                "schema_version": "history-missing-resume-audit.v2",
                "resume_id": key,
                "identity": identity,
                "recorded_at": self.acquisition._now().isoformat(),
                "outcome": "blocked" if reason else "reconciled" if recovered else "resumed",
                "reason": reason,
                "before": before,
                "after_epoch_state": epoch.state,
                "after_attempts": epoch.attempts,
                "reconciled_attempts": recovered,
                "source_files": sources,
                "HTTP_attempts": 0,
                "automatic_retry": False,
                "financial_qualified": False,
                "owner_authenticated": False,
            }
            ref, digest = self.archive.put(result)
            if self._sources() != sources:
                raise ValueError("history_v2_resume_source_changed_at_exit")
            payload = self.archive.read(ref, digest).decode()
            session.add(
                Audit(
                    resume_id=key,
                    owner_claim=owner_claim,
                    request_id=request_id,
                    epoch_id=epoch_id,
                    plan_id=plan_id,
                    payload_json=payload,
                    payload_hash=canonical_hash(result),
                    original_ref=ref,
                    original_hash=digest,
                    source_json=source_body.decode(),
                    source_hash=hashlib.sha256(source_body).hexdigest(),
                    recorded_at=_dt(result["recorded_at"]),
                )
            )
        return self.read(owner_claim=owner_claim, request_id=request_id)
