from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import (
    SourceManifest,
    SourceType,
    TextEnvelope,
)
from packages.kernel.decision_hub_kernel.application.source_ingest import (
    SourceIngestionService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord
from packages.kernel.decision_hub_kernel.ports.sources import SourcePollResult
from packages.provider_adapters.research.discovery import CryptoMacroDiscoveryPolicy
from packages.source_adapters.official_feeds import OfficialFeedSource
from packages.source_adapters.registry import SourceRegistry

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
PACK_ROOT = Path(__file__).resolve().parents[2] / "packs" / "crypto_macro"


class DiscoverySource:
    manifest = SourceManifest(
        source_id="fed-press",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
    )

    def __init__(self, text: str) -> None:
        self.text = text

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        envelope = TextEnvelope(
            source_id=self.manifest.source_id,
            source_type=SourceType.official_feed,
            observed_at=NOW,
            published_at=NOW,
            received_at=NOW,
            raw_text=self.text,
            language="en",
            event_hint="fed-press",
            content_hash=hashlib.sha256(self.text.encode()).hexdigest(),
        )
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after="one",
            envelopes=(envelope,),
            fetched_at=NOW,
        )


def test_high_impact_discovery_queues_baseline_and_research_but_executes_only_baseline(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'discovery.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(
        DiscoverySource(
            "Federal Reserve chair speech says inflation risks remain elevated "
            "and rates may stay higher."
        )
    )
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT),
        clock=lambda: NOW,
    )

    first = asyncio.run(ingestion.poll_once("fed-press"))
    second = asyncio.run(ingestion.poll_once("fed-press"))

    assert len(first.run_targets) == 1
    assert first.run_targets[0].strategy_version == "baseline.v1"
    assert len(second.run_targets) == 1
    assert second.run_targets[0].run_id == first.run_targets[0].run_id
    with database.session() as session:
        runs = session.query(RunRecord).order_by(RunRecord.strategy_version).all()
    assert [item.strategy_version for item in runs] == ["baseline.v1", "research.v1"]
    assert len({item.idempotency_key for item in runs}) == 2


def test_low_impact_discovery_preserves_baseline_compatibility(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'low-impact.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(DiscoverySource("Routine administrative notice."))
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT),
        clock=lambda: NOW,
    )

    result = asyncio.run(ingestion.poll_once("fed-press"))

    assert len(result.run_targets) == 1
    with database.session() as session:
        runs = session.query(RunRecord).all()
    assert [item.strategy_version for item in runs] == ["baseline.v1"]


@pytest.mark.parametrize(
    "title",
    [
        "Federal Reserve Board issues enforcement action with former bank employee",
        "Federal Reserve Board approves application by regional bank",
        "Federal Reserve Board announces community service award recipients",
    ],
)
def test_fed_site_boilerplate_cannot_promote_routine_title_to_research(
    tmp_path: Path, title: str
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'fed-boilerplate.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(
        DiscoverySource(
            f"{title}\nFederal Reserve Board of Governors monetary policy inflation"
        )
    )
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT),
        clock=lambda: NOW,
    )

    asyncio.run(ingestion.poll_once("fed-press"))

    with database.session() as session:
        runs = session.query(RunRecord).all()
    assert [item.strategy_version for item in runs] == ["baseline.v1"]


@pytest.mark.parametrize(
    "title",
    [
        "Chair Powell discusses the economic outlook and monetary policy",
        "Governor Warsh speaks about inflation and interest rates",
        "Federal Open Market Committee releases FOMC statement",
    ],
)
def test_explicit_high_impact_title_promotes_one_idempotent_research_run(
    tmp_path: Path, title: str
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'high-impact-title.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(DiscoverySource(f"{title}\nRoutine site footer"))
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT),
        clock=lambda: NOW,
    )

    asyncio.run(ingestion.poll_once("fed-press"))
    asyncio.run(ingestion.poll_once("fed-press"))

    with database.session() as session:
        runs = session.query(RunRecord).order_by(RunRecord.strategy_version).all()
    assert [item.strategy_version for item in runs] == ["baseline.v1", "research.v1"]
    assert (
        next(item for item in runs if item.strategy_version == "research.v1").admission_origin
        == "automatic"
    )


def test_calendar_observation_never_starts_immediate_research() -> None:
    policy = CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT)
    manifest = SourceManifest(
        source_id="bls-calendar",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
    )
    text = "Consumer Price Index (scheduled: 2026-09-10T12:30:00+00:00)"
    envelope = TextEnvelope(
        source_id="bls-calendar",
        source_type=SourceType.official_feed,
        observed_at=NOW,
        received_at=NOW,
        raw_text=text,
        language="en",
        event_hint="bls-cpi-2026-09",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )

    assert policy(manifest, envelope) == ("baseline.v1",)


def test_fresh_official_feed_bootstrap_creates_no_observation_or_run(
    tmp_path: Path,
) -> None:
    body = """<rss><channel>
      <item><guid>old-1</guid><title>Powell discusses monetary policy</title>
      <pubDate>2026-08-29T10:00:00Z</pubDate></item>
      <item><guid>old-2</guid><title>FOMC statement</title>
      <pubDate>2026-08-29T11:00:00Z</pubDate></item>
    </channel></rss>"""

    async def fetcher(_url: str) -> tuple[int, str]:
        return 200, body

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'fresh-bootstrap.sqlite3'}")
    database.create_all()
    registry = SourceRegistry()
    registry.register(
        OfficialFeedSource(
            SourceManifest(
                source_id="fed-press",
                source_type="official_feed",
                version="fixture.v1",
                authority_level="official",
                allowed_domains=("federalreserve.gov",),
            ),
            "https://www.federalreserve.gov/feeds/press_all.xml",
            fetcher=fetcher,
            bootstrap_latest=True,
        )
    )
    ingestion = SourceIngestionService(
        database,
        registry,
        strategy_selector=CryptoMacroDiscoveryPolicy.from_pack(PACK_ROOT),
        clock=lambda: NOW,
    )

    result = asyncio.run(ingestion.poll_once("fed-press"))

    assert result.accepted_count == 0
    assert result.run_targets == ()
    assert result.cursor is not None and result.cursor.endswith("|old-2")
    with database.session() as session:
        assert session.query(RunRecord).count() == 0
