"""V2 provider blocks identify the whole reviewed product packet, never a spot projection."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.generated.provider_input import (
    ResearchProviderToolEmissionSubmit,
)
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    _candidate,
    tool_arguments_json,
)
from packages.kernel.decision_hub_kernel.application.research_publication_binding import (
    observation_sha,
)
from packages.kernel.decision_hub_kernel.ports.research_action_synthesis import (
    normalize_action_synthesis,
)


def original(candidate):
    transcript = json.loads((Path(__file__).resolve().parents[2] / 'artifacts/d2-native-v2/native-transcript.json').read_text())
    arguments = {'candidate': candidate, 'review_request': transcript['request']}
    arguments['review_request']['synthesis'] = candidate
    encoded = json.dumps(arguments, ensure_ascii=False)
    return ResearchProviderToolEmissionSubmit.model_validate({
        'schema_version': 'research-provider-tool-emission-submit.v1',
        'run_id': 'native-v2-run', 'generation': 1,
        'root_session_id': 'root', 'execution_session_id': 'root',
        'dispatch_id': 'actual-dispatch', 'request_hash': 'a' * 64,
        'plugin_build_hash': 'b' * 64,
        'input_ref': {'output_id': 'original-input', 'payload_hash': 'c' * 64},
        'call_id': 'actual-submit', 'tool_name': 'decision_hub_synthesis_submit',
        'raw_arguments_json': encoded,
        'raw_arguments_hash': hashlib.sha256(encoded.encode()).hexdigest(),
        'arguments_hash': hashlib.sha256(tool_arguments_json(arguments).encode()).hexdigest(),
        'block_index': 0,
    })


def packet():
    transcript = json.loads((Path(__file__).resolve().parents[2] / 'artifacts/d2-native-v2/native-transcript.json').read_text())
    calls = [event['data'] for event in transcript['context']['events'] if event['type'] == 'tool/call']
    for call in calls:
        arguments = call.get('arguments')
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if isinstance(arguments, dict) and 'candidate' in arguments:
            return arguments['candidate']
    raise AssertionError('actual native fixture synthesis call required')


def test_complete_v2_provider_block_has_whole_packet_hash_and_rejects_ref_substitution():
    value = packet()
    expected = normalize_action_synthesis(value)
    assert _candidate(original(value)) == observation_sha(expected.model_dump(mode='json'))
    changed = deepcopy(value)
    changed['candidate_set']['candidate_target_ref']['hash'] = 'f' * 64
    assert _candidate(original(changed)) is None


def test_v2_provider_hash_changes_with_real_forecast_without_legacy_projection():
    value = packet()
    before = _candidate(original(value))
    assert before is not None
    changed = deepcopy(value)
    changed['gaps'].append('missing_test_source')
    # Gaps do not own any other reference and are part of the full candidate.
    assert _candidate(original(changed)) not in (None, before)
