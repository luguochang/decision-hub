"""Bounded, restart-safe acquisition of the registered history-at-fit windows.

This worker stores observations, never training or source-admission permission.
HTTP is outside SQLite transactions; a reserved request is charged before send.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.provider_adapters.market.okx_path import MarketPathError, OKXMarketPathAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)

from ..persistence.db import Database, RawCaptureRecord, as_utc
from ..persistence.history_observed_records import HistoryObservedAcquisitionJobRecord as Job
from ..ports.history_observed_training import HistoryObservedTrainingProtocolV1
from ..ports.market_path import (
    MarketPathPage,
    MarketPathScope,
    MarketPricePath,
    aware_utc,
    canonical_hash,
    path_hashes,
)
from .history_observed_ledger import HistoryObservedLedger
from .market_path_store import MarketPathStore

REQUIRED_SOURCES = (
    "packages/kernel/decision_hub_kernel/application/history_observed_acquisition.py",
    "packages/provider_adapters/market/okx_path.py",
    "packages/provider_adapters/research/raw_capture.py",
)
TERMINAL = {"completed", "partial", "failed", "expired"}
STATES = TERMINAL | {"held", "pending", "running"}


class _Page(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    phase: Literal["label", "feature"]
    cursor: str
    next_cursor: str | None
    capture_ref: str
    capture_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    received_at: datetime
    rows: tuple[tuple[int, str], ...] = Field(max_length=300)


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["history-observed-acquisition-checkpoint.v1"] = (
        "history-observed-acquisition-checkpoint.v1"
    )
    phase: Literal["label", "feature", "done"] = "label"
    cursor: str | None = None
    retry_used: Literal[0, 1] = 0
    inflight: bool = False
    pages: tuple[_Page, ...] = Field(default=(), max_length=6)
    page_count: int = Field(default=0, ge=0, le=6)
    attempt_count: int = Field(default=0, ge=0, le=12)
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def checksum(self) -> _Checkpoint:
        if self.page_count != len(self.pages) or self.attempt_count < self.page_count:
            raise ValueError("history_observed_checkpoint_counts_invalid")
        if (
            canonical_hash(self.model_dump(mode="json", exclude={"payload_hash"}))
            != self.payload_hash
        ):
            raise ValueError("history_observed_checkpoint_changed")
        return self


def _checkpoint(**values: Any) -> _Checkpoint:
    payload: dict[str, Any] = {
        "schema_version": "history-observed-acquisition-checkpoint.v1",
        "phase": "label",
        "cursor": None,
        "retry_used": 0,
        "inflight": False,
        "pages": (),
        "attempt_count": 0,
        **values,
    }
    payload["page_count"] = len(payload["pages"])
    payload = TypeAdapter(Any).dump_python(payload, mode="json")
    return _Checkpoint.model_validate({**payload, "payload_hash": canonical_hash(payload)})


@dataclass(frozen=True)
class HistoryAcquisitionClaim:
    job_id: str
    registration_id: str
    scope: MarketPathScope
    cutoff_at: datetime
    deadline_at: datetime
    owner: str
    generation: int
    attempt: int
    protocol: HistoryObservedTrainingProtocolV1
    checkpoint: _Checkpoint


class _SharedSession:
    """Let the unchanged MarketPathStore join the worker's fenced writer."""

    def __init__(self, session: Session) -> None:
        self.current = session

    @contextmanager
    def session(self) -> Generator[Session]:
        yield self.current


class HistoryObservedAcquisition:
    def __init__(
        self,
        database: Database,
        ledger: HistoryObservedLedger,
        archive: RawBodyArchive,
        *,
        owner_id: str,
        adapter: OKXMarketPathAdapter | None = None,
        lease_seconds: int = 120,
    ) -> None:
        if not owner_id or len(owner_id) > 128 or not 30 <= lease_seconds <= 600:
            raise ValueError("history_observed_acquisition_owner_invalid")
        self.database, self.ledger, self.archive = database, ledger, archive
        self.owner, self.adapter, self.lease_seconds = (
            owner_id,
            adapter or OKXMarketPathAdapter(max_pages=6),
            lease_seconds,
        )

    def now(self) -> datetime:
        return aware_utc(self.ledger.clock())

    @staticmethod
    def _writer(session: Session) -> None:
        connection = session.connection()
        native = connection.connection.driver_connection
        if (
            connection.dialect.name != "sqlite"
            or native is None
            or native.in_transaction
            or session.new
            or session.dirty
            or session.deleted
        ):
            raise ValueError("history_observed_fresh_writer_required")
        # There is no retry around BEGIN or the context manager's COMMIT.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        if not native.in_transaction:
            raise ValueError("history_observed_physical_writer_required")

    def _protocol(
        self, session: Session, registration_id: str
    ) -> HistoryObservedTrainingProtocolV1:
        protocol = self.ledger.protocol(session, registration_id)
        if not set(REQUIRED_SOURCES).issubset(dict(protocol.implementation_files)):
            raise ValueError("history_observed_acquisition_source_unregistered")
        return protocol

    def _jobs(self, session: Session, protocol: HistoryObservedTrainingProtocolV1) -> list[Job]:
        rows = list(
            session.scalars(
                select(Job)
                .where(Job.registration_id == protocol.registration_id)
                .execution_options(populate_existing=True)
            )
        )
        expected = {
            (scope.scope_id, cutoff)
            for scope in protocol.scopes
            for cutoff in protocol.daily_cutoffs
        }
        if len(rows) != 180 or {(row.scope_id, as_utc(row.cutoff_at)) for row in rows} != expected:
            raise ValueError("history_observed_fixed_job_denominator_changed")
        for row in rows:
            if (
                row.job_id
                != "hobs_job_"
                + canonical_hash(
                    [protocol.registration_id, row.scope_id, as_utc(row.cutoff_at).isoformat()]
                )[:48]
                or row.protocol_hash != protocol.payload_hash
                or as_utc(row.deadline_at) != protocol.registered_at + timedelta(hours=6)
                or as_utc(row.recorded_at) != protocol.registered_at
                or row.state not in STATES
                or not 0 <= row.attempts <= protocol.per_job_attempt_cap
                or row.lease_generation < 0
            ):
                raise ValueError("history_observed_job_identity_changed")
        return rows

    def activate(self, registration_id: str) -> int:
        """Explicitly release only the already registered fixed task set."""
        with self.database.session() as session:
            self._writer(session)
            protocol = self._protocol(session, registration_id)
            rows, now = self._jobs(session, protocol), self.now()
            changed = 0
            for row in rows:
                if row.state == "held":
                    row.state = "pending" if now < as_utc(row.deadline_at) else "expired"
                    row.next_attempt_at = protocol.registered_at
                    row.checkpoint_json = _checkpoint().model_dump_json()
                    row.error_code = None if row.state == "pending" else "fixed_deadline_elapsed"
                    row.completed_at = now if row.state == "expired" else None
                    changed += 1
            session.flush()
            self.ledger.require_sources(protocol)
            return changed

    def _read_checkpoint(self, row: Job) -> _Checkpoint:
        checkpoint = _Checkpoint.model_validate_json(row.checkpoint_json)
        if checkpoint.attempt_count != row.attempts:
            raise ValueError("history_observed_checkpoint_attempts_changed")
        for page in checkpoint.pages:
            if hashlib.sha256(self.archive.read(page.capture_ref)).hexdigest() != page.capture_hash:
                raise ValueError("history_observed_checkpoint_original_changed")
        return checkpoint

    def claim(
        self, registration_id: str, job_id: str | None = None
    ) -> HistoryAcquisitionClaim | None:
        with self.database.session() as session:
            self._writer(session)
            protocol = self._protocol(session, registration_id)
            rows, now = self._jobs(session, protocol), self.now()
            # Shared table fencing prevents two processes/registrations issuing HTTP together.
            all_rows = list(session.scalars(select(Job).execution_options(populate_existing=True)))
            if any(row.state == "running" and row.lease_expires_at is None for row in all_rows):
                raise ValueError("history_observed_running_lease_missing")
            if any(
                row.state == "running"
                and row.lease_expires_at is not None
                and as_utc(row.lease_expires_at) > now
                for row in all_rows
            ):
                return None
            last = max(
                (
                    as_utc(row.last_attempt_at)
                    for row in all_rows
                    if row.last_attempt_at is not None
                ),
                default=None,
            )
            if last is not None and now < last + timedelta(seconds=1):
                return None
            # Include the post-response cooldown, so varying source-check duration
            # cannot reduce the actual interval between two request starts.
            if any(row.attempts > 0 and as_utc(row.next_attempt_at) > now for row in all_rows):
                return None
            for row in sorted(rows, key=lambda item: (item.cutoff_at, item.scope_id)):
                if (
                    row.state in TERMINAL
                    or row.state == "held"
                    or (job_id and row.job_id != job_id)
                ):
                    continue
                if now >= as_utc(row.deadline_at):
                    self._terminal(row, "expired", "fixed_deadline_elapsed", now)
                    continue
                if as_utc(row.next_attempt_at) > now:
                    continue
                checkpoint = self._read_checkpoint(row)
                if checkpoint.inflight:
                    # A crash cannot turn an uncertain GET outcome into an uncharged retry.
                    self._terminal(row, "partial", "attempt_outcome_unknown", now)
                    continue
                scope = next(scope for scope in protocol.scopes if scope.scope_id == row.scope_id)
                scope_attempts = sum(
                    item.attempts for item in rows if item.scope_id == row.scope_id
                )
                if (
                    row.attempts >= protocol.per_job_attempt_cap
                    or checkpoint.page_count >= protocol.daily_page_cap
                    or scope_attempts >= 600
                    or sum(item.attempts for item in rows) >= 1200
                ):
                    self._terminal(row, "partial", "fixed_attempt_budget_exhausted", now)
                    continue
                row.state, row.lease_owner = "running", self.owner
                row.lease_generation += 1
                row.attempts += 1
                row.last_attempt_at = now
                row.lease_expires_at = min(
                    now + timedelta(seconds=self.lease_seconds), as_utc(row.deadline_at)
                )
                reserved = _checkpoint(
                    **{
                        key: value
                        for key, value in checkpoint.model_dump(exclude={"payload_hash"}).items()
                        if key not in ("inflight", "attempt_count")
                    },
                    inflight=True,
                    attempt_count=row.attempts,
                )
                row.checkpoint_json = reserved.model_dump_json()
                session.flush()
                self.ledger.require_sources(protocol)
                return HistoryAcquisitionClaim(
                    row.job_id,
                    registration_id,
                    scope,
                    as_utc(row.cutoff_at),
                    as_utc(row.deadline_at),
                    self.owner,
                    row.lease_generation,
                    row.attempts,
                    protocol,
                    reserved,
                )
            session.flush()
            self.ledger.require_sources(protocol)
            return None

    @staticmethod
    def _terminal(row: Job, state: str, error: str | None, now: datetime) -> None:
        row.state, row.error_code, row.completed_at = state, error, now
        row.next_attempt_at = now + timedelta(seconds=1)
        row.lease_generation += 1
        row.lease_owner = row.lease_expires_at = None

    def _owns(self, session: Session, claim: HistoryAcquisitionClaim) -> Job:
        row = session.get(Job, claim.job_id, populate_existing=True)
        now = self.now()
        if (
            row is None
            or row.state != "running"
            or row.lease_owner != claim.owner
            or row.lease_generation != claim.generation
            or row.attempts != claim.attempt
            or row.registration_id != claim.registration_id
            or row.protocol_hash != claim.protocol.payload_hash
            or row.scope_id != claim.scope.scope_id
            or as_utc(row.cutoff_at) != claim.cutoff_at
            or as_utc(row.deadline_at) != claim.deadline_at
            or now >= claim.deadline_at
            or row.lease_expires_at is None
            or now >= as_utc(row.lease_expires_at)
            or row.checkpoint_json != claim.checkpoint.model_dump_json()
        ):
            raise ValueError("history_observed_acquisition_lease_lost")
        self.ledger.require_sources(claim.protocol)
        return row

    def _capture(self, claim: HistoryAcquisitionClaim, value: dict[str, Any]) -> None:
        with self.database.session() as session:
            self._writer(session)
            self._owns(session, claim)
            received = aware_utc(value["completed_at"])
            recorded = aware_utc(value["recorded_at"])
            if (
                not claim.protocol.registered_at
                <= aware_utc(value["started_at"])
                <= received
                <= recorded
                <= self.now()
            ):
                raise ValueError("history_observed_capture_clock_invalid")
            session.add(RawCaptureRecord(**value))
            session.flush()
            self._owns(session, claim)

    @staticmethod
    def _window(claim: HistoryAcquisitionClaim) -> tuple[datetime, datetime]:
        if claim.checkpoint.phase == "label":
            return claim.cutoff_at + timedelta(minutes=1), claim.cutoff_at + timedelta(
                days=1, minutes=1
            )
        if claim.checkpoint.phase == "feature":
            return claim.cutoff_at - timedelta(minutes=31), claim.cutoff_at
        raise ValueError("history_observed_completed_checkpoint_not_requestable")

    def _save(
        self, claim: HistoryAcquisitionClaim, page: MarketPathPage, batch: CaptureBatch
    ) -> None:
        start, end = self._window(claim)
        expected_cursor = claim.checkpoint.cursor or str(int(end.timestamp() * 1000))
        captures = [
            capture
            for capture in batch.captures
            if capture["body_ref"] == page.capture_ref
            and capture["body_hash"] == page.capture_hash
            and capture["status_code"] == 200
            and capture["error_code"] is None
            and capture["completed_at"] == page.received_at
        ]
        if (
            page.scope != claim.scope
            or page.cursor != expected_cursor
            or not captures
            or page.capture_ref is None
            or not claim.protocol.registered_at <= page.received_at <= self.now()
            or (
                page.next_cursor is not None
                and (
                    int(page.next_cursor) >= int(expected_cursor)
                    or int(page.next_cursor) < int(start.timestamp() * 1000)
                )
            )
        ):
            raise ValueError("history_observed_page_identity_invalid")
        if hashlib.sha256(self.archive.read(page.capture_ref)).hexdigest() != page.capture_hash:
            raise ValueError("history_observed_page_original_changed")
        old = {stamp: digest for saved in claim.checkpoint.pages for stamp, digest in saved.rows}
        selected = tuple(
            (int(bar.open_at.timestamp() * 1000), canonical_hash(bar.semantic_payload()))
            for bar in page.candles
        )
        if any(stamp in old and old[stamp] != digest for stamp, digest in selected):
            raise ValueError("history_observed_conflicting_page")
        phase = claim.checkpoint.phase
        if phase not in ("label", "feature"):
            raise ValueError("history_observed_page_phase_invalid")
        saved = _Page(
            phase=phase,
            cursor=expected_cursor,
            next_cursor=page.next_cursor,
            capture_ref=page.capture_ref,
            capture_hash=page.capture_hash,
            received_at=page.received_at,
            rows=selected,
        )
        pages = (*claim.checkpoint.pages, saved)
        phase = "feature" if page.next_cursor is None and phase == "label" else phase
        phase = "done" if page.next_cursor is None and saved.phase == "feature" else phase
        checkpoint = _checkpoint(
            phase=phase, cursor=page.next_cursor, pages=pages, attempt_count=claim.attempt
        )
        semantic, manifest = path_hashes(claim.scope, page.candles)
        path = MarketPricePath(
            scope=claim.scope,
            window_start_at=start,
            window_end_at=end,
            candles=page.candles,
            page_count=1,
            request_count=1,
            status="partial",
            semantic_hash=semantic,
            manifest_hash=manifest,
        )
        with self.database.session() as session:
            self._writer(session)
            row = self._owns(session, claim)
            # Original verification, candle inserts and checkpoint commit share the same fence.
            store = MarketPathStore(
                cast(Database, _SharedSession(session)), self.archive, clock=self.ledger.clock
            )
            store.ingest(path)
            session.flush()
            self._owns(session, claim)
            row.checkpoint_json = checkpoint.model_dump_json()
            row.lease_owner = row.lease_expires_at = None
            row.next_attempt_at = self.now() + timedelta(seconds=1)
            if phase == "done":
                start_ms = int(claim.cutoff_at.timestamp() * 1000)
                expected = {start_ms + index * 60000 for index in range(1, 1441)}
                expected |= {start_ms - index * 60000 for index in range(1, 32)}
                observed = {stamp for item in pages for stamp, _ in item.rows}
                self._terminal(
                    row,
                    "completed" if observed == expected else "partial",
                    None if observed == expected else "fixed_window_gap",
                    self.now(),
                )
            elif checkpoint.page_count >= claim.protocol.daily_page_cap:
                self._terminal(row, "partial", "fixed_page_budget_exhausted", self.now())
            else:
                row.state = "pending"
            session.flush()
            self.ledger.require_sources(claim.protocol)

    def _failure(
        self,
        claim: HistoryAcquisitionClaim,
        code: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        with self.database.session() as session:
            self._writer(session)
            row = self._owns(session, claim)
            now = self.now()
            if (
                retryable
                and claim.checkpoint.retry_used == 0
                and row.attempts < claim.protocol.per_job_attempt_cap
                and claim.checkpoint.page_count < claim.protocol.daily_page_cap
            ):
                delay = max(1.0, retry_after or 0.0)
                if not math.isfinite(delay) or now + timedelta(seconds=delay) >= claim.deadline_at:
                    self._terminal(row, "expired", "retry_exceeds_fixed_deadline", now)
                else:
                    row.state, row.error_code = "pending", code
                    row.next_attempt_at = now + timedelta(seconds=delay)
                    row.checkpoint_json = _checkpoint(
                        **{
                            key: value
                            for key, value in claim.checkpoint.model_dump(
                                exclude={"payload_hash"}
                            ).items()
                            if key not in ("inflight", "retry_used")
                        },
                        inflight=False,
                        retry_used=1,
                    ).model_dump_json()
                    row.lease_owner = row.lease_expires_at = None
            else:
                self._terminal(row, "partial" if claim.checkpoint.pages else "failed", code, now)
                row.checkpoint_json = _checkpoint(
                    **{
                        key: value
                        for key, value in claim.checkpoint.model_dump(
                            exclude={"payload_hash"}
                        ).items()
                        if key != "inflight"
                    },
                    inflight=False,
                ).model_dump_json()
            session.flush()
            self.ledger.require_sources(claim.protocol)

    async def execute(self, claim: HistoryAcquisitionClaim) -> None:
        # Reject a stale claim before any request, not only when persisting its result.
        with self.database.session() as session:
            self._writer(session)
            self._owns(session, claim)

        def persist(value: dict[str, Any]) -> None:
            self._capture(claim, value)

        batch = CaptureBatch(claim.job_id, self.adapter.adapter_version, self.archive, persist)
        try:
            start, end = self._window(claim)
            remaining = (claim.deadline_at - self.now()).total_seconds()
            with capture_scope(batch):
                async with asyncio.timeout(min(15.0, remaining)):
                    page = await self.adapter.candles(
                        scope=claim.scope,
                        start_at=start,
                        end_at=end,
                        cursor=claim.checkpoint.cursor,
                    )
            self._save(claim, page, batch)
        except MarketPathError as exc:
            self._failure(
                claim, exc.code, retryable=exc.retryable, retry_after=exc.retry_after_seconds
            )
        except TimeoutError:
            self._failure(claim, "history_observed_transport_timeout", retryable=True)
        except asyncio.CancelledError:
            self._failure(claim, "history_observed_acquisition_cancelled")
            raise
        except ValueError:
            self._failure(claim, "history_observed_original_verification_failed")
            raise
        # SQLite/unknown COMMIT failures escape. Never replay an uncertain write/send.

    async def process_one(self, registration_id: str, job_id: str | None = None) -> bool:
        claim = self.claim(registration_id, job_id)
        if claim is None:
            return False
        await self.execute(claim)
        return True
