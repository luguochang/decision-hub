"""Native prompt and full original wires; no model, fill or financial acceptance."""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest
from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    PerpetualSynthesisDraftV1,
)
from packages.kernel.decision_hub_kernel.ports.research_synthesis_protocol import (
    V2_DECISION_OUTPUT_SCHEMA_REF,
)

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest

REPAIR = Path(__file__).resolve().parents[2]
PROFILE = REPAIR / "packages/runtime_adapters/dsh_runtime/perpetual_profile.py"
FIXTURE = REPAIR / "extensions/dsh/decision-hub/tests/fixtures/perpetual-preparation.json"


def _profile() -> ModuleType:
    spec = importlib.util.spec_from_file_location("incremental_perpetual_content_profile", PROFILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module.__file__, str)
    assert Path(module.__file__).resolve() == PROFILE.resolve()
    return module


def _request() -> ResearchSessionRequest:
    original_fixture = importlib.import_module("tests.runtime.test_dsh_research_runtime")
    value = original_fixture._request()
    return ResearchSessionRequest.model_validate(value.model_dump(mode="json"))


def _prompt() -> str:
    # Full original typed draft is untrusted test data, never runtime evidence.
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    draft = PerpetualSynthesisDraftV1.model_validate(fixture["draft"])
    request = ResearchSessionRequest.model_validate(
        {**_request().model_dump(mode="json"), "output_schema_ref": V2_DECISION_OUTPUT_SCHEMA_REF}
    )
    return _profile().build_perpetual_prompt(
        request, request.model_dump_json(), draft.model_dump_json(), fixture["options"]["binding"]
    )


@pytest.mark.parametrize(
    "requirement",
    (
        "perpetual P_ref",
        "price type, evidence_ref, observed/received time and freshness",
        "actual instrument tick",
        "do not invent P_ref, tick or default distances",
        "long: SL=floor_tick(P_ref*(1-d_SL)), TP=ceil_tick(P_ref*(1+d_TP))",
        "short: SL=ceil_tick(P_ref*(1+d_SL)), TP=floor_tick(P_ref*(1-d_TP))",
        "not first-touch protected-strategy EV",
        "continue holding, consider long, consider short and invalidate the hold judgment",
        "notification or successor research/recomparison, never automatic entry",
    ),
)
def test_method_requires_researchable_protection_and_hold_switch_content(requirement: str) -> None:
    assert requirement in _prompt()


def test_complete_original_draft_retains_existing_local_exit_basis_and_analysis_channels() -> None:
    wire = json.loads(FIXTURE.read_text(encoding="utf-8"))["draft"]
    before = deepcopy(wire)
    exit_policy = next(value for value in wire["assumptions"] if value["role"] == "exit_policy")
    basis = next(value for value in wire["assumptions"] if value["role"] == "basis")
    exit_policy["statement"] = (
        "离线协议样本：价格和tick原件不足，保护价未知；到期按原冻结目标规则重核，不能假定成交。"
    )
    basis["statement"] = (
        "离线协议样本：证据未补齐继续观望；多空机制各获新证据时分别重核，原机制失效则撤销判断。"
    )
    wire["analysis"]["thesis"] = basis["statement"]
    wire["analysis"]["main_chain"][0]["confirmation"] = "新证据触发后继研究，不自动开仓。"
    wire["analysis"]["main_chain"][0]["invalidation"] = "原机制被反证则观望判断失效，重核全部候选。"
    parsed = PerpetualSynthesisDraftV1.model_validate(wire)
    assert parsed.assumptions[3].statement == exit_policy["statement"]
    assert [candidate["action"] for candidate in wire["candidates"]] == [
        candidate["action"] for candidate in before["candidates"]
    ]
    for candidate, old in zip(wire["candidates"], before["candidates"], strict=True):
        assert candidate["exit_ref"] == old["exit_ref"]
        assert candidate["exit_ref"] == {"local_id": exit_policy["assumption_id"]}
        assert candidate["basis_refs"] == old["basis_refs"]
        assert candidate["basis_refs"] == [{"local_id": basis["assumption_id"]}]
        assert candidate["condition_refs"] == old["condition_refs"]
    assert wire["path_distribution"] == before["path_distribution"]
    assert wire["direction_distribution"] == before["direction_distribution"]
    repair = _profile().build_perpetual_repair_prompt(_request(), [], [], {"original": "offline"})
    assert repair.endswith("frozen packet from memory; reuse the actual prepared_call_id.")
