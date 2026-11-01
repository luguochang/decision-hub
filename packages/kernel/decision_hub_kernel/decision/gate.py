from __future__ import annotations

import hashlib
from dataclasses import dataclass

from packages.contracts_py.decision_hub_contracts.models import GateStatus


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    decisions: list[dict[str, str]]


def _hash(value: object) -> str:
    return hashlib.sha256(repr(value).encode()).hexdigest()


def evaluate_gate(
    candidate: dict[str, object],
    *,
    directional_value_validated: bool = False,
) -> GateResult:
    decisions: list[dict[str, str]] = []
    facts = candidate.get("facts", [])
    citations = candidate.get("citations", [])
    counter = candidate.get("counter_thesis", "")
    direction = candidate.get("direction", "no_trade")
    # A completed research session may intentionally conclude no_trade.  That
    # is a publishable research report, while the action summary still keeps
    # trade_authorized=false.  Plain candidates without this attestation must
    # remain research_only so a missing/unsafe direction cannot be promoted.
    research_complete = bool(candidate.get("research_complete", False))
    probability = float(str(candidate.get("probability", 0.5)))
    directional = direction in {"long", "short"}
    checks = [
        ("facts_present", bool(facts), "facts_missing"),
        ("citations_present", bool(citations), "citations_missing"),
        ("counter_thesis_present", bool(counter), "counter_thesis_missing"),
        (
            "manual_action_fields",
            bool(candidate.get("trigger")) and bool(candidate.get("invalidation")),
            "action_fields_missing",
        ),
        ("probability_cap", probability <= 0.65, "uncalibrated_probability_capped"),
        (
            "directional_value",
            not directional or directional_value_validated,
            "directional_value_unvalidated",
        ),
    ]
    for rule_id, passed, reason in checks:
        decisions.append(
            {
                "rule_id": rule_id,
                "status": "pass" if passed else "fail",
                "reason_code": "ok" if passed else reason,
                "input_hash": _hash((rule_id, candidate.get(rule_id), facts, citations)),
            }
        )
    failed = [item for item in decisions if item["status"] == "fail"]
    if not facts or not citations:
        status = GateStatus.reject
    elif failed or (direction == "no_trade" and not research_complete):
        status = GateStatus.research_only
    else:
        status = GateStatus.publish
    return GateResult(status=status, decisions=decisions)
