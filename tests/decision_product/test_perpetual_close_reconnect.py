"""Offline transport reconnect with real temporary raw originals, never Run submission."""

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from tests.decision_product.test_okx_perpetual_original import INST, NOW
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["recover", "exhausted", "malformed", "http4xx", "archive"])
async def test_public_close_get_reconnect_preserves_attempt_originals(
    persisted_original, monkeypatch, mode
):
    from packages.kernel.decision_hub_kernel.application import perpetual_close_source as module
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import MINUTE_MS, VERSION
    from packages.provider_adapters.research import raw_capture

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW + timedelta(seconds=10)

    requests, sleeps, response_bodies = [], [], []

    async def sleep(delay):
        sleeps.append(delay)

    async def respond(request):
        requests.append(str(request.url))
        if mode == "exhausted" or (mode == "recover" and len(requests) == 1):
            raise httpx.ConnectError("offline original connection failure", request=request)
        if mode == "malformed":
            return httpx.Response(200, json={"code": "0", "data": []})
        if mode == "http4xx":
            return httpx.Response(429, json={"code": "rate-limit", "data": []})
        after = int(request.url.params["after"])
        data = []
        for index in range(1, 301):
            stamp = after - index * MINUTE_MS
            price = "100" if stamp // MINUTE_MS % 2 else "200"
            data.append([str(stamp), price, price, price, price, "1", "0.01", "1", "1"])
        response = httpx.Response(200, json={"code": "0", "data": data})
        response_bodies.append(response.content)
        return response

    real_client = raw_capture.CapturingAsyncClient
    monkeypatch.setattr(raw_capture, "datetime", Clock)
    monkeypatch.setattr(module, "utcnow", Clock.now)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(
        module,
        "CapturingAsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    db, archive, instrument_source, metadata = persisted_original
    if mode == "archive":

        def fail_archive(body):
            raise OSError("offline archive write failure")

        monkeypatch.setattr(archive, "put", fail_archive)
    source = module.PerpetualCloseSource(db, instrument_source)
    if mode == "recover":
        collected = await source.collect_window(inst_id=INST, max_pages=5)
        assert len(requests) == 6 and sleeps == [1]
        assert requests[0] == requests[1]
        assert len(collected.capture_ids) == 5
        prepared = source.prepare_perpetual_close_window(
            instrument_ref=instrument_source.metadata_contract_reference(metadata.capture_id),
            capture_ids=collected.capture_ids,
            as_of=collected.as_of,
        )
        assert len(prepared.value.closes) == 1441
        assert prepared.value.end_at == NOW
    else:
        error = {
            "exhausted": httpx.ConnectError,
            "malformed": ValueError,
            "http4xx": httpx.HTTPStatusError,
            "archive": OSError,
        }[mode]
        with pytest.raises(error):
            await source.collect_window(inst_id=INST, max_pages=5)
        assert len(requests) == (3 if mode == "exhausted" else 1)
        assert sleeps == ([1, 2] if mode == "exhausted" else [])
    with db.session() as session:
        captures = session.scalars(
            select(RawCaptureRecord).where(
                RawCaptureRecord.context_id.startswith(VERSION + ":" + INST + ":")
            )
        ).all()
    if mode == "archive":
        assert captures == []
        return
    failed = [row for row in captures if row.error_code]
    successful = [row for row in captures if row.status_code == 200]
    if mode in {"recover", "exhausted"}:
        assert len(failed) == (1 if mode == "recover" else 3)
        assert all(
            row.error_code == "ConnectError"
            and row.status_code is None
            and row.body_ref is None
            and row.byte_count == 0
            for row in failed
        )
        assert len({row.capture_id for row in captures}) == len(requests)
    if mode == "recover":
        assert set(collected.capture_ids) == {row.capture_id for row in successful}
        assert not set(collected.capture_ids) & {row.capture_id for row in failed}
        assert {archive.read(row.body_ref) for row in successful} == set(response_bodies)
        # A retry keeps one logical-page identity; later pages keep their own cursor.
        assert failed[0].context_id == next(
            row.context_id for row in successful if row.url == failed[0].url
        )
    elif mode == "exhausted":
        assert successful == [] and len({row.context_id for row in failed}) == 1
    else:
        assert len(captures) == 1 and captures[0].error_code is None
        assert captures[0].status_code == (429 if mode == "http4xx" else 200)
