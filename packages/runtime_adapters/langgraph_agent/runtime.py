from __future__ import annotations

import os
import time
from typing import Any

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from packages.kernel.decision_hub_kernel.ports.runtime import AgentRequest, AgentResult
from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


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

    def __init__(self) -> None:
        self._fallback = FakeAgentRuntime()
        self.enabled = os.getenv("DECISION_HUB_LLM_ENABLED", "0") == "1"
        self.api_mode = self.resolve_api_mode()
        self._agent: Any = None
        if self.enabled:
            self._agent = self._build_agent()

    @staticmethod
    def resolve_api_mode() -> str:
        mode = os.getenv("DECISION_HUB_LLM_API_MODE", "responses").strip().lower()
        if mode not in {"responses", "chat"}:
            raise RuntimeError(
                "DECISION_HUB_LLM_API_MODE must be either 'responses' or 'chat'"
            )
        return mode

    def _build_agent(self) -> Any:
        raw_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("SUB2API_API_KEY")
        )
        if not raw_key:
            raise RuntimeError(
                "DECISION_HUB_LLM_ENABLED=1 requires an OpenAI-compatible API key "
                "in OPENAI_API_KEY, DEEPSEEK_API_KEY, or SUB2API_API_KEY"
            )
        model = ChatOpenAI(
            model=os.getenv("DECISION_HUB_MODEL", "deepseek-chat"),
            api_key=SecretStr(raw_key),
            base_url=os.getenv("OPENAI_BASE_URL") or os.getenv("SUB2API_BASE_URL"),
            temperature=0,
            # OpenAI-compatible relays may expose Chat Completions without Responses API.
            # Responses is the default for GPT-5-compatible relays; Chat is an explicit
            # fallback for providers that expose only /chat/completions.
            use_responses_api=self.api_mode == "responses",
        )
        return create_agent(
            model,
            tools=[],
            system_prompt=(
                "You are a constrained market decision specialist. Return only the requested "
                "structured fields. Separate facts from inferences, include a counter-thesis, "
                "and never claim certainty from a single text source. Every field is required; "
                "use an empty string or empty list when a role has no value for that field."
            ),
            response_format=AgentPayload,
            name="decision-hub-specialist",
        )

    async def execute(self, request: AgentRequest) -> AgentResult:
        if not self.enabled:
            return await self._fallback.execute(request)
        started = time.perf_counter()
        prompt = (
            f"Role: {request.role}\n"
            f"Evidence IDs: {', '.join(request.evidence)}\n"
            f"Text:\n{request.text}\n\n"
            "Use the evidence IDs as citations when directly supported. Fill every schema field; "
            "use an empty string or empty list for fields outside this role."
        )
        result = await self._agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        structured = result.get("structured_response")
        if isinstance(structured, AgentPayload):
            payload = structured.model_dump()
        elif isinstance(structured, dict):
            payload = structured
        else:
            raise ValueError("agent_structured_response_missing")
        return AgentResult(
            role=request.role,
            payload=payload,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            cost_usd=0,
        )
