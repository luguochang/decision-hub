from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
)
from packages.orchestration.langgraph.graphs.supervisor_graph import (
    SupervisorCandidateState,
    build_supervisor_candidate_graph,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


class RecordingRuntime(FakeAgentRuntime):
    def __init__(self) -> None:
        self.roles: list[str] = []

    async def execute(self, request: AgentRequest) -> AgentResult:
        self.roles.append(request.role)
        return await super().execute(request)


def candidate_input() -> SupervisorCandidateState:
    return {
        "run_id": "candidate-run-1",
        "text": "Powell says policy remains restrictive.",
        "evidence": ("evidence:fed:1",),
        "deadline_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        "domain_pack_ref": "crypto_macro.v1",
        "replan_count": 0,
        "specialist_results": [],
    }


def test_supervisor_uses_dynamic_required_specialists_and_returns_candidate() -> None:
    runtime = RecordingRuntime()
    graph = build_supervisor_candidate_graph(
        runtime,
        available_capabilities={"policy_delta", "counter_thesis", "data_quality"},
        required_capabilities=("counter_thesis", "data_quality"),
    )

    result = asyncio.run(graph.ainvoke(candidate_input()))

    assert runtime.roles[0] == "research_supervisor"
    assert set(runtime.roles[1:-1]) == {
        "policy_delta",
        "counter_thesis",
        "data_quality",
    }
    assert runtime.roles[-1] == "decision_synthesis"
    assert result["candidate"]["direction"] == "short"
    assert result["coverage"] == [
        "counter_thesis",
        "data_quality",
        "policy_delta",
    ]


class UnknownCapabilityRuntime(RecordingRuntime):
    async def execute(self, request: AgentRequest) -> AgentResult:
        result = await super().execute(request)
        if request.role != "research_supervisor":
            return result
        return AgentResult(
            role=result.role,
            payload={
                "tasks": [
                    {
                        "task_id": "unsafe",
                        "capability_id": "shell_everything",
                        "instruction": "run arbitrary shell",
                    }
                ]
            },
            runtime_id=result.runtime_id,
            runtime_version=result.runtime_version,
            latency_ms=result.latency_ms,
            usage=result.usage,
        )


def test_supervisor_rejects_unknown_capability_before_fanout() -> None:
    runtime = UnknownCapabilityRuntime()
    graph = build_supervisor_candidate_graph(
        runtime,
        available_capabilities={"policy_delta", "counter_thesis", "data_quality"},
        required_capabilities=("counter_thesis", "data_quality"),
    )

    with pytest.raises(AgentExecutionError, match="research plan requested unknown capability"):
        asyncio.run(graph.ainvoke(candidate_input()))
    assert runtime.roles == ["research_supervisor"]


class FlakyRequiredRuntime(RecordingRuntime):
    def __init__(self, *, always_fail: bool = False) -> None:
        super().__init__()
        self.always_fail = always_fail
        self.failed_once = False

    async def execute(self, request: AgentRequest) -> AgentResult:
        if request.role == "counter_thesis" and (self.always_fail or not self.failed_once):
            self.roles.append(request.role)
            self.failed_once = True
            raise AgentExecutionError(
                "provider_unavailable", "specialist unavailable", retryable=True
            )
        return await super().execute(request)


def test_supervisor_replans_only_missing_required_capability_once() -> None:
    runtime = FlakyRequiredRuntime()
    graph = build_supervisor_candidate_graph(
        runtime,
        available_capabilities={"policy_delta", "counter_thesis", "data_quality"},
        required_capabilities=("counter_thesis", "data_quality"),
    )

    result = asyncio.run(graph.ainvoke(candidate_input()))

    assert runtime.roles.count("counter_thesis") == 2
    assert runtime.roles.count("data_quality") == 1
    assert result["replan_count"] == 1
    assert result["missing_capabilities"] == []
    assert result["candidate"]


def test_supervisor_fails_closed_after_one_replan() -> None:
    runtime = FlakyRequiredRuntime(always_fail=True)
    graph = build_supervisor_candidate_graph(
        runtime,
        available_capabilities={"counter_thesis", "data_quality"},
        required_capabilities=("counter_thesis", "data_quality"),
    )

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(graph.ainvoke(candidate_input()))

    assert raised.value.error_code == "research_coverage_missing"
    assert runtime.roles.count("counter_thesis") == 2


class DuplicatePlanRuntime(RecordingRuntime):
    async def execute(self, request: AgentRequest) -> AgentResult:
        result = await super().execute(request)
        if request.role != "research_supervisor":
            return result
        task = {
            "task_id": "duplicate",
            "capability_id": "counter_thesis",
            "instruction": "Review the counter thesis.",
        }
        return AgentResult(
            role=result.role,
            payload={"tasks": [task, task]},
            runtime_id=result.runtime_id,
            runtime_version=result.runtime_version,
            latency_ms=result.latency_ms,
            usage=result.usage,
        )


def test_supervisor_rejects_duplicate_tasks() -> None:
    runtime = DuplicatePlanRuntime()
    graph = build_supervisor_candidate_graph(
        runtime,
        available_capabilities={"counter_thesis"},
        required_capabilities=("counter_thesis",),
    )

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(graph.ainvoke(candidate_input()))

    assert raised.value.error_code == "research_plan_invalid"


def test_supervisor_checkpoint_resumes_without_repeating_specialists() -> None:
    async def run() -> None:
        runtime = RecordingRuntime()
        saver = InMemorySaver()
        graph = build_supervisor_candidate_graph(
            runtime,
            available_capabilities={"counter_thesis", "data_quality"},
            required_capabilities=("counter_thesis", "data_quality"),
            checkpointer=saver,
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": "supervisor-checkpoint-1"}
        }

        interrupted = await graph.ainvoke(
            candidate_input(), config=config, interrupt_before=["synthesize"]
        )
        assert "candidate" not in interrupted
        specialist_calls = len(
            [role for role in runtime.roles if role in {"counter_thesis", "data_quality"}]
        )
        resumed = await graph.ainvoke(None, config=config)

        assert resumed["candidate"]
        assert len(
            [role for role in runtime.roles if role in {"counter_thesis", "data_quality"}]
        ) == specialist_calls

    asyncio.run(run())
