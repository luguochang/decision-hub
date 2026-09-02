from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult

from apps.hub_api.main import create_app
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService
from packages.kernel.decision_hub_kernel.persistence.db import Database, SnapshotRecord
from packages.workbench_adapters.mcp import build_core_mcp_server


async def test_mcp_exposes_typed_queries_and_owner_bound_commands(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'mcp.sqlite3'}")
    client = TestClient(create_app(database))
    server = build_core_mcp_server(WorkbenchAssetService(database), owner_id="owner")

    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "workbench_overview",
        "list_research_memos",
        "list_feedback",
        "list_capabilities",
        "submit_research_memo",
        "submit_feedback",
    }

    with pytest.raises(ToolError, match="owner_identity_mismatch"):
        await server.call_tool(
            "submit_feedback",
            {
                "request_id": "mcp-denied",
                "target_type": "run",
                "target_id": "missing",
                "created_by": "other",
                "verdict": "needs_review",
                "notes": "Not allowed.",
            },
        )

    observation = client.post(
        "/v1/observations",
        headers={"Idempotency-Key": "mcp-observation"},
        json={"text": "MCP fixture", "source_id": "mcp-fixture"},
    )
    assert observation.status_code == 202
    run_id = observation.json()["run_id"]
    run = client.get(f"/v1/runs/{run_id}").json()
    with database.session() as session:
        snapshot = session.get(SnapshotRecord, run["snapshot_id"])
        assert snapshot is not None
        evidence_id = json.loads(snapshot.evidence_json)[0]["evidence_id"]

    memo = cast(CallToolResult, await server.call_tool(
        "submit_research_memo",
        {
            "request_id": "mcp-memo",
            "run_id": run_id,
            "snapshot_id": run["snapshot_id"],
            "domain_pack_ref": "crypto_macro.v1",
            "created_by": "owner",
            "claims": ["The MCP path preserves the existing snapshot."],
            "evidence_refs": [evidence_id],
            "counterpoints": [],
            "uncertainties": [],
            "follow_up_questions": [],
        },
    ))
    assert memo.is_error is False
    assert memo.structured_content is not None
    assert memo.structured_content["request_id"] == "mcp-memo"

    overview = cast(CallToolResult, await server.call_tool("workbench_overview", {"limit": 10}))
    assert overview.is_error is False
    assert overview.structured_content is not None
    assert len(overview.structured_content["memos"]) == 1


async def test_mcp_stdio_transport_handshake_and_structured_query(tmp_path: Path) -> None:
    """Exercise the official subprocess transport, not only the in-process server."""
    env = {
        **os.environ,
        "DECISION_HUB_DATA_DIR": str(tmp_path / "data"),
        "DECISION_HUB_OWNER_ID": "owner",
        "PYTHONPATH": str(Path.cwd()),
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apps.hub_mcp.main"],
        env=env,
        cwd=Path.cwd(),
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "decision-hub-workbench"

            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "workbench_overview",
                "list_research_memos",
                "list_feedback",
                "list_capabilities",
                "submit_research_memo",
                "submit_feedback",
            }

            overview = await session.call_tool("workbench_overview", {"limit": 10})
            assert overview.is_error is False
            assert overview.structured_content is not None
            assert overview.structured_content["memos"] == []


async def test_mcp_streamable_http_official_client_smoke(tmp_path: Path) -> None:
    """Start the real entrypoint and complete an official HTTP client handshake."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = cast(int, listener.getsockname()[1])
    env = {
        **os.environ,
        "DECISION_HUB_DATA_DIR": str(tmp_path / "http-data"),
        "DECISION_HUB_OWNER_ID": "owner",
        "DECISION_HUB_MCP_TRANSPORT": "streamable-http",
        "DECISION_HUB_MCP_HOST": "127.0.0.1",
        "DECISION_HUB_MCP_PORT": str(port),
        "PYTHONPATH": str(Path.cwd()),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "apps.hub_mcp.main"],
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
                pytest.fail(f"hub-mcp exited before readiness: {stderr}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            pytest.fail("hub-mcp streamable HTTP did not become ready")

        async with httpx2.AsyncClient(trust_env=False) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp", http_client=http_client
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "decision-hub-workbench"
                    tools = await session.list_tools()
                    assert "workbench_overview" in {tool.name for tool in tools.tools}
                    overview = await session.call_tool(
                        "workbench_overview", {"limit": 10}
                    )
                    assert overview.is_error is False
                    assert overview.structured_content is not None
                    assert overview.structured_content["capabilities"] == []
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
