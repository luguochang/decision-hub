"""Pure entry-order tests; original-reader seams stand in for authenticated inputs.

No SQL, checkpoint store, Worker, graph invocation, model, or historical DB is opened.
The default source is this checkout's real production file; no temp asset is required.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from packages.kernel.decision_hub_kernel.application import research_context_selection
from tests.dsh_native.test_dsh_web_runtime import _request  # pyright: ignore[reportPrivateUsage]

REPOSITORY = Path(__file__).resolve().parents[2]
PRODUCTION = (
    REPOSITORY / "packages/kernel/decision_hub_kernel/application/research_graph_binding.py"
)
AT = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)
OWNER = "recovery-unit-owner"
RECIPE_ERROR = "research_graph_recipe_current_implementation_unavailable"


@pytest.fixture
def binding():
    source = Path(os.environ.get("RESEARCH_GRAPH_BINDING_SOURCE", str(PRODUCTION)))
    name = "research_resume_deadline_under_test"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == source.resolve()
    yield module
    sys.modules.pop(name, None)


def arrange(binding: Any, monkeypatch: pytest.MonkeyPatch, *, seconds: int = 0):
    request = _request("run-deadline-order").model_copy(
        update={"deadline_at": AT + timedelta(seconds=seconds)}
    )
    state = binding.initial_research_state(request)
    run = SimpleNamespace(
        run_id=request.run_id, status="running", artifact_id=None,
        lease_owner=OWNER, lease_expires_at=AT + timedelta(minutes=5),
    )

    class MemorySession:
        def get(self, *_args: Any, **_kwargs: Any):
            return run

        def scalar(self, *_args: Any, **_kwargs: Any):
            return None

        def execute(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("no_SQL_allowed_in_this_unit_test")

    def native_required(*_args: Any, **_kwargs: Any) -> bool:
        return True

    def original_request(*_args: Any):
        return request

    monkeypatch.setattr(binding, "utcnow", lambda: AT)
    monkeypatch.setattr(binding, "graph_requires_native_binding_in_session", native_required)
    monkeypatch.setattr(binding, "_original_request", original_request)
    return request, state, run, MemorySession()


def recipe_seam(binding: Any, monkeypatch: pytest.MonkeyPatch, *, available: bool):
    def read_recipe(*_args: Any):
        if not available:
            raise ValueError(RECIPE_ERROR)
        return None

    monkeypatch.setattr(binding, "_recipe_selection", read_recipe)


@pytest.mark.parametrize("seconds", [0, -1], ids=["equal", "elapsed"])
@pytest.mark.parametrize("available", [False, True], ids=["recipe-changed", "recipe-same"])
def test_expired_original_task_stops_before_current_recipe(
    binding: Any, monkeypatch: pytest.MonkeyPatch, seconds: int, available: bool,
):
    _, state, _, session = arrange(binding, monkeypatch, seconds=seconds)
    recipe_seam(binding, monkeypatch, available=available)
    with pytest.raises(PermissionError, match="^context_selection_deadline_elapsed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


@pytest.mark.parametrize("available", [False, True], ids=["recipe-changed", "recipe-same"])
def test_unexpired_original_preserves_current_recipe_guard(
    binding: Any, monkeypatch: pytest.MonkeyPatch, available: bool,
):
    _, state, _, session = arrange(binding, monkeypatch, seconds=1)
    recipe_seam(binding, monkeypatch, available=available)
    if available:
        active, prepared = binding.prepare_graph_state_in_session(session, state, OWNER)
        assert active is True and prepared == state
    else:
        with pytest.raises(ValueError, match="^" + RECIPE_ERROR + "$"):
            binding.prepare_graph_state_in_session(session, state, OWNER)


def test_checkpoint_cannot_extend_original_deadline(binding: Any, monkeypatch: pytest.MonkeyPatch):
    request, state, _, session = arrange(binding, monkeypatch, seconds=-1)
    state["request"] = request.model_copy(
        update={"deadline_at": AT + timedelta(days=1)}
    ).model_dump(mode="json")
    recipe_seam(binding, monkeypatch, available=False)
    with pytest.raises(ValueError, match="^research_graph_checkpoint_task_changed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_invalid_original_task_is_not_relabelled_deadline(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = arrange(binding, monkeypatch, seconds=-1)
    recipe_seam(binding, monkeypatch, available=False)

    def invalid_original(*_args: Any):
        raise ValueError("original_bundle_receipt_invalid")

    monkeypatch.setattr(binding, "_original_request", invalid_original)
    with pytest.raises(ValueError, match="^original_bundle_receipt_invalid$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


@pytest.mark.parametrize("change", ["owner", "lease", "status", "artifact"])
def test_original_writer_permission_remains_first(
    binding: Any, monkeypatch: pytest.MonkeyPatch, change: str,
):
    _, state, run, session = arrange(binding, monkeypatch, seconds=-1)
    recipe_seam(binding, monkeypatch, available=False)
    if change == "owner":
        run.lease_owner = "other-owner"
    elif change == "lease":
        run.lease_expires_at = AT
    elif change == "status":
        run.status = "cancelled"
    else:
        run.artifact_id = "already-committed"
    with pytest.raises(PermissionError, match="^research_graph_writer_permission_lost$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_legacy_dispatch_is_unchanged(binding: Any, monkeypatch: pytest.MonkeyPatch):
    _, state, _, session = arrange(binding, monkeypatch, seconds=-1)

    def legacy(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(binding, "graph_requires_native_binding_in_session", legacy)
    assert binding.prepare_graph_state_in_session(session, state, None) == (False, state)


def test_expiry_crossing_after_permission_still_stops(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = arrange(binding, monkeypatch, seconds=0)
    clock = iter([AT - timedelta(microseconds=1), AT])
    monkeypatch.setattr(binding, "utcnow", lambda: next(clock))
    recipe_seam(binding, monkeypatch, available=True)
    with pytest.raises(PermissionError, match="^context_selection_deadline_elapsed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_existing_context_fence_uses_same_equal_boundary():
    service = research_context_selection.ContextSelectionService(
        None, clock=lambda: AT,  # type: ignore[arg-type]
    )
    with pytest.raises(PermissionError, match="^context_selection_deadline_elapsed$"):
        service._fence(None, "run-deadline-order", OWNER, AT)  # type: ignore[arg-type]
