"""Versioned server paper policy, authenticated by the existing source guard.

This policy declares a simulation configuration. It is not venue rule timing,
account registration, balances, execution capability or permission to start paper.
"""

from __future__ import annotations

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionExecutionProfileV1,
    DecisionSourceRefV1,
)

from ..ports.market_path import canonical_hash

PROFILE_REF = "decision-profile:okx-usdt-linear-paper-net-isolated-1x.v1"
_INSTRUMENTS = frozenset({"okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"})
# Every element is immutable. No caller-owned DTO or mutable body is registered.
_SERVER_BODY = (
    ("schema_version", "decision-execution-profile.v1"),
    ("profile_ref", PROFILE_REF),
    ("execution", "paper_only"),
    ("position_mode", "net"),
    ("margin_mode", "isolated"),
    ("settle_ccy", "USDT"),
    ("initial_notional_to_margin_ratio", "1"),
)


def _server_profile(instrument_id: str) -> DecisionExecutionProfileV1:
    if instrument_id not in _INSTRUMENTS:
        raise ValueError("decision_execution_profile_instrument_unsupported")
    body = dict(_SERVER_BODY)
    return DecisionExecutionProfileV1.model_validate({**body, "hash": canonical_hash(body)})


def paper_execution_profile_reference(instrument_id: str) -> DecisionSourceRefV1:
    """Return the fixed server policy reference for an approved instrument."""
    profile = _server_profile(instrument_id)
    return DecisionSourceRefV1(ref=profile.profile_ref, hash=profile.hash)


def read_execution_profile(
    instrument_id: str,
    reference: DecisionSourceRefV1,
) -> DecisionExecutionProfileV1:
    """Authenticate only the reference; rebuild a fresh DTO from server bytes."""
    profile = _server_profile(instrument_id)
    reference = DecisionSourceRefV1.model_validate(reference)
    if reference.ref != profile.profile_ref or reference.hash != profile.hash:
        raise ValueError("decision_execution_profile_original_mismatch")
    return profile
