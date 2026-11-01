from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.kernel.decision_hub_kernel.decision.path_risk import compute_path_risk
from packages.kernel.decision_hub_kernel.ports.market_path import (
    MarketCandle,
    MarketPathScope,
    path_hashes,
    spot_scope,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE = spot_scope("BTC-USDT")


def candle(index, *, o="100", high="101", low="99", close="100", received=None):
    return MarketCandle(
        scope_id=SCOPE.scope_id,
        venue="okx",
        instrument="BTC-USDT",
        open_at=START + timedelta(minutes=index),
        close_at=START + timedelta(minutes=index + 1),
        open=Decimal(o),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume_base=Decimal("1"),
        volume_quote=Decimal("100"),
        received_at=received or START + timedelta(hours=1),
        capture_ref="raw://sha256/" + "a" * 64,
        capture_hash="a" * 64,
    )


def risk(bars, *, minutes=3, as_of=None):
    return compute_path_risk(
        SCOPE,
        tuple(bars),
        START,
        START + timedelta(minutes=minutes),
        as_of=as_of or START + timedelta(hours=2),
    )


def test_positive_terminal_return_does_not_hide_long_adverse_path():
    result = risk(
        [
            candle(0, high="103", low="95", close="102"),
            candle(1, o="102", high="106", low="101", close="105"),
            candle(2, o="105", high="107", low="103", close="104"),
        ]
    )
    assert result.window_status == "complete"
    assert result.terminal_return == Decimal(".04")
    assert result.mae_long == Decimal(".05")
    assert result.mae_short == Decimal(".07")
    assert abs(result.mdd_close - Decimal(1) / Decimal(105)) < Decimal("1e-20")
    assert result.crossings[0].interval_start_at == START
    assert result.crossings[0].interval_end_at == START + timedelta(minutes=1)
    assert result.same_bar_order_ambiguous


def test_missing_scheduled_anchor_never_selects_later_bar():
    result = risk([candle(1, o="90", high="91", low="80", close="90"), candle(2)])
    assert result.anchor_at == START and result.anchor_price is None
    assert result.mae_long is None and not result.crossings
    assert "scheduled_anchor_missing" in result.reasons


def test_gap_is_lower_bound_and_absence_is_unknown():
    result = risk([candle(0), candle(2)])
    assert result.quality == "observed_lower_bound"
    assert result.terminal_return is None
    assert all(item.event_presence is None for item in result.crossings)
    assert result.missing_ranges == ((START + timedelta(minutes=1), START + timedelta(minutes=2)),)


def test_observed_crossing_after_gap_does_not_claim_firstness():
    result = risk([candle(0), candle(2, low="95")])
    assert result.crossings[0].event_presence is True
    assert result.crossings[0].firstness == "unknown"


def test_prepublication_low_is_not_part_of_path():
    result = risk([candle(-1, low="50"), candle(0), candle(1), candle(2)])
    assert result.mae_long == Decimal(".01")
    assert result.observed_count == 3


def test_late_received_data_is_not_known_at_cutoff():
    result = risk([candle(0), candle(1), candle(2)], as_of=START + timedelta(minutes=5))
    assert result.anchor_price is None
    assert result.window_status == "unknown"


def test_late_storage_acceptance_is_not_known_at_cutoff():
    bars = tuple(
        candle(i, received=START + timedelta(minutes=i + 1)).model_copy(
            update={"accepted_at": START + timedelta(minutes=10)}
        )
        for i in range(3)
    )
    before = risk(bars, as_of=START + timedelta(minutes=5))
    after = risk(bars, as_of=START + timedelta(minutes=10))
    assert before.anchor_price is None and before.window_status == "unknown"
    assert after.window_status == "complete"


def test_storage_acceptance_changes_provenance_not_price_semantics():
    original = candle(0)
    stored = original.model_copy(update={"accepted_at": START + timedelta(hours=3)})
    before, after = path_hashes(SCOPE, (original,)), path_hashes(SCOPE, (stored,))
    assert before[0] == after[0]
    assert before[1] != after[1]


def test_spot_unit_and_scope_id_mismatches_are_rejected():
    payload = SCOPE.model_dump()
    payload["price_unit"] = "USD/BTC"
    with pytest.raises(ValidationError, match="scope_units_invalid"):
        MarketPathScope.model_validate(payload)
    payload = SCOPE.model_dump()
    payload["scope_id"] = "some-other-market"
    with pytest.raises(ValidationError, match="scope_id_invalid"):
        MarketPathScope.model_validate(payload)


def test_not_due_label_does_not_fill_false_or_terminal_return():
    result = risk(
        [candle(0, received=START + timedelta(minutes=1))], as_of=START + timedelta(minutes=1)
    )
    assert result.window_status == "pending" and result.pending_count == 2
    assert result.terminal_return is None
    assert all(item.event_presence is None for item in result.crossings)


def test_swap_mark_mismatch_is_rejected():
    wrong = candle(0).model_copy(update={"price_type": "mark"})
    with pytest.raises(ValueError, match="scope_mismatch"):
        risk([wrong])


def test_conflicting_duplicates_are_not_selected_by_favorable_result():
    with pytest.raises(ValueError, match="conflicting_duplicate"):
        risk([candle(0), candle(0, low="90")])


def test_recollection_changes_provenance_not_price_semantics():
    first = candle(0)
    later = first.model_copy(
        update={"received_at": START + timedelta(hours=2), "capture_ref": "another-original"}
    )
    first_hashes, later_hashes = path_hashes(SCOPE, (first,)), path_hashes(SCOPE, (later,))
    assert first_hashes[0] == later_hashes[0]
    assert first_hashes[1] != later_hashes[1]


@pytest.mark.parametrize("field,value", [("close", "NaN"), ("low", "-1"), ("high", "90")])
def test_invalid_numbers_are_rejected_at_contract(field, value):
    payload = candle(0).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        MarketCandle.model_validate(payload)


def test_unaligned_window_is_rejected_instead_of_using_whole_bar():
    with pytest.raises(ValueError, match="window_invalid"):
        compute_path_risk(
            SCOPE,
            (),
            START + timedelta(seconds=1),
            START + timedelta(minutes=3),
            as_of=START + timedelta(hours=2),
        )
