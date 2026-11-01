"""One finite60-day positive math fixture; no ROOT DB, provider or old trial."""

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
from sqlalchemy import func, select

from apps.hub_worker.composition import build_history_observed_worker
from packages.kernel.decision_hub_kernel.application.history_dataset_model_v2 import (
    HistoryDatasetModelV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_dataset_v2 import (
    HistoryDatasetV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import Base, Database, RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_model_v2_records import (
    HistoryDatasetModelV2Record as Model,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.history_observed_training import fixed_cutoffs
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)

ROOT = Path(__file__).resolve().parents[2]
OWNER = "v2_math_private_owner"


def test_crossrid_fit_exact_supports_firstsave_and_negative_gates(tmp_path, monkeypatch):
    async def reject(*_args, **_kwargs):
        raise AssertionError("private V2 math attempted HTTP")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject)
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path))
    db = Database("sqlite+pysqlite:///" + str(tmp_path / "private.sqlite3"))
    Base.metadata.create_all(
        db.engine,
        tables=[
            table
            for name, table in Base.metadata.tables.items()
            if name != "market_history_v2_models"
        ],
    )
    spec = importlib.util.spec_from_file_location(
        "private_math_0086", ROOT / "migrations/versions/0086_history_dataset_model_v2.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    commands = build_history_observed_worker(db, owner_id=OWNER)
    originals = HistoryDatasetV2Service(commands, tmp_path, fixture_mode=True)
    models = HistoryDatasetModelV2Service(originals)
    known = datetime.now(UTC)
    cutoffs = fixed_cutoffs(known)
    scope = spot_scope("BTC-USDT")
    begin = int((cutoffs[0] - timedelta(minutes=31)).timestamp() * 1000)
    stop = int((cutoffs[59] + timedelta(days=1, minutes=1)).timestamp() * 1000)
    high_feature = {
        int((day - timedelta(minutes=i)).timestamp() * 1000)
        for day in cutoffs[30:60]
        for i in range(1, 32)
    }
    captures, witnesses, selected, raw_files = [], [], {}, {}
    received, recorded, accepted = (
        known - timedelta(seconds=3),
        known - timedelta(seconds=2),
        known - timedelta(seconds=1),
    )
    for offset in range(begin, stop, 300 * 60000):
        stamps = list(range(offset, min(stop, offset + 300 * 60000), 60000))
        data = [
            [
                str(stamp),
                "100",
                "103",
                "97",
                "101" if stamp in high_feature and (stamp // 60000) % 2 else "100",
                "1",
                "100",
                "100",
                "1",
            ]
            for stamp in reversed(stamps)
        ]
        body = encoded({"code": "0", "data": data})
        digest, ref = originals.raw.put(body)
        identity = "private_page_" + str(offset)
        capture = {
            "capture_id": identity,
            "context_id": identity,
            "url": "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1m&limit=300&after="
            + str(stamps[-1] + 60000),
            "status_code": 200,
            "body_hash": digest,
            "body_ref": ref,
            "byte_count": len(body),
            "media_type": "application/json",
            "adapter_version": "okx-closed-spot-path.v1",
            "error_code": None,
            "started_at": received - timedelta(seconds=1),
            "completed_at": received,
            "recorded_at": recorded,
        }
        records = []
        for index, row in enumerate(data):
            stamp = int(row[0])
            opened = datetime.fromtimestamp(stamp / 1000, UTC)
            bar = MarketCandle(
                scope_id=scope.scope_id,
                venue=scope.venue,
                instrument=scope.instrument,
                open_at=opened,
                close_at=opened + timedelta(minutes=1),
                open=Decimal(row[1]),
                high=Decimal(row[2]),
                low=Decimal(row[3]),
                close=Decimal(row[4]),
                volume_base=Decimal("1"),
                volume_quote=Decimal("100"),
                received_at=received,
                accepted_at=accepted,
                capture_ref=ref,
                capture_hash=digest,
                row_index=index,
            )
            revision = canonical_hash(bar.semantic_payload())
            key = "candle_" + canonical_hash([scope.scope_id, revision])[:48]
            records.append(
                {
                    "candle_id": key,
                    "scope_id": scope.scope_id,
                    "venue": scope.venue,
                    "instrument": scope.instrument,
                    "price_type": "trade",
                    "interval_seconds": 60,
                    "open_at": bar.open_at,
                    "close_at": bar.close_at,
                    "received_at": received,
                    "known_at": bar.known_at,
                    "revision_hash": revision,
                    "candle_json": bar.model_dump_json(),
                    "capture_ref": ref,
                    "capture_hash": digest,
                }
            )
            selected[stamp] = {
                "candle_id": key,
                "semantic_revision": revision,
                "first_known_at": accepted.isoformat(),
                "first_received_at": received.isoformat(),
                "first_accepted_at": accepted.isoformat(),
                "witness_index": len(witnesses),
            }
        with db.engine.begin() as connection:
            connection.execute(RawCaptureRecord.__table__.insert(), capture)
            connection.execute(MarketCandleRecord.__table__.insert(), records)
        with commands._read_session() as session:
            actual = originals._row(session, "raw_captures", identity)
        captures.append(("raw_captures", identity, canonical_hash(actual)))
        pairs = sorted([[stamp, selected[stamp]["semantic_revision"]] for stamp in stamps])
        witnesses.append(
            {
                "origin": "registered_v1",
                "registration_id": "private_R1" if len(witnesses) % 2 else "private_R2",
                "scope_id": scope.scope_id,
                "start_at": datetime.fromtimestamp(offset / 1000, UTC).isoformat(),
                "end_at": datetime.fromtimestamp((stamps[-1] + 60000) / 1000, UTC).isoformat(),
                "page": {
                    "phase": "label",
                    "cursor": str(stamps[-1] + 60000),
                    "next_cursor": None,
                    "rows": pairs,
                    "capture_ref": ref,
                    "capture_hash": digest,
                    "received_at": received.isoformat(),
                },
                "capture": actual,
                "producer_sources": originals.require_sources(),
            }
        )
        raw_files[str(tmp_path / "raw-captures" / digest[:2] / digest)] = digest
    with commands._read_session() as session:
        rows = (
            session.connection()
            .exec_driver_sql(
                "SELECT * FROM market_candles WHERE scope_id=? AND open_at>=? AND open_at<? LIMIT 90000",
                (
                    scope.scope_id,
                    datetime.fromtimestamp(begin / 1000, UTC).replace(tzinfo=None).isoformat(" "),
                    datetime.fromtimestamp(stop / 1000, UTC).replace(tzinfo=None).isoformat(" "),
                ),
            )
            .mappings()
            .all()
        )
        readset = captures + [
            ("market_candles", row["candle_id"], canonical_hash(dict(row))) for row in rows
        ]
        prepared = originals.prepare_original(
            session,
            kind="dataset",
            owner_claim=OWNER,
            request_id="private_60joint",
            plan_id="private_plan",
            known_asof=known,
            cutoffs=cutoffs,
            provenance={
                "fixture_mode": True,
                "original_witnesses": witnesses,
                "selected_rows": {
                    scope.scope_id: sorted(selected.items()),
                    spot_scope("ETH-USDT").scope_id: [],
                },
                "file_readset": sorted(raw_files.items()),
                "row_readset": readset,
            },
            files=tuple(sorted(raw_files.items())),
            rows=tuple(readset),
        )
    with commands._write_session() as session:
        dataset = originals.record_original(session, prepared)
    with commands._read_session() as session:
        ready = models.readiness(
            session, dataset_id=dataset.original_id, owner_claim=OWNER, scope_id=scope.scope_id
        )
        assert (
            ready["joint_days"],
            ready["low_days"],
            ready["high_days"],
            ready["count_gate_met"],
        ) == (60, 30, 30, True)
        assert (
            len(ready["windows"]) == 90
            and sum(item["status"] == "excluded" for item in ready["windows"]) == 30
        )
        fitted = models.prepare(
            session,
            dataset_id=dataset.original_id,
            owner_claim=OWNER,
            request_id="private_fit",
            scope_id=scope.scope_id,
        )
        with pytest.raises(ValueError, match="60_30_30"):
            models.prepare(
                session,
                dataset_id=dataset.original_id,
                owner_claim=OWNER,
                request_id="no_eth_fit",
                scope_id=spot_scope("ETH-USDT").scope_id,
            )
    assert fitted.value.baseline.long_mae_sorted == (Decimal("0.03"),) * 60
    assert fitted.value.conditional_high.short_mae_sorted == (Decimal("0.03"),) * 30
    with commands._write_session() as session:
        saved = models.record(session, fitted)
    with commands._read_session() as session:
        loaded = models.read(session, saved.model_id, OWNER)
        assert (
            loaded == saved
            and loaded.historical_pit_verified is False
            and loaded.financial_qualified is False
        )
    with commands._write_session() as session:
        assert models.record(session, fitted) == saved
    with pytest.raises(ValueError, match="preparation_changed"):
        with commands._write_session() as session:
            models.record(
                session,
                replace(fitted, value=fitted.value.model_copy(update={"dataset_hash": "0" * 64})),
            )
    with db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(ValueError, match="nonempty_models"):
                migration.downgrade()
    with commands._read_session() as session:
        assert session.scalar(select(func.count()).select_from(Model)) == 1
    assert (
        hashlib.sha256(Path(saved.source_original_ref).read_bytes()).hexdigest()
        == saved.source_original_hash
    )
    from tests.market_risk.history_forecast_v2_control import run_forecast_control

    run_forecast_control(SimpleNamespace(
        database=db, commands=commands, originals=originals, models=models,
        saved_model=saved, scope=scope, data_directory=tmp_path,
    ))
    db.engine.dispose()
