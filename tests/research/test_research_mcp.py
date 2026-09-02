from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult
from starlette.testclient import TestClient

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityGatewayService,
    research_evidence_content_hash,
)
from packages.provider_adapters.research import (
    ReplayResearchCapabilityAdapter,
    load_replay_archive,
    load_replay_fixtures,
)
from packages.workbench_adapters.research_mcp import build_research_mcp_server

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)
QUERY = "warsh event identity"


def _candidate() -> EvidenceCandidate:
    excerpt = "Kevin Warsh discussed inflation risks in an archived policy speech."
    url = "https://www.federalreserve.gov/newsevents/speech/warsh-archive.htm"
    content_hash = research_evidence_content_hash(
        requirement_id="archived_requirement",
        kind="official",
        authority="official",
        source_id="federal-reserve-archive",
        source_url=url,
        published_at=NOW - timedelta(hours=2),
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate.model_validate(
        {
            "evidence_id": f"ev_{content_hash[:32]}",
            "requirement_id": "archived_requirement",
            "kind": "official",
            "authority": "official",
            "source_id": "federal-reserve-archive",
            "source_url": url,
            "published_at": NOW - timedelta(hours=2),
            "observed_at": NOW - timedelta(minutes=2),
            "received_at": NOW - timedelta(minutes=1),
            "content_hash": content_hash,
            "excerpt": excerpt,
            "structured_payload_ref": None,
            "tool_call_id": "archived-call",
            "research_session_id": "archived-session",
            "round": 1,
            "quality": "candidate",
            "freshness_status": "unknown",
            "conflict_group": None,
        }
    )


def _manifest() -> ResearchCapabilityManifest:
    return ResearchCapabilityManifest.model_validate(
        {
            "schema_version": "research-capability-manifest.v1",
            "capability_id": "replay.research",
            "version": "1.0.0",
            "kind": "replay",
            "implementation_ref": "adapter://runtime/replay-research",
            "input_schema_ref": "replay-research-query.v1",
            "output_schema_ref": "research-capability-result.v1",
            "permissions": ["fixture:read"],
            "allowed_domains": [],
            "timeout_seconds": 5,
            "cost_policy_ref": "no-external-cost.v1",
            "freshness_policy_ref": "fixture-pit.v1",
            "license_status": "approved",
            "audit_status": "approved",
            "replay_policy": "deterministic",
            "secret_policy": "none",
        }
    )


def _server(*, enabled: bool = True, bridge_key: str | None = None):
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research", fixtures={QUERY: [_candidate()]}
    )
    gateway = ResearchCapabilityGatewayService(
        [_manifest()],
        [adapter],
        enabled_capabilities=["replay.research"] if enabled else [],
    )
    return build_research_mcp_server(gateway, bridge_key=bridge_key)


def _arguments(query: str = QUERY) -> dict[str, object]:
    return {
        "request_id": "mcp-replay-request",
        "capability_id": "replay.research",
        "requirement_id": "event_identity",
        "query": query,
        "research_session_id": "live-research-session",
        "round": 2,
        "mode": "replay",
        "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
        "cutoff_at": NOW.isoformat(),
        "target_url": None,
        "symbols": [],
        "fields": [],
        "allowed_domains": [],
        "max_results": 10,
        "max_cost_usd": 0.0,
    }


async def test_research_mcp_exposes_only_read_only_capability_tool() -> None:
    server = _server()

    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {"research_capability_execute"}
    forbidden = {"submit_research_memo", "submit_feedback", "promotion", "gate"}
    assert forbidden.isdisjoint({tool.name for tool in tools})

    result = cast(
        CallToolResult, await server.call_tool("research_capability_execute", _arguments())
    )
    assert result.is_error is False
    assert result.structured_content is not None
    candidate = result.structured_content["evidence_candidates"][0]
    assert candidate["requirement_id"] == "event_identity"
    assert candidate["research_session_id"] == "live-research-session"
    assert candidate["round"] == 2
    assert candidate["tool_call_id"] == "mcp-replay-request"


async def test_research_mcp_denial_is_a_stable_tool_error() -> None:
    with pytest.raises(ToolError, match="research_capability_not_enabled") as raised:
        await _server(enabled=False).call_tool("research_capability_execute", _arguments())
    assert "provenance=" in str(raised.value)
    assert '"origin":"gateway"' in str(raised.value)


def test_research_http_bridge_requires_secret_and_reuses_canonical_gateway() -> None:
    app = _server(bridge_key="test-bridge-secret").streamable_http_app()
    with TestClient(app) as client:
        unauthorized = client.post(
            "/decision-hub/v1/research-capabilities/execute",
            json=_arguments(),
        )
        accepted = client.post(
            "/decision-hub/v1/research-capabilities/execute",
            headers={"x-decision-hub-bridge-key": "test-bridge-secret"},
            json={"schema_version": "research-capability-query.v1", **_arguments()},
        )

    assert unauthorized.status_code == 401
    assert unauthorized.json()["error_code"] == "research_tool_unauthorized"
    assert accepted.status_code == 200
    assert accepted.json()["schema_version"] == "research-capability-result.v1"
    assert accepted.json()["evidence_candidates"][0]["research_session_id"] == (
        "live-research-session"
    )


def test_replay_fixture_loader_is_explicit_versioned_and_fail_closed(tmp_path: Path) -> None:
    fixture_path = tmp_path / "replay.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "research-replay-fixtures.v1",
                "queries": [
                    {
                        "query": QUERY,
                        "evidence_candidates": [_candidate().model_dump(mode="json")],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_replay_fixtures(fixture_path)
    assert list(loaded) == [QUERY]
    assert load_replay_fixtures(None) == {}

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="research_replay_fixture_invalid"):
        load_replay_fixtures(invalid)


async def test_replay_evaluation_case_matches_requirement_not_model_wording() -> None:
    case_path = Path(
        "packs/crypto_macro/evaluations/r2r_pit_v1/cases/powell_stanford_20240403.json"
    )
    archive = load_replay_archive(case_path)
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="replay.research",
        fixtures=archive.queries,
        requirement_fixtures=archive.requirements,
    )
    result = await adapter.execute(
        ResearchCapabilityQuery.model_validate(
            {
                "schema_version": "research-capability-query.v1",
                "request_id": "free-wording-request",
                "capability_id": "replay.research",
                "requirement_id": "event_identity",
                "query": "Model-selected wording that is not an archived alias",
                "target_url": None,
                "symbols": [],
                "fields": [],
                "allowed_domains": [],
                "max_results": 10,
                "max_cost_usd": 0.0,
                "research_session_id": "evaluation-session",
                "round": 1,
                "mode": "replay",
                "observed_at": "2024-04-03T16:10:02Z",
                "cutoff_at": "2024-04-03T16:15:00Z",
            }
        )
    )

    assert result.evidence_candidates[0].requirement_id == "event_identity"
    assert result.evidence_candidates[0].research_session_id == "evaluation-session"
    assert result.evidence_candidates[0].tool_call_id == "free-wording-request"


async def test_research_mcp_streamable_http_official_client_smoke(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = cast(int, listener.getsockname()[1])
    fixture_path = tmp_path / "replay.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "research-replay-fixtures.v1",
                "queries": [
                    {
                        "query": QUERY,
                        "evidence_candidates": [_candidate().model_dump(mode="json")],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
        "DECISION_HUB_RESEARCH_REPLAY_FIXTURES": str(fixture_path),
        "DECISION_HUB_RESEARCH_MCP_HOST": "127.0.0.1",
        "DECISION_HUB_RESEARCH_MCP_PORT": str(port),
        "PYTHONPATH": str(Path.cwd()),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "apps.research_mcp.main"],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, stderr = process.communicate()
                pytest.fail(f"research-mcp exited before readiness: {stderr}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("research-mcp streamable HTTP did not become ready")

        async with httpx2.AsyncClient(trust_env=False) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp", http_client=http_client
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == (
                        "decision-hub-research-capabilities"
                    )
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == {
                        "research_capability_execute"
                    }
                    result = await session.call_tool(
                        "research_capability_execute", _arguments()
                    )
                    assert result.is_error is False
                    assert result.structured_content is not None
                    assert result.structured_content["provider"] == "archived-replay"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
