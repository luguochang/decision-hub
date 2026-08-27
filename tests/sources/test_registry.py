from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    ObservationRecord,
    RunRecord,
)
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceManifest,
    SourcePollResult,
)
from packages.source_adapters.registry import SourceRegistry


def _envelope(text: str, *, revision_of: str | None = None) -> TextEnvelope:
    now = datetime(2026, 8, 27, 1, 0, tzinfo=UTC)
    return TextEnvelope(
        source_id="fixture-feed",
        source_type=SourceType.official_feed,
        observed_at=now,
        received_at=now,
        raw_text=text,
        language="en",
        revision_of=revision_of,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


class FixtureSource:
    manifest = SourceManifest(
        source_id="fixture-feed",
        source_type="official_feed",
        version="fixture.v1",
        capabilities=("poll", "revision"),
        authority_level="official",
    )

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("source_rate_limited")
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="2",
            envelopes=(_envelope("item one"), _envelope("item two", revision_of="obs_old")),
            fetched_at=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
        )


def test_registry_commits_cursor_only_after_admission_and_deduplicates(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'sources.sqlite3'}")
    database.create_all()
    source = FixtureSource()
    registry = SourceRegistry()
    registry.register(source)
    def clock() -> datetime:
        return datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(
        database, registry, admission=AdmissionService(database), clock=clock
    )

    first = asyncio.run(ingestion.poll_once("fixture-feed"))
    second = asyncio.run(ingestion.poll_once("fixture-feed"))

    assert first.accepted_count == 2
    assert first.duplicate_count == 0
    assert second.accepted_count == 0
    assert second.duplicate_count == 2
    assert ingestion.health("fixture-feed").cursor == "2"
    assert ingestion.health("fixture-feed").status == "healthy"
    with database.session() as session:
        assert session.query(ObservationRecord).count() == 2
        assert (
            session.query(ObservationRecord)
            .filter(ObservationRecord.revision_of.is_not(None))
            .count()
            == 1
        )


def test_registry_failure_preserves_cursor_and_records_health(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'failure.sqlite3'}")
    database.create_all()
    source = FixtureSource()
    registry = SourceRegistry()
    registry.register(source)
    def clock() -> datetime:
        return datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(
        database, registry, admission=AdmissionService(database), clock=clock
    )
    asyncio.run(ingestion.poll_once("fixture-feed"))
    source.fail = True

    result = asyncio.run(ingestion.poll_once("fixture-feed"))

    assert result.accepted_count == 0
    health = ingestion.health("fixture-feed")
    assert health.cursor == "2"
    assert health.status == "degraded"
    assert health.error_code == "source_rate_limited"
    assert health.consecutive_failures == 1
    assert health.next_poll_at is not None


class InvalidHashSource(FixtureSource):
    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        envelope = _envelope("valid text").model_copy(update={"content_hash": "0" * 64})
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="invalid",
            envelopes=(envelope,),
            fetched_at=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
        )


def test_invalid_source_payload_does_not_advance_cursor_or_create_run(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'invalid.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(InvalidHashSource())
    ingestion = SourceIngestionService(
        database,
        registry,
        clock=lambda: datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC),
    )
    result = asyncio.run(ingestion.poll_once("fixture-feed"))
    assert result.error_code == "source_invalid_payload"
    assert ingestion.health("fixture-feed").cursor is None
    assert result.run_targets == ()
    with database.session() as session:
        assert session.query(ObservationRecord).count() == 0
        assert session.query(RunRecord).count() == 0


class SingleEnvelopeSource:
    def __init__(self, manifest: SourceManifest, envelope: TextEnvelope) -> None:
        self.manifest = manifest
        self.envelope = envelope

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="next",
            envelopes=(self.envelope,),
            fetched_at=datetime(2026, 8, 27, 1, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("manifest", "envelope"),
    [
        (
            FixtureSource.manifest,
            _envelope("wrong source").model_copy(update={"source_id": "other-source"}),
        ),
        (
            FixtureSource.manifest,
            _envelope("wrong type").model_copy(update={"source_type": SourceType.manual}),
        ),
        (
            SourceManifest(
                source_id="fixture-feed",
                source_type="official_feed",
                version="fixture.v1",
                authority_level="official",
                allowed_domains=("federalreserve.gov",),
            ),
            _envelope("wrong domain").model_copy(
                update={"source_url": "https://untrusted.example.test/item"}
            ),
        ),
        (
            FixtureSource.manifest,
            _envelope("future received").model_copy(
                update={"received_at": datetime(2026, 8, 27, 1, 2, tzinfo=UTC)}
            ),
        ),
    ],
)
def test_source_contract_mismatch_does_not_advance_cursor(
    tmp_path: Path, manifest: SourceManifest, envelope: TextEnvelope
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'contract.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(SingleEnvelopeSource(manifest, envelope))
    ingestion = SourceIngestionService(
        database,
        registry,
        clock=lambda: datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC),
    )

    result = asyncio.run(ingestion.poll_once(manifest.source_id))

    assert result.error_code == "source_invalid_payload"
    assert ingestion.health(manifest.source_id).cursor is None
    with database.session() as session:
        assert session.query(ObservationRecord).count() == 0
        assert session.query(RunRecord).count() == 0


def test_poll_due_uses_durable_schedule_without_calling_source_again(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'schedule.sqlite3'}")
    database.create_all()
    source = FixtureSource()
    registry = SourceRegistry()
    registry.register(source)
    now = datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(database, registry, clock=lambda: now)

    asyncio.run(ingestion.poll_once("fixture-feed"))
    result = asyncio.run(ingestion.poll_due("fixture-feed"))

    assert result.skipped is True
    assert source.calls == 1


class DisabledSource(FixtureSource):
    manifest = SourceManifest(
        source_id="disabled-fixture",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
        enabled=False,
    )

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        raise AssertionError("disabled source must not be polled")


def test_disabled_source_is_observable_without_degrading_product_health(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'disabled-source.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(DisabledSource())
    now = datetime(2026, 8, 27, 1, 0, 1, tzinfo=UTC)
    ingestion = SourceIngestionService(database, registry, clock=lambda: now)

    result = asyncio.run(ingestion.poll_once("disabled-fixture"))
    health = ingestion.health("disabled-fixture")

    assert result.error_code == "source_disabled"
    assert health.status == "disabled"
    assert health.error_code == "source_disabled"
    assert health.consecutive_failures == 0
    assert health.next_poll_at == now + timedelta(seconds=60)
