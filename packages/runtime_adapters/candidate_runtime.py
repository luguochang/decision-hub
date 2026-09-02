from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import cast

from packages.contracts_py.decision_hub_contracts.models import CandidateProposal
from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
    AgentRuntime,
    AgentUsage,
)
from packages.runtime_adapters.errors import map_runtime_error

RuntimeCallable = Callable[
    [AgentRequest],
    Awaitable[AgentResult | Mapping[str, object]] | AgentResult | Mapping[str, object],
]


class CandidateConfigurationRuntime:
    """Apply an immutable CandidateProposal to an existing runtime.

    The wrapper changes the actual AgentRequest rather than merely relabelling an
    unchanged runtime. Provider and harness adapters can therefore consume the same
    candidate instructions without the evaluation layer knowing their implementation.
    """

    def __init__(self, base: AgentRuntime, proposal: CandidateProposal) -> None:
        self._base = base
        self.proposal = proposal
        digest = hashlib.sha256(
            proposal.model_dump_json().encode("utf-8")
        ).hexdigest()[:16]
        self.runtime_id = f"candidate-config:{self._base.runtime_id}"
        self.runtime_version = f"candidate-config.v1:{digest}"
        self.max_attempts = self._base.max_attempts
        self.cost_budget = self._base.cost_budget

    async def execute(self, request: AgentRequest) -> AgentResult:
        instructions = (
            f"Candidate type: {self.proposal.candidate_type}",
            f"Candidate version: {self.proposal.version}",
            f"Candidate summary: {self.proposal.summary}",
            *(f"Candidate change: {item}" for item in self.proposal.changes),
        )
        result = await self._base.execute(
            AgentRequest(
                role=request.role,
                text=request.text,
                evidence=request.evidence,
                deadline_at=request.deadline_at,
                max_tokens=request.max_tokens,
                instructions=(*request.instructions, *instructions),
            )
        )
        return AgentResult(
            role=result.role,
            payload=result.payload,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            usage=result.usage,
            provider_id=result.provider_id,
            model=result.model,
            api_mode=result.api_mode,
            schema_version=result.schema_version,
        )


class CandidateAgentRuntime:
    """Thin adapter for an external harness runtime.

    Pi and DSH are intentionally represented by the same product port.  The adapter
    accepts either a native `AgentResult` or a structured mapping supplied by an
    audited bridge; it never owns a graph, ledger, retry loop or promotion decision.
    """

    def __init__(
        self,
        execute_fn: RuntimeCallable,
        *,
        runtime_id: str,
        runtime_version: str,
        max_attempts: int = 1,
        cost_budget: float | None = None,
    ) -> None:
        if not runtime_id or not runtime_version:
            raise ValueError("candidate_runtime_identity_required")
        if max_attempts < 1:
            raise ValueError("candidate_runtime_attempts_invalid")
        self._execute_fn = execute_fn
        self.runtime_id = runtime_id
        self.runtime_version = runtime_version
        self.max_attempts = max_attempts
        self.cost_budget = cost_budget

    async def execute(self, request: AgentRequest) -> AgentResult:
        started = time.perf_counter()
        timeout_seconds = (request.deadline_at - datetime.now(UTC)).total_seconds()
        if timeout_seconds <= 0:
            raise AgentExecutionError(
                "provider_timeout",
                "candidate runtime exceeded its deadline",
                retryable=True,
            )
        try:
            async with asyncio.timeout(timeout_seconds):
                value = self._execute_fn(request)
                if inspect.isawaitable(value):
                    value = await value
        except AgentExecutionError:
            raise
        except TimeoutError as exc:
            raise AgentExecutionError(
                "provider_timeout", "candidate runtime exceeded its deadline", retryable=True
            ) from exc
        except PermissionError as exc:
            raise AgentExecutionError("tool_denied", "candidate runtime tool was denied") from exc
        except BaseException as exc:
            raise map_runtime_error(
                exc,
                provider_id=self.runtime_id,
                fallback_code="candidate_runtime_failed",
            ) from exc
        if isinstance(value, AgentResult):
            _validate_payload(value.payload)
            return AgentResult(
                role=request.role,
                payload=dict(value.payload),
                runtime_id=self.runtime_id,
                runtime_version=self.runtime_version,
                latency_ms=round((time.perf_counter() - started) * 1000),
                cost_usd=value.cost_usd,
                usage=value.usage,
                provider_id=value.provider_id,
                model=value.model,
                api_mode=value.api_mode,
                schema_version=value.schema_version,
            )
        raw_value = cast(object, value)
        if not isinstance(raw_value, Mapping) or not all(isinstance(key, str) for key in raw_value):
            raise AgentExecutionError(
                "structured_output_invalid",
                "candidate runtime returned a non-object payload",
            )
        payload = dict(raw_value)
        _validate_payload(payload)
        return AgentResult(
            role=request.role,
            payload=payload,
            runtime_id=self.runtime_id,
            runtime_version=self.runtime_version,
            latency_ms=round((time.perf_counter() - started) * 1000),
            usage=AgentUsage(cost_status="unknown"),
            api_mode="candidate",
        )


class PiAgentRuntime(CandidateAgentRuntime):
    """Pi SDK bridge; active product code only depends on `AgentRuntime`."""

    def __init__(
        self,
        execute_fn: RuntimeCallable,
        *,
        version: str = "pi-adapter.v1",
        max_attempts: int = 1,
        cost_budget: float | None = None,
    ) -> None:
        super().__init__(
            execute_fn,
            runtime_id="pi",
            runtime_version=version,
            max_attempts=max_attempts,
            cost_budget=cost_budget,
        )


class DshAgentRuntime(CandidateAgentRuntime):
    """DSH harness bridge for replay/holdout/shadow candidates."""

    def __init__(
        self,
        execute_fn: RuntimeCallable,
        *,
        version: str = "dsh-adapter.v1",
        max_attempts: int = 1,
        cost_budget: float | None = None,
    ) -> None:
        super().__init__(
            execute_fn,
            runtime_id="dsh",
            runtime_version=version,
            max_attempts=max_attempts,
            cost_budget=cost_budget,
        )


def _validate_payload(payload: Mapping[str, object]) -> None:
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AgentExecutionError(
            "structured_output_invalid",
            "candidate runtime returned a non-JSON payload",
        ) from exc
