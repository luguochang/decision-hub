"""Three finite private V2 acquisition controls; no real HTTP or model fixture."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from apps.hub_worker.composition import build_history_observed_worker
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_missing_acquisition_v2 import (
    HistoryMissingAcquisitionV2,
)
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2AttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2EpochRecord as Epoch,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_dataset_v2 import history_v2_id
from packages.kernel.decision_hub_kernel.ports.history_observed_training import fixed_cutoffs
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash, spot_scope
from packages.provider_adapters.market.history_missing_transport_v2 import HistoryMissingTransportV2

ROOT = Path(__file__).resolve().parents[2]
OWNER = "missing_v2_private_owner"


@pytest.fixture
def private(tmp_path, monkeypatch):
    async def reject(*_args, **_kwargs):
        raise AssertionError("private acquisition attempted real HTTP")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    database = Database("sqlite+pysqlite:///" + str(tmp_path / "private.sqlite3"))
    excluded = {
        "market_history_v2_originals",
        "market_history_v2_epochs",
        "market_history_v2_jobs",
        "market_history_v2_attempts",
    }
    Base.metadata.create_all(
        database.engine,
        tables=[table for name, table in Base.metadata.tables.items() if name not in excluded],
    )
    path = ROOT / "migrations/versions/0084_history_dataset_v2.py"
    spec = importlib.util.spec_from_file_location("private_acquisition_0084", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    clock = SimpleNamespace(offset=timedelta())

    def now():
        return datetime.now(UTC) + clock.offset

    commands = build_history_observed_worker(database, owner_id=OWNER, clock=now)
    originals = HistoryDatasetV2Service(
        commands, tmp_path, source_root=ROOT, clock=now, fixture_mode=True
    )
    known = now()
    cutoffs = fixed_cutoffs(known)
    start, end = cutoffs[-3] + timedelta(minutes=1), cutoffs[-3] + timedelta(minutes=4)
    interval = {
        "scope_id": spot_scope("BTC-USDT").scope_id,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "cursor": str(int(end.timestamp() * 1000)),
        "max_pages": 3,
    }
    with commands._read_session() as session:
        prepared = originals.prepare_original(
            session,
            kind="plan",
            owner_claim=OWNER,
            request_id="private_plan",
            plan_id=history_v2_id("plan", OWNER, "private_plan"),
            known_asof=known,
            cutoffs=cutoffs,
            provenance={
                "fixture_mode": True,
                "missing_intervals": [interval],
                "original_witnesses": [],
                "file_readset": [],
                "row_readset": [],
            },
        )
    with commands._write_session() as session:
        plan = originals.record_original(session, prepared)
    calls, mode = [], SimpleNamespace(value="ok")
    body = json.dumps(
        {
            "code": "0",
            "data": [
                [
                    str(int((start + timedelta(minutes=index)).timestamp() * 1000)),
                    "100",
                    "102",
                    "99",
                    "101",
                    "1",
                    "100",
                    "100",
                    "1",
                ]
                for index in (2, 1, 0)
            ],
        }
    ).encode()

    async def handler(request):
        calls.append(request)
        with commands._read_session() as session:
            attempt = session.scalar(select(Attempt).where(Attempt.state == "reserved"))
            assert attempt is not None and session.get(Job, attempt.job_id).state == "running"
            epoch = session.get(Epoch, attempt.epoch_id)
            assert epoch.attempts == session.scalar(
                select(func.count()).select_from(Attempt).where(Attempt.epoch_id == epoch.epoch_id)
            )
        if mode.value == "timeout":
            raise httpx.ReadTimeout("private timeout")
        return httpx.Response(200, stream=httpx.ByteStream(body))

    transport = HistoryMissingTransportV2(
        originals.raw, transport=httpx.MockTransport(handler), fixture_mode=True
    )
    acquisition = HistoryMissingAcquisitionV2(
        commands, originals, transport, fixture_mode=True, clock=now
    )
    epoch = acquisition.register_epoch(
        owner_claim=OWNER, request_id="private_epoch", plan_id=plan.original_id
    )
    env = SimpleNamespace(
        database=database,
        commands=commands,
        originals=originals,
        plan=plan,
        epoch=epoch,
        acquisition=acquisition,
        calls=calls,
        mode=mode,
        clock=clock,
        start=start,
        end=end,
        transport=transport,
    )
    try:
        yield env
    finally:
        database.engine.dispose()


def test_predebit_one_http_and_first_known_never_rewritten(private):
    env = private
    first = asyncio.run(
        env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    )
    assert len(env.calls) == 1 and first["attempt_state"] == "captured"
    assert first["admission_error_code"] is None and first["witness"]["origin"] == "missing_v2"
    with env.commands._read_session() as session:
        attempt = session.scalar(select(Attempt))
        original_result, original_hash = attempt.result_json, attempt.result_hash
        records = list(
            session.scalars(select(MarketCandleRecord).order_by(MarketCandleRecord.open_at))
        )
        assert len(records) == 3
        clocks = [
            (row.candle_id, row.candle_json, row.received_at, row.known_at) for row in records
        ]
        assert session.get(Epoch, env.epoch.original_id).attempts == 1
    repeated = env.acquisition.register_epoch(
        owner_claim=OWNER, request_id="private_epoch", plan_id=env.plan.original_id
    )
    assert (
        repeated.payload_hash == env.epoch.payload_hash
        and repeated.recorded_at == env.epoch.recorded_at
    )
    with pytest.raises(ValueError, match="request_conflict"):
        env.acquisition.register_epoch(
            owner_claim=OWNER, request_id="private_epoch", plan_id=env.plan.original_id, budget=1199
        )
    second_epoch = env.acquisition.register_epoch(
        owner_claim=OWNER, request_id="second_epoch", plan_id=env.plan.original_id
    )
    second = asyncio.run(
        env.acquisition.collect_one(epoch_id=second_epoch.original_id, owner_claim=OWNER)
    )
    assert len(env.calls) == 2 and second["attempt_state"] == "captured"
    with env.commands._read_session() as session:
        rows = list(
            session.scalars(select(MarketCandleRecord).order_by(MarketCandleRecord.open_at))
        )
        assert [
            (row.candle_id, row.candle_json, row.received_at, row.known_at) for row in rows
        ] == clocks
        first_attempt = session.get(Attempt, first["attempt_id"])
        assert (first_attempt.result_json, first_attempt.result_hash) == (
            original_result,
            original_hash,
        )
        assert session.scalar(select(func.count()).select_from(RawCaptureRecord)) == 2


def test_audit_survives_original_admission_failure_and_never_replays(private):
    env = private
    reserved = env.acquisition.claim(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    assert reserved is not None
    result = asyncio.run(
        env.transport.fetch_one(
            context_id=reserved.attempt_id,
            scope=spot_scope("BTC-USDT"),
            cursor=reserved.cursor,
            start_at=reserved.start_at,
            end_at=reserved.end_at,
        )
    )
    digest = result.capture["body_hash"]
    raw = env.originals.raw.root / digest[:2] / digest
    raw.write_bytes(b"private corrupted original")
    saved = env.acquisition._save_result(reserved, result)
    assert saved["admission_error_code"] == "history_v2_result_original_changed"
    with env.commands._read_session() as session:
        attempt = session.get(Attempt, reserved.attempt_id)
        assert attempt.state == "failed" and attempt.result_json is not None
        audited = json.loads(attempt.result_json)
        assert audited["error_code"] is None and audited["witness"]["page"]["rows"]
        assert session.get(RawCaptureRecord, result.capture["capture_id"]) is not None
        assert session.get(Epoch, env.epoch.original_id).state == "paused"
        assert session.scalar(select(func.count()).select_from(MarketCandleRecord)) == 0
    assert (
        asyncio.run(env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER))
        is None
    )
    assert len(env.calls) == 1


def test_two_transport_errors_pause_and_expired_unknown_lease_never_sends(private):
    env = private
    env.mode.value = "timeout"
    one = asyncio.run(
        env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    )
    assert one["error_code"] == "history_missing_transport_timeout"
    time.sleep(1.02)
    two = asyncio.run(
        env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    )
    assert two["error_code"] == "history_missing_transport_timeout" and len(env.calls) == 2
    with env.commands._read_session() as session:
        epoch = session.get(Epoch, env.epoch.original_id)
        assert epoch.state == "paused" and epoch.attempts == epoch.consecutive_transport_errors == 2
        assert session.scalar(select(func.count()).select_from(RawCaptureRecord)) == 2
    assert (
        asyncio.run(env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER))
        is None
    )
    lease_epoch = env.acquisition.register_epoch(
        owner_claim=OWNER, request_id="unknown_lease_epoch", plan_id=env.plan.original_id
    )
    reserved = env.acquisition.claim(epoch_id=lease_epoch.original_id, owner_claim=OWNER)
    assert reserved is not None
    env.clock.offset += timedelta(seconds=61)
    assert (
        asyncio.run(
            env.acquisition.collect_one(epoch_id=lease_epoch.original_id, owner_claim=OWNER)
        )
        is None
    )
    with env.commands._read_session() as session:
        assert session.get(Epoch, lease_epoch.original_id).state == "paused"
        assert session.get(Attempt, reserved.attempt_id).state == "send_unknown"
    assert len(env.calls) == 2


def test_captured_acquisition_freezes_cross_connection_and_rejects_rehashed_identity(private):
    env = private
    receipt = asyncio.run(
        env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    )
    assert receipt["attempt_state"] == "captured" and len(env.calls) == 1
    with env.commands._read_session() as session:
        prepared = env.originals.prepare_freeze(
            session,
            owner_claim=OWNER,
            request_id="acquired_dataset",
            plan_id=env.plan.original_id,
            epoch_ids=(env.epoch.original_id,),
        )
        assert len(prepared.value.provenance["selected_rows"][spot_scope("BTC-USDT").scope_id]) == 3
    with env.commands._write_session() as session:
        saved = env.originals.record_original(session, prepared)
    with env.commands._read_session() as session:
        loaded = env.originals.read_original(session, saved.original_id, OWNER)
        assert loaded == saved and loaded.financial_qualified is False
        assert loaded.provenance["epoch_originals"] == [
            [env.epoch.original_id, env.epoch.payload_hash]
        ]
    with env.commands._write_session() as session:
        attempt = session.get(Attempt, receipt["attempt_id"])
        changed = json.loads(attempt.result_json)
        changed["witness"]["attempt_id"] = "htry_unrelated_private_attempt"
        attempt.result_json = json.dumps(changed, sort_keys=True, separators=(",", ":"))
        attempt.result_hash = canonical_hash(changed)
    with env.commands._read_session() as session:
        with pytest.raises(ValueError, match="witness|attempt"):
            env.originals.prepare_freeze(
                session,
                owner_claim=OWNER,
                request_id="rehashed_identity_must_fail",
                plan_id=env.plan.original_id,
                epoch_ids=(env.epoch.original_id,),
            )
    assert len(env.calls) == 1
