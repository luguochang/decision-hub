from __future__ import annotations

from typing import Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

ApiMode = Literal["responses", "chat"]
_API_MODE_ADAPTER = TypeAdapter(ApiMode)


def validate_api_mode(value: str) -> ApiMode:
    return _API_MODE_ADAPTER.validate_python(value.strip().lower())


class ProviderCapabilityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    supported_api_modes: tuple[ApiMode, ...] = Field(min_length=1)
    supports_structured_output: bool
    capability_version: str = Field(min_length=1)

    def supports_api_mode(self, api_mode: ApiMode) -> bool:
        return api_mode in self.supported_api_modes


class ProviderConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DECISION_HUB_",
        env_file=None,
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )

    provider_id: str = Field(default="openai-compatible", min_length=1)
    base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "SUB2API_BASE_URL"),
    )
    model: str = Field(default="deepseek-chat", min_length=1)
    api_mode: ApiMode = Field(
        default="responses",
        validation_alias="DECISION_HUB_LLM_API_MODE",
    )
    timeout_seconds: float = Field(default=60, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    token_budget: int = Field(default=4_000, gt=0)
    cost_budget: float | None = Field(default=None, gt=0)
    input_cost_per_million_tokens: float | None = Field(default=None, ge=0)
    output_cost_per_million_tokens: float | None = Field(default=None, ge=0)
    pricing_version: str | None = Field(default=None, min_length=1)
    supports_structured_output: bool = True
    supported_api_modes: tuple[ApiMode, ...] = ("responses", "chat")
    capability_version: str = Field(default="provider-capabilities.v1", min_length=1)

    @model_validator(mode="after")
    def selected_mode_must_be_supported(self) -> ProviderConfig:
        if self.api_mode not in self.supported_api_modes:
            raise ValueError(f"api_mode {self.api_mode!r} is not declared in supported_api_modes")
        pricing = (
            self.input_cost_per_million_tokens,
            self.output_cost_per_million_tokens,
            self.pricing_version,
        )
        if self.cost_budget is not None and any(item is None for item in pricing):
            raise ValueError("cost_budget requires input/output token prices and pricing_version")
        return self

    @classmethod
    def from_env(cls) -> ProviderConfig:
        return cls()

    def capability_manifest(self) -> ProviderCapabilityManifest:
        return ProviderCapabilityManifest(
            provider_id=self.provider_id,
            model=self.model,
            supported_api_modes=self.supported_api_modes,
            supports_structured_output=self.supports_structured_output,
            capability_version=self.capability_version,
        )
