"""Pure known-cancel controls: no SQLite, HTTP, provider, model, or product run."""
import ast
import copy
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from fastapi import HTTPException

from packages.kernel.decision_hub_kernel.application import research_publication_jobs48 as jobs
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import BundleCommitUnconfirmed, BundleWriterBeginBusy
from packages.kernel.decision_hub_kernel.ports.research_report_recovery import ReportRecoveryStatusV1
from test_publication_diagnostic_gap_recovery import control, event, functions, FakeSession
from test_publication_claim_renewal import renewal, FakeSession as RenewSession, NOW


@pytest.fixture
def cancelled(control):
    state=control
    # This pure orchestration fixture follows the existing queue-purpose port.
    state.life._known_cancel_archive_ownership69 = state.life._retry_owned_archive_proof51
    reason="publication_execution_compute_budget_exceeded"
    state.rows += [event(3,2,"queued","retry_queued"),event(4,3,"queued","claimed"),event(5,4,"cancelled","cancelled")]
    state.rows[-1].reason_code=reason
    body=__import__('json').loads(state.rows[-1].payload_json);body['reason_code']=reason
    raw=jobs.encoded(body);state.rows[-1].payload_json=raw.decode();state.rows[-1].event_id=jobs.sha(raw)
    state.old_version=5
    vars(state.job).update(generation=4,state_version=5,state="cancelled",reason_code=reason,
        publication_id="pub",publication_hash="a"*64,publication_schema="source.v1",
        publication_created_at=NOW,selection_native_json="selection",selection_native_hash="b"*64,
        reader_version=jobs.READER_VERSION,event_id="original-event",artifact_id="original-artifact")
    state.life.preparer.reader_abi_qualification=lambda _s,**_k:jobs.CURRENT_READER_ABI
    state.owner_calls=0
    def owner(_s,_r):
        state.owner_calls+=1
        return ("normal-local-owner","crypto:spot:okx:BTC-USDT:trade:1m","c"*64)
    state.owner=owner
    return state


def recover(state):
    return state.life.retry_known_cancelled("gap-run",expected_request="gap-request",
        expected_generation=4,expected_version=5,verify_owner_native=state.owner)


def test_current_abi_known_cancel_uses_two_proofs_and_preserves_history(cancelled):
    original=copy.deepcopy(cancelled.rows)
    reply=recover(cancelled)
    assert reply["state"]=="queued" and reply["generation"]==5 and reply["state_version"]==6
    assert reply["report_confirmed"] is False and reply["automatic_retry"] is False
    assert len(cancelled.proof_calls)==2 and cancelled.proof_calls[0] is not cancelled.proof_calls[1]
    assert cancelled.owner_calls==3 and cancelled.attempts==1
    assert cancelled.rows[:5]==original
    assert not cancelled.life.archive.fenced("gap-run")
    assert list((cancelled.life.archive.root/'diagnostics').glob('*.json'))==[]


@pytest.mark.parametrize("abi",[None,"legacy",True,1])
def test_old_abi_cannot_retry_cancelled_even_with_normal_issuer(cancelled,abi):
    cancelled.life.preparer.reader_abi_qualification=lambda *_a,**_k:abi
    with pytest.raises(ValueError,match="publication_recovery_current_reader_required"):recover(cancelled)
    assert cancelled.attempts==0 and not cancelled.proof_calls
    assert cancelled.job.state=="cancelled" and cancelled.job.generation==4


def test_abi_custom_equality_cannot_issue_permission(cancelled):
    class Equal:
        def __eq__(self,_other):pytest.fail('untrusted equality invoked')
    cancelled.life.preparer.reader_abi_qualification=lambda *_a,**_k:Equal()
    with pytest.raises(ValueError,match="publication_recovery_current_reader_required"):recover(cancelled)
    assert cancelled.attempts==0


@pytest.mark.parametrize("field,value",[("state","unconfirmed"),("owner","other"),
    ("lease_expires_at",NOW),("bundle_hash","a"*64),("confirmed_at",NOW),
    ("generation",5),("state_version",6),("request_id","different"),("reason_code","unknown")])
def test_exact_cancel_head_and_no_saved_or_ack_required(cancelled,field,value):
    setattr(cancelled.job,field,value)
    with pytest.raises(ValueError,match="publication_recovery_exact_known_cancel_required"):recover(cancelled)
    assert cancelled.attempts==0


def test_saved_or_ack_target_refuses(cancelled):
    cancelled.target_saved=[object()]
    with pytest.raises(ValueError,match="publication_retry_saved_or_confirmed_requires_review"):recover(cancelled)
    assert cancelled.attempts==0


@pytest.mark.parametrize('change',['missing','tampered','after_cas'])
def test_saved_diagnostic_declarations_are_rehashed_not_invented(cancelled,change):
    from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import BundleClaim
    claim=BundleClaim('gap-run','gap-request','fixture',1)
    _ref,digest=cancelled.life.archive.put_diagnostic(claim,{'schema_version':'closed-test.v1'})
    cancelled.rows[1]=event(2,1,'unconfirmed','unconfirmed',{'status':'saved','sha256':digest})
    path=cancelled.life.archive._path(cancelled.life.archive.diagnostic_ref(claim))
    if change=='missing':path.unlink()
    elif change=='tampered':path.write_bytes(b'{"different":true}')
    else:cancelled.post_write=lambda:path.write_bytes(b'{"different":true}')
    with pytest.raises(ValueError):recover(cancelled)
    assert cancelled.attempts==int(change=='after_cas')
    assert cancelled.life.archive.fenced('gap-run')==(change=='after_cas')


def test_canonical_but_impossible_generation_history_refused(cancelled):
    cancelled.rows[1]=event(2,2,'unconfirmed','unconfirmed',{'status':'unavailable','sha256':None})
    with pytest.raises(ValueError,match='publication_recovery_known_history_required'):recover(cancelled)
    assert cancelled.attempts==0


def test_preexisting_unknown_commit_fence_never_released(cancelled):
    ref=cancelled.life.archive.fence_ref('gap-run');cancelled.life.archive.exclusive(ref,b'original-unknown')
    with pytest.raises(ValueError,match="publication_bundle_fence_requires_readonly_reconciliation"):recover(cancelled)
    assert cancelled.life.archive._path(ref).read_bytes()==b'original-unknown'
    assert cancelled.attempts==0 and cancelled.owner_calls==0


@pytest.mark.parametrize("change",["unknown_commit","writer_owner","exit_owner","owner_native",
    "second_archive","writer_archive","native_history","history_body","source_after","exit_abi","head"])
def test_changed_permission_keeps_new_fence_failclosed(cancelled,change):
    if change=='unknown_commit':cancelled.unknown_commit=True
    elif change in {'writer_owner','exit_owner','owner_native'}:
        at=2 if change!='exit_owner' else 3
        def owner(_s,_r):
            cancelled.owner_calls+=1
            return ('normal-local-owner','crypto:spot:okx:BTC-USDT:trade:1m',
                    'd'*64 if cancelled.owner_calls==at else 'c'*64)
        cancelled.owner=owner
    elif change=='second_archive':cancelled.proofs[1]={'inventory':[('orphan',)],'owners':[]}
    elif change=='writer_archive':cancelled.writer_change=True
    elif change=='native_history':cancelled.post_write=lambda:setattr(cancelled.rows[0],'occurred_at',NOW.replace(day=10))
    elif change=='history_body':cancelled.post_write=lambda:setattr(cancelled.rows[0],'payload_json',cancelled.rows[0].payload_json+' ')
    elif change=='source_after':cancelled.source_change_at=2
    elif change=='exit_abi':
        calls=[0]
        def qualifier(*_a,**_k):
            calls[0]+=1
            return jobs.CURRENT_READER_ABI if calls[0]==1 else None
        cancelled.life.preparer.reader_abi_qualification=qualifier
    else:cancelled.readback_change=True
    with pytest.raises((ValueError,PermissionError,BundleCommitUnconfirmed)):recover(cancelled)
    assert cancelled.life.archive.fenced('gap-run')


def test_default_unconfirmed_retry_still_rejects_cancelled(cancelled):
    with pytest.raises(ValueError,match='publication_retry_exact_unconfirmed_head_required'):
        cancelled.life.retry_unconfirmed('gap-run',expected_request='gap-request',expected_generation=4,expected_version=5)
    assert cancelled.attempts==0 and not cancelled.proof_calls


def use_original_attempt(state):
    """Real fence/BEGIN/COMMIT code, fake in-memory Session; no SQLite DB."""
    state.busy=False
    class Writer(FakeSession):
        def exec_driver_sql(self,statement,parameters=None):
            if statement=='BEGIN IMMEDIATE':
                if state.busy:
                    native=sqlite3.OperationalError('closed-busy-control');native.sqlite_errorcode=sqlite3.SQLITE_BUSY
                    raise OperationalError(statement,(),native)
                return None
            return super().exec_driver_sql(statement,parameters)
        def execute(self,statement):
            if state.cas_lost:return SimpleNamespace(rowcount=0)
            for field,value in statement.compile().params.items():
                if field in {'generation','state','reason_code','available_at','updated_at'}:
                    setattr(state.job,field,value)
            return SimpleNamespace(rowcount=1)
        def flush(self):pass
        def add(self,row):state.rows.append(row)
        def commit(self):
            if state.unknown_commit:raise RuntimeError('closed-commit-control')
    @contextmanager
    def factory():
        before=copy.deepcopy(vars(state.job));old_rows=list(state.rows)
        try:yield Writer(state,writer=True)
        except BaseException:
            if not state.unknown_commit:
                vars(state.job).clear();vars(state.job).update(before);state.rows[:]=old_rows
            raise
    state.life.database=SimpleNamespace(factory=factory)
    def attempt(*args):
        state.attempts+=1
        return jobs.DurablePublicationLifecycle._attempt(state.life,*args)
    state.life._attempt=attempt


@pytest.mark.parametrize('failure',['none','busy','cas','unknown_commit'])
def test_original_attempt_release_rules_remain_exact(cancelled,failure):
    use_original_attempt(cancelled)
    if failure=='none':
        assert recover(cancelled)['state']=='queued'
        assert not cancelled.life.archive.fenced('gap-run')
        return
    if failure=='busy':cancelled.busy=True;error=BundleWriterBeginBusy
    elif failure=='cas':cancelled.cas_lost=True;error=PermissionError
    else:cancelled.unknown_commit=True;error=BundleCommitUnconfirmed
    with pytest.raises(error):recover(cancelled)
    assert cancelled.life.archive.fenced('gap-run')==(failure=='unknown_commit')
    assert cancelled.job.state==('queued' if failure=='unknown_commit' else 'cancelled')


def test_ro_status_has_no_archive_proof_or_write_and_legacy_reason_is_not_backfilled(cancelled):
    before=copy.deepcopy(vars(cancelled.job))
    cancelled.life.preparer.reader_abi_qualification=lambda *_a,**_k:None
    value=ReportRecoveryStatusV1.model_validate(cancelled.life.known_cancel_recovery_status('gap-run'))
    assert value.action=='new_research_required' and value.proof_scope=='metadata_prechecks_only'
    assert value.source_abi is None and not cancelled.proof_calls and cancelled.attempts==0
    assert vars(cancelled.job)==before


@pytest.mark.parametrize('reason',sorted(jobs.CANCEL_REASONS))
def test_cancel_closed_reason_saved_in_same_original_job_event_cas(renewal,monkeypatch,reason):
    def execute(session,statement):
        values=statement.compile().params
        session.state.job.state=values['state'];session.state.job.generation=values['generation']
        session.state.job.reason_code=values['reason_code']
        session.state.job.owner=session.state.job.lease_expires_at=None
        return SimpleNamespace(rowcount=1)
    monkeypatch.setattr(RenewSession,'execute',execute)
    renewal.life.cancel_preparation(renewal.claim,reason_code=reason)
    assert renewal.job.reason_code==reason and renewal.commits==1
    assert renewal.events[-1].reason_code==reason and '"transition_phase":"cancelled"' in renewal.events[-1].payload_json
    with pytest.raises(PermissionError):renewal.life.fail_claim(renewal.claim,reason='publication_execution_stop_requested')
    assert renewal.job.reason_code==reason and renewal.job.state=='cancelled'


@pytest.mark.parametrize('reason',[None,'arbitrary',True,1])
def test_unknown_cancel_reason_rejected_before_writer(renewal,reason):
    with pytest.raises(ValueError,match='publication_bundle_cancel_reason_invalid'):
        renewal.life.cancel_preparation(renewal.claim,reason_code=reason)
    assert renewal.commits==0 and not renewal.life.archive.fenced(renewal.claim.run_id)


def test_closed_reason_custom_equality_rejected_before_writer(renewal):
    class Equal:
        def __eq__(self,_other):pytest.fail('custom equality invoked')
    with pytest.raises(ValueError,match='publication_bundle_cancel_reason_invalid'):
        renewal.life.cancel_preparation(renewal.claim,reason_code=Equal())
    assert renewal.commits==0


def test_authorization_expressions_default_retry_and_save_remain_exact():
    dev=Path(__file__).resolve().parents[2]
    path='packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py'
    before=(dev/'artifacts/core-goal-20261011/known-cancel-recovery01/root_before'/path).read_text(encoding='utf-8')
    after=Path(jobs.__file__).read_text(encoding='utf-8')
    old,new=functions(before),functions(after)
    changed={name for name in old if old[name]!=new[name]}
    assert changed=={'DurablePublicationLifecycle.cancel_preparation','DurablePublicationLifecycle.cancel_preparation.write','PublicationBundleWorker.process_one'}
    assert old['DurablePublicationLifecycle.cancel_preparation.write'].replace(
        'reason_code="publication_bundle_preparation_cancelled"','reason_code=reason_code')==new['DurablePublicationLifecycle.cancel_preparation.write']
    assert old['DurablePublicationLifecycle.retry_unconfirmed']==new['DurablePublicationLifecycle.retry_unconfirmed']
    assert old['DurablePublicationLifecycle.renew_claim']==new['DurablePublicationLifecycle.renew_claim']


def test_public_recovery_route_is_ro_not_a_mutation_contract(cancelled):
    from apps.hub_api.research_quality_routes import research_quality_router
    router=research_quality_router(SimpleNamespace(),publication_lifecycle=cancelled.life)
    route=next(item for item in router.routes if item.path.endswith('/report/recovery/status'))
    assert route.methods=={'GET'}
    assert route.endpoint('gap-run').action=='owner_request_required'
    assert cancelled.attempts==0 and not cancelled.proof_calls
    assert not any(item.path.endswith('/report/recovery') for item in router.routes)
