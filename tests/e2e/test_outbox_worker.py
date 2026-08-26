from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.application.outbox import OutboxService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


def test_local_outbox_is_durable_and_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'outbox.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FakeAgentRuntime())
    asyncio.run(service.submit_and_run(ObservationCreate(text="Powell says higher for longer.")))
    outbox = OutboxService(database)
    assert outbox.drain_local() == 1
    assert outbox.drain_local() == 0
    notifications = tmp_path / "data" / "exports" / "notifications.jsonl"
    assert notifications.read_text().count("artifact_id") == 1
