"""Frozen spot Task hints never substitute the other asset for unavailable data."""

from __future__ import annotations

import hashlib
import json

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from packages.runtime_adapters.dsh_runtime.profile import build_trusted_web_research_prompt
from tests.dsh_native.test_research_prompt_query_scope import NOW, _request


def _spot_request(instrument):
    request = _request(
        narrative_present=True, preferred=("web.search",), allowed_web=("web.search",)
    )
    admission = f"research-run-spot-admission:{request.run_id}:" + "a" * 64
    scope = f"crypto:spot:okx:{instrument}:trade:1m"
    path = f"market-path-manifest:{scope}:" + "b" * 64
    excerpt = json.dumps({"original_ref": admission, "scope_id": scope, "instrument": instrument})
    item = ResearchInputEvidence(
        evidence_id=admission,
        source_id=admission,
        kind="document",
        authority="unverified",
        source_url=None,
        published_at=None,
        observed_at=NOW,
        received_at=NOW,
        content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
        excerpt=excerpt,
    )
    return request.model_copy(
        update={
            "evidence_refs": [admission, path],
            "input_evidence": [item],
            "allowed_capabilities": [
                "spot_market_facts",
                "data.historical",
                "market.crypto_derivatives",
                "market.crypto_crowding",
                "market.cross_asset",
                "web.search",
            ],
        }
    )


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
def test_exact_frozen_asset_drives_all_market_query_examples_and_does_not_mutate_task(instrument):
    request = _spot_request(instrument)
    before = request.model_dump_json()
    prompt = build_trusted_web_research_prompt(request)
    asset = instrument.split("-")[0]
    other = "ETH" if asset == "BTC" else "BTC"
    assert f"The server-frozen Run asset is exactly {instrument}" in prompt
    assert f'symbols=["{instrument}"]' in prompt
    assert f'symbols=["{instrument}-SWAP"]' in prompt
    assert f'symbols=["crypto.{asset.lower()}_usdt.ohlcv_1d"]' in prompt
    assert f'symbols=["{other}-USDT"]' not in prompt
    assert f'symbols=["{other}-USDT-SWAP"]' not in prompt
    assert f'symbols=["crypto.{other.lower()}_usdt.ohlcv_1d"]' not in prompt
    assert "Never substitute another asset" in prompt
    assert "one OKX path is not two sources" in prompt
    assert request.model_dump_json() == before


def test_admission_excerpt_corruption_does_not_fall_back_to_btc():
    request = _spot_request("ETH-USDT")
    item = request.input_evidence[0].model_copy(update={"content_hash": "0" * 64})
    with pytest.raises(ValueError, match="research_prompt_spot_scope_invalid"):
        build_trusted_web_research_prompt(request.model_copy(update={"input_evidence": [item]}))


def test_conflicting_asset_pointers_do_not_select_first_or_default_asset():
    eth, btc = _spot_request("ETH-USDT"), _spot_request("BTC-USDT")
    request = eth.model_copy(
        update={
            "input_evidence": [*eth.input_evidence, *btc.input_evidence],
            "evidence_refs": [*eth.evidence_refs, *btc.evidence_refs],
        }
    )
    with pytest.raises(ValueError, match="research_prompt_spot_scope_conflict"):
        build_trusted_web_research_prompt(request)


def test_legacy_request_keeps_existing_btc_hints_but_new_spot_without_scope_is_rejected():
    request = _request()
    prompt = build_trusted_web_research_prompt(request)
    assert 'symbols=["BTC-USDT"]' in prompt
    assert "The server-frozen Run asset" not in prompt
    with pytest.raises(ValueError, match="research_prompt_spot_scope_missing"):
        build_trusted_web_research_prompt(
            request.model_copy(
                update={
                    "allowed_capabilities": [*request.allowed_capabilities, "spot_market_facts"],
                }
            )
        )
