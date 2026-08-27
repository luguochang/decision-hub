from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import cast

from packages.kernel.decision_hub_kernel.ports.runtime import (
    AgentExecutionError,
    AgentRequest,
    AgentResult,
    AgentUsage,
)
from packages.runtime_adapters.errors import map_runtime_error

RuntimeCallable = Callable[
    [AgentRequest],
    Awaitable[AgentResult | Mapping[str, object]] | AgentResult | Mapping[str, object],
]


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
