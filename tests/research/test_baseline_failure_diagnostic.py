"""Unexpected baseline failures remain diagnosable without logging payloads."""
import json
import logging
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

from tests.research.test_research_mcp import _server


@pytest.mark.parametrize('kind', ['runtime', 'sqlite_busy'])
def test_unexpected_baseline_failure_logs_safe_class_and_stack_only(kind, tmp_path, caplog):
    private = 'private-baseline-diagnostic-control'
    error = RuntimeError(private)
    if kind == 'sqlite_busy':
        path = tmp_path / 'diagnostic.sqlite3'
        first, second = sqlite3.connect(path), sqlite3.connect(path, timeout=0)
        try:
            first.execute('CREATE TABLE fixture (id INTEGER)')
            first.execute('BEGIN IMMEDIATE')
            try:
                second.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError as native:
                assert native.sqlite_errorcode == sqlite3.SQLITE_BUSY
                error = OperationalError('SELECT ' + private, {'secret': private}, native)
        finally:
            first.close()
            second.close()

    class FailingBaseline:
        async def deliver(self, _identity):
            raise error

    caplog.set_level(logging.ERROR, logger='packages.workbench_adapters.research_mcp')
    app = _server(bridge_key='diagnostic-control-key', baseline_service=FailingBaseline()).streamable_http_app()
    with TestClient(app) as client:
        response = client.post('/decision-hub/v1/research-capabilities/baseline',
            headers={'x-decision-hub-bridge-key': 'diagnostic-control-key'}, json={
                'schema_version': 'research-baseline-request.v1', 'run_id': 'diagnostic-run',
                'research_session_id': 'diagnostic-session', 'generation': 1, 'request_hash': 'a' * 64,
            })
    assert response.status_code == 503
    assert response.json()['cause_code'] == 'baseline_operation_unavailable'
    records = [r for r in caplog.records if r.name == 'packages.workbench_adapters.research_mcp']
    assert len(records) == 1, 'Unexpected baseline failure has no safe cause diagnostic'
    text = records[0].getMessage()
    assert private not in text and 'diagnostic-control-key' not in text
    assert records[0].exc_info is None
    diagnostic = json.loads(text.removeprefix('research_baseline_operation_failed '))
    assert diagnostic['run_id'] == 'diagnostic-run' and diagnostic['generation'] == 1
    assert diagnostic['exception_chain'][0]['class'] == type(error).__name__
    assert any(f['function'] == 'deliver' for f in diagnostic['exception_chain'][0]['frames'])
    if kind == 'sqlite_busy':
        assert any(c.get('sqlite_errorcode') == sqlite3.SQLITE_BUSY for c in diagnostic['exception_chain'])
