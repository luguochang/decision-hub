"""Pure shapes only: no research, origin, Provider or permission attestation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from packages.contracts_py.decision_hub_contracts import binding_contract_base as shapes
from packages.contracts_py.decision_hub_contracts import context_execution_contract_base as context
from packages.contracts_py.decision_hub_contracts import execution_contract_base as execution
from packages.contracts_py.decision_hub_contracts import models
from packages.contracts_py.decision_hub_contracts.generated.research_execution_binding import (
    ResearchBundleTaskRowIdentity,
)

ROOT = Path(__file__).resolve().parents[2]


def sample(filename: str, definition: str) -> dict[str, Any]:
    cases = json.loads((ROOT / "tests/fixtures" / filename).read_text("utf-8"))
    return next(
        row["value"] for row in cases if row["definition"] == definition and row["expected"]
    )


SPECS = (
    (
        execution,
        ResearchBundleTaskRowIdentity,
        sample("research_execution_contract_cases.json", "research_bundle_task_row_identity"),
    ),
    (
        context,
        models.ResearchContextSelectionRowIdentityV3,
        sample(
            "research_context_execution_cases.json", "research_context_selection_row_identity_v3"
        ),
    ),
)


@pytest.mark.parametrize("base,model,original", SPECS, ids=("execution", "context"))
def test_exact_canonical_shapes_reuse_success_and_keep_fresh_typed_objects(
    base: Any,
    model: type[BaseModel],
    original: dict[str, Any],
) -> None:
    assert base._deny_retrieve is shapes._deny_retrieve  # pyright: ignore[reportPrivateUsage]
    with shapes.shape_validation_scope() as stats:
        first = model.model_validate(deepcopy(original))
        before = stats.hit_count
        second = model.model_validate(deepcopy(original))
        assert first == second and first is not second
        assert stats.hit_count > before and stats.miss_count > 0


@pytest.mark.parametrize("base,model,original", SPECS, ids=("execution", "context"))
def test_current_transitive_schema_mutation_rejects_prior_shape_success(
    base: Any,
    model: type[BaseModel],
    original: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = base._validator(model)
    with shapes.shape_validation_scope() as stats:
        model.model_validate(deepcopy(original))
        definition = validator._resolver.lookup(validator.schema["$ref"]).contents
        monkeypatch.setitem(definition["properties"]["recorded_at"], "type", "integer")
        before = stats.hit_count
        with pytest.raises(ValidationError):
            model.model_validate(deepcopy(original))
        assert stats.hit_count == before


@pytest.mark.parametrize("base,model,original", SPECS, ids=("execution", "context"))
def test_unknown_formatter_bypasses_success_and_executes_actual_rejection(
    base: Any,
    model: type[BaseModel],
    original: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = base._validator(model)
    assert validator.format_checker is not None
    with shapes.shape_validation_scope() as stats:
        model.model_validate(deepcopy(original))
        before = stats.bypass_count
        calls: list[object] = []

        def unknown(value: object) -> bool:
            calls.append(value)
            return False

        monkeypatch.setitem(validator.format_checker.checkers, "date-time", (unknown, ()))
        with pytest.raises(ValidationError):
            model.model_validate(deepcopy(original))
        assert calls and stats.bypass_count > before


@pytest.mark.parametrize("boundary", ("time", "size"))
def test_context_link_semantic_time_and_size_guards_run_after_shape_hits(
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = sample(
        "research_context_execution_cases.json",
        "research_context_selection_execution_link_v2",
    )
    model = models.ResearchContextSelectionExecutionLinkV2
    with shapes.shape_validation_scope() as stats:
        model.model_validate(deepcopy(original))
        model.model_validate(deepcopy(original))
        before = stats.hit_count
        if boundary == "time":

            def reject_time(_value: object) -> list[str]:
                return ["controlled_actual_time_guard"]

            monkeypatch.setattr(
                context,
                "context_selection_time_issue_codes",
                reject_time,
            )
        else:

            def reject_size(_value: Any, *, limit: int) -> None:
                assert limit == 16_384
                raise ValueError("controlled_actual_size_guard")

            monkeypatch.setattr(context, "canonical_payload_size", reject_size)
        with pytest.raises(ValidationError, match=f"controlled_actual_{boundary}_guard"):
            model.model_validate(deepcopy(original))
        assert stats.hit_count > before
