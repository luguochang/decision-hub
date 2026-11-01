from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.application.source_ingest import SourceIngestionService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollResult
from packages.source_adapters.registry import SourceRegistry


class SequencedSource:
    manifest = SourceManifest(
        source_id="poll-ledger-fixture",
        source_type="official_feed",
        version="fixture.v1",
        authority_level="official",
    )

    def __init__(self) -> None:
        self.calls = 0

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("source_rate_limited")
        text = "first item"
        now = datetime.now(UTC)
        envelopes = (
            (
                TextEnvelope(
                    source_id=self.manifest.source_id,
                    source_type=SourceType.official_feed,
                    observed_at=now,
                    received_at=now,
                    raw_text=text,
                    language="en",
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                ),
            )
            if self.calls == 1
            else ()
        )
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after=f"cursor-{self.calls}",
            envelopes=envelopes,
            fetched_at=now,
        )


def test_poll_attempts_survive_restart_and_read_only_api(tmp_path: Path) -> None:
    db_path = tmp_path / "source-attempts.sqlite3"
    db_url = f"sqlite+pysqlite:///{db_path}"
    database = Database(db_url)
    database.initialize()
    source = SequencedSource()
    registry = SourceRegistry()
    registry.register(source)
    ingestion = SourceIngestionService(database, registry)

    assert asyncio.run(ingestion.poll_due(source.manifest.source_id)).accepted_count == 1
    assert asyncio.run(ingestion.poll_due(source.manifest.source_id)).skipped is True
    assert len(ingestion.list_attempts(source.manifest.source_id)) == 1
    assert asyncio.run(ingestion.poll_once(source.manifest.source_id)).accepted_count == 0
    assert (
        asyncio.run(ingestion.poll_once(source.manifest.source_id)).error_code
        == "source_rate_limited"
    )
    database.engine.dispose()

    reopened = Database(db_url)
    app = create_app(database=reopened, source_connectors=[source], sources_enabled=True)
    with TestClient(app) as client:
        response = client.get(f"/v1/sources/{source.manifest.source_id}/poll-attempts")
        assert response.status_code == 200
        attempts = response.json()
        assert [item["status"] for item in attempts] == ["failed", "empty", "succeeded"]
        assert [item["trigger_type"] for item in attempts] == [
            "manual",
            "manual",
            "scheduled",
        ]
        assert [item["received_count"] for item in attempts] == [0, 0, 1]
        assert [item["accepted_count"] for item in attempts] == [0, 0, 1]
        assert attempts[0]["error_code"] == "source_rate_limited"
        assert attempts[0]["cursor_before"] == "cursor-2"
        assert attempts[0]["cursor_after"] == "cursor-2"
        assert attempts[1]["cursor_after"] == "cursor-2"
        assert all(item["attempt_id"] for item in attempts)
        assert all(item["started_at"] and item["completed_at"] for item in attempts)
        assert all(item["latency_ms"] is not None for item in attempts)
        assert client.get(
            f"/v1/sources/{source.manifest.source_id}/poll-attempts?limit=1&offset=1"
        ).json() == attempts[1:2]
        assert client.get("/v1/sources/unknown/poll-attempts").status_code == 404
    reopened.engine.dispose()
