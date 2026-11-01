from datetime import timedelta
from decimal import Decimal

from packages.kernel.decision_hub_kernel.application.market_features import build_market_features
from tests.test_path_risk import SCOPE, START, candle


def feature_bars():
    return tuple(
        candle(
            i,
            o=str(100 + i),
            high=str(101 + i),
            low=str(99 + i),
            close=str(100 + i),
            received=START + timedelta(minutes=i + 1),
        )
        for i in range(31)
    )


def test_thirty_returns_require_thirty_one_continuous_closes():
    complete = build_market_features(SCOPE, feature_bars(), START + timedelta(minutes=31))
    assert complete.status == "fresh" and complete.return_30m == Decimal(".3")
    assert complete.realized_vol_30m is not None and complete.realized_vol_30m > 0
    insufficient = build_market_features(SCOPE, feature_bars()[1:], START + timedelta(minutes=31))
    assert insufficient.status == "unknown" and insufficient.return_30m is None


def test_gap_cannot_be_hidden_by_recent_endpoint_prices():
    bars = feature_bars()
    result = build_market_features(SCOPE, bars[:15] + bars[16:], START + timedelta(minutes=31))
    assert result.status == "unknown" and result.return_30m is None


def test_stale_quote_does_not_return_fresh_status():
    result = build_market_features(SCOPE, feature_bars(), START + timedelta(minutes=35))
    assert result.status == "stale"
    assert "latest_closed_bar_stale" in result.reasons


def test_future_receipt_is_not_a_historical_feature():
    result = build_market_features(SCOPE, feature_bars(), START + timedelta(minutes=30))
    assert result.latest_close_at == START + timedelta(minutes=30)
    assert result.return_30m is None
    assert result.spread_fraction is None


def test_backfill_receipt_does_not_refresh_market_time():
    receipt = START + timedelta(days=2)
    bars = tuple(item.model_copy(update={"received_at": receipt}) for item in feature_bars())
    result = build_market_features(SCOPE, bars, receipt)
    assert result.status == "stale"
    assert result.data_as_of == result.latest_close_at == START + timedelta(minutes=31)
    assert result.known_at == receipt


def test_storage_acceptance_is_the_feature_knowledge_cutoff():
    accepted = START + timedelta(minutes=40)
    bars = tuple(item.model_copy(update={"accepted_at": accepted}) for item in feature_bars())
    before = build_market_features(SCOPE, bars, START + timedelta(minutes=31))
    assert before.status == "unknown" and before.known_at is None
    after = build_market_features(SCOPE, bars, accepted)
    assert after.known_at == accepted and after.status == "stale"
