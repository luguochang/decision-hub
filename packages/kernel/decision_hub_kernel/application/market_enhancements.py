"""Three source-bound fixed feature routes, without fitted probabilities.

The archived input reader proves retained bytes, not provider rights or a
qualified dataset. Advanced fitting, F5, cost and promotion remain closed.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ..ports.market_extension import ExtensionOriginals, InputRouteManifest
from ..ports.market_path import FrozenContract, aware_utc, canonical_hash, spot_scope
from .market_extension_registry import ROUTE_FIELDS, input_route_manifest

Route = Literal["MACRO_STATE_V1", "CROWDING_V1", "EVENT_SURPRISE_V1"]
FORMULA_VERSION = "market-enhancement-fixed-features.v1"
PARAMETERS = {
    "horizon_seconds": 86400,
    "macro_changes": [1, 5],
    "macro_order": "distinct_observation_periods_latest_known_vintage_at_cutoff",
    "crowding_oi": "current/prior-1_same_venue_contract_unit",
    "crowding_funding": "fraction_per_hour=rate/(period_seconds/3600)",
    "crowding_basis": "derivative_price/spot_price-1_same_price_basis",
    "crowding_cross_venue": "max_minus_min_exact_observed_time_and_contract_kind",
    "surprise": "actual-consensus_same_event_period_unit_consensus_known_before_public_lower",
    "prior_change": "actual-prior_not_consensus",
    "min_independent_day_blocks": 100,
    "min_distinct_event_blocks": 30,
    "fitted_model": None,
    "probability": None,
    "promotion": False,
}


class SourcePoint(FrozenContract):
    original_ref: str = Field(min_length=1)
    original_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_id: str = Field(min_length=1)
    observed_at: AwareDatetime
    received_at: AwareDatetime
    known_at: AwareDatetime
    public_at: AwareDatetime | None = None
    public_precision: Literal["instant", "day", "unknown"] = "unknown"

    @model_validator(mode="after")
    def ordered(self):
        if not self.observed_at <= self.received_at <= self.known_at:
            raise ValueError("enhancement_source_time_invalid")
        if self.public_at is not None and self.public_at > self.received_at:
            raise ValueError("enhancement_public_time_invalid")
        if self.public_precision == "instant" and self.public_at is None:
            raise ValueError("enhancement_public_time_missing")
        return self


class MacroRelease(FrozenContract):
    series_id: str = Field(min_length=1)
    observation_date: date
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    vintage_at: AwareDatetime | None = None
    source: SourcePoint


class MacroStateInput(FrozenContract):
    schema_version: Literal["macro-state-input.v1"] = "macro-state-input.v1"
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    releases: tuple[MacroRelease, ...] = Field(max_length=128)


class CrowdingPoint(FrozenContract):
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    venue: str = Field(min_length=1)
    contract: str = Field(min_length=1)
    contract_kind: Literal["perpetual", "future"]
    expires_at: AwareDatetime | None = None
    open_interest: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    open_interest_unit: Literal["contracts", "base_coin", "quote_currency"] | None = None
    funding_rate: float | None = Field(default=None, allow_inf_nan=False)
    funding_period_seconds: int | None = Field(default=None, gt=0)
    derivative_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    spot_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    price_basis: Literal["mark", "trade"]
    source: SourcePoint


class CrowdingPair(FrozenContract):
    current: CrowdingPoint
    prior: CrowdingPoint | None = None


class CrowdingInput(FrozenContract):
    schema_version: Literal["crowding-input.v1"] = "crowding-input.v1"
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    venues: tuple[CrowdingPair, ...] = Field(max_length=16)


class EventValue(FrozenContract):
    event_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    source: SourcePoint


class EventSurpriseInput(FrozenContract):
    schema_version: Literal["event-surprise-input.v1"] = "event-surprise-input.v1"
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    actual: EventValue | None = None
    consensus: EventValue | None = None
    prior: EventValue | None = None
    # An uncertain release interval cannot pretend to be a precise event clock.
    public_lower_at: AwareDatetime | None = None
    public_upper_at: AwareDatetime | None = None
    before_feature_ref: str | None = None
    after_feature_ref: str | None = None

    @model_validator(mode="after")
    def public_interval(self):
        if (self.public_lower_at is None) != (self.public_upper_at is None):
            raise ValueError("event_public_interval_incomplete")
        if self.public_lower_at is not None and self.public_lower_at > self.public_upper_at:
            raise ValueError("event_public_interval_invalid")
        return self


class FeatureValue(FrozenContract):
    name: str
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str
    status: Literal["available", "unknown"]
    reason_code: str | None = None


class EnhancementReceipt(FrozenContract):
    schema_version: Literal["market-enhancement-features.v1"] = "market-enhancement-features.v1"
    route: Route
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    cutoff_at: AwareDatetime
    formula_version: Literal["market-enhancement-fixed-features.v1"] = FORMULA_VERSION
    formula_hash: str
    input_hash: str
    status: Literal["available", "partial", "unavailable"]
    features: tuple[FeatureValue, ...]
    original_refs: tuple[str, ...]
    original_hashes: tuple[str, ...]
    data_pit_verified: bool
    missing_reasons: tuple[str, ...]
    # These are deterministic features, not a forecast or F5 decision.
    probability: None = None
    probability_qualified: Literal[False] = False
    promotion: Literal[False] = False

    @property
    def payload_hash(self):
        return canonical_hash(self.model_dump(mode="json"))


def formula_manifest():
    return {
        "version": FORMULA_VERSION,
        "parameters": json.loads(json.dumps(PARAMETERS)),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def _available(point: SourcePoint, cutoff):
    return point.known_at <= cutoff


def _public(point: SourcePoint, cutoff):
    return (
        point.public_precision == "instant"
        and point.public_at is not None
        and point.public_at <= point.received_at <= point.known_at <= cutoff
    )


def _feature(name, value, unit, reason=None):
    return FeatureValue(
        name=name,
        value=value,
        unit=unit,
        status="available" if value is not None else "unknown",
        reason_code=reason,
    )


def _receipt(route, data, cutoff, features, points, reasons, pit):
    proofs = sorted({(p.original_ref, p.original_hash) for p in points})
    all_reasons = tuple(dict.fromkeys(reasons + [f.reason_code for f in features if f.reason_code]))
    available = sum(f.status == "available" for f in features)
    return EnhancementReceipt(
        route=route,
        instrument=data.instrument,
        cutoff_at=cutoff,
        formula_hash=canonical_hash(formula_manifest()),
        input_hash=canonical_hash(data.model_dump(mode="json")),
        status="unavailable"
        if not available
        else ("available" if available == len(features) else "partial"),
        features=tuple(features),
        original_refs=tuple(x[0] for x in proofs),
        original_hashes=tuple(x[1] for x in proofs),
        data_pit_verified=bool(points) and pit,
        missing_reasons=all_reasons,
    )


def macro_state(data: MacroStateInput, *, cutoff_at: datetime) -> EnhancementReceipt:
    cutoff = aware_utc(cutoff_at)
    groups = defaultdict(list)
    reasons, points, features = [], [], []
    for release in data.releases:
        if (
            _available(release.source, cutoff)
            and release.observation_date <= cutoff.date()
            and (release.vintage_at is None or release.vintage_at <= cutoff)
        ):
            groups[(release.series_id, release.source.source_id, release.unit)].append(release)
        else:
            reasons.append("macro_future_vintage_or_known_excluded")
    pit = True
    for (series, source, unit), rows in sorted(groups.items()):
        # Same-period revisions are not additional releases or training samples.
        dates = defaultdict(list)
        for row in rows:
            dates[row.observation_date].append(row)
        selected = []
        for _, revisions in sorted(dates.items()):
            revisions.sort(key=lambda x: (x.source.known_at, x.vintage_at or x.source.known_at))
            latest = revisions[-1]
            tied = [
                r
                for r in revisions
                if (r.source.known_at, r.vintage_at) == (latest.source.known_at, latest.vintage_at)
            ]
            if any(r != latest for r in tied):
                raise ValueError("macro_same_vintage_conflict")
            selected.append(latest)
        latest = selected[-1]
        prefix = series + ":" + source
        features.append(_feature(prefix + ":level", latest.value, unit))
        for periods in (1, 5):
            value = latest.value - selected[-1 - periods].value if len(selected) > periods else None
            features.append(
                _feature(
                    prefix + f":change_{periods}_release",
                    value,
                    unit,
                    None if value is not None else "macro_release_history_insufficient",
                )
            )
        used = selected[-6:]
        points.extend(r.source for r in used)
        if not all(
            r.vintage_at is not None and r.vintage_at <= cutoff and _public(r.source, cutoff)
            for r in used
        ):
            pit = False
            reasons.append("macro_vintage_or_exact_public_time_unknown_descriptive_only")
    if not groups:
        features.append(_feature("macro:level", None, "unknown", "macro_observations_unavailable"))
    return _receipt("MACRO_STATE_V1", data, cutoff, features, points, reasons, pit)


def crowding(data: CrowdingInput, *, cutoff_at: datetime) -> EnhancementReceipt:
    cutoff = aware_utc(cutoff_at)
    features, points, reasons, comparisons = [], [], [], []
    seen = set()
    for pair in data.venues:
        current, prior = pair.current, pair.prior
        key = (current.venue, current.contract, current.price_basis)
        if key in seen:
            raise ValueError("crowding_venue_contract_duplicate")
        seen.add(key)
        if current.instrument != data.instrument:
            raise ValueError("crowding_instrument_mismatch")
        prefix = ":".join(key)
        current_ok = _available(current.source, cutoff) and (
            current.contract_kind != "future"
            or (current.expires_at is not None and current.expires_at > cutoff)
        )
        prior_ok = (
            prior is not None
            and current_ok
            and _available(prior.source, cutoff)
            and (
                prior.instrument,
                prior.venue,
                prior.contract,
                prior.contract_kind,
                prior.expires_at,
                prior.open_interest_unit,
                prior.price_basis,
            )
            == (
                current.instrument,
                current.venue,
                current.contract,
                current.contract_kind,
                current.expires_at,
                current.open_interest_unit,
                current.price_basis,
            )
            and prior.source.observed_at < current.source.observed_at
        )
        oi = (
            (current.open_interest / prior.open_interest - 1)
            if prior_ok
            and current.open_interest is not None
            and prior.open_interest not in {None, 0}
            and current.open_interest_unit is not None
            else None
        )
        funding = (
            current.funding_rate / (current.funding_period_seconds / 3600)
            if current_ok
            and current.funding_rate is not None
            and current.funding_period_seconds is not None
            else None
        )
        basis = (
            current.derivative_price / current.spot_price - 1
            if current_ok
            and current.derivative_price is not None
            and current.spot_price is not None
            else None
        )
        features.extend(
            [
                _feature(
                    prefix + ":oi_change",
                    oi,
                    "fraction",
                    None if oi is not None else "crowding_same_contract_oi_unavailable",
                ),
                _feature(
                    prefix + ":funding_per_hour",
                    funding,
                    "fraction/hour",
                    None if funding is not None else "crowding_funding_period_unavailable",
                ),
                _feature(
                    prefix + ":basis",
                    basis,
                    "fraction",
                    None if basis is not None else "crowding_basis_unavailable",
                ),
            ]
        )
        if current_ok:
            points.append(current.source)
            if prior_ok:
                points.append(prior.source)
        comparisons.append((current, funding, basis, current_ok))
    aligned = (
        len({c.venue for c, *_ in comparisons}) >= 2
        and all(ok for _, _, _, ok in comparisons)
        and len(
            {
                (c.contract_kind, c.expires_at, c.price_basis, c.source.observed_at)
                for c, *_ in comparisons
            }
        )
        == 1
    )
    for field, index, unit in (
        ("funding_dispersion", 1, "fraction/hour"),
        ("basis_dispersion", 2, "fraction"),
    ):
        values = [row[index] for row in comparisons]
        value = (
            max(values) - min(values) if aligned and all(x is not None for x in values) else None
        )
        features.append(
            _feature(
                field,
                value,
                unit,
                None if value is not None else "crowding_multi_venue_or_alignment_unknown",
            )
        )
    return _receipt(
        "CROWDING_V1",
        data,
        cutoff,
        features,
        points,
        reasons,
        bool(points) and all(_public(p, cutoff) for p in points),
    )


def event_surprise(data: EventSurpriseInput, *, cutoff_at: datetime) -> EnhancementReceipt:
    cutoff = aware_utc(cutoff_at)
    actual = data.actual
    points, reasons = [], []
    actual_ok = (
        actual is not None and _available(actual.source, cutoff) and _public(actual.source, cutoff)
    )
    lower = data.public_lower_at or (actual.source.public_at if actual is not None else None)
    if data.public_upper_at is not None and (
        data.public_upper_at > cutoff
        or actual is None
        or actual.source.public_at is None
        or not data.public_lower_at <= actual.source.public_at <= data.public_upper_at
    ):
        actual_ok = False
    if actual_ok:
        points.append(actual.source)
    features = []
    for name, baseline in (
        ("actual_minus_consensus", data.consensus),
        ("actual_minus_prior", data.prior),
    ):
        matched = (
            actual_ok
            and baseline is not None
            and lower is not None
            and (actual.event_id, actual.period, actual.unit)
            == (baseline.event_id, baseline.period, baseline.unit)
            and baseline.source.known_at < lower
            and _available(baseline.source, cutoff)
        )
        value = actual.value - baseline.value if matched else None
        if matched:
            points.append(baseline.source)
        features.append(
            _feature(
                name,
                value,
                actual.unit if actual is not None else "unknown",
                None
                if matched
                else (
                    "event_consensus_missing_or_not_prepublic"
                    if name.endswith("consensus")
                    else "event_prior_missing_or_scope_mismatch"
                ),
            )
        )
    if not data.before_feature_ref or not data.after_feature_ref:
        reasons.append("event_pre_post_feature_snapshots_unavailable")
    return _receipt("EVENT_SURPRISE_V1", data, cutoff, features, points, reasons, actual_ok)


def macro_release_from_fact(
    payload: dict, *, original_ref: str, original_hash: str, known_at: datetime
) -> MacroRelease:
    """Convert a caller-authenticated, exact saved FRED FactVersion payload.

    Root's source reader must first validate its Fact/Evidence/raw lineage. This
    codec checks the full provided payload hash and keeps its real Kernel known
    clock. It never converts the observation date into a publication instant.
    """
    if (
        canonical_hash(payload) != original_hash
        or payload.get("quality") != "accepted"
        or payload.get("source_id") != "fred-public"
        or payload.get("payload_schema_ref") != "fred.series-observation.v2"
        or payload.get("field") != "level"
    ):
        raise ValueError("enhancement_fred_fact_original_invalid")
    attrs = payload["attributes"]
    if attrs.get("time_schema") != "fred.observation-time.v2":
        raise ValueError("enhancement_fred_time_contract_unavailable")
    published = payload.get("published_at")
    return MacroRelease.model_validate(
        {
            "series_id": attrs["series"],
            "observation_date": attrs["observation_date"],
            "value": payload["value"],
            "unit": payload["unit"],
            "vintage_at": attrs.get("revision_at"),
            "source": {
                "original_ref": original_ref,
                "original_hash": original_hash,
                "source_id": payload["source_id"],
                "observed_at": payload["observed_at"],
                "received_at": payload["received_at"],
                "known_at": aware_utc(known_at),
                "public_at": published,
                "public_precision": attrs.get("public_time_precision", "unknown"),
            },
        }
    )


INPUT_TYPES = {
    "MACRO_STATE_V1": MacroStateInput,
    "CROWDING_V1": CrowdingInput,
    "EVENT_SURPRISE_V1": EventSurpriseInput,
}
CALCULATORS = {
    "MACRO_STATE_V1": macro_state,
    "CROWDING_V1": crowding,
    "EVENT_SURPRISE_V1": event_surprise,
}


def component_feature_callback(payload: dict, parameters: dict) -> dict:
    """Pure whitelist callback for the real feature consumer, with fixed scope."""
    route = parameters["route"]
    if route not in INPUT_TYPES or set(parameters) != {"route", "instrument"}:
        raise ValueError("enhancement_component_parameters_invalid")
    if set(payload) != {"input", "cutoff_at"}:
        raise ValueError("enhancement_component_input_invalid")
    data = INPUT_TYPES[route].model_validate(payload["input"])
    if data.instrument != parameters["instrument"]:
        raise ValueError("enhancement_component_scope_mismatch")
    cutoff = datetime.fromisoformat(payload["cutoff_at"].replace("Z", "+00:00"))
    return CALCULATORS[route](data, cutoff_at=cutoff).model_dump(mode="json")


class ArchivedEnhancementInputs:
    """Read exact retained input bytes. No fetching, latest selection or rights claim."""

    def __init__(self, archive):
        self.archive = archive

    def read(self, route: Route, reference: str, expected_hash: str):
        body = self.archive.read(reference)
        if hashlib.sha256(body).hexdigest() != expected_hash:
            raise ValueError("enhancement_input_original_changed")
        return INPUT_TYPES[route].model_validate_json(body)


@dataclass(frozen=True)
class EnhancementBinding:
    route: Route
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    input_ref: str
    input_hash: str
    cutoff_at: datetime


class MarketEnhancements:
    def __init__(self, reader: ArchivedEnhancementInputs):
        self.reader = reader

    @staticmethod
    def manifests(instrument: str) -> tuple[InputRouteManifest, ...]:
        scope = spot_scope(instrument)
        return tuple(
            input_route_manifest(
                route,
                scope_id=scope.scope_id,
                instrument=instrument,
                protocol_hash=canonical_hash(formula_manifest()),
                matching_version=FORMULA_VERSION,
            )
            for route in INPUT_TYPES
        )

    def register_candidates(self, registry, instrument: str) -> tuple[str, ...]:
        return tuple(
            registry.register("enhancement:" + manifest.payload_hash, manifest)
            for manifest in self.manifests(instrument)
        )

    def evaluate(self, binding: EnhancementBinding) -> EnhancementReceipt:
        if binding.route not in INPUT_TYPES:
            raise ValueError("enhancement_route_unknown")
        data = self.reader.read(binding.route, binding.input_ref, binding.input_hash)
        if data.instrument != binding.instrument:
            raise ValueError("enhancement_input_instrument_mismatch")
        return CALCULATORS[binding.route](data, cutoff_at=binding.cutoff_at)


class EnhancementOriginalReader:
    """Extension registry adapter for explicitly frozen exact inputs.

    Retained bytes and valid data clocks do not prove rights, mature labels,
    cost or F5 power. This reader deliberately cannot approve an advanced model.
    """

    def __init__(self, service: MarketEnhancements, bindings: tuple[EnhancementBinding, ...]):
        self.service = service
        self.bindings = {(b.route, spot_scope(b.instrument).scope_id): b for b in bindings}
        if len(self.bindings) != len(bindings):
            raise ValueError("enhancement_frozen_binding_duplicate")

    def verify(self, manifest: InputRouteManifest) -> ExtensionOriginals:
        binding = self.bindings.get((manifest.route, manifest.scope_id))
        expected = next(
            (m for m in self.service.manifests(manifest.instrument) if m.route == manifest.route),
            None,
        )
        if manifest != expected or binding is None:
            raise ValueError("enhancement_manifest_or_frozen_input_mismatch")
        result = self.service.evaluate(binding)
        reasons = tuple(
            dict.fromkeys(
                result.missing_reasons
                + (
                    "enhancement_source_rights_not_attested",
                    "enhancement_mature_labels_unavailable",
                    "f5_independent_sample_qualification_missing",
                    "enhancement_cost_unverified",
                )
            )
        )
        return ExtensionOriginals(
            manifest_hash=manifest.payload_hash,
            scope_id=manifest.scope_id,
            instrument=manifest.instrument,
            known_at=aware_utc(binding.cutoff_at),
            original_refs=(binding.input_ref,) + result.original_refs,
            original_hashes=(binding.input_hash,) + result.original_hashes,
            fields=ROUTE_FIELDS[manifest.route] if result.status == "available" else (),
            quality_verified=result.status == "available",
            public_time_verified=result.data_pit_verified,
            reasons=reasons,
        )
