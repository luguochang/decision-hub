"""Actual callback header contract; private SQLite, no model or financial output."""

import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Request

from apps.hub_api.model_binding_request import bind_model_request
from tests.dsh_native.test_original_research_proof_lifetime import (
    PrivateDatabase,
    payload,
    request,
)

WITNESS = "e" * 64


def native_request(witness=WITNESS):
    original = request(deadline=datetime.now(UTC) + timedelta(seconds=10), budget_ms=10_000)
    scope = dict(original.scope)
    scope.update(
        method="PUT",
        path=f"/v1/dsh/sessions/{payload().run_id}/model-binding",
        headers=[*scope["headers"], (b"x-decision-hub-harness-witness", witness.encode())],
    )

    async def receive():
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_native_deadline_and_witness_reach_one_owned_binding_with_writer_and_source_lease(
    tmp_path,
):
    database = PrivateDatabase(tmp_path, remaining=10)
    caller = threading.get_ident()
    calls = []

    def bind(value, *, request_check, diagnostics, writer_start, source_lease,
             harness_witness_hash):
        assert threading.get_ident() != caller
        assert harness_witness_hash == WITNESS
        if os.name == "nt":
            assert source_lease is not None
        calls.append(value)
        request_check()
        with database.session() as session:
            writer_start()
            session.connection.execute("INSERT INTO effects VALUES (1)")
            request_check()
        return value

    result = await bind_model_request(
        native_request(), SimpleNamespace(database=database, bind=bind), payload(),
    )
    assert result == payload() and calls == [payload()]
    assert database.count() == 1 and database.live == 0


@pytest.mark.asyncio
async def test_a_witness_header_does_not_override_the_original_service_rejection(tmp_path):
    database = PrivateDatabase(tmp_path, remaining=10)
    calls = []

    def bind(value, *, harness_witness_hash, **options):
        assert harness_witness_hash == WITNESS
        calls.append(value)
        options["request_check"]()
        raise ValueError("harness_root_witness_identity_mismatch")

    with pytest.raises(ValueError, match="^harness_root_witness_identity_mismatch$"):
        await bind_model_request(
            native_request(), SimpleNamespace(database=database, bind=bind), payload(),
        )
    assert calls == [payload()] and database.count() == 0 and database.live == 0


@pytest.mark.asyncio
async def test_malformed_witness_cannot_reach_the_binding_service(tmp_path):
    database = PrivateDatabase(tmp_path, remaining=10)

    def bind(*args, **kwargs):
        pytest.fail("malformed witness must not reach service")

    with pytest.raises(ValueError, match="^harness_root_witness_header_invalid$"):
        await bind_model_request(
            native_request("invalid"), SimpleNamespace(database=database, bind=bind), payload(),
        )
    assert database.count() == 0 and database.live == 0
