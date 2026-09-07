from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    CryptoEventWindowFailure,
    CryptoEventWindowObservation,
    CryptoEventWindowPayload,
    EventWindowCapture,
    EventWindowSample,
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter

WINDOW_REF_PREFIX = "crypto-window://"
_WINDOW_REF = re.compile(r"^crypto-window://([a-f0-9]{64})$")
_FIELD_ALIASES = {
    "spot_price": "price",
    "spot_volume": "volume",
}
_DERIVATIVE_FIELDS = frozenset(
    {
        "funding_rate",
        "open_interest",
        "open_interest_delta",
        "mark_price",
        "index_price",
        "basis",
        "crowding_signal",
        "book_imbalance",
    }
)
_SPOT_FIELDS = frozenset({"price", "volume", "spot_price", "spot_volume"})
_BASELINE_OFFSET = "t-5m"
_POST_OFFSET = "t+1m"


class CryptoEventWindowProvider(Protocol):
    """Provider-neutral capture seam used by the durable scheduler."""

    provider_id: str

    async def capture(
        self, sample: EventWindowSample
    ) -> Sequence[CryptoEventWindowObservation] | CryptoEventWindowPayload: ...


class CapabilitySnapshotWindowProvider:
    """Reuse an existing typed market adapter for a scheduler capture.

    The adapter is called with a bounded synthetic query, then its canonical
    facts are projected into the provider-neutral capture payload. This keeps
    HTTP parsing and vendor field mappings in the existing provider adapter;
    the event-window layer owns only timing and archival semantics.
    """

    def __init__(
        self,
        provider_id: str,
        adapter: ResearchCapabilityAdapter,
        *,
        symbols: Sequence[str],
        fields: Sequence[str],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.provider_id = provider_id
        self.adapter = adapter
        self.symbols = tuple(symbols)
        self.fields = tuple(fields)
        self.clock = clock

    async def capture(self, sample: EventWindowSample) -> Sequence[CryptoEventWindowObservation]:
        captured_at = _aware(self.clock())
        query = ResearchCapabilityQuery(
            schema_version="research-capability-query.v1",
            request_id=f"event-window:{sample.sample_id}",
            capability_id=self.adapter.capability_id,
            requirement_id=(
                "crypto_spot_confirmation"
                if any(
                    item in {"price", "spot_price", "volume", "spot_volume"} for item in self.fields
                )
                else "derivatives_crowding"
            ),
            query=f"event-window:{sample.event_id}:{sample.offset}",
            target_url=None,
            symbols=list(self.symbols),
            fields=list(self.fields),
            allowed_domains=[],
            max_results=20,
            max_cost_usd=0.0,
            research_session_id="event-window-capture",
            round=1,
            mode="live",
            observed_at=captured_at,
            cutoff_at=captured_at,
        )
        result = await self.adapter.execute(query)
        candidates = {item.evidence_id: item for item in result.evidence_candidates}
        observations: list[CryptoEventWindowObservation] = []
        for fact in result.facts or []:
            candidate = candidates.get(fact.evidence_id)
            canonical_field = _FIELD_ALIASES.get(fact.field, fact.field)
            # A mixed CoinEx query currently labels the whole response as one
            # metric family. Reclassify at this canonical boundary so a spot
            # price cannot enter the derivatives requirement by accident.
            metric_family = (
                "crypto.spot" if canonical_field in _SPOT_FIELDS else "crypto.derivatives"
            )
            observations.append(
                CryptoEventWindowObservation(
                    provider_id=self.provider_id,
                    source_id=fact.source_id,
                    venue=fact.venue or self.provider_id.removesuffix("-public"),
                    metric_family=metric_family,
                    field=canonical_field,
                    value=fact.value if fact.value is not None else "",
                    unit=fact.unit,
                    source_url=candidate.source_url if candidate else None,
                    published_at=fact.published_at,
                )
            )
        return observations


class CryptoEventWindowArchive:
    """Immutable content-addressed storage for one event-window capture payload."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, payload: CryptoEventWindowPayload) -> tuple[str, str]:
        """Atomically persist a canonical payload and return ``(ref, sha256)``."""

        serialized = _canonical_payload(payload)
        payload_hash = hashlib.sha256(serialized).hexdigest()
        path = self.root / f"{payload_hash}.json"
        if not path.exists():
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{payload_hash}.", suffix=".tmp", dir=self.root
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return f"{WINDOW_REF_PREFIX}{payload_hash}", payload_hash

    put = write
    store = write

    def load(
        self, payload_ref: str, *, expected_hash: str | None = None
    ) -> CryptoEventWindowPayload:
        """Load and verify a payload reference; all mismatches fail closed."""

        match = _WINDOW_REF.fullmatch(payload_ref)
        if match is None:
            raise ValueError("provider_window_payload_ref_invalid")
        payload_hash = match.group(1)
        if expected_hash is not None and payload_hash != expected_hash:
            raise ValueError("provider_window_payload_hash_mismatch")
        path = self.root / f"{payload_hash}.json"
        try:
            serialized = path.read_bytes()
        except OSError as exc:
            raise ValueError("provider_window_payload_missing") from exc
        if hashlib.sha256(serialized).hexdigest() != payload_hash:
            raise ValueError("provider_window_payload_hash_mismatch")
        try:
            payload = CryptoEventWindowPayload.model_validate(json.loads(serialized))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("provider_window_payload_invalid") from exc
        if hashlib.sha256(_canonical_payload(payload)).hexdigest() != payload_hash:
            raise ValueError("provider_window_payload_hash_mismatch")
        return payload


class CryptoEventWindowSampler:
    """Capture one due slot from every configured venue and archive one payload."""

    def __init__(
        self,
        providers: Iterable[CryptoEventWindowProvider],
        archive: CryptoEventWindowArchive,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        values = tuple(providers)
        if not values:
            raise ValueError("event_window_provider_required")
        provider_ids = [item.provider_id for item in values]
        if any(not item.strip() for item in provider_ids) or len(set(provider_ids)) != len(
            provider_ids
        ):
            raise ValueError("event_window_provider_identity_invalid")
        self.providers = values
        self.archive = archive
        self.clock = clock

    async def capture(self, sample: EventWindowSample) -> EventWindowCapture | None:
        observations: list[CryptoEventWindowObservation] = []
        failures: list[CryptoEventWindowFailure] = []
        for provider in self.providers:
            try:
                result = await provider.capture(sample)
                observations.extend(_provider_observations(provider, result, sample))
                if not observations or not any(
                    item.provider_id == provider.provider_id for item in observations
                ):
                    failures.append(
                        CryptoEventWindowFailure(
                            provider_id=provider.provider_id,
                            error_code="event_window_provider_empty",
                            retryable=True,
                        )
                    )
            except Exception as exc:
                failures.append(
                    CryptoEventWindowFailure(
                        provider_id=provider.provider_id,
                        error_code=getattr(exc, "error_code", None)
                        or "event_window_provider_failed",
                        retryable=bool(getattr(exc, "retryable", True)),
                    )
                )
        if not observations:
            return None
        captured_at = _aware(self.clock())
        payload = CryptoEventWindowPayload(
            schema_version="crypto-event-window-payload.v1",
            event_id=sample.event_id,
            offset=sample.offset,
            target_at=sample.target_at,
            captured_at=captured_at,
            observations=observations,
            failures=failures,
        )
        payload_ref, payload_hash = self.archive.write(payload)
        return EventWindowCapture(
            schema_version="event-window-capture.v1",
            observed_at=captured_at,
            received_at=captured_at,
            provider_id="crypto-window-archive",
            payload_ref=payload_ref,
            payload_hash=payload_hash,
        )


class CryptoEventWindowResearchAdapter:
    """Project immutable event-window payloads into canonical Evidence and Facts."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live", "replay"})
    supported_fields = frozenset(
        {
            "price",
            "volume",
            "spot_price",
            "spot_volume",
            "event_return",
            "funding_rate",
            "open_interest",
            "open_interest_delta",
            "mark_price",
            "index_price",
            "basis",
            "crowding_signal",
            "book_imbalance",
        }
    )

    def __init__(
        self,
        event_watches: EventWatchService,
        archive: CryptoEventWindowArchive,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.event_watches = event_watches
        self.archive = archive
        self.clock = clock

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not query.event_id:
            raise ResearchCapabilityError(
                "research_event_id_required",
                "event-window capability requires a durable event_id",
                retryable=False,
            )
        requested = tuple(query.requested_event_offsets or ())
        if not requested:
            raise ResearchCapabilityError(
                "research_event_offsets_required",
                "event-window capability requires requested event offsets",
                retryable=False,
            )
        samples = self.event_watches.list_event_samples(query.event_id)
        if not samples:
            raise ResearchCapabilityError(
                "research_event_watch_not_found",
                "no durable EventWatch exists for the requested event",
                retryable=False,
            )
        by_offset = {item.offset: item for item in samples}
        if any(offset not in by_offset for offset in requested):
            # Missing slots remain explicit in the Gate; never substitute a current quote.
            selected = tuple(by_offset[offset] for offset in requested if offset in by_offset)
        else:
            selected = tuple(by_offset[offset] for offset in requested)
        captures = self._load_captures(selected, query)
        candidates, facts = self._project(query, captures)
        completed_at = max(
            (sample.received_at for sample, _payload in captures if sample.received_at is not None),
            default=query.cutoff_at,
        )
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="event-window-archive",
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=0.0,
            completed_at=_aware(completed_at),
        )

    def _load_captures(
        self,
        samples: Sequence[EventWindowSample],
        query: ResearchCapabilityQuery,
    ) -> tuple[tuple[EventWindowSample, CryptoEventWindowPayload], ...]:
        captures: list[tuple[EventWindowSample, CryptoEventWindowPayload]] = []
        for sample in samples:
            if sample.status != "captured" or not sample.payload_ref or not sample.payload_hash:
                continue
            try:
                payload = self.archive.load(sample.payload_ref, expected_hash=sample.payload_hash)
            except ValueError as exc:
                raise ResearchCapabilityError(
                    str(exc),
                    "event-window payload failed integrity validation",
                    retryable=False,
                    origin="provider",
                ) from exc
            if (
                payload.event_id != query.event_id
                or payload.event_id != sample.event_id
                or payload.offset != sample.offset
                or payload.target_at != sample.target_at
            ):
                raise ResearchCapabilityError(
                    "research_event_window_lineage_mismatch",
                    "event-window payload does not match its durable sample",
                    retryable=False,
                    origin="gateway",
                )
            observed_at = sample.observed_at
            received_at = sample.received_at
            if received_at is None or observed_at is None:
                continue
            if observed_at > received_at or received_at > query.cutoff_at:
                raise ResearchCapabilityError(
                    "research_event_window_pit_violation",
                    "event-window sample timestamps violate PIT",
                    retryable=False,
                    origin="pit",
                )
            captures.append((sample, payload))
        return tuple(captures)

    def _project(
        self,
        query: ResearchCapabilityQuery,
        captures: Sequence[tuple[EventWindowSample, CryptoEventWindowPayload]],
    ) -> tuple[list[EvidenceCandidate], list[FactEnvelope]]:
        requested = {_FIELD_ALIASES.get(item, item) for item in query.fields}
        include_all = not requested
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        observations_by_key: dict[
            tuple[str, str, str, str],
            list[tuple[EventWindowSample, CryptoEventWindowObservation, EvidenceCandidate]],
        ] = defaultdict(list)

        for sample, payload in captures:
            observed_at = sample.observed_at
            received_at = sample.received_at
            if observed_at is None or received_at is None:
                continue
            grouped: dict[tuple[str, str, str], list[CryptoEventWindowObservation]] = defaultdict(
                list
            )
            for observation in payload.observations:
                field = _FIELD_ALIASES.get(observation.field, observation.field)
                if (
                    not include_all
                    and field not in requested
                    and field not in {"event_return", "open_interest_delta"}
                ):
                    continue
                if observation.published_at is not None and observation.published_at > observed_at:
                    raise ResearchCapabilityError(
                        "research_event_window_pit_violation",
                        "event-window observation was published after it was observed",
                        retryable=False,
                        origin="pit",
                    )
                grouped[(observation.provider_id, observation.source_id, observation.venue)].append(
                    observation
                )
            for key, grouped_observations in grouped.items():
                provider_id, source_id, venue = key
                excerpt = json.dumps(
                    {
                        "event_id": query.event_id,
                        "offset": sample.offset,
                        "observations": [
                            item.model_dump(mode="json") for item in grouped_observations
                        ],
                        "failures": [item.model_dump(mode="json") for item in payload.failures],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:4000]
                source_url = next(
                    (item.source_url for item in grouped_observations if item.source_url), None
                )
                content_hash = research_evidence_content_hash(
                    requirement_id=query.requirement_id,
                    kind="market",
                    authority="exchange",
                    source_id=source_id,
                    source_url=str(source_url) if source_url else None,
                    published_at=max(
                        (item.published_at for item in grouped_observations if item.published_at),
                        default=None,
                    ),
                    excerpt=excerpt,
                    structured_payload_ref=sample.payload_ref,
                )
                candidate = EvidenceCandidate(
                    evidence_id=research_evidence_instance_id(
                        content_hash=content_hash,
                        research_session_id=query.research_session_id,
                    ),
                    requirement_id=query.requirement_id,
                    kind="market",
                    authority="exchange",
                    source_id=source_id,
                    source_url=AnyUrl(str(source_url)) if source_url else None,
                    published_at=max(
                        (item.published_at for item in grouped_observations if item.published_at),
                        default=None,
                    ),
                    observed_at=observed_at,
                    received_at=received_at,
                    content_hash=content_hash,
                    excerpt=excerpt,
                    structured_payload_ref=sample.payload_ref,
                    tool_call_id=query.request_id,
                    research_session_id=query.research_session_id,
                    round=query.round,
                    quality="candidate",
                    freshness_status="unknown",
                    conflict_group=None,
                )
                candidates.append(candidate)
                for observation in grouped_observations:
                    field = _FIELD_ALIASES.get(observation.field, observation.field)
                    observations_by_key[(provider_id, source_id, venue, field)].append(
                        (sample, observation, candidate)
                    )
                    if include_all or field in requested:
                        facts.append(
                            _window_fact(
                                query=query,
                                candidate=candidate,
                                sample=sample,
                                observation=observation,
                                field=field,
                            )
                        )

        facts.extend(
            _derived_facts(
                query=query,
                observations=observations_by_key,
                requested=requested,
                include_all=include_all,
            )
        )
        return candidates, _dedupe_facts(facts)


def _provider_observations(
    provider: CryptoEventWindowProvider,
    result: Sequence[CryptoEventWindowObservation] | CryptoEventWindowPayload,
    sample: EventWindowSample,
) -> list[CryptoEventWindowObservation]:
    observations = (
        list(result.observations) if isinstance(result, CryptoEventWindowPayload) else list(result)
    )
    if isinstance(result, CryptoEventWindowPayload) and (
        result.event_id != sample.event_id
        or result.offset != sample.offset
        or result.target_at != sample.target_at
    ):
        raise ValueError("event_window_provider_lineage_mismatch")
    for observation in observations:
        if observation.provider_id != provider.provider_id:
            raise ValueError("event_window_provider_identity_mismatch")
    return observations


def _window_fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    sample: EventWindowSample,
    observation: CryptoEventWindowObservation,
    field: str,
) -> FactEnvelope:
    observed_at = sample.observed_at
    received_at = sample.received_at
    if observed_at is None or received_at is None:
        raise ResearchCapabilityError(
            "research_event_window_pit_violation",
            "event-window fact requires observed and received timestamps",
            retryable=False,
            origin="pit",
        )
    instrument = (
        "BTC-USDT-SWAP" if observation.metric_family == "crypto.derivatives" else "BTC-USDT"
    )
    attributes: dict[str, float | str | bool | None] = {
        "provider_id": observation.provider_id,
        "venue": observation.venue,
    }
    payload_schema_ref = "crypto-event-window-payload.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=observation.metric_family,
        field=field,
        instrument=instrument,
        venue=observation.venue,
        value=observation.value,
        unit=observation.unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=sample.offset,
        published_at=observation.published_at,
        source_id=candidate.source_id,
        independence_group=observation.provider_id,
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family=observation.metric_family,
        field=field,
        instrument=instrument,
        venue=observation.venue,
        value=observation.value,
        unit=observation.unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=sample.offset,
        observed_at=observed_at,
        received_at=received_at,
        published_at=observation.published_at,
        source_id=candidate.source_id,
        independence_group=observation.provider_id,
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )


def _derived_facts(
    *,
    query: ResearchCapabilityQuery,
    observations: Mapping[
        tuple[str, str, str, str],
        list[tuple[EventWindowSample, CryptoEventWindowObservation, EvidenceCandidate]],
    ],
    requested: set[str],
    include_all: bool,
) -> list[FactEnvelope]:
    output: list[FactEnvelope] = []
    for (provider_id, source_id, venue, field), values in observations.items():
        if field not in {"price", "open_interest"}:
            continue
        by_offset = {
            sample.offset: (sample, observation, candidate)
            for sample, observation, candidate in values
        }
        baseline = by_offset.get(_BASELINE_OFFSET)
        post = by_offset.get(_POST_OFFSET)
        if baseline is None or post is None:
            continue
        derived_field = "event_return" if field == "price" else "open_interest_delta"
        if not include_all and derived_field not in requested:
            continue
        try:
            baseline_value = Decimal(str(baseline[1].value))
            post_value = Decimal(str(post[1].value))
            if baseline_value == 0:
                continue
            value = str((post_value / baseline_value - Decimal(1)) * Decimal(100))
        except (InvalidOperation, ValueError):
            continue
        sample, observation, candidate = post
        observed_at = sample.observed_at
        received_at = sample.received_at
        if observed_at is None or received_at is None:
            continue
        metric_family = observation.metric_family
        unit = "percent"
        instrument = "BTC-USDT-SWAP" if metric_family == "crypto.derivatives" else "BTC-USDT"
        attributes: dict[str, float | str | bool | None] = {
            "provider_id": provider_id,
            "venue": venue,
            "baseline_offset": _BASELINE_OFFSET,
            "post_offset": _POST_OFFSET,
        }
        payload_schema_ref = "crypto-event-window-payload.v1"
        payload_hash = research_fact_payload_hash(
            requirement_id=query.requirement_id,
            metric_family=metric_family,
            field=derived_field,
            instrument=instrument,
            venue=venue,
            value=value,
            unit=unit,
            window_start_at=baseline[0].target_at,
            window_end_at=post[0].target_at,
            event_offset=_POST_OFFSET,
            published_at=observation.published_at,
            source_id=source_id,
            independence_group=provider_id,
            delay_class="realtime",
            payload_schema_ref=payload_schema_ref,
            attributes=attributes,
        )
        output.append(
            FactEnvelope(
                schema_version="fact-envelope.v1",
                fact_id=research_fact_instance_id(
                    evidence_id=candidate.evidence_id, payload_hash=payload_hash
                ),
                evidence_id=candidate.evidence_id,
                requirement_id=query.requirement_id,
                metric_family=metric_family,
                field=derived_field,
                instrument=instrument,
                venue=venue,
                value=value,
                unit=unit,
                window_start_at=baseline[0].target_at,
                window_end_at=post[0].target_at,
                event_offset=_POST_OFFSET,
                observed_at=observed_at,
                received_at=received_at,
                published_at=observation.published_at,
                source_id=source_id,
                independence_group=provider_id,
                quality="candidate",
                delay_class="realtime",
                payload_schema_ref=payload_schema_ref,
                payload_hash=payload_hash,
                attributes=attributes,
            )
        )
    return output


def _dedupe_facts(facts: Iterable[FactEnvelope]) -> list[FactEnvelope]:
    return list({item.fact_id: item for item in facts}.values())


def _canonical_payload(payload: CryptoEventWindowPayload) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("event_window_timestamp_must_be_aware")
    return value.astimezone(UTC)
