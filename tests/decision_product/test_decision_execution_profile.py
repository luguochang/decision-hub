"""Server policy identity is independent of caller supplied DTO copies."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionSourceRefV1,
)

MODULE = "packages.kernel.decision_hub_kernel.application.decision_execution_profile"
REF = "decision-profile:okx-usdt-linear-paper-net-isolated-1x.v1"
BODY = {
    "schema_version": "decision-execution-profile.v1",
    "profile_ref": REF,
    "execution": "paper_only",
    "position_mode": "net",
    "margin_mode": "isolated",
    "settle_ccy": "USDT",
    "initial_notional_to_margin_ratio": "1",
}


def api():
    assert importlib.util.find_spec(MODULE) is not None, (
        "server execution profile reader is missing"
    )
    return importlib.import_module(MODULE)


def digest(body):
    return hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize("instrument", ["okx:BTC-USDT-SWAP", "okx:ETH-USDT-SWAP"])
def test_server_reference_hash_covers_complete_fixed_policy(instrument):
    profile_api = api()
    reference = profile_api.paper_execution_profile_reference(instrument)
    assert reference == DecisionSourceRefV1(ref=REF, hash=digest(BODY))
    profile = profile_api.read_execution_profile(instrument, reference)
    assert profile.model_dump(mode="json") == {**BODY, "hash": digest(BODY)}


@pytest.mark.parametrize(
    "change",
    [
        {"ref": "unknown-profile.v1"},
        {"hash": "0" * 64},
    ],
)
def test_unknown_profile_ref_or_hash_is_rejected(change):
    profile_api = api()
    reference = DecisionSourceRefV1.model_validate({"ref": REF, "hash": digest(BODY), **change})
    with pytest.raises(ValueError, match="^decision_execution_profile_original_mismatch$"):
        profile_api.read_execution_profile("okx:BTC-USDT-SWAP", reference)


@pytest.mark.parametrize("instrument", ["BTC-USDT-SWAP", "okx:BTC-USDT", "other:ETH-USDT-SWAP"])
def test_unapproved_instrument_scope_cannot_use_policy(instrument):
    profile_api = api()
    reference = DecisionSourceRefV1(ref=REF, hash=digest(BODY))
    with pytest.raises(ValueError, match="^decision_execution_profile_instrument_unsupported$"):
        profile_api.paper_execution_profile_reference(instrument)
    with pytest.raises(ValueError, match="^decision_execution_profile_instrument_unsupported$"):
        profile_api.read_execution_profile(instrument, reference)


def test_client_model_copy_cannot_replace_server_body_or_poison_fresh_reads():
    profile_api = api()
    reference = profile_api.paper_execution_profile_reference("okx:BTC-USDT-SWAP")
    first = profile_api.read_execution_profile("okx:BTC-USDT-SWAP", reference)
    copied = first.model_copy(
        update={"margin_mode": "cross", "initial_notional_to_margin_ratio": "10"}
    )
    counterfeit_body = copied.model_dump(mode="json", exclude={"hash"})
    counterfeit_ref = reference.model_copy(update={"hash": digest(counterfeit_body)})
    with pytest.raises(ValueError, match="^decision_execution_profile_original_mismatch$"):
        profile_api.read_execution_profile("okx:BTC-USDT-SWAP", counterfeit_ref)
    second = profile_api.read_execution_profile("okx:BTC-USDT-SWAP", reference)
    assert second is not first
    assert second.model_dump(mode="json") == {**BODY, "hash": digest(BODY)}
    with pytest.raises(ValidationError, match="frozen_instance"):
        second.execution = "live"
    assert profile_api.paper_execution_profile_reference("okx:BTC-USDT-SWAP") is not reference
