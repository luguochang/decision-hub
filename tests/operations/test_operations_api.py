from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    EvolutionJobCreate,
    RuntimeModeView,
    SourceManifest,
)
from packages.kernel.decision_hub_kernel.application.live_observation import (
    EvolutionJobService,
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.application.search import (
    SEARCH_INPUT_SCHEMA_REF,
    SEARCH_OUTPUT_SCHEMA_REF,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.query_views.operations import OperationsQueryService

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)


def test_operations_api_reports_real_api_and_absent_workers_without_fake_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.delenv("DECISION_HUB_LLM_CANARY_STATUS", raising=False)
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'operations-api.sqlite3'}")

    with TestClient(create_app(database, source_connectors=[])) as client:
        response = client.get("/v1/operations")

    assert response.status_code == 200
    body = response.json()
    services = {item["service_id"]: item for item in body["services"]}
    assert services["hub-api"]["status"] == "online"
    assert services["hub-api"]["heartbeat_at"] is not None
    assert services["hub-realtime-worker"]["status"] == "offline"
    assert services["hub-realtime-worker"]["instance_id"] is None
    assert services["hub-realtime-worker"]["heartbeat_at"] is None
    assert services["hub-evolution-worker"]["status"] == "offline"
    assert body["runtime"] == {
        "runtime_id": "fake",
        "runtime_version": "fake.v1",
        "mode": "fake",
        "provider_configured": False,
        "live_canary_status": "not_run",
        "model": None,
        "api_mode": None,
    }
    assert body["jobs"]["queued"] == 0
    assert body["recent_jobs"] == []


def test_operations_api_reports_manifest_sources_as_disabled_when_polling_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'sources-off.sqlite3'}")

    with TestClient(create_app(database, sources_enabled=False)) as client:
        body = client.get("/v1/operations").json()

    assert body["sources"]
    assert all(item["enabled"] is False for item in body["sources"])


def test_operations_query_assembles_heartbeat_source_capability_and_job(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'operations.sqlite3'}")
    database.create_all()
    source = SourceManifest(
        source_id="fed-rss",
        source_type="official_feed",
        version="1.0.0",
        authority_level="primary",
        allowed_domains=("federalreserve.gov",),
        enabled=True,
    )
    database.ensure_source_state(source)
    heartbeats = ServiceHeartbeatService(database, clock=lambda: NOW)
    heartbeats.beat(
        service_id="hub-realtime-worker",
        role="realtime_worker",
        instance_id="worker-1",
        version="0.1.0",
        mode="fake",
        interval_seconds=10,
        started_at=NOW,
    )
    workbench = WorkbenchAssetService(database)
    workbench.register_capability(
        CapabilityManifest(
            capability_id="search.web.primary",
            version="1.0.0",
            capability_type="tool",
            provider="fixture-search",
            license="owner-approved",
            input_schema_ref=SEARCH_INPUT_SCHEMA_REF,
            output_schema_ref=SEARCH_OUTPUT_SCHEMA_REF,
            permissions=["read_only", "network:https"],
            network_domains=["federalreserve.gov"],
            timeout_seconds=5,
            max_cost_usd=0.05,
            status="audited",
        )
    )
    workbench.set_capability_status("search.web.primary", "shadow")
    jobs = EvolutionJobService(database, clock=lambda: NOW)
    job = jobs.enqueue(
        EvolutionJobCreate(
            trigger_key="feedback:operations",
            trigger_type="feedback",
            domain_pack_ref="crypto_macro.v1",
            input_refs=["feedback:operations"],
            max_attempts=3,
        )
    )
    service = OperationsQueryService(
        database,
        runtime=RuntimeModeView(
            runtime_id="fake",
            runtime_version="fake.v1",
            mode="fake",
            provider_configured=False,
            live_canary_status="not_run",
            model=None,
            api_mode=None,
        ),
        source_manifests=[source],
        clock=lambda: NOW + timedelta(seconds=21),
    )

    overview = service.overview()

    services = {item.service_id: item for item in overview.services}
    assert services["hub-realtime-worker"].status == "stale"
    assert services["hub-api"].status == "offline"
    assert overview.sources[0].source_id == "fed-rss"
    assert overview.sources[0].enabled is True
    assert overview.capabilities[0].status == "shadow"
    assert overview.jobs.queued == 1
    assert overview.recent_jobs[0].job_id == job.job_id

    with TestClient(create_app(database, source_connectors=[])) as client:
        jobs_response = client.get("/v1/evolution/jobs")
    assert jobs_response.status_code == 200
    assert jobs_response.json()[0]["job_id"] == job.job_id
