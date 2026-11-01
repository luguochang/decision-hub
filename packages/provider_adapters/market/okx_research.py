from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

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
    async with CapturingAsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("OKX response must be an object")
    return payload


class OKXDerivativesResearchAdapter:
    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    # Keep the primary public OKX route capable of satisfying the same spot and
    # derivatives fields as the CoinEx fallback. Previously the narrower OKX
    # declaration caused the router to skip the primary for the normal BTC
    # request (spot_volume/index_price/basis), so every live run hit CoinEx.
    supported_fields = frozenset(
        {
            "ticker",
            "spot_price",
            "spot_volume",
            "funding_rate",
            "open_interest",
            "mark_price",
            "index_price",
            "basis",
        }
    )
    _default_fields = supported_fields
    supported_fields = supported_fields | {"perp_trade_price"}
    _supported_fields = supported_fields

    symbols_required = True

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://www.okx.com",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if not query.symbols:
            raise ResearchCapabilityError(
                "research_symbols_required", "OKX derivatives capability requires symbols"
            )
        fields = tuple(dict.fromkeys(query.fields)) or tuple(sorted(self._default_fields))
        if not set(fields) <= self._supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid", "unsupported OKX derivatives field requested"
            )
        requested_jobs = [
            (symbol, field, self._url(symbol, field))
            for symbol in dict.fromkeys(query.symbols)
            for field in fields
        ]
        # Each complete symbol/field observation keeps its own original and
        # linked facts. Honor the caller's count before spending any network.
        jobs = requested_jobs[: query.max_results]
        omitted = [
            {"symbol": symbol, "field": field}
            for symbol, field, _ in requested_jobs[query.max_results :]
        ]
        partial_attributes: dict[str, str] = {}
        coverage = None
        if omitted:
            partial_attributes = {
                "query_result_status": "partial_max_results",
                "requested_job_count": str(len(requested_jobs)),
                "selected_job_count": str(len(jobs)),
                "omitted_job_count": str(len(omitted)),
                "omitted_jobs_json": json.dumps(omitted, sort_keys=True, separators=(",", ":")),
            }
            coverage = {
                "status": "partial_max_results",
                "omitted_job_count": len(omitted),
                "omitted_jobs": omitted,
            }
            # The complete list remains in typed fact attributes. Keep the
            # existing excerpt bound without truncating a long gap declaration.
            if len(json.dumps(coverage)) > 1000:
                coverage = {
                    "status": "partial_max_results",
                    "omitted_job_count": len(omitted),
                    "omitted_jobs_fact_attribute": "omitted_jobs_json",
                }
        payloads = await asyncio.gather(
            *(self._fetch_job(symbol, field) for symbol, field, _ in jobs)
        )
        received_at = datetime.now(UTC)
        candidates: list[EvidenceCandidate] = []
        facts: list[FactEnvelope] = []
        for (symbol, field, url), payload in zip(jobs, payloads, strict=True):
            if field == "basis":
                mark_row = _first_okx_row(cast(Mapping[str, object], payload["mark"]))
                index_row = _first_okx_row(cast(Mapping[str, object], payload["index"]))
                row = {
                    "markPx": mark_row.get("markPx"),
                    "idxPx": index_row.get("idxPx"),
                    "ts": max(str(mark_row.get("ts", "")), str(index_row.get("ts", ""))),
                }
            else:
                row = _first_okx_row(payload)
            published_at, clock_attributes = _okx_source_clock(row, received_at)
            excerpt_body = {"field": field, "instrument": symbol, "value": row}
            field_attributes = dict(partial_attributes)
            if field == "perp_trade_price":
                original = cast(dict[str, object], payload["perpetual_original"])
                excerpt_body.update(
                    price_basis="trade",
                    perpetual_original={
                        key: original[key] for key in (
                            "body_hash", "instrument_id", "price_basis",
                            "provider_observed_at", "executable_quote",
                        )
                    },
                )
                field_attributes.update(
                    price_basis="trade", instrument_kind="perpetual",
                    capture_id=str(original["capture_id"]), body_hash=str(original["body_hash"]),
                    perpetual_original_trace_json=json.dumps(
                        original, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ),
                )
            if clock_attributes:
                excerpt_body["source_clock"] = (
                    {key: value for key, value in clock_attributes.items()
                     if key != "adapter_received_at"}
                    if field == "perp_trade_price" else clock_attributes
                )
            if coverage is not None:
                excerpt_body["query_coverage"] = coverage
            excerpt = json.dumps(
                excerpt_body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )[:4000]
            content_hash = research_evidence_content_hash(
                requirement_id=query.requirement_id,
                kind="market",
                authority="exchange",
                source_id="okx-public",
                source_url=url,
                published_at=published_at,
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
                source_id="okx-public",
                source_url=AnyUrl(url),
                published_at=published_at,
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
            candidates.append(candidate)
            fact = _okx_fact(
                query=query,
                candidate=candidate,
                symbol=symbol,
                provider_field=field,
                row=row,
                published_at=published_at,
                clock_attributes=clock_attributes,
                received_at=received_at,
                query_attributes=field_attributes,
            )
            if fact is not None:
                facts.append(fact)
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="okx-public",
            evidence_candidates=candidates,
            facts=facts,
            cost_usd=0.0,
            completed_at=received_at,
        )

    async def _fetch_job(self, symbol: str, field: str) -> Mapping[str, object]:
        if field == "perp_trade_price":
            from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import (
                collect_public_original_capture,
            )

            from .perpetual_trade import collect_perpetual_trade

            return await collect_perpetual_trade(
                _swap_instrument(symbol), collector=collect_public_original_capture,
            )
        if field != "basis":
            return await self.fetcher(self._url(symbol, field))
        mark_url = self._url(symbol, "mark_price")
        index_url = self._url(symbol, "index_price")
        mark, index = await asyncio.gather(self.fetcher(mark_url), self.fetcher(index_url))
        return {"mark": mark, "index": index}

    def _url(self, symbol: str, field: str) -> str:
        if field == "perp_trade_price":
            from packages.kernel.decision_hub_kernel.ports.okx_perpetual_original import public_url

            return public_url(_swap_instrument(symbol), "ticker")
        if field in {"ticker", "spot_price", "spot_volume"}:
            inst_id = _spot_instrument(symbol)
            return f"{self.base_url}/api/v5/market/ticker?instId={inst_id}"
        inst_id = _swap_instrument(symbol)
        if field == "funding_rate":
            return f"{self.base_url}/api/v5/public/funding-rate?instId={inst_id}"
        if field == "open_interest":
            return f"{self.base_url}/api/v5/public/open-interest?instType=SWAP&instId={inst_id}"
        if field == "index_price":
            return f"{self.base_url}/api/v5/market/index-tickers?instId={_spot_instrument(symbol)}"
        return f"{self.base_url}/api/v5/public/mark-price?instType=SWAP&instId={inst_id}"


def _spot_instrument(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("/", "-")
    if normalized.endswith("-SWAP"):
        normalized = normalized[:-5]
    if "-" not in normalized and normalized.endswith("USDT"):
        normalized = f"{normalized[:-4]}-USDT"
    return normalized


def _swap_instrument(symbol: str) -> str:
    spot = _spot_instrument(symbol)
    return spot if spot.endswith("-SWAP") else f"{spot}-SWAP"


def _first_okx_row(payload: Mapping[str, object]) -> Mapping[str, object]:
    if str(payload.get("code", "0")) != "0":
        raise ResearchCapabilityError(
            "research_market_unavailable", "OKX returned an unsuccessful response", retryable=True
        )
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX response has no data row"
        )
    return data[0]


def _okx_timestamp(row: Mapping[str, object]) -> datetime | None:
    raw = row.get("ts")
    try:
        return datetime.fromtimestamp(int(str(raw)) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _okx_source_clock(
    row: Mapping[str, object], received_at: datetime
) -> tuple[datetime | None, dict[str, str]]:
    """Classify once per observation; a leading provider clock is not a receipt."""
    published_at = _okx_timestamp(row)
    if published_at is None or published_at <= received_at:
        return published_at, {}
    return None, {
        "provider_timestamp_raw": str(row["ts"]),
        "adapter_received_at": received_at.isoformat(),
        "source_clock_status": "ahead_unverified",
    }


def _spot_volume_unit(symbol: str, row: Mapping[str, object]) -> str:
    # The admitted request chooses the asset; a provider row may only confirm
    # that identity, never select a different asset or supply missing scope.
    expected = _spot_instrument(symbol)
    base, separator, quote = expected.partition("-")
    if not separator or not base.isalnum() or quote != "USDT":
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX spot volume request instrument is invalid"
        )
    if row.get("instId") != expected:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX spot volume instrument does not match request"
        )
    return base.lower()


def _open_interest_unit(symbol: str, row: Mapping[str, object]) -> str:
    expected = _swap_instrument(symbol)
    parts = expected.split("-")
    if len(parts) != 3 or parts[2] != "SWAP" or not all(part.isalnum() for part in parts[:2]):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX open interest request instrument is invalid"
        )
    if row.get("instId") != expected:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "OKX open interest instrument does not match request"
        )
    return parts[0].lower()


def _okx_fact(
    *,
    query: ResearchCapabilityQuery,
    candidate: EvidenceCandidate,
    symbol: str,
    provider_field: str,
    row: Mapping[str, object],
    published_at: datetime | None,
    clock_attributes: Mapping[str, str],
    received_at: datetime,
    query_attributes: Mapping[str, str],
) -> FactEnvelope | None:
    mappings = {
        "ticker": ("price", "last", "usdt"),
        "perp_trade_price": ("price", "last", "usdt"),
        "spot_price": ("price", "last", "usdt"),
        "spot_volume": ("volume", "vol24h", "btc"),
        "funding_rate": ("funding_rate", "fundingRate", "rate"),
        "open_interest": ("open_interest", "oiCcy", "btc"),
        "mark_price": ("mark_price", "markPx", "usdt"),
        "index_price": ("index_price", "idxPx", "usdt"),
        "basis": ("basis", None, "rate"),
    }
    if provider_field == "basis":
        field, unit = "basis", "rate"
        try:
            mark = Decimal(str(row.get("markPx")))
            index = Decimal(str(row.get("idxPx")))
            if index == 0:
                return None
            value = str((mark - index) / index)
        except (InvalidOperation, TypeError, ValueError):
            return None
    else:
        field, row_key, unit = mappings[provider_field]
        assert row_key is not None
        value = row.get(row_key)
        if provider_field == "spot_volume":
            unit = _spot_volume_unit(symbol, row)
        elif provider_field == "open_interest":
            unit = _open_interest_unit(symbol, row)
    if value in {None, ""}:
        return None
    attributes: dict[str, float | str | bool | None] = {
        "provider_field": provider_field,
        **query_attributes,
        **clock_attributes,
    }
    payload_schema_ref = "okx.public-api.v5"
    metric_family = (
        "crypto.spot"
        if provider_field in {"ticker", "spot_price", "spot_volume"}
        else "crypto.derivatives"
    )
    payload_hash = research_fact_payload_hash(
        requirement_id=query.requirement_id,
        metric_family=metric_family,
        field=field,
        instrument=symbol,
        venue="okx",
        value=str(value),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="okx",
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
        metric_family=metric_family,
        field=field,
        instrument=symbol,
        venue="okx",
        value=str(value),
        unit=unit,
        window_start_at=None,
        window_end_at=None,
        event_offset=None,
        observed_at=received_at,
        received_at=received_at,
        published_at=published_at,
        source_id=candidate.source_id,
        independence_group="okx",
        quality="candidate",
        delay_class="realtime",
        payload_schema_ref=payload_schema_ref,
        payload_hash=payload_hash,
        attributes=attributes,
    )
