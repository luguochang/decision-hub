"""The unique bound target goes through the existing unit calculator unchanged."""

from copy import deepcopy
from datetime import timedelta

import pytest

from packages.kernel.decision_hub_kernel.decision.action_comparison import compare_action_candidates
from tests.decision_product.test_action_comparison import content_hash, fixture, ref, resign
from tests.decision_product.test_okx_perpetual_original import NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_paper import creation, partition, service
from tests.decision_product.test_perpetual_paper import runtime as runtime


def bound_inputs():
    value = fixture()
    old = value['target']
    target = {key: deepcopy(body) for key, body in old.items()
              if key not in {'schema_version', 'kind', 'target_id', 'anchor', 'price_basis'}}
    target.update(schema_version='publication-bound-target.v1', candidate_target_ref=ref('pending'),
                  anchor_rule_ref=ref('anchor-rule'), assigned_at='2025-12-31T23:59:30Z',
                  duration_seconds=14400, clock_policy_ref=ref('strict-next-minute'),
                  price_basis='trade_1m_close', method_ref=ref('real-threshold-method'))
    value['schema_version'] = 'decision-method-inputs.v2'
    value['target'] = target
    return resign(value)


def test_same_reviewed_paths_use_single_publication_bound_target_without_old_projection():
    old = compare_action_candidates(fixture())
    value = bound_inputs()
    result = compare_action_candidates(value)
    assert result['target_ref']['hash'] == content_hash(value['target'])
    assert result['selected_action_id'] == old['selected_action_id'] == 'open_short'
    for actual, previous in zip(result['candidate_results'], old['candidate_results'], strict=True):
        assert {key: body for key, body in actual.items() if key != 'outcome'} == {
            key: body for key, body in previous.items() if key != 'outcome'
        }
        assert actual['outcome']['conditional_on_fill'] == previous['outcome']['conditional_on_fill']
        assert actual['outcome']['source_ref']['hash'] == content_hash(value['path_distribution'])
    assert 'kind' not in value['target'] and 'anchor' not in value['target']


def test_bound_target_cannot_use_spot_price_basis_or_legacy_method_version():
    value = bound_inputs()
    value['target']['price_basis'] = 'trade'
    resign(value)
    with pytest.raises(ValueError, match='comparison_new_perp_target_required'):
        compare_action_candidates(value)


def test_real_paper_snapshot_and_utility_bind_distinct_projection_and_ledger_originals(runtime):
    svc = service(runtime)
    svc.create_account('alice', creation(runtime))
    with runtime[0].session() as session:
        account, _position = svc.account_reader(session, 'alice', partition())
    value = bound_inputs()
    stamp = lambda time: time.isoformat().replace('+00:00', 'Z')
    at = stamp(NOW)
    for key in ('forecast_as_of', 'comparison_as_of', 'forecast_known_at_max', 'comparison_known_at_max'):
        value[key] = at
    for key in ('occurrence_distribution', 'outcome_given_occurrence'):
        value['event_probability'][key]['as_of'] = at
    value['market_distribution']['direction_distribution']['as_of'] = at
    value['price_bridge']['known_at'] = at
    value['target'].update(assigned_at=stamp(NOW - timedelta(seconds=30)),
                           window_start=at, window_end=stamp(NOW + timedelta(hours=4)))
    value['account_snapshot'] = account.model_dump(mode='json')
    value['bindings']['account'] = {'ref': 'test:actual-snapshot-projection',
                                    'hash': content_hash(value['account_snapshot'])}
    value['utility_policy']['account_ref'] = account.original.model_dump(mode='json')
    resign(value)
    value['market_distribution']['event_scenario_ref'] = deepcopy(value['bindings']['event_probability'])
    resign(value)
    assert value['utility_policy']['account_ref'] != value['bindings']['account']
    result = compare_action_candidates(value)
    assert result['selected_action_id'] == 'open_short'
    assert result['bindings']['account']['hash'] == content_hash(value['account_snapshot'])
    value = bound_inputs()
    value['schema_version'] = 'decision-method-inputs.v1'
    with pytest.raises(ValueError, match='comparison_new_perp_target_required'):
        compare_action_candidates(value)
