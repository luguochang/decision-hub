"""Private callback/physical RO wrapper leaves, not actual full business audit."""
from pathlib import Path
from datetime import datetime,UTC
import importlib.util
import sqlite3
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from packages.kernel.decision_hub_kernel.application import research_quality_view as original

DEV=Path(__file__).resolve().parents[2]
SOURCE=DEV/'artifacts/market-risk-implementation-20261007/B2/report-serving-r3-48/full-audit-source'
NOW=datetime(2026,10,10,2,tzinfo=UTC)
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
routes=load('_private_full_audit_routes48',SOURCE/'apps/hub_api/research_quality_routes.py')
jobs=load('packages.kernel.decision_hub_kernel.application._private_full_audit_jobs48',
    SOURCE/'packages/kernel/decision_hub_kernel/application/research_publication_jobs48.py')

@pytest.fixture
def view():
    return original.ResearchQualityViewV1.model_validate_json((DEV/
        'artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json').read_bytes())

class PrivateLifecycle:
    def __init__(self,view,state='confirmed'):
        self.view=view;self.full_calls=0
        self.head={'run_id':view.run_id,'state':state,'state_version':0 if state=='legacy_full_original' else 4,
            'confirmed_at':NOW.isoformat() if state=='confirmed' else None,'reason_code':None}
        self.latest=dict(self.head)
    def read_with_head(self,run):
        return (NS(run_id=run,quality=lambda **kw:self.view) if self.head['state']=='confirmed' else None),dict(self.head)
    def status(self,run):return dict(self.latest)
    def audit_original(self,run,*,as_of):self.full_calls+=1;return self.view

def callbacks(life):
    return {r.path.rsplit('/',1)[1]:r.endpoint for r in routes.research_quality_router(
        object(),publication_lifecycle=life,clock=lambda:NOW).routes}

def test_full_audit_is_explicit_and_compares_complete_public_original(view):
    life=PrivateLifecycle(view);endpoint=callbacks(life)
    endpoint['quality'](view.run_id,Response());assert life.full_calls==0
    response=Response();result=endpoint['audit'](view.run_id,response)
    assert result==view and life.full_calls==1
    assert response.headers['X-Decision-Hub-Report-Read-Mode']=='full_original_audit'
    assert response.headers['X-Decision-Hub-Report-Audit-Comparison']=='saved_bundle_full_quality'

def test_same_report_hash_cannot_hide_a_changed_other_quality_field(view):
    life=PrivateLifecycle(view)
    altered=view.model_copy(update={'unknown_receipt_codes':('private_changed_field',)})
    assert altered.decision.full_report_hash==view.decision.full_report_hash
    life.audit_original=lambda *a,**kw:altered
    with pytest.raises(HTTPException) as error:callbacks(life)['audit'](view.run_id,Response())
    assert error.value.status_code==409 and error.value.detail=='research_quality_full_audit_verification_failed'

def test_serving_revoke_at_exit_rejects_an_otherwise_matching_audit(view):
    life=PrivateLifecycle(view);life.latest.update(state='revoked',state_version=5,confirmed_at=None)
    with pytest.raises(HTTPException) as error:callbacks(life)['audit'](view.run_id,Response())
    assert life.full_calls==1 and error.value.detail=='research_quality_full_audit_verification_failed'

def test_revoked_permission_never_reaches_full_reader(view):
    life=PrivateLifecycle(view,'revoked')
    with pytest.raises(HTTPException) as error:callbacks(life)['audit'](view.run_id,Response())
    assert life.full_calls==0 and error.value.detail=='research_quality_bundle_not_confirmed'

def test_legacy_original_has_no_false_saved_bundle_comparison(view):
    life=PrivateLifecycle(view,'legacy_full_original');response=Response()
    assert callbacks(life)['audit'](view.run_id,response)==view
    assert response.headers['X-Decision-Hub-Report-Audit-Comparison']=='legacy_original_only'
    assert response.headers['X-Decision-Hub-Report-State-Version']=='0'

def test_wrapper_uses_real_private_file_ro_queryonly_begin_and_zero_changes(tmp_path,monkeypatch):
    path=tmp_path/'private.sqlite'
    with sqlite3.connect(path) as native:native.execute('CREATE TABLE marker(value TEXT)')
    engine=create_engine('sqlite+pysqlite://',poolclass=NullPool,
        creator=lambda:sqlite3.connect(path.as_uri()+'?mode=ro',uri=True))
    life=object.__new__(jobs.DurablePublicationLifecycle);life.read_engine=engine
    def private_reader(session,run,*,as_of):
        native=session.connection().connection.driver_connection
        assert native.in_transaction and native.total_changes==0
        assert native.execute('PRAGMA query_only').fetchone()==(1,)
        with pytest.raises(sqlite3.OperationalError):native.execute("INSERT INTO marker VALUES ('denied')")
        return 'private-normal-reader-leaf'
    monkeypatch.setattr(original,'read_published_research_quality_in_session',private_reader)
    try:assert life.audit_original('private',as_of=NOW)=='private-normal-reader-leaf'
    finally:engine.dispose()
