"""Explicit bounded V2 gap attempts; unknown sends pause and never auto replay."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import case, func, select

from packages.provider_adapters.market.history_missing_transport_v2 import (
    CAPTURE_PRODUCER_V2,
    ORIGINAL_DECODER_V1,
    CapturedHistoryPageV2,
    HistoryMissingTransportV2,
)

from ..persistence.db import RawCaptureRecord, as_utc
from ..persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord as Original,
)
from ..persistence.history_dataset_v2_records import (
    HistoryMissingV2AttemptRecord as Attempt,
)
from ..persistence.history_dataset_v2_records import (
    HistoryMissingV2EpochRecord as Epoch,
)
from ..persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from ..persistence.risk_records import MarketCandleRecord
from ..ports.history_dataset_v2 import history_v2_id
from ..ports.market_path import MarketCandle, aware_utc, canonical_hash, spot_scope
from .history_observed_commands import HistoryObservedCommandService

SOURCE_IDENTITY = (
    "packages/kernel/decision_hub_kernel/application/history_missing_acquisition_v2.py",
    hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
)
TRANSPORT_FAILURES = {
    "history_missing_transport_timeout",
    "history_missing_transport_unavailable",
    "history_missing_transport_cancelled",
}
CAPTURE_FIELDS = tuple(column.name for column in RawCaptureRecord.__table__.columns)


def _dt(value):
    return (
        aware_utc(value)
        if isinstance(value, datetime)
        else aware_utc(datetime.fromisoformat(value))
    )


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=lambda item: item.isoformat()
    )


@dataclass(frozen=True)
class ReservedHistoryMissingV2:
    attempt_id: str
    epoch_id: str
    job_id: str
    owner_claim: str
    generation: int
    lease_owner: str
    reserved_at: datetime
    lease_expires_at: datetime
    scope_id: str
    start_at: datetime
    end_at: datetime
    cursor: str
    source_files: tuple[tuple[str, str], ...]


class HistoryMissingAcquisitionV2:
    def __init__(self, commands, originals_service, transport, fixture_mode=False, *, clock=None):
        if (
            type(commands) is not HistoryObservedCommandService
            or type(transport) is not HistoryMissingTransportV2
        ):
            raise ValueError("history_v2_actual_command_and_transport_required")
        if clock is not None and not fixture_mode:
            raise ValueError("history_v2_clock_injection_fixture_only")
        self.commands, self.originals, self.transport = commands, originals_service, transport
        self.fixture_mode, self.clock = fixture_mode, clock or (lambda: datetime.now(UTC))
        self._bindings = self._dependencies()
        self._functions = self._critical_functions()
        self._codes = tuple(function.__code__ for function in self._functions)
        self._sources()

    def _critical_functions(self):
        return (
            type(self).claim,
            type(self)._save_result,
            type(self)._audit_result,
            type(self)._verified_page,
            type(self)._receipt,
            type(self.transport).fetch_one,
            type(self.transport)._decode,
            type(self.transport)._request,
        )

    def _dependencies(self):
        return (
            self.commands,
            self.originals,
            self.transport,
            self.transport.archive,
            self.transport.transport,
            self.originals.raw,
            self.clock,
            type(self.commands)._read_session,
            type(self.commands)._write_session,
        )

    def _require_dependencies(self):
        if any(
            left is not right
            for left, right in zip(self._bindings, self._dependencies(), strict=True)
        ) or (
            self.transport.archive is not self.originals.raw
            or self.transport.fixture_mode is not self.fixture_mode
            or (self.transport.transport is not None) is not self.fixture_mode
        ):
            raise ValueError("history_v2_sealed_transport_dependency_changed")
        if any(
            current is not sealed or current.__code__ is not code
            for current, sealed, code in zip(
                self._critical_functions(), self._functions, self._codes, strict=True
            )
        ):
            raise ValueError("history_v2_loaded_acquisition_or_transport_callable_changed")

    def _sources(self, expected=None):
        self._require_dependencies()
        current = self.originals.require_sources(expected=expected)
        if any(
            dict(current).get(name) != digest
            for name, digest in (SOURCE_IDENTITY, *self.transport.source_files)
        ):
            raise ValueError("history_v2_loaded_acquisition_or_transport_source_changed")
        return current

    def _now(self):
        return aware_utc(self.clock())

    def _intervals(self, plan):
        result = []
        for item in plan.provenance["missing_intervals"]:
            scope = next(
                (
                    spot_scope(asset)
                    for asset in ("BTC-USDT", "ETH-USDT")
                    if spot_scope(asset).scope_id == item["scope_id"]
                ),
                None,
            )
            start, end = _dt(item["start_at"]), _dt(item["end_at"])
            minutes = (end - start).total_seconds() / 60
            maximum = item["max_pages"]
            cursor = item.get("cursor", str(int(end.timestamp() * 1000)))
            if (
                scope is None
                or start.timestamp() % 60
                or end.timestamp() % 60
                or not 0 < minutes <= 90 * 1440
                or end > plan.known_asof
                or type(maximum) is not int
                or not 1 <= maximum <= math.ceil(minutes / 300) + 2
                or cursor != str(int(end.timestamp() * 1000))
            ):
                raise ValueError("history_v2_missing_interval_invalid")
            result.append(
                {
                    "scope_id": scope.scope_id,
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "cursor": cursor,
                    "max_pages": maximum,
                }
            )
        if not 1 <= len(result) <= 1200 or len({canonical_hash(item) for item in result}) != len(
            result
        ):
            raise ValueError("history_v2_missing_intervals_empty_duplicate_or_over_limit")
        return result

    def register_epoch(
        self,
        *,
        owner_claim,
        request_id,
        plan_id,
        budget=1200,
        per_scope_budget=600,
        deadline_at=None,
    ):
        if (
            type(budget) is not int
            or not 1 <= budget <= 1200
            or type(per_scope_budget) is not int
            or not 1 <= per_scope_budget <= 600
        ):
            raise ValueError("history_v2_epoch_budget_invalid")
        with self.commands._read_session() as session:
            self._sources()
            plan = self.originals.read_original(session, plan_id, owner_claim)
            if (
                plan.kind != "plan"
                or bool(plan.provenance.get("fixture_mode", False)) != self.fixture_mode
            ):
                raise ValueError("history_v2_plan_kind_or_fixture_authority_invalid")
            intervals = self._intervals(plan)
            prior = session.scalar(
                select(Original).where(
                    Original.kind == "epoch",
                    Original.owner_claim == owner_claim,
                    Original.request_id == request_id,
                )
            )
            if prior is not None:
                value, _ = self._epoch(session, prior.original_id, owner_claim)
                if (
                    value.plan_id != plan_id
                    or value.provenance["plan_hash"] != plan.payload_hash
                    or value.provenance["budget"] != budget
                    or value.provenance["per_scope_budget"] != per_scope_budget
                    or value.provenance["intervals"] != intervals
                    or (
                        deadline_at is not None
                        and _dt(value.provenance["deadline_at"]) != _dt(deadline_at)
                    )
                ):
                    raise ValueError("history_v2_epoch_request_conflict")
                return value
            now = self._now()
            deadline = _dt(deadline_at) if deadline_at is not None else now + timedelta(hours=6)
            if not now < deadline <= now + timedelta(hours=6):
                raise ValueError("history_v2_epoch_deadline_invalid")
            prepared = self.originals.prepare_original(
                session,
                kind="epoch",
                owner_claim=owner_claim,
                request_id=request_id,
                plan_id=plan_id,
                known_asof=now,
                cutoffs=plan.cutoffs,
                provenance={
                    "plan_hash": plan.payload_hash,
                    "budget": budget,
                    "per_scope_budget": per_scope_budget,
                    "deadline_at": deadline.isoformat(),
                    "intervals": intervals,
                    "fixture_mode": self.fixture_mode,
                },
            )
        with self.commands._write_session() as session:
            self._sources(prepared.value.source_files)
            fresh_plan = self.originals.read_original(session, plan_id, owner_claim)
            if (
                fresh_plan.payload_hash != plan.payload_hash
                or self._intervals(fresh_plan) != intervals
            ):
                raise ValueError("history_v2_plan_changed_before_epoch_write")
            prior = session.get(Original, history_v2_id("epoch", owner_claim, request_id))
            if prior is not None:
                first, first_epoch = self._epoch(session, prior.original_id, owner_claim)
                if (
                    first.plan_id != plan_id
                    or first.provenance["plan_hash"] != plan.payload_hash
                    or first.provenance["budget"] != budget
                    or first.provenance["per_scope_budget"] != per_scope_budget
                    or first.provenance["intervals"] != intervals
                    or (
                        deadline_at is not None
                        and _dt(first.provenance["deadline_at"]) != _dt(deadline_at)
                    )
                ):
                    raise ValueError("history_v2_epoch_request_conflict")
                self._jobs(session, first, first_epoch)
                return first
            value = self.originals.record_original(session, prepared)
            epoch = session.get(Epoch, value.original_id)
            if epoch is None:
                session.add(
                    Epoch(
                        epoch_id=value.original_id,
                        plan_id=plan_id,
                        payload_hash=value.payload_hash,
                        state="active",
                        attempts=0,
                        consecutive_transport_errors=0,
                        error_code=None,
                        recorded_at=value.recorded_at,
                    )
                )
                for item in intervals:
                    session.add(
                        Job(
                            job_id="hgap_" + canonical_hash([value.original_id, item])[:48],
                            epoch_id=value.original_id,
                            plan_id=plan_id,
                            scope_id=item["scope_id"],
                            start_at=_dt(item["start_at"]),
                            end_at=_dt(item["end_at"]),
                            cursor=item["cursor"],
                            max_pages=item["max_pages"],
                            page_count=0,
                            attempts=0,
                            retry_used=0,
                            generation=0,
                            state="pending",
                            lease_owner=None,
                            lease_expires_at=None,
                            next_attempt_at=value.recorded_at,
                            error_code=None,
                        )
                    )
        return value

    def _jobs(self, session, value, epoch):
        jobs = list(
            session.scalars(select(Job).where(Job.epoch_id == epoch.epoch_id).order_by(Job.job_id))
        )
        expected = {
            "hgap_" + canonical_hash([epoch.epoch_id, item])[:48]: item
            for item in value.provenance["intervals"]
        }
        if len(jobs) != len(expected) or set(expected) != {job.job_id for job in jobs}:
            raise ValueError("history_v2_epoch_job_set_changed")
        counts = {
            item.job_id: item
            for item in session.execute(
                select(
                    Attempt.job_id,
                    func.count().label("total"),
                    func.max(Attempt.generation).label("generation"),
                    func.sum(case((Attempt.state == "captured", 1), else_=0)).label("pages"),
                )
                .where(Attempt.epoch_id == epoch.epoch_id)
                .group_by(Attempt.job_id)
            )
        }
        if set(counts) - set(expected) or epoch.attempts != sum(job.attempts for job in jobs):
            raise ValueError("history_v2_epoch_attempt_accounting_changed")
        for job in jobs:
            item, count = expected[job.job_id], counts.get(job.job_id)
            if (
                job.plan_id != value.plan_id
                or job.scope_id != item["scope_id"]
                or as_utc(job.start_at) != _dt(item["start_at"])
                or as_utc(job.end_at) != _dt(item["end_at"])
                or job.max_pages != item["max_pages"]
                or job.retry_used not in (0, 1)
                or not 0 <= job.page_count <= job.max_pages
                or not 0 <= job.attempts <= 2 * job.max_pages
                or job.attempts != (count.total if count else 0)
                or job.generation != job.attempts
                or (count and count.generation != job.generation)
                or job.page_count != (count.pages if count else 0)
            ):
                raise ValueError("history_v2_job_interval_or_attempt_accounting_changed")
            last = session.execute(
                select(Attempt.cursor, Attempt.state, Attempt.result_json)
                .where(Attempt.job_id == job.job_id)
                .order_by(Attempt.generation.desc())
                .limit(1)
            ).first()
            cursor = (
                json.loads(last.result_json)["next_cursor"]
                if last and last.state == "captured"
                else last.cursor
                if last
                else item["cursor"]
            )
            if job.cursor != cursor:
                raise ValueError("history_v2_job_cursor_changed")
        return jobs

    def _epoch(self, session, epoch_id, owner_claim):
        value = self.originals.read_original(session, epoch_id, owner_claim)
        self._sources(value.source_files)
        plan = self.originals.read_original(session, value.plan_id, owner_claim)
        row = session.get(Epoch, epoch_id)
        if (
            value.kind != "epoch"
            or row is None
            or row.payload_hash != value.payload_hash
            or row.plan_id != value.plan_id
            or as_utc(row.recorded_at) != value.recorded_at
            or value.provenance["plan_hash"] != plan.payload_hash
            or bool(value.provenance["fixture_mode"]) != self.fixture_mode
            or value.provenance["intervals"] != self._intervals(plan)
        ):
            raise ValueError("history_v2_epoch_or_plan_identity_changed")
        return value, row

    def claim(self, *, epoch_id, owner_claim, job_id=None, lease_owner=None):
        with self.commands._write_session() as session:
            value, epoch = self._epoch(session, epoch_id, owner_claim)
            now = self._now()
            jobs = self._jobs(session, value, epoch)
            for job in jobs:
                if job.state == "running":
                    if job.lease_expires_at is None or as_utc(job.lease_expires_at) <= now:
                        epoch.state, epoch.error_code = (
                            "paused",
                            "history_v2_expired_lease_send_unknown",
                        )
                        job.state, job.error_code = "unknown", epoch.error_code
                        for attempt in session.scalars(
                            select(Attempt).where(
                                Attempt.job_id == job.job_id,
                                Attempt.state.in_(("reserved", "captured_pending")),
                            )
                        ):
                            attempt.state = (
                                "admission_unknown" if attempt.capture_id else "send_unknown"
                            )
                    return None
            if epoch.state != "active":
                return None
            if now >= _dt(value.provenance["deadline_at"]):
                epoch.state, epoch.error_code = "expired", "history_v2_epoch_deadline_elapsed"
                return None
            if epoch.attempts != sum(job.attempts for job in jobs):
                raise ValueError("history_v2_epoch_attempt_accounting_changed")
            if epoch.attempts >= value.provenance["budget"]:
                epoch.state, epoch.error_code = "paused", "history_v2_epoch_budget_exhausted"
                return None
            latest = session.scalar(
                select(func.max(Attempt.reserved_at)).where(Attempt.epoch_id == epoch_id)
            )
            if latest is not None and now < as_utc(latest) + timedelta(seconds=1):
                return None
            for job in jobs:
                if (
                    job.state not in {"pending", "retry"}
                    or (job_id is not None and job.job_id != job_id)
                    or as_utc(job.next_attempt_at) > now
                ):
                    continue
                interval = next(
                    (
                        item
                        for item in value.provenance["intervals"]
                        if job.job_id == "hgap_" + canonical_hash([epoch_id, item])[:48]
                    ),
                    None,
                )
                if (
                    interval is None
                    or any(getattr(job, key) != interval[key] for key in ("scope_id", "max_pages"))
                    or as_utc(job.start_at) != _dt(interval["start_at"])
                    or as_utc(job.end_at) != _dt(interval["end_at"])
                ):
                    raise ValueError("history_v2_job_immutable_interval_changed")
                if (
                    job.cursor is None
                    or job.page_count >= job.max_pages
                    or job.attempts >= 2 * job.max_pages
                ):
                    job.state, job.error_code = "partial", "history_v2_job_page_budget_exhausted"
                    continue
                if (
                    sum(item.attempts for item in jobs if item.scope_id == job.scope_id)
                    >= value.provenance["per_scope_budget"]
                ):
                    job.state, job.error_code = "partial", "history_v2_scope_budget_exhausted"
                    continue
                self.transport._request(
                    "preflight",
                    next(
                        spot_scope(asset)
                        for asset in ("BTC-USDT", "ETH-USDT")
                        if spot_scope(asset).scope_id == job.scope_id
                    ),
                    job.cursor,
                    as_utc(job.start_at),
                    as_utc(job.end_at),
                )
                job.attempts += 1
                job.generation += 1
                epoch.attempts += 1
                job.state, job.lease_owner = "running", lease_owner or "v2_" + uuid4().hex
                job.lease_expires_at = now + timedelta(seconds=60)
                reserved = ReservedHistoryMissingV2(
                    "htry_" + uuid4().hex,
                    epoch_id,
                    job.job_id,
                    owner_claim,
                    job.generation,
                    job.lease_owner,
                    now,
                    job.lease_expires_at,
                    job.scope_id,
                    as_utc(job.start_at),
                    as_utc(job.end_at),
                    job.cursor,
                    value.source_files,
                )
                session.add(
                    Attempt(
                        attempt_id=reserved.attempt_id,
                        epoch_id=epoch_id,
                        job_id=job.job_id,
                        generation=job.generation,
                        cursor=job.cursor,
                        reserved_at=now,
                        state="reserved",
                        capture_id=None,
                        result_json=None,
                        result_hash=None,
                        fixture_mode=self.fixture_mode,
                    )
                )
                return reserved
            if all(job.state in {"completed", "partial", "failed"} for job in jobs):
                epoch.state = (
                    "completed" if all(job.state == "completed" for job in jobs) else "partial"
                )
            return None

    async def collect_one(self, *, epoch_id, owner_claim, job_id=None, lease_owner=None):
        reserved = self.claim(
            epoch_id=epoch_id, owner_claim=owner_claim, job_id=job_id, lease_owner=lease_owner
        )
        if reserved is None:
            return None
        self._sources(reserved.source_files)
        scope = next(
            spot_scope(asset)
            for asset in ("BTC-USDT", "ETH-USDT")
            if spot_scope(asset).scope_id == reserved.scope_id
        )
        result = await self.transport.fetch_one(
            context_id=reserved.attempt_id,
            scope=scope,
            cursor=reserved.cursor,
            start_at=reserved.start_at,
            end_at=reserved.end_at,
        )
        return self._save_result(reserved, result)

    def _save_result(self, reserved, result: CapturedHistoryPageV2):
        self._audit_result(reserved, result)
        with self.commands._write_session() as session:
            value, epoch = self._epoch(session, reserved.epoch_id, reserved.owner_claim)
            self._jobs(session, value, epoch)
            job, attempt = (
                session.get(Job, reserved.job_id),
                session.get(Attempt, reserved.attempt_id),
            )
            capture, now = result.capture, self._now()
            if (
                job is None
                or attempt is None
                or attempt.state != "captured_pending"
                or job.generation != reserved.generation
                or attempt.generation != reserved.generation
                or job.lease_owner != reserved.lease_owner
                or attempt.cursor != reserved.cursor
                or job.cursor != reserved.cursor
                or capture["context_id"] != reserved.attempt_id
                or capture["adapter_version"] != CAPTURE_PRODUCER_V2
                or capture["original_decoder_version"] != ORIGINAL_DECODER_V1
                or capture["fixture_mode"] is not self.fixture_mode
                or not reserved.reserved_at
                <= aware_utc(capture["started_at"])
                <= aware_utc(capture["completed_at"])
                <= aware_utc(capture["recorded_at"])
                <= now
            ):
                raise ValueError("history_v2_attempt_capture_identity_or_clock_changed")
            receipt = self._receipt(reserved, result)
            if (
                attempt.capture_id != capture["capture_id"]
                or attempt.result_json != _json(receipt)
                or attempt.result_hash != canonical_hash(json.loads(attempt.result_json))
            ):
                raise ValueError("history_v2_immutable_attempt_result_changed")
            saved_capture = session.get(RawCaptureRecord, capture["capture_id"])
            if saved_capture is None or any(
                (as_utc(getattr(saved_capture, key)) != _dt(capture[key]))
                if key in {"started_at", "completed_at", "recorded_at"}
                else getattr(saved_capture, key) != capture[key]
                for key in CAPTURE_FIELDS
            ):
                raise ValueError("history_v2_saved_capture_changed")
            error = result.error_code
            if now > reserved.lease_expires_at or now >= _dt(value.provenance["deadline_at"]):
                error = "history_v2_result_after_lease_or_deadline"
            rows = ()
            if error is None:
                try:
                    body, bars, rows = self._verified_page(reserved, result)
                except (ValueError, OSError, ArithmeticError, RecursionError, KeyError, TypeError):
                    error = "history_v2_result_original_changed"
                if error is None:
                    now = self._now()
                    if now > reserved.lease_expires_at or now >= _dt(
                        value.provenance["deadline_at"]
                    ):
                        error = "history_v2_result_after_lease_or_deadline"
                if error is None:
                    for bar in bars:
                        revision = canonical_hash(bar.semantic_payload())
                        identity = "candle_" + canonical_hash([bar.scope_id, revision])[:48]
                        if session.get(MarketCandleRecord, identity) is None:
                            accepted = bar.model_copy(update={"accepted_at": now})
                            session.add(
                                MarketCandleRecord(
                                    candle_id=identity,
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
                    if self.originals.raw.read(capture["body_ref"]) != body:
                        raise ValueError("history_v2_result_original_exit_changed")
            attempt.state = "failed" if error else "captured"
            job.lease_owner, job.lease_expires_at = None, None
            job.next_attempt_at, job.error_code = now + timedelta(seconds=1), error
            if error:
                job.state = "retry" if job.retry_used == 0 else "failed"
                job.retry_used = 1
            else:
                job.page_count += 1
                job.cursor, job.retry_used = result.page.next_cursor, 0
                job.state = "completed" if job.cursor is None else "pending"
            epoch.consecutive_transport_errors = (
                epoch.consecutive_transport_errors + 1 if error in TRANSPORT_FAILURES else 0
            )
            if (
                result.cancelled
                or epoch.consecutive_transport_errors >= 2
                or error == "history_v2_result_after_lease_or_deadline"
                or error == "history_v2_result_original_changed"
            ):
                epoch.state, epoch.error_code = "paused", error
            self._sources(value.source_files)
        return {**receipt, "admission_error_code": error, "attempt_state": attempt.state}

    def _receipt(self, reserved, result):
        capture = {key: result.capture[key] for key in CAPTURE_FIELDS}
        next_cursor = result.page.next_cursor if result.page is not None else None
        page = {
            "phase": "gap",
            "cursor": reserved.cursor,
            "next_cursor": next_cursor,
            "capture_ref": capture["body_ref"],
            "capture_hash": capture["body_hash"],
            "received_at": capture["completed_at"],
            "rows": result.semantic_rows,
        }
        return {
            "attempt_id": reserved.attempt_id,
            "capture": capture,
            "source_files": reserved.source_files,
            "capture_source_files": result.source_files,
            "rows": result.semantic_rows,
            "next_cursor": next_cursor,
            "error_code": result.error_code,
            "cancelled": result.cancelled,
            "fixture_mode": self.fixture_mode,
            "financial_qualified": False,
            "witness": {
                "origin": "missing_v2",
                "epoch_id": reserved.epoch_id,
                "attempt_id": reserved.attempt_id,
                "job_id": reserved.job_id,
                "scope_id": reserved.scope_id,
                "start_at": reserved.start_at.isoformat(),
                "end_at": reserved.end_at.isoformat(),
                "page": page,
                "capture": capture,
                "producer_sources": reserved.source_files,
            },
        }

    def _audit_result(self, reserved, result):
        self._require_dependencies()
        capture, now = result.capture, self._now()
        if (
            capture["context_id"] != reserved.attempt_id
            or capture["adapter_version"] != CAPTURE_PRODUCER_V2
            or capture["original_decoder_version"] != ORIGINAL_DECODER_V1
            or capture["fixture_mode"] is not self.fixture_mode
            or result.source_files != self.transport.source_files
            or capture["error_code"] != result.error_code
            or not reserved.reserved_at
            <= _dt(capture["started_at"])
            <= _dt(capture["completed_at"])
            <= _dt(capture["recorded_at"])
            <= now
        ):
            raise ValueError("history_v2_audit_capture_identity_or_clock_changed")
        with self.commands._write_session() as session:
            attempt = session.get(Attempt, reserved.attempt_id)
            if (
                attempt is None
                or attempt.state != "reserved"
                or attempt.epoch_id != reserved.epoch_id
                or attempt.job_id != reserved.job_id
                or attempt.generation != reserved.generation
                or attempt.cursor != reserved.cursor
                or attempt.fixture_mode is not self.fixture_mode
                or as_utc(attempt.reserved_at) != reserved.reserved_at
            ):
                raise ValueError("history_v2_audit_reserved_attempt_changed")
            session.add(RawCaptureRecord(**{key: capture[key] for key in CAPTURE_FIELDS}))
            pending = self._receipt(reserved, result)
            attempt.capture_id, attempt.result_json = capture["capture_id"], _json(pending)
            attempt.result_hash = canonical_hash(json.loads(attempt.result_json))
            attempt.state = "captured_pending"

    def _verified_page(self, reserved, result):
        capture, scope = (
            result.capture,
            next(
                spot_scope(asset)
                for asset in ("BTC-USDT", "ETH-USDT")
                if spot_scope(asset).scope_id == reserved.scope_id
            ),
        )
        body = self.originals.raw.read(capture["body_ref"])
        if (
            body != result.body
            or len(body) != capture["byte_count"]
            or len(body) > 1_000_000
            or hashlib.sha256(body).hexdigest() != capture["body_hash"]
            or result.page is None
            or capture["status_code"] != 200
        ):
            raise ValueError("history_v2_raw_identity_invalid")
        payload = json.loads(body)
        if (
            not isinstance(payload, dict)
            or str(payload.get("code")) != "0"
            or not isinstance(payload.get("data"), list)
            or not 1 <= len(payload["data"]) <= 300
        ):
            raise ValueError("history_v2_raw_rows_invalid")
        bars, previous = [], int(reserved.cursor)
        for index, row in enumerate(payload["data"]):
            if not isinstance(row, list) or len(row) != 9 or str(row[8]) != "1":
                raise ValueError("history_v2_raw_confirmation_invalid")
            stamp = int(row[0])
            if stamp >= previous or stamp % 60_000:
                raise ValueError("history_v2_raw_cursor_invalid")
            previous = stamp
            opened = datetime.fromtimestamp(stamp / 1000, UTC)
            bar = MarketCandle(
                scope_id=scope.scope_id,
                venue=scope.venue,
                instrument=scope.instrument,
                price_type=scope.price_type,
                open_at=opened,
                close_at=opened + timedelta(minutes=1),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume_base=Decimal(str(row[5])),
                volume_quote=Decimal(str(row[7])),
                received_at=_dt(capture["completed_at"]),
                capture_ref=capture["body_ref"],
                capture_hash=capture["body_hash"],
                row_index=index,
            )
            if reserved.start_at <= opened < reserved.end_at:
                bars.append(bar)
        bars.sort(key=lambda item: item.open_at)
        rows = tuple(
            (int(bar.open_at.timestamp() * 1000), canonical_hash(bar.semantic_payload()))
            for bar in bars
        )
        expected_next = (
            str(previous) if previous > int(reserved.start_at.timestamp() * 1000) else None
        )
        if (
            not bars
            or rows != result.semantic_rows
            or result.page.scope != scope
            or result.page.cursor != reserved.cursor
            or result.page.next_cursor != expected_next
            or tuple(bars) != result.page.candles
        ):
            raise ValueError("history_v2_raw_semantics_or_next_cursor_changed")
        return body, bars, rows
