"""Finite hourly-slot controls; fake producer outputs never claim probability proof."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from apps.hub_api.history_owner_authority import HistoryOwnerAuthority
from apps.hub_api.history_product_routes import HistoryProductBootstrap
from apps.hub_worker.composition import build_history_observed_worker
from apps.hub_worker.history_hourly_v2 import (
    SCOPES,
    HistoryHourlyCommitUnconfirmed,
    HistoryHourlyV2,
    install_history_hourly_on_risk_lane,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastV2Service,
    forecast_id,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_model_v2 import (
    HistoryDatasetModelV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.history_hourly_v2_records import (
    HistoryHourlyV2SlotRecord as Slot,
)
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash


def setup(tmp_path, monkeypatch, *, granted=False):
    def reject(*_args, **_kwargs):
        raise AssertionError("hourly private control attempted provider HTTP")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    db = Database("sqlite+pysqlite:///" + (tmp_path / "private.sqlite3").as_posix())
    Base.metadata.create_all(db.engine, tables=[Slot.__table__, Forecast.__table__])
    owner = "hourly_private_control_owner"
    commands = build_history_observed_worker(db, owner_id=owner)
    clock = [datetime(2026, 10, 9, 16, 2, 5, tzinfo=UTC)]
    originals = HistoryDatasetV2Service(
        commands, tmp_path, fixture_mode=True, clock=lambda: clock[0]
    )
    service = (
        None
        if not granted
        else HistoryDatasetForecastV2Service(
            HistoryDatasetModelV2Service(originals),
            owner_claim=owner,
            grants={SCOPES[0]: "hfit2_" + "c" * 48},
        )
    )
    authority = HistoryOwnerAuthority(
        signing_key=b"h" * 32,
        pairing_sha256=hashlib.sha256(b"private-hour-control-token-000001").digest(),
        owner_identity=owner,
        origin="http://127.0.0.1:8894",
    )
    boot = HistoryProductBootstrap(authority, originals, forecasts=service, allow_producer=granted)
    return db, commands, clock, boot


def test_no_model_slots_reopen_unique_hour_and_source_corruption(tmp_path, monkeypatch):
    db, commands, clock, boot = setup(tmp_path, monkeypatch)
    tick = HistoryHourlyV2(boot)
    btc, eth = tick.process_one(), tick.process_one()
    assert btc.status == eth.status == "blocked"
    assert btc.reason_code == "history_hourly_trusted_model_not_configured"
    assert tick.process_one() is None
    reopened = HistoryHourlyV2(boot)
    assert reopened.process_one() is None
    with commands._read_session() as session:
        rows = session.scalars(select(Slot).order_by(Slot.scope_id)).all()
        assert len(rows) == 2 and all(row.fixture_mode for row in rows)
        before = tuple(row.receipt_json for row in rows)
        assert session.scalar(select(func.count()).select_from(Forecast)) == 0
        ref = rows[0].source_original_ref
    clock[0] += timedelta(hours=1)
    assert reopened.process_one().status == "blocked"
    with commands._read_session() as session:
        assert (
            tuple(
                session.scalars(
                    select(Slot.receipt_json)
                    .where(Slot.slot_id.in_([btc.slot_id, eth.slot_id]))
                    .order_by(Slot.scope_id)
                ).all()
            )
            == before
        )
    from pathlib import Path

    target = Path(ref)
    body = target.read_bytes()
    target.write_bytes(body + b" ")
    try:
        with commands._read_session() as session:
            with pytest.raises(ValueError, match="history_observed_archive_hash_changed"):
                tick._read_slot(session, btc.slot_id)
    finally:
        target.write_bytes(body)
    db.engine.dispose()


@pytest.mark.parametrize("committed", [True, False])
def test_reservation_actual_commit_ack_loss_fence_survives_restart(
    tmp_path, monkeypatch, committed
):
    db, commands, clock, boot = setup(tmp_path, monkeypatch)
    writer = commands._write_session
    counts = {"writes": 0}

    @contextmanager
    def lost_ack():
        counts["writes"] += 1
        if not committed:
            raise OSError("private reservation did not return a COMMIT result")
        with writer() as session:
            yield session
        raise OSError("private actual reservation COMMIT acknowledgement lost")

    monkeypatch.setattr(commands, "_write_session", lost_ack)
    tick = HistoryHourlyV2(boot)
    with pytest.raises(HistoryHourlyCommitUnconfirmed):
        tick.process_one()
    with commands._read_session() as session:
        assert session.scalar(select(func.count()).select_from(Slot)) == int(committed)
    monkeypatch.setattr(commands, "_write_session", writer)
    reopened = HistoryHourlyV2(boot)
    assert reopened.process_one().scope_id == SCOPES[1]
    clock[0] += timedelta(hours=1)
    assert reopened.process_one().scope_id == SCOPES[1]
    assert counts["writes"] == 1
    assert reopened._fence_path(SCOPES[0]).is_file()
    assert reopened.last_fence_reason == "history_hourly_unconfirmed_fence_requires_reconciliation"
    with commands._read_session() as session:
        assert session.scalar(select(func.count()).select_from(Forecast)) == 0
        assert session.scalar(
            select(func.count()).select_from(Slot).where(Slot.scope_id == SCOPES[0])
        ) == int(committed)
    db.engine.dispose()


def fake_producer(boot, monkeypatch):
    """Unit branch double: marker row is deliberately not a valid forecast original."""
    calls = {"prepare": 0, "record": 0, "read": 0}
    service = boot.forecasts
    result = [None]

    def prepare(session, *, scope_id, request_id, cutoff_at):
        calls["prepare"] += 1
        result[0] = SimpleNamespace(
            forecast_id=forecast_id(service.owner_claim, request_id),
            scope_id=scope_id,
            model_id=service.grants[scope_id],
            cutoff_at=cutoff_at,
            request_id=request_id,
        )
        return result[0]

    def record(session, value):
        calls["record"] += 1
        session.add(
            Forecast(
                forecast_id=value.forecast_id,
                owner_claim=service.owner_claim,
                request_id=value.request_id,
                scope_id=value.scope_id,
                model_id=value.model_id,
                model_hash="0" * 64,
                cutoff_at=value.cutoff_at,
                payload_json="{}",
                payload_hash="0" * 64,
                original_ref="private-unit-marker-not-original",
                original_hash="0" * 64,
                produced_at=boot.originals.now(),
                recorded_at=boot.originals.now(),
            )
        )
        session.flush()
        return value

    def read(session, identifier):
        calls["read"] += 1
        assert identifier == result[0].forecast_id
        return result[0]

    monkeypatch.setattr(service, "prepare", prepare)
    monkeypatch.setattr(service, "record", record)
    monkeypatch.setattr(service, "read", read)
    return calls, result


@pytest.mark.parametrize("failure", ["forecast_ack", "receipt_ack", "none"])
def test_dispatch_unknown_commits_no_replay_and_firstsave(tmp_path, monkeypatch, failure):
    db, commands, clock, boot = setup(tmp_path, monkeypatch, granted=True)
    calls, _ = fake_producer(boot, monkeypatch)
    writer = commands._write_session
    writers = [0]

    @contextmanager
    def selected_lost_ack():
        writers[0] += 1
        with writer() as session:
            yield session
        if writers[0] == {"forecast_ack": 2, "receipt_ack": 3}.get(failure):
            raise OSError("private actual COMMIT acknowledgement lost")

    monkeypatch.setattr(commands, "_write_session", selected_lost_ack)
    tick = HistoryHourlyV2(boot)
    if failure == "receipt_ack":
        with pytest.raises(HistoryHourlyCommitUnconfirmed):
            tick.process_one()
    else:
        result = tick.process_one()
        assert result.status == (
            "unconfirmed" if failure == "forecast_ack" else "committed_verified"
        )
    assert writers[0] == 3 and calls["record"] == 1
    with commands._read_session() as session:
        btc = session.scalar(select(Slot).where(Slot.scope_id == SCOPES[0]))
        first_receipt, first_forecast = btc.receipt_json, btc.forecast_id
        assert session.scalar(select(func.count()).select_from(Forecast)) == 1
    monkeypatch.setattr(commands, "_write_session", writer)
    restarted = HistoryHourlyV2(boot)
    assert restarted.process_one().scope_id == SCOPES[1]
    assert restarted.process_one() is None
    assert calls["prepare"] == calls["record"] == 1
    with commands._read_session() as session:
        btc = session.scalar(select(Slot).where(Slot.scope_id == SCOPES[0]))
        assert (btc.receipt_json, btc.forecast_id) == (first_receipt, first_forecast)
    assert restarted._fence_path(SCOPES[0]).exists() is (failure != "none")
    db.engine.dispose()


def test_existing_forecast_receipt_ack_loss_does_not_write_second_receipt(tmp_path, monkeypatch):
    db, commands, clock, boot = setup(tmp_path, monkeypatch, granted=True)
    calls, _ = fake_producer(boot, monkeypatch)
    cutoff = clock[0].replace(second=0, microsecond=0)
    request = (
        "auto_"
        + canonical_hash([boot.forecasts.grants[SCOPES[0]], SCOPES[0], cutoff.isoformat()])[:48]
    )
    with commands._read_session() as session:
        value = boot.forecasts.prepare(
            session, scope_id=SCOPES[0], request_id=request, cutoff_at=cutoff
        )
    with commands._write_session() as session:
        boot.forecasts.record(session, value)
    writer = commands._write_session
    count = [0]

    @contextmanager
    def receipt_ack_lost():
        count[0] += 1
        with writer() as session:
            yield session
        if count[0] == 2:
            raise OSError("private existing-firstsave receipt COMMIT acknowledgement lost")

    monkeypatch.setattr(commands, "_write_session", receipt_ack_lost)
    tick = HistoryHourlyV2(boot)
    with pytest.raises(HistoryHourlyCommitUnconfirmed):
        tick.process_one()
    assert count[0] == 2 and calls["record"] == calls["prepare"] == 1
    assert tick._fence_path(SCOPES[0]).is_file()
    with commands._read_session() as session:
        row = session.scalar(select(Slot))
        assert row.status == "committed_verified" and row.forecast_id == value.forecast_id
    db.engine.dispose()


def test_managed_hook_preserves_callback_and_cancel_joins_worker_error(tmp_path, monkeypatch):
    db, _, _, boot = setup(tmp_path, monkeypatch)
    old_calls = []

    async def old():
        old_calls.append(True)
        return 7

    realtime = SimpleNamespace(scheduler=SimpleNamespace(risk_enhancement_tick=old))
    tick = install_history_hourly_on_risk_lane(realtime, db, bootstrap=boot)
    entered, left = threading.Event(), threading.Event()

    def fails_when_cancelled(*, cancel, budget_seconds):
        entered.set()
        while not cancel.wait(0.005):
            pass
        time.sleep(0.03)
        left.set()
        raise OSError("private worker audit failure while cancelling")

    async def exercise():
        assert await realtime.scheduler.risk_enhancement_tick() == 8
        assert old_calls == [True]
        with pytest.raises(ValueError, match="history_hourly_risk_lane_already_bound"):
            install_history_hourly_on_risk_lane(realtime, db, bootstrap=boot)
        monkeypatch.setattr(tick, "process_one", fails_when_cancelled)
        task = asyncio.create_task(tick.tick())
        while not entered.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert left.is_set() and tick._busy is False

    asyncio.run(exercise())
    db.engine.dispose()
