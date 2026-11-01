"""Synthetic local bars test truthful sampling; not provider acceptance."""

from datetime import timedelta
from decimal import Decimal

from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    FactVersionRecord,
    MacroEventRecord,
)
from packages.kernel.decision_hub_kernel.ports.market_path import MarketCandle
from packages.query_views.market_risk import MarketRiskQuery
from tests.market_risk.test_market_data import NOW, SCOPE


def test_aggregation_retains_intraminute_extremes_hashes_and_declared_gap(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'timeline.sqlite'}")
    database.create_all()
    end, start = NOW.replace(second=0), NOW.replace(second=0) - timedelta(minutes=3000)
    bars = tuple(
        MarketCandle(
            scope_id=SCOPE.scope_id,
            venue="okx",
            instrument="BTC-USDT",
            open_at=start + timedelta(minutes=i),
            close_at=start + timedelta(minutes=i + 1),
            open=Decimal(100),
            close=Decimal(100),
            low=Decimal(50 if i == 1532 else 99),
            high=Decimal(200 if i == 1528 else 101),
            received_at=NOW,
        )
        for i in range(3000)
        if i != 1200
    )
    query = MarketRiskQuery(database, tmp_path, clock=lambda: NOW)

    class SyntheticStore:
        def load(self, *args, **kwargs):
            return bars

    query.store = SyntheticStore()
    result = query.timeline(SCOPE.scope_id, start=start, end=end)
    assert result.aggregation.source_points == 2999
    assert result.aggregation.output_points == len(result.points) < 2000
    assert result.aggregation.method == "extreme_samples"
    assert min(Decimal(p.low) for p in result.points) == 50
    assert max(Decimal(p.high) for p in result.points) == 200
    assert result.points[0].at == bars[0].close_at
    assert result.points[-1].at == bars[-1].close_at
    assert len(result.missing_ranges) == 1
    assert result.missing_ranges[0].start_at == start + timedelta(minutes=1200)
    database.engine.dispose()


def test_markers_use_known_time_and_exact_asset_without_future_rows(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'markers.sqlite'}")
    database.create_all()

    def fact(key, asset, known):
        return FactVersionRecord(
            fact_version_id=key,
            semantic_key=key,
            market="crypto",
            scope_instrument=asset,
            field="close",
            source_id="fixture",
            original_fact_id=key,
            parsed_version_id=key,
            payload_json="{}",
            known_at=known,
            observed_at=NOW - timedelta(hours=2),
            recorded_at=known,
        )

    with database.session() as session:
        session.add_all(
            [
                fact("actual-btc", "BTC-USDT", NOW - timedelta(minutes=5)),
                fact("other-eth", "ETH-USDT", NOW - timedelta(minutes=5)),
                fact("future-btc", "BTC-USDT", NOW + timedelta(minutes=5)),
            ]
        )
        session.add(
            MacroEventRecord(
                macro_event_id="global-calendar",
                event_id="macro-event",
                external_event_id="macro",
                family="inflation",
                scheduled_at=NOW + timedelta(days=1),
                source_id="fixture",
            status="scheduled",
            created_at=NOW - timedelta(minutes=3),
            updated_at=NOW - timedelta(minutes=3),
            )
        )
    query = MarketRiskQuery(database, tmp_path, clock=lambda: NOW)
    markers, truncated = query._markers(SCOPE, start=NOW - timedelta(minutes=30), end=NOW)
    assert {m["marker_id"] for m in markers} == {"actual-btc", "global-calendar"}
    assert markers[0]["at"] == NOW - timedelta(minutes=5)
    assert markers[0]["occurred_at"] == NOW - timedelta(hours=2)
    assert "相关性待研究" in markers[1]["label"] and not truncated
    database.engine.dispose()
