from __future__ import annotations

import asyncio
import json
from datetime import datetime

from langgraph.graph import END, START, StateGraph

from packages.kernel.decision_hub_kernel.application.calls import RunCallService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentRequest,
    AgentRuntime,
)
from packages.orchestration.langgraph.state.decision import DecisionState


async def _research_node(
    state: DecisionState,
    runtime: AgentRuntime,
    calls: RunCallService | None = None,
) -> dict[str, object]:
    if calls is None:
        raise RuntimeError("research graph requires the normalized call projection")
    run_id = state["run_id"]
    evidence_items = calls.database.get_snapshot_evidence(state["snapshot_id"])
    evidence_ids = tuple(str(item["evidence_id"]) for item in evidence_items)
    text = "\n\n".join(str(item.get("text", "")) for item in evidence_items)
    deadline = datetime.fromisoformat(state["deadline_at"])

    async def execute(role: str, role_text: str = text) -> tuple[str, dict[str, object]]:
        existing = calls.database.find_role_output(run_id, role)
        if existing:
            return existing
        request = AgentRequest(
            role=role,
            text=role_text,
            evidence=evidence_ids,
            deadline_at=deadline,
        )
        handle = calls.start(
            run_id,
            role,
            attempt=calls.next_attempt(run_id, role),
            runtime=runtime,
        )
        try:
            result = await runtime.execute(request)
            calls.complete(handle, result)
        except BaseException as exc:
            calls.fail(handle, exc)
            raise
        output_id = calls.database.save_role_output(
            run_id, role, result.payload, result.schema_version
        )
        return output_id, result.payload

    policy, counter = await asyncio.gather(
        execute("policy_delta"),
        execute("counter_thesis"),
    )
    calls.enforce_budget(run_id, runtime.cost_budget)
    synthesis_context = (
        f"{text}\n\nPolicy analysis:\n{json.dumps(policy[1], ensure_ascii=False)}"
        f"\n\nCounter-thesis:\n{json.dumps(counter[1], ensure_ascii=False)}"
    )
    decision = await execute("decision_synthesis", synthesis_context)
    calls.enforce_budget(run_id, runtime.cost_budget)
    candidate = dict(decision[1])
    # Keep evidence fields owned by the specialist that produced them. The synthesis
    # prompt contains serialized reviewer context, which must never be persisted as a
    # user-facing fact or citation.
    policy_facts = policy[1].get("facts")
    if isinstance(policy_facts, list) and all(isinstance(item, str) for item in policy_facts):
        candidate["facts"] = policy_facts
    policy_citations = policy[1].get("citations")
    if isinstance(policy_citations, list) and all(
        isinstance(item, str) for item in policy_citations
    ):
        candidate["citations"] = policy_citations
    candidate["policy_delta"] = policy[1]
    candidate["counter_thesis"] = counter[1].get(
        "counter_thesis", candidate.get("counter_thesis", "")
    )
    candidate["uncertainty"] = counter[1].get(
        "uncertainty", candidate.get("uncertainty", [])
    )
    candidate_output_id = calls.database.save_role_output(
        run_id, "decision_candidate", candidate, "strategy-candidate.v1"
    )
    return {
        "policy_output_id": policy[0],
        "counter_output_id": counter[0],
        "candidate_output_id": candidate_output_id,
    }


def build_research_graph(runtime: AgentRuntime, database: Database | None = None):
    calls = RunCallService(database) if database else None
    graph = StateGraph(DecisionState)

    async def research_node(state: DecisionState) -> dict[str, object]:
        return await _research_node(state, runtime, calls)

    graph.add_node("research", research_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", END)
    return graph.compile()
