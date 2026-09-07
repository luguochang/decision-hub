from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts_py.decision_hub_contracts import (
    ProviderRoute,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
)
from packages.provider_adapters.routing import ProviderCapabilityRouter

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _route(
    provider_id: str,
    *,
    role: str,
    priority: int,
    domains: list[str] | None = None,
    requires_event_window: bool = False,
) -> ProviderRoute:
    return ProviderRoute.model_validate(
        {
            "provider_id": provider_id,
            "adapter_ref": f"adapter://{provider_id}",
            "route_role": role,
            "priority": priority,
            "service_tier": "free_proxy",
            "allowed_domains": domains or [f"{provider_id}.example"],
            "timeout_seconds": 1,
            "cost_policy_ref": "known-free.v1",
            "license_status": "approved",
            "audit_status": "approved",
            "requires_event_window": requires_event_window,
        }
    )


def _query() -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "request-router-1",
            "capability_id": "market.crypto_spot",
            "requirement_id": "crypto_spot_confirmation",
            "query": "BTC event window",
            "target_url": None,
            "symbols": ["BTC-USDT"],
            "fields": ["price", "volume", "event_return"],
            "allowed_domains": [],
            "max_results": 10,
            "max_cost_usd": 0.1,
            "research_session_id": "session-router-1",
            "round": 1,
            "mode": "replay",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=1),
        }
    )


def _event_query() -> ResearchCapabilityQuery:
    return _query().model_copy(
        update={
            "event_id": "event-1",
            "event_at": NOW,
            "window_start_at": NOW - timedelta(minutes=5),
            "window_end_at": NOW + timedelta(minutes=1),
            "requested_event_offsets": ["t-5m", "t+1m"],
        }
    )


class StubAdapter:
    capability_id = "market.crypto_spot"
    supported_modes = frozenset({"live", "replay"})

    def __init__(
        self,
        provider_id: str,
        behavior: Callable[[ResearchCapabilityQuery], ResearchCapabilityResult],
        *,
        supported_fields: frozenset[str] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.behavior = behavior
        self.calls = 0
        if supported_fields is not None:
            self.supported_fields = supported_fields

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.calls += 1
        return self.behavior(query)


def _success(provider_id: str) -> Callable[[ResearchCapabilityQuery], ResearchCapabilityResult]:
    def execute(query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=provider_id,
            evidence_candidates=[],
            facts=[],
            cost_usd=0.0,
            completed_at=NOW + timedelta(seconds=1),
        )

    return execute


@pytest.mark.asyncio
async def test_provider_router_stops_after_primary_success() -> None:
    primary = StubAdapter("primary-provider", _success("primary-provider"))
    fallback = StubAdapter("fallback-provider", _success("fallback-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={
            primary.provider_id: primary,
            fallback.provider_id: fallback,
        },
        clock=lambda: NOW,
    )

    result = await router.execute(_query())

    assert primary.calls == 1
    assert fallback.calls == 0
    assert result.provider == "primary-provider"
    assert result.provider_attempts is not None
    assert [item.status for item in result.provider_attempts] == ["succeeded"]
    assert result.provider_attempts[0].route_role == "primary"


@pytest.mark.asyncio
async def test_provider_router_falls_back_only_after_retryable_failure() -> None:
    def retryable_failure(_query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        raise ResearchCapabilityError(
            "provider_rate_limited",
            "primary returned 429",
            retryable=True,
            origin="provider",
            cause_code="http_429",
        )

    primary = StubAdapter("primary-provider", retryable_failure)
    fallback = StubAdapter("fallback-provider", _success("fallback-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={
            primary.provider_id: primary,
            fallback.provider_id: fallback,
        },
        clock=lambda: NOW,
    )

    result = await router.execute(_query())

    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.provider == "fallback-provider"
    assert result.provider_attempts is not None
    assert [item.status for item in result.provider_attempts] == [
        "failed",
        "succeeded",
    ]
    assert result.provider_attempts[0].error_code == "provider_rate_limited"
    assert result.provider_attempts[0].retryable is True
    assert result.provider_attempts[1].route_role == "fallback"


@pytest.mark.asyncio
async def test_provider_router_does_not_hide_nonretryable_contract_failure() -> None:
    def contract_failure(_query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "required field is missing",
            retryable=False,
            origin="provider",
            cause_code="schema",
        )

    primary = StubAdapter("primary-provider", contract_failure)
    fallback = StubAdapter("fallback-provider", _success("fallback-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={
            primary.provider_id: primary,
            fallback.provider_id: fallback,
        },
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(_query())

    assert fallback.calls == 0
    assert raised.value.error_code == "research_market_output_invalid"
    assert [item.status for item in raised.value.provider_attempts] == ["failed"]
    assert raised.value.provider_attempts[0].retryable is False


@pytest.mark.asyncio
async def test_provider_router_filters_routes_by_requested_domain() -> None:
    primary = StubAdapter("primary-provider", _success("primary-provider"))
    fallback = StubAdapter("fallback-provider", _success("fallback-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={primary.provider_id: primary, fallback.provider_id: fallback},
        clock=lambda: NOW,
    )

    result = await router.execute(
        _query().model_copy(update={"allowed_domains": ["fallback-provider.example"]})
    )

    assert result.provider == "fallback-provider"
    assert primary.calls == 0
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_provider_router_rejects_unapproved_requested_domain() -> None:
    primary = StubAdapter("primary-provider", _success("primary-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(_route("primary-provider", role="primary", priority=10),),
        adapters={primary.provider_id: primary},
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(
            _query().model_copy(update={"allowed_domains": ["unapproved.example"]})
        )

    assert raised.value.error_code == "provider_route_unavailable"
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_provider_router_fails_closed_when_no_route_supports_requested_fields() -> None:
    primary = StubAdapter(
        "primary-provider",
        _success("primary-provider"),
        supported_fields=frozenset({"price"}),
    )
    fallback = StubAdapter(
        "fallback-provider",
        _success("fallback-provider"),
        supported_fields=frozenset({"volume"}),
    )
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={primary.provider_id: primary, fallback.provider_id: fallback},
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(_query())

    assert raised.value.error_code == "provider_route_unavailable"
    assert primary.calls == 0


@pytest.mark.asyncio
async def test_event_window_query_uses_archive_route_only() -> None:
    archive = StubAdapter("event-window-archive", _success("event-window-archive"))
    current = StubAdapter("primary-provider", _success("primary-provider"))
    fallback = StubAdapter("fallback-provider", _success("fallback-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route(
                "event-window-archive",
                role="primary",
                priority=0,
                requires_event_window=True,
                domains=[],
            ),
            _route("primary-provider", role="primary", priority=10),
            _route("fallback-provider", role="fallback", priority=20),
        ),
        adapters={
            archive.provider_id: archive,
            current.provider_id: current,
            fallback.provider_id: fallback,
        },
        clock=lambda: NOW,
    )

    result = await router.execute(_event_query())

    assert result.provider == "event-window-archive"
    assert archive.calls == 1
    assert current.calls == fallback.calls == 0


@pytest.mark.asyncio
async def test_event_window_archive_failure_cannot_fallback_to_current_snapshot() -> None:
    def archive_failure(_query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        raise ResearchCapabilityError(
            "provider_window_archive_missing",
            "archive slot is unavailable",
            retryable=False,
            origin="provider",
        )

    archive = StubAdapter("event-window-archive", archive_failure)
    current = StubAdapter("primary-provider", _success("primary-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route(
                "event-window-archive",
                role="primary",
                priority=0,
                requires_event_window=True,
                domains=[],
            ),
            _route("primary-provider", role="primary", priority=10),
        ),
        adapters={archive.provider_id: archive, current.provider_id: current},
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(_event_query())

    assert raised.value.error_code == "provider_window_archive_missing"
    assert archive.calls == 1
    assert current.calls == 0


@pytest.mark.asyncio
async def test_current_snapshot_query_never_calls_archive_route() -> None:
    archive = StubAdapter("event-window-archive", _success("event-window-archive"))
    current = StubAdapter("primary-provider", _success("primary-provider"))
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(
            _route(
                "event-window-archive",
                role="primary",
                priority=0,
                requires_event_window=True,
                domains=[],
            ),
            _route("primary-provider", role="primary", priority=10),
        ),
        adapters={archive.provider_id: archive, current.provider_id: current},
        clock=lambda: NOW,
    )

    result = await router.execute(_query())

    assert result.provider == "primary-provider"
    assert archive.calls == 0
    assert current.calls == 1


@pytest.mark.asyncio
async def test_provider_router_preserves_missing_adapter_provenance() -> None:
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(_route("missing-provider", role="primary", priority=10),),
        adapters={},
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(_query())

    assert raised.value.error_code == "provider_adapter_missing"
    assert raised.value.provider_attempts == ()


@pytest.mark.asyncio
async def test_provider_router_fails_closed_on_adapter_capability_mismatch() -> None:
    adapter = StubAdapter("primary-provider", _success("primary-provider"))
    adapter.capability_id = "market.other"
    router = ProviderCapabilityRouter(
        capability_id="market.crypto_spot",
        routes=(_route("primary-provider", role="primary", priority=10),),
        adapters={adapter.provider_id: adapter},
        clock=lambda: NOW,
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await router.execute(_query())

    assert raised.value.error_code == "provider_adapter_capability_mismatch"
