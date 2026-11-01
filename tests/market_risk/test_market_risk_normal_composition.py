"""Private normal builders: no formal DB, HTTP, provider or model execution."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from apps.hub_worker.composition import build_realtime_worker, build_research_worker
from apps.hub_worker.market_case_context import MarketCaseContextProvider
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.persistence.market_case_records import (
    MarketComponentPointerRecord,
    MarketComponentVersionRecord,
    MarketExtensionRegistrationRecord,
)
from packages.kernel.decision_hub_kernel.persistence.path_forecast_records import (
    RiskShadowTrialRecord,
)
from packages.pilot_runtime import PilotSettings
from packages.runtime_adapters.dsh_runtime import (
    DshWebHostClient,
    DshWebHostConfig,
    DshWebResearchRuntime,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


@pytest.mark.asyncio
async def test_normal_builders_wire_all_consumers_and_bounded_independent_routes(
    tmp_path: Path, monkeypatch
):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'normal.sqlite3'}")
    database.create_all()
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "private-data"))
    monkeypatch.setenv("DECISION_HUB_RISK_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_SPOT_RESEARCH_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_MARKET_ENHANCEMENTS_ENABLED", "1")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_EXECUTION_MODE", "live")
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "0")
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT", raising=False)
    monkeypatch.delenv("DECISION_HUB_RETAINED_RECIPE_RECEIPT_SHA256", raising=False)
    network_calls = []

    def reject_network(request):
        network_calls.append(str(request.url))
        raise AssertionError("constructing the normal workers must not execute provider HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_network)) as client:
        runtime = DshWebResearchRuntime(
            DshSessionLinkService(database),
            DshWebHostClient(
                DshWebHostConfig(
                    base_url="http://127.0.0.1:9",
                    bridge_key="private-control-only",
                ),
                client=client,
            ),
        )
        realtime = build_realtime_worker(
            database,
            runtime=FakeAgentRuntime(),
            settings=PilotSettings(sources_enabled=False, market_enabled=False),
        )
        research = build_research_worker(
            database,
            runtime=runtime,
            allowed_capabilities=("replay.research", "spot_market_facts"),
        )
        scheduler = realtime.scheduler
        assert scheduler.risk_data.component_consumer is scheduler.risk_monitor.component_consumer
        assert (
            scheduler.risk_shadow_tick.__self__.ledger.component_consumer
            is scheduler.risk_data.component_consumer
        )
        assert scheduler.risk_research_tick is not None
        assert scheduler.risk_memory_tick is not None
        assert scheduler.risk_enhancement_tick is not None
        assert set(
            scheduler.risk_data.component_consumer.registry.original_reader.protocol_handlers
        ) == {
            "source_parser",
            "feature",
            "monitor_rule",
            "research_recipe",
        }
        path_ledger = scheduler.risk_shadow_tick.__self__.ledger
        alarm_service = path_ledger.forecast_alarm_publisher.__self__
        assert alarm_service.database is database
        assert path_ledger.forecast_alarm_score_reader.__self__ is alarm_service
        assert path_ledger.forecast_alarm_original_reader.__self__ is alarm_service
        assert research.worker.spot_prepared_factory.component_consumer is not None
        assert isinstance(
            research.worker.spot_prepared_factory.context_inputs_provider, MarketCaseContextProvider
        )
        assert research.worker.spot_prepared_factory.pack_root.name == "1.2.0"
        assert research.worker.prepared_factory.pack_root.name == "1.1.0"
        assert network_calls == []
        with database.session() as session:
            assert (
                session.scalar(select(func.count()).select_from(MarketComponentVersionRecord)) == 12
            )
            assert (
                session.scalar(select(func.count()).select_from(MarketComponentPointerRecord)) == 0
            )
            assert (
                session.scalar(select(func.count()).select_from(MarketExtensionRegistrationRecord))
                == 6
            )
            trial = session.scalars(select(RiskShadowTrialRecord)).one()
            assert trial.request_id == "market-risk-path-shadow.v1-first-28-days"
        # Building the second worker must neither approve versions nor duplicate trials.
        await research.close()
        from apps.hub_api.main import create_app

        client = TestClient(create_app(database))
        scope = "crypto:spot:okx:BTC-USDT:trade:1m"
        current = client.get(f"/v1/risk/scopes/{scope}/path-trial")
        assert current.status_code == 200, current.text
        assert current.json()["trial"]["protocol_version"] == "path-shadow-24h.v2"
        enhancements = client.get(f"/v1/risk/scopes/{scope}/enhancements")
        assert enhancements.status_code == 200, enhancements.text
        assert len(enhancements.json()["routes"]) == 3
        alarms = client.get(
            f"/v1/risk/scopes/{scope}/forecast-alarms?owner_id=owner",
            headers={"X-Owner-Id": "owner"},
        )
        assert alarms.status_code == 200, alarms.text
        assert alarms.json()["items"] == []
        assert (
            client.get(f"/v1/risk/scopes/{scope}/forecast-alarms?owner_id=owner").status_code == 403
        )
    database.engine.dispose()
