from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.sources import (
    SourceManifest,
    SourcePollFailure,
    SourcePollResult,
)
from packages.source_adapters.official_feeds.diagnostics import response_snapshot
from packages.source_adapters.registry import SourceRegistry


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "source_authentication_required"), (403, "source_access_denied"),
     (429, "source_rate_limited"), (503, "source_unavailable")],
)
def test_http_error_keeps_original_receipt(status: int, code: str) -> None:
    with pytest.raises(SourcePollFailure) as caught:
        response_snapshot(status, b"unavailable", "fixture.v1")
    assert caught.value.error_code == code
    assert caught.value.snapshot.http_status == status
    assert caught.value.snapshot.parse_status == "not_attempted"
    assert caught.value.snapshot.coverage_status == "unknown"
    assert len(caught.value.snapshot.content_hash) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "delay"), [(401, 86400), (403, 86400), (429, 120), (503, 120)]
)
async def test_access_schedule_survives_restart_and_success_resumes_normal_polling(
    tmp_path: Path, status: int, delay: int,
) -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)

    class Source:
        manifest = SourceManifest(
            source_id="access-fixture", source_type="official_feed", version="fixture.v1",
            poll_interval_seconds=60,
        )
        http_status = status
        calls = 0

        async def poll(self, cursor: str | None = None) -> SourcePollResult:
            self.calls += 1
            snapshot = response_snapshot(self.http_status, b"response", "fixture.v1")
            return SourcePollResult(
                source_id=self.manifest.source_id, cursor_before=cursor, cursor_after="unchanged",
                fetched_at=now, content_snapshot=snapshot,
            )

    source = Source()
    registry = SourceRegistry()
    registry.register(source)
    url = f"sqlite+pysqlite:///{tmp_path / 'access.sqlite3'}"
    database = Database(url)
    database.create_all()
    service = SourceIngestionService(database, registry, clock=lambda: now)
    result = await service.poll_due(source.manifest.source_id)
    assert result.error_code is not None
    failed_receipt = service.list_attempts(source.manifest.source_id)[0]
    assert service.health(source.manifest.source_id).next_poll_at == now + timedelta(seconds=delay)
    database.engine.dispose()

    database = Database(url)
    service = SourceIngestionService(database, registry, clock=lambda: now)
    now += timedelta(seconds=delay - 1)
    assert (await service.poll_due(source.manifest.source_id)).skipped
    assert source.calls == 1
    assert service.list_attempts(source.manifest.source_id) == [failed_receipt]
    assert service.health(source.manifest.source_id).consecutive_failures == 1

    now += timedelta(seconds=1)
    source.http_status = 200
    assert (await service.poll_due(source.manifest.source_id)).error_code is None
    health = service.health(source.manifest.source_id)
    assert health.status == "healthy" and health.error_code is None
    assert health.consecutive_failures == 0
    assert health.next_poll_at == now + timedelta(seconds=60)
    assert source.calls == 2
    assert service.list_attempts(source.manifest.source_id)[1] == failed_receipt
    database.engine.dispose()
