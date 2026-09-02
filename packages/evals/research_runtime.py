from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    ResearchSessionRequest,
    ResearchSessionResult,
)
from packages.kernel.decision_hub_kernel.ports.research import (
    ResearchHarnessRuntime,
    ResearchTraceSink,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime import (
    DshResearchRuntime,
    DshRuntimeConfig,
    inspect_profile,
)

ResearchRuntimeFactory = Callable[[DshRuntimeConfig], ResearchHarnessRuntime]


class CaseScopedDshResearchRuntime:
    """Run each PIT case against its own archived, fail-closed Research MCP."""

    runtime_id = "dsh"

    def __init__(
        self,
        case_paths: Mapping[str, Path],
        *,
        config: DshRuntimeConfig,
        runtime_factory: ResearchRuntimeFactory = DshResearchRuntime,
        readiness_timeout_seconds: float = 10,
    ) -> None:
        self.case_paths = dict(case_paths)
        self.config = config
        self.runtime_factory = runtime_factory
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.runtime_version = f"dsh-sdk-{config.sdk_version}"
        self.profile_ref = inspect_profile(config.profile_path).profile_ref

    async def execute(
        self,
        request: ResearchSessionRequest,
        trace_sink: ResearchTraceSink | None = None,
    ) -> ResearchSessionResult:
        fixture_path = self.case_paths.get(request.event_id)
        if fixture_path is None:
            raise AgentExecutionError(
                "research_replay_fixture_missing",
                "the evaluation case has no archived Research MCP fixture",
            )
        port = _free_port()
        process = _start_mcp(fixture_path, port)
        runtime: ResearchHarnessRuntime | None = None
        try:
            await _wait_ready(process, port, self.readiness_timeout_seconds)
            runtime = self.runtime_factory(
                self.config.model_copy(
                    update={"research_mcp_url": f"http://127.0.0.1:{port}/mcp"}
                )
            )
            return await runtime.execute(request, trace_sink=trace_sink)
        finally:
            if runtime is not None:
                await runtime.close()
            _stop_process(process)

    async def close(self) -> None:
        return None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_mcp(fixture_path: Path, port: int) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
        "DECISION_HUB_RESEARCH_REPLAY_FIXTURES": str(fixture_path.resolve()),
        "DECISION_HUB_RESEARCH_MCP_HOST": "127.0.0.1",
        "DECISION_HUB_RESEARCH_MCP_PORT": str(port),
        "PYTHONPATH": str(Path.cwd()),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "apps.research_mcp.main"],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def _wait_ready(
    process: subprocess.Popen[bytes], port: int, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AgentExecutionError(
                "research_mcp_startup_failed",
                "the case-scoped Research MCP exited before readiness",
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise AgentExecutionError(
        "research_mcp_startup_timeout",
        "the case-scoped Research MCP did not become ready",
        retryable=True,
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
