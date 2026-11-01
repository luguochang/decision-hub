from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from packages.contracts_py.decision_hub_contracts import FactEnvelope
from packages.kernel.decision_hub_kernel.decision.daily_strategy import compute_daily_strategy
from packages.query_views.research.service import _action_summary, _research_outlook_from_facts
from tests.evolution.test_research_worker import _result


def test_query_projection_accepts_only_a_valid_daily_strategy_receipt() -> None:
    bars = [
        {
            "date": f"2026-01-{index + 1:02d}",
            "open": 99 + index,
            "high": 101 + index,
            "low": 98 + index,
            "close": 100 + index,
            "volume": 1000 + index,
        }
        for index in range(80)
    ]
    receipt = compute_daily_strategy(bars, evidence_refs=["ev-daily"]).as_json()
    fact = FactEnvelope(
        schema_version="fact-envelope.v1",
        fact_id="fact-daily",
        evidence_id="ev-daily",
        requirement_id="market_daily_strategy",
        metric_family="crypto.spot.history",
        field="daily_strategy_receipt",
        instrument="BTCUSDT",
        venue="binance",
        value="daily-trend-v0",
        unit="json",
        window_start_at=datetime(2026, 1, 1, tzinfo=UTC),
        window_end_at=datetime(2026, 3, 22, tzinfo=UTC),
        event_offset=None,
        observed_at=datetime(2026, 3, 22, tzinfo=UTC),
        received_at=datetime(2026, 3, 22, tzinfo=UTC),
        published_at=None,
        source_id="binance-public",
        independence_group="binance-public:daily",
        quality="accepted",
        delay_class="historical",
        payload_schema_ref="strategy-receipt.v1",
        payload_hash="a" * 64,
        attributes={"daily_strategy_receipt_json": receipt},
    )

    outlook = _research_outlook_from_facts([fact], scope_instrument="BTCUSDT")

    assert outlook is not None
    assert outlook.outlook == "bullish"
    assert outlook.strategy_status == "baseline_only"
    assert outlook.execution_permission is False
    assert outlook.strategy_receipt is not None

    assert _research_outlook_from_facts([fact.model_copy(update={"quality": "rejected"})], scope_instrument="BTCUSDT") is None
    mismatched = json.loads(receipt)
    mismatched["evidence_refs"] = ["different-evidence"]
    assert _research_outlook_from_facts([fact.model_copy(update={
        "attributes": {"daily_strategy_receipt_json": json.dumps(mismatched)}
    })], scope_instrument="BTCUSDT") is None

    invalid = fact.model_copy(
        update={"attributes": {"daily_strategy_receipt_json": json.dumps({})}}
    )
    assert _research_outlook_from_facts([invalid], scope_instrument="BTCUSDT") is None


def _daily_summary(scope_instrument: str | None):
    # Accepted BTC spot history with the same receipt/fact shape as the actual
    # ETH report. These synthetic prices are private control data, not forecasts.
    base = _result(SimpleNamespace(current_round=1, evidence_refs=[], request_id="scope-control"))
    candidate = base.evidence_candidates[0].model_copy(update={
        "quality": "accepted", "freshness_status": "fresh",
    })
    bars = [
        {"date": f"2026-01-{index + 1:02d}", "open": 83000 + index,
         "high": 83002 + index, "low": 82999 + index, "close": 83001 + index,
         "volume": 1000 + index}
        for index in range(80)
    ]
    receipt = compute_daily_strategy(bars, evidence_refs=[candidate.evidence_id]).as_json()
    fact = FactEnvelope(
        schema_version="fact-envelope.v1", fact_id="fact-btc-daily",
        evidence_id=candidate.evidence_id, requirement_id="market_regime_context",
        metric_family="crypto.spot.history", field="period_close", instrument="BTC-USDT",
        venue="gateio", value="83080", unit="usdt",
        window_start_at=datetime(2026, 1, 1, tzinfo=UTC),
        window_end_at=datetime(2026, 3, 22, tzinfo=UTC), event_offset=None,
        observed_at=datetime(2026, 3, 22, tzinfo=UTC), received_at=datetime(2026, 3, 22, tzinfo=UTC),
        published_at=None, source_id="gateio-public", independence_group="gateio-public:daily",
        quality="accepted", delay_class="historical", payload_schema_ref="historical-window-summary.v1",
        payload_hash="b" * 64, attributes={
            "dataset_id": "crypto.btc_usdt.ohlcv_1d", "daily_strategy_receipt_json": receipt,
        },
    )
    result = base.model_copy(update={"evidence_candidates": [candidate], "facts": [fact]})
    run = SimpleNamespace(
        run_id="scope-control", status="research_only", failure=None,
        coverage=result.final_coverage,
        # Free text deliberately disagrees with the frozen scope; no inference.
        event_title="研究BTC未来7天" if scope_instrument == "ETH-USDT" else "ETH研究",
    )
    before = result.model_dump_json()
    summary = _action_summary(
        run, SimpleNamespace(gate_status="research_only"), result, [candidate],
        scope_instrument=scope_instrument, scheduled_recheck_at=None,
    )
    assert result.model_dump_json() == before
    return summary, candidate, fact


def test_eth_saved_scope_never_assigns_accepted_btc_daily_direction() -> None:
    summary, candidate, fact = _daily_summary("ETH-USDT")
    assert summary.research_outlook is None
    assert summary.market_view is not None and summary.market_view.direction == "unresolved"
    assert "7日价格规则倾向" not in summary.headline
    assert "日线参考未与原任务资产核对" in summary.rationale
    assert "daily_outlook_asset_mismatch" in summary.warning_codes
    assert candidate.evidence_id in summary.supporting_evidence_refs
    assert fact.instrument == "BTC-USDT"
    unknown, _, _ = _daily_summary(None)
    assert unknown.research_outlook is None
    assert "daily_outlook_scope_unverified" in unknown.warning_codes


def test_btc_saved_scope_keeps_its_accepted_daily_reference() -> None:
    summary, candidate, _ = _daily_summary("BTC-USDT")
    assert summary.research_outlook is not None and summary.research_outlook.outlook == "bullish"
    assert summary.research_outlook.evidence_refs == [candidate.evidence_id]
    assert summary.market_view is not None and summary.market_view.direction == "bullish"
    assert summary.headline.startswith("7日价格规则倾向：偏多")
    assert summary.trade_authorized is False
    assert not any(code.startswith("daily_outlook_") for code in summary.warning_codes)

    alias_summary, _, _ = _daily_summary("BTCUSDT")
    assert alias_summary.research_outlook is not None
    assert alias_summary.research_outlook.outlook == "bullish"
