from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import yaml

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    EvidenceRequirement,
    FactEnvelope,
)
from packages.kernel.decision_hub_kernel.decision.sufficiency import (
    assess_evidence_sufficiency,
)


@dataclass(frozen=True)
class FactRequirement:
    """Pack-owned bridge from product language to canonical evidence IDs."""

    requirement_id: str
    canonical_requirement_id: str
    description: str
    importance: str
    minimum_fact: bool
    source_priority: tuple[str, ...]
    allowed_fallbacks: tuple[str, ...]
    preferred_capabilities: tuple[str, ...]
    freshness_seconds: int
    minimum_independent_sources: int
    authority_floor: str
    confidence_cap: float
    accepted_metric_families: tuple[str, ...]
    required_metric_families: tuple[str, ...]
    required_fields: tuple[str, ...]
    required_event_offsets: tuple[str, ...]
    unit_policy: str | None
    field_units: dict[str, tuple[str, ...]]
    minimum_venues: int
    venue_required: bool
    minimum_independence_groups: int
    allowed_delay_classes: tuple[str, ...]
    semantic_policy_ref: str | None

    def as_contract_requirement(self) -> EvidenceRequirement:
        return EvidenceRequirement(
            requirement_id=self.canonical_requirement_id,
            description=self.description,
            importance=self.importance,  # type: ignore[arg-type]
            source_priority=list(self.source_priority),
            authority_floor=self.authority_floor,  # type: ignore[arg-type]
            preferred_capabilities=list(self.preferred_capabilities),
            freshness_seconds=self.freshness_seconds,
            minimum_independent_sources=self.minimum_independent_sources,
            allowed_fallbacks=list(self.allowed_fallbacks),
            confidence_cap=self.confidence_cap,
            accepted_metric_families=list(self.accepted_metric_families),
            required_metric_families=list(self.required_metric_families),
            required_fields=list(self.required_fields),
            required_event_offsets=list(self.required_event_offsets),
            unit_policy=self.unit_policy,
            field_units={key: list(value) for key, value in self.field_units.items()},
            minimum_venues=self.minimum_venues,
            venue_required=self.venue_required,
            minimum_independence_groups=self.minimum_independence_groups,
            allowed_delay_classes=cast(
                list[Literal["realtime", "delayed", "historical", "unknown"]],
                list(self.allowed_delay_classes),
            ),
            semantic_policy_ref=self.semantic_policy_ref,
        )


@dataclass(frozen=True)
class FactPackAssessment:
    status: str
    covered_requirement_ids: tuple[str, ...]
    missing_requirement_ids: tuple[str, ...]


class CryptoMacroFactPack:
    """Deterministic six-fact minimum pack for crypto-macro research.

    This class only owns requirement/source policy. It does not fetch the
    network, infer market direction, or relax the Kernel Gate.
    """

    schema_version = "crypto-macro-evidence-policy.v3"

    def __init__(
        self,
        requirements: Iterable[FactRequirement],
        *,
        event_classes: Iterable[str],
        event_requirement_overrides: Mapping[str, Mapping[str, str]],
        instrument: str = "BTC-USDT-SWAP",
    ) -> None:
        if instrument not in {"BTC-USDT-SWAP", "BTC-USDT", "ETH-USDT", "ETH-USDT-SWAP"}:
            raise ValueError("crypto_macro_fact_pack_instrument_not_supported")
        self.instrument = instrument
        self.asset = instrument.split("-")[0]
        values = tuple(requirements)
        ids = [item.requirement_id for item in values]
        canonical_ids = [item.canonical_requirement_id for item in values]
        if len(set(ids)) != len(ids) or len(set(canonical_ids)) != len(canonical_ids):
            raise ValueError("crypto_macro_source_manifest_requirements_invalid")
        if sum(item.minimum_fact for item in values) != 6:
            raise ValueError("crypto_macro_source_manifest_minimum_facts_invalid")
        self.requirements = tuple(item for item in values if item.minimum_fact)
        self._all_requirements = {item.canonical_requirement_id: item for item in values}
        self._all_by_id = {item.requirement_id: item for item in values}
        self._by_id = {item.requirement_id: item for item in self.requirements}
        classes = tuple(event_classes)
        if not classes or len(classes) != len(set(classes)):
            raise ValueError("crypto_macro_source_manifest_event_classes_invalid")
        unknown_families = set(event_requirement_overrides) - set(classes)
        if unknown_families:
            raise ValueError("crypto_macro_source_manifest_event_overrides_invalid")
        normalized_overrides: dict[str, dict[str, Literal["hard", "soft"]]] = {}
        for event_family, overrides in event_requirement_overrides.items():
            normalized: dict[str, Literal["hard", "soft"]] = {}
            for requirement_id, importance in overrides.items():
                if requirement_id not in self._all_requirements or importance not in {
                    "hard",
                    "soft",
                }:
                    raise ValueError("crypto_macro_source_manifest_event_overrides_invalid")
                normalized[requirement_id] = cast(Literal["hard", "soft"], importance)
            if not normalized:
                raise ValueError("crypto_macro_source_manifest_event_overrides_invalid")
            normalized_overrides[event_family] = normalized
        self.event_classes = classes
        self._event_requirement_overrides = normalized_overrides

    @classmethod
    def from_pack(
        cls, pack_root: Path, *, instrument: str = "BTC-USDT-SWAP"
    ) -> CryptoMacroFactPack:
        path = pack_root / "evidence" / "source_manifest.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError("crypto_macro_source_manifest_invalid") from exc
        return cls.from_manifest(raw, instrument=instrument)

    @classmethod
    def from_manifest(
        cls, raw: object, *, instrument: str = "BTC-USDT-SWAP"
    ) -> CryptoMacroFactPack:
        """Parse the exact saved policy, without rereading a changed file."""
        if not isinstance(raw, Mapping) or raw.get("schema_version") != cls.schema_version:
            raise ValueError("crypto_macro_source_manifest_invalid")
        entries = raw.get("requirements")
        event_classes = raw.get("event_classes")
        event_requirement_overrides = raw.get("event_requirement_overrides", {})
        if (
            not isinstance(entries, list)
            or not isinstance(event_classes, list)
            or not isinstance(event_requirement_overrides, Mapping)
        ):
            raise ValueError("crypto_macro_source_manifest_invalid")
        parsed: list[FactRequirement] = []
        for item in entries:
            if not isinstance(item, Mapping):
                raise ValueError("crypto_macro_source_manifest_invalid")
            try:
                parsed.append(
                    FactRequirement(
                        requirement_id=_required_str(item, "requirement_id"),
                        canonical_requirement_id=_required_str(item, "canonical_requirement_id"),
                        description=_required_str(item, "description"),
                        importance=_required_str(item, "importance"),
                        minimum_fact=_required_bool(item, "minimum_fact"),
                        source_priority=_required_tuple(item, "source_priority"),
                        allowed_fallbacks=_required_tuple(
                            item, "allowed_fallbacks", allow_empty=True
                        ),
                        preferred_capabilities=_required_tuple(item, "preferred_capabilities"),
                        freshness_seconds=_required_int(item, "freshness_seconds"),
                        minimum_independent_sources=_required_int(
                            item, "minimum_independent_sources"
                        ),
                        authority_floor=_required_str(item, "authority_floor"),
                        confidence_cap=_required_float(item, "confidence_cap"),
                        accepted_metric_families=_required_tuple(
                            item, "accepted_metric_families", allow_empty=True
                        ),
                        required_metric_families=_required_tuple(
                            item, "required_metric_families", allow_empty=True
                        ),
                        required_fields=_required_tuple(
                            item, "required_fields", allow_empty=True
                        ),
                        required_event_offsets=_required_tuple(
                            item, "required_event_offsets", allow_empty=True
                        ),
                        unit_policy=_optional_str(item, "unit_policy"),
                        field_units=_required_string_tuple_map(item, "field_units"),
                        minimum_venues=_required_int(item, "minimum_venues"),
                        venue_required=_required_bool(item, "venue_required"),
                        minimum_independence_groups=_required_int(
                            item, "minimum_independence_groups"
                        ),
                        allowed_delay_classes=_required_tuple(
                            item, "allowed_delay_classes", allow_empty=True
                        ),
                        semantic_policy_ref=_optional_str(item, "semantic_policy_ref"),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("crypto_macro_source_manifest_invalid") from exc
        parsed_overrides: dict[str, dict[str, str]] = {}
        try:
            for event_family, overrides in event_requirement_overrides.items():
                if not isinstance(event_family, str) or not event_family.strip():
                    raise ValueError("event_family")
                if not isinstance(overrides, Mapping):
                    raise ValueError("event_requirement_overrides")
                parsed_overrides[event_family.strip()] = {
                    _required_mapping_key(requirement_id): _required_importance(importance)
                    for requirement_id, importance in overrides.items()
                }
            parsed_event_classes = _string_tuple(event_classes, "event_classes")
            return cls(
                parsed,
                event_classes=parsed_event_classes,
                event_requirement_overrides=parsed_overrides,
                instrument=instrument,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("crypto_macro_source_manifest_invalid") from exc

    def requirement(self, requirement_id: str) -> FactRequirement:
        try:
            return self._by_id[requirement_id]
        except KeyError as exc:
            raise KeyError(requirement_id) from exc

    def capability_ladder(self, requirement_id: str) -> tuple[str, ...]:
        return self.requirement(requirement_id).preferred_capabilities

    def contract_requirements(self) -> tuple[EvidenceRequirement, ...]:
        return tuple(item.as_contract_requirement() for item in self.requirements)

    def all_contract_requirements(self) -> tuple[EvidenceRequirement, ...]:
        """Return the complete policy from the single Pack-owned manifest."""

        return tuple(item.as_contract_requirement() for item in self._all_requirements.values())

    def contract_requirements_for_event(
        self, event_family: str | None
    ) -> tuple[EvidenceRequirement, ...]:
        """Apply Pack-owned importance rules for a known event family.

        Unknown or absent families retain the conservative default policy. The
        override changes only hard/soft importance; source and semantic gates
        remain identical.
        """

        policy_family = (
            "crypto_market_snapshot"
            if _is_crypto_market_snapshot_family(event_family)
            else event_family or ""
        )
        overrides = self._event_requirement_overrides.get(policy_family, {})
        projected: list[EvidenceRequirement] = []
        for item in self._all_requirements.values():
            if (item.canonical_requirement_id in {
                "usd_reserve_balances", "real_yield_10y", "inflation_compensation_10y",
            } and policy_family != "crypto_market_snapshot"):
                # Fixed current-decision slice; do not invent event-time or vintage obligations.
                continue
            if (
                item.canonical_requirement_id == "market_narrative"
                and policy_family != "crypto_market_snapshot"
            ):
                # The fixed narrative lookup belongs to the latest BTC advice
                # entry. Other event families already have their own official
                # discovery plan and must not inherit this extra web route.
                continue
            if policy_family == "crypto_market_snapshot" and item.canonical_requirement_id in {
                "event_identity",
                "policy_or_data_delta",
                "expectation_pricing",
                "counter_thesis",
            }:
                # A point-in-time market question has no external policy event to
                # identify or compare. Counter-thesis is a synthesis obligation,
                # not a source acquisition target. Keeping these items in the
                # evidence plan produced irrelevant gaps and wasted tool calls.
                continue
            selected = replace(
                item,
                importance=overrides.get(item.canonical_requirement_id, item.importance),
            )
            if policy_family == "crypto_market_snapshot":
                if item.canonical_requirement_id == "crypto_spot_confirmation":
                    selected = replace(
                        selected,
                        description=(
                            f"Observe the current {self.asset} spot price and volume snapshot "
                            "from at least two independent exchange venues."
                        ),
                        required_fields=("price", "volume"),
                        required_event_offsets=(),
                        minimum_independent_sources=2,
                        minimum_venues=2,
                        minimum_independence_groups=2,
                    )
                elif item.canonical_requirement_id == "macro_transmission":
                    # A current daily-direction request has no event-relative
                    # t-5m/t+1m anchor.  Require a completed-daily rates/USD
                    # snapshot and five-session change instead.  This remains
                    # soft and research-level; an aggregator cannot authorize
                    # execution or stand in for an official policy release.
                    selected = replace(
                        selected,
                        description=(
                            "Observe completed-daily US 10Y yield and USD-index "
                            "levels plus their five-session changes."
                        ),
                        freshness_seconds=259200,
                        minimum_independent_sources=1,
                        authority_floor="audited_aggregator",
                        required_fields=("level", "change_5d"),
                        required_event_offsets=(),
                        field_units={
                            "level": ("yield_percent", "index"),
                            "change_5d": ("bps", "percent"),
                        },
                        minimum_independence_groups=2,
                        allowed_delay_classes=("realtime", "delayed"),
                    )
                elif item.canonical_requirement_id == "cross_asset_confirmation":
                    selected = replace(
                        selected,
                        description=(
                            "Observe completed-daily equity and volatility levels "
                            "plus their five-session returns."
                        ),
                        freshness_seconds=259200,
                        minimum_independent_sources=1,
                        authority_floor="audited_aggregator",
                        required_metric_families=(
                            "cross_asset.equity",
                            "cross_asset.volatility",
                        ),
                        required_fields=("level", "return_5d"),
                        required_event_offsets=(),
                        field_units={
                            "level": ("price", "index", "spread_bps"),
                            "return_5d": ("percent",),
                        },
                        minimum_independence_groups=2,
                        allowed_delay_classes=("realtime", "delayed"),
                    )
                elif item.canonical_requirement_id == "derivatives_crowding":
                    selected = replace(
                        selected,
                        description=(
                            "Observe current funding, open interest and its change, "
                            "basis and crowding across at least two independent "
                            "exchange venues."
                        ),
                        required_fields=(
                            "funding_rate",
                            "open_interest",
                            "open_interest_delta",
                            "basis",
                            "crowding_signal",
                        ),
                        required_event_offsets=(),
                        minimum_independent_sources=2,
                        minimum_venues=2,
                        minimum_independence_groups=2,
                    )
            if self.asset == "ETH":
                selected = replace(
                    selected,
                    description=selected.description.replace("BTC", "ETH"),
                    field_units={
                        key: tuple("eth" if unit == "btc" else unit for unit in units)
                        for key, units in selected.field_units.items()
                    },
                )
            projected.append(selected.as_contract_requirement())
        return tuple(projected)

    @property
    def requirement_aliases(self) -> dict[str, str]:
        """Return Pack IDs mapped to their canonical cross-module IDs."""

        return {
            item.requirement_id: item.canonical_requirement_id for item in self._all_by_id.values()
        }

    def contract_requirement(self, canonical_requirement_id: str) -> EvidenceRequirement | None:
        item = self._all_requirements.get(canonical_requirement_id)
        return item.as_contract_requirement() if item is not None else None

    @property
    def minimum_requirements(self) -> tuple[FactRequirement, ...]:
        return tuple(item for item in self.requirements if item.minimum_fact)

    def assess(
        self,
        evidence: Iterable[EvidenceCandidate],
        *,
        cutoff_at: datetime,
        facts: Iterable[FactEnvelope] = (),
    ) -> FactPackAssessment:
        values = tuple(evidence)
        typed_facts = tuple(facts)
        requirements = [item.as_contract_requirement() for item in self.minimum_requirements]
        # Candidate records use the canonical requirement IDs from the contract.
        coverage = assess_evidence_sufficiency(
            requirements,
            values,
            cutoff_at=cutoff_at,
            facts=typed_facts,
        )
        covered = tuple(
            item.requirement_id
            for item in self.minimum_requirements
            if item.canonical_requirement_id in coverage.covered_requirement_ids
        )
        missing = tuple(
            item.requirement_id
            for item in self.minimum_requirements
            if item.requirement_id not in covered
        )
        return FactPackAssessment(
            status="sufficient" if not missing else "research_only",
            covered_requirement_ids=covered,
            missing_requirement_ids=missing,
        )


def _is_crypto_market_snapshot_family(event_family: str | None) -> bool:
    if event_family is None:
        return False
    normalized = event_family.strip().lower().replace("-", "_")
    return normalized in {
        "crypto_market_snapshot",
        "btc_market_snapshot",
        "market_snapshot",
    } or normalized.startswith("btc_current_snapshot")


def _required_str(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value.strip()


def _required_int(values: Mapping[str, object], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(key)
    return value


def _required_bool(values: Mapping[str, object], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise ValueError(key)
    return value


def _required_float(values: Mapping[str, object], key: str) -> float:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(key)
    normalized = float(value)
    if not 0 <= normalized <= 1:
        raise ValueError(key)
    return normalized


def _optional_str(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value.strip()


def _required_string_tuple_map(
    values: Mapping[str, object], key: str
) -> dict[str, tuple[str, ...]]:
    raw = values.get(key)
    if not isinstance(raw, Mapping):
        raise ValueError(key)
    result: dict[str, tuple[str, ...]] = {}
    for field, units in raw.items():
        if not isinstance(field, str) or not field.strip() or not isinstance(units, list):
            raise ValueError(key)
        parsed = tuple(item.strip() for item in units if isinstance(item, str) and item.strip())
        if not parsed or len(parsed) != len(units) or len(set(parsed)) != len(parsed):
            raise ValueError(key)
        result[field.strip()] = parsed
    return result


def _required_tuple(
    values: Mapping[str, object], key: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    raw = values.get(key)
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise ValueError(key)
    result = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    if len(result) != len(raw) or len(set(result)) != len(result):
        raise ValueError(key)
    return result


def _string_tuple(raw: object, key: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(key)
    result = tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())
    if len(result) != len(raw) or len(result) != len(set(result)):
        raise ValueError(key)
    return result


def _required_mapping_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event_requirement_overrides")
    return value.strip()


def _required_importance(value: object) -> str:
    if value not in {"hard", "soft"}:
        raise ValueError("event_requirement_overrides")
    return cast(str, value)
