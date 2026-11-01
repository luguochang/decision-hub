"""Explicit history-at-fit commands; status never collects or fits.

The command layer owns transaction lifetimes, not training algorithms. Each
preparation uses a physical read-only SQLite snapshot and the same producer
instance/thread. Only a fresh writer may persist the existing prepared value.
An uncertain BEGIN/COMMIT escapes; no command retries a write or HTTP attempt.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..persistence.db import Database, as_utc
from ..persistence.history_observed_records import HistoryObservedTrainingRecord
from ..ports.history_observed_model import FrozenHistoryObservedPathModelV2
from ..ports.history_observed_training import (
    HistoryObservedDatasetManifestV1,
    HistoryObservedTrainingProtocolV1,
)
from ..ports.market_path import MarketPathScope, aware_utc
from .history_observed_acquisition import HistoryObservedAcquisition
from .history_observed_dataset import require_snapshot, snapshot_identity
from .history_observed_ledger import HistoryObservedLedger
from .history_observed_model import HistoryObservedModelService


@dataclass(frozen=True)
class HistoryObservedJobStatus:
    job_id: str
    scope_id: str
    cutoff_at: datetime
    deadline_at: datetime
    state: str
    attempts: int
    lease_generation: int
    lease_expires_at: datetime | None
    next_attempt_at: datetime
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True)
class HistoryObservedStoredStatus:
    """Stored row metadata, not a verified dataset or model original."""

    kind: str
    scope_id: str
    payload_hash: str
    known_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class HistoryObservedCommandStatus:
    registration_id: str
    protocol_hash: str
    registered_at: datetime
    deadline_at: datetime
    as_of: datetime
    jobs: tuple[HistoryObservedJobStatus, ...]
    state_counts: tuple[tuple[str, int], ...]
    attempts: int
    stored: tuple[HistoryObservedStoredStatus, ...]
    proof_scope: Literal["registered_protocol_and_job_metadata"] = (
        "registered_protocol_and_job_metadata"
    )
    dataset_originals_verified: Literal[False] = False
    model_originals_verified: Literal[False] = False
    ready_for_real_fit: Literal[False] = False
    financial_qualified: Literal[False] = False


class HistoryObservedCommandService:
    def __init__(
        self,
        database: Database,
        ledger: HistoryObservedLedger,
        acquisition: HistoryObservedAcquisition,
        model: HistoryObservedModelService,
    ) -> None:
        if (
            acquisition.database is not database
            or acquisition.ledger is not ledger
            or model.ledger is not ledger
        ):
            raise ValueError("history_observed_command_composition_invalid")
        self.database, self.ledger = database, ledger
        self.acquisition, self.model = acquisition, model

    @contextmanager
    def _read_session(self) -> Generator[Session, None, None]:
        # Hold the connection until query_only is restored, even after Session
        # rollback. Never return a query_only pooled driver before restoration.
        with self.database.engine.connect() as connection:
            native = connection.connection.driver_connection
            if (
                connection.dialect.name != "sqlite"
                or not isinstance(native, sqlite3.Connection)
                or native.in_transaction
            ):
                raise ValueError("history_observed_command_fresh_SQLite_required")
            prior = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
            if prior not in (0, 1):
                raise ValueError("history_observed_command_read_mode_invalid")
            try:
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.exec_driver_sql("BEGIN")
                with self.database.factory(bind=connection) as session:
                    session.connection()
                    boundary = snapshot_identity(session)
                    yield session
                    require_snapshot(session, boundary)
            finally:
                try:
                    connection.rollback()
                    connection.exec_driver_sql(f"PRAGMA query_only={int(prior)}")
                    connection.rollback()
                except BaseException:
                    # An un-restored driver must not be reused by another writer.
                    connection.invalidate()
                    raise

    @contextmanager
    def _write_session(self) -> Generator[Session, None, None]:
        with self.database.session() as session:
            # Reuse the sealed collector's clean fresh BEGIN IMMEDIATE boundary.
            self.acquisition._writer(session)  # pyright: ignore[reportPrivateUsage]
            yield session
        # The existing Database.session commits before the command can return.

    def register(self, request_id: str) -> HistoryObservedTrainingProtocolV1:
        with self._write_session() as session:
            value = self.ledger.register(session, request_id)
        return value

    def activate(self, registration_id: str) -> int:
        return self.acquisition.activate(registration_id)

    async def process_one(self, registration_id: str, job_id: str | None = None) -> bool:
        return await self.acquisition.process_one(registration_id, job_id)

    def freeze(
        self,
        registration_id: str,
        scope: MarketPathScope,
        known_by: datetime,
    ) -> HistoryObservedDatasetManifestV1:
        with self._read_session() as session:
            prepared = self.ledger.prepare(session, registration_id, scope, known_by)
        with self._write_session() as session:
            value = self.ledger.freeze(session, prepared)
        return value

    def fit(
        self, registration_id: str, scope: MarketPathScope,
    ) -> FrozenHistoryObservedPathModelV2:
        with self._read_session() as session:
            prepared = self.model.prepare_fit(session, registration_id, scope)
        with self._write_session() as session:
            value = self.model.save(session, prepared)
        return value

    def status(self, registration_id: str) -> HistoryObservedCommandStatus:
        """Authenticate registration/job identities, never prepare or write archives.

        Dataset/model entries are explicitly stored metadata only. Their presence
        and job completion counts never confer fit, model or financial authority.
        """
        with self._read_session() as session:
            protocol = self.ledger.protocol(session, registration_id)
            # Reuse the exact 180-job/clock/source-binding check, not a new count.
            rows = self.acquisition._jobs(  # pyright: ignore[reportPrivateUsage]
                session, protocol,
            )
            attempts = sum(row.attempts for row in rows)
            if attempts > protocol.total_attempt_cap or any(
                sum(row.attempts for row in rows if row.scope_id == scope.scope_id)
                > protocol.per_scope_attempt_cap
                for scope in protocol.scopes
            ):
                raise ValueError("history_observed_status_attempt_budget_invalid")
            jobs = tuple(
                HistoryObservedJobStatus(
                    row.job_id, row.scope_id, as_utc(row.cutoff_at), as_utc(row.deadline_at),
                    row.state, row.attempts, row.lease_generation,
                    as_utc(row.lease_expires_at) if row.lease_expires_at is not None else None,
                    as_utc(row.next_attempt_at),
                    as_utc(row.completed_at) if row.completed_at is not None else None,
                    row.error_code,
                )
                for row in sorted(rows, key=lambda row: (row.scope_id, row.cutoff_at))
            )
            stored = tuple(
                HistoryObservedStoredStatus(
                    row.kind, row.scope_id, row.payload_hash,
                    as_utc(row.known_at), as_utc(row.recorded_at),
                )
                for row in session.scalars(
                    select(HistoryObservedTrainingRecord)
                    .where(HistoryObservedTrainingRecord.registration_id == registration_id)
                    .order_by(
                        HistoryObservedTrainingRecord.kind,
                        HistoryObservedTrainingRecord.scope_id,
                    )
                    .execution_options(populate_existing=True)
                )
            )
            self.ledger.require_sources(protocol)
            return HistoryObservedCommandStatus(
                registration_id, protocol.payload_hash, protocol.registered_at,
                protocol.registered_at + timedelta(seconds=protocol.acquisition_deadline_seconds),
                aware_utc(self.ledger.clock()), jobs,
                tuple(sorted(Counter(job.state for job in jobs).items())),
                attempts, stored,
            )
