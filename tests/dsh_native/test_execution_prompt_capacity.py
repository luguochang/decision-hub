"""Frozen AUTO-47 producer input and offline preparation; never calls a model."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import gzip
import hashlib
from importlib import import_module
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.contracts_py.decision_hub_contracts import DshSessionPrompt
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import ResearchExecutionPromptV2
from packages.kernel.decision_hub_kernel.application.research_execution_binding import (
    ExecutionPreparationCandidate, ResearchExecutionPreparationService,
)
from tests.dsh_native.test_execution_preparation_v2 import PreparedInputs, inputs, _snapshot

assert inputs is not None  # Imported fixture supplies only an isolated temporary database.
NEW_CAP = 1048576
ARCHIVED_SHA = 'a36193a291e55e5d5d7db445a4c52a648df6de2d6fb4e8f4b28e2df88ca552c4'


def _archived_prompt() -> str:
    prompt = gzip.decompress((Path(__file__).parent / 'fixtures/auto47-perpetual-producer-prompt.txt.gz').read_bytes()).decode('utf-8')
    assert len(prompt) == 275167
    assert hashlib.sha256(prompt.encode()).hexdigest() == ARCHIVED_SHA
    return prompt


def _wire(prompt: str) -> dict:
    return dict(schema_version='dsh-session-prompt.v2', run_id='controlled-prompt-regression',
                dsh_session_id='controlled-session', request_id='controlled-request', generation=1,
                request_hash='0'*64, request_hash_kind='dsh_link_identity.v1', method_hash='1'*64,
                bundle_hash='2'*64, execution_request_receipt_hash='3'*64,
                recorded_at=datetime(2026,10,21,5,5,38,tzinfo=UTC), prompt=prompt)


def _host_wire(value: dict) -> dict:
    return dict(schema_version='dsh-session-prompt.v1', **{key:value[key] for key in
                ['run_id','dsh_session_id','request_id','request_hash','generation','prompt']})


@pytest.mark.parametrize('module', [
    'research_execution_binding', 'research_context_execution', 'research_context_selection',
    'research_context_selection_v2', 'research_context_selection_v3', 'research_context_selection_v4',
])
def test_exact_auto47_producer_prompt_passes_internal_and_host_contracts(module: str) -> None:
    value = _wire(_archived_prompt())
    model = import_module('packages.contracts_py.decision_hub_contracts.generated.' + module).ResearchExecutionPromptV2
    actual = model.model_validate(value)
    host = DshSessionPrompt.model_validate(_host_wire(value))
    assert actual.prompt == host.prompt == value['prompt']


def test_new_prompt_cap_counts_unicode_code_points_and_preserves_small_prompts() -> None:
    for prompt in ['Previously valid small prompt.', '\U0001f4c8' * NEW_CAP]:
        value = _wire(prompt)
        assert ResearchExecutionPromptV2.model_validate(value).prompt == prompt
        assert DshSessionPrompt.model_validate(_host_wire(value)).prompt == prompt
    with pytest.raises(ValidationError):
        ResearchExecutionPromptV2.model_validate(_wire('x' * (NEW_CAP + 1)))


def test_large_full_prompt_prepares_offline_candidate_without_writing(inputs: PreparedInputs) -> None:
    # Keep this test Run's authentic policy prefix; append the unchanged archived
    # perpetual body. The exact original prompt is checked separately above.
    archived_body = _archived_prompt()[17772:]
    prefix = inputs.prompt.split('\n\nControlled body; not an executed Host.')[0]
    prompt = prefix + '\n\n' + archived_body
    assert 262144 < len(prompt) <= NEW_CAP
    before = _snapshot(inputs.database, inputs.request.run_id)
    service = ResearchExecutionPreparationService(inputs.database)
    with inputs.database.session() as session:
        session.connection().exec_driver_sql('BEGIN')
        candidate = service.prepare_in_session(
            session, inputs.request, inputs.submit, inputs.upstream, prompt,
            runtime_id='dsh', runtime_version=f'dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}',
            profile_ref='decision-research.web.v1', prompt_policy_hash='4'*64,
            recorded_at=inputs.now, _candidate_only=True,
        )
        assert isinstance(candidate, ExecutionPreparationCandidate)
        assert candidate.prepared.prompt.prompt == prompt
        assert candidate.prepared.integrity.prompt_hash == hashlib.sha256(prompt.encode()).hexdigest()
        assert candidate.prepared.host_prompt().prompt == prompt
    assert _snapshot(inputs.database, inputs.request.run_id) == before

