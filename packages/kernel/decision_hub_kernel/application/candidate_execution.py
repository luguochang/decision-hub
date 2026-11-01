"""Closed execution rules; descriptive proposal text never becomes executable policy."""

import hashlib
import json
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import CandidateProposal

RULES = {
    "cite_frozen_evidence": (
        "结论引用本次冻结证据",
        "Cite only supplied frozen evidence for each directional conclusion.",
    ),
    "separate_missing_evidence": (
        "明确区分缺失资料与反向证据",
        "Distinguish missing evidence from contradictory evidence; absence is not confirmation.",
    ),
    "examine_counter_evidence": (
        "结论前检查反向证据",
        "Examine supplied counter-evidence before choosing a direction and explain limitations.",
    ),
    "abstain_on_conflicting_evidence": (
        "证据冲突未解决时保持观望",
        "When supplied directional evidence conflicts and cannot be resolved, choose neutral.",
    ),
}


def execution_error(proposal: CandidateProposal) -> str | None:
    if proposal.execution_scope != "directional_synthesis":
        return "candidate_execution_scope_mismatch"
    if proposal.candidate_type not in {"strategy", "profile", "doctrine"}:
        return "candidate_operational_change_unsupported"
    if not proposal.execution_rules:
        return "candidate_execution_rules_missing"
    if (len(set(proposal.execution_rules)) != len(proposal.execution_rules)
            or any(rule not in RULES for rule in proposal.execution_rules)):
        return "candidate_execution_rules_invalid"
    return None


def require_executable(proposal: CandidateProposal) -> None:
    if error := execution_error(proposal):
        raise ValueError(error)


def checked_proposal(candidate) -> CandidateProposal:
    if not candidate.content_ref:
        raise ValueError("candidate_artifact_not_found")
    path = Path(candidate.content_ref)
    if path.name != f"{candidate.candidate_id}.json" or not path.is_file():
        raise ValueError("candidate_artifact_ref_invalid")
    if path.stat().st_size > 256_000:
        raise ValueError("candidate_artifact_invalid")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != candidate.content_hash:
        raise ValueError("candidate_artifact_hash_mismatch")
    try:
        artifact = json.loads(body)
        proposal = CandidateProposal.model_validate(artifact["proposal"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("candidate_artifact_invalid") from exc
    if (artifact.get("schema_version") != "candidate-artifact.v1"
            or proposal.version != candidate.version
            or proposal.candidate_type != candidate.candidate_type
            or proposal.execution_scope != candidate.execution_scope):
        raise ValueError("candidate_artifact_identity_mismatch")
    return proposal


def candidate_execution_error(candidate, domain: str | None = None) -> str | None:
    if candidate is None:
        return "candidate_not_found"
    # A release baseline is executable by its original adapter, not this wrapper.
    if candidate.source == "release" and not candidate.content_ref and domain:
        body = json.dumps({"schema_version": "release-baseline.v1",
                           "domain_pack_ref": domain, "version": candidate.version},
                          sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(body.encode()).hexdigest() == candidate.content_hash:
            return None
    try:
        return execution_error(checked_proposal(candidate))
    except (ValueError, OSError) as exc:
        return str(exc) if isinstance(exc, ValueError) else "candidate_artifact_unreadable"
