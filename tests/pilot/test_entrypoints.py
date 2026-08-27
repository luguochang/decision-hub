from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.persistence.db import Database


def test_api_readiness_is_redacted_and_reports_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'readiness.sqlite3'}")
    app = create_app(database)

    response = TestClient(app).get("/v1/pilot/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["schema_version"] == "pilot-readiness.v1"
    assert all("OPENAI_API_KEY" not in str(check) for check in payload["checks"])
    assert all("password" not in str(check).lower() for check in payload["checks"])
