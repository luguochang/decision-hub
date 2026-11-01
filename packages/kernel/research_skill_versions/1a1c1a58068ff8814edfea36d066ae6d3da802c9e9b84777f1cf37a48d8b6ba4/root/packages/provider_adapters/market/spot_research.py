"""BTC/ETH spot research from committed OKX candles, with no provider requests.

The last closed trade candle is not a current bid/ask quote. A path fact is a
whole-window calculation/manifest, not a claim that derivatives were obtained.
Gateway and Kernel admission retain authority over candidate E/Fact promotion.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from pydantic import AnyUrl

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    FactEnvelope,
    ResearchCapabilityManifest,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
)
from packages.kernel.decision_hub_kernel.application.fact_store import (
    research_fact_instance_id,
    research_fact_payload_hash,
)
from packages.kernel.decision_hub_kernel.application.market_features import build_market_features
from packages.kernel.decision_hub_kernel.application.market_risk_data import MarketRiskDataService
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)
from packages.kernel.decision_hub_kernel.decision.path_risk import compute_path_risk
from packages.kernel.decision_hub_kernel.persistence.db import utcnow
from packages.kernel.decision_hub_kernel.ports.market_path import (
    aware_utc,
    canonical_hash,
    decimal_text,
    spot_scope,
)

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.spot_scope_admission import (
        EffectiveSpotRecipe,
        SpotCapabilityRoute,
    )

IMPLEMENTATION = "packages.provider_adapters.market.spot_research.SpotMarketFactsAdapter"
CAPABILITY = "spot_market_facts"
PROVIDER = "okx-spot-ledger"
VERSION = "okx-spot-ledger.v1"
FIELDS = frozenset(
    {
        "spot_price",
        "spot_volume",
        "return_30m",
        "return_24h",
        "realized_vol_30m",
        "path",
    }
)
INSTRUMENTS = frozenset({"BTC-USDT", "ETH-USDT"})
CODE_FILES = (
    "packages/provider_adapters/market/spot_research.py",
    "packages/kernel/decision_hub_kernel/application/market_path_store.py",
    "packages/kernel/decision_hub_kernel/application/market_features.py",
    "packages/kernel/decision_hub_kernel/decision/path_risk.py",
    "packages/kernel/decision_hub_kernel/ports/market_path.py",
)


class _FactPayload(TypedDict):
    requirement_id: str
    metric_family: str
    field: str
    instrument: str | None
    venue: str | None
    value: float | str | None
    unit: str
    window_start_at: datetime | None
    window_end_at: datetime | None
    event_offset: str | None
    published_at: datetime | None
    source_id: str
    independence_group: str
    delay_class: Literal["realtime", "delayed", "historical", "unknown"]
    payload_schema_ref: str
    attributes: dict[str, float | str | bool | None]


class _SpotRouteMaterial(TypedDict):
    schema_version: str
    capability_id: str
    provider_id: str
    implementation_ref: str
    adapter_version: str
    asset_mode: Literal["instrument"]
    instrument: str
    scope_id: str
    supported_fields: list[str]
    manifest_hash: str
    code_files: dict[str, str]
    network_requests: bool
    source_domains_are_provenance_only: bool
    product_release_qualified: bool
    forecast_qualified: bool


class _SignedRouteReceipt(TypedDict):
    body: _SpotRouteMaterial
    signature: str


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _metric_text(value: Decimal) -> str:
    # Store verifies source OHLC precision; a derived ratio can legitimately
    # have >18 decimal places. Preserve the computed value without rounding.
    if not value.is_finite():
        raise _denied("spot_research_metric_invalid")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _denied(code: str) -> ResearchCapabilityError:
    return ResearchCapabilityError(code, code, retryable=False, origin="storage")


class SpotMarketFactsAdapter:
    capability_id = CAPABILITY
    provider_id = PROVIDER
    adapter_version = VERSION
    supported_modes = frozenset({"live", "replay"})
    supported_symbols = INSTRUMENTS
    supported_fields = FIELDS
    symbols_required = True
    max_symbols = 1
    usage_notes = (
        "One exact BTC-USDT or ETH-USDT spot symbol; no aliases or swaps. "
        "Reads committed OKX trade candles only. spot_price is the latest closed "
        "1m close; spot_volume is that candle's base volume, not a 24h ticker. "
        "path independently evaluates the complete requested minute window. "
        "Funding, open interest, bid/ask spread and crowding remain unknown."
    )

    def __init__(
        self, data: MarketRiskDataService, *, clock: Callable[[], datetime] = utcnow,
        read_enabled: bool | None = None,
    ) -> None:
        self.data, self.clock = data, clock
        # Reading committed originals is independent of the worker's HTTP
        # acquisition switch. Only the composition root grants this permission.
        self.read_enabled = data.enabled if read_enabled is None else read_enabled

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        if (
            query.capability_id != CAPABILITY
            or query.mode not in self.supported_modes
            or len(query.symbols) != 1
            or query.symbols[0] not in INSTRUMENTS
            or len(set(query.fields)) != len(query.fields)
            or not set(query.fields) <= FIELDS
            or query.target_url is not None
        ):
            raise _denied("spot_research_query_scope_invalid")
        instrument = query.symbols[0]
        scope = spot_scope(instrument)
        if not self.read_enabled or self.data.scopes.get(scope.scope_id) != scope:
            raise _denied("spot_research_scope_not_enabled")
        started = aware_utc(self.clock())
        cutoff = aware_utc(query.cutoff_at)
        deadline = aware_utc(query.deadline_at) if query.deadline_at else cutoff
        if started > deadline or (query.mode == "live" and started > cutoff):
            raise _denied("spot_research_deadline_exceeded")
        as_of = min(started, cutoff)
        end = query.window_end_at or as_of.replace(second=0, microsecond=0)
        start = query.window_start_at or end - timedelta(hours=24)
        start, end = aware_utc(start), aware_utc(end)
        if (
            not start < end <= as_of
            or (end - start) > timedelta(hours=24)
            or start.timestamp() % 60
            or end.timestamp() % 60
        ):
            raise _denied("spot_research_window_invalid")
        # One extra endpoint lets a complete 24h path also supply a 24h close return.
        try:
            bars = self.data.store.load(
                scope,
                start_at=start - timedelta(minutes=4),
                end_at=end,
                as_of=as_of,
            )
        except (ValueError, OSError) as exc:
            raise _denied("spot_research_original_invalid") from exc
        if not bars or not any(bar.open_at >= start for bar in bars):
            raise _denied("spot_research_original_missing")
        latest = bars[-1]
        if latest.close_at > end or latest.known_at > as_of:
            raise _denied("spot_research_original_future")
        if (as_of - latest.close_at).total_seconds() > self.data.stale_after_seconds:
            raise _denied("spot_research_original_stale")
        if query.window_end_at is None:
            # A live reader may arrive before the next collector commit. The
            # default window ends at the real latest retained closed candle;
            # an explicit requested end is never silently moved.
            end = latest.close_at
            if query.window_start_at is None:
                start = end - timedelta(hours=24)
        if not start < end:
            raise _denied("spot_research_window_invalid")
        bars = tuple(bar for bar in bars if bar.open_at >= start - timedelta(minutes=1))
        feature = build_market_features(
            scope,
            bars,
            as_of,
            stale_after_seconds=self.data.stale_after_seconds,
        )
        path = compute_path_risk(scope, bars, start, end, as_of=as_of)
        fields = set(query.fields) or {"spot_price", "return_30m", "path"}
        values: dict[str, Decimal | str | None] = {
            "spot_price": latest.close,
            "spot_volume": latest.volume_base,
            "return_30m": feature.return_30m,
            "return_24h": feature.return_24h,
            "realized_vol_30m": feature.realized_vol_30m,
            "path": _json(path.model_dump(mode="json"))
            if path.window_status == "complete"
            else None,
        }
        statuses = {
            field: "verified" if values[field] is not None else "unknown"
            for field in sorted(fields)
        }
        excerpt = _json(
            {
                "schema_version": "okx.spot-ledger-research.v1",
                "scope_id": scope.scope_id,
                "instrument": instrument,
                "instrument_kind": "spot",
                "price_type": "trade",
                "price_basis": "latest_closed_1m_trade_close",
                "requested_fields": sorted(fields),
                "field_statuses": statuses,
                "requested_window": [start.isoformat(), end.isoformat()],
                "latest_close_at": latest.close_at.isoformat(),
                "source_known_at": max(bar.known_at for bar in bars).isoformat(),
                "path_status": path.window_status,
                "observed_bars": path.observed_count,
                "expected_bars": path.expected_count,
                "semantic_hash": path.semantic_hash,
                "manifest_hash": path.manifest_hash,
                "spot_price": decimal_text(latest.close),
                "return_30m": _metric_text(feature.return_30m)
                if feature.return_30m is not None
                else None,
                "funding_rate": "unknown",
                "open_interest": "unknown",
                "crowding": "unknown",
                "bid_ask_spread": "unknown",
                "forecast_qualified": False,
            }
        )
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={instrument}&bar=1m"
        evidence_hash = research_evidence_content_hash(
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id="okx-public",
            source_url=url,
            published_at=None,
            excerpt=excerpt,
            structured_payload_ref=None,
        )
        candidate = EvidenceCandidate(
            evidence_id=research_evidence_instance_id(
                content_hash=evidence_hash,
                research_session_id=query.research_session_id,
            ),
            requirement_id=query.requirement_id,
            kind="market",
            authority="exchange",
            source_id="okx-public",
            source_url=AnyUrl(url),
            published_at=None,
            observed_at=latest.close_at,
            received_at=max(bar.received_at for bar in bars),
            content_hash=evidence_hash,
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
        for field in sorted(fields):
            value = values[field]
            if value is None:
                continue  # Absence is an explicit unknown, never a zero or a derivative fact.
            metrics = {
                "spot_price": ("price", "usdt"),
                "spot_volume": ("volume", instrument.split("-")[0].lower()),
                "return_30m": ("return_30m", "fraction"),
                "return_24h": ("return_24h", "fraction"),
                "realized_vol_30m": ("realized_vol_30m", "fraction"),
                "path": ("path", "path_manifest"),
            }
            fact_field, unit = metrics[field]
            window_start = (
                latest.open_at
                if field in {"spot_price", "spot_volume"}
                else latest.close_at - timedelta(minutes=30)
                if field in {"return_30m", "realized_vol_30m"}
                else latest.close_at - timedelta(hours=24)
                if field == "return_24h"
                else start
            )
            attributes: dict[str, float | str | bool | None] = {
                "provider_field": field,
                "instrument_kind": "spot",
                "scope_id": scope.scope_id,
                "price_type": "trade",
                "price_basis": "latest_closed_1m_trade_close",
                "source_known_at": max(bar.known_at for bar in bars).isoformat(),
                "source_received_at": max(bar.received_at for bar in bars).isoformat(),
                "path_manifest_hash": path.manifest_hash,
                "path_semantic_hash": path.semantic_hash,
                "latest_capture_ref": latest.capture_ref,
                "latest_capture_hash": latest.capture_hash,
                "window_status": path.window_status,
                "derivatives_available": False,
                "crowding_available": False,
                "forecast_qualified": False,
            }
            common: _FactPayload = {
                "requirement_id": query.requirement_id,
                "metric_family": "crypto.spot",
                "field": fact_field,
                "instrument": instrument,
                "venue": "okx",
                "value": _metric_text(value) if isinstance(value, Decimal) else value,
                "unit": unit,
                "window_start_at": window_start,
                "window_end_at": latest.close_at,
                "event_offset": None,
                "published_at": None,
                "source_id": "okx-public",
                "independence_group": "okx",
                "delay_class": "realtime",
                "payload_schema_ref": "okx.spot-ledger-fact.v1",
                "attributes": attributes,
            }
            digest = research_fact_payload_hash(**common)
            facts.append(
                FactEnvelope(
                    schema_version="fact-envelope.v1",
                    fact_id=research_fact_instance_id(
                        evidence_id=candidate.evidence_id, payload_hash=digest
                    ),
                    evidence_id=candidate.evidence_id,
                    observed_at=latest.close_at,
                    received_at=max(bar.received_at for bar in bars),
                    quality="candidate",
                    payload_hash=digest,
                    **common,
                )
            )
        finished = aware_utc(self.clock())
        if finished > deadline or (query.mode == "live" and finished > cutoff):
            raise _denied("spot_research_deadline_exceeded")
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=CAPABILITY,
            provider=PROVIDER,
            evidence_candidates=[candidate],
            facts=facts,
            cost_usd=0.0,
            completed_at=finished,
        )

    def route_material(
        self, instrument: str, manifest: ResearchCapabilityManifest
    ) -> _SpotRouteMaterial:
        """Material for trusted server registration; does not enable a capability."""
        if (
            instrument not in INSTRUMENTS
            or manifest.capability_id != CAPABILITY
            or manifest.implementation_ref != IMPLEMENTATION
            or manifest.kind != "python_adapter"
            or "storage:read" not in manifest.permissions
            or "network:https" in manifest.permissions
            or manifest.audit_status != "approved"
            or manifest.license_status != "approved"
            or set(manifest.allowed_domains) != {"www.okx.com"}
            or not self.read_enabled
            or self.data.scopes.get(spot_scope(instrument).scope_id) != spot_scope(instrument)
            or self.execute.__func__ is not _EXECUTE
            or _EXECUTE.__code__ is not _EXECUTE_CODE
        ):
            raise ValueError("spot_route_registration_invalid")
        root = Path(__file__).resolve().parents[3]
        return {
            "schema_version": "spot-research-route-receipt.v1",
            "capability_id": CAPABILITY,
            "provider_id": PROVIDER,
            "implementation_ref": IMPLEMENTATION,
            "adapter_version": VERSION,
            "asset_mode": "instrument",
            "instrument": instrument,
            "scope_id": spot_scope(instrument).scope_id,
            "supported_fields": sorted(FIELDS),
            "manifest_hash": canonical_hash(manifest.model_dump(mode="json")),
            "code_files": {
                p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in CODE_FILES
            },
            "network_requests": False,
            "source_domains_are_provenance_only": True,
            "product_release_qualified": False,
            "forecast_qualified": False,
        }

    def signed_route_receipt(
        self,
        instrument: str,
        manifest: ResearchCapabilityManifest,
        *,
        signing_key: bytes,
    ) -> _SignedRouteReceipt:
        """Only composition supplies this key. It is never a query/model field."""
        if len(signing_key) < 32:
            raise ValueError("spot_route_signing_key_invalid")
        body = self.route_material(instrument, manifest)
        return {
            "body": body,
            "signature": hmac.new(signing_key, _json(body).encode(), hashlib.sha256).hexdigest(),
        }

    def verify_admission_route(
        self,
        route: SpotCapabilityRoute,
        effective: EffectiveSpotRecipe,
        *,
        read_resource: Callable[[str], bytes],
        signing_key: bytes,
    ) -> bool:
        """An MR15 verifier for this exact registered route; macro routes use theirs."""
        try:
            manifest_bytes = read_resource(route.manifest.ref)
            route_bytes = read_resource(route.route.ref)
            if (
                hashlib.sha256(manifest_bytes).hexdigest() != route.manifest.sha256
                or hashlib.sha256(route_bytes).hexdigest() != route.route.sha256
            ):
                return False
            manifest = ResearchCapabilityManifest.model_validate_json(manifest_bytes)
            receipt = json.loads(route_bytes)
            expected = self.signed_route_receipt(
                effective.instrument, manifest, signing_key=signing_key
            )
            return (
                route.capability_id == CAPABILITY
                and route.asset_mode == "instrument"
                and route.instrument == effective.instrument
                and receipt["body"] == expected["body"]
                and hmac.compare_digest(receipt["signature"], expected["signature"])
            )
        except (ValueError, TypeError, KeyError, OSError):
            return False


_EXECUTE = SpotMarketFactsAdapter.execute
_EXECUTE_CODE = _EXECUTE.__code__
