"""Purpose-bound references to authenticated proposal content, never market evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, cast

from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
    DecisionModelAssumptionV1,
)


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _reference(body: dict[str, Any]) -> dict[str, str]:
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    return dict(ref="decision_model_assumption_" + digest, hash=digest)


def proposal_assessment_refs(
    synthesis: dict[str, Any], assumptions: Sequence[dict[str, Any]]
) -> dict[str, set[str]]:
    """Caller already authenticated these exact same-prepare bodies and source usage."""
    if not assumptions:
        return {}
    primary = (
        synthesis["primary_v2"]
        if synthesis["schema_version"] == "research-decision-synthesis.v3"
        else synthesis
    )
    bodies = [
        DecisionModelAssumptionV1.model_validate(row).model_dump(mode="json") for row in assumptions
    ]
    by_ref = {canonical(_reference(body)): body for body in bodies}
    result: dict[str, set[str]] = {
        "probability_completeness": set(),
        "cost_protection_and_conditions": set(),
    }

    def include(check: str, reference: dict[str, Any] | None, role: str) -> None:
        if reference is not None and by_ref.get(canonical(reference), {}).get("role") == role:
            result[check].add(canonical(reference))

    include(
        "probability_completeness",
        primary["market_distribution"]["direction_distribution"]["source_ref"],
        "probability_source",
    )
    for candidate in primary["candidate_set"]["candidates"]:
        include("cost_protection_and_conditions", candidate["exit_ref"], "exit_policy")
        for reference in candidate["condition_refs"]:
            include("cost_protection_and_conditions", reference, "entry_condition")
        for reference in candidate["basis_refs"]:
            include("cost_protection_and_conditions", reference, "basis")
    return result


def model_assumptions_from_review_prompt(prompt: str) -> list[dict[str, Any]]:
    """Extract only after native replay equated this exact block to frozen prepare."""
    marker = "BEGIN_DECLARED_MODEL_ASSUMPTIONS_JSON"
    lines = prompt.split("\n")
    if marker not in lines:
        return []
    if lines.count(marker) != 1:
        raise ValueError("perpetual_assessment_assumptions_ambiguous")
    index = lines.index(marker)
    if index + 3 >= len(lines) or lines[index + 2] != "END_DECLARED_MODEL_ASSUMPTIONS_JSON":
        raise ValueError("perpetual_assessment_assumptions_missing")
    raw = json.loads(lines[index + 1])
    if not isinstance(raw, list) or len(cast(list[Any], raw)) > 128:
        raise ValueError("perpetual_assessment_assumptions_invalid")
    parsed = [
        DecisionModelAssumptionV1.model_validate(row).model_dump(mode="json")
        for row in cast(list[Any], raw)
    ]
    digest = hashlib.sha256(canonical(parsed).encode()).hexdigest()
    if (
        canonical(parsed) != lines[index + 1]
        or lines[index + 3] != "MODEL_ASSUMPTIONS_SHA256=" + digest
    ):
        raise ValueError("perpetual_assessment_assumptions_changed")
    return parsed
