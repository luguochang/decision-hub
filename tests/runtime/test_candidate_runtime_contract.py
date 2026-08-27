from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
)
from packages.runtime_adapters.candidate_runtime import (
    DshAgentRuntime,
    PiAgentRuntime,
    RuntimeCallable,
)


def request(*, timeout_ms: int = 1000) -> AgentRequest:
    return AgentRequest(
        role="counter_thesis",
        text="Policy remains restrictive.",
        evidence=("evidence:fed:1",),
        deadline_at=datetime.now(UTC) + timedelta(milliseconds=timeout_ms),
    )


def runtime(adapter: str, execute_fn: RuntimeCallable):
    if adapter == "pi":
        return PiAgentRuntime(execute_fn)
    return DshAgentRuntime(execute_fn)


@pytest.mark.parametrize("adapter", ["pi", "dsh"])
def test_harness_adapters_share_success_contract(adapter: str) -> None:
    async def execute(_request: AgentRequest) -> Mapping[str, object]:
        return {"counter_thesis": "Growth may weaken first.", "citations": ["evidence:fed:1"]}

    selected = runtime(adapter, execute)
    result = asyncio.run(selected.execute(request()))

    assert result.runtime_id == adapter
    assert result.runtime_version.endswith("adapter.v1")
    assert result.payload["counter_thesis"] == "Growth may weaken first."
    assert selected.max_attempts == 1


@pytest.mark.parametrize("adapter", ["pi", "dsh"])
@pytest.mark.parametrize(
    ("status_code", "error_code"),
    [(429, "provider_rate_limited"), (503, "provider_unavailable")],
)
def test_harness_adapters_share_provider_error_taxonomy(
    adapter: str, status_code: int, error_code: str
) -> None:
    class ProviderError(Exception):
        def __init__(self) -> None:
            super().__init__(f"provider status {status_code}")
            self.status_code = status_code

    async def execute(_request: AgentRequest) -> Mapping[str, object]:
        raise ProviderError

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(runtime(adapter, execute).execute(request()))

    assert raised.value.error_code == error_code
    assert raised.value.retryable is True


@pytest.mark.parametrize("adapter", ["pi", "dsh"])
def test_harness_adapters_enforce_deadline_and_tool_denial(adapter: str) -> None:
    async def slow(_request: AgentRequest) -> Mapping[str, object]:
        await asyncio.sleep(0.05)
        return {"late": True}

    with pytest.raises(AgentExecutionError) as timeout:
        asyncio.run(runtime(adapter, slow).execute(request(timeout_ms=1)))
    assert timeout.value.error_code == "provider_timeout"

    async def denied(_request: AgentRequest) -> Mapping[str, object]:
        raise PermissionError("shell denied")

    with pytest.raises(AgentExecutionError) as tool_denied:
        asyncio.run(runtime(adapter, denied).execute(request()))
    assert tool_denied.value.error_code == "tool_denied"


@pytest.mark.parametrize("adapter", ["pi", "dsh"])
def test_harness_adapters_reject_non_json_structured_output(adapter: str) -> None:
    async def invalid(_request: AgentRequest) -> Mapping[str, object]:
        return {"opaque": object()}

    with pytest.raises(AgentExecutionError) as raised:
        asyncio.run(runtime(adapter, invalid).execute(request()))

    assert raised.value.error_code == "structured_output_invalid"
