from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest
from packages.source_adapters.official_feeds import OfficialHtmlCalendarSource
from packages.source_adapters.registry import SourceRegistry


@pytest.mark.asyncio
async def test_http_parse_coverage_and_new_count_are_independent_and_durable(
    tmp_path: Path,
) -> None:
    year = datetime.now(UTC).year + 2
    status, body = 403, "Access denied"

    async def fetcher(_url: str) -> tuple[int, str]:
        return status, body

    registry = SourceRegistry()
    registry.register(
        OfficialHtmlCalendarSource(
            SourceManifest(source_id="fed-calendar", source_type="official_feed", version="v2"),
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            family="fomc",
            event_title="Policy meeting",
            fetcher=fetcher,
        )
    )
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'coverage.sqlite3'}")
    database.create_all()
    service = SourceIngestionService(database, registry)
    assert (await service.poll_once("fed-calendar")).error_code == "source_access_denied"
    status, body = 200, "<html>Structure changed</html>"
    assert (await service.poll_once("fed-calendar")).error_code == "source_parse_error"
    body = "<h2>2020 FOMC Meetings</h2><ul><li>December 20</li></ul>"
    assert (await service.poll_once("fed-calendar")).error_code is None
    body = f"<h2>{year} FOMC Meetings</h2><ul><li>December 20</li></ul>"
    assert (await service.poll_once("fed-calendar")).accepted_count == 1
    assert (await service.poll_once("fed-calendar")).accepted_count == 0

    # Read via a new service instance to prove projection survives restarts.
    rows = SourceIngestionService(database, registry).list_attempts("fed-calendar")
    unchanged, added, old_only, invalid, rejected = [row.content_snapshot for row in rows]
    assert all([unchanged, added, old_only, invalid, rejected])
    assert unchanged == added
    assert unchanged.upcoming_count == 1
    assert rows[0].received_count == 0 and rows[0].status == "empty"
    assert old_only.http_status == 200 and old_only.parse_status == "valid"
    assert old_only.parsed_count == 1 and old_only.coverage_status == "no_upcoming"
    assert invalid.http_status == 200 and invalid.parse_status == "invalid"
    assert rejected.http_status == 403 and rejected.parse_status == "not_attempted"
    assert len(rejected.content_hash) == 64
