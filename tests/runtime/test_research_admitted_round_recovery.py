"""An admitted model round may need only deterministic final persistence, not more RPC."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from test_research_resume_deadline import AT, OWNER, RECIPE_ERROR, arrange, recipe_seam

pytest_plugins = ["test_research_resume_deadline"]


def admitted_case(module: Any, monkeypatch: pytest.MonkeyPatch):
    request, base, run, session = arrange(module, monkeypatch, seconds=-1)
    reference = module.GraphRoundAdmissionRef(row=module.ObservedExecutionRef(
        output_id="admitted-round", role="research_graph_round:1",
        schema_version="research-graph-round-admission.v2", payload_hash="3" * 64,
        recorded_at=AT,
    ))
    frozen = SimpleNamespace(
        reference=reference, state=base, receipt=SimpleNamespace(logical_round=1),
    )

    def read_round(_session: Any, **kwargs: Any):
        assert kwargs == {
            "run_id": request.run_id, "reference": reference,
            "expected_lease_owner": OWNER, "require_current_permission": True,
        }
        return frozen

    monkeypatch.setattr(module, "read_graph_round_in_session", read_round)
    state = {**base, "graph_round_admission_ref": reference.model_dump(mode="json")}
    return request, state, run, session


def test_admitted_round_retains_late_finalization_path(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = admitted_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    assert binding.prepare_graph_state_in_session(session, state, OWNER) == (True, state)


def test_admitted_round_still_requires_current_recipe(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = admitted_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=False)
    with pytest.raises(ValueError, match="^" + RECIPE_ERROR + "$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_admitted_marker_still_requires_native_ref(binding: Any, monkeypatch: pytest.MonkeyPatch):
    _, state, _, session = admitted_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    state["graph_round_admission_ref"] = {"kind": "graph_round_admission", "row": {}}
    with pytest.raises(ValidationError):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_admitted_round_still_propagates_original_reader_rejection(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = admitted_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)

    def invalid_original(_session: Any, **_kwargs: Any):
        raise ValueError("original_round_authentication_failed")

    monkeypatch.setattr(binding, "read_graph_round_in_session", invalid_original)
    with pytest.raises(ValueError, match="^original_round_authentication_failed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)


def test_admitted_round_still_requires_cached_state_equality(
    binding: Any, monkeypatch: pytest.MonkeyPatch,
):
    _, state, _, session = admitted_case(binding, monkeypatch)
    recipe_seam(binding, monkeypatch, available=True)
    state["loop_summary"] = "modified checkpoint cache"
    with pytest.raises(ValueError, match="^research_graph_checkpoint_projection_changed$"):
        binding.prepare_graph_state_in_session(session, state, OWNER)
