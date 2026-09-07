from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts.models import (
    RunStatus,
    SourceHealth,
    SourceManifest,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database, utcnow
from packages.kernel.decision_hub_kernel.ports.sources import SourceRegistryPort

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
    ) -> None:
        self.database = database
        self.registry = registry
        self.admission = admission or AdmissionService(database)
        self.runs = runs or RunService(database)
        self.clock = clock
        self.strategy_selector = strategy_selector or _baseline_strategy
        self.immediate_strategy_versions = frozenset(immediate_strategy_versions)
        self.event_watches = event_watches or EventWatchService(database, clock=clock)
        for manifest in registry.manifests():
            self.database.ensure_source_state(manifest)

    def health(self, source_id: str) -> SourceHealth:
        self.registry.get(source_id)
        return self.database.get_source_health(source_id)

    def health_all(self) -> list[SourceHealth]:
        return [self.health(item.source_id) for item in self.registry.manifests()]

    async def poll_due(self, source_id: str) -> SourceIngestResult:
        health = self.health(source_id)
        now = self.clock()
        if health.next_poll_at and health.next_poll_at > now:
            return SourceIngestResult(
                source_id=source_id,
                accepted_count=0,
                duplicate_count=0,
                revision_count=0,
                cursor=health.cursor,
                skipped=True,
            )
        return await self.poll_once(source_id)

    async def poll_once(self, source_id: str) -> SourceIngestResult:
        source = self.registry.get(source_id)
        health = self.database.get_source_health(source_id)
        started = time.perf_counter()
        if not source.manifest.enabled:
            self.database.update_source_disabled(
                source_id,
                next_poll_at=(
                    self.clock() + timedelta(seconds=source.manifest.poll_interval_seconds)
                ),
            )
            return self._failure(source_id, health.cursor, "source_disabled")
        try:
            result = await source.poll(health.cursor)
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
                if envelope.scheduled_at is not None:
                    self.event_watches.ensure_watch(
                        event_id=event_id,
                        source_id=envelope.source_id,
                        event_family=envelope.event_family
                        or envelope.event_hint
                        or "scheduled_event",
                        scheduled_at=envelope.scheduled_at,
                    )
                strategies = tuple(
                    dict.fromkeys(
                        strategy
                        for strategy in self.strategy_selector(source.manifest, envelope)
                        if strategy
                    )
                )
                if not strategies:
                    raise SourceContractError("strategy selector returned no strategy")
                for strategy_version in strategies:
                    # Keep the historical baseline idempotency key stable. Research
                    # candidates use an independent key so shadow runs never claim
                    # or overwrite the baseline Run.
                    key = (
                        f"source:{source_id}:{envelope.content_hash}"
                        if strategy_version == "baseline.v1"
                        else f"source:{source_id}:{envelope.content_hash}:{strategy_version}"
                    )
                    run_id, run_created = self.runs.create(
                        event_id,
                        idempotency_key=key,
                        strategy_version=strategy_version,
                        admission_origin="automatic",
                        available_at=envelope.scheduled_at,
                    )
                    run = self.database.get_run_record(run_id)
                    due = envelope.scheduled_at is None or envelope.scheduled_at <= self.clock()
                    if strategy_version in self.immediate_strategy_versions and due and (
                        run_created or (run and run.status == RunStatus.admitted.value)
                    ):
                        targets.append(
                            RunTarget(
                                event_id=event_id,
                                run_id=run_id,
                                strategy_version=strategy_version,
                            )
                        )
            latency_ms = round((time.perf_counter() - started) * 1000)
            next_poll_at = result.next_poll_at or self.clock() + timedelta(
                seconds=source.manifest.poll_interval_seconds
            )
            self.database.update_source_success(
                source_id, result.cursor_after, latency_ms, next_poll_at=next_poll_at
            )
            return SourceIngestResult(
                source_id=source_id,
                accepted_count=accepted,
                duplicate_count=duplicates,
                revision_count=revisions,
                cursor=result.cursor_after,
                run_targets=tuple(targets),
            )
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            error_code = _source_error_code(exc)
            failures = health.consecutive_failures + 1
            backoff = min(
                source.manifest.poll_interval_seconds * (2 ** min(failures, 4)),
                900,
            )
            self.database.update_source_failure(
                source_id,
                error_code,
                latency_ms,
                next_poll_at=self.clock() + timedelta(seconds=backoff),
            )
            return self._failure(source_id, health.cursor, error_code)

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


def _baseline_strategy(_manifest: SourceManifest, _envelope: TextEnvelope) -> tuple[str, ...]:
    """Preserve the R0/R1 behavior when no discovery policy is configured."""

    return ("baseline.v1",)


def _source_error_code(error: Exception) -> str:
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
