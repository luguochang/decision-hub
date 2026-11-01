"""Local DTO parity only; these synthetic references grant no authority."""

from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    ActionReviewRequestV2,
    ResearchDecisionSynthesisV2,
)


def test_ordinary_market_preserves_event_original_without_event_conditioning():
    fixture = run_path(str(Path(__file__).with_name("test_d2_candidate_contract.py")))["review_request"]()
    conditioned = deepcopy(fixture["synthesis"])
    assert conditioned["market_distribution"]["event_scenario_ref"] == conditioned["event_probability_ref"]
    ResearchDecisionSynthesisV2.model_validate(conditioned)
    ordinary = deepcopy(conditioned)
    ordinary["market_distribution"]["event_scenario_ref"] = None
    parsed = ResearchDecisionSynthesisV2.model_validate(ordinary)
    assert parsed.market_distribution.event_scenario_ref is None
    assert parsed.event_probability_ref.model_dump(mode="json") == conditioned["event_probability_ref"]
    packet = deepcopy(fixture)
    packet["synthesis"] = ordinary
    # A server input domain can outlive the authored candidate without extending it.
    packet["allowed_domain"]["valid_until"] = "2027-01-01T00:00:00Z"
    assert ActionReviewRequestV2.model_validate(packet).synthesis.market_distribution.event_scenario_ref is None
    substituted = deepcopy(ordinary)
    substituted["market_distribution"]["event_scenario_ref"] = {"ref": "unrelated:event", "hash": "f" * 64}
    with pytest.raises(ValidationError, match="decision_v2_candidate_synthesis_probability"):
        ResearchDecisionSynthesisV2.model_validate(substituted)
    candidate_expired = deepcopy(packet)
    candidate_expired["requested_at"] = ordinary["candidate_set"]["valid_until"]
    domain_expired = deepcopy(packet)
    domain_expired["allowed_domain"]["valid_until"] = packet["requested_at"]
    for value in [candidate_expired, domain_expired]:
        with pytest.raises(ValidationError, match="decision_v2_candidate_request_expiry"):
            ActionReviewRequestV2.model_validate(value)
