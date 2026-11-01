"""Gold and enhanced input routes are separately admitted; no proxy asset aliases."""

from __future__ import annotations

from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, model_validator

from .market_path import FrozenContract, canonical_hash


class GoldMarketSpec(FrozenContract):
    schema_version: Literal["gold-market-spec.v1"] = "gold-market-spec.v1"
    scope_id: str
    instrument: str
    instrument_kind: Literal["spot", "future"]
    provider_id: str | None = None
    venue: str | None = None
    quote_currency: Literal["USD"] = "USD"
    price_unit: Literal["USD/troy_ounce"] = "USD/troy_ounce"
    price_basis: Literal["trade", "bid", "ask", "mark"]
    calendar_ref: str | None = None
    calendar_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    timezone: str
    interval_seconds: Literal[60] = 60
    label_horizon_seconds: Literal[86400] = 86400
    contract_code: str | None = None
    expires_at: AwareDatetime | None = None
    contract_multiplier_troy_ounces: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def separate_instrument(self) -> GoldMarketSpec:
        ZoneInfo(self.timezone)
        if self.instrument_kind == "spot":
            if self.instrument != "XAU-USD" or any(
                (self.contract_code, self.expires_at, self.contract_multiplier_troy_ounces)
            ):
                raise ValueError("gold_spot_contract_invalid")
        elif (
            self.instrument == "XAU-USD"
            or not self.contract_code
            or self.expires_at is None
            or self.contract_multiplier_troy_ounces is None
        ):
            raise ValueError("gold_future_contract_required")
        expected = (
            f"gold:{self.instrument_kind}:{self.venue or 'unselected'}:"
            f"{self.instrument}:{self.price_basis}:1m"
        )
        if self.scope_id != expected:
            raise ValueError("gold_scope_identity_invalid")
        return self

    @property
    def payload_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class InputRouteManifest(FrozenContract):
    schema_version: Literal["market-input-route.v1"] = "market-input-route.v1"
    route: Literal["MACRO_STATE_V1", "CROWDING_V1", "EVENT_SURPRISE_V1"]
    scope_id: str
    instrument: str
    formula_version: str
    feature_names: tuple[str, ...]
    matching_version: str
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    min_independent_day_blocks: Literal[100] = 100
    min_distinct_event_blocks: Literal[30] = 30
    target_horizon_seconds: Literal[86400] = 86400
    promotion: Literal[False] = False

    @property
    def payload_hash(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))


class ExtensionOriginals(FrozenContract):
    """Actual reader output. Every evidence item must be bound to a retained original."""

    manifest_hash: str
    scope_id: str
    instrument: str
    known_at: AwareDatetime
    original_refs: tuple[str, ...]
    original_hashes: tuple[str, ...]
    fields: tuple[str, ...]
    provider_id: str | None = None
    calendar_hash: str | None = None
    price_unit: str | None = None
    price_basis: str | None = None
    rights_verified: bool = False
    public_time_verified: bool = False
    calendar_verified: bool = False
    mature_labels_verified: bool = False
    quality_verified: bool = False
    statistical_qualified: bool = False
    independent_day_blocks: int = Field(default=0, ge=0)
    distinct_event_blocks: int = Field(default=0, ge=0)
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def originals(self) -> ExtensionOriginals:
        if len(self.original_refs) != len(self.original_hashes):
            raise ValueError("market_extension_original_refs_invalid")
        return self


class ExtensionOriginalReader(Protocol):
    def verify(
        self,
        manifest: GoldMarketSpec | InputRouteManifest,
    ) -> ExtensionOriginals: ...


class ExtensionAdmission(FrozenContract):
    schema_version: Literal["market-extension-admission.v1"] = "market-extension-admission.v1"
    registration_id: str
    manifest_hash: str
    scope_id: str
    registered_at: AwareDatetime
    checked_at: AwareDatetime
    state: Literal["not_admitted", "descriptive_only", "research_qualified"]
    reasons: tuple[str, ...]
    originals: ExtensionOriginals | None
    # Qualified data/research are never an automatic probability or trading promotion.
    probability_calibrated: Literal[False] = False
    trading_authority: Literal[False] = False
