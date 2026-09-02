from __future__ import annotations

import asyncio
import collections
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.research_evidence import (
    research_evidence_content_hash,
)
from packages.runtime_adapters.dsh_runtime import DshRuntimeConfig, DshSdkClient
from packages.runtime_adapters.dsh_runtime.tool_result_attestation import (
    extract_capability_results,
)

QUERY = "r2-r-02 archived event identity canary"
SESSION_ID = "decision-hub-r2-r02-mcp-live-canary"
PUBLISHED_AT = "2026-08-29T08:00:00Z"
OBSERVED_AT = "2026-08-29T10:00:00Z"
RECEIVED_AT = "2026-08-29T10:01:00Z"
CUTOFF_AT = "2026-08-29T12:00:00Z"
SOURCE_URL = "https://www.federalreserve.gov/newsevents/speech/r2-r-02-canary.htm"
EXCERPT = "R2-R-02 canary evidence confirms the archived event identity."
TOOL_NAME = "mcp__decision_research__research_capability_execute"


def _fixture_payload() -> dict[str, object]:
    from datetime import datetime

    published_at = datetime.fromisoformat(PUBLISHED_AT.replace("Z", "+00:00"))
    content_hash = research_evidence_content_hash(
        requirement_id="archived_requirement",
        kind="official",
        authority="official",
        source_id="federal-reserve-archive",
        source_url=SOURCE_URL,
        published_at=published_at,
        excerpt=EXCERPT,
        structured_payload_ref=None,
    )
    return {
        "schema_version": "research-replay-fixtures.v1",
        "queries": [
            {
                "query": QUERY,
                "evidence_candidates": [
                    {
                        "evidence_id": f"ev_{content_hash[:32]}",
                        "requirement_id": "archived_requirement",
                        "kind": "official",
                        "authority": "official",
                        "source_id": "federal-reserve-archive",
                        "source_url": SOURCE_URL,
                        "published_at": PUBLISHED_AT,
                        "observed_at": OBSERVED_AT,
                        "received_at": RECEIVED_AT,
                        "content_hash": content_hash,
                        "excerpt": EXCERPT,
                        "structured_payload_ref": None,
                        "tool_call_id": "archived-call",
                        "research_session_id": "archived-session",
                        "round": 1,
                        "quality": "candidate",
                        "freshness_status": "unknown",
                        "conflict_group": None,
                    }
                ],
            }
        ],
    }


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_ready(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise RuntimeError(f"research MCP exited before readiness: {stderr[-1000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise RuntimeError("research MCP did not become ready")


async def _run() -> dict[str, object]:
    if os.getenv("DECISION_HUB_DSH_LIVE_CANARY") != "1":
        raise RuntimeError("set DECISION_HUB_DSH_LIVE_CANARY=1 to authorize external model use")

    base = DshRuntimeConfig.from_env()
    with tempfile.TemporaryDirectory(prefix="decision-hub-dsh-mcp-live-") as raw_temp:
        temp = Path(raw_temp)
        fixture_path = temp / "replay.json"
        fixture_path.write_text(
            json.dumps(_fixture_payload(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        port = _free_port()
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        server_env = {
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
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        previous_url = os.environ.get("DECISION_HUB_RESEARCH_MCP_URL")
        try:
            await _wait_ready(process, port)
            os.environ["DECISION_HUB_RESEARCH_MCP_URL"] = mcp_url
            config = base.model_copy(
                update={
                    "workspace": temp,
                    "session_root": temp / "sessions",
                    "request_timeout_seconds": 120,
                }
            )
            client = DshSdkClient(config)
            try:
                async with asyncio.timeout(125):
                    run = await client.run(
                        "Call the tool "
                        f"{TOOL_NAME} exactly once with these arguments: "
                        "request_id='r2-r02-mcp-canary', "
                        "capability_id='replay.research', "
                        "requirement_id='event_identity', "
                        f"query='{QUERY}', research_session_id='{SESSION_ID}', "
                        "round=1, mode='replay', "
                        f"observed_at='{OBSERVED_AT}', cutoff_at='{CUTOFF_AT}', "
                        "target_url=null, symbols=[], fields=[], allowed_domains=[], "
                        "max_results=10, max_cost_usd=0. "
                        f"After the tool result contains '{EXCERPT}', reply with exactly "
                        "DSH_MCP_CANARY_OK. Do not call todo or subagent tools.",
                        session_id=SESSION_ID,
                    )
            finally:
                await client.close()
        finally:
            if previous_url is None:
                os.environ.pop("DECISION_HUB_RESEARCH_MCP_URL", None)
            else:
                os.environ["DECISION_HUB_RESEARCH_MCP_URL"] = previous_url
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        event_types: collections.Counter[str] = collections.Counter()
        tool_names: collections.Counter[str] = collections.Counter()
        observed_tool_fields: collections.Counter[str] = collections.Counter()
        failed_tools = 0
        for notification in run.notifications:
            if notification.method != "session.event":
                continue
            event = notification.payload.get("event")
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if isinstance(event_type, str):
                event_types[event_type] += 1
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            for field in ("toolName", "name", "tool", "provider", "id", "callId"):
                if isinstance(data.get(field), str):
                    observed_tool_fields[f"{field}={data[field]}"] += 1
            tool_name = data.get("toolName") or data.get("name") or data.get("tool")
            if isinstance(tool_name, str):
                tool_names[tool_name] += 1
            if event_type in {"tool/result", "tool/call-result"} and (
                bool(data.get("isError")) or data.get("status") == "error"
            ):
                failed_tools += 1

        matching_calls = tool_names[TOOL_NAME]
        canonical_results = extract_capability_results(run.notifications)
        canonical_evidence = sum(
            len(result.evidence_candidates) for result in canonical_results
        )
        passed = (
            run.finish_reason == "completed"
            and run.final_response.strip() == "DSH_MCP_CANARY_OK"
            and matching_calls >= 1
            and event_types["tool/call"] >= 1
            and event_types["tool/result"] >= 1
            and failed_tools == 0
            and len(canonical_results) == 1
            and canonical_evidence == 1
        )
        return {
            "status": "passed" if passed else "failed",
            "sdk_version": config.sdk_version,
            "provider": config.provider,
            "model": config.model,
            "finish_reason": run.finish_reason,
            "final_response_matched": (run.final_response.strip() == "DSH_MCP_CANARY_OK"),
            "research_mcp_tool_events": matching_calls,
            "observed_tool_fields": sorted(observed_tool_fields),
            "tool_calls": event_types["tool/call"],
            "tool_results": event_types["tool/result"],
            "failed_tools": failed_tools,
            "canonical_results": len(canonical_results),
            "canonical_evidence": canonical_evidence,
            "turns_completed": event_types["turn/end"],
            "steps_completed": event_types["step/end"],
            "session_files": sum(1 for item in (temp / "sessions").rglob("*") if item.is_file()),
        }


def main() -> int:
    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
