"""Artifact-only one-shot collection and committed-read availability boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic_ns
from typing import Any
from uuid import uuid4

import httpx
from packages.kernel.decision_hub_kernel.ports.action_quote_clock import (
    MEASUREMENT_ADAPTER,
    MEASUREMENT_URL,
)
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence.action_quote_repository import (
    QuoteRepository,
    StoredQuote,
    _capture,
)
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.ports.action_quote import (
    capture_context,
    require_scope,
    text_at,
)
from packages.kernel.decision_hub_kernel.ports.market_path import MarketPathScope, aware_utc
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    CapturingAsyncClient,
    RawBodyArchive,
    capture_scope,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def writer_lock(session: Session) -> None:
    # Same SQLite short writer mode used by current persistence services.
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        raise ValueError("action_quote_draft_sqlite_only")
    connection.exec_driver_sql("BEGIN IMMEDIATE")


class QuoteStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        archive: RawBodyArchive,
        *,
        clock: Callable[[], datetime] = now_utc,
        writer_guard: Callable[[Session], None] | None = None,
    ):
        self.sessions = session_factory
        self.archive = archive
        self.clock = clock
        self.writer_guard = writer_guard
        self.repository = QuoteRepository(archive)

    def record_capture(self, entry: dict[str, Any]) -> None:
        # Mirrors FactLineageStore.record_capture; no new HTTP body archive.
        with self.sessions() as session, session.begin():
            session.add(RawCaptureRecord(**entry))

    def admit_capture(
        self, *, scope: MarketPathScope, capture_id: str, clock_measurement_id: str | None = None
    ) -> StoredQuote:
        with self.sessions() as session:
            prepared = self.repository.prepare(
                session,
                scope=scope,
                capture_id=capture_id,
                clock_measurement_id=clock_measurement_id,
            )
        with self.sessions() as session, session.begin():
            writer_lock(session)
            if self.writer_guard is not None:
                self.writer_guard(session)
            quote = self.repository.admit(session, prepared, admitted_at=aware_utc(self.clock()))
        # This method returns only after commit. It does not guess available_at.
        return quote

    def observe_available(self, *, quote_id: str) -> StoredQuote:
        with self.sessions() as session:
            # Fresh session proves the source quote exists after a prior commit.
            quote = self.repository.read(session, quote_id=quote_id)
            available = aware_utc(self.clock())
        if quote.available_at is not None:
            return quote
        with self.sessions() as session, session.begin():
            writer_lock(session)
            if self.writer_guard is not None:
                self.writer_guard(session)
            result = self.repository.record_available(
                session,
                quote_id=quote_id,
                quote_hash=quote.payload_hash,
                observed_available_at=available,
            )
        return result


class QuoteCaptureService:
    """One bounded time/ticker/time bracket per normal scope sampling slot."""

    def __init__(self, store: QuoteStore, *, transport: httpx.AsyncBaseTransport | None = None):
        self.store = store
        # Test-only MockTransport injection. Normal composition leaves this None.
        self.transport = transport
        self.capture_ids: tuple[str, ...] = ()

    async def collect_once(self, *, scope: MarketPathScope) -> StoredQuote:
        require_scope(scope)
        batch = CaptureBatch(
            capture_context(scope, uuid4().hex).replace("action-quote.v1:", "action-quote.v2:"),
            "okx-action-quote.v2",
            self.store.archive,
            self.store.record_capture,
        )
        url = f"https://www.okx.com/api/v5/market/ticker?instId={scope.instrument}"
        started = now_utc()
        bracket_start, bracket_mono = now_utc(), monotonic_ns()
        measurements = {}

        async def bounded_get(
            client: CapturingAsyncClient, endpoint: str, seconds: int
        ) -> httpx.Response:
            count, begin = len(batch.captures), now_utc()
            try:
                return await asyncio.wait_for(
                    client.get(endpoint, timeout=seconds, headers={"Cache-Control": "no-cache"}),
                    timeout=seconds,
                )
            except (TimeoutError, asyncio.CancelledError):
                if len(batch.captures) == count:
                    batch.record(
                        url=endpoint,
                        started_at=begin,
                        completed_at=now_utc(),
                        error="action_quote_request_deadline_exceeded",
                    )
                raise

        async def measured_time(client: CapturingAsyncClient, name: str) -> None:
            begin, mono = now_utc(), monotonic_ns()
            response = await bounded_get(client, "https://www.okx.com/api/v5/public/time", 2)
            measurements[name] = {
                "started_at": text_at(begin),
                "completed_at": text_at(now_utc()),
                "elapsed_monotonic_ns": monotonic_ns() - mono,
            }
            response.raise_for_status()

        async def bracket(client: CapturingAsyncClient) -> None:
            await measured_time(client, "before")
            response = await bounded_get(client, url, 8)
            response.raise_for_status()
            await measured_time(client, "after")

        try:
            with capture_scope(batch):
                async with CapturingAsyncClient(
                    timeout=8, transport=self.transport, follow_redirects=False
                ) as client:
                    await asyncio.wait_for(bracket(client), timeout=14)
        except TimeoutError:
            if not batch.captures:
                batch.record(
                    url=url,
                    started_at=started,
                    completed_at=now_utc(),
                    error="action_quote_request_deadline_exceeded",
                )
            raise
        finally:
            self.capture_ids = tuple(entry["capture_id"] for entry in batch.captures)
        if len(batch.captures) != 3:
            raise ValueError("action_quote_capture_count_invalid")
        measurements["bracket"] = {
            "started_at": text_at(bracket_start),
            "completed_at": text_at(now_utc()),
            "elapsed_monotonic_ns": monotonic_ns() - bracket_mono,
        }
        metadata = {
            "schema_version": "action-quote-clock-measurement.v1",
            "kind": "internal_clock_measurement",
            "captures": [_capture(RawCaptureRecord(**entry)).payload() for entry in batch.captures],
            **measurements,
        }
        raw = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        digest, ref = self.store.archive.put(raw)
        measured_at = now_utc()
        clock_id = "raw_" + uuid4().hex
        self.store.record_capture(
            {
                "capture_id": clock_id,
                "context_id": batch.context_id,
                "url": MEASUREMENT_URL,
                "status_code": None,
                "body_hash": digest,
                "body_ref": ref,
                "byte_count": len(raw),
                "media_type": "application/json",
                "adapter_version": MEASUREMENT_ADAPTER,
                "error_code": None,
                "started_at": measured_at,
                "completed_at": measured_at,
                "recorded_at": now_utc(),
            }
        )
        self.capture_ids = (*self.capture_ids, clock_id)
        quote = self.store.admit_capture(
            scope=scope, capture_id=batch.captures[1]["capture_id"], clock_measurement_id=clock_id
        )
        return self.store.observe_available(quote_id=quote.quote_id)
