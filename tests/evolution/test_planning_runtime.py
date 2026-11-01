from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.contracts_py.decision_hub_contracts.models import CandidateProposal
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRequest
from packages.orchestration.langgraph.evolution_executor import EvolutionJobExecutor
from packages.runtime_adapters.langgraph_agent import evolution


def test_live_planning_routes_role_schemas_and_rejects_unfrozen_evidence(monkeypatch):
    class Runtime:
        enabled = True
        max_attempts = 1
        cost_budget = None

        def __init__(self, config=None, *, response_model, system_prompt):
            self.model = response_model
            self.refs = ["feedback:f1"]

        async def execute(self, request):
            return SimpleNamespace(payload={"schema": self.model, "evidence_refs": self.refs})

    monkeypatch.setattr(evolution, "LangGraphAgentRuntime", Runtime)
    runtime = evolution.EvolutionPlanningRuntime()

    def request(role):
        return AgentRequest(
            role, "frozen input", ("feedback:f1",), datetime.now(UTC) + timedelta(seconds=20)
        )

    async def verify():
        assert (await runtime.execute(request("research_supervisor"))).payload[
            "schema"
        ] is evolution.PlanningTasks
        assert (await runtime.execute(request("counter_thesis"))).payload[
            "schema"
        ] is evolution.SpecialistAssessment
        assert (await runtime.execute(request("decision_synthesis"))).payload[
            "schema"
        ] is evolution.DirectionalCandidateProposal
        runtime.specialist.refs = ["invented:source"]
        with pytest.raises(AgentExecutionError, match="unfrozen evidence"):
            await runtime.execute(request("data_quality"))
        with pytest.raises(AgentExecutionError, match="unknown evolution role"):
            await runtime.execute(request("trade"))

    asyncio.run(verify())


def test_cancelling_evolution_cleans_up_model_operation_and_lease_renewer():
    executor = object.__new__(EvolutionJobExecutor)
    renew = Mock()
    executor.jobs = SimpleNamespace(renew=renew)

    async def verify():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(executor._with_lease(operation(), "job", "worker", 1))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        count = renew.call_count
        await asyncio.sleep(0.4)
        assert renew.call_count == count

    asyncio.run(verify())


def test_model_candidate_schema_requires_all_fields_without_rewriting_storage_contract():
    schema = evolution.DirectionalCandidateProposal.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["execution_scope"]["const"] == "directional_synthesis"
    assert "execution_scope" not in CandidateProposal.model_json_schema()["required"]
