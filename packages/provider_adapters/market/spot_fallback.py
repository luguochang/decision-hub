from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

JsonFetcher = Callable[[str], Awaitable[Mapping[str, object]]]


async def _fetch_json(url: str) -> Mapping[str, object]:
    async with CapturingAsyncClient(timeout=12, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise ValueError("spot fallback response must be an object or list")
    return {"payload": payload}


class PublicSpotMarketResearchAdapter:
    """Read-only BTC/ETH spot from approved public exchange endpoints.

    This adapter deliberately exposes only spot price and volume. It cannot
    satisfy funding/open-interest/crowding requirements, so adding a route here
    cannot accidentally turn a spot quote into derivatives evidence.
    """

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_fields = frozenset({"spot_price", "spot_volume"})
    supported_symbols = frozenset(
        {"BTC", "BTC-USDT", "BTC/USDT", "BTC-USDT-SWAP", "BTC/USDT/SWAP", "BTCUSDT"}
        | {"ETH", "ETH-USDT", "ETH/USDT", "ETH-USDT-SWAP", "ETH/USDT/SWAP", "ETHUSDT"}
    )
    symbols_required = True
    max_symbols = 1

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        parser: Callable[[Mapping[str, object]], tuple[str, str]],
        fetcher: JsonFetcher | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.parser = parser
        self.fetcher = fetcher or _fetch_json

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        symbols = {
            "BTC": "BTC",
            "BTC-USDT": "BTC",
            "BTC-USDT-SWAP": "BTC",
            "BTCUSDT": "BTC",
            "ETH": "ETH",
            "ETH-USDT": "ETH",
            "ETH-USDT-SWAP": "ETH",
            "ETHUSDT": "ETH",
        }
        if len(query.symbols) != 1 or query.symbols[0].upper().replace("/", "-") not in symbols:
            raise ResearchCapabilityError(
                "research_symbols_required",
                f"{self.provider_id} spot fallback requires one BTC/USDT or ETH/USDT symbol",
            )
        asset = symbols[query.symbols[0].upper().replace("/", "-")]
        instrument = f"{asset}-USDT"
        fields = set(query.fields) or set(self.supported_fields)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                f"{self.provider_id} spot fallback supports only spot price and volume",
            )
        url = self._url(asset)
        payload = await self.fetcher(url)
        raw = payload.get("payload")
        if self.provider_id == "gateio-public" and isinstance(raw, list) and raw:
            if (
                isinstance(raw[0], Mapping)
                and raw[0].get("currency_pair", f"{asset}_USDT") != f"{asset}_USDT"
            ):
                raise ResearchCapabilityError(
                    "research_market_output_invalid", "Gate spot pair does not match query"
                )
        elif self.provider_id == "huobi-public" and isinstance(raw, Mapping):
            if (
                raw.get("ch", f"market.{asset.lower()}usdt.detail.merged")
                != f"market.{asset.lower()}usdt.detail.merged"
            ):
                raise ResearchCapabilityError(
                    "research_market_output_invalid", "Huobi spot channel does not match query"
                )
        price, volume = self.parser(payload)
        values = {"spot_price": price, "spot_volume": volume}
        received_at = datetime.now(UTC)
        excerpt = json.dumps(
            {
                "provider": self.provider_id,
                "symbol": instrument,
                "values": {field: values[field] for field in sorted(fields)},
                "observed_at": received_at.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
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
                field={"spot_price": "price", "spot_volume": "volume"}[field],
                value=values[field],
                unit={"spot_price": "usdt", "spot_volume": asset.lower()}[field],
                instrument=instrument,
                received_at=received_at,
            )
            for field in sorted(fields)
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

    def _url(self, asset: str) -> str:
        """Replace only the finite public symbol parameter on the existing route."""
        parts = urlsplit(self.base_url)
        parameters = dict(parse_qsl(parts.query, keep_blank_values=True))
        if self.provider_id == "gateio-public":
            parameters["currency_pair"] = f"{asset}_USDT"
        elif self.provider_id == "huobi-public":
            parameters["symbol"] = f"{asset.lower()}usdt"
        else:
            raise ResearchCapabilityError(
                "provider_configuration_invalid", "unsupported public spot provider"
            )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(parameters), ""))


def parse_gate_spot(payload: Mapping[str, object]) -> tuple[str, str]:
    raw = payload.get("payload")
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Gate spot response has no ticker"
        )
    row = raw[0]
    return _required(row.get("last"), "last"), _required(row.get("base_volume"), "base_volume")


def parse_huobi_spot(payload: Mapping[str, object]) -> tuple[str, str]:
    raw = payload.get("payload")
    if not isinstance(raw, Mapping) or raw.get("status") != "ok":
        raise ResearchCapabilityError(
            "research_market_unavailable", "Huobi spot response is unsuccessful"
        )
    tick = raw.get("tick")
    if not isinstance(tick, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Huobi spot response has no ticker"
        )
    return _required(tick.get("close"), "close"), _required(tick.get("amount"), "amount")


def _required(value: object, field: str) -> str:
    if value in {None, ""}:
        raise ResearchCapabilityError(
            "research_market_output_invalid", f"spot response is missing {field}"
        )
    return str(value)


def _fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    field: str,
    value: str,
    unit: str,
    instrument: str,
    received_at: datetime,
) -> FactEnvelope:
    attributes: dict[str, float | str | bool | None] = {"provider_field": field}
    payload_schema_ref = "public-exchange.spot-ticker.v1"
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family="crypto.spot",
        field=field,
        instrument=instrument,
        venue=candidate.source_id.removesuffix("-public"),
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
        metric_family="crypto.spot",
        field=field,
        instrument=instrument,
        venue=candidate.source_id.removesuffix("-public"),
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
