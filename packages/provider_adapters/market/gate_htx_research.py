"""Typed public derivatives adapters for exchanges reachable from the local runtime.

The original live route only had OKX and Binance derivatives adapters.  Those
endpoints are not reachable from some deployments, while Gate and HTX expose
the same public fields without credentials.  These adapters deliberately stay
read-only and emit the existing ``FactEnvelope`` shape; they do not change any
direction or execution policy.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

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
from packages.provider_adapters.http_errors import classify_provider_exception
from packages.provider_adapters.research.raw_capture import CapturingAsyncClient

JsonFetcher = Callable[[str], Awaitable[Any]]


async def _fetch_json(url: str) -> Any:
    """Fetch one public endpoint with one bounded transport retry.

    HTX exposes one logical snapshot through several public endpoints.  A
    single transient TLS/connect reset used to discard the whole venue even
    when an immediate retry succeeded.  Keep the retry inside the adapter so
    the outer provider router still records one auditable venue attempt.  HTTP
    rejections and payload errors are never retried here.
    """

    for attempt in range(2):
        try:
            async with CapturingAsyncClient(
                timeout=httpx.Timeout(4.0, connect=2.0), follow_redirects=True
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            if attempt == 0:
                await asyncio.sleep(0.15)
                continue
            error = classify_provider_exception(exc)
            raise error from exc
        except Exception as exc:
            error = classify_provider_exception(exc)
            raise error from exc

    raise AssertionError("bounded provider retry loop is exhaustive")  # pragma: no cover


_FIELDS = frozenset(
    {
        "funding_rate",
        "open_interest",
        "open_interest_delta",
        "mark_price",
        "index_price",
        "basis",
    }
)


_GATE_SYMBOLS = {
    "ETH": "ETH_USDT",
    "ETHUSDT": "ETH_USDT",
    "ETH-USDT": "ETH_USDT",
    "ETH-USDT-SWAP": "ETH_USDT",
    "BTC": "BTC_USDT",
    "BTCUSDT": "BTC_USDT",
    "BTC-USDT": "BTC_USDT",
    "BTC-USDT-SWAP": "BTC_USDT",
}

_HTX_SYMBOLS = {
    "ETH": "ETH-USDT",
    "ETHUSDT": "ETH-USDT",
    "ETH-USDT": "ETH-USDT",
    "ETH-USDT-SWAP": "ETH-USDT",
    "BTC": "BTC-USDT",
    "BTCUSDT": "BTC-USDT",
    "BTC-USDT": "BTC-USDT",
    "BTC-USDT-SWAP": "BTC-USDT",
}


class GateDerivativesResearchAdapter:
    """Gate.io USDT perpetual public snapshot and short OI change window."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_symbols = frozenset(_GATE_SYMBOLS)
    symbols_required = True
    max_symbols = 1
    supported_fields = _FIELDS

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://api.gateio.ws/api/v4",
        provider_id: str = "gateio-derivatives-public",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        contract = _gate_contract(query.symbols)
        fields = set(query.fields) or set(self.supported_fields)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "Gate derivatives adapter supports only typed perpetual fields",
            )
        ticker_url = f"{self.base_url}/futures/usdt/tickers?contract={contract}"
        stats_url = (
            f"{self.base_url}/futures/usdt/contract_stats?"
            f"{urlencode({'contract': contract, 'interval': '5m', 'limit': 10})}"
        )
        urls = {"ticker": ticker_url}
        if fields & {"open_interest", "open_interest_delta"}:
            urls["stats"] = stats_url
        payloads = dict(
            zip(
                urls,
                await asyncio.gather(*(self.fetcher(url) for url in urls.values())),
                strict=True,
            )
        )
        ticker = _gate_ticker(payloads["ticker"])
        if ticker.get("contract") != contract:
            raise ResearchCapabilityError(
                "research_market_output_invalid", "Gate ticker contract does not match query"
            )
        stats = _gate_stats(payloads.get("stats")) if "stats" in urls else []
        values, windows = _gate_values(fields, ticker, stats)
        return _build_derivatives_result(
            query=query,
            provider_id=self.provider_id,
            venue="gateio",
            instrument=contract.replace("_", "-") + "-SWAP",
            urls=urls,
            values=values,
            windows=windows,
            derivations=(
                {"open_interest_delta": _oi_change_original(stats[-2], stats[-1], "open_interest")}
                if "open_interest_delta" in fields else {}
            ),
            received_at=datetime.now(UTC),
            payload_schema_ref="gate.public-futures.v4",
        )


class HtxDerivativesResearchAdapter:
    """HTX/Huobi USDT swap public snapshot and historical OI delta."""

    capability_id = "market.crypto_derivatives"
    supported_modes = frozenset({"live"})
    supported_symbols = frozenset(_HTX_SYMBOLS)
    symbols_required = True
    max_symbols = 1
    supported_fields = _FIELDS

    def __init__(
        self,
        *,
        fetcher: JsonFetcher | None = None,
        base_url: str = "https://api.hbdm.com",
        provider_id: str = "htx-derivatives-public",
    ) -> None:
        self.fetcher = fetcher or _fetch_json
        self.base_url = base_url.rstrip("/")
        self.provider_id = provider_id

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        contract = _htx_contract(query.symbols)
        fields = set(query.fields) or set(self.supported_fields)
        if not fields <= self.supported_fields:
            raise ResearchCapabilityError(
                "research_market_field_invalid",
                "HTX derivatives adapter supports only typed perpetual fields",
            )
        urls = {
            "ticker": (
                f"{self.base_url}/linear-swap-ex/market/detail/merged?contract_code={contract}"
            ),
            "funding": (
                f"{self.base_url}/linear-swap-api/v1/swap_funding_rate?contract_code={contract}"
            ),
            "index": (f"{self.base_url}/linear-swap-api/v1/swap_index?contract_code={contract}"),
            "open_interest": (
                f"{self.base_url}/linear-swap-api/v1/swap_open_interest?contract_code={contract}"
            ),
        }
        if "open_interest_delta" in fields:
            urls["history"] = (
                f"{self.base_url}/linear-swap-api/v1/swap_his_open_interest?"
                f"{urlencode({'contract_code': contract, 'period': '60min', 'amount_type': 1})}"
            )
        # Each endpoint is independent and bounded by the gateway route timeout.
        payloads = dict(
            zip(
                urls,
                await asyncio.gather(*(self.fetcher(url) for url in urls.values())),
                strict=True,
            )
        )
        ticker = _htx_ticker(payloads["ticker"])
        funding = _htx_data_object(payloads["funding"], "funding")
        index = _htx_first_data(payloads["index"], "index")
        open_interest = _htx_first_data(payloads["open_interest"], "open_interest")
        history = _htx_history(payloads.get("history")) if "history" in urls else []
        # Reject an upstream response naming another asset. URL-only snapshots
        # without a symbol retain the existing provider provenance boundary.
        rows = [funding, index, open_interest]
        if "history" in payloads:
            rows.append(_htx_data_object(payloads["history"], "open-interest history"))
        if any(row.get("contract_code", contract) != contract for row in rows):
            raise ResearchCapabilityError(
                "research_market_output_invalid", "HTX contract does not match query"
            )
        channel = payloads["ticker"].get("ch")
        if channel is not None and channel != f"market.{contract}.detail.merged":
            raise ResearchCapabilityError(
                "research_market_output_invalid", "HTX ticker channel does not match query"
            )
        values, windows = _htx_values(fields, ticker, funding, index, open_interest, history)
        return _build_derivatives_result(
            query=query,
            provider_id=self.provider_id,
            venue="htx",
            instrument=contract + "-SWAP",
            urls=urls,
            values=values,
            windows=windows,
            derivations=(
                {"open_interest_delta": _oi_change_original(history[-2], history[-1], "volume")}
                if "open_interest_delta" in fields else {}
            ),
            received_at=datetime.now(UTC),
            payload_schema_ref="htx.public-linear-swap.v1",
        )


def _gate_contract(symbols: Sequence[str]) -> str:
    aliases = _GATE_SYMBOLS
    if len(symbols) != 1 or symbols[0].upper() not in aliases:
        raise ResearchCapabilityError(
            "research_symbols_required", "Gate derivatives requires one BTC/USDT or ETH/USDT symbol"
        )
    return aliases[symbols[0].upper()]


def _htx_contract(symbols: Sequence[str]) -> str:
    aliases = _HTX_SYMBOLS
    if len(symbols) != 1 or symbols[0].upper() not in aliases:
        raise ResearchCapabilityError(
            "research_symbols_required", "HTX derivatives requires one BTC/USDT or ETH/USDT symbol"
        )
    return aliases[symbols[0].upper()]


def _gate_ticker(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Gate ticker has no contract row"
        )
    return payload[0]


def _gate_stats(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Gate contract stats has no rows"
        )
    rows = sorted(
        (row for row in payload if row.get("open_interest") is not None),
        key=_gate_stats_time,
    )
    if len(rows) < 2:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "Gate contract stats requires two OI rows"
        )
    return rows


def _gate_values(
    fields: set[str], ticker: Mapping[str, object], stats: list[Mapping[str, object]]
) -> tuple[dict[str, str], dict[str, tuple[datetime, datetime]]]:
    latest = stats[-1] if stats else {}
    values: dict[str, str] = {}
    windows: dict[str, tuple[datetime, datetime]] = {}
    mapping = {
        "funding_rate": ticker.get("funding_rate") or ticker.get("funding_rate_indicative"),
        "open_interest": ticker.get("total_size") or latest.get("open_interest"),
        "mark_price": ticker.get("mark_price"),
        "index_price": ticker.get("index_price"),
    }
    for field in fields - {"basis", "open_interest_delta"}:
        value = mapping.get(field)
        if value in {None, ""}:
            raise ResearchCapabilityError(
                "research_market_output_invalid", f"Gate response is missing {field}"
            )
        values[field] = str(value)
    if "basis" in fields:
        basis = ticker.get("basis_rate")
        if basis in {None, ""}:
            basis = _ratio(mapping.get("mark_price"), mapping.get("index_price"))
        values["basis"] = str(basis)
    if "open_interest_delta" in fields:
        before, after = stats[-2], stats[-1]
        values["open_interest_delta"] = _change_percent(
            before.get("open_interest"), after.get("open_interest")
        )
        start, end = _unix_datetime(before.get("time")), _unix_datetime(after.get("time"))
        windows["open_interest_delta"] = (start, end)
    return values, windows


def _htx_envelope(payload: object, label: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or str(payload.get("status", "ok")) != "ok":
        raise ResearchCapabilityError(
            "research_market_unavailable", f"HTX {label} response is unsuccessful", retryable=True
        )
    return payload


def _htx_ticker(payload: object) -> Mapping[str, object]:
    envelope = _htx_envelope(payload, "ticker")
    tick = envelope.get("tick")
    if not isinstance(tick, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "HTX ticker has no tick object"
        )
    return tick


def _htx_data_object(payload: object, label: str) -> Mapping[str, object]:
    envelope = _htx_envelope(payload, label)
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", f"HTX {label} has no data object"
        )
    return data


def _htx_first_data(payload: object, label: str) -> Mapping[str, object]:
    envelope = _htx_envelope(payload, label)
    data = envelope.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
        raise ResearchCapabilityError(
            "research_market_output_invalid", f"HTX {label} has no data row"
        )
    return data[0]


def _htx_history(payload: object) -> list[Mapping[str, object]]:
    envelope = _htx_envelope(payload, "open-interest history")
    data = envelope.get("data")
    rows = data.get("tick") if isinstance(data, Mapping) else None
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ResearchCapabilityError(
            "research_market_output_invalid", "HTX open-interest history has no rows"
        )
    rows = sorted(
        (row for row in rows if row.get("volume") is not None),
        key=_htx_history_time,
    )
    if len(rows) < 2:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "HTX open-interest history requires two rows"
        )
    return rows


def _htx_values(
    fields: set[str],
    ticker: Mapping[str, object],
    funding: Mapping[str, object],
    index: Mapping[str, object],
    open_interest: Mapping[str, object],
    history: list[Mapping[str, object]],
) -> tuple[dict[str, str], dict[str, tuple[datetime, datetime]]]:
    mapping = {
        "funding_rate": funding.get("funding_rate"),
        # HTX public detail is the latest executable swap price; retain this
        # provenance rather than inventing an unavailable mark-price endpoint.
        "mark_price": ticker.get("close"),
        "index_price": index.get("index_price"),
        "open_interest": open_interest.get("volume"),
    }
    values: dict[str, str] = {}
    windows: dict[str, tuple[datetime, datetime]] = {}
    for field in fields - {"basis", "open_interest_delta"}:
        value = mapping.get(field)
        if value in {None, ""}:
            raise ResearchCapabilityError(
                "research_market_output_invalid", f"HTX response is missing {field}"
            )
        values[field] = str(value)
    if "basis" in fields:
        values["basis"] = _ratio(mapping.get("mark_price"), mapping.get("index_price"))
    if "open_interest_delta" in fields:
        before, after = history[-2], history[-1]
        values["open_interest_delta"] = _change_percent(before.get("volume"), after.get("volume"))
        windows["open_interest_delta"] = (
            _millis_datetime(before.get("ts")),
            _millis_datetime(after.get("ts")),
        )
    return values, windows


def _oi_change_original(
    before: Mapping[str, object], after: Mapping[str, object], field: str,
) -> dict[str, str]:
    """Retain the exact calculation inputs in the reviewable evidence body."""
    return {
        "base_field": field,
        "base_unit": "contracts",
        "start_value": str(before[field]),
        "end_value": str(after[field]),
        "formula": "(end_value/start_value-1)*100",
        "unit": "percent",
    }


def _build_derivatives_result(
    *,
    query: ResearchCapabilityQuery,
    provider_id: str,
    venue: str,
    instrument: str,
    urls: Mapping[str, str],
    values: Mapping[str, str],
    windows: Mapping[str, tuple[datetime, datetime]],
    received_at: datetime,
    payload_schema_ref: str,
    derivations: Mapping[str, object] | None = None,
) -> ResearchCapabilityResult:
    units = {
        "funding_rate": "rate",
        "open_interest": "contracts",
        "open_interest_delta": "percent",
        "mark_price": "usdt",
        "index_price": "usdt",
        "basis": "rate",
    }
    excerpt = json.dumps(
        {
            "provider": provider_id,
            "instrument": instrument,
            "values": values,
            "source_urls": urls,
            "observed_at": received_at.isoformat(),
            "units": {field: units[field] for field in values},
            "windows": {
                field: {"start_at": start.isoformat(), "end_at": end.isoformat()}
                for field, (start, end) in windows.items()
            },
            "derivations": dict(derivations or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    primary_url = urls["ticker"]
    content_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id,
        kind="market",
        authority="exchange",
        source_id=provider_id,
        source_url=primary_url,
        published_at=None,
        excerpt=excerpt,
        structured_payload_ref=None,
    )
    candidate = EvidenceCandidate(
        evidence_id=research_evidence_instance_id(
            content_hash=content_hash, research_session_id=query.research_session_id
        ),
        requirement_id=query.requirement_id,
        kind="market",
        authority="exchange",
        source_id=provider_id,
        source_url=AnyUrl(primary_url),
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
    facts: list[FactEnvelope] = []
    aliases = {"spot_price": "price", "spot_volume": "volume"}
    for provider_field, value in sorted(values.items()):
        window = windows.get(provider_field)
        attrs: dict[str, float | str | bool | None] = {"provider_field": provider_field}
        if provider_field == "mark_price" and provider_id.startswith("htx-"):
            attrs["proxy_kind"] = "latest_swap_price"
        payload_hash = research_fact_payload_hash(
            requirement_id=query.requirement_id,
            metric_family="crypto.derivatives",
            field=aliases.get(provider_field, provider_field),
            instrument=instrument,
            venue=venue,
            value=value,
            unit=units[provider_field],
            window_start_at=window[0] if window else None,
            window_end_at=window[1] if window else None,
            event_offset=None,
            published_at=None,
            source_id=provider_id,
            independence_group=provider_id,
            delay_class="realtime",
            payload_schema_ref=payload_schema_ref,
            attributes=attrs,
        )
        facts.append(
            FactEnvelope(
                schema_version="fact-envelope.v1",
                fact_id=research_fact_instance_id(
                    evidence_id=candidate.evidence_id, payload_hash=payload_hash
                ),
                evidence_id=candidate.evidence_id,
                requirement_id=query.requirement_id,
                metric_family="crypto.derivatives",
                field=aliases.get(provider_field, provider_field),
                instrument=instrument,
                venue=venue,
                value=value,
                unit=units[provider_field],
                window_start_at=window[0] if window else None,
                window_end_at=window[1] if window else None,
                event_offset=None,
                observed_at=received_at,
                received_at=received_at,
                published_at=None,
                source_id=provider_id,
                independence_group=provider_id,
                quality="candidate",
                delay_class="realtime",
                payload_schema_ref=payload_schema_ref,
                payload_hash=payload_hash,
                attributes=attrs,
            )
        )
    return ResearchCapabilityResult(
        schema_version="research-capability-result.v1",
        request_id=query.request_id,
        capability_id=query.capability_id,
        provider=provider_id,
        evidence_candidates=[candidate],
        facts=facts,
        cost_usd=0.0,
        completed_at=received_at,
    )


def _number(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


def _gate_stats_time(row: Mapping[str, object]) -> float:
    return _number(row.get("time"))


def _htx_history_time(row: Mapping[str, object]) -> float:
    return _number(row.get("ts"))


def _ratio(mark: object, index: object) -> str:
    try:
        mark_value, index_value = Decimal(str(mark)), Decimal(str(index))
        if index_value == 0:
            raise InvalidOperation
        return str((mark_value - index_value) / index_value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "derivatives response cannot produce basis"
        ) from exc


def _change_percent(before: object, after: object) -> str:
    try:
        old, new = Decimal(str(before)), Decimal(str(after))
        if old == 0:
            raise InvalidOperation
        return str((new - old) / old * Decimal("100"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ResearchCapabilityError(
            "research_market_output_invalid", "derivatives response cannot produce OI change"
        ) from exc


def _unix_datetime(value: object) -> datetime:
    return datetime.fromtimestamp(_number(value), tz=UTC)


def _millis_datetime(value: object) -> datetime:
    return datetime.fromtimestamp(_number(value) / 1000, tz=UTC)
