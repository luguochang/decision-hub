from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


async def _fetch_json(url: str) -> Mapping[str, object]:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise ResearchCapabilityError(
            "provider_timeout",
            "order-book provider request timed out",
            retryable=True,
            origin="transport",
            cause_code="timeout",
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise ResearchCapabilityError(
            "provider_rate_limited" if status == 429 else "provider_upstream_unavailable"
            if status >= 500
            else "provider_request_rejected",
            f"order-book provider returned HTTP {status}",
            retryable=status == 429 or status >= 500,
            origin="provider",
            cause_code=f"http_{status}",
        ) from exc
    except httpx.TransportError as exc:
        raise ResearchCapabilityError(
            "provider_transport_unavailable",
            "order-book provider transport is unavailable",
            retryable=True,
            origin="transport",
            cause_code=type(exc).__name__.lower(),
        ) from exc
    if not isinstance(payload, dict):
        raise ResearchCapabilityError(
            "provider_output_invalid",
            "order-book provider returned a non-object payload",
            retryable=False,
            origin="provider",
            cause_code="payload_type",
        )
    return payload


class CryptoCrowdingResearchAdapter:
    """Normalize a public exchange order book into a typed crowding proxy.

    ``crowding_signal`` is deliberately an order-book imbalance proxy, not a
    liquidation or leverage measure. The proxy kind is retained in the fact
    attributes so the domain Gate and UI cannot present it as a stronger fact
    than the provider actually supplied.
    """

    capability_id = "market.crypto_crowding"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset({"crowding_signal", "book_imbalance", "bid_depth", "ask_depth"})

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        exchange: str,
        fetcher: JsonFetcher | None = None,
    ) -> None:
        if not provider_id.strip() or not exchange.strip():
            raise ValueError("crowding_provider_identity_required")
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.exchange = exchange
        self.fetcher = fetcher or _fetch_json

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        symbol = _symbol(query.symbols)
        fields = set(query.fields) or set(self.supported_fields)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "unsupported crowding field requested",
                retryable=False,
                origin="provider",
            )
        url = self._url(symbol)
        payload = await self.fetcher(url)
        bid_depth, ask_depth = _depths(payload)
        total = bid_depth + ask_depth
        if total <= 0:
            raise ResearchCapabilityError(
                "research_market_output_invalid",
                "order-book provider returned zero usable depth",
                retryable=False,
                origin="provider",
                cause_code="empty_depth",
            )
        imbalance = (bid_depth - ask_depth) / total
        values: dict[str, str] = {
            "crowding_signal": _decimal_text(imbalance),
            "book_imbalance": _decimal_text(imbalance),
            "bid_depth": _decimal_text(bid_depth),
            "ask_depth": _decimal_text(ask_depth),
        }
        selected = {field: values[field] for field in sorted(fields)}
        received_at = datetime.now(UTC)
        excerpt = json.dumps(
            {
                "exchange": self.exchange,
                "instrument": symbol,
                "proxy_kind": "orderbook_imbalance",
                "values": selected,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )[:4000]
        content_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id=self.provider_id,
            source_url=url,
            published_at=None,
            excerpt=excerpt,
            structured_payload_ref=None,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id=self.provider_id,
            source_url=AnyUrl(url),
            published_at=None,
            observed_at=received_at,
            received_at=received_at,
            content_hash=content_hash,
            excerpt=excerpt,
            structured_payload_ref=None,
            tool_call_id=query.request_id,
            research_session_id=query.research_session_id,
            round=query.round,
            quality="candidate",
            freshness_status="unknown",
            conflict_group=None,
        )
        facts = [
            _fact(
                query=query,
                candidate=candidate,
                field=field,
                value=value,
                instrument=symbol,
                venue=self.exchange,
                received_at=received_at,
            )
            for field, value in selected.items()
        ]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider=self.provider_id,
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    def _url(self, symbol: str) -> str:
        if self.exchange == "okx":
            return f"{self.base_url}/api/v5/market/books?instId={symbol}&sz=20"
        if self.exchange == "coinex":
            # CoinEx v2 requires an explicit merge interval in addition to the
            # market and depth count. Without it the public endpoint returns
            # 4004 before any provider fallback can be evaluated.
            market = symbol.replace("-USDT-SWAP", "USDT")
            return f"{self.base_url}/v2/futures/depth?market={market}&limit=20&interval=0.01"
        raise ResearchCapabilityError(
            "provider_configuration_invalid",
            "unsupported crowding exchange",
            retryable=False,
            origin="gateway",
        )


def _symbol(symbols: Sequence[str]) -> str:
    aliases = {
        "BTC": "BTC-USDT-SWAP",
        "BTCUSDT": "BTC-USDT-SWAP",
        "BTC-USDT": "BTC-USDT-SWAP",
        "BTC-USDT-SWAP": "BTC-USDT-SWAP",
    }
    if len(symbols) != 1 or symbols[0].upper() not in aliases:
        raise ResearchCapabilityError(
            "research_symbols_required",
            "crowding capability requires one audited BTC perpetual symbol",
            retryable=False,
            origin="provider",
        )
    return aliases[symbols[0].upper()]


def _depths(payload: Mapping[str, object]) -> tuple[Decimal, Decimal]:
    """Read common OKX/CoinEx depth shapes without retaining raw order-book rows."""

    data: object = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, Mapping):
        nested = data.get("depth")
        if isinstance(nested, Mapping):
            data = nested
    if not isinstance(data, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "order-book provider returned no depth object",
            retryable=False,
            origin="provider",
            cause_code="depth_missing",
        )
    bids = _levels(data.get("bids") or data.get("buy"))
    asks = _levels(data.get("asks") or data.get("sell"))
    if not bids or not asks:
        raise ResearchCapabilityError(
            "research_market_output_invalid",
            "order-book provider returned no bid/ask levels",
            retryable=False,
            origin="provider",
            cause_code="levels_missing",
        )
    return sum(bids, Decimal(0)), sum(asks, Decimal(0))


def _levels(raw: object) -> list[Decimal]:
    if not isinstance(raw, list):
        return []
    result: list[Decimal] = []
    for level in raw[:100]:
        if isinstance(level, Mapping):
            size = level.get("sz") or level.get("quantity") or level.get("amount")
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            size = level[1]
        else:
            continue
        try:
            parsed = Decimal(str(size))
        except (InvalidOperation, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    field: str,
    value: str,
    instrument: str,
    venue: str,
    received_at: datetime,
) -> FactEnvelope:
    unit = "ratio" if field in {"crowding_signal", "book_imbalance"} else "contracts"
    attributes: dict[str, float | str | bool | None] = {
        "proxy_kind": "orderbook_imbalance",
        "provider_id": candidate.source_id,
    }
    payload_schema_ref = "crypto.orderbook-crowding.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family="crypto.derivatives",
        field=field,
        instrument=instrument,
        venue=venue,
        value=value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        published_at=None,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        attributes=attributes,
    )
    return FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id=research_fact_instance_id(
            evidence_id=candidate.evidence_id, payload_hash=payload_hash
        ),
        evidence_id=candidate.evidence_id,
        requirement_id=query.requirement_id,
        metric_family="crypto.derivatives",
        field=field,
        instrument=instrument,
        venue=venue,
        value=value,
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=None,
        source_id=candidate.source_id,
        independence_group=candidate.source_id,
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )
