from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
    AgentUsage,
)
from packages.runtime_adapters.errors import map_runtime_error
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime
from packages.runtime_adapters.langgraph_agent.provider_config import (
    ProviderConfig,
    validate_api_mode,
)


class AgentPayload(BaseModel):
    """Strict cross-provider payload; every role fills unused fields explicitly."""

    model_config = ConfigDict(extra="forbid")

    direction: str = Field(description="long, short, neutral, or no_trade")
    probability: float = Field(ge=0, le=1)
    headline: str
    summary: str
    facts: list[str]
    inferences: list[str]
    counter_thesis: str
    uncertainty: list[str]
    transmission_chain: list[str]
    citations: list[str]
    trigger: str
    invalidation: str
    delta: str


class LangGraphAgentRuntime:
    """Production adapter seam; uses a structured agent when credentials are configured.

    The R0 deterministic path intentionally falls back to FakeAgentRuntime for replay and
    local smoke tests. This keeps CI reproducible while preserving the official runtime port.
    """

    runtime_id = "langgraph-native"
    runtime_version = "langgraph-native.v1"

    def __init__(
        self,
        provider_config: ProviderConfig | None = None,
        *,
        response_model: type[BaseModel] = AgentPayload,
        fallback_runtime: FakeAgentRuntime | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._fallback = fallback_runtime or FakeAgentRuntime()
        self.response_model = response_model
        self.system_prompt = system_prompt or (
            "You are a constrained market decision specialist. Return only the requested "
            "structured fields. Separate facts from inferences, include a counter-thesis, "
            "and never claim certainty from a single text source. Every field is required; "
            "use an empty string or empty list when a role has no value for that field."
        )
        self.enabled = os.getenv("DECISION_HUB_LLM_ENABLED", "0") == "1"
        self.provider_config = provider_config or ProviderConfig.from_env()
        self.capability_manifest = self.provider_config.capability_manifest()
        self.api_mode = self.provider_config.api_mode
        self.max_attempts = self.provider_config.max_retries + 1
        self.cost_budget = self.provider_config.cost_budget
        self._agent: Any = None
        if self.enabled:
            self._agent = self._build_agent()

    @property
    def effective_runtime_id(self) -> str:
        return self.runtime_id if self.enabled else self._fallback.runtime_id

    @property
    def effective_runtime_version(self) -> str:
        return self.runtime_version if self.enabled else self._fallback.runtime_version

    @staticmethod
    def resolve_api_mode() -> str:
        try:
            return validate_api_mode(os.getenv("DECISION_HUB_LLM_API_MODE", "responses"))
        except ValueError as exc:
            raise RuntimeError(
                "DECISION_HUB_LLM_API_MODE must be either 'responses' or 'chat'"
            ) from exc

    def _build_agent(self) -> Any:
        raw_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("SUB2API_API_KEY")
        )
        if not raw_key:
            raise AgentExecutionError(
                "configuration_invalid",
                "DECISION_HUB_LLM_ENABLED=1 requires an OpenAI-compatible API key "
                "in OPENAI_API_KEY, DEEPSEEK_API_KEY, or SUB2API_API_KEY",
            )
        if not self.capability_manifest.supports_structured_output:
            raise AgentExecutionError(
                "configuration_invalid",
                "configured Provider does not support structured output",
                provider_id=self.provider_config.provider_id,
                model=self.provider_config.model,
            )
        model = ChatOpenAI(
            model=self.provider_config.model,
            api_key=SecretStr(raw_key),
            base_url=(
                str(self.provider_config.base_url)
                if self.provider_config.base_url is not None
                else None
            ),
            temperature=0,
            timeout=self.provider_config.timeout_seconds,
            # Workflow retries are owned by LangGraph so each attempt is visible in RunCall.
            max_retries=0,
            max_completion_tokens=self.provider_config.token_budget,
            # OpenAI-compatible relays may expose Chat Completions without Responses API.
            # Responses is the default for GPT-5-compatible relays; Chat is an explicit
            # fallback for providers that expose only /chat/completions.
            use_responses_api=self.provider_config.api_mode == "responses",
        )
        return create_agent(
            model,
            tools=[],
            system_prompt=self.system_prompt,
            response_format=self.response_model,
            name="decision-hub-specialist",
        )

    async def execute(self, request: AgentRequest) -> AgentResult:
        if not self.enabled:
            return await self._fallback.execute(request)
        started = time.perf_counter()
        prompt = (
            f"Role: {request.role}\n"
            f"Evidence IDs: {', '.join(request.evidence)}\n"
            + (
                "Candidate instructions:\n"
                + "\n".join(f"- {item}" for item in request.instructions)
                + "\n"
                if request.instructions
                else ""
            )
            + f"Text:\n{request.text}\n\n"
            + "Use the evidence IDs as citations when directly supported. Fill every schema "
            + "field; use an empty string or empty list for fields outside this role."
        )
        if self._agent is None:
            raise AgentExecutionError("configuration_invalid", "agent is not configured")
        timeout_seconds = max(
            0.001,
            (request.deadline_at - datetime.now(UTC)).total_seconds(),
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await self._agent.ainvoke(
                    {"messages": [{"role": "user", "content": prompt}]}
                )
        except TimeoutError as exc:
            raise AgentExecutionError(
                "provider_timeout",
                "provider call exceeded the request deadline",
                retryable=True,
                provider_id=self.provider_config.provider_id,
                model=self.provider_config.model,
            ) from exc
        except BaseException as exc:
            raise map_runtime_error(
                exc,
                provider_id=self.provider_config.provider_id,
                model=self.provider_config.model,
            ) from exc
        structured = result.get("structured_response")
        try:
            payload = self.response_model.model_validate(structured).model_dump()
        except Exception as exc:
            raise AgentExecutionError(
                "structured_output_invalid",
                f"provider response did not match {self.response_model.__name__}",
                provider_id=self.provider_config.provider_id,
                model=self.provider_config.model,
            ) from exc
        usage = self._extract_usage(result)
        return AgentResult(
            role=request.role,
            payload=payload,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=usage.cost_usd,
            usage=usage,
            provider_id=self.provider_config.provider_id,
            model=self.provider_config.model,
            api_mode=self.provider_config.api_mode,
        )

    def _extract_usage(self, result: dict[str, object]) -> AgentUsage:
        usage = result.get("usage_metadata")
        if not isinstance(usage, dict):
            messages = result.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    candidate = getattr(message, "usage_metadata", None)
                    if isinstance(candidate, dict):
                        usage = candidate
                        break
        if not isinstance(usage, dict):
            return AgentUsage(cost_status="unknown")
        prompt_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        completion_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        total_tokens = usage.get("total_tokens")
        if (
            total_tokens is None
            and isinstance(prompt_tokens, int)
            and isinstance(completion_tokens, int)
        ):
            total_tokens = prompt_tokens + completion_tokens
        cost_usd: float | None = None
        cost_status = "unknown"
        if (
            isinstance(prompt_tokens, int)
            and isinstance(completion_tokens, int)
            and self.provider_config.input_cost_per_million_tokens is not None
            and self.provider_config.output_cost_per_million_tokens is not None
        ):
            cost_usd = (
                prompt_tokens * self.provider_config.input_cost_per_million_tokens
                + completion_tokens * self.provider_config.output_cost_per_million_tokens
            ) / 1_000_000
            cost_status = "estimated"
        return AgentUsage(
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
            total_tokens=total_tokens if isinstance(total_tokens, int) else None,
            cost_usd=cost_usd,
            cost_status=cost_status,
            pricing_version=(
                self.provider_config.pricing_version if cost_status == "estimated" else None
            ),
        )
