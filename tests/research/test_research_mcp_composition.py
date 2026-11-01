# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from apps.research_mcp.main import (
    _configured_search_transport,
    _durable_progress_enabled,
    compose_capability_adapters,
    load_capability_manifests,
)
from packages.contracts_py.decision_hub_contracts import (
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
)
from packages.kernel.decision_hub_kernel.ports.research import ResearchCapabilityAdapter
from packages.provider_adapters.official_sources.documents import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import FetchedDocument, ReplayResearchArchive
from packages.provider_adapters.routing import ProviderCapabilityRouter

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_stable_web_search_selects_configured_openai_responses_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    monkeypatch.setenv("DECISION_HUB_SEARCH_PROVIDER", "openai_responses")
    monkeypatch.setattr(
        "apps.research_mcp.main.OpenAIResponsesWebSearchTransport.from_env",
        lambda: sentinel,
    )

    assert _configured_search_transport() is sentinel


def test_stable_web_search_fails_closed_for_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_SEARCH_PROVIDER", "unknown")

    with pytest.raises(RuntimeError, match="research_search_provider_invalid:unknown"):
        _configured_search_transport()


def test_replay_mcp_can_explicitly_join_durable_evaluation_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", raising=False)
    assert not _durable_progress_enabled({"replay.research"})

    monkeypatch.setenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "1")
    assert _durable_progress_enabled({"replay.research"})

    monkeypatch.setenv("DECISION_HUB_RESEARCH_DURABLE_PROGRESS", "0")
    assert not _durable_progress_enabled({"replay.research"})


class _FakeProvider:
    capability_id = "market.crypto_derivatives"
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


def _result(provider_id: str) -> Callable[[ResearchCapabilityQuery], ResearchCapabilityResult]:
    def make(query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=provider_id,
            evidence_candidates=[],
            facts=[],
            cost_usd=0.0,
            completed_at=NOW,
        )

    return make


def _query(
    *,
    fields: list[str] | None = None,
    allowed_domains: list[str] | None = None,
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "composition-request-1",
            "capability_id": "market.crypto_derivatives",
            "requirement_id": "derivatives_crowding",
            "query": "BTC perpetual market snapshot",
            "target_url": None,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": fields or ["funding_rate"],
            "allowed_domains": allowed_domains or [],
            "max_results": 10,
            "max_cost_usd": 0.1,
            "research_session_id": "composition-session-1",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=1),
        }
    )


def _router(
    *,
    okx: ResearchCapabilityAdapter | None = None,
    binance: ResearchCapabilityAdapter | None = None,
) -> ProviderCapabilityRouter:
    registry: dict[str, ResearchCapabilityAdapter] = {}
    if okx is not None:
        registry["adapter://provider/market/okx-public"] = okx
    if binance is not None:
        registry["adapter://provider/market/binance-public"] = binance
    adapters = compose_capability_adapters(
        load_capability_manifests(),
        ReplayResearchArchive({}, {}),
        provider_registry=registry,
    )
    selected = [item for item in adapters if item.capability_id == "market.crypto_derivatives"]
    assert len(selected) == 1
    assert isinstance(selected[0], ProviderCapabilityRouter)
    return selected[0]


def test_pack_composition_registers_one_stable_router_with_approved_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECISION_HUB_CRYPTO_DERIVATIVES_PROVIDER", "binance")
    router = _router(
        okx=_FakeProvider("okx-public", _result("okx-public")),
        binance=_FakeProvider("binance-public", _result("binance-public")),
    )

    assert router.capability_id == "market.crypto_derivatives"
    assert [route.provider_id for route in router.routes] == [
        "gateio-public",
        "huobi-public",
        "okx-public",
        "binance-public",
        "gateio-derivatives-public",
        "htx-derivatives-public",
    ]
    assert [route.route_role for route in router.routes] == [
        "fallback",
        "fallback",
        "primary",
        "fallback",
        "fallback",
        "fallback",
    ]
    assert router.minimum_successes_by_requirement == {
        "crypto_spot_confirmation": 2,
        "derivatives_crowding": 2,
    }


def test_pack_composition_routes_daily_cross_asset_with_audited_fallback() -> None:
    adapters = compose_capability_adapters(
        load_capability_manifests(), ReplayResearchArchive({}, {})
    )
    selected = [item for item in adapters if item.capability_id == "market.cross_asset"]

    assert len(selected) == 1
    assert isinstance(selected[0], ProviderCapabilityRouter)
    assert [route.provider_id for route in selected[0].routes] == [
        "fred-public",
        "yahoo-finance-public",
    ]


@pytest.mark.asyncio
async def test_composed_router_collects_two_venues_in_one_call() -> None:
    okx = _FakeProvider("okx-public", _result("okx-public"))
    binance = _FakeProvider("binance-public", _result("binance-public"))
    router = _router(okx=okx, binance=binance)

    result = await router.execute(_query(allowed_domains=["okx.com", "api.binance.com"]))

    assert result.provider == "okx-public+binance-public"
    assert okx.calls == 1
    assert binance.calls == 1


@pytest.mark.asyncio
async def test_gateway_composition_falls_back_after_retryable_primary_failure() -> None:
    def retryable_failure(_query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        raise ResearchCapabilityError(
            "provider_rate_limited",
            "OKX returned 429",
            retryable=True,
            origin="provider",
            cause_code="http_429",
        )

    okx = _FakeProvider("okx-public", retryable_failure)
    binance = _FakeProvider("binance-public", _result("binance-public"))
    router = _router(okx=okx, binance=binance)
    manifest = next(
        item
        for item in load_capability_manifests()
        if item.capability_id == "market.crypto_derivatives"
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest],
        [router],
        enabled_capabilities=[manifest.capability_id],
        clock=lambda: NOW,
    )

    result = await gateway.execute(_query(allowed_domains=["okx.com", "api.binance.com"]))

    assert result.provider == "binance-public"
    assert okx.calls == 2
    assert binance.calls == 1
    assert result.provider_attempts is not None
    assert [attempt.status for attempt in result.provider_attempts] == [
        "failed",
        "succeeded",
        "failed",
    ]
    assert result.provider_attempts[0].error_code == "provider_rate_limited"


@pytest.mark.asyncio
async def test_composed_router_uses_binance_for_spot_fields_okx_does_not_advertise() -> None:
    okx = _FakeProvider(
        "okx-public", _result("okx-public"), supported_fields=frozenset({"funding_rate"})
    )
    binance = _FakeProvider(
        "binance-public",
        _result("binance-public"),
        supported_fields=frozenset({"spot_price", "spot_volume", "funding_rate"}),
    )
    router = _router(okx=okx, binance=binance)

    result = await router.execute(
        _query(
            fields=["spot_price", "spot_volume"],
            allowed_domains=["okx.com", "api.binance.com"],
        )
    )

    assert result.provider == "binance-public"
    assert okx.calls == 0
    assert binance.calls == 1


@pytest.mark.asyncio
async def test_composed_router_respects_route_domain_and_gateway_boundary() -> None:
    okx = _FakeProvider("okx-public", _result("okx-public"))
    binance = _FakeProvider("binance-public", _result("binance-public"))
    router = _router(okx=okx, binance=binance)
    manifest = next(
        item
        for item in load_capability_manifests()
        if item.capability_id == "market.crypto_derivatives"
    )
    gateway = ResearchCapabilityGatewayService(
        [manifest],
        [router],
        enabled_capabilities=[manifest.capability_id],
        clock=lambda: NOW,
    )

    result = await gateway.execute(_query(allowed_domains=["api.binance.com"]))

    assert result.provider == "binance-public"
    assert okx.calls == 0
    assert binance.calls == 1

    with pytest.raises(ResearchCapabilityError) as raised:
        await gateway.execute(_query(allowed_domains=["example.com"]))
    assert raised.value.error_code == "research_domain_denied"


def test_composition_fails_closed_for_unknown_route_ref() -> None:
    manifests = load_capability_manifests()
    target = next(item for item in manifests if item.capability_id == "market.crypto_derivatives")
    assert target.provider_routes is not None
    broken = target.model_copy(
        update={
            "provider_routes": [
                target.provider_routes[0].model_copy(
                    update={"adapter_ref": "adapter://provider/does-not-exist"}
                )
            ]
        }
    )
    broken_manifests = [broken if item is target else item for item in manifests]

    with pytest.raises(RuntimeError, match="research_provider_adapter_ref_unregistered"):
        compose_capability_adapters(broken_manifests, ReplayResearchArchive({}, {}))


def test_composition_fails_closed_for_adapter_capability_mismatch() -> None:
    mismatched = _FakeProvider("okx-public", _result("okx-public"))
    mismatched.capability_id = "market.cross_asset"

    with pytest.raises(RuntimeError, match="research_provider_adapter_capability_mismatch"):
        compose_capability_adapters(
            load_capability_manifests(),
            ReplayResearchArchive({}, {}),
            provider_registry={"adapter://provider/market/okx-public": mismatched},
        )


def test_composition_fails_closed_for_duplicate_provider_id() -> None:
    manifests = load_capability_manifests()
    target = next(item for item in manifests if item.capability_id == "market.crypto_derivatives")
    assert target.provider_routes is not None
    non_event_routes = [
        route for route in target.provider_routes if not route.requires_event_window
    ]
    first, second = non_event_routes[0], non_event_routes[1]
    broken = target.model_copy(
        update={
            "provider_routes": [
                first,
                second.model_copy(update={"provider_id": first.provider_id}),
            ]
        }
    )
    broken_manifests = [broken if item is target else item for item in manifests]

    with pytest.raises(RuntimeError, match="research_provider_provider_id_duplicate"):
        compose_capability_adapters(broken_manifests, ReplayResearchArchive({}, {}))


@pytest.mark.asyncio
async def test_real_composition_accepts_canonical_official_event_requirement() -> None:
    adapters = compose_capability_adapters(
        load_capability_manifests(), ReplayResearchArchive({}, {})
    )
    official = next(item for item in adapters if item.capability_id == "official.macro")

    async def fetcher(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text=(
                '<?xml version="1.0"?><rss><channel><item>'
                "<title>Waller, Economic Outlook</title>"
                "<link>https://www.federalreserve.gov/newsevents/speech/"
                "waller20260903a.htm</link>"
                "<pubDate>Thu, 3 Sep 2026 12:30:00 GMT</pubDate>"
                "</item></channel></rss>"
            ),
            observed_at=NOW,
            received_at=NOW,
        )

    cast(OfficialDocumentResearchAdapter, official).fetcher = fetcher
    query = ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "official-composition-request-1",
            "capability_id": "official.macro",
            "requirement_id": "event_identity",
            "query": "Waller speech on 2026-09-03",
            "target_url": "https://www.federalreserve.gov/feeds/speeches.xml",
            "symbols": [],
            "fields": [],
            "allowed_domains": ["federalreserve.gov"],
            "max_results": 10,
            "max_cost_usd": 0.0,
            "research_session_id": "official-composition-session-1",
            "round": 1,
            "mode": "live",
            "observed_at": NOW,
            "cutoff_at": NOW + timedelta(minutes=1),
            "event_id": "event-waller-20260903",
        }
    )

    result = await official.execute(query)

    assert result.provider == "fed.monetary_policy"
    assert {item.field for item in result.facts or []} == {
        "event_actor",
        "event_time",
        "revision_status",
    }
