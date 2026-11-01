"""Finite integration controls; private model qualification remains synthetic."""

import asyncio
import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, select
from starlette.requests import Request

from apps.hub_api.history_outcome_index_routes import history_outcome_index_router
from apps.hub_api.history_outcome_routes import OutcomeBootstrapV2
from apps.hub_api.history_owner_authority import HISTORY_COOKIE, HistoryOwnerAuthority
from apps.hub_api.history_product_routes import HistoryProductBootstrap
from apps.hub_worker.history_outcome_runtime40 import install_history_outcome40_on_risk_lane
from packages.contracts_py.decision_hub_contracts.generated.history_outcome_index_v2 import (
    HistoryOutcomeIndexV2,
)
from packages.kernel.decision_hub_kernel.application.history_outcome_jobs_v2 import (
    HistoryOutcomeJobsV2,
    HistoryOutcomeJobUnconfirmed,
)
from packages.kernel.decision_hub_kernel.application.history_outcome_recovery_v2 import (
    HistoryOutcomeRecoveryV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import as_utc
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeObservationV2Record as Observation,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeOpportunityV2Record as Opportunity,
)
from packages.kernel.decision_hub_kernel.persistence.history_outcome_v2_records import (
    HistoryOutcomeProtocolV2Record as Protocol,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import (
    MarketCandleRecord as Candle,
)
from tests.market_risk.test_history_outcome_jobs40 import publication
from tests.market_risk.test_history_outcome_service40 import fixture40, insert_exact_1440


def test_model_issuer_actual_tuple_shape_rejects_previous_mock_reply_before_write(
    tmp_path, monkeypatch
):
    from packages.kernel.decision_hub_kernel.application import history_outcome_v2

    env = fixture40(tmp_path, monkeypatch)

    class PreviousIncorrectMockShape:
        def __init__(self, _writer):
            pass

        def issue(self, _session, _identifier):
            return SimpleNamespace(model_bytes=b"{}")

    monkeypatch.setattr(history_outcome_v2, "TransferAuthority39", PreviousIncorrectMockShape)
    with pytest.raises(ValueError, match="actual_model_issuer_reply"):
        env.service.register("bad-issuer-shape")
    with env.service.commands._read_session() as session:
        assert session.scalar(select(Protocol.protocol_id)) is None


def boot(env, protocol):
    token, origin = "e" * 32, "http://127.0.0.1:8074"
    authority = HistoryOwnerAuthority(
        signing_key=b"s" * 32,
        pairing_sha256=hashlib.sha256(token.encode()).digest(),
        owner_identity=env.service.owner_claim,
        origin=origin,
    )
    cookie = authority.pair(token, origin=origin).cookie_value
    history = HistoryProductBootstrap(
        authority, env.service.originals, forecasts=env.service.forecasts
    )
    bootstrap = OutcomeBootstrapV2(history, env.service, (protocol.protocol_id,))

    def request(supplied=cookie):
        return Request(
            dict(
                type="http",
                method="GET",
                path="/v1/history/evaluations",
                headers=[
                    (b"origin", origin.encode()),
                    (b"cookie", f"{HISTORY_COOKIE}={supplied}".encode()),
                ],
                query_string=b"",
            )
        )

    return bootstrap, request


@pytest.mark.parametrize("lost_finish_ack", [False, True])
def test_actual_ro_rejection_consumes_fixed_attempt_but_unknown_finish_stays_fenced(
    tmp_path, monkeypatch, lost_finish_ack
):
    env = fixture40(tmp_path, monkeypatch)
    protocol, jobs, identifier = publication(env, "recovery-original")
    with env.service.commands._read_session() as session:
        row = session.get(Opportunity, identifier)
        cutoff = as_utc(row.label_cutoff_at)
        deadline = as_utc(row.label_deadline_at)
        env.clock[0] = as_utc(row.next_due_at)
    insert_exact_1440(env, row, cutoff)
    # One actual private native row contradicts its frozen semantic candle.
    # This goes through the untouched full loader, not a patched error method.
    with env.db.engine.begin() as connection:
        connection.execute(
            Candle.__table__.update()
            .where(Candle.scope_id == row.scope_id, Candle.open_at == cutoff + timedelta(minutes=1))
            .values(revision_hash="f" * 64)
        )
    recovery = HistoryOutcomeRecoveryV2(jobs)
    original = env.service.commands._write_session
    writes = []

    @contextmanager
    def finish_ack_lost():
        writes.append(1)
        with original() as session:
            yield session
        if len(writes) == 2:
            raise OSError("synthetic actual finish post-COMMIT ACK loss")

    if lost_finish_ack:
        monkeypatch.setattr(env.service.commands, "_write_session", finish_ack_lost)
        with pytest.raises(HistoryOutcomeJobUnconfirmed):
            recovery.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
        assert writes == [1, 1] and jobs._fence(identifier).exists()
    else:
        value = recovery.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
        assert value["outcome"] == "readonly_prepare_rejected" and value["attempt"] == 1
        assert value["error_code"] == "history_observed_candle_identity_or_clock_changed"
        assert not jobs._fence(identifier).exists()
        assert (
            json.loads(env.service.archive.read(value["rejection_ref"], value["rejection_hash"]))[
                "subject_save_attempted"
            ]
            is False
        )
    with env.service.commands._read_session() as session:
        current = session.get(Opportunity, identifier)
        assert current.attempts == 1 and current.generation == 2 and current.state == "ready"
        assert current.lease_owner is None and as_utc(current.label_deadline_at) == deadline
        assert as_utc(current.next_due_at) == cutoff + timedelta(days=1, minutes=16)
        assert not session.scalars(
            select(Observation).where(Observation.kind == "observation")
        ).all()
    assert len(tuple((env.service.archive.root / "job-rejections").glob("*.json"))) == 1
    fresh = HistoryOutcomeRecoveryV2(HistoryOutcomeJobsV2(env.service))
    if lost_finish_ack:
        # The same committed attempt is not replayed even after its due offset.
        env.clock[0] = cutoff + timedelta(days=1, minutes=16)
        monkeypatch.setattr(env.service.commands, "_write_session", original)
        with pytest.raises(HistoryOutcomeJobUnconfirmed, match="requires_reconciliation"):
            fresh.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)
    else:
        assert (
            fresh.process_one(protocol.protocol_id, "observe", opportunity_id=identifier)["outcome"]
            == "no_due_opportunity"
        )


def test_signed_index_actual_native_metadata_no_deep_or_archive_write(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("index-private")
    bootstrap, request = boot(env, protocol)
    # Explicit synthetic metadata projection, never financial qualification.
    with env.db.engine.begin() as connection:
        connection.execute(Protocol.__table__.update().values(fixture_mode=False))
    mutations, statements = [], []

    def trace(_conn, _cursor, statement, *_args):
        statements.append(statement)
        if (
            statement.lstrip()
            .upper()
            .startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))
        ):
            mutations.append(statement)

    event.listen(env.db.engine, "before_cursor_execute", trace)
    monkeypatch.setattr(env.service.archive, "put", lambda _value: pytest.fail("GET archive.put"))
    monkeypatch.setattr(
        env.service.archive, "read", lambda *_args: pytest.fail("index deep original")
    )
    try:
        endpoint = history_outcome_index_router(bootstrap).routes[0].endpoint
        reply = endpoint(request())
        assert reply.evaluations[0].protocol_id == protocol.protocol_id
        assert reply.evaluations[0].state == "confirmed" and not reply.current_originals_verified
        selects = [sql for sql in statements if "SELECT protocol_id,owner_claim" in sql]
        assert len(selects) == 1 and not mutations
        with pytest.raises(HTTPException) as caught:
            endpoint(request("forged"))
        assert caught.value.status_code == 403
        with pytest.raises(HTTPException) as caught:
            history_outcome_index_router(None).routes[0].endpoint(request())
        assert caught.value.status_code == 503
        value = reply.model_dump(mode="json")
        value["evaluations"][0]["first_hour"] = protocol.first_hour.isoformat().replace(
            ":00+", ":00.000001+"
        )
        with pytest.raises(ValidationError):
            HistoryOutcomeIndexV2.model_validate_json(json.dumps(value))
    finally:
        event.remove(env.db.engine, "before_cursor_execute", trace)


def test_managed_callback_first_one_action_and_cancel_thread_join(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("runtime-private")
    bootstrap, _request = boot(env, protocol)
    order = []

    async def original():
        order.append("risk-first")
        return 7

    scheduler = SimpleNamespace(risk_enhancement_tick=original)
    realtime = SimpleNamespace(scheduler=scheduler)
    runtime = install_history_outcome40_on_risk_lane(realtime, bootstrap=bootstrap)
    # Real reservation lookup before first H returns no work and writes none.
    assert asyncio.run(scheduler.risk_enhancement_tick()) == 7
    assert order == ["risk-first"] and runtime.last_receipt["outcome"] == "no_due_opportunity"
    with pytest.raises(ValueError, match="already_bound"):
        install_history_outcome40_on_risk_lane(realtime, bootstrap=bootstrap)
    entered, exited = threading.Event(), threading.Event()

    def wait_for_cancel(_protocol, *, budget_seconds, checkpoint):
        entered.set()
        try:
            while True:
                checkpoint()
                exited.wait(0.005)
        finally:
            exited.set()

    monkeypatch.setattr(runtime.recovery, "tick", wait_for_cancel)

    async def cancel():
        task = asyncio.create_task(runtime.tick(1))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.sleep(0.001)
        task.cancel()
        assert await runtime.tick(1) == 0  # previous finite reader is still owned
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()

    asyncio.run(cancel())


def test_automatic_final_score_all48_missing_once_and_unknown_ack_no_replay(tmp_path, monkeypatch):
    env = fixture40(tmp_path, monkeypatch)
    protocol = env.service.register("runtime-final-private")
    bootstrap, _request = boot(env, protocol)
    realtime = SimpleNamespace(scheduler=SimpleNamespace(risk_enhancement_tick=None))
    runtime = install_history_outcome40_on_risk_lane(realtime, bootstrap=bootstrap)
    env.clock[0] = protocol.last_hour + timedelta(days=2, minutes=6)
    assert asyncio.run(runtime.tick(120)) == 1
    assert runtime.last_receipt["outcome"] == "final_score_saved"
    assert (
        runtime.last_receipt["expected_total"] == 48 and runtime.last_receipt["paired_count"] == 0
    )
    score_id = runtime.last_receipt["score_id"]
    assert asyncio.run(runtime.tick(120)) == 0
    assert runtime.last_receipt["outcome"] == "final_score_already_saved"
    with env.service.commands._read_session() as session:
        score = env.service.read_score(session, score_id)
        assert score.expected_total == 48 and sum(score.publication_counts.values()) == 48
        assert score.paired_count == 0 and score.ci is None
    # A different exact protocol uses the same real commit boundary but loses
    # its ACK. Its saved row cannot cause the runtime fence to be cleared.
    env.clock[0] = protocol.registered_at
    other = env.service.register("runtime-final-unknown")
    otherboot, _request = boot(env, other)
    other_runtime = install_history_outcome40_on_risk_lane(
        SimpleNamespace(scheduler=SimpleNamespace(risk_enhancement_tick=None)), bootstrap=otherboot
    )
    env.clock[0] = other.last_hour + timedelta(days=2, minutes=6)
    original = env.service.commands._write_session
    writes = []

    @contextmanager
    def ack_lost():
        writes.append(1)
        with original() as session:
            yield session
        raise OSError("synthetic actual final score ACK loss")

    monkeypatch.setattr(env.service.commands, "_write_session", ack_lost)
    assert asyncio.run(other_runtime.tick(120)) == 0
    assert other_runtime.last_receipt["outcome"] == "unconfirmed" and writes == [1]
    monkeypatch.setattr(env.service.commands, "_write_session", original)
    assert asyncio.run(other_runtime.tick(120)) == 0 and writes == [1]
    assert other_runtime.last_receipt["outcome"] == "unconfirmed"
