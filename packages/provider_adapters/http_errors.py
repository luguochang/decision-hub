from __future__ import annotations

import httpx

from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)


def classify_provider_exception(exc: Exception) -> ResearchCapabilityError:
    """Map transport details to stable provider errors without leaking payloads."""

    if isinstance(exc, ResearchCapabilityError):
        return exc
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ResearchCapabilityError(
            "provider_timeout",
            "provider request exceeded its route timeout",
            retryable=True,
            origin="transport",
            cause_code="timeout",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ResearchCapabilityError(
                "provider_rate_limited",
                "provider rate limit was reached",
                retryable=True,
                origin="provider",
                cause_code="http_429",
            )
        if status >= 500:
            return ResearchCapabilityError(
                "provider_upstream_unavailable",
                "provider returned an upstream server error",
                retryable=True,
                origin="provider",
                cause_code=f"http_{status}",
            )
        return ResearchCapabilityError(
            "provider_request_rejected",
            "provider rejected the request",
            retryable=False,
            origin="provider",
            cause_code=f"http_{status}",
        )
    if isinstance(exc, httpx.TransportError):
        return ResearchCapabilityError(
            "provider_transport_unavailable",
            "provider transport is unavailable",
            retryable=True,
            origin="transport",
            cause_code=type(exc).__name__.lower(),
        )
    return ResearchCapabilityError(
        "provider_execution_failed",
        "provider adapter failed",
        retryable=bool(getattr(exc, "retryable", False)),
        origin="provider",
        cause_code=type(exc).__name__.lower(),
    )
