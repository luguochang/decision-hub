"""Causal text is a reviewed proposal; refs need the separate actual resolver."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ResearchDecisionSynthesisV2,
)
from tests.contracts.test_d2_candidate_contract import review_request


def analysis_for(synthesis):
    return dict(
        schema_version="decision-causal-analysis.v1",
        candidate_target_ref=synthesis["candidate_set"]["candidate_target_ref"],
        thesis="主观情景：持续买盘可能延续，但现有证据不足以确认未来方向。",
        main_chain=[
            dict(
                link_id="main",
                claim_type="inference",
                statement="已观察价格是历史信息；需求延续只是待验证机制。",
                evidence_refs=synthesis["reasoning_refs"],
                confirmation="目标窗口出现持续成交确认后复核。",
                invalidation="后续价格与机制背离时撤销该解释。",
            )
        ],
        opposite_chain=[
            dict(
                link_id="opposite",
                claim_type="scenario",
                statement="反向机制：短期流动性消退可能令走势逆转。",
                evidence_refs=[],
                confirmation="需要新增可核验的流动性证据。",
                invalidation="持续反向成交确认前不视为事实。",
            )
        ],
        unresolved_questions=["尚缺真实流动性与未来成交证据；反向机制仍为假设。"],
    )


def test_native_v2_requires_complete_target_bound_analysis():
    synthesis = deepcopy(review_request()["synthesis"])
    synthesis["analysis"] = analysis_for(synthesis)
    assert ResearchDecisionSynthesisV2.model_validate(synthesis).analysis.thesis
    without = deepcopy(synthesis)
    without.pop("analysis")
    with pytest.raises(ValidationError):
        ResearchDecisionSynthesisV2.model_validate(without)
    wrong = deepcopy(synthesis)
    wrong["analysis"]["candidate_target_ref"] = dict(ref="other-target", hash="f" * 64)
    with pytest.raises(ValidationError):
        ResearchDecisionSynthesisV2.model_validate(wrong)
    false_fact = deepcopy(synthesis)
    false_fact["analysis"]["opposite_chain"][0]["claim_type"] = "fact"
    with pytest.raises(ValidationError):
        ResearchDecisionSynthesisV2.model_validate(false_fact)


def test_actual_review_original_resolver_is_required():
    from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
        validate_analysis_originals,
    )

    assert callable(validate_analysis_originals)


def test_catalog_requires_full_hash_closed_materials_and_rejects_invented_refs():
    from packages.kernel.decision_hub_kernel.application.perpetual_review_materials import (
        SERVER_ROLES,
        _entry,
        select_review_originals,
        validate_analysis_originals,
    )

    source = _entry(
        "frozen_input", "admitted-evidence", dict(excerpt="真实形状的测试原件；不是模型来源认证。")
    )
    catalog = dict(
        schema_version="decision-review-originals.v1",
        run_id="native-v2-run",
        request_hash="a" * 64,
        as_of="2026-01-01T00:10:00Z",
        originals=[_entry("server_material", role, dict(role=role)) for role in SERVER_ROLES]
        + [source],
    )
    synthesis = review_request()["synthesis"]
    synthesis["reasoning_refs"] = [source["source_ref"]]
    synthesis["analysis"]["main_chain"][0]["evidence_refs"] = [source["source_ref"]]
    validate_analysis_originals(synthesis, catalog)
    selected = select_review_originals(synthesis, catalog)
    later = _entry("run_evidence", "later", dict(excerpt="后续新增证据"))
    assert (
        select_review_originals(synthesis, dict(catalog, originals=catalog["originals"] + [later]))
        == selected
    )
    invented = deepcopy(synthesis)
    invented["analysis"]["main_chain"][0]["evidence_refs"][0]["hash"] = "e" * 64
    with pytest.raises(ValueError, match="original_unavailable"):
        validate_analysis_originals(invented, catalog)
    changed = deepcopy(catalog)
    changed["originals"][-1]["body_json"] = "{}"
    with pytest.raises(ValueError, match="body_mismatch"):
        validate_analysis_originals(synthesis, changed)
    missing = deepcopy(catalog)
    missing["originals"].pop(0)
    with pytest.raises(ValueError, match="required_original_missing"):
        validate_analysis_originals(synthesis, missing)
