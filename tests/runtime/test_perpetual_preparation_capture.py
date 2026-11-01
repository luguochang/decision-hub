"""Actual registered native prepare/review/finalize with a synthetic model backend."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.ports.runtime import AgentExecutionError
from packages.runtime_adapters.dsh_runtime.client import DshSdkNotification
from packages.runtime_adapters.dsh_runtime.event_mapper import (
    _active_generation_notifications,
    map_notifications,
)
from packages.runtime_adapters.dsh_runtime.perpetual_attestation import (
    extract_attested_perpetual_synthesis_captures,
)


def fixture():
    raw = json.loads((Path(__file__).resolve().parents[2] / 'artifacts/d2-native-preparation/phased-native-transcript.json').read_text('utf-8'))
    return raw, ResearchSessionRequest.model_validate(raw['context']['request'])


def extract(raw, request):
    session = raw['context']['binding']['sessionId']
    return extract_attested_perpetual_synthesis_captures(
        [DshSdkNotification('session.event', dict(sessionId=session, event=row)) for row in raw['context']['events']],
        expected_session_id=session, request=request)


def test_phase_capture_replays_original_draft_without_model_hashes_or_packet_recopy():
    raw, request = fixture()
    captures = extract(raw, request)
    assert len(captures) == 1
    assert captures[0].call_id == 'submit'
    assert captures[0].review_call_id == 'review'
    assert captures[0].authorization.verdict == 'approved'
    args = raw['context']['events'][5]['data']['arguments']
    assert set(args) == {'action', 'request_id', 'prepared_call_id'}
    assert len(json.dumps(args)) < 300


def test_phase_capture_rejects_mutated_authored_draft_with_unchanged_normalized_packet():
    raw, request = fixture()
    raw = deepcopy(raw)
    raw['context']['events'][1]['data']['arguments']['draft']['path_distribution']['scenarios'][0]['terminal_return_fraction'] = '0.99'
    with pytest.raises(AgentExecutionError):
        extract(raw, request)


@pytest.mark.parametrize('source', [
    {'kind': 'plugin', 'plugin': 'decision-hub-protected-context'},
    {'kind': 'plugin', 'plugin': 'compact'},
    {'kind': 'plugin', 'plugin': '@deepseek-ai/dsh-system-prompt'},
    {'kind': 'skill-catalog', 'form': 'catalog'},
    {'kind': 'subagent-report', 'form': 'relay'},
    {'kind': 'subagent-settled', 'form': 'notice'},
])
def test_checkpoint_mapping_preserves_approved_synthesis_across_native_notices(source):
    raw, request = fixture()
    session = raw['context']['binding']['sessionId']
    events = deepcopy(raw['context']['events'])
    events.extend([
        {'seq': 8, 'type': 'user/message', 'data': {
            'source': source, 'content': [{'type': 'text', 'text': 'Native notice.'}],
        }},
        {'seq': 9, 'type': 'turn/end', 'data': {'reason': {'kind': 'aborted'}}},
    ])
    traces = map_notifications(
        [DshSdkNotification('session.event', dict(sessionId=session, event=row))
         for row in events],
        run_id=request.run_id, research_session_id=session, request=request,
    )
    terminal = next(trace for trace in reversed(traces) if trace.event_type == 'round_completed')
    assert terminal.status == 'succeeded'
    assert 'attested synthesis' in terminal.summary


@pytest.mark.parametrize('data', [
    {'content': []},
    {'source': {'kind': 'user', 'rpcId': 'second-user-request'}, 'content': []},
    {'source': {'kind': 'user'}, 'content': []},
])
def test_generation_boundary_never_reuses_approval_before_a_later_user_input(data):
    first = DshSdkNotification('session.event', {'sessionId': 'session', 'event': {
        'type': 'user/message', 'data': {'source': {'kind': 'user', 'rpcId': 'first'}},
    }})
    second = DshSdkNotification('session.event', {'sessionId': 'session', 'event': {
        'type': 'user/message', 'data': data,
    }})
    notice = DshSdkNotification('session.event', {'sessionId': 'session', 'event': {
        'type': 'user/message', 'data': {'source': {'kind': 'plugin', 'plugin': 'compact'}},
    }})
    assert list(_active_generation_notifications([first, second, notice])) == [second, notice]
