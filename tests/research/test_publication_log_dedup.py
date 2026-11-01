"""Pure observer controls; real thread executor, no data/model/service IO."""
import ast
import asyncio
import hashlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from packages.kernel.decision_hub_kernel.application.research_publication_execution import (
    PublicationExecutionControl, PublicationExecutionCoordinator,
    PublicationExecutionStopped, checkpoint,
)

CLAIM = SimpleNamespace(run_id="private-run", generation=40)


def test_many_initial_empty_claim_polls_emit_no_attempt_record():
    async def scenario():
        values = []
        coordinator = PublicationExecutionCoordinator(observer=values.append)
        try:
            for _ in range(160):
                assert await coordinator.run_once(lambda: False) is False
            assert values == [] and coordinator.snapshot()["state"] == "idle"
        finally:
            coordinator.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["return", "raise", "cancelled_publication"])
def test_terminal_emitted_once_per_real_attempt_not_following_empty_polls(outcome):
    async def scenario():
        values = []
        coordinator = PublicationExecutionCoordinator(observer=values.append)
        failure = ValueError("SENSITIVE_SYNTHETIC_SENTINEL")
        def work():
            try:
                with coordinator.attempt_scope(CLAIM, renew=lambda: True,
                        cancel_preparation=lambda: None) as attempt:
                    checkpoint("normal_capture")
                    if outcome == "raise":
                        raise failure
                    if outcome == "cancelled_publication":
                        attempt.request_cancel("compute_budget_exceeded")
                        checkpoint("source_collection")
                    return True
            except PublicationExecutionStopped:
                return True
        try:
            if outcome == "raise":
                with pytest.raises(ValueError) as caught:
                    await coordinator.run_once(work)
                assert caught.value is failure
            else:
                assert await coordinator.run_once(work) is True
            before = list(values)
            assert 1 <= len(before) <= 64
            assert sum(value["native_exited"] for value in before) == 1
            for _ in range(160):
                assert await coordinator.run_once(lambda: False) is False
            assert values == before
            assert coordinator.snapshot()["native_exited"] is True
            if outcome == "cancelled_publication":
                assert before[-1]["reason_code"] == "compute_budget_exceeded"
                assert before[-1]["publication_stop_status"] == "publication_cancelled"
        finally:
            coordinator.close()
    asyncio.run(scenario())


def test_repeated_same_closed_phase_or_cancel_state_does_not_fill_quota():
    control = PublicationExecutionControl(CLAIM, renew=lambda: True)
    for _ in range(200):
        control.checkpoint("normal_capture")
    for _ in range(200):
        control.request_cancel("compute_budget_exceeded")
    values = control.take_observations()
    assert len(values) == 2
    assert values[0]["phase"] == "normal_capture"
    assert values[1]["publication_stop_status"] == "stop_unconfirmed"
    assert control.snapshot()["reason_code"] == "compute_budget_exceeded"


def test_next_real_generation_gets_its_own_terminal_after_idle_ticks():
    async def scenario():
        values = []
        coordinator = PublicationExecutionCoordinator(observer=values.append)
        def work(claim):
            with coordinator.attempt_scope(claim, renew=lambda: True):
                checkpoint("normal_capture")
                return True
        try:
            await coordinator.run_once(lambda: work(CLAIM))
            for _ in range(5):
                await coordinator.run_once(lambda: False)
            await coordinator.run_once(lambda: work(SimpleNamespace(run_id="different-private-run", generation=50)))
            terminal = [value for value in values if value["native_exited"]]
            assert [value["generation"] for value in terminal] == [40, 50]
            assert all("private-run" not in repr(value) for value in values)
        finally:
            coordinator.close()
    asyncio.run(scenario())


def test_exact_delta_preserves_budget_cancel_lease_save_and_native_drain_methods():
    root = Path(__file__).resolve().parents[2]
    before = root / "artifacts/core-goal-20261011/publication-log-dedup01/before/packages/kernel/decision_hub_kernel/application/research_publication_execution.py"
    after = root / "packages/kernel/decision_hub_kernel/application/research_publication_execution.py"
    def methods(path):
        tree = ast.parse(path.read_text())
        return {node.name + "." + method.name: ast.dump(method)
                for node in tree.body if isinstance(node, ast.ClassDef)
                for method in node.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))}
    dedup_base = root / "artifacts/core-goal-20261011/publication-log-dedup01"
    dedup = dedup_base / "candidate/packages/kernel/decision_hub_kernel/application/research_publication_execution.py"
    reason_base = root / "artifacts/core-goal-20261011/known-cancel-controller01"
    reason_before = reason_base / "before/packages/kernel/decision_hub_kernel/application/research_publication_execution.py"
    reason_after = reason_base / "candidate/packages/kernel/decision_hub_kernel/application/research_publication_execution.py"
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest(dedup_base / "candidate.json") == "28fd1049afb41e64d4c0ce857e522a19c9d5855a129a438b51298fe765a35cd6"
    assert digest(reason_base / "candidate.json") == "1b4e9463de46dc7528458975d9288ba98bfcfac7317cddf09d6787efd6c31c5b"
    assert digest(before) == "dbb659896e8007f5febef9cfbd756e57d06f75f58ec5dde890c76a48592ce8b1"
    assert digest(dedup) == "637b8be335027e2bc09a61f98289619465fc40dad04956806c3bd66a0f255641"
    assert reason_before.read_bytes() == dedup.read_bytes()
    assert digest(reason_after) == "6aa3dfbcfade1e224a6478663c4575bc613b2c5968d244a28be138f148961396"
    assert after.read_bytes() == reason_after.read_bytes()  # Whole current file pinned.
    a, b = methods(before), methods(dedup)
    changed = {name for name in a if a[name] != b[name]}
    assert changed == {"PublicationExecutionControl.__init__", "PublicationExecutionControl._record_locked",
                       "PublicationExecutionCoordinator.__init__", "PublicationExecutionCoordinator.attempt_scope",
                       "PublicationExecutionCoordinator.run_once"}
    for name in ("poll_budget", "request_cancel", "_renew_once", "_renew_loop", "prepare_save", "_cancel_preparation_once", "stop_and_join"):
        assert a["PublicationExecutionControl." + name] == b["PublicationExecutionControl." + name]
    c, d = methods(reason_before), methods(reason_after)
    assert {name for name in c if c[name] != d[name]} == {
        "PublicationExecutionControl.__init__", "PublicationExecutionControl.request_cancel",
        "PublicationExecutionControl._cancel_preparation_once", "PublicationExecutionCoordinator.attempt_scope",
    }
    for name in ("poll_budget", "_renew_once", "_renew_loop", "prepare_save", "stop_and_join"):
        assert c["PublicationExecutionControl." + name] == d["PublicationExecutionControl." + name]
    assert c["PublicationExecutionCoordinator.run_once"] == d["PublicationExecutionCoordinator.run_once"]
