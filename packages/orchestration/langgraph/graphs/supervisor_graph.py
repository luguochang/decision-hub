from __future__ import annotations

import json
import operator
from datetime import datetime
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import Checkpointer
from langgraph.types import Send

from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentRuntime,
)


class ResearchTask(TypedDict):
    task_id: str
    capability_id: str
    instruction: str


class SpecialistResult(TypedDict):
    capability_id: str
    payload: dict[str, object]
    status: Literal["succeeded", "failed"]
    error_code: str | None


class SupervisorCandidateState(TypedDict, total=False):
    run_id: str
    text: str
    evidence: tuple[str, ...]
    deadline_at: str
    domain_pack_ref: str
    replan_count: int
    plan: list[ResearchTask]
    specialist_results: Annotated[list[SpecialistResult], operator.add]
    coverage: list[str]
    missing_capabilities: list[str]
    candidate: dict[str, object]
    task_id: str
    capability_id: str
    instruction: str


def _default_tasks(capabilities: set[str]) -> list[ResearchTask]:
    return [
        ResearchTask(
            task_id=f"task-{capability}",
            capability_id=capability,
            instruction=f"Investigate {capability} using only the frozen evidence.",
        )
        for capability in sorted(capabilities)
    ]


def build_supervisor_candidate_graph(
    runtime: AgentRuntime,
    *,
    available_capabilities: set[str],
    required_capabilities: tuple[str, ...],
    checkpointer: Checkpointer = None,
):
    if not set(required_capabilities).issubset(available_capabilities):
        raise ValueError("required_capability_unavailable")

    async def plan(state: SupervisorCandidateState) -> dict[str, object]:
        result = await runtime.execute(
            AgentRequest(
                role="research_supervisor",
                text=state["text"],
                evidence=state["evidence"],
                deadline_at=datetime.fromisoformat(state["deadline_at"]),
            )
        )
        raw_tasks = result.payload.get("tasks")
        tasks = raw_tasks if isinstance(raw_tasks, list) else _default_tasks(available_capabilities)
        if not tasks or len(tasks) > 8:
            raise AgentExecutionError(
                "research_plan_invalid", "research plan must contain 1 to 8 tasks"
            )
        normalized: list[ResearchTask] = []
        seen_capabilities: set[str] = set()
        seen_tasks: set[str] = set()
        for item in tasks:
            if not isinstance(item, dict):
                raise AgentExecutionError(
                    "research_plan_invalid", "research plan task must be an object"
                )
            capability = item.get("capability_id")
            task_id = item.get("task_id")
            instruction = item.get("instruction")
            if not all(
                isinstance(value, str) and value for value in (capability, task_id, instruction)
            ):
                raise AgentExecutionError(
                    "research_plan_invalid", "research plan task fields are required"
                )
            assert isinstance(capability, str)
            assert isinstance(task_id, str)
            assert isinstance(instruction, str)
            if capability not in available_capabilities:
                raise AgentExecutionError(
                    "research_capability_unknown",
                    "research plan requested unknown capability",
                )
            if capability in seen_capabilities or task_id in seen_tasks:
                raise AgentExecutionError(
                    "research_plan_invalid", "research plan contains duplicate tasks"
                )
            seen_capabilities.add(capability)
            seen_tasks.add(task_id)
            normalized.append(
                ResearchTask(task_id=task_id, capability_id=capability, instruction=instruction)
            )
        for capability in required_capabilities:
            if capability not in seen_capabilities:
                normalized.append(
                    ResearchTask(
                        task_id=f"required-{capability}",
                        capability_id=capability,
                        instruction=f"Required coverage for {capability}.",
                    )
                )
        if not normalized or len(normalized) > 8:
            raise AgentExecutionError(
                "research_plan_invalid", "research plan must contain 1 to 8 tasks"
            )
        return {"plan": normalized, "specialist_results": []}

    def replan(state: SupervisorCandidateState) -> dict[str, object]:
        missing = state.get("missing_capabilities", [])
        if not missing or state.get("replan_count", 0) >= 1:
            raise AgentExecutionError(
                "research_coverage_missing", "required research coverage is still missing"
            )
        return {
            "replan_count": state.get("replan_count", 0) + 1,
            "plan": _default_tasks(set(missing)),
        }

    def fanout(state: SupervisorCandidateState) -> list[Send]:
        return [
            Send(
                "specialist",
                {
                    "run_id": state["run_id"],
                    "text": state["text"],
                    "evidence": state["evidence"],
                    "deadline_at": state["deadline_at"],
                    **task,
                },
            )
            for task in state["plan"]
        ]

    async def specialist(state: SupervisorCandidateState) -> dict[str, object]:
        try:
            result = await runtime.execute(
                AgentRequest(
                    role=state["capability_id"],
                    text=f"{state['text']}\n\nTask: {state['instruction']}",
                    evidence=state["evidence"],
                    deadline_at=datetime.fromisoformat(state["deadline_at"]),
                )
            )
        except AgentExecutionError as exc:
            return {
                "specialist_results": [
                    SpecialistResult(
                        capability_id=state["capability_id"],
                        payload={},
                        status="failed",
                        error_code=exc.error_code,
                    )
                ]
            }
        return {
            "specialist_results": [
                SpecialistResult(
                    capability_id=state["capability_id"],
                    payload=result.payload,
                    status="succeeded",
                    error_code=None,
                )
            ]
        }

    def assess(state: SupervisorCandidateState) -> dict[str, object]:
        coverage = sorted(
            {
                item["capability_id"]
                for item in state["specialist_results"]
                if item["status"] == "succeeded"
            }
        )
        missing = sorted(set(required_capabilities) - set(coverage))
        return {"coverage": coverage, "missing_capabilities": missing}

    def route_after_assessment(
        state: SupervisorCandidateState,
    ) -> Literal["replan", "synthesize", "coverage_failed"]:
        if not state.get("missing_capabilities"):
            return "synthesize"
        return "replan" if state.get("replan_count", 0) < 1 else "coverage_failed"

    def coverage_failed(state: SupervisorCandidateState) -> None:
        raise AgentExecutionError(
            "research_coverage_missing",
            f"required research coverage missing: {state.get('missing_capabilities', [])}",
        )

    async def synthesize(state: SupervisorCandidateState) -> dict[str, object]:
        context = json.dumps(state["specialist_results"], ensure_ascii=False)
        result = await runtime.execute(
            AgentRequest(
                role="decision_synthesis",
                text=f"{state['text']}\n\nSpecialist results:\n{context}",
                evidence=state["evidence"],
                deadline_at=datetime.fromisoformat(state["deadline_at"]),
            )
        )
        return {"candidate": result.payload}

    graph = StateGraph(SupervisorCandidateState)
    graph.add_node("supervisor", plan)
    graph.add_node("replan", replan)
    graph.add_node("specialist", specialist)
    graph.add_node("assess", assess)
    graph.add_node("coverage_failed", coverage_failed)
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", fanout, ["specialist"])
    graph.add_conditional_edges("replan", fanout, ["specialist"])
    graph.add_edge("specialist", "assess")
    graph.add_conditional_edges(
        "assess",
        route_after_assessment,
        ["replan", "synthesize", "coverage_failed"],
    )
    graph.add_edge("coverage_failed", END)
    graph.add_edge("synthesize", END)
    return graph.compile(checkpointer=checkpointer)
