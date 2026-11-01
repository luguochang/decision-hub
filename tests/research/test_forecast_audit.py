from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.kernel.decision_hub_kernel.application.commit import _forecast_audit_context
from packages.kernel.decision_hub_kernel.application.forecast_audit import forecast_audit_for_run
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.kernel.decision_hub_kernel.persistence.db import (
    ArtifactRecord,
    Database,
    EventRecord,
    ForecastRecord,
    OutcomeRecord,
    ResearchFactRecord,
    RunRecord,
    SnapshotRecord,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def test_forecast_audit_freezes_strategy_features_and_checks_time_order(tmp_path: Path) -> None:
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'forecast-audit.sqlite3'}")
    database.create_all()
    bars = [
        {
            "date": f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}",
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1000 + index,
        }
        for index in range(80)
    ]
    receipt = compute_daily_strategy(bars, as_of="2026-03-24", evidence_refs=["ev-1"])
    with database.session() as session:
        session.add(
            EventRecord(
                event_id="event-1",
                event_type="crypto_market_snapshot",
                occurred_at=NOW,
                received_at=NOW,
            )
        )
        session.add(
            SnapshotRecord(
                snapshot_id="snapshot-1",
                event_id="event-1",
                cutoff_at=NOW - timedelta(minutes=1),
                snapshot_hash="a" * 64,
                evidence_json="[]",
                run_id="run-1",
                created_at=NOW - timedelta(minutes=1),
            )
        )
        session.add(
            RunRecord(
                run_id="run-1",
                event_id="event-1",
                snapshot_id="snapshot-1",
                status="completed",
                strategy_version="research.v1",
                runtime_version="fixture",
                admission_origin="manual",
                priority=100,
                artifact_id="artifact-1",
                created_at=NOW - timedelta(minutes=2),
                updated_at=NOW,
                finished_at=NOW,
            )
        )
        session.add(
            ResearchFactRecord(
                fact_id="fact-1",
                run_id="run-1",
                capability_id="data.historical",
                evidence_id="ev-1",
                requirement_id="market_regime_context",
                metric_family="crypto.spot.history",
                field="daily_strategy_receipt",
                instrument="BTCUSDT",
                venue="binance",
                value_json=json.dumps(receipt.strategy_version),
                unit="json",
                observed_at=NOW - timedelta(minutes=2),
                received_at=NOW - timedelta(minutes=1),
                source_id="binance-public",
                independence_group="binance-public:daily",
                quality="accepted",
                delay_class="historical",
                payload_schema_ref="strategy-receipt.v1",
                payload_hash="b" * 64,
                attributes_json=json.dumps({"daily_strategy_receipt": receipt.as_dict()}),
                accepted_at=NOW - timedelta(minutes=1),
            )
        )
        session.add(
            ArtifactRecord(
                artifact_id="artifact-1",
                run_id="run-1",
                event_id="event-1",
                gate_status="degraded",
                headline="fixture",
                summary="fixture",
                payload_json="{}",
                created_at=NOW,
            )
        )

    context = _forecast_audit_context(database, "run-1", NOW)
    assert context.strategy_version == "daily-trend-v0.1"
    assert context.as_of == "2026-03-24"
    assert context.input_snapshot_id == "snapshot-1"
    assert context.input_fingerprint is not None and len(context.input_fingerprint) == 64
    assert json.loads(context.feature_snapshot_json or "{}")["return_1d_pct"] > 0

    with database.session() as session:
        session.add(
            ForecastRecord(
                forecast_id="forecast-1",
                artifact_id="artifact-1",
                instrument="BTC-USDT-SWAP",
                horizon="24h",
                direction="long",
                probability=0.6,
                trigger="fixture",
                invalidation="fixture",
                expires_at=NOW + timedelta(hours=24),
                forecasted_at=NOW,
                as_of=context.as_of,
                strategy_version=context.strategy_version,
                input_snapshot_id=context.input_snapshot_id,
                input_fingerprint=context.input_fingerprint,
                feature_snapshot_json=context.feature_snapshot_json,
                evidence_cutoff_at=context.evidence_cutoff_at,
            )
        )
        session.add(
            OutcomeRecord(
                outcome_id="outcome-1",
                forecast_id="forecast-1",
                observed_at=NOW + timedelta(hours=24),
                direction_correct=True,
                return_pct=1.0,
                quality_status="observed",
            )
        )

    audit = forecast_audit_for_run(database, "run-1")
    assert len(audit) == 1
    assert audit[0].immutable_snapshot is True
    assert audit[0].forecasted_before_outcome is True
    assert audit[0].trailing_direction == "bullish"
    assert audit[0].same_as_trailing_trend is True
