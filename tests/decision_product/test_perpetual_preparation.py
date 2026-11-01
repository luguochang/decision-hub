"""Pure deterministic preparation, authored assumptions, and cross-language parity."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    return json.loads((ROOT / 'artifacts/d2-native-preparation/draft-fixture.json').read_text('utf-8'))


def prepare(value):
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import (
        prepare_perpetual_synthesis,
    )
    return prepare_perpetual_synthesis(value['draft'], **value['options'])


def test_preparation_derives_only_references_preserves_authored_values_and_matches_ts():
    value = fixture()
    original = deepcopy(value)
    result = prepare(value)
    assert value == original
    draft, candidate = value['draft'], result['candidate']
    assert candidate['path_distribution']['scenarios'] == draft['path_distribution']['scenarios']
    assert candidate['market_distribution']['direction_distribution']['probabilities'] == draft['direction_distribution']['probabilities']
    assert candidate['analysis']['thesis'] == draft['analysis']['thesis']
    assert result['assumptions'] == draft['assumptions']
    assert candidate['candidate_set']['candidates'][0]['unit_notional_usdt'] == draft['candidates'][0]['unit_notional_usdt']
    expected = ROOT / 'artifacts/d2-native-preparation/prepared-fixture.json'
    if expected.exists():
        assert result == json.loads(expected.read_text('utf-8'))


@pytest.mark.parametrize('change', ['foreign_source', 'role', 'cycle'])
def test_preparation_rejects_unsupported_original_role_and_recursive_intents(change):
    value = fixture()
    draft = value['draft']
    if change == 'foreign_source':
        draft['candidates'][0]['cost_ref'] = dict(ref='unacquired-source', hash='f' * 64)
    elif change == 'role':
        ref = draft['candidates'][0]['size_policy_ref']
        assert 'local_id' in ref
        next(row for row in draft['assumptions'] if row['assumption_id'] == ref['local_id'])['role'] = 'event_definition'
    else:
        draft['candidates'][0]['nested_candidate_id'] = draft['candidates'][0]['candidate_id']
    with pytest.raises(ValueError):
        prepare(value)


def test_unavailable_outcome_derives_only_missing_own_condition_and_preserves_raw_source():
    from packages.kernel.decision_hub_kernel.ports.perpetual_preparation import body_hash

    value = fixture()
    outcome = value["draft"]["event_probability"]["outcome_given_occurrence"]
    outcome.update(kind="unavailable", probabilities=None, conditioning_ref=None)
    original = deepcopy(value)
    result = prepare(value)
    assert value == original
    assert result["source_draft_hash"] == body_hash(original["draft"])
    event = result["candidate"]["event_probability"]
    assert event["outcome_given_occurrence"]["conditioning_ref"] == event["event_definition_ref"]
    assert event["outcome_given_occurrence"]["probabilities"] is None
    assert event["outcome_given_occurrence"]["kind"] == "unavailable"

    conflict = deepcopy(value)
    assumption = deepcopy(next(row for row in conflict["draft"]["assumptions"]
                               if row["role"] == "event_definition"))
    assumption["assumption_id"] = "different_event_definition"
    conflict["draft"]["assumptions"].append(assumption)
    conflict["draft"]["event_probability"]["outcome_given_occurrence"]["conditioning_ref"] = {
        "local_id": assumption["assumption_id"]
    }
    with pytest.raises(ValueError):
        prepare(conflict)
