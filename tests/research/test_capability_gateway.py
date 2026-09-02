from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    SearchEvidence,
    SearchQuery,
    SearchResult,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    ResearchCapabilityGatewayService,
    research_evidence_content_hash,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)
from packages.kernel.decision_hub_kernel.ports.search import SearchCapabilityError
from packages.provider_adapters.macro_market import FredSeriesResearchAdapter
from packages.provider_adapters.market import (
    CoinExMarketResearchAdapter,
    OKXDerivativesResearchAdapter,
)
from packages.provider_adapters.official_sources import OfficialDocumentResearchAdapter
from packages.provider_adapters.research import (
    FetchedDocument,
    ReplayResearchCapabilityAdapter,
    WebSearchResearchAdapter,
)

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=UTC)
CUTOFF = NOW + timedelta(minutes=5)


def _manifest(
    capability_id: str = "official.macro",
    *,
    kind: str = "python_adapter",
    domains: list[str] | None = None,
    audit_status: str = "approved",
    license_status: str = "approved",
    permissions: list[str] | None = None,
) -> ResearchCapabilityManifest:
    return ResearchCapabilityManifest.model_validate(
        {
            "schema_version": "research-capability-manifest.v1",
            "capability_id": capability_id,
            "version": "1.0.0",
            "kind": kind,
            "implementation_ref": f"adapter://{capability_id}",
            "input_schema_ref": "research-capability-query.v1",
            "output_schema_ref": "research-capability-result.v1",
            "permissions": permissions or ["network:https"],
            "allowed_domains": domains or ["federalreserve.gov"],
            "timeout_seconds": 1,
            "cost_policy_ref": "test.v1",
            "freshness_policy_ref": "test.v1",
            "license_status": license_status,
            "audit_status": audit_status,
            "replay_policy": "archive_required" if kind != "replay" else "deterministic",
            "secret_policy": "none",
        }
    )


def _query(
    capability_id: str = "official.macro",
    *,
    mode: str = "live",
    domains: list[str] | None = None,
    target_url: str | None = "https://www.federalreserve.gov/newsevents/speech/test.htm",
    symbols: list[str] | None = None,
    fields: list[str] | None = None,
    query_text: str = "latest policy speech",
    cutoff_at: datetime = CUTOFF,
    research_session_id: str = "research-session-1",
) -> ResearchCapabilityQuery:
    return ResearchCapabilityQuery.model_validate(
        {
            "schema_version": "research-capability-query.v1",
            "request_id": "capability-request-1",
            "capability_id": capability_id,
            "requirement_id": "event_identity",
            "query": query_text,
            "target_url": target_url,
            "symbols": symbols or [],
            "fields": fields or [],
            "allowed_domains": domains or ["federalreserve.gov"],
            "max_results": 10,
            "max_cost_usd": 0.05,
            "research_session_id": research_session_id,
            "round": 1,
            "mode": mode,
            "observed_at": NOW,
            "cutoff_at": cutoff_at,
        }
    )


def _candidate(
    *,
    source_url: str = "https://www.federalreserve.gov/newsevents/speech/test.htm",
    received_at: datetime = NOW + timedelta(seconds=2),
) -> EvidenceCandidate:
    excerpt = "The Committee remains attentive to inflation risks."
    content_hash = research_evidence_content_hash(
        requirement_id="event_identity",
        kind="official",
        authority="official",
        source_id="federal-reserve",
        source_url=source_url,
        published_at=NOW - timedelta(hours=1),
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    return EvidenceCandidate(
        evidence_id=f"ev_{content_hash[:32]}",
        requirement_id="event_identity",
        kind="official",
        authority="official",
        source_id="federal-reserve",
        source_url=AnyUrl(source_url),
        published_at=NOW - timedelta(hours=1),
        observed_at=NOW + timedelta(seconds=1),
        received_at=received_at,
        content_hash=content_hash,
        excerpt=excerpt,
        structured_payload_ref=None,
        tool_call_id="capability-request-1",
        research_session_id="research-session-1",
        round=1,
        quality="candidate",
        freshness_status="unknown",
        conflict_group=None,
    )


class _Adapter:
    capability_id = "official.macro"
    supported_modes = frozenset({"live"})

    def __init__(
        self,
        result: ResearchCapabilityResult | None = None,
        *,
        delay: float = 0,
    ) -> None:
        self.result = result or ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id="capability-request-1",
            capability_id=self.capability_id,
            provider="official-fixture",
            evidence_candidates=[_candidate()],
            cost_usd=0.0,
            completed_at=NOW + timedelta(seconds=3),
        )
        self.delay = delay
        self.calls = 0

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        self.last_query = query
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _gateway(
    *,
    manifest: ResearchCapabilityManifest | None = None,
    adapter: _Adapter | None = None,
    enabled: bool = True,
) -> ResearchCapabilityGatewayService:
    selected_manifest = manifest or _manifest()
    selected_adapter = adapter or _Adapter()
    return ResearchCapabilityGatewayService(
        [selected_manifest],
        [selected_adapter],
        enabled_capabilities=[selected_manifest.capability_id] if enabled else [],
        clock=lambda: NOW + timedelta(seconds=4),
    )


@pytest.mark.asyncio
async def test_gateway_allows_only_approved_enabled_canonical_capability() -> None:
    result = await _gateway().execute(_query())

    assert result.provider == "official-fixture"
    assert result.evidence_candidates[0].authority == "official"


@pytest.mark.asyncio
async def test_gateway_owns_live_observation_time_and_keeps_model_time_diagnostic() -> None:
    adapter = _Adapter()
    server_time = NOW + timedelta(seconds=4)
    gateway = ResearchCapabilityGatewayService(
        [_manifest()],
        [adapter],
        enabled_capabilities=["official.macro"],
        clock=lambda: server_time,
    )
    query = _query().model_copy(
        update={"observed_at": CUTOFF + timedelta(days=365)}
    )

    result = await gateway.execute(query)

    assert adapter.last_query.observed_at == server_time
    assert adapter.last_query.requested_observed_at == CUTOFF + timedelta(days=365)
    assert result.completed_at == server_time
    assert result.evidence_candidates[0].observed_at == server_time
    assert result.evidence_candidates[0].received_at == server_time


@pytest.mark.asyncio
async def test_gateway_reports_server_owned_pit_failure_with_provenance() -> None:
    gateway = ResearchCapabilityGatewayService(
        [_manifest()],
        [_Adapter()],
        enabled_capabilities=["official.macro"],
        clock=lambda: CUTOFF + timedelta(seconds=1),
    )

    with pytest.raises(ResearchCapabilityError) as raised:
        await gateway.execute(_query())

    error = raised.value
    assert error.error_code == "research_pit_violation"
    assert error.origin == "pit"
    assert error.capability_id == "official.macro"
    assert error.tool_call_id == "capability-request-1"
    assert error.retryable is False
    assert error.provenance().model_dump(mode="json") == {
        "error_code": "research_pit_violation",
        "origin": "pit",
        "cause_code": None,
        "capability_id": "official.macro",
        "tool_call_id": "capability-request-1",
        "retryable": False,
        "deadline_ms": None,
    }


class _SearchFailingAdapter(_Adapter):
    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        del query
        raise SearchCapabilityError(
            "search_provider_failed", "upstream returned 503", retryable=True
        )


class _CancelledAdapter(_Adapter):
    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        del query
        raise asyncio.CancelledError()


class _BlockingAdapter(_Adapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        del query
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("blocking adapter unexpectedly completed")


@pytest.mark.asyncio
async def test_gateway_preserves_provider_error_code_instead_of_mapping_everything_to_timeout(
) -> None:
    with pytest.raises(ResearchCapabilityError) as raised:
        await ResearchCapabilityGatewayService(
            [_manifest()],
            [_SearchFailingAdapter()],
            enabled_capabilities=["official.macro"],
            clock=lambda: NOW + timedelta(seconds=4),
        ).execute(_query())

    error = raised.value
    assert error.error_code == "search_provider_failed"
    assert error.origin == "provider"
    assert error.retryable is True
    assert error.cause_code == "searchcapabilityerror"


@pytest.mark.asyncio
async def test_gateway_preserves_cancellation_instead_of_mapping_shutdown_to_provider_failure(
) -> None:
    with pytest.raises(asyncio.CancelledError):
        await ResearchCapabilityGatewayService(
            [_manifest()],
            [_CancelledAdapter()],
            enabled_capabilities=["official.macro"],
            clock=lambda: NOW + timedelta(seconds=4),
        ).execute(_query())


@pytest.mark.asyncio
async def test_gateway_preserves_owner_cancellation_while_adapter_is_running() -> None:
    adapter = _BlockingAdapter()
    task = asyncio.create_task(
        ResearchCapabilityGatewayService(
            [_manifest()],
            [adapter],
            enabled_capabilities=["official.macro"],
            clock=lambda: NOW + timedelta(seconds=4),
        ).execute(_query())
    )
    await adapter.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.cancelled is True


@pytest.mark.parametrize(
    ("manifest", "enabled", "expected"),
    [
        (_manifest(audit_status="candidate"), True, "research_capability_not_audited"),
        (_manifest(license_status="review_required"), True, "research_capability_not_audited"),
        (_manifest(), False, "research_capability_not_enabled"),
    ],
)
@pytest.mark.asyncio
async def test_gateway_is_deny_by_default(
    manifest: ResearchCapabilityManifest,
    enabled: bool,
    expected: str,
) -> None:
    adapter = _Adapter()
    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway(manifest=manifest, adapter=adapter, enabled=enabled).execute(_query())

    assert raised.value.error_code == expected
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (_query(domains=["example.com"]), "research_domain_denied"),
        (
            _query(target_url="https://example.com/not-allowed"),
            "research_target_domain_denied",
        ),
        (_query(mode="replay"), "research_capability_mode_denied"),
        (
            _query(cutoff_at=NOW - timedelta(seconds=1)),
            "research_pit_violation",
        ),
    ],
)
@pytest.mark.asyncio
async def test_gateway_denies_domain_mode_and_future_query(
    query: ResearchCapabilityQuery, expected: str
) -> None:
    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway().execute(query)
    assert raised.value.error_code == expected


@pytest.mark.asyncio
async def test_gateway_rejects_future_or_tampered_evidence() -> None:
    future = _candidate(received_at=CUTOFF + timedelta(seconds=1))
    future_result = _Adapter().result.model_copy(update={"evidence_candidates": [future]})
    with pytest.raises(ResearchCapabilityError) as future_error:
        await _gateway(adapter=_Adapter(future_result)).execute(_query())
    assert future_error.value.error_code == "research_pit_violation"

    tampered = _candidate().model_copy(update={"content_hash": "0" * 64})
    tampered_result = _Adapter().result.model_copy(update={"evidence_candidates": [tampered]})
    with pytest.raises(ResearchCapabilityError) as hash_error:
        await _gateway(adapter=_Adapter(tampered_result)).execute(_query())
    assert hash_error.value.error_code == "research_content_hash_mismatch"


@pytest.mark.asyncio
async def test_gateway_enforces_timeout() -> None:
    manifest = _manifest().model_copy(update={"timeout_seconds": 0.001})
    with pytest.raises(ResearchCapabilityError) as raised:
        await _gateway(manifest=manifest, adapter=_Adapter(delay=0.05)).execute(_query())
    assert raised.value.error_code == "research_capability_timeout"
    assert raised.value.retryable is True


class _SearchPort:
    def __init__(self, source_urls: list[str] | None = None) -> None:
        self.source_urls = source_urls or [
            "https://www.federalreserve.gov/newsevents/speech/example.htm"
        ]

    async def search(self, query: SearchQuery) -> SearchResult:
        title = "Policy speech"
        snippet = "Inflation risks remain elevated."
        from packages.kernel.decision_hub_kernel.application.search import (
            search_evidence_content_hash,
        )

        evidence = []
        for index, raw_url in enumerate(self.source_urls, start=1):
            source_url = AnyUrl(raw_url)
            evidence.append(
                SearchEvidence(
                    evidence_id=f"search-{index}",
                    title=title,
                    snippet=f"{snippet} Source {index}.",
                    source_url=source_url,
                    observed_at=NOW + timedelta(seconds=1),
                    published_at=NOW - timedelta(hours=1),
                    received_at=NOW + timedelta(seconds=2),
                    content_hash=search_evidence_content_hash(
                        title=title,
                        snippet=f"{snippet} Source {index}.",
                        source_url=str(source_url),
                        published_at=NOW - timedelta(hours=1),
                    ),
                )
            )
        return SearchResult(
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="search-fixture",
            evidence=evidence,
            cost_usd=0.01,
            completed_at=NOW + timedelta(seconds=3),
        )


@pytest.mark.asyncio
async def test_web_search_adapter_reuses_search_port_and_marks_summary_search_derived() -> None:
    adapter = WebSearchResearchAdapter(_SearchPort())
    query = _query(
        capability_id="web.search",
        target_url=None,
        domains=["federalreserve.gov"],
    )

    result = await adapter.execute(query)

    assert result.evidence_candidates[0].authority == "search_derived"
    assert result.evidence_candidates[0].kind == "web"


@pytest.mark.asyncio
async def test_web_search_adapter_uses_conservative_publisher_identity_for_sufficiency() -> None:
    adapter = WebSearchResearchAdapter(
        _SearchPort(
            [
                "https://www.federalreserve.gov/newsevents/speech/first.htm",
                "https://federalreserve.gov/newsevents/speech/second.htm",
            ]
        )
    )
    query = _query(
        capability_id="web.search",
        target_url=None,
        domains=["federalreserve.gov"],
    )

    result = await adapter.execute(query)
    accepted = [
        item.model_copy(update={"quality": "accepted", "freshness_status": "fresh"})
        for item in result.evidence_candidates
    ]
    requirement = EvidenceRequirement(
        requirement_id="event_identity",
        description="Confirm the event from independent sources.",
        importance="hard",
        source_priority=["search_derived"],
        authority_floor="search_derived",
        preferred_capabilities=["web.search"],
        freshness_seconds=3600,
        minimum_independent_sources=2,
        allowed_fallbacks=[],
        confidence_cap=0.5,
    )
    coverage = assess_evidence_sufficiency(
        [requirement],
        accepted,
        cutoff_at=CUTOFF,
    )

    assert {item.source_id for item in result.evidence_candidates} == {
        "federalreserve.gov"
    }
    assert all(item.authority == "search_derived" for item in accepted)
    assert coverage.status == "insufficient"
    assert coverage.gaps[0].reason_code == "insufficient_sources"


@pytest.mark.asyncio
async def test_official_document_adapter_emits_authoritative_candidate() -> None:
    async def fetch(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="Official speech text",
            published_at=NOW - timedelta(hours=1),
            observed_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(seconds=2),
        )

    result = await OfficialDocumentResearchAdapter(fetcher=fetch).execute(_query())

    assert result.evidence_candidates[0].authority == "official"
    assert result.evidence_candidates[0].kind == "official"


@pytest.mark.asyncio
async def test_evidence_identity_is_idempotent_within_session_and_distinct_across_runs() -> None:
    async def fetch(url: str) -> FetchedDocument:
        return FetchedDocument(
            source_url=url,
            text="Official speech text",
            published_at=NOW - timedelta(hours=1),
            observed_at=NOW + timedelta(seconds=1),
            received_at=NOW + timedelta(seconds=2),
        )

    adapter = OfficialDocumentResearchAdapter(fetcher=fetch)
    first = await adapter.execute(_query(research_session_id="session-a"))
    retried = await adapter.execute(_query(research_session_id="session-a"))
    independent = await adapter.execute(_query(research_session_id="session-b"))

    first_evidence = first.evidence_candidates[0]
    assert retried.evidence_candidates[0].evidence_id == first_evidence.evidence_id
    assert independent.evidence_candidates[0].evidence_id != first_evidence.evidence_id
    assert independent.evidence_candidates[0].content_hash == first_evidence.content_hash


@pytest.mark.asyncio
async def test_okx_adapter_produces_typed_derivatives_candidates() -> None:
    async def fetch(url: str) -> Mapping[str, object]:
        field = (
            "fundingRate"
            if "funding-rate" in url
            else "oi"
            if "open-interest" in url
            else "markPx"
            if "mark-price" in url
            else "last"
        )
        return {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", field: "1.25", "ts": "1787976000000"}],
        }

    query = _query(
        capability_id="market.crypto_derivatives",
        domains=["okx.com"],
        target_url=None,
        symbols=["BTC-USDT-SWAP"],
        fields=["ticker", "funding_rate", "open_interest", "mark_price"],
    )

    result = await OKXDerivativesResearchAdapter(fetcher=fetch).execute(query)

    assert len(result.evidence_candidates) == 4
    assert {item.authority for item in result.evidence_candidates} == {"exchange"}
    assert all(item.source_id == "okx-public" for item in result.evidence_candidates)


@pytest.mark.asyncio
async def test_coinex_adapter_produces_one_typed_spot_snapshot() -> None:
    async def fetch(url: str) -> Mapping[str, object]:
        assert "/v2/spot/ticker" in url
        return {
            "code": 0,
            "data": [
                {
                    "market": "BTCUSDT",
                    "last": "79043",
                    "volume": "430.64519066",
                }
            ],
            "message": "OK",
        }

    query = _query(
        capability_id="market.crypto_derivatives",
        domains=["api.coinex.com"],
        target_url=None,
        symbols=["BTC"],
        fields=["spot_price", "spot_volume"],
    ).model_copy(update={"requirement_id": "crypto.spot"})

    result = await CoinExMarketResearchAdapter(fetcher=fetch).execute(query)

    assert result.provider == "coinex-public"
    assert len(result.evidence_candidates) == 1
    candidate = result.evidence_candidates[0]
    assert candidate.authority == "exchange"
    assert candidate.source_id == "coinex-public"
    assert '"spot_price":"79043"' in candidate.excerpt
    assert '"spot_volume":"430.64519066"' in candidate.excerpt


@pytest.mark.asyncio
async def test_coinex_adapter_produces_funding_oi_mark_and_basis_snapshot() -> None:
    async def fetch(url: str) -> Mapping[str, object]:
        if "funding-rate" in url:
            return {
                "code": 0,
                "data": [{"market": "BTCUSDT", "latest_funding_rate": "0.0001"}],
                "message": "OK",
            }
        return {
            "code": 0,
            "data": [
                {
                    "market": "BTCUSDT",
                    "mark_price": "79062",
                    "index_price": "79058.77",
                    "open_interest_volume": "924.564",
                }
            ],
            "message": "OK",
        }

    query = _query(
        capability_id="market.crypto_derivatives",
        domains=["api.coinex.com"],
        target_url=None,
        symbols=["BTCUSDT"],
        fields=["funding_rate", "open_interest", "mark_price", "index_price", "basis"],
    ).model_copy(update={"requirement_id": "crypto.derivatives"})

    result = await CoinExMarketResearchAdapter(fetcher=fetch).execute(query)

    values = json.loads(result.evidence_candidates[0].excerpt)["values"]
    assert values["funding_rate"] == "0.0001"
    assert values["open_interest"] == "924.564"
    assert float(values["basis"]) == pytest.approx((79062 - 79058.77) / 79058.77)


@pytest.mark.asyncio
async def test_coinex_adapter_rejects_unknown_symbol_and_missing_fields() -> None:
    adapter = CoinExMarketResearchAdapter(fetcher=lambda _url: _never_called())
    unknown = _query(
        capability_id="market.crypto_derivatives",
        domains=["api.coinex.com"],
        target_url=None,
        symbols=["ETHUSDT"],
        fields=["spot_price"],
    )
    with pytest.raises(ResearchCapabilityError, match="audited BTC/USDT"):
        await adapter.execute(unknown)

    async def missing(_url: str) -> Mapping[str, object]:
        return {"code": 0, "data": [{"market": "BTCUSDT"}], "message": "OK"}

    missing_field = unknown.model_copy(update={"symbols": ["BTCUSDT"]})
    with pytest.raises(ResearchCapabilityError) as raised:
        await CoinExMarketResearchAdapter(fetcher=missing).execute(missing_field)
    assert raised.value.error_code == "research_market_output_invalid"


async def _never_called() -> Mapping[str, object]:
    raise AssertionError("fetcher must not be called")


@pytest.mark.asyncio
async def test_fred_adapter_selects_latest_pit_eligible_row() -> None:
    async def fetch(_url: str) -> str:
        return "DATE,DGS2\n2026-08-27,4.12\n2026-08-28,4.20\n2026-08-30,9.99\n"

    query = _query(
        capability_id="market.cross_asset",
        domains=["fred.stlouisfed.org"],
        target_url=None,
        symbols=["DGS2"],
    )
    result = await FredSeriesResearchAdapter(fetcher=fetch).execute(query)

    candidate = result.evidence_candidates[0]
    assert '"value":"4.20"' in candidate.excerpt
    assert "9.99" not in candidate.excerpt
    assert candidate.published_at == datetime(2026, 8, 28, tzinfo=UTC)


@pytest.mark.asyncio
async def test_replay_adapter_never_falls_back_to_live_network() -> None:
    query = _query(mode="replay", query_text="archived speech")
    fixture = _candidate()
    adapter = ReplayResearchCapabilityAdapter(
        capability_id="official.macro",
        fixtures={"archived speech": [fixture]},
    )

    result = await adapter.execute(query)
    assert result.provider == "archived-replay"

    with pytest.raises(ResearchCapabilityError) as raised:
        await adapter.execute(_query(mode="replay", query_text="not archived"))
    assert raised.value.error_code == "research_replay_fixture_missing"
