# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import cast

import pytest
from mcp_types import CallToolResult

from tools.canary import run_research_fact_capabilities_canary as fact_canary


class _PassingFactServer:
    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> CallToolResult:
        assert name == "research_capability_execute"
        capability_id = str(arguments["capability_id"])
        return CallToolResult(
            content=[],
            structured_content={
                "provider": f"test:{capability_id}",
                "cost_usd": 0.0,
                "evidence_candidates": [
                    {
                        "source_url": "https://www.federalreserve.gov/newsevents.htm",
                        "authority": "official",
                        "observed_at": datetime.now(UTC).isoformat(),
                    }
                ],
                "facts": [
                    {
                        "metric_family": "event.identity",
                        "delay_class": "realtime",
                        "event_offset": None,
                    }
                ],
                "provider_attempts": [],
            },
        )


def test_fact_canary_disables_durable_progress_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str | None] = {}

    def create_server() -> _PassingFactServer:
        observed["durable_progress"] = os.getenv(
            "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
        )
        observed["capabilities"] = os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES")
        return _PassingFactServer()

    monkeypatch.setattr(fact_canary, "create_server", create_server)
    monkeypatch.setenv("DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY", "1")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "owner-value")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_CAPABILITIES", "owner-capability")

    result = asyncio.run(fact_canary._run())

    assert result["status"] == "passed"
    assert result["semantic_scope"] == "current_snapshot_adapter_health_only"
    cases = cast(list[dict[str, object]], result["cases"])
    assert len(cases) == 5
    assert all(item["fact_count"] == 1 for item in cases)
    assert observed == {
        "durable_progress": "0",
        "capabilities": fact_canary.CAPABILITIES,
    }
    assert os.getenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS") == "owner-value"
    assert os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES") == "owner-capability"


def test_fact_canary_restores_environment_after_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def create_server() -> _PassingFactServer:
        assert os.getenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS") == "0"
        raise RuntimeError("server creation failed")

    monkeypatch.setattr(fact_canary, "create_server", create_server)
    monkeypatch.setenv("DECISION_HUB_FACT_CAPABILITIES_LIVE_CANARY", "1")
    monkeypatch.delenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", raising=False)
    monkeypatch.delenv("DECISION_HUB_RESEARCH_CAPABILITIES", raising=False)

    with pytest.raises(RuntimeError, match="server creation failed"):
        asyncio.run(fact_canary._run())

    assert "DECISION_HUB_RESEARCH_DURABLE_PROGRESS" not in os.environ
    assert "DECISION_HUB_RESEARCH_CAPABILITIES" not in os.environ
