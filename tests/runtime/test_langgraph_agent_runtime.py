from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentRequest
from packages.runtime_adapters.langgraph_agent.runtime import AgentPayload, LangGraphAgentRuntime


def _request() -> AgentRequest:
    return AgentRequest(
        role="decision_synthesis",
        text="The committee remains data dependent.",
        evidence=("obs_fixture",),
        deadline_at=datetime.now(UTC) + timedelta(seconds=30),
    )


def test_runtime_defaults_to_deterministic_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISION_HUB_LLM_ENABLED", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = LangGraphAgentRuntime()

    result = __import__("asyncio").run(runtime.execute(_request()))

    assert runtime.enabled is False
    assert result.runtime_id == "fake"
    assert result.payload["citations"] == ["obs_fixture"]


def test_runtime_maps_structured_agent_result_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStructuredAgent:
        async def ainvoke(self, _input: dict[str, object]) -> dict[str, object]:
            return {
                "structured_response": {
                    "direction": "short",
                    "probability": 0.6,
                    "facts": ["fact"],
                    "citations": ["obs_fixture"],
                    "counter_thesis": "counter",
                    "trigger": "trigger",
                    "invalidation": "invalidation",
                }
            }

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")

    def build_fake_agent(_self: LangGraphAgentRuntime) -> FakeStructuredAgent:
        return FakeStructuredAgent()

    monkeypatch.setattr(LangGraphAgentRuntime, "_build_agent", build_fake_agent)
    runtime = LangGraphAgentRuntime()

    result = __import__("asyncio").run(runtime.execute(_request()))

    assert result.runtime_id == "langgraph-native"
    assert result.payload["direction"] == "short"
    assert result.payload["probability"] == 0.6
    assert "temporary-test-secret" not in repr(result)


def test_runtime_requires_key_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SUB2API_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="requires an OpenAI-compatible API key"):
        LangGraphAgentRuntime()


def test_agent_payload_is_strict_provider_schema() -> None:
    schema = AgentPayload.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_runtime_api_mode_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECISION_HUB_LLM_API_MODE", raising=False)
    assert LangGraphAgentRuntime.resolve_api_mode() == "responses"

    monkeypatch.setenv("DECISION_HUB_LLM_API_MODE", "chat")
    assert LangGraphAgentRuntime.resolve_api_mode() == "chat"

    monkeypatch.setenv("DECISION_HUB_LLM_API_MODE", "invalid")
    with pytest.raises(RuntimeError, match="must be either"):
        LangGraphAgentRuntime.resolve_api_mode()
