"""The live decision prompt prioritizes authorized exact-asset native facts."""

from __future__ import annotations

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
    LEGACY_OUTPUT_SCHEMA_REF,
)
from packages.runtime_adapters.dsh_runtime.profile import (
    build_research_prompt,
    build_synthesis_tool_repair_prompt,
    build_trusted_web_research_prompt,
)
from tests.dsh_native.test_spot_prompt_asset_scope import (
    _spot_request,  # pyright: ignore[reportPrivateUsage]
)

PRIORITY = "The first evidence-acquisition batch must include spot_market_facts"


def _request(instrument: str = "ETH-USDT") -> ResearchSessionRequest:
    request = _spot_request(instrument)
    requirements = [
        item.model_copy(update={"preferred_capabilities": ["spot_market_facts"]})
        if item.requirement_id == "crypto_spot_confirmation"
        else item
        for item in request.evidence_requirements
    ]
    return request.model_copy(update={
        "output_schema_ref": DECISION_OUTPUT_SCHEMA_REF,
        "evidence_requirements": requirements,
    })


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
def test_live_decision_first_batch_gets_exact_spot_before_optional_planning(instrument: str):
    request = _request(instrument)
    original = request.model_dump_json()
    for prompt in (
        build_trusted_web_research_prompt(request),
        build_research_prompt(request, "session-fixed"),
    ):
        assert PRIORITY in prompt
        first_batch = next(line for line in prompt.splitlines() if PRIORITY in line)
        assert instrument in first_batch
        assert "crypto_spot_confirmation" in first_batch
        other = "ETH-USDT" if instrument == "BTC-USDT" else "BTC-USDT"
        assert other not in first_batch
        assert prompt.index(PRIORITY) < prompt.index("Audited web routes declared")
        assert "Before acquisition create a concrete todo plan" not in prompt
        assert "macro acquisition may run in parallel" in prompt
        assert "required skill loading" in prompt
        assert "initial excerpt or summary is not an admitted typed Fact" in prompt
        assert "one OKX venue does not satisfy an independent-source requirement" in prompt
        assert "A non-retryable spot failure remains a visible gap" in prompt
        assert "The reviewer returns its declared receipt schema" in prompt
    assert request.model_dump_json() == original


@pytest.mark.parametrize("case", [
    "legacy", "replay", "historical", "continuation", "disabled", "unscoped", "absent",
])
def test_priority_does_not_grant_capability_or_rewrite_other_execution_paths(case: str):
    request = _request()
    updates = {}
    if case == "legacy":
        updates["output_schema_ref"] = LEGACY_OUTPUT_SCHEMA_REF
    elif case == "replay":
        updates["execution_mode"] = "replay"
    elif case == "historical":
        updates["research_mode"] = "historical_review"
    elif case == "continuation":
        updates["current_round"] = 2
    elif case == "disabled":
        updates["allowed_capabilities"] = [
            item for item in request.allowed_capabilities if item != "spot_market_facts"
        ]
    else:
        updates["evidence_requirements"] = [
            item.model_copy(update={
                "preferred_capabilities": ["market.crypto_derivatives"],
                "allowed_fallbacks": [],
            }) if item.requirement_id == "crypto_spot_confirmation" else item
            for item in request.evidence_requirements
            if case != "absent" or item.requirement_id != "crypto_spot_confirmation"
        ]
    prompt = build_trusted_web_research_prompt(request.model_copy(update=updates))
    assert PRIORITY not in prompt


def test_first_batch_accepts_explicit_fallback_but_repair_never_reopens_acquisition():
    request = _request()
    request = request.model_copy(update={"evidence_requirements": [
        item.model_copy(update={
            "preferred_capabilities": ["market.crypto_derivatives"],
            "allowed_fallbacks": ["spot_market_facts"],
        }) if item.requirement_id == "crypto_spot_confirmation" else item
        for item in request.evidence_requirements
    ]})
    assert PRIORITY in build_trusted_web_research_prompt(request)
    repair = build_synthesis_tool_repair_prompt(request, "session-fixed", [], [])
    assert PRIORITY not in repair
    assert "Do not acquire new evidence" in repair


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
@pytest.mark.parametrize(
    "legacy_ref", [None, LEGACY_OUTPUT_SCHEMA_REF, "research-session-result.v1"],
)
def test_integrated_macro_playbook_has_one_initial_acquisition_order(
    instrument: str, legacy_ref: str | None,
):
    request = _request(instrument)
    macro_requirements = [
        request.evidence_requirements[0].model_copy(update={
            "requirement_id": requirement_id,
            "preferred_capabilities": ["market.cross_asset"],
            "allowed_fallbacks": [],
        })
        for requirement_id in ("macro_transmission", "cross_asset_confirmation")
    ]
    request = request.model_copy(update={
        "evidence_requirements": [*request.evidence_requirements, *macro_requirements],
        "output_schema_ref": legacy_ref or DECISION_OUTPUT_SCHEMA_REF,
    })
    original = request.model_dump_json()
    for prompt in (
        build_trusted_web_research_prompt(request),
        build_research_prompt(request, "session-fixed"),
    ):
        conflict = "issue both before the hard daily/spot/derivatives tasks"
        assert (conflict in prompt) is (legacy_ref is not None)
        assert (PRIORITY in prompt) is (legacy_ref is None)
        assert 'symbols=["US10Y","DXY"]' in prompt
        assert 'fields=["level","change_5d"]' in prompt
        assert 'symbols=["SPX","VIX"]' in prompt
        assert 'fields=["level","return_5d"]' in prompt
        assert "never describe it as an official release or execution feed" in prompt
        assert "complete the declared acquisition checklist before reviewer/synthesis" in prompt
        assert "The live closing cutoff takes precedence" in prompt
        assert "one OKX path is not two sources" in prompt
    assert request.model_dump_json() == original
