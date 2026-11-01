"""Finite actual-SQLite pre-BEGIN cause controls; no Host or model invocation."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from apps.hub_api.model_binding_request import bind_model_request
from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import (
    DshModelDispatchBinding,
)


def payload():
    return DshModelDispatchBinding.model_validate(dict(
        schema_version='dsh-model-dispatch-binding.v1', run_id='run_' + 'a' * 32,
        dsh_session_id='dsh_' + 'b' * 64, request_hash='c' * 64, generation=1,
        plugin_build_hash='d' * 64, purpose='conversation', execution_session_id=None,
        parent_session_id=None, provider='private-control', model='private-model',
        reasoning_effort=None, temperature=None, max_tokens=8192, stop_sequences_hash=None,
    ))


def request():
    async def receive():
        await asyncio.sleep(0)
        return dict(type='http.request', body=b'', more_body=False)
    scope = dict(type='http', method='PUT', path='/isolated/model-binding', headers=[])
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_actual_initial_begin_busy_preserves_one_retryable_cause_without_writer(tmp_path):
    path = tmp_path / 'private.sqlite3'
    engine = create_engine('sqlite:///' + str(path), connect_args={'timeout': 0.02})
    locker = sqlite3.connect(path)
    locker.execute('CREATE TABLE private_control (value INTEGER)')
    locker.execute('BEGIN IMMEDIATE')
    calls = []

    def bind(value, *, request_check, diagnostics):
        calls.append(value)
        request_check()
        with engine.connect() as connection:
            with diagnostics.phase('writer_wait'):
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            pytest.fail('The contended writer must never be acquired')

    try:
        with pytest.raises(HTTPException) as caught:
            await bind_model_request(request(), SimpleNamespace(bind=bind), payload())
        assert caught.value.status_code == 409
        assert caught.value.detail == 'dsh_model_binding_writer_busy'
        assert isinstance(caught.value.__cause__, OperationalError)
        assert caught.value.__cause__.orig.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert len(calls) == 1
        assert locker.execute('SELECT COUNT(*) FROM private_control').fetchone()[0] == 0
        assert locker.total_changes == 0
    finally:
        locker.rollback()
        locker.close()
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('statement,code', [
    ('COMMIT', sqlite3.SQLITE_BUSY), ('UPDATE private_control SET value=1', sqlite3.SQLITE_BUSY),
    ('BEGIN IMMEDIATE', sqlite3.SQLITE_BUSY_SNAPSHOT),
])
async def test_postwrite_commit_and_snapshot_errors_never_gain_pre_begin_retry_code(
    statement, code,
):
    original = sqlite3.OperationalError('private error body must not be serialized')
    original.sqlite_errorcode = code
    error = OperationalError(statement, (), original)
    calls = []

    def bind(*args, **kwargs):
        calls.append(1)
        raise error

    with pytest.raises(OperationalError) as caught:
        await bind_model_request(request(), SimpleNamespace(bind=bind), payload())
    assert caught.value is error
    assert calls == [1]


@pytest.mark.asyncio
async def test_locked_message_without_typed_begin_evidence_is_not_classified_as_retryable_busy():
    error = RuntimeError('database is locked BEGIN IMMEDIATE')

    def bind(*args, **kwargs):
        raise error

    with pytest.raises(RuntimeError) as caught:
        await bind_model_request(request(), SimpleNamespace(bind=bind), payload())
    assert caught.value is error
