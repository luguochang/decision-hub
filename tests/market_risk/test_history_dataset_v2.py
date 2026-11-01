"""Finite private DatasetV2 controls: crossRID raw evidence and fresh readback."""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, func, select
from sqlalchemy.exc import InvalidRequestError

from apps.hub_worker.composition import build_history_observed_worker
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_v2_records import (
    HistoryDatasetV2OriginalRecord as Original,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_dataset_v2 import history_v2_id
from packages.kernel.decision_hub_kernel.ports.history_observed_training import fixed_cutoffs
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)

ROOT = Path(__file__).resolve().parents[2]
OWNER = "dataset_v2_private_owner"


@pytest.fixture
def private(tmp_path, monkeypatch):
    async def no_http(*_args, **_kwargs):
        raise AssertionError("private dataset control attempted HTTP")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_http)
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
    spec = importlib.util.spec_from_file_location(
        "private_dataset_0084", ROOT / "migrations/versions/0084_history_dataset_v2.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with database.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    commands = build_history_observed_worker(database, owner_id=OWNER)
    originals = HistoryDatasetV2Service(commands, tmp_path, fixture_mode=True)
    now = datetime.now(UTC)
    cutoffs = fixed_cutoffs(now)
    day = cutoffs[-3]
    received, recorded, accepted = (
        now - timedelta(seconds=30),
        now - timedelta(seconds=29),
        now - timedelta(seconds=28),
    )
    witnesses, files, rowset = [], {}, []
    for asset in ("BTC-USDT", "ETH-USDT"):
        scope = spot_scope(asset)
        for phase in ("feature", "label"):
            start = (
                day - timedelta(minutes=31) if phase == "feature" else day + timedelta(minutes=1)
            )
            end = day if phase == "feature" else day + timedelta(days=1, minutes=1)
            cursor, remaining = int(end.timestamp() * 1000), int((end - start).total_seconds() / 60)
            while remaining:
                size = min(remaining, 300)
                stamps = [cursor - (index + 1) * 60000 for index in range(size)]
                payload = {
                    "code": "0",
                    "data": [
                        [str(stamp), "100", "102", "99", "101", "1", "100", "100", "1"]
                        for stamp in stamps
                    ],
                }
                digest, ref = originals.raw.put(encoded(payload))
                identity = "private_" + asset + "_" + phase + "_" + str(cursor)
                capture = RawCaptureRecord(
                    capture_id=identity,
                    context_id=identity,
                    url="https://www.okx.com/api/v5/market/history-candles",
                    status_code=200,
                    body_hash=digest,
                    body_ref=ref,
                    byte_count=len(encoded(payload)),
                    media_type="application/json",
                    adapter_version="okx-closed-spot-path.v1",
                    error_code=None,
                    started_at=received - timedelta(seconds=1),
                    completed_at=received,
                    recorded_at=recorded,
                )
                with database.session() as session:
                    session.add(capture)
                    for index, stamp in enumerate(stamps):
                        opened = datetime.fromtimestamp(stamp / 1000, UTC)
                        bar = MarketCandle(
                            scope_id=scope.scope_id,
                            venue=scope.venue,
                            instrument=scope.instrument,
                            open_at=opened,
                            close_at=opened + timedelta(minutes=1),
                            open=Decimal("100"),
                            high=Decimal("102"),
                            low=Decimal("99"),
                            close=Decimal("101"),
                            volume_base=Decimal("1"),
                            volume_quote=Decimal("100"),
                            received_at=received,
                            accepted_at=accepted,
                            capture_ref=ref,
                            capture_hash=digest,
                            row_index=index,
                        )
                        revision = canonical_hash(bar.semantic_payload())
                        session.add(
                            MarketCandleRecord(
                                candle_id="candle_"
                                + canonical_hash([scope.scope_id, revision])[:48],
                                scope_id=scope.scope_id,
                                venue=scope.venue,
                                instrument=scope.instrument,
                                price_type="trade",
                                interval_seconds=60,
                                open_at=bar.open_at,
                                close_at=bar.close_at,
                                received_at=received,
                                known_at=bar.known_at,
                                revision_hash=revision,
                                candle_json=encoded(bar.model_dump(mode="json")).decode(),
                                capture_ref=ref,
                                capture_hash=digest,
                            )
                        )
                with commands._read_session() as session:
                    actual = originals._row(session, "raw_captures", identity)
                pairs = originals._pairs(
                    encoded(payload), scope.scope_id, start, end, str(cursor), received
                )
                witnesses.append(
                    {
                        "origin": "registered_v1",
                        "registration_id": "private_R1" if phase == "feature" else "private_R2",
                        "job_id": identity,
                        "scope_id": scope.scope_id,
                        "start_at": start.isoformat(),
                        "end_at": end.isoformat(),
                        "page": {
                            "phase": phase,
                            "cursor": str(cursor),
                            "next_cursor": str(min(stamps)) if remaining > size else None,
                            "capture_ref": ref,
                            "capture_hash": digest,
                            "received_at": received.isoformat(),
                            "rows": pairs,
                        },
                        "capture": actual,
                        "producer_sources": originals.require_sources(),
                    }
                )
                files[str(tmp_path / "raw-captures" / digest[:2] / digest)] = digest
                rowset.append(("raw_captures", identity, canonical_hash(actual)))
                cursor, remaining = min(stamps), remaining - size
    plan_id = history_v2_id("plan", OWNER, "private_plan")
    with commands._read_session() as session:
        prepared = originals.prepare_original(
            session,
            kind="plan",
            owner_claim=OWNER,
            request_id="private_plan",
            plan_id=plan_id,
            known_asof=now,
            cutoffs=cutoffs,
            provenance={
                "fixture_mode": True,
                "base_registration_ids": ["private_R1", "private_R2"],
                "original_witnesses": witnesses,
                "missing_intervals": [],
                "file_readset": sorted(files.items()),
                "row_readset": rowset,
            },
            files=tuple(sorted(files.items())),
            rows=tuple(rowset),
        )
    with commands._write_session() as session:
        plan = originals.record_original(session, prepared)
    env = SimpleNamespace(
        database=database,
        commands=commands,
        originals=originals,
        plan=plan,
        migration=migration,
        day=day,
    )
    try:
        yield env
    finally:
        database.engine.dispose()


def test_cross_registration_first_known_freeze_and_fresh_read(private):
    env = private
    with env.commands._read_session() as session:
        prepared = env.originals.prepare_freeze(
            session, owner_claim=OWNER, request_id="private_freeze", plan_id=env.plan.original_id
        )
    assert len(prepared.value.provenance["windows"]) == 180
    complete = [
        item for item in prepared.value.provenance["windows"] if item["status"] == "complete"
    ]
    assert len(complete) == 2 and all(item["cutoff_at"] == env.day.isoformat() for item in complete)
    assert {
        item["registration_id"] for item in prepared.value.provenance["original_witnesses"]
    } == {"private_R1", "private_R2"}
    assert all(len(rows) == 1471 for rows in prepared.value.provenance["selected_rows"].values())
    with env.commands._write_session() as session:
        saved = env.originals.record_original(session, prepared)
    with env.commands._read_session() as session:
        loaded = env.originals.read_original(session, saved.original_id, OWNER)
        assert loaded == saved and loaded.financial_qualified is False
        assert session.scalar(select(func.count()).select_from(Original)) == 2
    with env.commands._write_session() as session:
        same = env.originals.record_original(session, prepared)
    assert same == saved and same.recorded_at == saved.recorded_at
    changed = replace(
        prepared, value=prepared.value.model_copy(update={"provenance": {"forged": True}})
    )
    with pytest.raises(ValueError, match="history_v2_preparation_changed"):
        with env.commands._write_session() as session:
            env.originals.record_original(session, changed)


def test_raw_and_first_known_metadata_changed_between_prepare_writer_rejected(private):
    env = private
    with env.commands._read_session() as session:
        prepared = env.originals.prepare_freeze(
            session, owner_claim=OWNER, request_id="private_negative", plan_id=env.plan.original_id
        )
    raw_path = Path(env.plan.provenance["file_readset"][0][0])
    original_bytes = raw_path.read_bytes()
    try:
        raw_path.write_bytes(original_bytes + b" ")
        with pytest.raises(ValueError, match="history_v2_full_original_changed"):
            with env.commands._write_session() as session:
                env.originals.record_original(session, prepared)
    finally:
        raw_path.write_bytes(original_bytes)
    foreign = HistoryDatasetV2Service(env.commands, env.originals.data_directory, fixture_mode=True)
    with pytest.raises(ValueError, match="history_v2_preparation_changed"):
        with env.commands._write_session() as session:
            foreign.record_original(session, prepared)
    candle = next(item for item in prepared.rows if item[0] == "market_candles")
    with env.database.session() as session:
        row = session.get(MarketCandleRecord, candle[1])
        row.candle_json += " "
    with pytest.raises(ValueError, match="history_v2_original_metadata_changed"):
        with env.commands._write_session() as session:
            env.originals.record_original(session, prepared)
    with env.commands._read_session() as session:
        assert session.scalar(select(func.count()).select_from(Original)) == 1


def test_migration_preserves_nonempty_original_and_unknown_commit_not_replayed(private):
    env = private
    with env.database.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(
                ValueError, match="history_v2_nonempty_originals_and_attempts_preserved"
            ):
                env.migration.downgrade()
    with env.commands._read_session() as session:
        prepared = env.originals.prepare_freeze(
            session,
            owner_claim=OWNER,
            request_id="private_unknown_commit",
            plan_id=env.plan.original_id,
        )
    committed_calls = []

    def lose_ack(_session):
        committed_calls.append(1)
        raise RuntimeError("private_committed_acknowledgment_lost")

    event.listen(env.database.factory, "after_commit", lose_ack)
    try:
        with pytest.raises((RuntimeError, InvalidRequestError)) as error:
            with env.commands._write_session() as session:
                saved = env.originals.record_original(session, prepared)
        assert "private_committed_acknowledgment_lost" in str(error.value) or (
            error.value.__context__ is not None
            and "private_committed_acknowledgment_lost" in str(error.value.__context__)
        )
    finally:
        event.remove(env.database.factory, "after_commit", lose_ack)
    assert committed_calls == [1]
    # Actual COMMIT succeeded and its acknowledgement raised. Recovery reads
    # the first original on a new connection; it never runs a second writer.
    env.database.engine.dispose()
    with env.commands._read_session() as session:
        loaded = env.originals.read_original(session, saved.original_id, OWNER)
        assert loaded == saved
        assert session.scalar(select(func.count()).select_from(Original)) == 2
    assert (
        hashlib.sha256(Path(saved.source_original_ref).read_bytes()).hexdigest()
        == saved.source_original_hash
    )
