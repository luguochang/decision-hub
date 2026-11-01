"""Three additive private resume controls; no public HTTP, reset or old fitting."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application.history_missing_resume_v2 import (
    HistoryMissingResumeV2,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2AttemptRecord as Attempt,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2EpochRecord as Epoch,
)
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryMissingV2JobRecord as Job,
)
from packages.kernel.decision_hub_kernel.persistence.history_missing_resume_v2_records import (
    HistoryMissingResumeV2Record,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_dataset_v2 import history_v2_id
from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope

ROOT = Path(__file__).resolve().parents[2]
OWNER = "missing_v2_private_owner"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


private = _load(
    ROOT / "tests/market_risk/test_history_missing_acquisition_v2.py", "resume_private_fixture"
).private


@pytest.fixture
def env(private):
    env = private
    # The reused three-bar fixture creates registered Base tables, including
    # this additive table. Install the actual new migration on the private DB.
    with env.database.engine.begin() as connection:
        HistoryMissingResumeV2Record.__table__.drop(connection)
        migration = _load(
            ROOT / "migrations/versions/0085_history_missing_resume_v2.py", "private_resume_0085"
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    intervals = []
    for asset in ("BTC-USDT", "ETH-USDT"):
        intervals.append(
            {
                "scope_id": spot_scope(asset).scope_id,
                "start_at": env.start.isoformat(),
                "end_at": env.end.isoformat(),
                "cursor": str(int(env.end.timestamp() * 1000)),
                "max_pages": 3,
            }
        )
    with env.commands._read_session() as session:
        prepared = env.originals.prepare_original(
            session,
            kind="plan",
            owner_claim=OWNER,
            request_id="resume_plan",
            plan_id=history_v2_id("plan", OWNER, "resume_plan"),
            known_asof=env.originals.now(),
            cutoffs=env.plan.cutoffs,
            provenance={
                "fixture_mode": True,
                "missing_intervals": intervals,
                "original_witnesses": [],
                "file_readset": [],
                "row_readset": [],
            },
        )
    with env.commands._write_session() as session:
        env.plan = env.originals.record_original(session, prepared)
    env.epoch = env.acquisition.register_epoch(
        owner_claim=OWNER, request_id="resume_epoch", plan_id=env.plan.original_id
    )
    env.resume = HistoryMissingResumeV2(env.acquisition)
    return env


def call(env, request):
    return env.resume.resume(
        owner_claim=OWNER,
        request_id=request,
        epoch_id=env.epoch.original_id,
        plan_id=env.plan.original_id,
        expected_epoch_hash=env.epoch.payload_hash,
        expected_epoch_request_id=env.epoch.request_id,
    )


def test_known_transport_pause_preserves_budget_cursor_retry_and_first_audit(env):
    env.mode.value = "timeout"
    asyncio.run(env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER))
    time.sleep(1.02)
    asyncio.run(env.acquisition.collect_one(epoch_id=env.epoch.original_id, owner_claim=OWNER))
    assert len(env.calls) == 2
    with env.commands._read_session() as session:
        jobs = list(
            session.scalars(
                select(Job).where(Job.epoch_id == env.epoch.original_id).order_by(Job.job_id)
            )
        )
        before = [
            (
                row.job_id,
                row.cursor,
                row.attempts,
                row.retry_used,
                row.generation,
                row.page_count,
                row.state,
            )
            for row in jobs
        ]
        assert session.get(Epoch, env.epoch.original_id).state == "paused"
    receipt = call(env, "known_pause_resume")
    assert receipt["outcome"] == "resumed" and receipt["after_epoch_state"] == "active"
    assert receipt["HTTP_attempts"] == 0 and receipt["after_attempts"] == 2
    assert receipt["before"]["deadline_at"] == env.epoch.provenance["deadline_at"]
    assert receipt["before"]["budget"] == env.epoch.provenance["budget"]
    with env.commands._read_session() as session:
        jobs = list(
            session.scalars(
                select(Job).where(Job.epoch_id == env.epoch.original_id).order_by(Job.job_id)
            )
        )
        assert [
            (
                row.job_id,
                row.cursor,
                row.attempts,
                row.retry_used,
                row.generation,
                row.page_count,
                row.state,
            )
            for row in jobs
        ] == before
        assert session.get(Epoch, env.epoch.original_id).consecutive_transport_errors == 0
    assert call(env, "known_pause_resume") == receipt and len(env.calls) == 2
    assert env.resume.read(owner_claim=OWNER, request_id="known_pause_resume") == receipt


def test_unknown_without_original_stays_blocked_and_never_http(env):
    reserved = env.acquisition.claim(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    assert reserved is not None
    env.clock.offset += __import__("datetime").timedelta(seconds=61)
    assert env.acquisition.claim(epoch_id=env.epoch.original_id, owner_claim=OWNER) is None
    receipt = call(env, "missing_original_must_not_resume")
    assert receipt["outcome"] == "blocked" and receipt["after_epoch_state"] == "paused"
    assert receipt["reason"] == "history_v2_resume_unknown_send_without_original"
    assert receipt["HTTP_attempts"] == 0 and len(env.calls) == 0
    with env.commands._read_session() as session:
        assert session.get(Attempt, reserved.attempt_id).state == "send_unknown"
        assert session.get(Job, reserved.job_id).cursor == reserved.cursor
        assert session.get(Epoch, env.epoch.original_id).attempts == 1


def test_full_a_original_reconciles_without_http_or_first_known_rewrite(env):
    reserved = env.acquisition.claim(epoch_id=env.epoch.original_id, owner_claim=OWNER)
    assert reserved is not None
    scope = next(
        spot_scope(asset)
        for asset in ("BTC-USDT", "ETH-USDT")
        if spot_scope(asset).scope_id == reserved.scope_id
    )
    result = asyncio.run(
        env.transport.fetch_one(
            context_id=reserved.attempt_id,
            scope=scope,
            cursor=reserved.cursor,
            start_at=reserved.start_at,
            end_at=reserved.end_at,
        )
    )
    env.acquisition._audit_result(reserved, result)
    # Preserve an already stored first bar and prove reconcile does not move it.
    bar = result.page.candles[0]
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash

    revision = canonical_hash(bar.semantic_payload())
    candle_id = "candle_" + canonical_hash([bar.scope_id, revision])[:48]
    accepted = bar.model_copy(update={"accepted_at": env.originals.now()})
    with env.commands._write_session() as session:
        session.add(
            MarketCandleRecord(
                candle_id=candle_id,
                scope_id=bar.scope_id,
                venue=bar.venue,
                instrument=bar.instrument,
                price_type=bar.price_type,
                interval_seconds=60,
                open_at=bar.open_at,
                close_at=bar.close_at,
                received_at=bar.received_at,
                known_at=accepted.known_at,
                revision_hash=revision,
                candle_json=accepted.model_dump_json(),
                capture_ref=bar.capture_ref,
                capture_hash=bar.capture_hash,
            )
        )
    with env.commands._read_session() as session:
        row = session.get(MarketCandleRecord, candle_id)
        original_clock = (row.candle_json, row.received_at, row.known_at)
        original_result = session.get(Attempt, reserved.attempt_id).result_json
    env.clock.offset += __import__("datetime").timedelta(seconds=61)
    assert env.acquisition.claim(epoch_id=env.epoch.original_id, owner_claim=OWNER) is None
    receipt = call(env, "complete_a_reconcile")
    assert receipt["outcome"] == "reconciled" and receipt["reconciled_attempts"] == [
        reserved.attempt_id
    ]
    assert receipt["HTTP_attempts"] == 0 and len(env.calls) == 1
    with env.commands._read_session() as session:
        row = session.get(MarketCandleRecord, candle_id)
        assert (row.candle_json, row.received_at, row.known_at) == original_clock
        attempt = session.get(Attempt, reserved.attempt_id)
        assert attempt.state == "captured" and attempt.result_json == original_result
        assert session.get(Job, reserved.job_id).attempts == 1
        assert session.get(Epoch, env.epoch.original_id).attempts == 1
    assert call(env, "complete_a_reconcile") == receipt and len(env.calls) == 1
