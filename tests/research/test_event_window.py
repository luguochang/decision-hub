from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    CryptoEventWindowObservation,
    CryptoEventWindowPayload,
    EventWatch,
    EventWindowCapture,
    EventWindowSample,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.event_watch import EventWatchService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.market.event_window import (
    CapabilitySnapshotWindowProvider,
    CryptoEventWindowArchive,
    CryptoEventWindowResearchAdapter,
    CryptoEventWindowSampler,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'event-window.sqlite3'}")
    database.create_all()
    return database


def _observation(
    provider_id: str,
    *,
    field: str = "price",
    value: str = "100",
    venue: str = "okx",
    metric_family: str = "crypto.spot",
) -> CryptoEventWindowObservation:
    return CryptoEventWindowObservation(
        provider_id=provider_id,
        source_id=f"{provider_id}-source",
        venue=venue,
        metric_family=metric_family,
        field=field,
        value=value,
        unit="usdt" if field in {"price", "mark_price", "index_price"} else "btc",
        source_url=AnyUrl(f"https://{provider_id}.example/market"),
        published_at=None,
    )


def _payload(
    event_id: str,
    offset: str,
    target_at: datetime,
    *,
    observations: list[CryptoEventWindowObservation],
) -> CryptoEventWindowPayload:
    return CryptoEventWindowPayload(
        schema_version="crypto-event-window-payload.v1",
        event_id=event_id,
        offset=offset,
        target_at=target_at,
        captured_at=target_at,
        observations=observations,
        failures=[],
    )


def _capture_sample(
    service: EventWatchService,
    archive: CryptoEventWindowArchive,
    sample_id: str,
    payload: CryptoEventWindowPayload,
) -> None:
    payload_ref, payload_hash = archive.write(payload)
    service.capture(
        sample_id,
        EventWindowCapture(
            schema_version="event-window-capture.v1",
            observed_at=payload.captured_at,
            received_at=payload.captured_at,
            provider_id="crypto-window-archive",
            payload_ref=payload_ref,
            payload_hash=payload_hash,
        ),
    )


def test_archive_is_content_addressed_atomic_and_idempotent(tmp_path: Path) -> None:
    archive = CryptoEventWindowArchive(tmp_path / "archive")
    payload = _payload(
        "event-archive",
        "t-5m",
        NOW,
        observations=[_observation("okx-public")],
    )

    first_ref, first_hash = archive.write(payload)
    second_ref, second_hash = archive.write(payload)

    assert first_ref == second_ref == f"crypto-window://{first_hash}"
    assert first_hash == second_hash
    assert list((tmp_path / "archive").glob("*.tmp")) == []
    assert archive.load(first_ref, expected_hash=first_hash) == payload


def test_archive_rejects_reference_and_payload_tampering(tmp_path: Path) -> None:
    archive = CryptoEventWindowArchive(tmp_path / "archive")
    payload = _payload("event-tamper", "t-5m", NOW, observations=[_observation("okx-public")])
    ref, payload_hash = archive.write(payload)

    with pytest.raises(ValueError, match="ref_invalid"):
        archive.load("file:///tmp/not-a-window")
    with pytest.raises(ValueError, match="hash_mismatch"):
        archive.load(ref, expected_hash=hashlib.sha256(b"other").hexdigest())
    (tmp_path / "archive" / f"{payload_hash}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash_mismatch"):
        archive.load(ref)


class _Provider:
    def __init__(self, provider_id: str, *, fail: bool = False) -> None:
        self.provider_id = provider_id
        self.fail = fail
        self.calls = 0

    async def capture(self, sample: EventWindowSample):
        self.calls += 1
        if self.fail:
            raise _ProviderError()
        return [_observation(self.provider_id, venue=self.provider_id.removesuffix("-public"))]


class _ProviderError(RuntimeError):
    error_code = "provider_timeout"
    retryable = True


class _MixedSnapshotAdapter:
    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        facts = [
            FactEnvelope(
                schema_version="fact-envelope.v1",
                fact_id=f"fact-{field}",
                evidence_id=f"evidence-{field}",
                requirement_id=query.requirement_id,
                metric_family="crypto.derivatives",
                field=field,
                instrument="BTCUSDT",
                venue="coinex",
                value="100",
                unit="usdt" if field == "spot_price" else "rate",
                window_start_at=None,
                window_end_at=None,
                event_offset=None,
                observed_at=NOW,
                received_at=NOW,
                published_at=None,
                source_id="coinex-public",
                independence_group="coinex",
                quality="candidate",
                delay_class="realtime",
                payload_schema_ref="coinex.public-market.v2",
                payload_hash="a" * 64,
                attributes={"provider_field": field},
            )
            for field in ("spot_price", "funding_rate")
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="coinex-public",
            evidence_candidates=[],
            facts=facts,
            cost_usd=0.0,
            completed_at=NOW,
        )


def test_snapshot_window_provider_reclassifies_mixed_spot_and_derivatives_fields() -> None:
    provider = CapabilitySnapshotWindowProvider(
        "coinex-public",
        _MixedSnapshotAdapter(),
        symbols=("BTC",),
        fields=("spot_price", "funding_rate"),
        clock=lambda: NOW,
    )
    sample = EventWindowSample.model_validate(
        {
            "schema_version": "event-window-sample.v1",
            "sample_id": "watch:t-5m",
            "watch_id": "watch",
            "event_id": "event-mixed",
            "offset": "t-5m",
            "target_at": NOW,
            "status": "due",
            "observed_at": None,
            "received_at": None,
            "provider_id": None,
            "payload_ref": None,
            "payload_hash": None,
            "error_code": None,
        }
    )

    observations = asyncio.run(provider.capture(sample))

    assert {(item.field, item.metric_family) for item in observations} == {
        ("price", "crypto.spot"),
        ("funding_rate", "crypto.derivatives"),
    }


def test_sampler_keeps_partial_provider_failure_and_is_replayable(tmp_path: Path) -> None:
    archive = CryptoEventWindowArchive(tmp_path / "archive")
    successful = _Provider("okx-public")
    failed = _Provider("coinex-public", fail=True)
    sampler = CryptoEventWindowSampler([successful, failed], archive, clock=lambda: NOW)
    sample = {
        "schema_version": "event-window-sample.v1",
        "sample_id": "watch:t-5m",
        "watch_id": "watch",
        "event_id": "event-sampler",
        "offset": "t-5m",
        "target_at": NOW - timedelta(minutes=1),
        "status": "due",
        "observed_at": None,
        "received_at": None,
        "provider_id": None,
        "payload_ref": None,
        "payload_hash": None,
        "error_code": None,
    }
    result = asyncio.run(sampler.capture(EventWindowSample.model_validate(sample)))

    assert result is not None
    assert result.provider_id == "crypto-window-archive"
    stored = archive.load(result.payload_ref, expected_hash=result.payload_hash)
    assert {item.provider_id for item in stored.observations} == {"okx-public"}
    assert stored.failures[0].provider_id == "coinex-public"
    assert successful.calls == failed.calls == 1


def _window_setup(tmp_path: Path):
    clock = [NOW]
    database = _database(tmp_path)
    watch_service = EventWatchService(database, clock=lambda: clock[0])
    scheduled = NOW + timedelta(minutes=30)
    watch = watch_service.ensure_watch(
        event_id="event-facts",
        source_id="calendar",
        event_family="central_bank_speech",
        scheduled_at=scheduled,
        window_offsets=("t-5m", "t+1m"),
    )
    archive = CryptoEventWindowArchive(tmp_path / "archive")
    for offset, value in (("t-5m", "100"), ("t+1m", "110")):
        sample = next(
            item for item in watch_service.list_samples(watch.watch_id) if item.offset == offset
        )
        clock[0] = sample.target_at
        watch_service.advance()
        _capture_sample(
            watch_service,
            archive,
            sample.sample_id,
            _payload(
                "event-facts",
                offset,
                sample.target_at,
                observations=[
                    _observation("okx-public", value=value),
                    _observation(
                        "okx-public",
                        field="open_interest",
                        value="1000" if offset == "t-5m" else "1100",
                        metric_family="crypto.derivatives",
                    ),
                ],
            ),
        )
    clock[0] = NOW + timedelta(hours=1)
    return clock, watch_service, archive, watch


def _query(watch: EventWatch, *, fields: list[str]) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery(
        schema_version="research-capability-query.v1",
        request_id="window-query",
        capability_id="market.crypto_derivatives",
        requirement_id="crypto_spot_confirmation",
        query="BTC event window",
        target_url=None,
        symbols=["BTC-USDT"],
        fields=fields,
        allowed_domains=[],
        max_results=20,
        max_cost_usd=0.0,
        research_session_id="window-session",
        round=1,
        mode="replay",
        observed_at=NOW + timedelta(hours=1),
        cutoff_at=NOW + timedelta(hours=1),
        event_id=watch.event_id,
        event_at=watch.scheduled_at,
        window_start_at=None,
        window_end_at=None,
        requested_event_offsets=["t-5m", "t+1m"],
    )


def test_research_adapter_projects_window_and_derived_return(tmp_path: Path) -> None:
    _clock, service, archive, watch = _window_setup(tmp_path)
    result = asyncio.run(
        CryptoEventWindowResearchAdapter(service, archive).execute(
            _query(watch, fields=["price", "open_interest", "event_return", "open_interest_delta"])
        )
    )

    assert len(result.evidence_candidates) == 2
    assert {(item.field, item.event_offset) for item in result.facts or []} >= {
        ("price", "t-5m"),
        ("price", "t+1m"),
        ("open_interest", "t-5m"),
        ("open_interest", "t+1m"),
        ("event_return", "t+1m"),
        ("open_interest_delta", "t+1m"),
    }
    derived = {
        item.field: item
        for item in result.facts or []
        if item.field in {"event_return", "open_interest_delta"}
    }
    assert derived["event_return"].value == "10.0"
    assert derived["open_interest_delta"].value == "10.0"
    assert derived["event_return"].window_start_at is not None
    assert derived["event_return"].window_end_at is not None


def test_research_adapter_does_not_substitute_current_snapshot_for_missing_baseline(
    tmp_path: Path,
) -> None:
    _clock, service, archive, watch = _window_setup(tmp_path)
    baseline = next(item for item in service.list_samples(watch.watch_id) if item.offset == "t-5m")
    with service.database.session() as session:
        from packages.kernel.decision_hub_kernel.persistence.db import EventWindowSampleRecord

        row = session.get(EventWindowSampleRecord, baseline.sample_id)
        assert row is not None
        row.status = "missing"
    result = asyncio.run(
        CryptoEventWindowResearchAdapter(service, archive).execute(
            _query(watch, fields=["price", "event_return"])
        )
    )
    assert all(item.field != "event_return" for item in result.facts or [])
    assert all(item.event_offset != "t-5m" for item in result.facts or [])


def test_research_adapter_fails_closed_on_payload_hash_mismatch(tmp_path: Path) -> None:
    _clock, service, archive, watch = _window_setup(tmp_path)
    sample = next(item for item in service.list_samples(watch.watch_id) if item.offset == "t-5m")
    assert sample.payload_ref is not None
    path = archive.root / f"{sample.payload_hash}.json"
    path.write_text("tampered", encoding="utf-8")

    with pytest.raises(Exception) as raised:
        asyncio.run(
            CryptoEventWindowResearchAdapter(service, archive).execute(
                _query(watch, fields=["price"])
            )
        )
    assert getattr(raised.value, "error_code", None) == "provider_window_payload_hash_mismatch"
