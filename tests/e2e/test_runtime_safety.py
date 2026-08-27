from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    ForecastRecord,
    OutboxRecord,
)
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
    AgentUsage,
)
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


class FailingRuntime:
    runtime_id = "failure-fixture"
    runtime_version = "failure-fixture.v1"
    max_attempts = 1
    cost_budget: float | None = None

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code

    async def execute(self, request: AgentRequest) -> AgentResult:
        del request
        raise AgentExecutionError(self.error_code, "injected provider failure")


class RetryOnceRuntime(FakeAgentRuntime):
    max_attempts = 2

    def __init__(self) -> None:
        self.failed = False

    async def execute(self, request: AgentRequest) -> AgentResult:
        if request.role == "policy_delta" and not self.failed:
            self.failed = True
            raise AgentExecutionError(
                "provider_timeout", "retry fixture", retryable=True
            )
        return await super().execute(request)


class PricedRuntime(FakeAgentRuntime):
    cost_budget: float | None = 0.1

    async def execute(self, request: AgentRequest) -> AgentResult:
        result = await super().execute(request)
        return AgentResult(
            role=result.role,
            payload=result.payload,
            runtime_id=result.runtime_id,
            runtime_version=result.runtime_version,
            latency_ms=result.latency_ms,
            cost_usd=0.06,
            usage=AgentUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.06,
                cost_status="estimated",
                pricing_version="fixture-pricing.v1",
            ),
            provider_id="priced-fixture",
            model="deterministic-fixture",
            api_mode="offline",
        )


@pytest.mark.parametrize(
    "error_code",
    [
        "provider_timeout",
        "provider_rate_limited",
        "provider_unavailable",
        "structured_output_invalid",
        "configuration_invalid",
        "unknown_runtime_error",
    ],
)
def test_provider_failures_are_visible_and_never_publish(
    tmp_path: Path, error_code: str
) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / f'{error_code}.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, FailingRuntime(error_code))

    with pytest.raises(AgentExecutionError) as captured:
        asyncio.run(
            service.submit_and_run(
                ObservationCreate(text=f"provider failure fixture {error_code}")
            )
        )

    assert captured.value.error_code == error_code
    run = database.latest_runs(1)[0]
    inspector = database.get_run_inspector(run.run_id)
    assert run.status.value == "failed"
    assert run.error_code == error_code
    assert inspector is not None
    assert any(call.error_code == error_code for call in inspector.calls)
    assert any(
        step.step_name == "research"
        and step.status == "failed"
        and step.error_code == error_code
        for step in inspector.steps
    )
    assert inspector.artifact is None
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 0
        assert session.query(ForecastRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0


def test_langgraph_retry_preserves_step_and_call_attempts(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'retry.sqlite3'}")
    database.create_all()

    _, run_id, _ = asyncio.run(
        AnalyzeTextService(database, RetryOnceRuntime()).submit_and_run(
            ObservationCreate(text="Powell says rates may stay higher for longer.")
        )
    )

    inspector = database.get_run_inspector(run_id)
    assert inspector is not None
    research_steps = [
        step for step in inspector.steps if step.step_name == "research"
    ]
    policy_calls = [
        call for call in inspector.calls if call.role == "policy_delta"
    ]
    assert [(step.attempt, step.status) for step in research_steps] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    assert [(call.attempt, call.status) for call in policy_calls] == [
        (1, "failed"),
        (2, "succeeded"),
    ]
    assert inspector.artifact is not None


def test_cost_budget_exhaustion_fails_closed(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'budget.sqlite3'}")
    database.create_all()
    service = AnalyzeTextService(database, PricedRuntime())

    with pytest.raises(AgentExecutionError) as captured:
        asyncio.run(
            service.submit_and_run(
                ObservationCreate(text="Powell says rates may stay higher for longer.")
            )
        )

    assert captured.value.error_code == "budget_exhausted"
    run = database.latest_runs(1)[0]
    inspector = database.get_run_inspector(run.run_id)
    assert inspector is not None
    assert run.status.value == "failed"
    assert run.error_code == "budget_exhausted"
    assert inspector.artifact is None
    assert len(inspector.calls) == 2
    assert all(call.status == "succeeded" for call in inspector.calls)
    assert all(call.pricing_version == "fixture-pricing.v1" for call in inspector.calls)
    with database.session() as session:
        assert session.query(ArtifactRecord).count() == 0
        assert session.query(ForecastRecord).count() == 0
        assert session.query(OutboxRecord).count() == 0
