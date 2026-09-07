from __future__ import annotations

import httpx
import pytest

from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.http_errors import classify_provider_exception


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://provider.example/data")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("provider response", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "error_code", "retryable", "origin", "cause_code"),
    [
        (
            httpx.ReadTimeout(
                "slow", request=httpx.Request("GET", "https://provider.example")
            ),
            "provider_timeout",
            True,
            "transport",
            "timeout",
        ),
        (_http_error(429), "provider_rate_limited", True, "provider", "http_429"),
        (_http_error(503), "provider_upstream_unavailable", True, "provider", "http_503"),
        (_http_error(400), "provider_request_rejected", False, "provider", "http_400"),
        (
            httpx.ConnectError(
                "offline", request=httpx.Request("GET", "https://provider.example")
            ),
            "provider_transport_unavailable",
            True,
            "transport",
            "connecterror",
        ),
    ],
)
def test_classify_provider_http_failures(
    exc: Exception,
    error_code: str,
    retryable: bool,
    origin: str,
    cause_code: str,
) -> None:
    error = classify_provider_exception(exc)

    assert error.error_code == error_code
    assert error.retryable is retryable
    assert error.origin == origin
    assert error.cause_code == cause_code


def test_existing_capability_error_is_not_collapsed() -> None:
    original = ResearchCapabilityError(
        "provider_payload_invalid",
        "invalid typed payload",
        retryable=False,
        origin="provider",
        cause_code="schema",
    )

    assert classify_provider_exception(original) is original
