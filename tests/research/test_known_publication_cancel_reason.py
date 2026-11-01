"""Pure reason callback contract; no publication/native database writes."""
import asyncio
from types import SimpleNamespace
from threading import get_ident

import pytest

from packages.kernel.decision_hub_kernel.application.research_publication_execution import (
    REASONS, PublicationExecutionControl, PublicationExecutionCoordinator,
    PublicationExecutionStopped, active_execution,
)

CLAIM = SimpleNamespace(run_id="private-known-cancel", generation=40)
EXPECTED = {
    "compute_budget_exceeded": "publication_execution_compute_budget_exceeded",
    "total_budget_exceeded": "publication_execution_total_budget_exceeded",
    "stop_requested": "publication_execution_stop_requested",
}


@pytest.mark.parametrize("reason", sorted(REASONS))
def test_reason_callback_has_only_four_closed_codes_and_is_once_separate_context(reason):
    calls, old, owner = [], [], get_ident()
    def cancel_reason(*, reason_code):
        assert get_ident() != owner and active_execution() is None
        calls.append(reason_code)
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation=lambda: old.append(1), cancel_preparation_reason=cancel_reason)
    control.start()
    for _ in range(3):
        control.request_cancel(reason)
    control.stop_and_join()
    assert calls == [EXPECTED.get(reason, "publication_bundle_preparation_cancelled")]
    assert old == [] and not control.cancel_thread.is_alive()
    assert control.reason == reason and control.publication_stop_status == "publication_cancelled"


def test_legacy_callback_no_arguments_stays_compatible():
    calls = []
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation=lambda: calls.append("no_args"))
    control.start()
    control.request_cancel()
    control.stop_and_join()
    assert calls == ["no_args"] and control.publication_stop_status == "publication_cancelled"


def test_scope_passes_reason_callback_without_relaxing_cancel_or_save_boundary():
    async def scenario():
        calls = []
        coordinator = PublicationExecutionCoordinator()
        def work():
            with coordinator.attempt_scope(CLAIM, renew=lambda: True,
                    cancel_preparation_reason=lambda **kw: calls.append(kw)) as attempt:
                attempt.request_cancel("compute_budget_exceeded")
                with pytest.raises(PublicationExecutionStopped):
                    attempt.prepare_save()
                return True
        try:
            assert await coordinator.run_once(work) is True
            assert calls == [{"reason_code": "publication_execution_compute_budget_exceeded"}]
            assert coordinator.snapshot()["native_exited"]
        finally:
            coordinator.close()
    asyncio.run(scenario())


def test_reason_callback_failure_remains_unconfirmed_no_raw_text_or_legacy_second_try():
    calls, old = [], []
    def reject(*, reason_code):
        calls.append(reason_code)
        raise ValueError("SENSITIVE_SYNTHETIC_SENTINEL")
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation=lambda: old.append(1), cancel_preparation_reason=reject)
    control.start()
    control.request_cancel("compute_budget_exceeded")
    control.stop_and_join()
    assert len(calls) == 1 and not old
    assert control.publication_stop_status == "stop_unconfirmed"
    assert "SENSITIVE" not in repr(control.snapshot())


def test_saving_still_never_calls_queued_reason_writer():
    calls = []
    control = PublicationExecutionControl(CLAIM, renew=lambda: True,
        cancel_preparation_reason=lambda **kw: calls.append(kw))
    control.start()
    control.prepare_save()
    control.request_cancel()
    control.stop_and_join()
    assert calls == [] and control.publication_stop_status == "not_applicable_save"
