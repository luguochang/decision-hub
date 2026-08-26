from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from langgraph.graph import END, START, StateGraph

from packages.kernel.decision_hub_kernel.ports.runtime import AgentRequest, AgentRuntime
from packages.orchestration.langgraph.state.decision import DecisionState


async def _research_node(state: DecisionState, runtime: AgentRuntime) -> dict[str, object]:
    text = state["text"]
    evidence = tuple(state.get("evidence_ids", []))
    deadline = datetime.now(UTC) + timedelta(seconds=60)
    policy, counter = await asyncio.gather(
        runtime.execute(
            AgentRequest(role="policy_delta", text=text, evidence=evidence, deadline_at=deadline)
        ),
        runtime.execute(
            AgentRequest(role="counter_thesis", text=text, evidence=evidence, deadline_at=deadline)
        ),
    )
    decision = await runtime.execute(
        AgentRequest(role="decision_synthesis", text=text, evidence=evidence, deadline_at=deadline)
    )
    candidate = dict(decision.payload)
    candidate["policy_delta"] = policy.payload
    candidate["counter_thesis"] = counter.payload.get(
        "counter_thesis", candidate.get("counter_thesis", "")
    )
    candidate["uncertainty"] = counter.payload.get("uncertainty", candidate.get("uncertainty", []))
    return {"candidate": candidate}


def build_research_graph(runtime: AgentRuntime):
    graph = StateGraph(DecisionState)

    async def research_node(state: DecisionState) -> dict[str, object]:
        return await _research_node(state, runtime)

    graph.add_node("research", research_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", END)
    return graph.compile()
