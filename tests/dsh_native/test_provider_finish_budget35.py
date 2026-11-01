"""Focused private controls: budgets/checkpoint wiring, never a provider request."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from apps.hub_api import provider_input_routes as routes
from packages.kernel.decision_hub_kernel.application import provider_callback_diagnostics as diag
from packages.kernel.decision_hub_kernel.application import provider_input_receipts as receipts
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from packages.kernel.decision_hub_kernel.persistence.original_preparation import PreparedOriginals
from tests.dsh_native.test_provider_input_receipts import active as active
from tests.dsh_native.test_provider_input_receipts import (
    forbid_external_connections as forbid_external_connections,
)
from tests.dsh_native.test_provider_input_receipts import inputs as inputs

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert inputs is not None


class Request:
    def __init__(self, budget: str | None):
        self.headers = {} if budget is None else {"x-decision-hub-provider-input-budget-ms": budget}
        self.scope = {}

    async def is_disconnected(self):
        return False


def test_finish_budget_boundaries_legacy_default_and_diagnostics(monkeypatch):
    monkeypatch.delenv(diag.ENV_PATH, raising=False)
    observed = []

    def operation(payload, *, request_check, diagnostics):
        request_check()
        observed.append(diagnostics.budget_ms)
        return {"original_body": payload}

    for phase, budget in (("finish", "30000"), ("finish", "60000"),
                          ("input", "15000"), ("emission", "15000"),
                          ("input", "30000"), ("emission", "30000"),
                          ("finish", None)):
        body = {"unchanged": True}
        result = asyncio.run(routes._bounded_callback(Request(budget), operation, body, phase))
        assert result == {"original_body": body}
        assert observed[-1] == (30000 if budget is None else int(budget))
    for phase, budget in (("finish", "60001"), ("input", "30001"),
                          ("emission", "30001"), ("finish", "0")):
        count = len(observed)
        with pytest.raises(HTTPException) as error:
            asyncio.run(routes._bounded_callback(Request(budget), operation, {}, phase))
        assert error.value.status_code == 400 and len(observed) == count
    assert diag.ProviderCallbackDiagnostics(SimpleNamespace(), "finish", 60000).budget_ms == 60000
    assert diag.ProviderCallbackDiagnostics(SimpleNamespace(), "input", 60000).budget_ms is None


def test_server_remaining_deadline_refuses_before_completion(monkeypatch):
    monkeypatch.delenv(diag.ENV_PATH, raising=False)
    clock = [10.0]
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])

    def operation(payload, *, request_check, diagnostics):
        request_check()
        clock[0] += 0.004
        request_check()
        raise AssertionError("A spent callback must not grant a return")

    with pytest.raises(HTTPException) as error:
        asyncio.run(routes._bounded_callback(Request("3"), operation, {}, "finish"))
    assert error.value.status_code == 409
    assert error.value.detail == "research_provider_callback_lifetime_elapsed"


def test_real_readonly_deadline_never_enters_writer_and_all_checkpoints_are_forwarded(
    active, monkeypatch,
):
    prepared, submit = active
    service = receipts.ProviderInputReceiptService(prepared.database)
    calls, records = 0, []

    def expired():
        nonlocal calls
        calls += 1
        if calls >= 5:
            raise ValueError("research_provider_callback_lifetime_elapsed")

    with pytest.raises(ValueError, match="lifetime_elapsed"):
        service.record_input(
            submit, request_check=expired,
            diagnostics=diag.ProviderCallbackDiagnostics(submit, "input", sink=records.append),
        )
    assert not any(item["phase"] in {"writer_wait", "commit"} for item in records)
    with prepared.database.session() as session:
        assert not session.scalars(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == submit.run_id,
            RunRoleOutputRecord.role.like("model_input:%"),
        )).all()

    forwarded = []
    original_prepare = receipts.prepare_originals
    original_verify = PreparedOriginals.verify
    original_after = PreparedOriginals.verify_after_writes

    def checked_prepare(*args, **kwargs):
        forwarded.append(("prepare", kwargs.get("checkpoint")))
        return original_prepare(*args, **kwargs)

    def checked_verify(self, *args, **kwargs):
        forwarded.append(("verify", kwargs.get("checkpoint")))
        return original_verify(self, *args, **kwargs)

    def checked_after(self, *args, **kwargs):
        forwarded.append(("after_writes", kwargs.get("checkpoint")))
        return original_after(self, *args, **kwargs)

    def check():
        return None

    monkeypatch.setattr(receipts, "prepare_originals", checked_prepare)
    monkeypatch.setattr(PreparedOriginals, "verify", checked_verify)
    monkeypatch.setattr(PreparedOriginals, "verify_after_writes", checked_after)
    saved = service.record_input(submit, request_check=check)
    assert saved.producer_authenticated is True
    assert forwarded == [("prepare", check), ("verify", check), ("after_writes", check)]
