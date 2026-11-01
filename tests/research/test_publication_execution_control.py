"""Pure controls: no SQLite, model, HTTP, service or production execution."""
import asyncio
from contextvars import ContextVar
from pathlib import Path
from threading import Event, Lock, Thread, get_ident
from types import SimpleNamespace
import time

import pytest

from packages.kernel.decision_hub_kernel.application.research_publication_execution import (
    PHASES, PublicationExecutionControl, PublicationExecutionCoordinator,
    PublicationExecutionStopped, active_execution, checkpoint,
)


CLAIM = SimpleNamespace(run_id="private-run-never-log", generation=7)


class Clock:
    def __init__(self):
        self.value, self.lock = 0.0, Lock()
    def __call__(self):
        with self.lock:
            return self.value
    def set(self, value):
        with self.lock:
            self.value = value


def test_finite_defaults_and_closed_scalar_diagnostics():
    control = PublicationExecutionControl(CLAIM, renew=lambda: True)
    assert (control.compute_seconds, control.save_seconds, control.total_seconds) == (120, 120, 270)
    assert (control.renew_interval, control.max_renewals) == (30, 9)
    assert "private-run" not in repr(control.snapshot())
    assert len(control.snapshot()["run_sha256"]) == 64
    with pytest.raises(ValueError, match="unknown_phase"):
        control.checkpoint("untrusted-raw-text")
    with pytest.raises(ValueError, match="unknown_reason"):
        control.request_cancel("untrusted-raw-text")


@pytest.mark.parametrize("bounds", [
    {"compute_seconds": float("inf")}, {"total_seconds": 901},
    {"renew_interval": 90}, {"max_renewals": 31}, {"save_seconds": 0},
])
def test_invalid_bounds_fail_closed(bounds):
    with pytest.raises(ValueError, match="finite_bounds_required"):
        PublicationExecutionControl(CLAIM, renew=lambda: True, **bounds)


def test_renew_thread_has_empty_context_and_crosses_original_180_lease():
    clock, renewed = Clock(), Event()
    marker = ContextVar("test_native_scope", default=None)
    token = marker.set("compute-only")
    owner = get_ident()
    callbacks, lease = [], {"expires": 180.0}
    def renew():
        callbacks.append((get_ident(), marker.get(), active_execution()))
        lease["expires"] = clock() + 180
        renewed.set()
        return {"known": True}
    control = PublicationExecutionControl(CLAIM, renew=renew, clock=clock,
        compute_seconds=240, total_seconds=270, renew_interval=.01)
    try:
        clock.set(150)
        control.start()
        assert renewed.wait(1)
        clock.set(185)
        control.checkpoint("normal_capture")
        assert lease["expires"] > 185
        assert callbacks[0][0] != owner and callbacks[0][1:] == (None, None)
    finally:
        control.stop_and_join()
        marker.reset(token)
    assert not control.thread.is_alive()


def test_renew_failure_keeps_only_closed_reason_and_real_exit():
    def renew():
        raise ValueError("SENSITIVE_SYNTHETIC_SENTINEL")
    control = PublicationExecutionControl(CLAIM, renew=renew, renew_interval=.005)
    control.start()
    try:
        assert control.cancel.wait(1)
        with pytest.raises(PublicationExecutionStopped, match="renew_failed"):
            control.checkpoint("frozen_replay")
        assert "SENSITIVE" not in repr(control.snapshot())
    finally:
        control.stop_and_join()
    assert not control.thread.is_alive()


def test_renew_count_is_finite_and_save_does_not_continue_heartbeat():
    calls = []
    control = PublicationExecutionControl(CLAIM, renew=lambda: calls.append(1) or True,
        renew_interval=.005, max_renewals=2)
    control.start()
    try:
        assert control.cancel.wait(1)
        assert control.reason == "renew_limit_exceeded" and len(calls) == 2
    finally:
        control.stop_and_join()
    assert not control.thread.is_alive()


def test_prepare_save_joins_inflight_renew_before_final_known_handoff():
    entered, release = Event(), Event()
    calls, owner = [], get_ident()
    def renew():
        calls.append(get_ident())
        if len(calls) == 1:
            entered.set()
            assert release.wait(1)
        return True
    control = PublicationExecutionControl(CLAIM, renew=renew, renew_interval=.005)
    control.start()
    try:
        assert entered.wait(1)
        # Owner thread must hand off; callback is held until this release.
        release.set()
        control.prepare_save()
        assert not control.thread.is_alive()
        assert calls == [control.thread.ident, owner]
        assert control.state == "saving" and control.phase == "save_handoff"
        before = len(calls)
        time.sleep(.02)
        assert len(calls) == before
    finally:
        release.set()
        control.stop_and_join()


@pytest.mark.parametrize("stage,value,reason", [
    ("compute", 121, "compute_budget_exceeded"),
    ("save", 121, "save_budget_exceeded"),
    ("save", 271, "total_budget_exceeded"),
])
def test_budget_requests_cooperative_stop(stage, value, reason):
    clock = Clock()
    control = PublicationExecutionControl(CLAIM, renew=lambda: True, clock=clock)
    if stage == "save":
        control.prepare_save()
    clock.set(value)
    with pytest.raises(PublicationExecutionStopped) as error:
        control.checkpoint("preparation_verify")
    assert error.value.reason == reason
    assert control.snapshot()["state"] == "stop_requested"


def test_real_native_drain_after_budget_and_async_cancellation():
    async def scenario():
        clock, entered, release, handler = Clock(), Event(), Event(), Event()
        coordinator = PublicationExecutionCoordinator(clock=clock)
        counts = []
        def work():
            counts.append(1)
            try:
                with coordinator.attempt_scope(CLAIM, renew=lambda: True):
                    checkpoint("normal_capture")
                    entered.set()
                    assert release.wait(3)
                    checkpoint("source_collection")
            except PublicationExecutionStopped:
                handler.set()
                return "closed_failure"
        task = asyncio.create_task(coordinator.run_once(work))
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            clock.set(121)
            await asyncio.sleep(.07)
            assert coordinator.snapshot()["state"] == "stop_requested"
            assert not coordinator.snapshot()["native_exited"]
            with pytest.raises(RuntimeError, match="previous_native_work_running"):
                await coordinator.run_once(work)
            task.cancel()
            await asyncio.sleep(.07)
            assert not task.done() and not coordinator.native_future.done()
            assert not coordinator.native_exit.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert handler.is_set() and coordinator.native_exit.is_set()
            assert counts == [1]
            with pytest.raises(PublicationExecutionStopped):
                await coordinator.run_once(work)
        finally:
            release.set()
            if not task.done():
                await task
            coordinator.close()
    asyncio.run(scenario())


def test_cancelled_async_wrapper_does_not_end_native_future(monkeypatch):
    original = asyncio.wrap_future
    wrappers = []
    def wrap(future):
        value = original(future)
        wrappers.append(value)
        return value
    monkeypatch.setattr(asyncio, "wrap_future", wrap)
    async def scenario():
        entered, release = Event(), Event()
        coordinator = PublicationExecutionCoordinator()
        def work():
            entered.set()
            assert release.wait(3)
            return "actual_exit"
        task = asyncio.create_task(coordinator.run_once(work))
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            wrappers[0].cancel()
            await asyncio.sleep(.07)
            assert not task.done() and not coordinator.native_future.done()
            assert not coordinator.native_exit.is_set()
            release.set()
            assert await task == "actual_exit"
            assert coordinator.native_exit.is_set()
        finally:
            release.set()
            if not task.done():
                await task
            coordinator.close()
    asyncio.run(scenario())


def test_scope_exit_does_not_claim_failure_handler_native_exit():
    async def scenario():
        scope_exit, release = Event(), Event()
        coordinator = PublicationExecutionCoordinator()
        def work():
            with coordinator.attempt_scope(CLAIM, renew=lambda: True):
                checkpoint("normal_capture")
            scope_exit.set()
            assert release.wait(3)
            return "failure_handler_finished"
        task = asyncio.create_task(coordinator.run_once(work))
        try:
            while not scope_exit.is_set():
                await asyncio.sleep(.005)
            assert coordinator.active is None
            assert not coordinator.snapshot()["native_exited"]
            release.set()
            assert await task == "failure_handler_finished"
            assert coordinator.snapshot()["native_exited"]
        finally:
            release.set()
            if not task.done():
                await task
            coordinator.close()
    asyncio.run(scenario())


def test_phase_observer_on_event_loop_is_bounded_and_failure_is_nonfatal():
    async def scenario():
        owner, values = get_ident(), []
        def observe(value):
            assert get_ident() == owner and active_execution() is None
            values.append(value)
            raise ValueError("SENSITIVE_SYNTHETIC_SENTINEL")
        coordinator = PublicationExecutionCoordinator(observer=observe)
        def work():
            with coordinator.attempt_scope(CLAIM, renew=lambda: True) as attempt:
                checkpoint("normal_capture")
                checkpoint("frozen_replay")
                attempt.prepare_save()
                for _ in range(100):
                    checkpoint("preparation_verify")
                return "saved_by_fake_only"
        try:
            assert await coordinator.run_once(work) == "saved_by_fake_only"
            assert 4 <= len(values) <= 64
            assert {"normal_capture", "frozen_replay", "save_handoff"} <= {v["phase"] for v in values}
            assert values[-1]["native_exited"] is True
            assert all(v["phase"] in PHASES for v in values)
            assert "SENSITIVE" not in repr(values) and "private-run" not in repr(values)
        finally:
            coordinator.close()
    asyncio.run(scenario())


def test_native_error_is_preserved_not_diagnostic_observer_error():
    async def scenario():
        coordinator = PublicationExecutionCoordinator(observer=lambda _: (_ for _ in ()).throw(TypeError()))
        error = ValueError("synthetic-producer")
        def work():
            with coordinator.attempt_scope(CLAIM, renew=lambda: True):
                raise error
        try:
            with pytest.raises(ValueError) as caught:
                await coordinator.run_once(work)
            assert caught.value is error and coordinator.native_exit.is_set()
            await asyncio.sleep(0)
        finally:
            coordinator.close()
    asyncio.run(scenario())


def test_stop_before_submit_and_thread_owner_are_enforced():
    coordinator = PublicationExecutionCoordinator()
    coordinator.request_stop()
    try:
        with pytest.raises(PublicationExecutionStopped):
            asyncio.run(coordinator.run_once(lambda: pytest.fail("must not claim")))
    finally:
        coordinator.close()


def test_runtime_main_exact_wiring_and_original_bounds_remain():
    root = Path(__file__).resolve().parents[2]
    runtime = (root / "packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py").read_text()
    main = (root / "apps/hub_worker/main.py").read_text()
    assert "class _ExecutionNativeCollector(NativeCollector):" in runtime
    assert "checkpoint(\"frozen_replay\")" in runtime
    assert "worker.execution=execution" in runtime
    assert "execution=PublicationExecutionCoordinator(observer=observe_execution)" in runtime
    assert main.count("await publication_service.execution.run_once(publication_service.worker.process_one)") == 2
    assert "publication_service.execution.request_stop()" in main
    assert "128*1024*1024" in runtime and "MATERIAL_BOUND = 192*1024*1024" in runtime


def test_stop_barrier_cancels_publication_before_native_exit_and_prevents_foreign_claim():
    async def scenario():
        clock, entered, release, cancelled = Clock(), Event(), Event(), Event()
        marker = ContextVar("test_capture_context", default=None)
        state = {"state": "queued", "generation": 7, "owner": "original", "lease": 180}
        calls = []
        coordinator = PublicationExecutionCoordinator(clock=clock)
        def cancel_publication():
            assert marker.get() is None and active_execution() is None
            assert state["state"] == "queued" and state["generation"] == CLAIM.generation
            calls.append(get_ident())
            state.update(state="cancelled", generation=8, owner=None)
            cancelled.set()
            # Original cancel_preparation returns None after independent RO.
        def foreign_claim():
            if state["state"] == "queued" and state["lease"] <= clock():
                state.update(owner="foreign", generation=state["generation"] + 1)
                return "claimed"
            return None
        def work():
            token = marker.set("compute-only")
            try:
                try:
                    with coordinator.attempt_scope(CLAIM, renew=lambda: True,
                            cancel_preparation=cancel_publication):
                        entered.set()
                        assert release.wait(3)
                        checkpoint("source_collection")
                except PublicationExecutionStopped:
                    return "cancelled_publication_not_saved"
            finally:
                marker.reset(token)
        task = asyncio.create_task(coordinator.run_once(work))
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            clock.set(121)
            while not cancelled.is_set():
                await asyncio.sleep(.005)
            assert coordinator.snapshot()["publication_stop_status"] == "publication_cancelled"
            assert not coordinator.native_exit.is_set() and not task.done()
            clock.set(185)
            assert foreign_claim() is None and state["state"] == "cancelled"
            for _ in range(3):
                coordinator.active.poll_budget()
            assert len(calls) == 1
            release.set()
            assert await task == "cancelled_publication_not_saved"
            assert coordinator.native_exit.is_set()
        finally:
            release.set()
            if not task.done():
                await task
            coordinator.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, ValueError, TimeoutError, PermissionError])
def test_stop_barrier_failure_is_once_unconfirmed_and_does_not_mask_producer(failure):
    calls = []
    def cancel_publication():
        calls.append(1)
        if failure is False:
            return False
        raise failure("SENSITIVE_SYNTHETIC_SENTINEL")
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation=cancel_publication)
    control.start()
    for _ in range(3):
        control.request_cancel("compute_budget_exceeded")
    control.stop_and_join()
    assert calls == [1] and not control.cancel_thread.is_alive()
    assert control.publication_stop_status == "stop_unconfirmed"
    assert control.reason == "compute_budget_exceeded"
    assert "SENSITIVE" not in repr(control.snapshot())
    with pytest.raises(PublicationExecutionStopped) as caught:
        control.checkpoint("source_collection")
    assert caught.value.reason == "compute_budget_exceeded"


def test_saving_phase_stop_does_not_call_queued_cancel():
    calls = []
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation=lambda: calls.append(1))
    control.start()
    control.prepare_save()
    control.request_cancel()
    control.stop_and_join()
    assert calls == [] and control.cancel_thread is None
    assert control.publication_stop_status == "not_applicable_save"


def test_cancel_during_final_renew_is_serialized_and_never_enters_save():
    entered, release = Event(), Event()
    inflight, calls = {"renew": False}, []
    def renew():
        inflight["renew"] = True
        entered.set()
        assert release.wait(2)
        inflight["renew"] = False
        return True
    def cancel_publication():
        assert not inflight["renew"]
        calls.append(1)
    control = PublicationExecutionControl(CLAIM, renew=renew,
        cancel_preparation=cancel_publication)
    def request_external_stop():
        assert entered.wait(2)
        control.request_cancel()
        release.set()
    external = Thread(target=request_external_stop)
    control.start()
    external.start()
    try:
        with pytest.raises(PublicationExecutionStopped):
            control.prepare_save()
    finally:
        release.set()
        external.join()
        control.stop_and_join()
    assert calls == [1] and control.save_started is None
    assert control.publication_stop_status == "publication_cancelled"
    assert not control.cancel_thread.is_alive()
