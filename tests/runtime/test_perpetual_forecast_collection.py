"""No-network collector orchestration over real parsed synthetic capture pages."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from candidate_perpetual_forecast_support import parsed_pages, truth_module


@pytest.mark.parametrize("horizon,pages", [("4h", 1), ("24h", 5), ("72h", 15)])
def test_due_collection_follows_every_exact_older_cursor(horizon: Any, pages: Any):
    start, end, at, originals = parsed_pages(horizon, "ETH-USDT-SWAP")
    calls = []

    async def capture(cursor: Any, sequence: Any):
        calls.append((cursor, sequence))
        value = originals[sequence]
        assert cursor == value.after_ms
        return value

    result = asyncio.run(
        truth_module().collect_truth_pages(
            instrument_id="ETH-USDT-SWAP",
            start=start,
            end=end,
            horizon=horizon,
            capture_page=capture,
            clock=lambda: at,
        )
    )
    assert result == tuple(page.capture.capture_id for page in originals)
    assert len(calls) == pages


def test_not_due_does_not_request_or_label_truth():
    start, end, _, _ = parsed_pages()
    calls = []

    async def capture(cursor: Any, sequence: Any):
        calls.append((cursor, sequence))
        raise AssertionError("not-due data request")

    with pytest.raises(ValueError):
        asyncio.run(
            truth_module().collect_truth_pages(
                instrument_id="BTC-USDT-SWAP",
                start=start,
                end=end,
                horizon="4h",
                capture_page=capture,
                clock=lambda: end - timedelta(microseconds=1),
            )
        )
    assert calls == []


def test_capture_failure_retains_original_partial_ids_and_no_label():
    start, end, at, originals = parsed_pages("24h")
    module = truth_module()

    async def capture(cursor: Any, sequence: Any):
        if sequence == 1:
            raise ValueError("synthetic_capture_failed")
        return originals[sequence]

    with pytest.raises(ValueError) as error:
        asyncio.run(
            module.collect_truth_pages(
                instrument_id="BTC-USDT-SWAP",
                start=start,
                end=end,
                horizon="24h",
                capture_page=capture,
                clock=lambda: at,
            )
        )
    assert getattr(error.value, "capture_ids", None) == (originals[0].capture.capture_id,)
