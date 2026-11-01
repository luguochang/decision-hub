"""Pure renewal/wiring controls: fake transactions, no SQLite/HTTP/provider.

Actual _attempt fence and unknown-COMMIT logic run against a fake factory. This
does not qualify a physical SQLite renewal during a real frozen collector.
"""
from __future__ import annotations

import copy
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from packages.kernel.decision_hub_kernel.application import research_publication_jobs48 as jobs
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleArchive,BundleClaim,BundleCommitUnconfirmed,BundleWriterBeginBusy,
)
from test_publication_diagnostic_gap_recovery import functions

NOW=datetime(2026,10,11,tzinfo=timezone.utc)
DEV=Path(__file__).resolve().parents[2]
BEFORE=DEV/'artifacts/core-goal-20261011/execution-control-jobs01/before/packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py'


class FakeSession:
    def __init__(self,state,*,writer=False):self.state,self.writer=state,writer
    def get(self,kind,key,**_options):
        assert kind is jobs.Job and key==self.state.claim.run_id
        return self.state.job
    def connection(self):return self
    def exec_driver_sql(self,statement,parameters=None):
        if statement=='BEGIN IMMEDIATE':
            assert self.writer
            if self.state.busy:
                native=sqlite3.OperationalError('closed-busy-control')
                native.sqlite_errorcode=sqlite3.SQLITE_BUSY
                raise OperationalError(statement,(),native)
            return None
        assert statement.startswith('SELECT * FROM research_publication_bundle')
        assert parameters==(self.state.claim.run_id,)
        return SimpleNamespace(fetchall=lambda:self.state.saved)
    def execute(self,statement):
        assert self.writer
        text=str(statement)
        for field in ('request_id','owner','generation','state','state_version','lease_expires_at',
                      'publication_id','publication_hash','publication_schema','publication_created_at',
                      'selection_native_json','selection_native_hash','reader_version','event_id','artifact_id',
                      'bundle_hash IS NULL','confirmed_at IS NULL'):
            assert field in text
        assert 'lease_expires_at >' in text
        self.state.statements.append(statement)
        if self.state.cas_lost:return SimpleNamespace(rowcount=0)
        values=statement.compile().params
        self.state.job.lease_expires_at=values['lease_expires_at']
        self.state.job.updated_at=values['updated_at']
        return SimpleNamespace(rowcount=1)
    def expire(self,_job):pass
    def refresh(self,_job):pass
    def flush(self):pass
    def add(self,event):self.state.events.append(event)
    def commit(self):
        self.state.commits+=1
        if self.state.unknown_commit:raise RuntimeError('closed-commit-control')


@pytest.fixture
def renewal(tmp_path):
    claim=BundleClaim('renew-run','renew-request','publication-bundle:1:'+'a'*32,3)
    job=SimpleNamespace(run_id=claim.run_id,request_id=claim.request_id,owner=claim.owner,generation=3,
        state='queued',state_version=4,lease_expires_at=NOW+timedelta(seconds=60),bundle_hash=None,
        confirmed_at=None,publication_id='source-row',publication_hash='b'*64,
        publication_schema='source.v1',publication_created_at=NOW,selection_native_json='selection',
        selection_native_hash='c'*64,reader_version=jobs.READER_VERSION,event_id='event',artifact_id='artifact',
        reason_code=None,updated_at=NOW)
    state=SimpleNamespace(claim=claim,job=job,saved=[],busy=False,cas_lost=False,unknown_commit=False,
        commits=0,events=[],statements=[],publication_checks=0,source_failure_at=None,
        before_writer=None,exit_change=None,reads=0)
    life=object.__new__(jobs.DurablePublicationLifecycle)
    life.archive=BundleArchive(tmp_path/'archive',max_bundle_bytes=1024*1024,max_material_bytes=1024*1024)
    life.clock=lambda:NOW

    @contextmanager
    def ro():
        state.reads+=1
        if state.reads==2 and state.exit_change:state.exit_change()
        yield FakeSession(state)

    @contextmanager
    def factory():
        original=copy.deepcopy(vars(job))
        if state.before_writer:state.before_writer()
        try:
            yield FakeSession(state,writer=True)
        except Exception:
            if not state.unknown_commit:vars(job).clear();vars(job).update(original)
            raise

    def publication(_session,_job):
        state.publication_checks+=1
        if state.publication_checks==state.source_failure_at:
            raise ValueError('publication_job_selected_coverage_changed')

    life._ro,life._publication=ro,publication
    life.database=SimpleNamespace(factory=factory)
    state.life=life
    return state


def test_renewal_uses_original_attempt_and_independent_readback(renewal):
    result=renewal.life.renew_claim(renewal.claim)
    assert result['generation']==3 and result['state_version']==5 and result['state']=='queued'
    assert result['lease_expires_at']==(NOW+timedelta(seconds=180)).isoformat()
    assert renewal.job.owner==renewal.claim.owner and renewal.commits==1 and renewal.reads==2
    assert renewal.publication_checks==3 and len(renewal.statements)==1
    assert not renewal.life.archive.fenced(renewal.claim.run_id)
    assert len(renewal.events)==1
    assert '"transition_phase":"lease_renewed"' in renewal.events[0].payload_json
    assert '"schema_version":"publication-bundle-state-event.v2"' in renewal.events[0].payload_json


@pytest.mark.parametrize('seconds',[0,89,181,True,180.0,'180'])
def test_rolling_lease_bound_cannot_be_widened(renewal,seconds):
    with pytest.raises(ValueError,match='publication_renew_lease_bound_required'):
        renewal.life.renew_claim(renewal.claim,lease_seconds=seconds)
    assert renewal.reads==0 and renewal.commits==0


@pytest.mark.parametrize('change',['expired','at_expiry','prepared','saved','ack','bundle','confirmed','owner','request','generation'])
def test_expired_or_nonprepare_claim_never_revives(renewal,change):
    if change=='expired':renewal.job.lease_expires_at=NOW-timedelta(seconds=1)
    elif change=='at_expiry':renewal.job.lease_expires_at=NOW
    elif change=='prepared':renewal.job.state='prepared'
    elif change in {'saved','ack'}:renewal.saved=[object()]
    elif change=='bundle':renewal.job.bundle_hash='b'*64
    elif change=='confirmed':renewal.job.confirmed_at=NOW
    elif change=='owner':renewal.job.owner='other-owner'
    elif change=='request':renewal.job.request_id='other-request'
    else:renewal.job.generation+=1
    with pytest.raises(PermissionError):renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==0 and not renewal.life.archive.fenced(renewal.claim.run_id)


@pytest.mark.parametrize('change',['version','lease','source','expired'])
def test_writer_exact_head_race_loses_without_commit(renewal,change):
    field={'version':'state_version','lease':'lease_expires_at','source':'selection_native_hash','expired':'lease_expires_at'}[change]
    value={'version':5,'lease':NOW+timedelta(seconds=61),'source':'d'*64,'expired':NOW}[change]
    renewal.before_writer=lambda:setattr(renewal.job,field,value)
    with pytest.raises(PermissionError):renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==0 and not renewal.life.archive.fenced(renewal.claim.run_id)


def test_known_begin_busy_releases_only_own_uncommitted_fence(renewal):
    renewal.busy=True
    with pytest.raises(BundleWriterBeginBusy):renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==0 and not renewal.life.archive.fenced(renewal.claim.run_id)


def test_cas_rowcount_lost_never_commits(renewal):
    renewal.cas_lost=True
    with pytest.raises(PermissionError,match='publication_renew_exact_head_CAS_lost'):
        renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==0 and not renewal.life.archive.fenced(renewal.claim.run_id)


def test_unknown_commit_keeps_original_owned_fence(renewal):
    renewal.unknown_commit=True
    with pytest.raises(BundleCommitUnconfirmed):renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==1 and renewal.life.archive.fenced(renewal.claim.run_id)


def test_preexisting_unknown_fence_cannot_be_removed(renewal):
    reference=renewal.life.archive.fence_ref(renewal.claim.run_id)
    renewal.life.archive.exclusive(reference,b'original-unknown')
    with pytest.raises(ValueError,match='publication_bundle_fence_requires_readonly_reconciliation'):
        renewal.life.renew_claim(renewal.claim)
    assert renewal.life.archive._path(reference).read_bytes()==b'original-unknown'
    assert renewal.commits==0 and renewal.reads==0


@pytest.mark.parametrize('change',['version','lease','source','saved','expiry','publication'])
def test_independent_exit_change_keeps_renewal_fence(renewal,change):
    if change=='version':renewal.exit_change=lambda:setattr(renewal.job,'state_version',6)
    elif change=='lease':renewal.exit_change=lambda:setattr(renewal.job,'lease_expires_at',NOW+timedelta(seconds=179))
    elif change=='source':renewal.exit_change=lambda:setattr(renewal.job,'publication_hash','e'*64)
    elif change=='saved':renewal.exit_change=lambda:renewal.saved.append(object())
    elif change=='expiry':renewal.exit_change=lambda:setattr(renewal.job,'lease_expires_at',NOW)
    else:renewal.source_failure_at=3
    with pytest.raises((PermissionError,ValueError)):renewal.life.renew_claim(renewal.claim)
    assert renewal.commits==1 and renewal.life.archive.fenced(renewal.claim.run_id)


def worker_control(*,producer_error=False,handoff_error=False,controlled=True,cancel_before_producer=False):
    order=[]
    claim=BundleClaim('worker-run','worker-request','worker-owner',1)
    prepared=object()
    @contextmanager
    def scope(_claim,*,renew,cancel_preparation,cancel_preparation_reason=None):
        assert _claim is claim
        assert callable(cancel_preparation_reason)
        order.append('execution_enter')
        if cancel_before_producer:
            cancel_preparation()
        def handoff():
            order.extend(['stop_join','final_renew'])
            renew()
            if handoff_error:raise PermissionError('closed-handoff')
        try:yield SimpleNamespace(prepare_save=handoff)
        finally:order.append('execution_exit')
    def producer(_claim):
        order.append('producer')
        if producer_error:raise ValueError('closed-producer')
        return prepared
    life=SimpleNamespace(claim_one=lambda **_kw:claim,diagnostic_scope=lambda _c:nullcontext(),
        renew_claim=lambda _c:order.append('renew_callback'),
        cancel_preparation=lambda _c:(order.append('cancel_callback') if _c is claim else pytest.fail('claim changed')),
        save_verified=lambda _c,_p:order.append('save'),
        record_failure_diagnostic=lambda *_a:(order.append('diagnostic') or ('ref','a'*64)),
        preparer=SimpleNamespace(abandon=lambda _p:order.append('abandon')),
        archive=SimpleNamespace(fenced=lambda _r:False),
        fail_claim=lambda *_a,**_kw:order.append('fail_claim'))
    worker=jobs.PublicationBundleWorker(life,producer)
    if controlled:worker.execution=SimpleNamespace(attempt_scope=scope)
    return worker,order


def test_worker_handoff_stops_join_then_final_renew_before_original_save():
    worker,order=worker_control()
    assert worker.process_one() is True
    assert order==['execution_enter','producer','stop_join','final_renew','renew_callback','save','execution_exit']


@pytest.mark.parametrize('error',['producer','handoff'])
def test_worker_scope_exits_before_failure_writer(error):
    worker,order=worker_control(producer_error=error=='producer',handoff_error=error=='handoff')
    assert worker.process_one() is False
    assert 'save' not in order and order.index('execution_exit')<order.index('diagnostic')<order.index('fail_claim')
    if error=='handoff':assert 'abandon' in order


def test_legacy_worker_without_controller_preserves_original_flow():
    worker,order=worker_control(controlled=False)
    assert worker.process_one() is True and order==['producer','save']


def test_worker_cancel_callback_is_closed_to_original_claim():
    worker,order=worker_control(producer_error=True,cancel_before_producer=True)
    assert worker.process_one() is False
    assert order.index('cancel_callback')<order.index('producer')<order.index('execution_exit')
    assert 'save' not in order


def test_original_cancelled_generation_is_preserved_after_failure_handler(renewal):
    # Original cancellation uses _attempt plus request/owner/generation CAS.
    # It does not use _owned and does not check lease expiry.
    # This writer seam models its SQL CAS generation update without a real DB.
    original_execute=FakeSession.execute
    def cancel_execute(session,statement):
        text=str(statement)
        assert 'generation' in text and 'state' in text and 'state_version' in text
        values=statement.compile().params
        session.state.job.state=values['state']
        session.state.job.generation=values['generation']
        session.state.job.owner=session.state.job.lease_expires_at=None
        return SimpleNamespace(rowcount=1)
    try:
        FakeSession.execute=cancel_execute
        renewal.life.cancel_preparation(renewal.claim)
    finally:
        FakeSession.execute=original_execute
    assert renewal.job.state=='cancelled' and renewal.job.generation==renewal.claim.generation+1
    with pytest.raises(PermissionError):
        renewal.life.fail_claim(renewal.claim,reason='publication_execution_stop_requested')
    assert renewal.job.state=='cancelled' and renewal.job.generation==renewal.claim.generation+1


def test_gap_and_original_saved_ack_functions_unchanged():
    from test_confirmed_report_snapshot import _trusted_jobs_chain
    _trusted_jobs_chain()  # Pins current whole jobs file, no function exemption.
    before=functions(BEFORE.read_text(encoding='utf-8'))
    # The lease candidate is audited in its original sealed version; the
    # separately reviewed reason callback/cancellation delta has its own seal.
    sealed=DEV/'artifacts/core-goal-20261011/execution-control-jobs02/candidate/packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py'
    from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import sha
    assert sha(sealed.read_bytes())=='a77864ccd60b243b41e0ef0435ce4de07fde18bd0cd46a367da31239034c87e9'
    after=functions(sealed.read_text(encoding='utf-8'))
    additions={name for name in after if name not in before}
    assert additions=={'DurablePublicationLifecycle._renew_claim_head','DurablePublicationLifecycle.renew_claim',
                      'DurablePublicationLifecycle.renew_claim.write'}
    assert not set(before)-set(after)
    assert {name for name in before if before[name]!=after[name]}=={'PublicationBundleWorker.process_one'}
    assert 'lease_renewed' in jobs.EVENT_PHASES49

