"""Explicit perpetual scope versions never widen the legacy horizon contracts."""
from copy import deepcopy

import pytest

from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchFactoryStudySource,
    ResearchFactoryStudySourceV2,
)
from packages.contracts_py.decision_hub_contracts.generated.research_policy import (
    ResearchTaskScope,
    ResearchTaskScopeV2,
)
from packages.kernel.decision_hub_kernel.ports.action_plan import Opportunity, parse_opportunity


def study():
    return dict(schema_version="research-factory-study-source.v2", study_id="study", scope_key="a" * 64,
                question_key="question", market="crypto", instrument="BTC-USDT-SWAP", raw_horizon_scope="4h",
                horizon_scope=["4h"], identity_basis="perpetual_admission.v1",
                created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
                perpetual_scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                admission_ref={"ref": "admission", "hash": "a" * 64})


def scope():
    return dict(schema_version="research-task-scope.v2", run_id="run", event_id="event", study_id="study",
                trigger_snapshot_id="trigger", trigger_snapshot_hash="a" * 64, market="crypto",
                instrument="BTC-USDT-SWAP", horizon_scope=["4h"], domain_pack_ref="pack", execution_mode="live",
                research_mode="current_decision", scope_kind="current_snapshot", event_family="unknown",
                event_family_basis="unknown_conservative_default", watch_id=None, full_task_hash="a" * 64,
                requirements_hash="a" * 64, allowed_capabilities_hash="a" * 64,
                pit_cutoff_at="2026-01-01T00:00:00Z", deadline_at="2026-01-01T01:00:00Z", scope_hash="a" * 64,
                perpetual_scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                admission_ref={"ref": "admission", "hash": "a" * 64})


def opportunity():
    return dict(schema_version="decision-opportunity.v2", id="opp", key="opportunity", scope_id="crypto:perp:okx:BTC-USDT-SWAP:trade:1m",
                horizon="4h", kind="scheduled", event_ref=None, registered_at="2026-01-01T00:00:00Z",
                time_start="2026-01-01T00:00:00Z", time_end="2026-01-01T01:00:00Z", target_spec_hash="a" * 64,
                recipe_hash="a" * 64, cohort="prospective", status="open", reason=None)


@pytest.mark.parametrize("model,legacy,fixture", [(ResearchFactoryStudySourceV2, ResearchFactoryStudySource, study), (ResearchTaskScopeV2, ResearchTaskScope, scope)])
def test_new_perpetual_scope_and_legacy_contract_remain_separate(model, legacy, fixture):
    value = fixture()
    assert model.model_validate(value).horizon_scope == ["4h"]
    with pytest.raises(ValueError):
        legacy.model_validate(value)
    for instrument in ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]:
        extended = deepcopy(value)
        extended.update(instrument=instrument, perpetual_scope_id="crypto:perp:okx:" + instrument + ":trade:1m", horizon_scope=["4h", "24h", "72h"])
        assert model.model_validate(extended).horizon_scope == ["4h", "24h", "72h"]
    for patch in [dict(horizon_scope=["30m"]), dict(horizon_scope=[]), dict(horizon_scope=["4h", "4h"]), dict(instrument="ETH-USDT-SWAP")]:
        with pytest.raises(ValueError):
            model.model_validate({**value, **patch})


def test_opportunity_version_routing_preserves_legacy_alias():
    value = opportunity()
    assert parse_opportunity(value).horizon == "4h"
    with pytest.raises(ValueError):
        Opportunity.model_validate(value)
    for patch in [dict(horizon="30m"), dict(scope_id="crypto:okx:BTC-USDT:trade:1m"), dict(schema_version="decision-opportunity.v3")]:
        with pytest.raises(ValueError):
            parse_opportunity({**value, **patch})
