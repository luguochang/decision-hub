"""A real temporary raw archive/SQLite source, never spot or a model forecast."""

import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord
from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
from tests.decision_product.test_okx_perpetual_original import (
    INST,
    NOW,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)


def pages(fixture, *, fault=None, refresh=False):
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import VERSION, public_url

    db, archive, _, _ = fixture
    end_ms = int(NOW.timestamp() * 1000)
    opens = [end_ms - 60000 * index for index in range(1, 1501)]
    if fault == "missing":
        opens.remove(opens[750])
    after = end_ms
    ids = []
    for page in range(5 + int(refresh)):
        selected = opens[page * 300 : (page + 1) * 300]
        kind = "base"
        if page == 5:
            kind, after = "refresh", end_ms + 60000
            selected = [end_ms] + opens[:299]
        data = []
        for stamp in selected:
            price = "100" if ((end_ms - stamp) // 60000) % 2 else "200"
            if fault == "zero":
                price = "100"
            if fault == "conflict" and page == 5 and stamp == opens[0]:
                price = "150"
            data.append([str(stamp), price, price, price, price, "7", "0.07", "7", "1"])
        raw = json.dumps({"code": "0", "data": data}, separators=(",", ":")).encode()
        digest, body_ref = archive.put(raw)
        at = NOW + timedelta(seconds=(61 if page == 5 else 1 + page))
        if fault == "future" and page == 0:
            at = NOW - timedelta(seconds=30)
        original = CaptureOriginal(
            f"close_{page}",
            f"{VERSION}:{INST}:{'1' * 32}:{kind}:{page}",
            public_url(INST, after + (60000 if fault == "cursor" and page == 2 else 0)),
            200,
            body_ref,
            digest,
            len(raw),
            VERSION,
            None,
            at,
            at + timedelta(milliseconds=10),
            at + timedelta(milliseconds=20),
        )
        if fault == "spot" and page == 0:
            original = CaptureOriginal(
                **{**asdict(original), "url": original.url.replace(INST, "BTC-USDT")}
            )
        with db.session() as session:
            session.add(RawCaptureRecord(**asdict(original), media_type="application/json"))
        ids.append(original.capture_id)
        if selected:
            after = selected[-1]
    if fault == "raw":
        target = archive.root / digest[:2] / digest
        target.write_bytes(raw + b" ")
    return tuple(ids)


def prepare(fixture, capture_ids, *, as_of=NOW + timedelta(seconds=10)):
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        PerpetualCloseSource,
    )

    db, _, instrument_source, metadata = fixture
    return PerpetualCloseSource(db, instrument_source).prepare_perpetual_close_window(
        instrument_ref=instrument_source.metadata_contract_reference(metadata.capture_id),
        capture_ids=capture_ids,
        as_of=as_of,
    )


def test_complete_original_window_computes_approved_log_return_formula(persisted_original):
    prepared = prepare(persisted_original, pages(persisted_original))
    value = prepared.value
    assert len(value.closes) == 1441 and len(value.log_returns) == 1440
    assert value.closes[-1].close_at == NOW and value.end_at == NOW
    assert value.closes[0].close_at == NOW - timedelta(days=1)
    assert value.requested_end_at == NOW and value.as_of == NOW + timedelta(seconds=10)
    assert value.instrument_ref.ref.startswith("perp_metadata_contract:")
    assert all(item.capture_hash and item.capture_metadata_hash for item in value.closes)
    with localcontext() as context:
        context.prec = 50
        expected = Decimal(2).ln() * Decimal(15).sqrt()
        assert abs(Decimal(value.thresholds[0].raw_fraction) - expected) < Decimal("1e-46")
        assert value.thresholds[0].fraction == format(expected.quantize(Decimal("1e-18")), "f")
        # 1440 sequential sums have at most 1440 half-ulps of accumulation;
        # the independent closed form uses one multiplication instead.
        assert abs(Decimal(value.sigma24_squared) - Decimal(1440) * Decimal(2).ln() ** 2) < Decimal(
            "1e-43"
        )
    assert len(value.software_hash) == len(value.method_hash) == len(value.window_hash) == 64
    db = persisted_original[0]
    from packages.kernel.decision_hub_kernel.application.perpetual_close_source import (
        read_perpetual_close_window_in_session,
    )

    with db.session() as session:
        direct = read_perpetual_close_window_in_session(
            session,
            instrument_ref=value.instrument_ref,
            capture_ids=value.capture_ids,
            as_of=value.as_of,
            archive=persisted_original[1],
            instrument_reader=persisted_original[2].read_contract_in_session,
        )
    assert direct == value

    class CorruptCache:
        def selected_rows(self, raw, digest, indices):
            rows = json.loads(raw)["data"]
            rows[0][1:5] = ["999"] * 4
            return {index: rows[index] for index in indices}

    with (
        db.session() as session,
        pytest.raises(ValueError, match="perp_close_parse_cache_mismatch"),
    ):
        read_perpetual_close_window_in_session(
            session,
            instrument_ref=value.instrument_ref,
            capture_ids=value.capture_ids,
            as_of=value.as_of,
            archive=persisted_original[1],
            instrument_reader=persisted_original[2].read_contract_in_session,
            parser=CorruptCache(),
        )
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import begin_immediate

    with db.session() as session:
        begin_immediate(session)
        prepared.verify(session)
    with db.session() as session:
        session.get(RawCaptureRecord, "close_0").byte_count += 1
    with db.session() as session, pytest.raises(ValueError, match="readset_changed"):
        begin_immediate(session)
        prepared.verify(session)


@pytest.mark.parametrize(
    "fault,match",
    [
        ("missing", "perp_close_minute_missing"),
        ("cursor", "perp_close_cursor_chain_invalid"),
        ("spot", "perp_close_request_invalid"),
        ("raw", "raw_capture_archive_corrupt"),
        ("zero", "perp_close_threshold_not_positive"),
        ("future", "perp_close_future_bar"),
    ],
)
def test_gaps_or_substituted_originals_never_get_thresholds(persisted_original, fault, match):
    ids = pages(persisted_original, fault=fault)
    with pytest.raises(ValueError, match=match):
        prepare(persisted_original, ids)


def test_receipt_cutoff_and_minute_boundary_do_not_select_an_older_window(persisted_original):
    ids = pages(persisted_original)
    with pytest.raises(ValueError, match="perp_close_not_known_at_cutoff"):
        prepare(persisted_original, ids, as_of=NOW + timedelta(seconds=2))
    with pytest.raises(ValueError, match="perp_close_latest_boundary_missing"):
        prepare(persisted_original, ids, as_of=NOW + timedelta(seconds=70))


@pytest.mark.parametrize("fault", [None, "conflict"])
def test_once_only_refresh_must_cover_new_boundary_and_agree_with_prior_closes(
    persisted_original, fault
):
    ids = pages(persisted_original, refresh=True, fault=fault)
    if fault:
        with pytest.raises(ValueError, match="perp_close_conflicting_close"):
            prepare(persisted_original, ids, as_of=NOW + timedelta(seconds=70))
    else:
        value = prepare(persisted_original, ids, as_of=NOW + timedelta(seconds=70)).value
        assert value.requested_end_at == NOW and value.end_at == NOW + timedelta(minutes=1)
        assert len(value.closes) == 1441 and value.closes[-1].capture_id == "close_5"


@pytest.mark.asyncio
async def test_collector_uses_actual_capture_pipeline_and_once_only_boundary_refresh(
    persisted_original, monkeypatch
):
    from datetime import datetime

    import httpx

    from packages.kernel.decision_hub_kernel.application import perpetual_close_source as module
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import MINUTE_MS
    from packages.provider_adapters.research import raw_capture

    class Clock(datetime):
        current = NOW + timedelta(seconds=10)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    requests = []

    async def respond(request):
        requests.append(str(request.url))
        after = int(request.url.params["after"])
        data = []
        for index in range(1, 301):
            stamp = after - index * MINUTE_MS
            price = "100" if stamp // MINUTE_MS % 2 else "200"
            data.append([str(stamp), price, price, price, price, "1", "0.01", "1", "1"])
        if len(requests) == 5:
            Clock.current = NOW + timedelta(seconds=61)
        return httpx.Response(200, json={"code": "0", "data": data})

    real_client = raw_capture.CapturingAsyncClient
    monkeypatch.setattr(raw_capture, "datetime", Clock)
    monkeypatch.setattr(module, "utcnow", Clock.now)
    monkeypatch.setattr(
        module,
        "CapturingAsyncClient",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(respond),
        ),
    )
    db, _, instrument_source, _ = persisted_original
    source = module.PerpetualCloseSource(db, instrument_source)
    collected = await source.collect_window(inst_id=INST)
    assert len(requests) == len(collected.capture_ids) == 6
    assert collected.requested_end_at == NOW and collected.finished_at == Clock.current
    value = prepare(persisted_original, collected.capture_ids, as_of=collected.as_of).value
    assert value.end_at == NOW + timedelta(minutes=1) and len(value.closes) == 1441
    assert len(set(requests)) == 6
