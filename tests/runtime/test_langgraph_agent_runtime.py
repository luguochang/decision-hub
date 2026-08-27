from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError, AgentRequest
from packages.runtime_adapters.langgraph_agent import runtime as runtime_module
from packages.runtime_adapters.langgraph_agent.provider_config import ProviderConfig
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
                    "headline": "headline",
                    "summary": "summary",
                    "facts": ["fact"],
                    "inferences": [],
                    "citations": ["obs_fixture"],
                    "counter_thesis": "counter",
                    "uncertainty": [],
                    "transmission_chain": [],
                    "trigger": "trigger",
                    "invalidation": "invalidation",
                    "delta": "delta",
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

    with pytest.raises(
        AgentExecutionError, match="requires an OpenAI-compatible API key"
    ) as raised:
        LangGraphAgentRuntime()
    assert raised.value.error_code == "configuration_invalid"


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


def test_runtime_builds_langchain_model_from_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_chat_openai(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    def fake_create_agent(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")
    monkeypatch.setattr(runtime_module, "ChatOpenAI", fake_chat_openai)
    monkeypatch.setattr(runtime_module, "create_agent", fake_create_agent)

    runtime = LangGraphAgentRuntime(
        ProviderConfig.model_validate(
            {
                "provider_id": "fixture-provider",
                "base_url": "https://provider.example/v1",
                "model": "fixture-model",
                "api_mode": "chat",
                "timeout_seconds": 12,
                "max_retries": 1,
                "token_budget": 2_048,
            }
        )
    )

    assert runtime.capability_manifest.provider_id == "fixture-provider"
    assert captured["model"] == "fixture-model"
    assert captured["base_url"] == "https://provider.example/v1"
    assert captured["use_responses_api"] is False
    assert captured["timeout"] == 12
    assert captured["max_retries"] == 0
    assert runtime.max_attempts == 2
    assert captured["max_completion_tokens"] == 2_048
    assert "temporary-test-secret" not in repr(runtime.provider_config)


def test_runtime_rejects_provider_without_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")

    with pytest.raises(AgentExecutionError, match="does not support structured output") as raised:
        LangGraphAgentRuntime(ProviderConfig(supports_structured_output=False))
    assert raised.value.error_code == "configuration_invalid"


def test_runtime_maps_timeout_to_product_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowAgent:
        async def ainvoke(self, _input: dict[str, object]) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return {}

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")
    def build_slow_agent(_self: LangGraphAgentRuntime) -> SlowAgent:
        return SlowAgent()

    monkeypatch.setattr(LangGraphAgentRuntime, "_build_agent", build_slow_agent)
    runtime = LangGraphAgentRuntime()
    request = AgentRequest(
        role="decision_synthesis",
        text="fixture",
        evidence=("obs_fixture",),
        deadline_at=datetime.now(UTC) + timedelta(milliseconds=1),
    )

    with pytest.raises(AgentExecutionError, match="provider call exceeded") as raised:
        asyncio.run(runtime.execute(request))

    assert raised.value.error_code == "provider_timeout"


def test_runtime_maps_rate_limit_and_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAgent:
        def __init__(self, error: BaseException) -> None:
            self.error = error

        async def ainvoke(self, _input: dict[str, object]) -> dict[str, object]:
            raise self.error

    class RateLimitError(Exception):
        status_code = 429

    class ServerError(Exception):
        status_code = 503

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")
    failures = (
        (RateLimitError("slow down"), "provider_rate_limited"),
        (ServerError("down"), "provider_unavailable"),
    )
    for error, code in failures:
        def build_failing_agent(
            _self: LangGraphAgentRuntime, error: BaseException = error
        ) -> FailingAgent:
            return FailingAgent(error)

        monkeypatch.setattr(
            LangGraphAgentRuntime,
            "_build_agent",
            build_failing_agent,
        )
        runtime = LangGraphAgentRuntime()
        with pytest.raises(AgentExecutionError) as raised:
            asyncio.run(runtime.execute(_request()))
        assert raised.value.error_code == code


def test_runtime_estimates_cost_from_versioned_pricing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UsageAgent:
        async def ainvoke(self, _input: dict[str, object]) -> dict[str, object]:
            return {
                "structured_response": {
                    "direction": "short",
                    "probability": 0.6,
                    "headline": "headline",
                    "summary": "summary",
                    "facts": ["fact"],
                    "inferences": [],
                    "citations": ["obs_fixture"],
                    "counter_thesis": "counter",
                    "uncertainty": [],
                    "transmission_chain": [],
                    "trigger": "trigger",
                    "invalidation": "invalidation",
                    "delta": "delta",
                },
                "usage_metadata": {
                    "input_tokens": 1_000,
                    "output_tokens": 500,
                    "total_tokens": 1_500,
                },
            }

    monkeypatch.setenv("DECISION_HUB_LLM_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")
    def build_usage_agent(_self: LangGraphAgentRuntime) -> UsageAgent:
        return UsageAgent()

    monkeypatch.setattr(LangGraphAgentRuntime, "_build_agent", build_usage_agent)
    runtime = LangGraphAgentRuntime(
        ProviderConfig(
            cost_budget=0.1,
            input_cost_per_million_tokens=10,
            output_cost_per_million_tokens=20,
            pricing_version="fixture-pricing.v1",
        )
    )

    result = asyncio.run(runtime.execute(_request()))

    assert result.usage.cost_status == "estimated"
    assert result.usage.cost_usd == pytest.approx(0.02)
    assert result.usage.pricing_version == "fixture-pricing.v1"
    assert result.cost_usd == pytest.approx(0.02)
