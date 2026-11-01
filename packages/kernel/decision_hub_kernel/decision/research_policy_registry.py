"""Closed registry; probability execution requires independent producer and math originals."""

CHECKER_REGISTRY_VERSION = "research-policy-checker-registry.v1"
CITATION_EXECUTOR_VERSION = "research-policy-probability-executor.v6"
RULE_VERSION = "1.0.0"
GATE_RULE_IDS = frozenset(
    {
        "pit",
        "frozen-hard-coverage",
        "citation-membership",
        "citation-authority",
        "citation-precision-review",
        "gap-to-tool-continuation",
        "bounded-stop-consistency",
    }
)
RUBRIC_RULE_IDS = frozenset(
    {
        "causal-chain-structure",
        "causal-quality-review",
        "counter-thesis-quality-review",
        "horizon-contract",
        "probability-provenance",
        "kernel-cap",
        "execution-permission",
    }
)
REGISTERED_RULE_IDS = tuple(sorted(GATE_RULE_IDS | RUBRIC_RULE_IDS))
NONBLOCKING_RULE_IDS = frozenset(
    {
        "causal-chain-structure",
        "causal-quality-review",
        "counter-thesis-quality-review",
    }
)
SCOPE_KINDS = frozenset({"event", "current_snapshot", "historical_review"})


def blocking_checks_passed(checks) -> bool:
    """Derive completeness; this is not receipt authentication or permission.

    Caller must independently authenticate/execute the original compiled rules.
    No incomplete rule set, unknown applicability or failed blocking result can
    be promoted by an aggregate boolean supplied with a receipt.
    """
    try:
        rows = [check if isinstance(check, dict) else check.model_dump(mode="json") for check in checks]
        ids = [row["rule_id"] for row in rows]
        if len(ids) != len(REGISTERED_RULE_IDS) or len(set(ids)) != len(ids) or set(ids) != set(REGISTERED_RULE_IDS):
            return False
        blocking = [row for row in rows if row["blocking_for"] and row["applicability"] != "not_applicable"]
        return bool(blocking) and all(row["applicability"] == "applicable" and row["status"] == "pass" for row in blocking)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
