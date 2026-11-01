"""Exact OKX BTC/ETH USDT linear perpetual original decoders.

Pure original decoders. No HTTP, database, clock, stored-hash trust or orders.
They preserve source timestamps and never turn a funding indication into a fill
or a settled cashflow. Scope is two explicit OKX USDT linear SWAP instruments.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Mapping

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1, InstrumentContractV1,
)
from .action_quote import CaptureOriginal, decode_json, exact_price
from .market_path import aware_utc, canonical_hash

VERSION = "okx-perp-public.v1"
Kind = Literal["metadata", "ticker", "mark", "index", "funding"]
IDENTITIES = {
    "BTC-USDT-SWAP": ("BTC", "USDT", "BTC-USDT"),
    "ETH-USDT-SWAP": ("ETH", "USDT", "ETH-USDT"),
}


def public_url(inst_id: str, kind: Kind) -> str:
    if inst_id not in IDENTITIES:
        raise ValueError("perp_explicit_swap_identity_required")
    underlying = IDENTITIES[inst_id][2]
    urls = {
        "metadata": f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
        "ticker": f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
        "mark": f"https://www.okx.com/api/v5/public/mark-price?instType=SWAP&instId={inst_id}",
        "index": f"https://www.okx.com/api/v5/market/index-tickers?instId={underlying}",
        "funding": f"https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}",
    }
    if kind not in urls:
        raise ValueError("perp_public_kind_unsupported")
    return urls[kind]


def _millis(value: Any) -> datetime:
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,15}", value) is None:
        raise ValueError("perp_source_millisecond_timestamp_required")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(value))


def _positive_text(value: Any) -> str:
    exact_price(value)  # existing finite, positive, decimal-string validation
    return value


def _signed_text(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"-?(?:0|[1-9][0-9]{0,31})(?:\.[0-9]{1,18})?", value) is None:
        raise ValueError("perp_decimal_text_required")
    return value


@dataclass(frozen=True)
class PublicOriginal:
    kind: Kind
    inst_id: str
    capture: CaptureOriginal
    observed_at: datetime | None
    fields: Mapping[str, Any]

    @property
    def source_ref(self) -> DecisionSourceRefV1:
        # Bind capture metadata (including clocks/endpoint), not just body SHA.
        # The resolver must also reread the actual archived body and hash it.
        return DecisionSourceRefV1(
            ref=f"raw-capture:{self.capture.capture_id}", hash=canonical_hash(self.capture.payload()),
        )


def decode_public_original(kind: Kind, inst_id: str, capture: CaptureOriginal, raw: bytes) -> PublicOriginal:
    expected_url = public_url(inst_id, kind)
    if capture.url != expected_url or capture.adapter_version != VERSION:
        raise ValueError("perp_capture_endpoint_or_version_mismatch")
    if re.fullmatch(rf"{re.escape(VERSION)}:{re.escape(inst_id)}:{kind}:[a-f0-9]{{32}}", capture.context_id) is None:
        raise ValueError("perp_capture_context_mismatch")
    if capture.status_code != 200 or capture.error_code is not None:
        raise ValueError("perp_capture_not_successful")
    digest = hashlib.sha256(raw).hexdigest()
    if capture.body_hash != digest or capture.body_ref != f"raw://sha256/{digest}" or capture.byte_count != len(raw):
        raise ValueError("perp_capture_raw_hash_mismatch")
    if not aware_utc(capture.started_at) <= aware_utc(capture.completed_at) <= aware_utc(capture.recorded_at):
        raise ValueError("perp_capture_clock_order")
    response = decode_json(raw)  # existing duplicate-JSON-key rejection
    data = response.get("data")
    if response.get("code") != "0" or not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("perp_unique_successful_row_required")
    row = data[0]
    base, quote, underlying = IDENTITIES[inst_id]
    expected_id = underlying if kind == "index" else inst_id
    if row.get("instId") != expected_id:
        raise ValueError("perp_response_instrument_mismatch")
    if kind in {"metadata", "ticker", "mark"} and row.get("instType") != "SWAP":
        raise ValueError("perp_response_swap_required")

    observed = None
    if kind == "metadata":
        if (row.get("ctType") != "linear" or row.get("settleCcy") != quote
                or row.get("instFamily") != underlying or row.get("uly") != underlying
                or row.get("baseCcy") not in {"", base} or row.get("quoteCcy") not in {"", quote}
                or row.get("ctValCcy") not in {base, quote}):
            raise ValueError("perp_metadata_dimension_mismatch")
        if row.get("ctMult") in {None, ""}:
            raise ValueError("perp_ctmult_required_no_default")
        fields = {key: _positive_text(row.get(key)) for key in ("ctVal", "ctMult", "lotSz", "minSz", "tickSz")}
        fields.update(base=base, quote=quote, settle=quote, ct_val_ccy=row["ctValCcy"], raw_state=row.get("state"),
                      listing_time=row.get("listTime"), rule_effective_at=None)
        # baseCcy/quoteCcy are actually blank in the diagnostic response. Base
        # and quote above come from the exact approved SWAP/underlying binding,
        # checked against ctType/settleCcy/ctValCcy; never from a spot fallback.
    elif kind == "ticker":
        bid, ask = _positive_text(row.get("bidPx")), _positive_text(row.get("askPx"))
        if Decimal(bid) > Decimal(ask):
            raise ValueError("perp_crossed_bid_ask")
        observed = _millis(row.get("ts"))
        fields = {"bid": bid, "ask": ask, "last": _positive_text(row.get("last"))}
    elif kind in {"mark", "index"}:
        observed = _millis(row.get("ts"))
        fields = {"price": _positive_text(row.get("markPx" if kind == "mark" else "idxPx"))}
    else:
        observed = _millis(row["ts"]) if row.get("ts") not in {None, ""} else None
        fields = {
            "value_kind": "funding_indication", "indicated_rate": _signed_text(row.get("fundingRate")),
            "scheduled_settlement_at": _millis(row.get("fundingTime")),
            "next_scheduled_at": _millis(row["nextFundingTime"]) if row.get("nextFundingTime") not in {None, ""} else None,
            "settled_rate": None, "cashflow_usdt": None,
        }
        # Future settlement dates are plans known at receipt, not a future
        # observed fact. Even a past fundingTime does not prove settlement or
        # that this account held a position at the actual settlement instant.
    return PublicOriginal(kind, inst_id, capture, observed, MappingProxyType(fields))


@dataclass(frozen=True)
class RuleInputs:
    """Internal inputs returned by a mandatory real rule-original reader.

    A caller-constructed value is not authentication. The application reader
    must resolve this binding's frozen effective-time/schedule/tier originals.
    No new rule database, queue, SDK or current/latest pointer is introduced.
    """
    binding_original: DecisionSourceRefV1 | None = None
    effective_at: datetime | None = None
    known_at: datetime | None = None
    effective_original: DecisionSourceRefV1 | None = None
    funding_schedule_original: DecisionSourceRefV1 | None = None
    margin_tier_original: DecisionSourceRefV1 | None = None


def assemble_contract(
    originals: Mapping[Kind, PublicOriginal], rules: RuleInputs, *, cutoff: datetime,
) -> InstrumentContractV1:
    if "metadata" not in originals or not set(originals) <= {"metadata", "ticker", "mark", "index", "funding"}:
        raise ValueError("perp_metadata_original_required")
    metadata = originals["metadata"]
    if any(item.inst_id != metadata.inst_id or item.kind != key for key, item in originals.items()):
        raise ValueError("perp_original_scope_mismatch")
    as_of = aware_utc(cutoff)
    if any(aware_utc(item.capture.recorded_at) > as_of for item in originals.values()):
        raise ValueError("perp_original_not_available_at_cutoff")
    if rules.known_at is not None and aware_utc(rules.known_at) > as_of:
        raise ValueError("perp_rule_binding_not_known_at_cutoff")
    if (rules.effective_at is None) != (rules.effective_original is None):
        raise ValueError("perp_effective_time_original_pair")
    if rules.binding_original is None and any(value is not None for value in (
        rules.effective_at, rules.known_at, rules.effective_original,
        rules.funding_schedule_original, rules.margin_tier_original,
    )):
        raise ValueError("perp_rule_values_require_named_binding")
    if rules.binding_original is not None and rules.known_at is None:
        raise ValueError("perp_rule_binding_known_time_required")
    if any(item.observed_at is not None and item.observed_at > as_of for item in originals.values()):
        raise ValueError("perp_source_clock_after_cutoff")
    fields = metadata.fields
    state = ({"live": "live", "preopen": "preopen", "suspend": "suspended", "expired": "expired"}.get(fields["raw_state"], "unknown")
             if type(fields["raw_state"]) is str else "unknown")
    return InstrumentContractV1.model_validate({
        "schema_version": "instrument-contract.v1", "venue": "okx", "inst_id": metadata.inst_id,
        "instrument_kind": "perpetual", "linear_or_inverse": "linear",
        "base": fields["base"], "quote": fields["quote"], "settle": fields["settle"],
        "ct_val": fields["ctVal"], "ct_mult": fields["ctMult"], "ct_val_ccy": fields["ct_val_ccy"],
        "lot_sz": fields["lotSz"], "min_sz": fields["minSz"], "tick_sz": fields["tickSz"],
        "price_basis_refs": {name: originals[key].source_ref.model_dump(mode="json") if key in originals else None
                             for name, key in (("trade", "ticker"), ("mark", "mark"), ("index", "index"))},
        "rule_source_ref": metadata.source_ref.ref, "rule_source_hash": metadata.source_ref.hash,
        "funding_schedule_ref": rules.funding_schedule_original.model_dump(mode="json") if rules.funding_schedule_original else None,
        "margin_tier_ref": rules.margin_tier_original.model_dump(mode="json") if rules.margin_tier_original else None,
        "effective_at": aware_utc(rules.effective_at) if rules.effective_at is not None else None,
        "effective_at_source": "venue_rule" if rules.effective_original is not None else "unknown",
        # This is the actual metadata response receipt, never listing time or
        # an invented venue rule inception. Each linked source keeps its clock.
        "known_at": aware_utc(metadata.capture.completed_at), "state": state,
    })


def paper_dependency_gaps(
    contract: InstrumentContractV1, *, as_of: datetime, has_fresh_executable_quote: bool,
) -> tuple[str, ...]:
    """Dependency gaps only, never a sufficient execution/ownership approval.

    Unit research may keep this Instrument and use explicit cost assumptions.
    Future funding is not an entry prerequisite. Missing realized settlements
    remain incomplete in the separate settlement ledger, never zero cashflow.
    Unknown rule inception forbids backdating this observed snapshot; it does
    not invalidate its real known_at or current metadata dimensions.
    """
    gaps = []
    if contract.state != "live":
        gaps.append("instrument_not_live")
    if contract.margin_tier_ref is None:
        gaps.append("margin_tier_unknown")
    if not has_fresh_executable_quote:
        gaps.append("fresh_executable_bid_ask_missing")
    if contract.effective_at is not None and contract.effective_at > aware_utc(as_of):
        gaps.append("rules_not_yet_effective")
    if contract.known_at > aware_utc(as_of):
        gaps.append("metadata_not_known_at_requested_cutoff")
    return tuple(gaps)
