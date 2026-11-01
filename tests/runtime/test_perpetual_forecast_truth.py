"""Six-scope truth proof on synthetic bytes; no market/network acceptance claim."""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from candidate_perpetual_forecast_support import parsed_pages, truth_module


@pytest.mark.parametrize("instrument", ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
@pytest.mark.parametrize(
    "horizon,count,pages", [("4h", 241, 1), ("24h", 1441, 5), ("72h", 4321, 15)]
)
def test_six_scopes_authenticate_full_frozen_window(
    horizon: Any, count: Any, pages: Any, instrument: Any
):
    start, end, at, originals = parsed_pages(horizon, instrument)
    result = truth_module().derive_truth(
        originals, instrument_id=instrument, start=start, end=end, horizon=horizon, recorded_at=at
    )
    assert len(result) == count and len(originals) == pages
    assert result[0].close_at == start and result[-1].close_at == end
    assert result[0].price == "100" and result[-1].price == "110"
    assert all(item.recorded_at <= at for item in result)


@pytest.mark.parametrize(
    "change",
    ["missing", "duplicate_page", "cursor", "asset", "not_due", "future_record", "extra_page"],
)
def test_incomplete_or_misbound_truth_never_becomes_a_label(change: Any):
    start, end, at, originals = parsed_pages("24h", missing=change == "missing")
    instrument = "BTC-USDT-SWAP"
    if change == "duplicate_page":
        originals = (*originals, originals[-1])
    if change == "cursor":
        originals = (
            originals[0],
            replace(originals[1], after_ms=originals[0].after_ms),
            *originals[2:],
        )
    if change == "asset":
        instrument = "ETH-USDT-SWAP"
    if change == "not_due":
        at = end - timedelta(microseconds=1)
    if change == "future_record":
        at = end + timedelta(seconds=1)
    if change == "extra_page":
        originals = (*originals, replace(originals[-1], sequence=5))
    with pytest.raises(ValueError):
        truth_module().derive_truth(
            originals, instrument_id=instrument, start=start, end=end, horizon="24h", recorded_at=at
        )


@pytest.mark.parametrize("attack", ["body_hash", "unconfirmed", "invalid_ohlc", "duplicate_minute"])
def test_new_truth_codec_does_not_relax_original_capture_or_candle_integrity(attack: Any):
    with pytest.raises(ValueError):
        parsed_pages("72h", attack=attack)
