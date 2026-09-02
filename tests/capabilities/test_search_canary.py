# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from mcp_types import CallToolResult

from tools.canary import run_responses_web_search_canary as search_canary
from tools.canary.run_responses_web_search_canary import normalize_canary_failure


class _PassingSearchServer:
    async def call_tool(
        self, name: str, arguments: dict[str, object]
    ) -> CallToolResult:
        assert name == "research_capability_execute"
        assert arguments["capability_id"] == "web.search"
        return CallToolResult(
            content=[],
            structured_content={
                "provider": "test-search",
                "cost_usd": 0.01,
                "evidence_candidates": [
                    {
                        "source_url": "https://www.federalreserve.gov/newsevents.htm",
                        "authority": "search_derived",
                        "observed_at": datetime.now(UTC).isoformat(),
                    }
                ],
            },
        )


def test_search_canary_disables_durable_progress_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str | None] = {}

    def create_server() -> _PassingSearchServer:
        observed["durable_progress"] = os.getenv(
            "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
        )
        observed["capabilities"] = os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES")
        return _PassingSearchServer()

    monkeypatch.setattr(search_canary, "create_server", create_server)
    monkeypatch.setenv("DECISION_HUB_SEARCH_LIVE_CANARY", "1")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "owner-value")
    monkeypatch.setenv("DECISION_HUB_RESEARCH_CAPABILITIES", "owner-capability")

    result = asyncio.run(search_canary._run())

    assert result["status"] == "passed"
    assert observed == {
        "durable_progress": "0",
        "capabilities": "web.search",
    }
    assert os.getenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS") == "owner-value"
    assert os.getenv("DECISION_HUB_RESEARCH_CAPABILITIES") == "owner-capability"


def test_search_canary_restores_environment_after_server_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str | None] = {}

    def create_server() -> _PassingSearchServer:
        observed["durable_progress"] = os.getenv(
            "DECISION_HUB_RESEARCH_DURABLE_PROGRESS"
        )
        raise RuntimeError("server creation failed")

    monkeypatch.setattr(search_canary, "create_server", create_server)
    monkeypatch.setenv("DECISION_HUB_SEARCH_LIVE_CANARY", "1")
    monkeypatch.delenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", raising=False)
    monkeypatch.delenv("DECISION_HUB_RESEARCH_CAPABILITIES", raising=False)

    result = asyncio.run(search_canary._run())

    assert result["status"] == "failed"
    assert observed["durable_progress"] == "0"
    assert "DECISION_HUB_RESEARCH_DURABLE_PROGRESS" not in os.environ
    assert "DECISION_HUB_RESEARCH_CAPABILITIES" not in os.environ


def test_search_canary_normalizes_mcp_provenance_without_traceback() -> None:
    result = normalize_canary_failure(
        Exception(
            'Error executing tool: research_capability_timeout '
            'provenance={"error_code":"research_capability_timeout",'
            '"origin":"transport","retryable":true,"deadline_ms":20000}'
        ),
        elapsed_ms=20001,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "research_capability_timeout"
    assert result["latency_ms"] == 20001
    assert result["error_provenance"] == {
        "error_code": "research_capability_timeout",
        "origin": "transport",
        "retryable": True,
        "deadline_ms": 20000,
    }


def test_search_canary_uses_safe_fallback_for_non_structured_failure() -> None:
    result = normalize_canary_failure(Exception("connection reset"), elapsed_ms=12)

    assert result["status"] == "failed"
    assert result["error_code"] == "responses_search_canary_failed"
    assert result["error_provenance"] == {
        "error_code": "responses_search_canary_failed",
        "origin": "canary",
        "retryable": False,
    }
