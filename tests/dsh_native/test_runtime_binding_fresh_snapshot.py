"""Full native originals then independent SQLite freshness; no Host/model calls."""
from datetime import UTC, datetime, timedelta

import pytest

from packages.kernel.decision_hub_kernel.persistence.db import RunRecord, RunRoleOutputRecord
from packages.runtime_adapters.dsh_runtime import research_runtime_binding as binding
from tests.dsh_native.test_execution_preparation_v2 import inputs
from tests.research.test_full_method_v2_bundle import forbid_external_connections

pytestmark = pytest.mark.usefixtures('forbid_external_connections')
assert inputs is not None and forbid_external_connections is not None


@pytest.mark.parametrize('change', [None, 'cancel', 'owner', 'lease', 'bytes', 'absence', 'during_replay_cancel', 'during_replay_link'])
def test_native_original_binding_rechecks_intervening_commits(inputs, monkeypatch, change):
    database = inputs.database
    run_id = inputs.request.run_id
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        run.status = 'running'
        run.lease_owner = 'binding-original-owner'
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    with database.session() as session:
        anchor = binding.read_runtime_task_anchor(session, run_id)
    assert anchor.bundle_hash is not None and anchor.generation == 0
    real_prepare = binding.prepare_originals
    captures = []

    def prepare_then_change(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        captures.append(prepared)
        # The original reader has really exited its transaction. Commit from
        # another Session before authenticate_runtime_binding opens its fresh RO.
        if change in {'cancel', 'owner', 'lease', 'bytes', 'absence'}:
            with database.session() as session:
                run = session.get(RunRecord, run_id)
                if change == 'cancel':
                    run.status = 'cancelled'
                elif change == 'owner':
                    run.lease_owner = 'binding-replacement-owner'
                elif change == 'lease':
                    run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                elif change == 'bytes':
                    row = session.query(RunRoleOutputRecord).filter_by(
                        run_id=run_id, role='research_task_request').one()
                    row.payload_json = row.payload_json + ' '
                else:
                    session.add(RunRoleOutputRecord(output_id='late-method', run_id=run_id,
                        role='research_execution_method', schema_version='invalid.v1',
                        payload_json='{}', payload_hash='0' * 64, created_at=datetime.now(UTC)))
        return prepared

    monkeypatch.setattr(binding, 'prepare_originals', prepare_then_change)
    if change in {'during_replay_cancel', 'during_replay_link'}:
        from packages.kernel.decision_hub_kernel.persistence.original_preparation import PreparedOriginals
        from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
        real_verify = PreparedOriginals.verify_read_snapshot
        def advance_during_replay(prepared, session, **kwargs):
            real_verify(prepared, session, **kwargs)
            if change == 'during_replay_link':
                DshSessionLinkService(database).reserve(inputs.submit, inputs.upstream)
            else:
                with database.session() as other:
                    other.get(RunRecord, run_id).status = 'cancelled'
        monkeypatch.setattr(PreparedOriginals, 'verify_read_snapshot', advance_during_replay)
    if change:
        with pytest.raises(ValueError, match='readset_changed|permission_lost|original_execution_changed'):
            binding.authenticate_runtime_binding(database, anchor, None,
                deadline_at=inputs.request.deadline_at)
    else:
        binding.authenticate_runtime_binding(database, anchor, None,
            deadline_at=inputs.request.deadline_at)
    assert len(captures) == 1
    with database.engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA query_only').scalar_one() == 0


def test_real_authenticated_terminal_advance_reauthenticates_same_anchor_once(inputs, monkeypatch):
    from packages.contracts_py.decision_hub_contracts import DshSessionCompletion
    from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
    from packages.kernel.decision_hub_kernel.application.research_binding import read_research_bundle_in_session
    from packages.kernel.decision_hub_kernel.application.research_execution_binding import ResearchExecutionPreparationService
    from packages.kernel.decision_hub_kernel.application.research_policy_render_v2 import render_prepared_research_policy
    from packages.runtime_adapters.dsh_runtime.profile import PROMPT_POLICY_HASH, build_trusted_web_research_prompt
    database = inputs.database
    run_id = inputs.request.run_id
    with database.session() as session:
        run = session.get(RunRecord, run_id)
        run.status = 'running'
        run.lease_owner = 'terminal-race-owner'
        run.lease_expires_at = datetime.now(UTC) + timedelta(minutes=10)
        bundle = read_research_bundle_in_session(session, run_id)
    prompt = render_prepared_research_policy(bundle, inputs.request.allowed_capabilities) + '\n\n' + build_trusted_web_research_prompt(inputs.request)
    ResearchExecutionPreparationService(database).prepare(
        inputs.request, inputs.submit, inputs.upstream, prompt, runtime_id='dsh',
        runtime_version=f'dsh-web-{inputs.upstream.source_version}+{inputs.upstream.plugin_build_hash[:12]}',
        profile_ref='decision-research.web.v1', prompt_policy_hash=PROMPT_POLICY_HASH)
    with database.session() as session:
        anchor = binding.read_runtime_task_anchor(session, run_id)
    assert anchor.generation == 1
    real_prepare = binding.prepare_originals
    captures = []
    def prepare_then_real_callback(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        captures.append(prepared)
        if len(captures) == 1:
            # The same real authenticated callback writer used by the Host.
            # This is an explicit engineering completion, not a model result.
            DshSessionLinkService(database).complete(DshSessionCompletion(
                schema_version='dsh-session-completion.v1', run_id=run_id,
                dsh_session_id=inputs.submit.deterministic_session_id,
                terminal_status='completed', generation=1, last_seq=1,
                trace_ref=f'dsh-host://runs/{run_id}/trace',
                result_ref=f'dsh-host://runs/{run_id}/result', result_hash='a' * 64,
                completed_at=datetime.now(UTC), error=None), authenticated_host=True)
        return prepared
    monkeypatch.setattr(binding, 'prepare_originals', prepare_then_real_callback)
    binding.authenticate_runtime_binding(database, anchor, None,
        deadline_at=inputs.request.deadline_at)
    assert len(captures) == 2
    with database.session() as session:
        assert binding.read_runtime_task_anchor(session, run_id) == anchor
