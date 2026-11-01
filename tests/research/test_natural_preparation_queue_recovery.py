"""Native tmp SQLite queue-purpose controls, not Source or product qualification.

All archive owner/snapshot and lifecycle CAS/readback checks use real rows and
files. The explicit Source/creation-owner/journal seams below exercise ordering
only; they cannot grant a real preparation permit or Saved/ACK.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from packages.kernel.decision_hub_kernel.application import research_publication_jobs48 as jobs
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import BundleArchive
from packages.kernel.decision_hub_kernel.application.research_quality_view import ResearchQualityViewV1
from packages.kernel.decision_hub_kernel.application import research_quality_view
from packages.kernel.decision_hub_kernel.persistence.db import Base, RunRecord, RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleJobRecord as Job, PublicationBundleRecord as Saved,
    PublicationBundleConfirmationRecord as Ack, PublicationBundleStateEventRecord as Event,
)
from packages.kernel.decision_hub_kernel.ports.research_publication_bundle import FrozenPublicationBundleV1
from packages.kernel.decision_hub_kernel.ports import research_report_recovery as replies

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
RUN, REQUEST = 'queue-target', 'queue-target-freeze'
REASON = 'frozen_io_undeclared_authorization_resource'


def _publication(writer, run_id, *, quality=None):
    event_id, artifact_id = run_id + '-event', run_id + '-artifact'
    writer.add(RunRecord(run_id=run_id, event_id=event_id, artifact_id=artifact_id,
        strategy_version='research.v1', runtime_version='fixture', status='completed',
        finished_at=NOW, created_at=NOW, updated_at=NOW, admission_origin='scheduled_recheck',
        parent_run_id='fixture-parent', idempotency_key='research-review-plan:' + run_id + '-plan'))
    selection_body = jobs.encoded({'schema_version': 'research-graph-recipe-selection.v3'}).decode()
    selected = dict(zip(jobs.NATIVE_FIELDS, (run_id + '-selection', run_id,
        'graph_recipe_selection', 'research-graph-recipe-selection.v3', selection_body,
        jobs.sha(selection_body.encode()), NOW.isoformat())))
    writer.add(RunRoleOutputRecord(**{**selected, 'created_at': NOW}))
    body = dict(schema_version='fixture-publication.v1', run_id=run_id,
        event_id=event_id, artifact_id=artifact_id)
    if quality is not None:
        body.update(quality_binding=dict(source_result_hash=quality['decision']['full_report_hash'],
            quality_decision_hash=quality['decision']['decision_hash'],
            quality_sidecar_id=quality['quality_sidecar_id']),
            graph_final_ref={'row': quality['graph_final_ref']}, gate={'status': quality['gate_status']})
    payload = jobs.encoded(body).decode()
    publication = RunRoleOutputRecord(output_id=run_id + '-publication', run_id=run_id,
        role=jobs.PUBLICATION_SOURCE_ROLE, schema_version=body['schema_version'],
        payload_json=payload, payload_hash=jobs.sha(payload.encode()), created_at=NOW)
    writer.add(publication)
    selection_json = jobs.encoded(selected).decode()
    job = Job(run_id=run_id, request_id=REQUEST if run_id == RUN else run_id + '-freeze',
        event_id=event_id, artifact_id=artifact_id, publication_id=publication.output_id,
        publication_hash=publication.payload_hash, publication_schema=publication.schema_version,
        publication_created_at=NOW, generation=1, state_version=1, state='unconfirmed',
        reason_code=REASON, updated_at=NOW, created_at=NOW, available_at=NOW,
        selection_native_json=selection_json, selection_native_hash=jobs.sha(selection_json.encode()),
        reader_version=jobs.READER_VERSION)
    writer.add(job)
    writer.flush()
    job.selection_native_json = jobs.exact_selection(writer, run_id)
    job.selection_native_hash = jobs.sha(job.selection_native_json.encode())
    return job, publication


@pytest.fixture
def control(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'queue.db').as_posix())
    Base.metadata.create_all(engine, tables=[row.__table__ for row in
        (RunRecord, RunRoleOutputRecord, Job, Saved, Ack, Event)])
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    database = SimpleNamespace(engine=engine, factory=sessionmaker(engine, expire_on_commit=False))
    archive = BundleArchive(tmp_path / 'archive', max_bundle_bytes=16 * 1024 * 1024,
        max_material_bytes=192 * 1024 * 1024)
    state = SimpleNamespace(database=database, archive=archive, active=False, source_calls=0,
        native_calls=0, source_change_at=None, cas_lost=False, journal={}, owner_change=False)
    permit = object()  # Explicit test seam, never a Source-issued capability.
    @contextmanager
    def authority(session, *, run_id):
        assert run_id == RUN and not state.active
        assert session.connection().connection.driver_connection.execute('PRAGMA query_only').fetchone() == (1,)
        state.active = True
        try:
            yield permit
        finally:
            state.active = False
    def native(session, *, run_id, permit):
        assert state.active and permit is state.permit and run_id == RUN
        state.native_calls += 1
        if state.cas_lost and session.connection().connection.driver_connection.execute('PRAGMA query_only').fetchone() == (0,):
            session.execute(update(Job).where(Job.run_id == RUN).values(reason_code='changed'))
    def sources(value):
        assert state.active and value is permit
        state.source_calls += 1
        if state.source_calls == state.source_change_at:
            raise ValueError('publication_retry_source_changed')
    state.permit = permit
    preparer = SimpleNamespace(retry_authority49=authority, verify_retry_native49=native,
        verify_retry_sources49=sources, reader_abi_qualification=lambda _s, **_kw: jobs.CURRENT_READER_ABI)
    state.life = jobs.DurablePublicationLifecycle(database, archive, preparer=preparer, clock=lambda: NOW)
    with database.factory() as writer:
        job, _source = _publication(writer, RUN)
        jobs.append_event(writer, job, occurred_at=NOW, transition_phase='unconfirmed',
            failure_diagnostic={'status': 'unavailable', 'sha256': None})
        writer.commit()
    def owner(_session, _run):
        return dict(owner_id='fixture-owner', root_run_id='fixture-parent',
            lineage=['changed'] if state.owner_change else [RUN, 'fixture-parent'], owner_source='fixture')
    def journal(_session, reply, *, write):
        if write:
            state.journal['reply'] = copy.deepcopy(reply)
        else:
            assert state.journal['reply'] == reply
    state.owner, state.command_journal = owner, journal
    yield state
    state.life.close()
    engine.dispose()


def _retry(state, *, owned=True):
    options = dict(expected_request=REQUEST, expected_generation=1, expected_version=1,
        recovery_mode=jobs.DIAGNOSTIC_GAP_RECOVERY_MODE)
    if owned:
        options.update(expected_reason=REASON, verify_owner_native=state.owner,
            command_journal=state.command_journal)
    return state.life.retry_unconfirmed(RUN, **options)


def _owners(state, count=1, material_bytes=32):
    root = Path(research_quality_view.__file__).resolve().parents[4]
    raw = (root / 'artifacts/market-risk-implementation-20261007/B2/release39/'
        'eth-terminal39_01/quality.json').read_bytes()
    assert jobs.sha(raw) == '49e3b32d49357f023879317c68ec40d98b277e8e39c6bfbe0a3dcf084e3368c1'
    template = json.loads(raw)
    block = b'\0' * (1024 * 1024)
    digest = hashlib.sha256()
    remaining = material_bytes
    while remaining:
        portion = block[:min(remaining, len(block))]
        digest.update(portion)
        remaining -= len(portion)
    material_hash = digest.hexdigest()
    material_ref = 'materials/' + material_hash + '.json'
    path = state.archive._path(material_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.truncate(material_bytes)
    owners = []
    with state.database.factory() as writer:
        for index in range(count):
            run_id = 'queue-owner-' + str(index)
            quality = copy.deepcopy(template)
            quality['run_id'] = quality['decision']['run_id'] = run_id
            quality['usage'] = None
            quality['as_of'] = quality['published_at'] = NOW.isoformat()
            decision = {k: v for k, v in quality['decision'].items() if k != 'decision_hash'}
            quality['decision']['decision_hash'] = jobs.sha(jobs.encoded(decision))
            job, publication = _publication(writer, run_id, quality=quality)
            quality['publication_ref'] = dict(output_id=publication.output_id,
                role=publication.role, schema_version=publication.schema_version,
                payload_hash=publication.payload_hash, recorded_at=NOW.isoformat())
            value = ResearchQualityViewV1.model_validate_json(jobs.encoded(quality))
            bundle = FrozenPublicationBundleV1(run_id=run_id, event_id=job.event_id,
                artifact_id=job.artifact_id, publication_ref=value.publication_ref,
                recipe_selection_ref=value.recipe_selection_ref, original_published_at=NOW,
                original_read_at=NOW, quality_json=value.model_dump_json(),
                report_sha256=value.decision.full_report_hash,
                report_bytes=len(value.decision.full_report_json.encode()), material_ref=material_ref,
                material_sha256=material_hash, material_bytes=material_bytes,
                trusted_reader_source_sha256='c' * 64)
            reference, bundle_hash, size = state.archive.put(bundle)
            job.state, job.reason_code = 'confirmed', None
            job.bundle_hash, job.confirmed_at = bundle_hash, NOW
            writer.add(Saved(bundle_hash=bundle_hash, run_id=run_id, request_id=job.request_id,
                generation=1, bundle_ref=reference, bundle_bytes=size, material_ref=material_ref,
                material_hash=material_hash, material_bytes=material_bytes,
                report_hash=bundle.report_sha256, report_bytes=bundle.report_bytes,
                reader_hash=bundle.trusted_reader_source_sha256, prepared_at=NOW))
            writer.add(Ack(run_id=run_id, request_id=job.request_id, generation=1,
                bundle_hash=bundle_hash, confirmed_at=NOW))
            owners.append(SimpleNamespace(run_id=run_id, bundle=bundle, bundle_hash=bundle_hash,
                bundle_ref=reference, material_ref=material_ref))
        writer.commit()
    return owners


def test_all_native_owners_over_aggregate_bound_only_qualify_queue_purpose(control):
    _owners(control, count=2, material_bytes=97 * 1024 * 1024)
    with control.life._ro() as session:
        proof = control.life._preparation_queue_archive_ownership83(session, RUN)
        assert len(proof['owners']) == 2 and all(len(x['native']) == 6 for x in proof['owners'])
        assert proof['scope'] == jobs.PREPARATION_QUEUE_RECOVERY_MODE83
        assert proof['full_external_material_audit'] is False
        with pytest.raises(ValueError, match='publication_retry_archive_ownership_bound_exceeded'):
            control.life._retry_owned_archive_proof51(session, RUN)
    with pytest.raises(ValueError, match='publication_retry_archive_ownership_bound_exceeded'):
        _retry(control, owned=False)
    result = _retry(control)
    assert result['state'] == 'queued' and result['recovery_mode'] == proof['scope']
    assert result['report_confirmed'] is False and not control.life.archive.fenced(RUN)
    assert replies.PreparationRecoveryReplyV2.model_validate(result).automatic_retry is False
    assert replies.PreparationRecoveryReplyV2.model_validate({**result,
        'recovery_mode': jobs.DIAGNOSTIC_GAP_RECOVERY_MODE}).recovery_mode == jobs.DIAGNOSTIC_GAP_RECOVERY_MODE
    with control.life._ro() as session:
        assert list(session.scalars(select(Saved).where(Saved.run_id == RUN))) == []
        assert session.get(Ack, RUN) is None


@pytest.mark.parametrize('change', ['orphan', 'inflight', 'bundle', 'ack', 'index', 'fence'])
def test_queue_owner_inventory_snapshot_and_native_reject_changes(control, change):
    owner = _owners(control)[0]
    if change == 'orphan':
        control.archive._path('materials/' + 'a' * 64 + '.json').write_bytes(b'orphan')
    elif change == 'inflight':
        control.archive._path(owner.material_ref).parent.joinpath('partial.tmp').write_bytes(b'partial')
    elif change == 'bundle':
        path = control.archive._path(owner.bundle_ref)
        path.write_bytes(path.read_bytes() + b' ')
    elif change == 'fence':
        control.archive.exclusive(control.archive.fence_ref(owner.run_id), b'unknown-commit')
    else:
        with control.database.factory() as writer:
            if change == 'ack':
                writer.execute(delete(Ack).where(Ack.run_id == owner.run_id))
            else:
                writer.execute(update(Saved).where(Saved.run_id == owner.run_id).values(material_bytes=31))
            writer.commit()
    with pytest.raises(ValueError):
        _retry(control)
    with control.life._ro() as session:
        assert session.get(Job, RUN).generation == 1


@pytest.mark.parametrize('change', ['cas', 'source_entry', 'source_exit'])
def test_real_cas_and_source_exit_cannot_release_failed_recovery(control, change):
    if change == 'cas':
        control.cas_lost = True
    else:
        control.source_change_at = 1 if change == 'source_entry' else 2
    expected = 'publication_retry_generation_CAS_lost' if change == 'cas' else 'publication_retry_source_changed'
    with pytest.raises((ValueError, PermissionError), match=expected):
        _retry(control)
    with control.life._ro() as session:
        assert session.get(Ack, RUN) is None
        assert session.get(Job, RUN).generation == (2 if change == 'source_exit' else 1)
    assert control.life.archive.fenced(RUN) is (change == 'source_exit')


def test_queue_mode_is_not_caller_permission_and_wrong_target_remains_refused(control):
    with pytest.raises(ValueError, match='publication_retry_recovery_mode_invalid'):
        control.life.retry_unconfirmed(RUN, expected_request=REQUEST, expected_generation=1,
            expected_version=1, recovery_mode=jobs.PREPARATION_QUEUE_RECOVERY_MODE83)
    with control.database.factory() as writer:
        writer.execute(update(Job).where(Job.run_id == RUN).values(reason_code='different'))
        writer.commit()
    with pytest.raises(ValueError, match='publication_preparation_recovery_exact_head_required'):
        _retry(control)
    assert control.source_calls == 0
