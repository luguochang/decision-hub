from __future__ import annotations

from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError


def map_runtime_error(
    error: BaseException,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    fallback_code: str = "unknown_runtime_error",
) -> AgentExecutionError:
    """Map provider/harness exceptions into the bounded product taxonomy."""

    text = str(error).lower()
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if not isinstance(status, int):
        response_status = getattr(response, "status_code", None)
        status = response_status if isinstance(response_status, int) else None
    if status == 429 or "rate limit" in text or "rate_limited" in text:
        return AgentExecutionError(
            "provider_rate_limited",
            "provider rate limit reached",
            retryable=True,
            provider_id=provider_id,
            model=model,
        )
    if status in {408, 504} or "timed out" in text or "timeout" in text:
        return AgentExecutionError(
            "provider_timeout",
            "provider call exceeded the request deadline",
            retryable=True,
            provider_id=provider_id,
            model=model,
        )
    if isinstance(status, int) and status >= 500:
        return AgentExecutionError(
            "provider_unavailable",
            "provider returned a server error",
            retryable=True,
            provider_id=provider_id,
            model=model,
        )
    return AgentExecutionError(
        fallback_code,
        "runtime failed",
        provider_id=provider_id,
        model=model,
    )
