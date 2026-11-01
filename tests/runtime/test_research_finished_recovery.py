"""Preserve finished-final recovery while fencing unfinished work; pure reader seams."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from test_research_resume_deadline import (
    AT,
    OWNER,
    RECIPE_ERROR,
    arrange,
    recipe_seam,
)

pytest_plugins = ["test_research_resume_deadline"]


def finished_case(module: Any, monkeypatch: pytest.MonkeyPatch):
    request, base, run, session = arrange(module, monkeypatch, seconds=-1)
    round_ref = module.GraphRoundAdmissionRef(row=module.ObservedExecutionRef(
        output_id="original-round", role="research_graph_round:1",
        schema_version="research-graph-round-admission.v2", payload_hash="1" * 64,
        recorded_at=AT,
    ))
    final_ref = module.GraphFinalProjectionRef(row=module.ObservedExecutionRef(
        output_id="original-final", role="research_graph_final",
        schema_version="research-graph-final-projection.v2", payload_hash="2" * 64,
        recorded_at=AT,
    ))
    # The reader's validated result/receipts are deliberately substituted here;
    # these values are neither saved native originals nor database auth proof.
    result_json = {"reader_result": "already-finished-original"}

    class ReaderResult:
        stop_reason = SimpleNamespace(code="sufficient", detail="Original final reader result")

        def model_dump(self, *, mode: str):
            assert mode == "json"
            return dict(result_json)

    final = SimpleNamespace(
        reference=final_ref,
        receipt=SimpleNamespace(
            last_admission_ref=round_ref, decision_snapshot_id="snapshot-final",
        ),
        result=ReaderResult(),
    )
    frozen_round = SimpleNamespace(reference=round_ref, state=base)

    def read_final(_session: Any, **kwargs: Any):
        assert kwargs == {
            "run_id": request.run_id, "reference": final_ref,
            "expected_lease_owner": OWNER, "require_current_permission": True,
        }
        return final

    def read_round(_session: Any, **kwargs: Any):
        assert kwargs == {
            "run_id": request.run_id, "reference": round_ref,
            "expected_lease_owner": OWNER, "require_current_permission": True,
        }
        return frozen_round

    monkeypatch.setattr(module, "read_graph_final_in_session", read_final)
    monkeypatch.setattr(module, "read_graph_round_in_session", read_round)
    state = {
        **base,
        "graph_round_admission_ref": round_ref.model_dump(mode="json"),
        "graph_final_projection_ref": final_ref.model_dump(mode="json"),
        "final_result": result_json,
        "decision_snapshot_id": "snapshot-final",
        "stop_code": "sufficient",
        "stop_detail": "Original final reader result",
    }
    return request, state, run, session


def test_finished_final_after_runtime_deadline_retains_original_read_path(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    assert binding.prepare_graph_state_in_session(session, state, OWNER) == (True, state)


def test_late_final_does_not_bypass_current_recipe(binding: Any, monkeypatch: pytest.MonkeyPatch):
    _, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=False)
    with pytest.raises(ValueError, match="^" + RECIPE_ERROR + "$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


@pytest.mark.parametrize("boundary", ["final", "round"])
def test_late_final_does_not_bypass_original_reader_rejection(
    binding: Any, monkeypatch: pytest.MonkeyPatch, boundary: str,
):
    _, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)

    def invalid_original(_session: Any, **_kwargs: Any):
        raise ValueError("original_" + boundary + "_authentication_failed")

    reader = "read_graph_" + boundary + "_in_session"
    monkeypatch.setattr(binding, reader, invalid_original)
    with pytest.raises(ValueError, match="^original_" + boundary + "_authentication_failed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_late_final_does_not_bypass_cached_projection_equality(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    state["final_result"] = {"reader_result": "modified-checkpoint-result"}
    with pytest.raises(ValueError, match="^research_graph_checkpoint_projection_changed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_late_final_still_requires_original_writer(binding: Any, monkeypatch: pytest.MonkeyPatch):
    _, state, run, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    run.lease_owner = "other-owner"
    with pytest.raises(PermissionError, match="^research_graph_writer_permission_lost$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_late_final_still_requires_original_task(binding: Any, monkeypatch: pytest.MonkeyPatch):
    request, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    state["request"] = request.model_copy(update={"request_id": "changed"}).model_dump(mode="json")
    with pytest.raises(ValueError, match="^research_graph_checkpoint_task_changed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_finished_branch_preserves_recipe_precedence(binding: Any, monkeypatch: pytest.MonkeyPatch):
    request, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=False)
    state["request"] = request.model_copy(update={"request_id": "changed"}).model_dump(mode="json")
    with pytest.raises(ValueError, match="^" + RECIPE_ERROR + "$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_late_final_marker_still_requires_native_reference(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = finished_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    state["graph_final_projection_ref"] = {"kind": "graph_final_projection", "row": {}}
    with pytest.raises(ValidationError):
        binding.prepare_graph_state_in_session(session, state, OWNER)
