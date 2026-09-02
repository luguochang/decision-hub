# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.hub_api.main import create_app
from apps.hub_worker.composition import (
    build_evolution_worker,
    build_realtime_worker,
    build_research_worker,
)
from apps.hub_worker.main import _registry
from packages.kernel.decision_hub_kernel.application.evolution import EvolutionAssetService
from packages.kernel.decision_hub_kernel.application.live_observation import (
    ServiceHeartbeatService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.dsh_runtime import DshWebResearchRuntime
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.replay_runtime import ReplayResearchRuntime
from packages.source_adapters.registry import SourceRegistry


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'processes.sqlite3'}")
    database.create_all()
    return database


def test_release_baseline_bootstrap_is_idempotent_and_never_repoints(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    assets = EvolutionAssetService(database)

    first = assets.ensure_release_baseline("crypto_macro.v1")
    second = assets.ensure_release_baseline("crypto_macro.v1", version="ignored.v2")

    assert second == first
    assert second.generation == 1
    assert assets.get_candidate(first.candidate_id).source == "release"  # type: ignore[union-attr]
    overview = assets.overview()
    assert len(overview.pointers) == 1
    assert len(overview.candidates) == 1


def test_worker_composition_roots_tick_independently_and_write_heartbeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    database = _database(tmp_path)
    realtime = build_realtime_worker(
        database,
        registry=SourceRegistry(),
        runtime=FakeAgentRuntime(),
        settings=PilotSettings(),
        heartbeat_interval_seconds=1,
    )
    evolution = build_evolution_worker(
        database,
        runtime=FakeAgentRuntime(),
        heartbeat_interval_seconds=1,
    )

    realtime_report = asyncio.run(realtime.tick())
    evolution_report = asyncio.run(evolution.tick())

    assert realtime_report.runs_started == 0
    assert evolution_report is None
    services = {item.service_id: item for item in ServiceHeartbeatService(database).list_views()}
    assert services["hub-realtime-worker"].status == "online"
    assert services["hub-evolution-worker"].status == "online"
    assert services["hub-realtime-worker"].mode == "fake"
    assert services["hub-evolution-worker"].mode == "fake"


def test_api_lifespan_writes_a_distinct_durable_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.setenv("DECISION_HUB_HEARTBEAT_INTERVAL", "1")
    database = _database(tmp_path)

    with TestClient(create_app(database)) as client:
        assert client.get("/health/ready").status_code == 200
        services = {
            item.service_id: item for item in ServiceHeartbeatService(database).list_views()
        }
        assert services["hub-api"].role == "api"
        assert services["hub-api"].status == "online"
        assert services["hub-api"].mode == "fake"


def test_product_sources_require_explicit_calendar_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", raising=False)
    settings = PilotSettings(sources_enabled=True)

    default_registry = _registry(settings)
    with TestClient(create_app(_database(tmp_path))) as client:
        api_source_ids = {
            item["manifest"]["source_id"] for item in client.get("/v1/sources").json()
        }

    assert "bls-calendar" not in {item.source_id for item in default_registry.manifests()}
    assert "bls-calendar" not in api_source_ids

    monkeypatch.setenv("DECISION_HUB_CALENDAR_DISCOVERY_ENABLED", "1")
    enabled_registry = _registry(settings)
    assert "bls-calendar" in {item.source_id for item in enabled_registry.manifests()}


def test_research_worker_selects_explicit_replay_runtime_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "packs"
        / "crypto_macro"
        / "fixtures"
        / "research-worker-replay.json"
    )
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "replay")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME_FIXTURE", str(fixture))
    database = _database(tmp_path)

    worker = build_research_worker(database)

    assert isinstance(worker.worker.runtime, ReplayResearchRuntime)
    assert worker.mode == "replay"


def test_research_worker_selects_explicit_dsh_web_runtime_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "dsh-web")
    monkeypatch.setenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3080")
    monkeypatch.setenv("DECISION_HUB_DSH_HOST_SECRET", "host-secret")
    database = _database(tmp_path)

    worker = build_research_worker(database)

    assert isinstance(worker.worker.runtime, DshWebResearchRuntime)
    assert worker.mode == "provider"
    assert worker.worker.factory.execution_mode == "live"


def test_dsh_web_worker_accepts_explicit_replay_evidence_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "dsh-web")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_EXECUTION_MODE", "replay")
    monkeypatch.setenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3080")
    monkeypatch.setenv("DECISION_HUB_DSH_HOST_SECRET", "host-secret")

    worker = build_research_worker(_database(tmp_path))

    assert isinstance(worker.worker.runtime, DshWebResearchRuntime)
    assert worker.worker.factory.execution_mode == "replay"


def test_research_worker_rejects_unknown_execution_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", "dsh-web")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_EXECUTION_MODE", "diagnostic")
    monkeypatch.setenv("DECISION_HUB_DSH_WEB_URL", "http://127.0.0.1:3080")
    monkeypatch.setenv("DECISION_HUB_DSH_HOST_SECRET", "host-secret")

    with pytest.raises(ValueError, match="research_execution_mode_invalid"):
        build_research_worker(_database(tmp_path))


@pytest.mark.parametrize(
    ("runtime_mode", "fixture", "error_code"),
    [
        ("replay", None, "research_runtime_replay_fixture_required"),
        ("unsupported", None, "research_runtime_mode_invalid"),
    ],
)
def test_research_worker_runtime_configuration_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_mode: str,
    fixture: str | None,
    error_code: str,
) -> None:
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME", runtime_mode)
    if fixture is None:
        monkeypatch.delenv("DECISION_HUB_RESEARCH_RUNTIME_FIXTURE", raising=False)
    else:
        monkeypatch.setenv("DECISION_HUB_RESEARCH_RUNTIME_FIXTURE", fixture)

    with pytest.raises(ValueError, match=error_code):
        build_research_worker(_database(tmp_path))
