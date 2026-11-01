"""Finite cooperative publication execution; no data or permission authority.

The renew callback alone owns its fresh connection/CAS/fence protocol. Native
work and issuer proofs remain on their original compute thread. Budgets request
cooperative stop; neither a timer nor Future cancellation proves thread exit.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from datetime import datetime, timezone
import hashlib
import math
import os
from threading import Event, Lock, Thread, get_ident
import time
from uuid import uuid4


PHASES = frozenset({
    "produce", "resource_resolve", "normal_capture", "native_material", "source_collection",
    "frozen_replay", "material_write", "preparation_verify", "material_import", "bundle_put",
    "prepared_cas", "prepared_readback", "confirmation_verify", "confirmed_cas",
    "confirmed_readback", "final_verify", "consume", "save_handoff", "cleanup",
})
REASONS = frozenset({
    "stop_requested", "compute_budget_exceeded", "save_budget_exceeded",
    "total_budget_exceeded", "renew_failed", "renew_limit_exceeded",
})
_PUBLICATION_CANCEL_REASONS = {
    "compute_budget_exceeded": "publication_execution_compute_budget_exceeded",
    "total_budget_exceeded": "publication_execution_total_budget_exceeded",
    "stop_requested": "publication_execution_stop_requested",
}
# Fixed metadata only. Renewal time can overlap the serial compute stages.
_TIMING_STAGES = ("capture", "replay", "save", "renew")
RENEW_REJECTION_CODES = frozenset({
    "publication_execution_renew_not_confirmed",
    "publication_bundle_internal_claim_lost",
    "publication_bundle_fence_requires_readonly_reconciliation",
    "publication_renew_lease_bound_required", "publication_renew_preparation_only_required",
    "publication_renew_saved_or_confirmed_requires_review",
    "publication_renew_exact_head_CAS_lost", "publication_renew_clock_did_not_advance",
    "publication_renew_independent_readback_changed",
    "publication_bundle_independent_read_changed", "publication_bundle_current_publication_changed",
    "publication_job_selected_coverage_changed", "publication_job_selection_native_identity_invalid",
    "publication_job_attempt_head_missing",
    "publication_renew_writer_begin_busy", "publication_renew_commit_unconfirmed",
})
_ACTIVE = ContextVar("publication_execution_control", default=None)


class PublicationExecutionStopped(TimeoutError):
    def __init__(self, reason: str):
        if reason not in REASONS:
            raise ValueError("publication_execution_unknown_reason")
        self.reason = reason
        super().__init__("publication_execution_" + reason)


def active_execution():
    return _ACTIVE.get()


def checkpoint(phase: str) -> None:
    control = active_execution()
    if control is not None:
        control.checkpoint(phase)


@contextmanager
def execution_stage(stage: str):
    control = active_execution()
    if control is None:
        yield
    else:
        with control.measure_stage(stage):
            yield


class PublicationExecutionControl:
    def __init__(self, claim, *, renew, cancel_preparation=None, cancel_preparation_reason=None,
                 renew_rejection_code=None,
                 clock=time.monotonic, compute_seconds=120.0,
                 save_seconds=120.0, total_seconds=270.0, renew_interval=30.0,
                 max_renewals=9):
        values = (compute_seconds, save_seconds, total_seconds, renew_interval)
        if (any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in values)
                or total_seconds > 900 or compute_seconds > total_seconds
                or save_seconds > total_seconds or renew_interval >= 90
                or type(max_renewals) is not int or not 1 <= max_renewals <= 30):
            raise ValueError("publication_execution_finite_bounds_required")
        self.clock, self.renew = clock, renew
        self.renew_rejection_code = renew_rejection_code
        self.renew_rejection = None
        self.stage_totals = {stage: 0.0 for stage in _TIMING_STAGES}
        self.stage_calls = {stage: 0 for stage in _TIMING_STAGES}
        self.stage_failures = {stage: 0 for stage in _TIMING_STAGES}
        self.stage_started = {stage: None for stage in _TIMING_STAGES}
        self.cancel_preparation = cancel_preparation
        self.cancel_preparation_reason = cancel_preparation_reason
        self.compute_seconds, self.save_seconds, self.total_seconds = values[:3]
        self.renew_interval, self.max_renewals = renew_interval, max_renewals
        self.owner_thread = get_ident()
        self.run_sha256 = hashlib.sha256(claim.run_id.encode()).hexdigest()
        self.generation = claim.generation
        self.started = self.last_progress = clock()
        self.save_started = None
        self.phase, self.state, self.reason = "produce", "running", None
        self.renewals = 0
        self.lock, self.cancel, self.renew_stop = Lock(), Event(), Event()
        self.handoff_lock, self.cancel_thread = Lock(), None
        self.publication_stop_status = "not_requested"
        self.observations, self.observation_count = [], 0
        self.last_observation_key = None
        # Empty Context: never borrow a frozen IO scope, collector or issuer.
        self.thread = Thread(target=lambda: Context().run(self._renew_loop),
                             name="publication-lease-control", daemon=False)

    def request_cancel(self, reason="stop_requested"):
        if reason not in REASONS:
            raise ValueError("publication_execution_unknown_reason")
        with self.lock:
            if self.reason is None:
                self.reason = reason
            self.state = "stop_requested"
            self.cancel.set()
            self.renew_stop.set()
            if self.publication_stop_status == "not_requested":
                if self.save_started is not None:
                    self.publication_stop_status = "not_applicable_save"
                elif self.cancel_preparation is None and self.cancel_preparation_reason is None:
                    self.publication_stop_status = "stop_unconfirmed"
                else:
                    self.publication_stop_status = "pending"
                    self.cancel_thread = Thread(
                        target=lambda: Context().run(self._cancel_preparation_once),
                        name="publication-stop-barrier", daemon=False,
                    )
                    try:
                        self.cancel_thread.start()
                    except BaseException:
                        self.publication_stop_status = "stop_unconfirmed"
            self._record_locked()

    def _cancel_preparation_once(self):
        # No borrowed collector/context or concurrent renewal writer. The old
        # exact cancellation primitive retains its owner/gen/CAS/fence policy.
        if self.thread.ident is not None:
            self.thread.join()
        with self.handoff_lock:
            with self.lock:
                if self.save_started is not None:
                    self.publication_stop_status = "not_applicable_save"
                    self._record_locked()
                    return
            try:
                if self.cancel_preparation_reason is not None:
                    result = self.cancel_preparation_reason(reason_code=_PUBLICATION_CANCEL_REASONS.get(
                        self.reason, "publication_bundle_preparation_cancelled"))
                else:
                    result = self.cancel_preparation()
                status = "stop_unconfirmed" if result is False else "publication_cancelled"
            except BaseException:
                status = "stop_unconfirmed"
            with self.lock:
                self.publication_stop_status = status
                self._record_locked()

    def poll_budget(self):
        now = self.clock()
        with self.lock:
            started, save_started = self.started, self.save_started
        if now - started >= self.total_seconds:
            self.request_cancel("total_budget_exceeded")
        elif save_started is None and now - started >= self.compute_seconds:
            self.request_cancel("compute_budget_exceeded")
        elif save_started is not None and now - save_started >= self.save_seconds:
            self.request_cancel("save_budget_exceeded")

    def checkpoint(self, phase):
        if get_ident() != self.owner_thread:
            raise RuntimeError("publication_execution_compute_thread_changed")
        if phase not in PHASES:
            raise ValueError("publication_execution_unknown_phase")
        self.poll_budget()
        if self.cancel.is_set():
            raise PublicationExecutionStopped(self.reason or "stop_requested")
        with self.lock:
            self.phase, self.last_progress = phase, self.clock()
            self._record_locked()

    @contextmanager
    def measure_stage(self, stage):
        # Accounting adds no checkpoint, permission, renewal or retry.
        if stage not in _TIMING_STAGES:
            raise ValueError("publication_execution_unknown_timing_stage")
        with self.lock:
            if self.stage_started[stage] is not None:
                raise RuntimeError("publication_execution_timing_stage_nested")
            self.stage_calls[stage] += 1
            self.stage_started[stage] = self.clock()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            with self.lock:
                self.stage_totals[stage] += max(0.0, self.clock() - self.stage_started[stage])
                self.stage_started[stage] = None
                self.stage_failures[stage] += int(failed)

    def _renew_once(self):
        self.poll_budget()
        if self.cancel.is_set():
            return False
        if self.renewals >= self.max_renewals:
            self.request_cancel("renew_limit_exceeded")
            return False
        try:
            with self.measure_stage("renew"):
                if self.renew() is False:
                    raise RuntimeError("publication_execution_renew_not_confirmed")
        except BaseException as error:
            code = "unknown"
            if type(error) is RuntimeError and error.args == ("publication_execution_renew_not_confirmed",):
                code = "publication_execution_renew_not_confirmed"
            elif self.renew_rejection_code is not None:
                try:
                    candidate = self.renew_rejection_code(error)
                    if type(candidate) is str and candidate in RENEW_REJECTION_CODES:
                        code = candidate
                except BaseException:
                    pass  # A diagnostic classifier cannot change stop behavior.
            with self.lock:
                self.renew_rejection = code
            self.request_cancel("renew_failed")
            return False
        self.renewals += 1
        return True

    def _renew_loop(self):
        while not self.renew_stop.wait(self.renew_interval):
            if not self._renew_once():
                return

    def start(self):
        self.thread.start()

    def stop_and_join(self):
        self.renew_stop.set()
        if self.thread.ident is not None:
            self.thread.join()  # Actual exit, never a timed-out Future claim.
        with self.lock:
            cancel_thread = self.cancel_thread
        if cancel_thread is not None and cancel_thread.ident is not None:
            cancel_thread.join()

    def prepare_save(self):
        self.checkpoint("save_handoff")
        self.stop_and_join()
        with self.handoff_lock:
            self.checkpoint("save_handoff")
            # Original producer session/Collector/FrozenIO is already closed.
            # Final renewal and queued cancellation never write concurrently.
            if not self._renew_once():
                raise PublicationExecutionStopped(self.reason or "renew_failed")
            with self.lock:
                if self.cancel.is_set():
                    raise PublicationExecutionStopped(self.reason or "stop_requested")
                self.save_started, self.state = self.clock(), "saving"
                self._record_locked()

    def _snapshot_locked(self):
        now = self.clock()
        timings = {stage: {
            "calls": self.stage_calls[stage],
            "completed": self.stage_calls[stage] - int(self.stage_started[stage] is not None),
            "failures": self.stage_failures[stage],
            "active": self.stage_started[stage] is not None,
            "elapsed_ms": max(0, int((self.stage_totals[stage] +
                (0.0 if self.stage_started[stage] is None else max(0.0, now - self.stage_started[stage]))) * 1000)),
        } for stage in _TIMING_STAGES}
        return {"schema_version": "publication-execution-state.v1",
                "run_sha256": self.run_sha256, "generation": self.generation,
                "phase": self.phase, "state": self.state, "reason_code": self.reason,
                "publication_stop_status": self.publication_stop_status,
                "elapsed_ms": max(0, int((now - self.started) * 1000)),
                "last_progress_ms": max(0, int((now - self.last_progress) * 1000)),
                "renewals": self.renewals, "native_exited": False,
                "renew_rejection_code": self.renew_rejection,
                "stage_timings": timings,
                "unattributed_ms": max(0, int((now - self.started) * 1000) -
                    sum(timings[stage]["elapsed_ms"] for stage in ("capture", "replay", "save")))}

    def _record_locked(self):
        # Memory only: emission is on the coordinator's event-loop thread.
        key = (self.phase, self.state, self.reason, self.publication_stop_status)
        if self.observation_count < 64 and key != self.last_observation_key:
            self.observations.append(self._snapshot_locked())
            self.observation_count += 1
            self.last_observation_key = key

    def take_observations(self):
        with self.lock:
            values, self.observations = self.observations, []
            return values

    def snapshot(self):
        with self.lock:
            return self._snapshot_locked()


class PublicationExecutionCoordinator:
    def __init__(self, *, observer=None, **bounds):
        self.bounds, self.lock = bounds, Lock()
        self.observer, self.pending_observations = observer, []
        self.active, self.last, self.stop = None, None, Event()
        self.attempt_sequence = 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="publication-compute")
        self.native_future = None
        self.native_call = None
        self.native_exit = Event()
        self.native_exit.set()

    @contextmanager
    def attempt_scope(self, claim, *, renew, cancel_preparation=None, cancel_preparation_reason=None,
                      renew_rejection_code=None):
        if self.stop.is_set():
            raise PublicationExecutionStopped("stop_requested")
        control = PublicationExecutionControl(claim, renew=renew,
            cancel_preparation=cancel_preparation, cancel_preparation_reason=cancel_preparation_reason,
            renew_rejection_code=renew_rejection_code, **self.bounds)
        with self.lock:
            if self.active is not None:
                raise RuntimeError("publication_execution_parallel_attempt_forbidden")
            self.active = control
            self.attempt_sequence += 1
            # Correlation only, after the real attempt has entered. Never an
            # owner/source/commit permission or a claim acquired by telemetry.
            if self.native_call is not None and self.native_call["owner_thread"] == get_ident():
                self.native_call["run_sha256"] = control.run_sha256
                self.native_call["generation"] = control.generation
        token = _ACTIVE.set(control)
        try:
            control.start()
            yield control
        finally:
            control.stop_and_join()
            _ACTIVE.reset(token)
            with self.lock:
                self.last, self.active = control.snapshot(), None
                self.pending_observations.extend(control.take_observations())

    def request_stop(self):
        self.stop.set()
        with self.lock:
            active = self.active
        if active is not None:
            active.request_cancel()

    def snapshot(self):
        with self.lock:
            active, last = self.active, self.last
            interval = self._native_interval_locked()
        value = active.snapshot() if active is not None else dict(last or {
            "schema_version": "publication-execution-state.v1", "state": "idle",
            "phase": None, "reason_code": None,
        })
        value["native_exited"] = self.native_exit.is_set()
        if (interval is not None and interval["run_sha256"] == value.get("run_sha256")
                and interval["generation"] == value.get("generation")):
            value["native_call_interval"] = interval
        return value

    def _native_interval_locked(self):
        call = self.native_call
        if call is None or call["run_sha256"] is None:
            return None
        # The invocation finally is earlier than concurrent Future.done. The
        # executor OS thread can remain alive idle; neither denotes OS death.
        return {"schema_version": "publication-native-interval.v1",
                **{key: call[key] for key in (
                    "invocation_id", "process_id", "run_sha256", "generation",
                    "native_started_at_utc", "native_started_monotonic_ns",
                    "native_finished_at_utc", "native_finished_monotonic_ns")},
                "native_call_finished": call["native_finished_monotonic_ns"] is not None,
                "native_future_done_observed": None if self.native_future is None else self.native_future.done(),
                "native_exit_marker_observed": self.native_exit.is_set()}

    async def run_once(self, process_one):
        if self.stop.is_set():
            raise PublicationExecutionStopped("stop_requested")
        with self.lock:
            if not self.native_exit.is_set():
                raise RuntimeError("publication_execution_previous_native_work_running")
            self.native_exit.clear()
            starting_sequence = self.attempt_sequence
            self.native_call, self.native_future = None, None
        caller_context = copy_context()
        invocation_id = uuid4().hex

        def native():
            # Actual compute-wrapper entry, not queue submission/observer time.
            call = {"invocation_id": invocation_id, "process_id": os.getpid(),
                    "owner_thread": get_ident(), "run_sha256": None, "generation": None,
                    "native_started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "native_started_monotonic_ns": time.monotonic_ns(),
                    "native_finished_at_utc": None, "native_finished_monotonic_ns": None}
            with self.lock:
                self.native_call = call
            try:
                return caller_context.run(process_one)
            finally:
                # Includes the outer process_one failure handlers. Ending an
                # attempt_scope alone must not manufacture this end timestamp.
                finished_utc = datetime.now(timezone.utc).isoformat()
                finished_monotonic_ns = time.monotonic_ns()
                with self.lock:
                    call["native_finished_at_utc"] = finished_utc
                    call["native_finished_monotonic_ns"] = finished_monotonic_ns
                self.native_exit.set()

        try:
            future = self.executor.submit(native)
        except BaseException:
            self.native_exit.set()
            raise
        with self.lock:
            self.native_future = future

        def wrap_native_future():
            value = asyncio.wrap_future(future)
            value.add_done_callback(lambda item: None if item.cancelled() else item.exception())
            return value

        wrapped = wrap_native_future()
        cancelled = False
        observations = 0
        interval_entry_emitted = False

        def observe():
            nonlocal observations, interval_entry_emitted
            with self.lock:
                active = self.active
                values, self.pending_observations = self.pending_observations, []
            if active is not None:
                values.extend(active.take_observations())
            if self.observer is not None:
                for value in values[:max(0, 63 - observations)]:
                    observations += 1
                    if not interval_entry_emitted:
                        with self.lock:
                            interval = self._native_interval_locked()
                        if (interval is not None and not interval["native_call_finished"]
                                and interval["run_sha256"] == value.get("run_sha256")
                                and interval["generation"] == value.get("generation")):
                            value["native_call_interval"] = interval
                            interval_entry_emitted = True
                    try:
                        self.observer(value)
                    except BaseException:
                        pass  # Diagnostics cannot change native ownership or cleanup.

        while not future.done():
            with self.lock:
                active = self.active
            if active is not None:
                active.poll_budget()
            observe()
            try:
                if wrapped.cancelled():
                    wrapped = wrap_native_future()
                await asyncio.wait({wrapped}, timeout=0.05)
            except asyncio.CancelledError:
                cancelled = True
                self.request_stop()
        observe()
        with self.lock:
            attempted = self.attempt_sequence != starting_sequence
        # Empty claim polls must not replay the preceding attempt's terminal.
        if self.observer is not None and attempted:
            try:
                self.observer(self.snapshot())
            except BaseException:
                pass
        # The concurrent Future and independent exit marker refer to the
        # actual compute thread, not an asyncio wrapper's cancellation state.
        result = future.result()
        if not self.native_exit.is_set():
            raise RuntimeError("publication_execution_native_exit_not_observed")
        if cancelled:
            raise asyncio.CancelledError
        return result

    def close(self):
        self.request_stop()
        self.executor.shutdown(wait=True)
