"""Explicit research admission; runtime health and data coverage remain separate checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .research_run_scope import FrozenRunSpotAdmission
    from .spot_scope_admission import SpotTrialAdmission

from packages.contracts_py.decision_hub_contracts.generated.research_market import (
    ResearchMarketCatalog,
    ResearchMarketScope,
)

DEFAULT_RESEARCH_MARKET = "crypto"
DEFAULT_RESEARCH_INSTRUMENT = "BTC-USDT-SWAP"


class ResearchScopeError(ValueError):
    def __init__(self, code: str):
        self.error_code = code
        super().__init__(code)


def research_market_catalog() -> ResearchMarketCatalog:
    # Return fresh DTOs: a caller cannot mutate the process-wide admission policy.
    return ResearchMarketCatalog(
        registry_version="research-market-registry.v2",
        items=[
            ResearchMarketScope(
                scope_id="btc-macro.v1",
                label="比特币宏观研究",
                market="crypto",
                instrument="BTC-USDT-SWAP",
                instrument_kind="perpetual_swap",
                venue="multi_venue_research",
                quote_currency="USDT",
                price_unit="USDT/BTC",
                source_timezone="UTC",
                calendar_ref="crypto-24x7.v1",
                domain_pack_ref="crypto_macro.v1",
                research_enabled=True,
                outcome_enabled=True,
                supported_horizons=["30m", "24h", "72h"],
                blocking_reasons=[],
            ),
            ResearchMarketScope(
                scope_id="gold-macro.v1",
                label="黄金宏观研究（待开通）",
                market="metal",
                instrument="XAU-USD",
                instrument_kind="spot",
                venue="provider_not_selected",
                quote_currency="USD",
                price_unit="USD/troy_ounce",
                source_timezone="UTC",
                calendar_ref="provider_calendar_not_verified",
                domain_pack_ref="gold_macro.v1",
                research_enabled=False,
                outcome_enabled=False,
                supported_horizons=[],
                blocking_reasons=[
                    "gold_provider_not_verified",
                    "gold_calendar_not_verified",
                    "gold_data_rights_not_verified",
                    "gold_outcome_not_verified",
                    "gold_domain_pack_not_implemented",
                ],
            ),
            *[
                ResearchMarketScope(
                    scope_id=f"{asset.lower()}-spot-macro.v1",
                    label=f"{label}现货研究（待验证）",
                    market="crypto",
                    instrument=f"{asset}-USDT",
                    instrument_kind="spot",
                    venue="okx",
                    quote_currency="USDT",
                    price_unit=f"USDT/{asset}",
                    source_timezone="UTC",
                    calendar_ref="crypto-24x7.v1",
                    domain_pack_ref="crypto_macro.v1",
                    research_enabled=False,
                    outcome_enabled=False,
                    supported_horizons=["24h", "30m", "72h"],
                    blocking_reasons=[
                        "spot_research_disabled",
                        "spot_effective_recipe_not_verified",
                        "spot_asset_routes_not_verified",
                        "spot_current_data_not_verified",
                    ],
                )
                for asset, label in [("BTC", "比特币"), ("ETH", "以太坊")]
            ],
        ],
    )


def research_scope(market: str, instrument: str) -> ResearchMarketScope | None:
    # No alias inference: BTC spot, perpetuals, gold ETFs and futures are different instruments.
    return next(
        (
            item
            for item in research_market_catalog().items
            if (item.market, item.instrument) == (market, instrument)
        ),
        None,
    )


def research_scope_enabled(
    study,
    *,
    spot_admission: SpotTrialAdmission | None = None,
    frozen_spot_admission: FrozenRunSpotAdmission | None = None,
) -> bool:
    scope = research_scope(study.market, study.instrument)
    return bool(
        study.identity_basis != "legacy_unknown"
        and scope
        and (
            scope.research_enabled
            or (spot_admission and spot_admission.matches(study))
            or (frozen_spot_admission and frozen_spot_admission.matches(study))
        )
    )


def require_research_scope(
    study,
    *,
    domain_pack_ref: str | None = None,
    spot_admission: SpotTrialAdmission | None = None,
    frozen_spot_admission: FrozenRunSpotAdmission | None = None,
) -> ResearchMarketScope:
    # Trial admission is resolved against current durable evidence by the server helper.
    # Existing callers without that explicit context continue to reject both spot scopes.
    if not research_scope_enabled(
        study, spot_admission=spot_admission, frozen_spot_admission=frozen_spot_admission
    ):
        raise ResearchScopeError("study_scope_not_enabled")
    scope = research_scope(study.market, study.instrument)
    assert scope is not None
    if domain_pack_ref is not None and scope.domain_pack_ref != domain_pack_ref:
        raise ResearchScopeError("research_scope_pack_mismatch")
    horizons = {item.strip() for item in study.horizon_scope.split(",") if item.strip()}
    if not horizons or not horizons.issubset(scope.supported_horizons):
        raise ResearchScopeError("research_scope_horizon_not_enabled")
    return scope


def outcome_scope_enabled(market: str, instrument: str) -> bool:
    # Preserve the existing daily spot outcome. This is separate from research release.
    if (market, instrument) == ("crypto", "BTC-USDT"):
        return True
    scope = research_scope(market, instrument)
    if scope:
        return scope.outcome_enabled
    return False
