"""One additional forecast group sharing the actual V2 math positive fixture."""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

from packages.kernel.decision_hub_kernel.application.history_dataset_forecast_v2 import (
    HistoryDatasetForecastV2Service,
)
from packages.kernel.decision_hub_kernel.application.history_observed_dataset import encoded
from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.persistence.history_dataset_forecast_v2_records import (
    HistoryDatasetForecastV2Record as Forecast,
)
from packages.kernel.decision_hub_kernel.persistence.risk_records import MarketCandleRecord
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    canonical_hash,
    spot_scope,
)
from packages.provider_adapters.market.history_missing_transport_v2 import CAPTURE_PRODUCER_V2


def run_forecast_control(env):
    root = Path(__file__).resolve().parents[2]
    db, originals, models = env.database, env.originals, env.models
    spec = importlib.util.spec_from_file_location(
        "private_forecast_0087", root / "migrations/versions/0087_history_dataset_forecast_v2.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with db.engine.begin() as connection:
        Forecast.__table__.drop(connection, checkfirst=True)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    cutoff = env.saved_model.model_known_at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    received, recorded, accepted = (
        cutoff - timedelta(seconds=3),
        cutoff - timedelta(seconds=2),
        cutoff - timedelta(seconds=1),
    )
    stamps = [int((cutoff - timedelta(minutes=i)).timestamp() * 1000) for i in range(32, 1, -1)]
    data = [
        [str(stamp), "100", "103", "97", "100", "1", "100", "100", "1"]
        for stamp in reversed(stamps)
    ]
    body = encoded({"code": "0", "data": data})
    digest, ref = originals.raw.put(body)
    capture = dict(
        capture_id="private_future_page",
        context_id="private_future_page",
        url="https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=1m&limit=300&after="
        + str(int(cutoff.timestamp() * 1000)),
        status_code=200,
        body_hash=digest,
        body_ref=ref,
        byte_count=len(body),
        media_type="application/json",
        adapter_version=CAPTURE_PRODUCER_V2,
        error_code=None,
        started_at=received - timedelta(seconds=1),
        completed_at=received,
        recorded_at=recorded,
    )
    rows = []
    for index, raw in enumerate(data):
        opened = cutoff - timedelta(milliseconds=int(cutoff.timestamp() * 1000) - int(raw[0]))
        bar = MarketCandle(
            scope_id=env.scope.scope_id,
            venue=env.scope.venue,
            instrument=env.scope.instrument,
            open_at=opened,
            close_at=opened + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("97"),
            close=Decimal("100"),
            volume_base=Decimal("1"),
            volume_quote=Decimal("100"),
            received_at=received,
            accepted_at=accepted,
            capture_ref=ref,
            capture_hash=digest,
            row_index=index,
        )
        revision = canonical_hash(bar.semantic_payload())
        rows.append(
            dict(
                candle_id="candle_" + canonical_hash([bar.scope_id, revision])[:48],
                scope_id=bar.scope_id,
                venue=bar.venue,
                instrument=bar.instrument,
                price_type=bar.price_type,
                interval_seconds=60,
                open_at=bar.open_at,
                close_at=bar.close_at,
                received_at=bar.received_at,
                known_at=bar.known_at,
                revision_hash=revision,
                candle_json=bar.model_dump_json(),
                capture_ref=ref,
                capture_hash=digest,
            )
        )
    with db.engine.begin() as connection:
        connection.execute(RawCaptureRecord.__table__.insert(), capture)
        connection.execute(MarketCandleRecord.__table__.insert(), rows)
    originals.clock = lambda: cutoff + timedelta(seconds=5)
    service = HistoryDatasetForecastV2Service(
        models,
        owner_claim=env.saved_model.owner_claim,
        grants={env.scope.scope_id: env.saved_model.model_id},
    )
    saved = service.producer_once(env.scope.scope_id)
    assert saved.request_id.startswith("auto_")
    assert saved.cutoff_at == cutoff and saved.model_known_at <= cutoff
    assert (
        len(saved.selected_closes) == 31 and (cutoff - saved.latest_close_at).total_seconds() == 60
    )
    assert saved.feature_known_at <= cutoff and tuple(
        item.threshold for item in saved.frequencies
    ) == ("0.02", "0.05")
    assert saved.frequencies[0].baseline_long_probability == Decimal("60.5") / Decimal(61)
    assert saved.frequencies[1].baseline_long_probability == Decimal("0.5") / Decimal(61)
    assert (
        saved.financial_qualified is False
        and saved.historical_pit_verified is False
        and saved.fixture_mode is True
    )
    with env.commands._read_session() as session:
        assert service.read(session, saved.forecast_id) == saved
        assert session.scalar(select(func.count()).select_from(Forecast)) == 1
    originals.clock = lambda: cutoff + timedelta(minutes=1, seconds=5)
    assert service.producer_once(env.scope.scope_id, saved.request_id) == saved
    with pytest.raises(ValueError, match="scope_not_server_granted"):
        service.producer_once(spot_scope("ETH-USDT").scope_id)
    originals.clock = lambda: cutoff + timedelta(seconds=5)
    with env.commands._read_session() as session:
        prepared = service.prepare(
            session,
            scope_id=env.scope.scope_id,
            request_id="private_bad_prepared",
            cutoff_at=cutoff,
        )
    with pytest.raises(ValueError, match="preparation_changed"):
        with env.commands._write_session() as session:
            service.record(
                session,
                replace(prepared, value=prepared.value.model_copy(update={"model_hash": "0" * 64})),
            )
    with pytest.raises(ValueError, match="model_not_known_at_T"):
        with env.commands._read_session() as session:
            service.prepare(
                session,
                scope_id=env.scope.scope_id,
                request_id="private_early_model",
                cutoff_at=cutoff - timedelta(minutes=1),
            )
    raw_path = originals.raw.root / digest[:2] / digest
    raw_path.write_bytes(body + b" ")
    try:
        with pytest.raises(ValueError, match="raw_capture_archive_corrupt"):
            with env.commands._read_session() as session:
                service.read(session, saved.forecast_id)
    finally:
        raw_path.write_bytes(body)
    with db.engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(ValueError, match="nonempty_forecasts"):
                migration.downgrade()
    return saved
