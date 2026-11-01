"""Authenticated nonreusable clock brackets, through real capture and SQLite."""

import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord, utcnow
from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_paper import INST, creation, service
from tests.decision_product.test_perpetual_paper import runtime as runtime


@pytest.mark.asyncio
async def test_genuine_bracket_maps_nine_second_venue_offset_without_clock_change(
    runtime, monkeypatch
):
    calls = []

    async def response(transport, request):
        calls.append(request.url.path)
        ts = str(int((utcnow() + timedelta(seconds=9)).timestamp() * 1000))
        row = {"ts": ts}
        if request.url.path == "/api/v5/market/books":
            row.update(bids=[["9990", "20", "0", "1"]], asks=[["10010", "20", "0", "1"]], seqId=123)
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    svc = service(runtime)
    svc.create_account("alice", creation(runtime))
    collected = await svc.collect_bracketed_quote(inst_id=INST)
    cutoff = utcnow()
    prepared = svc.prepare_quote(
        instrument_ref=runtime[3],
        capture_id=collected.capture_id,
        clock_measurement_id=collected.clock_measurement_id,
        cutoff=cutoff,
    )
    book = prepared.value.book
    assert calls == ["/api/v5/public/time", "/api/v5/market/books", "/api/v5/public/time"]
    assert book.observed_at > book.received_at + timedelta(seconds=8)
    assert book.observed_local_lower <= book.observed_local_upper <= book.received_at
    assert cutoff - book.observed_local_lower < timedelta(seconds=5)
    receipt = json.loads(book.clock_receipt_json)
    assert receipt["offset_lower_us"] < 9_000_000 < receipt["offset_upper_us"]
    assert receipt["policy"]["reusable"] is False
    with runtime[0].session() as session:
        begin_immediate(session)
        prepared.verify(session)
        from packages.kernel.decision_hub_kernel.persistence.perpetual_paper_repository import (
            PerpetualPaperRepository,
        )

        filled = PerpetualPaperRepository(session).apply_fill(
            prepared=prepared,
            owner_id="alice",
            account_id="practice",
            expected_version=1,
            request_id="clock-fill",
            phase="entry",
            order_side="buy",
            requested_contracts=Decimal("1"),
            fee_rate=Decimal("0.001"),
            slippage_bps=Decimal("0"),
            cost_assumption_ref="clock-test-simulation-cost",
            now=utcnow(),
        )
        assert (
            filled["event"]["clock_receipt"]["measurement_capture_ref"]
            == collected.clock_measurement_id
        )
        assert filled["event"]["filled_contracts"] == "1"
    with pytest.raises(ValueError, match="quote_stale"):
        svc.prepare_quote(
            instrument_ref=runtime[3],
            capture_id=collected.capture_id,
            clock_measurement_id=collected.clock_measurement_id,
            cutoff=cutoff + timedelta(seconds=6),
        )
    with pytest.raises(ValueError, match="quote_stale"):
        svc.prepare_quote(instrument_ref=runtime[3], capture_id=collected.capture_id, cutoff=cutoff)
    with runtime[0].session() as session:
        row = session.get(RawCaptureRecord, collected.clock_measurement_id)
        path = runtime[1].root / row.body_hash[:2] / row.body_hash
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="archive_corrupt"):
        svc.prepare_quote(
            instrument_ref=runtime[3],
            capture_id=collected.capture_id,
            clock_measurement_id=collected.clock_measurement_id,
            cutoff=cutoff,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [31, -31])
async def test_excessive_clock_offset_is_rejected_instead_of_guessed(runtime, monkeypatch, offset):
    async def response(transport, request):
        ts = str(int((utcnow() + timedelta(seconds=offset)).timestamp() * 1000))
        row = {"ts": ts}
        if request.url.path == "/api/v5/market/books":
            row.update(bids=[["9990", "20", "0", "1"]], asks=[["10010", "20", "0", "1"]], seqId=123)
        return httpx.Response(200, request=request, json={"code": "0", "data": [row]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", response)
    svc = service(runtime)
    collected = await svc.collect_bracketed_quote(inst_id=INST)
    with pytest.raises(ValueError, match="clock_uncertain"):
        svc.prepare_quote(
            instrument_ref=runtime[3],
            capture_id=collected.capture_id,
            clock_measurement_id=collected.clock_measurement_id,
            cutoff=utcnow(),
        )
