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
from packages.kernel.decision_hub_kernel.application.dsh_sessions import (
    DshSessionLinkService,
)
from packages.kernel.decision_hub_kernel.application.research_review import (
    ResearchReviewService,
)
from packages.kernel.decision_hub_kernel.persistence.db import Database, RunRecord, utcnow
from packages.kernel.decision_hub_kernel.ports.research import (
    ResearchHarnessRuntime,
    ResearchTraceSink,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.orchestration.langgraph.executor import LangGraphResearchExecutor
from packages.runtime_adapters.dsh_runtime import (
    DshResearchRuntime,
    DshRuntimeConfig,
    DshWebHostClient,
    DshWebHostConfig,
    DshWebResearchRuntime,
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


class CaseScopedDshWebResearchRuntime:
    """Run immutable PIT cases through the persistent official DSH Web Host.

    The default mode keeps the historical direct runtime comparison. Product
    validation can opt into ``product_graph`` so the same candidate is driven
    through the durable LangGraph evidence rounds and review projection used by
    ``DurableResearchWorker``. Keeping the switch explicit prevents old result
    artifacts from being mislabeled as production-path evaluations.
    """

    runtime_id = "dsh-web"
    profile_ref = DshWebResearchRuntime.profile_ref

    def __init__(
        self,
        case_paths: Mapping[str, Path],
        *,
        database: Database,
        host_config: DshWebHostConfig,
        research_mcp_port: int = 8002,
        readiness_timeout_seconds: float = 10,
        poll_interval_seconds: float = 0.25,
        product_graph: bool = False,
    ) -> None:
        if not 1 <= research_mcp_port <= 65535:
            raise ValueError("research_mcp_port_out_of_range")
        self.case_paths = dict(case_paths)
        self.database = database
        self.host_config = host_config
        self.research_mcp_port = research_mcp_port
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.product_graph = product_graph
        self.runtime_id = "dsh-web-graph" if product_graph else "dsh-web"
        self.runtime_version = (
            "dsh-web-graph-unverified" if product_graph else "dsh-web-unverified"
        )

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
        self._ensure_evaluation_run(request)
        process = _start_mcp(
            fixture_path,
            self.research_mcp_port,
            durable_progress=True,
        )
        runtime = DshWebResearchRuntime(
            DshSessionLinkService(self.database),
            DshWebHostClient(self.host_config),
            poll_interval_seconds=self.poll_interval_seconds,
            readiness_grace_seconds=self.readiness_timeout_seconds,
        )
        try:
            await _wait_ready(
                process, self.research_mcp_port, self.readiness_timeout_seconds
            )
            if self.product_graph:
                state = await LangGraphResearchExecutor(
                    self.database, runtime
                ).execute(request)
                result = ResearchSessionResult.model_validate(state["final_result"])
                review = ResearchReviewService(self.database).review(
                    request.run_id, result
                )
                result = result.model_copy(update={"reviews": [review]})
            else:
                result = await runtime.execute(request, trace_sink=trace_sink)
            self.runtime_version = runtime.runtime_version
            return result
        finally:
            await runtime.close()
            _stop_process(process)

    async def close(self) -> None:
        return None

    def _ensure_evaluation_run(self, request: ResearchSessionRequest) -> None:
        """Materialize the Hub-side owner required by the Host bridge contract."""

        now = utcnow()
        with self.database.session() as session:
            existing = session.get(RunRecord, request.run_id)
            if existing is not None:
                if existing.event_id != request.event_id:
                    raise ValueError("evaluation_run_event_conflict")
                return
            session.add(
                RunRecord(
                    run_id=request.run_id,
                    event_id=request.event_id,
                    idempotency_key=f"evaluation:{request.request_id}",
                    strategy_version="evaluation.dsh-web.v1",
                    runtime_version=self.runtime_version,
                    admission_origin="manual",
                    priority=100,
                    created_at=now,
                    updated_at=now,
                )
            )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_mcp(
    fixture_path: Path,
    port: int,
    *,
    durable_progress: bool = False,
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "DECISION_HUB_RESEARCH_CAPABILITIES": "replay.research",
        "DECISION_HUB_RESEARCH_REPLAY_FIXTURES": str(fixture_path.resolve()),
        "DECISION_HUB_RESEARCH_MCP_HOST": "127.0.0.1",
        "DECISION_HUB_RESEARCH_MCP_PORT": str(port),
        "DECISION_HUB_RESEARCH_DURABLE_PROGRESS": "1" if durable_progress else "0",
        "DECISION_HUB_DSH_RESEARCH_TOOL_KEY": os.getenv(
            "DECISION_HUB_DSH_RESEARCH_TOOL_KEY",
            "decision-hub-local-research-tool-key",
        ),
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
