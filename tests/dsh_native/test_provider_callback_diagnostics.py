"""Safe observations around real private writers, never paid provider requests."""

from __future__ import annotations

import asyncio
import json
import os
import time
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from apps.hub_api.request_timing import SlowRequestLoggingMiddleware
from packages.kernel.decision_hub_kernel.application import provider_callback_diagnostics as diag
from packages.kernel.decision_hub_kernel.application.provider_input_receipts import (
    ProviderInputReceiptService,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRecord,
    RunRoleOutputRecord,
    utcnow,
)
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import (
    forbid_external_connections as forbid_external_connections,
)
from tests.dsh_native.test_provider_input_receipts import inputs as inputs


def payload(call_id="call-one"):
    return SimpleNamespace(run_id="run_" + "a" * 32, dispatch_id="dispatch-one", call_id=call_id)


def test_safe_fields_identity_bounds_and_sink_failure_never_grant_success(monkeypatch):
    monkeypatch.delenv(diag.ENV_PATH, raising=False)
    records = []
    observer = diag.ProviderCallbackDiagnostics(payload(), "emission", 15_000, sink=records.append)
    other = diag.ProviderCallbackDiagnostics(
        payload("call-two"), "emission", 15_000, sink=records.append
    )
    assert observer.callback_key_hash != other.callback_key_hash
    assert observer.operation_id != other.operation_id
    sensitive = "secret /file/path SELECT credentials https://unsafe.invalid"
    observer.event("route", "error", error=ValueError(sensitive), error_code=sensitive)
    for _ in range(100):
        observer.event("return")
    assert len(records) == diag.MAX_EVENTS
    encoded = json.dumps(records)
    assert sensitive not in encoded and "dispatch-one" not in encoded and "call-one" not in encoded
    assert records[0]["error_type"] == "ValueError" and records[0]["error_code"] is None
    assert records[0]["allocated_budget_ms"] == 15_000
    malformed = SimpleNamespace(run_id="\ud800", dispatch_id="\ud800")
    assert diag.ProviderCallbackDiagnostics(malformed, "input").run_id is None
    assert all(
        len((json.dumps(record) + "\n").encode()) < diag.MAX_LINE_BYTES for record in records
    )

    def broken(_record):
        raise OSError(sensitive)

    observer = diag.ProviderCallbackDiagnostics(payload(), "input", sink=broken)
    with observer.phase("input_verify"):
        actual_result = 42
    assert actual_result == 42
    with pytest.raises(ValueError, match="authority_denied"), observer.phase("postwrite"):
        raise ValueError("authority_denied")
    with pytest.raises(asyncio.CancelledError), observer.phase("input_verify"):
        raise asyncio.CancelledError
    assert diag.ProviderCallbackDiagnostics(payload(), "input")._sink is None


def test_async_sink_request_never_waits_for_disk_and_cap_is_lossy(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original_write = os.write

    def delayed(descriptor, raw):
        entered.set()
        assert release.wait(2)
        return original_write(descriptor, raw)

    monkeypatch.setattr(diag.os, "write", delayed)
    path = tmp_path / "provider-input.phases.jsonl"
    sink = diag._AsyncJsonlSink(path)
    try:
        assert sink({"safe": True})
        assert entered.wait(1)
        started = time.perf_counter()
        for _ in range(diag.QUEUE_CAPACITY + 2):
            sink({"safe": True})
        assert time.perf_counter() - started < 0.2
        assert sink.dropped >= 2
    finally:
        release.set()
        sink.queue.join()
    monkeypatch.setattr(diag, "MAX_FILE_BYTES", path.stat().st_size)
    assert sink({"safe": True})
    sink.queue.join()
    assert sink.failed
    assert not sink({"safe": True})


def test_real_asgi_response_and_send_error_preserve_original_outcome():
    records = []
    observer = diag.ProviderCallbackDiagnostics(payload(), "input", sink=records.append)
    app = FastAPI()
    app.add_middleware(SlowRequestLoggingMiddleware)

    @app.get("/controlled")
    def endpoint(request: Request):
        request.scope["decision_hub_provider_callback_response_observer"] = (
            observer.asgi_observation
        )
        return {"actual": True}

    with TestClient(app) as client:
        assert client.get("/controlled").json() == {"actual": True}
    assert [item["phase"] for item in records] == [
        "response_ready",
        "response_headers_sent",
        "response_body_sent",
    ]
    assert all(item["status_code"] == 200 for item in records)

    async def run():
        async def application(scope, receive, send):
            scope["decision_hub_provider_callback_response_observer"] = observer.asgi_observation
            await send({"type": "http.response.start", "status": 204, "headers": []})

        async def receive():
            return {"type": "http.disconnect"}

        async def denied_send(message):
            raise PermissionError("sensitive send exception")

        with pytest.raises(PermissionError, match="sensitive send exception"):
            await SlowRequestLoggingMiddleware(application)(
                {"type": "http", "method": "GET"},
                receive,
                denied_send,
            )

    asyncio.run(run())
    assert records[-1]["phase"] == "response_send" and records[-1]["state"] == "error"
    assert "sensitive send exception" not in json.dumps(records)


@pytest.mark.usefixtures("forbid_external_connections")
def test_real_private_record_commit_idempotence_and_last_fence_rollback(active):
    prepared, submit = active
    service = ProviderInputReceiptService(prepared.database)
    records = []
    observer = diag.ProviderCallbackDiagnostics(submit, "input", 15_000, sink=records.append)
    first = service.record_input(submit, diagnostics=observer)
    phases = {(item["phase"], item["state"]) for item in records}
    assert {
        ("source_capture", "finished"),
        ("original_auth", "finished"),
        ("writer_wait", "finished"),
        ("writer_source", "finished"),
        ("input_verify", "finished"),
        ("postwrite", "finished"),
        ("commit", "finished"),
        ("writer_lifetime", "released"),
        ("return", "finished"),
    } <= phases
    assert records[-1]["phase"] == "return"
    assert (
        service.record_input(
            submit,
            diagnostics=diag.ProviderCallbackDiagnostics(submit, "input", sink=records.append),
        )
        == first
    )
    with prepared.database.session() as session:
        assert (
            len(
                session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.role.like("model_input:%"),
                    )
                ).all()
            )
            == 1
        )

    new_submit = submit.model_copy(update={"dispatch_id": "cancel-before-commit"})
    cancelled = []
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 5:
            raise ValueError("research_provider_callback_lifetime_elapsed")

    with pytest.raises(ValueError, match="lifetime_elapsed"):
        service.record_input(
            new_submit,
            request_check=check,
            diagnostics=diag.ProviderCallbackDiagnostics(
                new_submit, "input", sink=cancelled.append
            ),
        )
    assert not any(item["result_status"] == "committed" for item in cancelled)
    assert not any(item["phase"] == "return" for item in cancelled)
    with prepared.database.session() as session:
        assert (
            len(
                session.scalars(
                    select(RunRoleOutputRecord).where(
                        RunRoleOutputRecord.role.like("model_input:%"),
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.usefixtures("forbid_external_connections")
def test_real_original_mutation_generation_and_expired_lease_still_refused(active):
    prepared, submit = active
    service = ProviderInputReceiptService(prepared.database)
    records = []
    with pytest.raises(ValueError, match="current_execution_mismatch"):
        service.record_input(
            submit.model_copy(update={"generation": 2}),
            diagnostics=diag.ProviderCallbackDiagnostics(submit, "input", sink=records.append),
        )

    def poison(session, _):
        if any(
            isinstance(row, RunRoleOutputRecord) and row.role.startswith("model_input:")
            for row in session.new
        ):
            session.connection().exec_driver_sql(
                "UPDATE dsh_session_prompts SET prompt=prompt||' changed' WHERE run_id=?",
                (submit.run_id,),
            )

    event.listen(prepared.database.factory.class_, "after_flush", poison)
    try:
        with pytest.raises(ValueError):
            service.record_input(
                submit,
                diagnostics=diag.ProviderCallbackDiagnostics(submit, "input", sink=records.append),
            )
    finally:
        event.remove(prepared.database.factory.class_, "after_flush", poison)
    assert not any(item["result_status"] == "committed" for item in records)
    with prepared.database.session() as session:
        assert not session.scalars(
            select(RunRoleOutputRecord).where(
                RunRoleOutputRecord.role.like("model_input:%"),
            )
        ).all()
        run = session.get(RunRecord, submit.run_id)
        run.lease_expires_at = utcnow()
    with pytest.raises(PermissionError, match="permission_lost"):
        service.record_input(
            submit,
            diagnostics=diag.ProviderCallbackDiagnostics(submit, "input", sink=records.append),
        )
