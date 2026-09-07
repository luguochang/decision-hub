from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.runtime_adapters.langgraph_agent.provider_config import (
    ProviderCapabilityManifest,
    ProviderConfig,
)


def test_provider_config_reads_responses_mode_without_persisting_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_PROVIDER_ID", "codexai-relay")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://codexai.club/v1")
    monkeypatch.setenv("DECISION_HUB_MODEL", "gpt-5.5")
    monkeypatch.setenv("DECISION_HUB_LLM_API_MODE", "responses")
    monkeypatch.setenv("OPENAI_API_KEY", "temporary-test-secret")

    config = ProviderConfig.from_env()
    manifest = config.capability_manifest()

    assert config.provider_id == "codexai-relay"
    assert config.model == "gpt-5.5"
    assert config.api_mode == "responses"
    assert "responses" in manifest.supported_api_modes
    assert "temporary-test-secret" not in repr(config)
    assert "temporary-test-secret" not in repr(config.model_dump())
    assert "temporary-test-secret" not in repr(manifest)


def test_provider_config_reads_chat_mode_without_changing_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_API_MODE", "chat")

    config = ProviderConfig.from_env()

    assert config.api_mode == "chat"
    assert config.capability_manifest().supports_api_mode("chat") is True


def test_provider_config_treats_empty_optional_base_urls_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("SUB2API_BASE_URL", "")

    config = ProviderConfig.from_env()

    assert config.base_url is None


def test_provider_config_rejects_unknown_api_mode_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_LLM_API_MODE", "xml")

    with pytest.raises(ValueError, match="LLM_API_MODE"):
        ProviderConfig.from_env()


def test_provider_config_rejects_invalid_budget_and_retry_values() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(max_retries=-1)

    with pytest.raises(ValidationError):
        ProviderConfig(token_budget=0)

    with pytest.raises(ValidationError, match="cost_budget requires"):
        ProviderConfig(cost_budget=1)


def test_capability_manifest_rejects_unsupported_selected_mode() -> None:
    with pytest.raises(ValidationError):
        ProviderConfig(
            api_mode="chat",
            supported_api_modes=("responses",),
        )


def test_capability_manifest_is_strict_and_versioned() -> None:
    manifest = ProviderCapabilityManifest(
        provider_id="openai-compatible",
        model="deepseek-chat",
        supported_api_modes=("responses", "chat"),
        supports_structured_output=True,
        capability_version="provider-capabilities.v1",
    )

    assert manifest.capability_version == "provider-capabilities.v1"
    assert manifest.model_json_schema()["additionalProperties"] is False
