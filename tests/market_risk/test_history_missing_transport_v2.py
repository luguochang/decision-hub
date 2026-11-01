"""Private transport controls: no external HTTP, DB, acquisition or fitting."""

import asyncio
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from packages.kernel.decision_hub_kernel.ports.market_path import spot_scope
from packages.provider_adapters.market import history_missing_transport_v2 as module
from packages.provider_adapters.research.raw_capture import RawBodyArchive

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(minutes=3)
CURSOR = str(int(END.timestamp() * 1000))


def row(index):
    return [
        str(int((START + timedelta(minutes=index)).timestamp() * 1000)),
        "100",
        "102",
        "99",
        "101",
        "1",
        "100",
        "100",
        "1",
    ]


def fetch(adapter, **changes):
    return adapter.fetch_one(
        **{
            "context_id": "hobs_v2_private_job_01",
            "scope": spot_scope("BTC-USDT"),
            "cursor": CURSOR,
            "start_at": START,
            "end_at": END,
            **changes,
        }
    )


def adapter(tmp_path, handler):
    return module.HistoryMissingTransportV2(
        RawBodyArchive(tmp_path), transport=httpx.MockTransport(handler), fixture_mode=True
    )


def check_metadata(result):
    entry = result.capture
    assert entry["capture_id"].startswith("raw_")
    assert entry["context_id"] == "hobs_v2_private_job_01"
    assert entry["started_at"] <= entry["completed_at"] <= entry["recorded_at"]
    assert all(entry[key].tzinfo == UTC for key in ("started_at", "completed_at", "recorded_at"))
    assert entry["adapter_version"] == module.CAPTURE_PRODUCER_V2
    assert entry["original_decoder_version"] == module.ORIGINAL_DECODER_V1
    assert entry["error_code"] == result.error_code
    assert entry["fixture_mode"] is True and result.financial_qualified is False
    assert result.source_files == module.SOURCE_FILES


def test_one_get_original_decoder_and_no_initialization_side_effects(tmp_path):
    calls = []
    body = json.dumps({"code": "0", "data": [row(2), row(1), row(0)]}).encode()

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        assert request.url.scheme == "https" and request.url.host == "www.okx.com"
        assert request.url.path == "/api/v5/market/history-candles"
        assert parse_qs(request.url.query.decode()) == {
            "instId": ["BTC-USDT"],
            "bar": ["1m"],
            "after": [CURSOR],
            "limit": ["300"],
        }
        assert request.extensions["timeout"] == {"connect": 15, "read": 15, "write": 15, "pool": 15}
        return httpx.Response(
            200, stream=httpx.ByteStream(body), headers={"Content-Type": "application/json"}
        )

    client = adapter(tmp_path, handler)
    assert calls == [] and list(tmp_path.iterdir()) == []
    result = asyncio.run(fetch(client))
    check_metadata(result)
    assert len(calls) == 1 and result.error_code is None and not result.cancelled
    assert result.body == body and result.page is not None and len(result.page.candles) == 3
    assert result.page.next_cursor is None
    assert len(result.semantic_rows) == 3
    assert result.capture["body_hash"] == hashlib.sha256(body).hexdigest()
    assert result.capture["byte_count"] == len(body)
    assert client.archive.read(result.capture["body_ref"]) == body
    assert all(bar.accepted_at is None for bar in result.page.candles)
    root = Path(module.__file__).resolve().parents[3]
    assert all(
        hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        for name, digest in result.source_files
    )


@pytest.mark.parametrize(
    "failure, expected, cancelled",
    [
        (httpx.ReadTimeout("private timeout"), "history_missing_transport_timeout", False),
        (TimeoutError(), "history_missing_transport_timeout", False),
        (httpx.ConnectError("private unavailable"), "history_missing_transport_unavailable", False),
        (asyncio.CancelledError(), "history_missing_transport_cancelled", True),
    ],
)
def test_error_attempt_returns_missing_original_metadata_once(
    tmp_path, failure, expected, cancelled
):
    calls = []

    async def handler(request):
        calls.append(request)
        raise failure

    result = asyncio.run(fetch(adapter(tmp_path, handler)))
    check_metadata(result)
    assert len(calls) == 1 and result.error_code == expected and result.cancelled == cancelled
    assert result.body is None and result.page is None and result.semantic_rows == ()
    assert result.capture["body_ref"] is None and result.capture["body_hash"] is None
    assert list(tmp_path.iterdir()) == []


def test_actual_outer_cancellation_and_partial_stream_do_not_lose_metadata(tmp_path):
    entered = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"private partial bytes"
            entered.set()
            await asyncio.Event().wait()

    async def handler(_request):
        return httpx.Response(200, stream=WaitingStream())

    async def run():
        task = asyncio.create_task(fetch(adapter(tmp_path, handler)))
        await entered.wait()
        task.cancel()
        return await task

    result = asyncio.run(run())
    check_metadata(result)
    assert result.error_code == "history_missing_transport_cancelled" and result.cancelled
    assert result.capture["status_code"] == 200
    assert result.capture["byte_count"] == len(b"private partial bytes")
    assert result.body is None and result.capture["body_ref"] is None
    assert result.page is None and list(tmp_path.iterdir()) == []


def test_outer_attempt_deadline_and_archive_failure_remain_explicit(tmp_path, monkeypatch):
    assert module.ATTEMPT_TIMEOUT_SECONDS == 15
    calls = []

    async def waiting_handler(request):
        calls.append(request)
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "ATTEMPT_TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(fetch(adapter(tmp_path, waiting_handler)))
    check_metadata(result)
    assert len(calls) == 1 and result.error_code == "history_missing_transport_timeout"
    assert result.body is None and result.capture["body_ref"] is None
    bad_root = tmp_path / "non_directory_archive"
    bad_root.write_bytes(b"private file")
    body = json.dumps({"code": "0", "data": [row(0)]}).encode()
    client = adapter(bad_root, lambda _request: httpx.Response(200, stream=httpx.ByteStream(body)))
    result = asyncio.run(fetch(client))
    check_metadata(result)
    assert result.error_code == "history_missing_archive_unavailable"
    assert result.body == body and result.capture["body_ref"] is None and result.page is None


@pytest.mark.parametrize(
    "kind", ["http", "oversize", "unclosed", "ohlc", "duplicate", "deepjson", "compressed"]
)
def test_http_body_cap_and_decoder_failures_remain_auditable(tmp_path, kind):
    rows = [row(2), row(1), row(0)]
    if kind == "unclosed":
        rows[0][8] = "0"
    elif kind == "ohlc":
        rows[0][2] = "98"
    elif kind == "duplicate":
        rows[1] = rows[0]
    body = (
        b"x" * (module.MAX_BODY_BYTES + 1)
        if kind == "oversize"
        else json.dumps({"code": "0", "data": rows}).encode()
    )
    if kind == "deepjson":
        body = b'{"code":"0","data":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}"
    if kind == "compressed":
        body = gzip.compress(b"x" * (module.MAX_BODY_BYTES + 1))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            503 if kind == "http" else 200,
            stream=httpx.ByteStream(body),
            headers={"Content-Encoding": "gzip"} if kind == "compressed" else {},
        )

    client = adapter(tmp_path, handler)
    result = asyncio.run(fetch(client))
    check_metadata(result)
    assert len(calls) == 1 and result.error_code is not None and result.page is None
    if kind == "oversize":
        assert result.error_code == "history_missing_body_too_large"
        assert result.body is None and result.capture["body_ref"] is None
        assert result.capture["byte_count"] > module.MAX_BODY_BYTES
    else:
        assert result.body == body and client.archive.read(result.capture["body_ref"]) == body
        assert result.error_code == (
            "history_missing_http_503"
            if kind == "http"
            else "history_missing_content_encoding_unsupported"
            if kind == "compressed"
            else "history_missing_original_decode_invalid"
        )


def test_invalid_request_and_fixture_gate_prevent_any_attempt(tmp_path):
    calls = []
    client = adapter(tmp_path, lambda request: calls.append(request))
    for changes in (
        {"cursor": " " + CURSOR},
        {"cursor": str(int(START.timestamp() * 1000))},
        {"scope": spot_scope("BTC-USDT", venue="other")},
        {"start_at": START.replace(tzinfo=None)},
        {"context_id": ""},
    ):
        with pytest.raises(ValueError):
            asyncio.run(fetch(client, **changes))
    with pytest.raises(ValueError, match="fixture_transport"):
        module.HistoryMissingTransportV2(RawBodyArchive(tmp_path), transport=client.transport)
    with pytest.raises(ValueError, match="explicit_transport"):
        module.HistoryMissingTransportV2(RawBodyArchive(tmp_path), fixture_mode=True)
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_multi_day_gap_uses_one_local_original_decoder_page(tmp_path):
    body = json.dumps({"code": "0", "data": [row(2), row(1), row(0)]}).encode()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(body))

    result = asyncio.run(fetch(adapter(tmp_path, handler), start_at=START - timedelta(days=40)))
    check_metadata(result)
    assert len(calls) == 1 and result.error_code is None and result.page is not None
    assert result.page.next_cursor == str(int(START.timestamp() * 1000))
    assert [bar.open_at for bar in result.page.candles] == [
        START + timedelta(minutes=index) for index in range(3)
    ]
