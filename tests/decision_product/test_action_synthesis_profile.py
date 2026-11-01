"""Frozen output selection and proactive policy; no model, DB, or live prices."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    _candidate,  # pyright: ignore[reportPrivateUsage]
    sha,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
)
from packages.kernel.decision_hub_kernel.decision.action_research_policy import (
    policy_hash,
    research_action_catalog,
    validate_action_catalog_candidate,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchActionCandidate
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    DECISION_OUTPUT_SCHEMA_REF,
    LEGACY_OUTPUT_SCHEMA_REF,
    synthesis_protocol,
)
from packages.runtime_adapters.dsh_runtime.profile import (
    build_research_prompt,
    build_structured_repair_prompt,
    build_synthesis_tool_repair_prompt,
    build_trusted_web_research_prompt,
)
from tests.decision_product.test_research_signal_contracts import action, legacy, synthesis
from tests.dsh_native.test_spot_prompt_asset_scope import (
    _spot_request,  # pyright: ignore[reportPrivateUsage]
)

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_prompts_match_saved_bytes_for_all_four_paths():
    request = ResearchSessionRequest.model_validate_json(
        (ROOT / "tests/fixtures/decision_product/legacy-prompt-request.json").read_text("utf-8"),
    )
    prompts = (
        build_research_prompt(request, "session-fixed"),
        build_trusted_web_research_prompt(request),
        build_structured_repair_prompt(request, "session-fixed", [], ["evidence-transcript"]),
        build_synthesis_tool_repair_prompt(request, "session-fixed", [], ["evidence-transcript"]),
    )
    assert [hashlib.sha256(value.encode()).hexdigest() for value in prompts] == [
        "d59441c56719f4807fb6b67db5d7d718d4c632505bdba2a6341124abf7b75383",
        "39d99b182a52181111f77e178b87909d1125f183153ec403603f64e383b7f863",
        "f797274c72a30544222530812d2a3bc4188c336ce613e65629d01d2364477d7e",
        "9fe96fb7de64190e6140e350a31bc4fdba0640ec4bc4aef42437d1da339e4d01",
    ]


@pytest.mark.parametrize("instrument", ["BTC-USDT", "ETH-USDT"])
def test_new_prompt_has_exact_scope_policies_and_repair_keeps_protocol(instrument: str):
    request = _spot_request(instrument).model_copy(
        update={"output_schema_ref": DECISION_OUTPUT_SCHEMA_REF}
    )
    before = request.model_dump_json()
    prompts = (
        build_trusted_web_research_prompt(request),
        build_research_prompt(request, "session"),
        build_synthesis_tool_repair_prompt(request, "session", [], []),
        build_structured_repair_prompt(request, "session", [], []),
    )
    catalog = research_action_catalog(instrument)
    for prompt in prompts:
        assert "research-decision-synthesis.v1" in prompt
        assert catalog["targets"]["24h"]["ref"] in prompt
        assert catalog["cost_policy"]["ref"] in prompt
        assert "Lack of calibration alone does not forbid" in prompt
        assert "Unknown probability is {kind:unavailable,value:null" in prompt
        assert "reduce_long" in prompt and "counterevidence" in prompt
        assert '"unit":"USDT/' + instrument.removesuffix("-USDT") + '"' in prompt
    assert request.model_dump_json() == before
    assert "action=no_trade with the exact declared missing" not in prompts[0]


def test_unknown_protocol_and_unfrozen_scope_do_not_fall_back_to_btc():
    assert synthesis_protocol(LEGACY_OUTPUT_SCHEMA_REF) == "legacy"
    assert synthesis_protocol("research-session-result.v1") == "legacy"
    with pytest.raises(ValueError, match="protocol_unsupported"):
        build_trusted_web_research_prompt(
            _spot_request("BTC-USDT").model_copy(update={"output_schema_ref": "random"})
        )
    request = ResearchSessionRequest.model_validate_json(
        (ROOT / "tests/fixtures/decision_product/legacy-prompt-request.json").read_text("utf-8"),
    ).model_copy(update={"output_schema_ref": DECISION_OUTPUT_SCHEMA_REF})
    with pytest.raises(ValueError, match="frozen_spot_scope_required"):
        build_trusted_web_research_prompt(request)


def _candidate_with_policy() -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = research_action_catalog("BTC-USDT")
    body = cast(dict[str, Any], action())
    body.update(
        size_policy_ref=catalog["size_policy"]["ref"],
        cost_policy_ref=catalog["cost_policy"]["ref"],
        target_spec_ref=catalog["targets"]["24h"]["ref"],
    )
    body["probability"]["target_id"] = body["target_spec_ref"]
    return body, catalog


def test_policy_preserves_locked_risk_cost_and_unknown_probability():
    body, catalog = _candidate_with_policy()
    validate_action_catalog_candidate(ResearchActionCandidate.model_validate(body), catalog)
    assert body["probability"]["value"] is None
    assert catalog["size_policy"]["definition"]["existing_long_initial_fraction"] == "1"
    assert catalog["size_policy"]["definition"]["new_nominal_cap_fraction"] == "0.25"
    assert catalog["cost_policy"]["definition"]["account_fee_verified"] is False
    for row in (catalog["size_policy"], catalog["cost_policy"], *catalog["targets"].values()):
        assert row["hash"] == policy_hash(row["definition"])
    original = copy.deepcopy(catalog)
    catalog["size_policy"]["definition"]["equity"] = "1"
    assert research_action_catalog("BTC-USDT") == original


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("scope", "scope_horizon_mismatch"),
        ("policy", "policy_reference_mismatch"),
        ("samples", "confirmation_insufficient"),
        ("feature", "feature_not_admitted"),
        ("source", "input_unregistered"),
        ("inputs", "inputs_incomplete"),
    ],
)
def test_unknown_policy_scope_feature_and_incomplete_inputs_rejected(mutation: str, reason: str):
    body, catalog = _candidate_with_policy()
    if mutation == "scope":
        catalog = research_action_catalog("ETH-USDT")
    elif mutation == "policy":
        body["size_policy_ref"] = "model-invented"
    elif mutation == "samples":
        body["entry"][0]["min_fresh_samples"] = 1
    elif mutation == "feature":
        body["entry"][0]["expression"]["feature"] = "invented"
    elif mutation == "source":
        body["entry"][0]["input_requirements"][0]["source"] = "blog"
    else:
        body["entry"][0]["input_requirements"][0]["metric"] = "box_high_6h"
    with pytest.raises(ValueError, match=reason):
        validate_action_catalog_candidate(ResearchActionCandidate.model_validate(body), catalog)


def _emission(candidate: dict[str, Any]):
    arguments = {"candidate": candidate}
    raw = json.dumps(arguments, ensure_ascii=False)
    return ResearchProviderToolEmissionSubmit.model_validate(
        dict(
            schema_version="research-provider-tool-emission-submit.v1",
            run_id="run-1",
            generation=1,
            root_session_id="session-1",
            execution_session_id="session-1",
            dispatch_id="dispatch-1",
            request_hash="a" * 64,
            plugin_build_hash="b" * 64,
            input_ref={"output_id": "input-1", "payload_hash": "c" * 64},
            call_id="call-1",
            tool_name="decision_hub_synthesis_submit",
            raw_arguments_json=raw,
            raw_arguments_hash=sha(raw),
            arguments_hash=sha(tool_arguments_json(arguments)),
            block_index=0,
        )
    )


def test_provider_preserves_old_projection_hash_but_binds_whole_raw_actions():
    wrapper = cast(dict[str, Any], synthesis())
    old, new = _emission(legacy()), _emission(wrapper)
    old_hash = _candidate(old)
    assert old_hash is not None and _candidate(new) == old_hash
    wrapper["action_intents"][0]["reason"] = "Changed action reasoning"
    changed = _emission(wrapper)
    assert _candidate(changed) == old_hash
    assert changed.arguments_hash != new.arguments_hash
    assert changed.raw_arguments_hash != new.raw_arguments_hash
    assert observation_sha(wrapper) != observation_sha(synthesis())


def test_invalid_action_cannot_use_valid_nested_candidate_as_provider_success():
    wrapper = cast(dict[str, Any], synthesis())
    wrapper["action_intents"][0]["action"] = "buy_anyway"
    assert _candidate(_emission(wrapper)) is None


def test_provider_rejects_changed_actual_arguments_with_unchanged_hash():
    emission = _emission(synthesis())
    with pytest.raises(ValueError, match="tool_arguments_invalid"):
        _candidate(emission.model_copy(update={"raw_arguments_json": "{}"}))
