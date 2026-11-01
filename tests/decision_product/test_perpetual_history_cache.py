"""Same-hour Entry collection must retain genuine history receipts without public I/O."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from packages.kernel.decision_hub_kernel.application import perpetual_base_rate_source as source
from packages.kernel.decision_hub_kernel.application.perpetual_research_entry import (
    PerpetualResearchEntryService,
)
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)

AT = datetime(2026, 10, 31, 12, 10, tzinfo=UTC)
BTC = "BTC-USDT-SWAP"


def entry():
    database = object()
    instrument_source = SimpleNamespace(database=database, archive=object(), lineage=object())
    binding = SimpleNamespace(database=database, source=instrument_source)
    return PerpetualResearchEntryService(database, binding, total_deadline_seconds=3600)


def test_real_entry_reuses_original_receipt_within_hour(monkeypatch):
    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("original-hour-0", "original-hour-1"), AT)

    async def collect(bound_source, *, inst_id):
        calls.append((bound_source, inst_id))
        return original

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    first = asyncio.run(service.collect_history(inst_id=BTC))
    second = asyncio.run(service.collect_history(inst_id=BTC))
    assert first is second is original
    assert len(calls) == 1
    assert calls[0] == (service.binding.source, BTC)


def test_real_entry_coalesces_concurrent_callers(monkeypatch):
    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("concurrent-original",), AT)

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        await asyncio.sleep(0.01)
        return original

    async def run():
        values = await asyncio.gather(*(service.collect_history(inst_id=BTC) for _ in range(8)))
        assert all(value is original for value in values)

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(run())
    assert calls == [BTC]


def test_instrument_entry_and_source_are_isolated(monkeypatch):
    first, second, calls = entry(), entry(), []

    async def collect(bound_source, *, inst_id):
        calls.append((bound_source, inst_id))
        return source.CollectedHourlyHistory((f"original-{len(calls)}",), AT)

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    btc = asyncio.run(first.collect_history(inst_id=BTC))
    eth = asyncio.run(first.collect_history(inst_id="ETH-USDT-SWAP"))
    other = asyncio.run(second.collect_history(inst_id=BTC))
    assert len({btc.capture_ids, eth.capture_ids, other.capture_ids}) == 3
    assert asyncio.run(first.collect_history(inst_id=BTC)) is btc
    assert len(calls) == 3


def test_hour_boundary_expires_without_rewriting_receipt(monkeypatch):
    service, calls, clock = entry(), [], [AT]

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        return source.CollectedHourlyHistory((f"original-{len(calls)}",), clock[0])

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: clock[0])
    first = asyncio.run(service.collect_history(inst_id=BTC))
    clock[0] = AT.replace(minute=59, second=59)
    assert asyncio.run(service.collect_history(inst_id=BTC)) is first
    assert first.as_of == AT
    assert first.capture_ids == ("original-1",)
    clock[0] = AT.replace(hour=13, minute=0)
    refreshed = asyncio.run(service.collect_history(inst_id=BTC))
    assert refreshed.capture_ids == ("original-2",)
    assert refreshed.as_of == clock[0]
    assert calls == [BTC, BTC]


def test_cross_boundary_completion_does_not_seed_new_hour(monkeypatch):
    service, calls, clock = entry(), [], [AT]

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        clock[0] = AT.replace(hour=13, minute=0)
        return source.CollectedHourlyHistory((f"original-{len(calls)}",), clock[0])

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: clock[0])
    first = asyncio.run(service.collect_history(inst_id=BTC))
    second = asyncio.run(service.collect_history(inst_id=BTC))
    assert first.capture_ids == ("original-1",)
    assert second.capture_ids == ("original-2",)
    assert len(calls) == 2


def test_adapter_version_change_requires_new_collection(monkeypatch):
    service, calls = entry(), []

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        return source.CollectedHourlyHistory((f"original-{len(calls)}",), AT)

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    first = asyncio.run(service.collect_history(inst_id=BTC))
    monkeypatch.setattr(source, "VERSION", "new-history-adapter-version")
    second = asyncio.run(service.collect_history(inst_id=BTC))
    assert second.capture_ids != first.capture_ids
    assert len(calls) == 2


def test_none_and_exception_are_retryable(monkeypatch):
    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("recovered",), AT)

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        if len(calls) == 1:
            return None
        if len(calls) == 2:
            raise RuntimeError("collection-failed")
        return original

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    assert asyncio.run(service.collect_history(inst_id=BTC)) is None
    import pytest

    with pytest.raises(RuntimeError, match="collection-failed"):
        asyncio.run(service.collect_history(inst_id=BTC))
    assert asyncio.run(service.collect_history(inst_id=BTC)) is original
    assert asyncio.run(service.collect_history(inst_id=BTC)) is original
    assert len(calls) == 3


def test_cancelled_waiter_does_not_cancel_shared_producer(monkeypatch):
    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("not-cancelled",), AT)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def collect(bound_source, *, inst_id):
            calls.append(inst_id)
            started.set()
            await release.wait()
            return original

        monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
        first = asyncio.create_task(service.collect_history(inst_id=BTC))
        await started.wait()
        other = asyncio.create_task(service.collect_history(inst_id=BTC))
        await asyncio.sleep(0)
        first.cancel()
        import pytest

        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await asyncio.wait_for(other, 1) is original
        assert await service.collect_history(inst_id=BTC) is original

    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(run())
    assert calls == [BTC]


def test_cancelled_producer_clears_pending_and_informs_all_waiters(monkeypatch):
    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("after-cancel",), AT)

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        if len(calls) == 1:
            await asyncio.sleep(0.01)
            raise asyncio.CancelledError
        return original

    async def run():
        results = await asyncio.wait_for(
            asyncio.gather(*(service.collect_history(inst_id=BTC) for _ in range(3)),
                           return_exceptions=True), 1
        )
        assert all(isinstance(value, asyncio.CancelledError) for value in results)
        assert await service.collect_history(inst_id=BTC) is original

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(run())
    assert calls == [BTC, BTC]


def test_injected_close_collector_never_starts_history_io(monkeypatch):
    service = entry()

    async def recorded(*, inst_id):
        return None

    async def network_forbidden(*args, **kwargs):
        raise AssertionError("injected/replay collector triggered public history")

    service.collect_window = recorded
    monkeypatch.setattr(source, "collect_hourly_history_or_none", network_forbidden)
    assert asyncio.run(service.collect_history(inst_id=BTC)) is None


def test_threaded_event_loops_share_one_producer(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    service, calls = entry(), []
    started, joined, release = Event(), Event(), Event()
    original = source.CollectedHourlyHistory(("cross-loop-original",), AT)

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.001)
        return original

    async def other_loop():
        waiter = asyncio.create_task(service.collect_history(inst_id=BTC))
        await asyncio.sleep(0)
        joined.set()
        return await asyncio.wait_for(waiter, 2)

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(asyncio.run, service.collect_history(inst_id=BTC))
        try:
            assert started.wait(2)
            second = pool.submit(asyncio.run, other_loop())
            assert joined.wait(2)
        finally:
            release.set()
        assert first.result(timeout=2) is original
        assert second.result(timeout=2) is original
    assert calls == [BTC]


def test_loop_shutdown_cancels_producer_and_allows_later_loop_retry(monkeypatch):
    import pytest

    service, calls = entry(), []
    original = source.CollectedHourlyHistory(("new-loop-recovered",), AT)

    async def abandon():
        started = asyncio.Event()

        async def collect(bound_source, *, inst_id):
            calls.append(inst_id)
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
        waiter = asyncio.create_task(service.collect_history(inst_id=BTC))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        # asyncio.run now cancels the still-live shared producer during shutdown.

    async def recover(bound_source, *, inst_id):
        calls.append(inst_id)
        return original

    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(abandon())
    monkeypatch.setattr(source, "collect_hourly_history_or_none", recover)
    assert asyncio.run(service.collect_history(inst_id=BTC)) is original
    assert calls == [BTC, BTC]


def test_producer_cancelled_before_first_step_releases_waiter(monkeypatch):
    import pytest

    service = entry()

    async def forbidden(bound_source, *, inst_id):
        raise AssertionError("cancelled producer must not begin I/O")

    async def run():
        waiter = asyncio.create_task(service.collect_history(inst_id=BTC))
        await asyncio.sleep(0)
        producer, = service._history_cache._tasks
        producer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, 1)
        assert not service._history_cache._pending

    monkeypatch.setattr(source, "collect_hourly_history_or_none", forbidden)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(run())


def test_success_cache_is_bounded_and_evicts_least_recent(monkeypatch):
    service, calls = entry(), []
    monkeypatch.setattr(service._history_cache, "MAX_ENTRIES", 2)

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        return source.CollectedHourlyHistory((f"receipt-{len(calls)}",), AT)

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: AT)
    btc = asyncio.run(service.collect_history(inst_id=BTC))
    asyncio.run(service.collect_history(inst_id="ETH-USDT-SWAP"))
    assert asyncio.run(service.collect_history(inst_id=BTC)) is btc
    asyncio.run(service.collect_history(inst_id="SOL-USDT-SWAP"))
    assert asyncio.run(service.collect_history(inst_id=BTC)) is btc
    eth = asyncio.run(service.collect_history(inst_id="ETH-USDT-SWAP"))
    assert eth.capture_ids == ("receipt-4",)
    assert calls == [BTC, "ETH-USDT-SWAP", "SOL-USDT-SWAP", "ETH-USDT-SWAP"]


def test_pending_bound_skips_new_io_but_existing_waiter_can_join(monkeypatch):
    service, calls = entry(), []
    monkeypatch.setattr(service._history_cache, "MAX_ENTRIES", 1)
    original = source.CollectedHourlyHistory(("bounded-pending",), AT)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def collect(bound_source, *, inst_id):
            calls.append(inst_id)
            started.set()
            await release.wait()
            return original

        monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
        first = asyncio.create_task(service.collect_history(inst_id=BTC))
        await started.wait()
        assert await service.collect_history(inst_id="ETH-USDT-SWAP") is None
        other = asyncio.create_task(service.collect_history(inst_id=BTC))
        await asyncio.sleep(0)
        release.set()
        assert await first is original
        assert await other is original

    monkeypatch.setattr(source, "utcnow", lambda: AT)
    asyncio.run(run())
    assert calls == [BTC]


def test_cached_receipt_still_uses_reader_to_reject_corrupt_original(
    monkeypatch, persisted_original
):
    from tests.decision_product.test_okx_perpetual_original import NOW
    from tests.decision_product.test_perpetual_base_rate_source import hourly_pages, read

    service, calls = entry(), []
    ids = hourly_pages(persisted_original)
    original = source.CollectedHourlyHistory(ids, NOW.replace(minute=1))

    async def collect(bound_source, *, inst_id):
        calls.append(inst_id)
        return original

    monkeypatch.setattr(source, "collect_hourly_history_or_none", collect)
    monkeypatch.setattr(source, "utcnow", lambda: NOW.replace(minute=1))
    collected = asyncio.run(service.collect_history(inst_id=BTC))
    before = read(persisted_original, collected.capture_ids, as_of=collected.as_of)
    assert before["status"] == "available"
    database, archive, _, _ = persisted_original
    from packages.kernel.decision_hub_kernel.persistence.db import RawCaptureRecord

    with database.session() as session:
        record = session.get(RawCaptureRecord, ids[0])
        target = archive.root / record.body_hash[:2] / record.body_hash
        target.write_bytes(target.read_bytes() + b" ")
    reused = asyncio.run(service.collect_history(inst_id=BTC))
    result = read(persisted_original, reused.capture_ids, as_of=reused.as_of)
    assert reused is original
    assert (result["status"], result["reason"]) == ("unavailable", "raw_capture_archive_corrupt")
    assert calls == [BTC]
