from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update

from packages.contracts_py.decision_hub_contracts.models import (
    RunStatus,
    SourceHealth,
    SourceManifest,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.discovery_admission import (
    DiscoveryAdmissionService,
)
from packages.kernel.decision_hub_kernel.application.discovery_recovery import (
    DiscoveryRecoveryService,
    policy_snapshot,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    SourcePollAttemptRecord,
    SourceStateRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceContentSnapshot,
    SourcePollFailure,
    SourceRegistryPort,
)

RunStrategySelector = Callable[[SourceManifest, TextEnvelope], Iterable[str]]


class RunTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    run_id: str
    strategy_version: str = "baseline.v1"


class SourceIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)
    cursor: str | None = None
    run_targets: tuple[RunTarget, ...] = ()
    skipped: bool = False
    error_code: str | None = None


class SourcePollAttemptView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str
    source_id: str
    trigger_type: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    cursor_before: str | None
    cursor_after: str | None
    received_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)
    latency_ms: int | None
    error_code: str | None
    content_snapshot: SourceContentSnapshot | None = None


class SourceIngestionService:
    """Owns poll -> validate -> admission -> durable Run -> cursor commit semantics."""

    def __init__(
        self,
        database: Database,
        registry: SourceRegistryPort,
        *,
        admission: AdmissionService | None = None,
        runs: RunService | None = None,
        clock: Callable[[], datetime] = utcnow,
        strategy_selector: RunStrategySelector | None = None,
        immediate_strategy_versions: Iterable[str] = ("baseline.v1",),
        event_watches: EventWatchService | None = None,
        poll_timeout_seconds: float = 45,
        poll_context: Callable | None = None,
        event_archive=None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.admission = admission or AdmissionService(database)
        self.runs = runs or RunService(database)
        self.clock = clock
        self.strategy_selector = strategy_selector or _baseline_strategy
        self.immediate_strategy_versions = frozenset(immediate_strategy_versions)
        self.event_watches = event_watches or EventWatchService(database, clock=clock)
        self.discovery_admission = DiscoveryAdmissionService(database, self.runs)
        self.discovery_recovery = DiscoveryRecoveryService(database, self.runs)
        if poll_timeout_seconds <= 0:
            raise ValueError("source_poll_timeout_invalid")
        self.poll_timeout_seconds = poll_timeout_seconds
        self.poll_context = poll_context or (lambda attempt_id, manifest: nullcontext())
        self.event_archive = event_archive
        for manifest in registry.manifests():
            self.database.ensure_source_state(manifest)

    def recover_discovery_admissions(self, *, limit: int = 8) -> int:
        return self.discovery_recovery.recover_due(
            registry=self.registry, selector=self.strategy_selector,
            validate_envelope=self._validate_envelope, clock=self.clock, limit=limit,
        )

    async def _poll_source(self, source, cursor, trigger_type, lookback_hours):
        async with asyncio.timeout(self.poll_timeout_seconds):
            if trigger_type == "catchup":
                poll_recent = getattr(source, "poll_recent", None)
                accepts = getattr(self.strategy_selector, "accepts_title", None)
                if poll_recent is None or accepts is None:
                    raise SourceContractError("source_catchup_not_supported")
                return await poll_recent(
                    cursor,
                    lookback_hours=lookback_hours,
                    accepts_title=lambda title: accepts(source.manifest, title),
                )
            return await source.poll(cursor)

    def health(self, source_id: str) -> SourceHealth:
        self.registry.get(source_id)
        return self.database.get_source_health(source_id)

    def health_all(self) -> list[SourceHealth]:
        return [self.health(item.source_id) for item in self.registry.manifests()]

    def list_attempts(
        self, source_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[SourcePollAttemptView]:
        self.registry.get(source_id)
        with self.database.session() as session:
            rows = session.scalars(
                select(SourcePollAttemptRecord)
                .where(SourcePollAttemptRecord.source_id == source_id)
                .order_by(
                    SourcePollAttemptRecord.started_at.desc(),
                    SourcePollAttemptRecord.attempt_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._attempt_view(row) for row in rows]

    async def poll_due(self, source_id: str) -> SourceIngestResult:
        self._recover_expired_poll(source_id)
        health = self.health(source_id)
        now = self.clock()
        if health.next_poll_at and health.next_poll_at > now:
            slot = await asyncio.to_thread(self._cpi_release_slot, now, 'scheduled') if source_id == 'bls-public-api' else None
            if slot is None:
                return SourceIngestResult(
                source_id=source_id,
                accepted_count=0,
                duplicate_count=0,
                revision_count=0,
                cursor=health.cursor,
                skipped=True,
            )
        return await self.poll_once(source_id, trigger_type="scheduled")

    def _cpi_release_slot(self, now, trigger_type, *, session=None):
        from .cpi_event_poll import read_due_cpi_release_slot_in_session
        from .cpi_pre_event_context import _archive
        archive = self.event_archive if self.event_archive is not None else _archive()
        if session is not None:
            return read_due_cpi_release_slot_in_session(session, archive=archive,
                now=now, trigger_type=trigger_type)[0]
        with self.database.session() as owned:
            return read_due_cpi_release_slot_in_session(owned, archive=archive,
                now=now, trigger_type=trigger_type)[0]

    def _recover_expired_poll(self, source_id: str) -> None:
        """Close interrupted manual polls even when the next scheduled poll is hours away."""
        now = self.clock()
        with self.database.session() as reader:
            row = reader.get(SourceStateRecord, source_id)
            if (row is None or row.poll_owner is None or row.poll_lease_expires_at is None
                    or as_utc(row.poll_lease_expires_at) > now):
                return
        # The precheck avoids a writer for empty recovery; the original CAS
        # still owns recovery if another poll changes the lease meanwhile.
        with self.database.session() as session:
            recovered = session.execute(
                update(SourceStateRecord)
                .where(
                    SourceStateRecord.source_id == source_id,
                    SourceStateRecord.poll_owner.is_not(None),
                    SourceStateRecord.poll_lease_expires_at <= now,
                )
                .values(poll_owner=None, poll_lease_expires_at=None)
            )
            if recovered.rowcount == 1:
                session.execute(
                    update(SourcePollAttemptRecord)
                    .where(
                        SourcePollAttemptRecord.source_id == source_id,
                        SourcePollAttemptRecord.status == "running",
                    )
                    .values(
                        status="failed",
                        completed_at=now,
                        error_code="source_poll_lease_recovered",
                    )
                )

    async def poll_once(
        self,
        source_id: str,
        *,
        trigger_type: str = "manual",
        lookback_hours: int = 168,
    ) -> SourceIngestResult:
        self.registry.get(source_id)
        owner = uuid4().hex
        now = self.clock()
        with self.database.session() as session:
            claimed = session.execute(
                update(SourceStateRecord)
                .where(
                    SourceStateRecord.source_id == source_id,
                    or_(
                        SourceStateRecord.poll_owner.is_(None),
                        SourceStateRecord.poll_lease_expires_at <= now,
                    ),
                )
                .values(
                    poll_owner=owner,
                    poll_lease_expires_at=now + timedelta(seconds=self.poll_timeout_seconds + 60),
                )
            )
            if claimed.rowcount != 1:
                return self._failure(
                    source_id, self.health(source_id).cursor, "source_poll_busy"
                ).model_copy(update={"skipped": True})
            if source_id == "bls-public-api":
                # Durable and shared by manual/API/Worker; failed requests count.
                state = session.get(SourceStateRecord, source_id, populate_existing=True)
                utc_now = now.astimezone(UTC)
                day = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
                count = session.scalar(select(func.count()).select_from(SourcePollAttemptRecord).where(
                    SourcePollAttemptRecord.source_id == source_id,
                    SourcePollAttemptRecord.started_at >= day,
                    SourcePollAttemptRecord.started_at < day + timedelta(days=1),
                    SourcePollAttemptRecord.status != "skipped",
                )) or 0
                latest = session.scalar(select(SourcePollAttemptRecord.started_at).where(
                    SourcePollAttemptRecord.source_id == source_id,
                    SourcePollAttemptRecord.status != "skipped",
                ).order_by(SourcePollAttemptRecord.started_at.desc()).limit(1))
                wait_until = max((as_utc(state.next_poll_at) if state else None) or day,
                                 (as_utc(latest) + timedelta(hours=6)) if latest else day)
                # Only the authenticated scheduled window can override the
                # ordinary six-hour floor. Recheck under the existing poll
                # owner transaction; manual/catchup cannot request this grant.
                event_slot = self._cpi_release_slot(utc_now, trigger_type, session=session) if trigger_type == 'scheduled' and count < 24 else None
                code = "source_bls_daily_quota_exhausted" if count >= 24 else "source_bls_poll_interval_pending"
                if count >= 24 or (wait_until > utc_now and event_slot is None):
                    pending = max(wait_until, day + timedelta(days=1)) if count >= 24 else wait_until
                    session.execute(update(SourceStateRecord).where(SourceStateRecord.source_id == source_id, SourceStateRecord.poll_owner == owner).values(poll_owner=None, poll_lease_expires_at=None, next_poll_at=pending))
                    return SourceIngestResult(source_id=source_id, accepted_count=0, duplicate_count=0, revision_count=0, cursor=state.cursor if state else None, skipped=True, error_code=code)
                # Reserve the floor before HTTP: crash/restart must not refetch.
                session.execute(update(SourceStateRecord).where(SourceStateRecord.source_id == source_id, SourceStateRecord.poll_owner == owner).values(next_poll_at=utc_now + timedelta(hours=6)))
                if event_slot is not None:
                    trigger_type = event_slot.trigger_type
            # A new owner can recover attempts left behind by a killed process.
            session.execute(
                update(SourcePollAttemptRecord)
                .where(
                    SourcePollAttemptRecord.source_id == source_id,
                    SourcePollAttemptRecord.status == "running",
                )
                .values(status="failed", completed_at=now, error_code="source_poll_lease_recovered")
            )
        try:
            return await self._poll_owned(
                source_id, trigger_type=trigger_type, owner=owner, lookback_hours=lookback_hours
            )
        finally:
            with self.database.session() as session:
                session.execute(
                    update(SourceStateRecord)
                    .where(
                        SourceStateRecord.source_id == source_id,
                        SourceStateRecord.poll_owner == owner,
                    )
                    .values(poll_owner=None, poll_lease_expires_at=None)
                )

    def _owns_poll(self, source_id: str, owner: str) -> bool:
        with self.database.session() as session:
            row = session.get(SourceStateRecord, source_id)
            return bool(
                row
                and row.poll_owner == owner
                and (as_utc(row.poll_lease_expires_at) or self.clock()) > self.clock()
            )

    async def _poll_owned(
        self,
        source_id: str,
        *,
        trigger_type: str,
        owner: str,
        lookback_hours: int,
    ) -> SourceIngestResult:
        source = self.registry.get(source_id)
        health = self.database.get_source_health(source_id)
        started = time.perf_counter()
        attempt_id = self._start_attempt(source_id, trigger_type, health.cursor)
        received_count = 0
        accepted = 0
        duplicates = 0
        revisions = 0
        cursor_after = health.cursor
        content_snapshot = None
        source_success_committed = False
        if not source.manifest.enabled:
            self.database.update_source_disabled(
                source_id,
                next_poll_at=(
                    self.clock() + timedelta(seconds=source.manifest.poll_interval_seconds)
                ),
            )
            self._finish_attempt(
                attempt_id,
                status="skipped",
                error_code="source_disabled",
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            return self._failure(source_id, health.cursor, "source_disabled")
        try:
            with self.poll_context(attempt_id, source.manifest):
                result = await self._poll_source(
                    source, health.cursor, trigger_type, lookback_hours
                )
            if not self._owns_poll(source_id, owner):
                raise SourcePollLeaseLost()
            content_snapshot = result.content_snapshot
            received_count = len(result.envelopes)
            cursor_after = result.cursor_after
            if result.source_id != source_id:
                raise SourceContractError("source_id mismatch")
            if result.cursor_before != health.cursor:
                raise SourceContractError("cursor_before mismatch")
            if len(result.envelopes) > source.manifest.max_batch:
                raise SourceContractError("source batch exceeds max_batch")
            for envelope in result.envelopes:
                self._validate_envelope(source.manifest, envelope)
            admitted = self.admission.admit_envelopes(result.envelopes)
            accepted = sum(1 for _, _, created in admitted if created)
            duplicates = len(admitted) - accepted
            revisions = sum(
                1 for _, envelope, created in admitted if created and envelope.revision_of
            )
            targets: list[RunTarget] = []
            for event_id, envelope, _created in admitted:
                strategies = tuple(
                    dict.fromkeys(
                        strategy
                        for strategy in self.strategy_selector(source.manifest, envelope)
                        if strategy
                    )
                )
                if not strategies and not getattr(
                    self.strategy_selector, "record_only_allowed", False
                ):
                    raise SourceContractError("strategy selector returned no strategy")
                explain = getattr(self.strategy_selector, "explain", None)
                details = (
                    explain(source.manifest, envelope)
                    if explain
                    else {
                        "reason": "research_not_selected_by_policy",
                        "policy_version": "baseline-compat.v1",
                    }
                )
                if trigger_type == "catchup":
                    details = {
                        **details,
                        "trigger_type": "catchup",
                        "lookback_hours": lookback_hours,
                    }
                # Exact calendar occurrences still need observations even when the
                # discovery policy records an event without starting any analysis.
                if envelope.scheduled_at is not None and (
                    not envelope.macro_event
                    or (
                        envelope.macro_event.time_precision == "exact"
                        and envelope.macro_event.status != "cancelled"
                    )
                ):
                    self.event_watches.ensure_watch(
                        event_id=event_id,
                        source_id=envelope.source_id,
                        event_family=envelope.event_family
                        or envelope.event_hint
                        or "scheduled_event",
                        scheduled_at=envelope.scheduled_at,
                    )
                # Retain an explicit research decision even when the source is
                # only recorded or routed to the compatibility baseline.
                for strategy_version in dict.fromkeys((*strategies, "research.v1")):
                    run_id, run_created, run_event_id = self.discovery_admission.decide(
                        attempt_id=attempt_id,
                        event_id=event_id,
                        envelope=envelope,
                        strategy=strategy_version,
                        selected=strategy_version in strategies,
                        reason=details["reason"]
                        if strategy_version == "research.v1"
                        else "baseline_compatibility",
                        policy_version=details["policy_version"],
                        recovery_context={
                            "manifest": source.manifest.model_dump(mode="json"),
                            "policy": recovery_policy,
                        } if (recovery_policy := policy_snapshot(
                            self.strategy_selector
                        )) is not None else None,
                        details=details,
                        now=self.clock(),
                        max_daily_research=getattr(
                            self.strategy_selector, "max_daily_research", 12
                        ),
                        max_study_research_per_hour=getattr(
                            self.strategy_selector, "max_study_research_per_hour", 2
                        ),
                    )
                    if run_id is None:
                        continue
                    run = self.database.get_run_record(run_id)
                    due = run.available_at is None or as_utc(run.available_at) <= self.clock()
                    if (
                        strategy_version in self.immediate_strategy_versions
                        and due
                        and (run_created or (run and run.status == RunStatus.admitted.value))
                    ):
                        targets.append(
                            RunTarget(
                                event_id=run_event_id,
                                run_id=run_id,
                                strategy_version=strategy_version,
                            )
                        )
            latency_ms = round((time.perf_counter() - started) * 1000)
            next_poll_at = result.next_poll_at or self.clock() + timedelta(
                seconds=source.manifest.poll_interval_seconds
            )
            if trigger_type == "catchup" and health.next_poll_at:
                next_poll_at = health.next_poll_at
            if source_id == "bls-public-api":
                next_poll_at = max(next_poll_at, self.clock() + timedelta(hours=6))
            if not self._owns_poll(source_id, owner):
                raise SourcePollLeaseLost()
            self.database.update_source_success(
                source_id, result.cursor_after, latency_ms, next_poll_at=next_poll_at
            )
            source_success_committed = True
            self._finish_attempt(
                attempt_id,
                status="empty" if received_count == 0 else "succeeded",
                cursor_after=cursor_after,
                received_count=received_count,
                accepted_count=accepted,
                duplicate_count=duplicates,
                revision_count=revisions,
                latency_ms=latency_ms,
                content_snapshot=content_snapshot,
            )
            return SourceIngestResult(
                source_id=source_id,
                accepted_count=accepted,
                duplicate_count=duplicates,
                revision_count=revisions,
                cursor=result.cursor_after,
                run_targets=tuple(targets),
            )
        except asyncio.CancelledError:
            # Intentional worker shutdown must not leave a permanently running
            # attempt. The cursor remains replayable through admission keys.
            self._finish_attempt(
                attempt_id,
                status="failed",
                error_code="source_poll_interrupted",
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
        except Exception as exc:
            if source_success_committed:
                raise
            latency_ms = round((time.perf_counter() - started) * 1000)
            if isinstance(exc, SourcePollLeaseLost) or not self._owns_poll(source_id, owner):
                self._finish_attempt(
                    attempt_id,
                    status="failed",
                    error_code="source_poll_lease_lost",
                    latency_ms=latency_ms,
                )
                return self._failure(source_id, health.cursor, "source_poll_lease_lost")
            error_code = _source_error_code(exc)
            if isinstance(exc, SourcePollFailure):
                content_snapshot = exc.snapshot
            failures = health.consecutive_failures + 1
            backoff = min(
                source.manifest.poll_interval_seconds * (2 ** min(failures, 4)),
                900,
            )
            # Access restrictions need an operator check, not frequent network retries.
            # Keep this schedule durable; manual recovery still creates its own receipt.
            if error_code in {"source_access_denied", "source_authentication_required"}:
                backoff = max(source.manifest.poll_interval_seconds, 24 * 60 * 60)
            if source_id == "bls-public-api":
                backoff = max(backoff, 6 * 60 * 60)
            self.database.update_source_failure(
                source_id,
                error_code,
                latency_ms,
                next_poll_at=self.clock() + timedelta(seconds=backoff),
            )
            self._finish_attempt(
                attempt_id,
                status="failed",
                cursor_after=cursor_after,
                received_count=received_count,
                accepted_count=accepted,
                duplicate_count=duplicates,
                revision_count=revisions,
                latency_ms=latency_ms,
                error_code=error_code,
                content_snapshot=content_snapshot,
            )
            return self._failure(source_id, health.cursor, error_code)

    def _start_attempt(self, source_id: str, trigger_type: str, cursor_before: str | None) -> str:
        attempt_id = uuid4().hex
        with self.database.session() as session:
            session.add(
                SourcePollAttemptRecord(
                    attempt_id=attempt_id,
                    source_id=source_id,
                    trigger_type=trigger_type,
                    status="running",
                    started_at=self.clock(),
                    completed_at=None,
                    cursor_before=cursor_before,
                    cursor_after=None,
                    received_count=0,
                    accepted_count=0,
                    duplicate_count=0,
                    revision_count=0,
                    latency_ms=None,
                    error_code=None,
                )
            )
        return attempt_id

    def _finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        cursor_after: str | None = None,
        received_count: int = 0,
        accepted_count: int = 0,
        duplicate_count: int = 0,
        revision_count: int = 0,
        latency_ms: int,
        error_code: str | None = None,
        content_snapshot: SourceContentSnapshot | None = None,
    ) -> None:
        with self.database.session() as session:
            record = session.get(SourcePollAttemptRecord, attempt_id)
            if record is None:
                raise RuntimeError(f"source poll attempt disappeared: {attempt_id}")
            record.status = status
            record.completed_at = self.clock()
            record.cursor_after = cursor_after
            record.received_count = received_count
            record.accepted_count = accepted_count
            record.duplicate_count = duplicate_count
            record.revision_count = revision_count
            record.latency_ms = latency_ms
            record.error_code = error_code
            record.content_snapshot_json = (
                content_snapshot.model_dump_json() if content_snapshot else None
            )

    @staticmethod
    def _attempt_view(record: SourcePollAttemptRecord) -> SourcePollAttemptView:
        return SourcePollAttemptView(
            attempt_id=record.attempt_id,
            source_id=record.source_id,
            trigger_type=record.trigger_type,
            status=record.status,
            started_at=as_utc(record.started_at),
            completed_at=as_utc(record.completed_at),
            cursor_before=record.cursor_before,
            cursor_after=record.cursor_after,
            received_count=record.received_count,
            accepted_count=record.accepted_count,
            duplicate_count=record.duplicate_count,
            revision_count=record.revision_count,
            latency_ms=record.latency_ms,
            error_code=record.error_code,
            content_snapshot=SourceContentSnapshot.model_validate_json(record.content_snapshot_json)
            if record.content_snapshot_json
            else None,
        )

    @staticmethod
    def _failure(source_id: str, cursor: str | None, error_code: str) -> SourceIngestResult:
        return SourceIngestResult(
            source_id=source_id,
            accepted_count=0,
            duplicate_count=0,
            revision_count=0,
            cursor=cursor,
            error_code=error_code,
        )

    def _validate_envelope(self, manifest: SourceManifest, envelope: TextEnvelope) -> None:
        if envelope.source_id != manifest.source_id:
            raise SourceContractError("envelope source_id mismatch")
        if envelope.source_type.value != manifest.source_type:
            raise SourceContractError("envelope source_type mismatch")
        digest = hashlib.sha256(envelope.raw_text.encode("utf-8")).hexdigest()
        if envelope.content_hash != digest:
            raise SourceContractError("envelope content_hash mismatch")
        if envelope.received_at > self.clock() + timedelta(seconds=5):
            raise SourceContractError("envelope received_at is in the future")
        if envelope.published_at and envelope.published_at > envelope.received_at:
            raise SourceContractError("published_at exceeds received_at")
        if envelope.source_url and manifest.allowed_domains:
            hostname = (urlparse(str(envelope.source_url)).hostname or "").lower()
            if not any(
                hostname == allowed or hostname.endswith(f".{allowed}")
                for allowed in manifest.allowed_domains
            ):
                raise SourceContractError("source_url is outside allowed domains")


class SourceContractError(ValueError):
    error_code = "source_invalid_payload"


class SourcePollLeaseLost(RuntimeError):
    pass


def _baseline_strategy(_manifest: SourceManifest, _envelope: TextEnvelope) -> tuple[str, ...]:
    """Preserve the R0/R1 behavior when no discovery policy is configured."""

    return ("baseline.v1",)


def _source_error_code(error: Exception) -> str:
    if isinstance(error, SourcePollFailure):
        return error.error_code
    if isinstance(error, TimeoutError):
        return "source_timeout"
    if isinstance(error, SourceContractError):
        return error.error_code
    text = str(error).lower()
    if "rate" in text or "429" in text:
        return "source_rate_limited"
    if "timeout" in text:
        return "source_timeout"
    if "xml" in text or "parse" in text or "json" in text:
        return "source_parse_error"
    return getattr(error, "error_code", None) or "source_unavailable"
