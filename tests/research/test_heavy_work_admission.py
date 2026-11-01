import asyncio
import threading

import pytest

from apps.hub_worker.work_admission import HeavyWorkAdmission
from packages.kernel.decision_hub_kernel.application.research_publication_execution import (
    PublicationExecutionCoordinator,
)


async def wait_for_native_entry(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 5), "native work did not enter"


@pytest.mark.asyncio
async def test_cancel_then_thread_error_drains_before_competing_work() -> None:
    gate = HeavyWorkAdmission()
    started, finish = threading.Event(), threading.Event()
    marks = []

    def native() -> None:
        started.set()
        assert finish.wait(5)
        marks.append("thread_exit")
        raise ValueError("thread_failure")

    async def competing_work() -> None:
        async with gate.admitted():
            marks.append("next_enter")

    task = asyncio.create_task(gate.run_thread(native))
    competing = None
    try:
        await wait_for_native_entry(started)
        task.cancel()
        competing = asyncio.create_task(competing_work())
        await asyncio.sleep(0)
        assert not task.done() and not competing.done()
        assert marks == []
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await competing
        assert marks == ["thread_exit", "next_enter"]
    finally:
        finish.set()
        await asyncio.gather(task, *([competing] if competing is not None else []),
                             return_exceptions=True)


@pytest.mark.asyncio
async def test_normal_error_propagates_and_repeated_cancel_still_drains() -> None:
    gate = HeavyWorkAdmission()

    def failure() -> None:
        raise LookupError("normal_failure")

    with pytest.raises(LookupError, match="normal_failure"):
        await gate.run_thread(failure)

    started, finish = threading.Event(), threading.Event()

    def native() -> int:
        started.set()
        assert finish.wait(5)
        return 7

    task = asyncio.create_task(gate.run_thread(native))
    try:
        await wait_for_native_entry(started)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with gate.admitted():
            pass
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_fifo_admission_leaves_dsh_wait_and_control_plane_running() -> None:
    gate = HeavyWorkAdmission()
    dsh_waiting, dsh_finish = asyncio.Event(), asyncio.Event()
    marks = []

    async def dsh() -> None:
        async with gate.admitted():
            marks.append("factory")
        dsh_waiting.set()
        await dsh_finish.wait()
        marks.append("dsh_finish")

    async def queued(name: str) -> None:
        async with gate.admitted():
            marks.append(name)

    dsh_task = asyncio.create_task(dsh())
    publication = maintenance = None
    try:
        await dsh_waiting.wait()
        async with gate.admitted():
            publication = asyncio.create_task(queued("publication_claim"))
            await asyncio.sleep(0)
            maintenance = asyncio.create_task(queued("maintenance"))
            await asyncio.sleep(0)
            heartbeat = asyncio.create_task(asyncio.sleep(0, result="heartbeat"))
            assert await heartbeat == "heartbeat"
            assert not dsh_task.done()
        await asyncio.gather(publication, maintenance)
        assert marks == ["factory", "publication_claim", "maintenance"]
        dsh_finish.set()
        await dsh_task
    finally:
        dsh_finish.set()
        tasks = [task for task in (dsh_task, publication, maintenance) if task is not None]
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_normal_coordinator_holds_gate_until_real_native_exit_on_cancel() -> None:
    gate = HeavyWorkAdmission()
    started, finish = threading.Event(), threading.Event()
    coordinator = PublicationExecutionCoordinator()
    marks = []

    def native() -> None:
        started.set()
        assert finish.wait(5)
        marks.append("native_exit")

    async def publish() -> None:
        async with gate.admitted():
            await coordinator.run_once(native)

    async def competing_work() -> None:
        async with gate.admitted():
            marks.append("maintenance_enter")

    task = asyncio.create_task(publish())
    competing = None
    try:
        await wait_for_native_entry(started)
        task.cancel()
        competing = asyncio.create_task(competing_work())
        await asyncio.sleep(0)
        assert coordinator.stop.is_set()
        assert not coordinator.native_exit.is_set()
        assert not task.done() and not competing.done()
        assert marks == []
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await competing
        assert coordinator.native_exit.is_set()
        assert coordinator.native_future.done()
        assert marks == ["native_exit", "maintenance_enter"]
    finally:
        finish.set()
        await asyncio.gather(task, *([competing] if competing is not None else []),
                             return_exceptions=True)
        coordinator.close()
