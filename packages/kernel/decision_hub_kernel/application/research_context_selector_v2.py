"""Exact microsecond recency; source authentication belongs to the producer.

The frozen v1 relevance/authority/excerpt rules are reused. Their float recency
key is replaced only in this explicitly versioned selector.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice

from packages.contracts_py.decision_hub_contracts import (
    ContextExclusion,
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchContextManifest,
    ResearchInputEvidence,
    ResearchTaskPolicy,
)
from packages.contracts_py.decision_hub_contracts.context_selection_semantics import (
    MAX_CONSIDERED_INPUTS,
    parse_rfc3339_microseconds,
)
from packages.kernel.decision_hub_kernel.application.research_context import (
    _authority,  # pyright: ignore[reportPrivateUsage]
    _relevance,  # pyright: ignore[reportPrivateUsage]
    _terms,  # pyright: ignore[reportPrivateUsage]
    project_excerpt,
)

RANKING_RULES_REF = "research-context.relevance-authority-recency.v2"


@dataclass(frozen=True)
class ContextSelectionComputation:
    considered: tuple[ResearchInputEvidence, ...]
    task_count: int
    winners: tuple[ResearchInputEvidence, ...]
    eligible: tuple[ResearchInputEvidence, ...]
    ranked: tuple[ResearchInputEvidence, ...]
    projected: tuple[ResearchInputEvidence, ...]
    manifest: ResearchContextManifest


def _bounded(values: Iterable[ResearchInputEvidence]) -> tuple[ResearchInputEvidence, ...]:
    snapshot = tuple(islice(iter(values), MAX_CONSIDERED_INPUTS + 1))
    if len(snapshot) > MAX_CONSIDERED_INPUTS:
        raise ValueError("context_selection_considered_limit")
    # Detach mutable caller DTOs before any sorting/projection operation.
    return tuple(
        ResearchInputEvidence.model_validate_json(item.model_dump_json()) for item in snapshot
    )


def input_from_candidate(candidate: EvidenceCandidate) -> ResearchInputEvidence:
    return ResearchInputEvidence(
        evidence_id=candidate.evidence_id,
        kind=candidate.kind,
        authority=candidate.authority,
        source_id=candidate.source_id,
        source_url=candidate.source_url,
        published_at=candidate.published_at,
        observed_at=candidate.observed_at,
        received_at=candidate.received_at,
        content_hash=candidate.content_hash,
        excerpt=candidate.excerpt,
    )


def select_context_v2(
    inputs: Sequence[ResearchInputEvidence],
    candidates: Iterable[EvidenceCandidate],
    *,
    requirements: Sequence[EvidenceRequirement],
    policy: ResearchTaskPolicy,
    manifest_prefix: str,
    revision: int,
    created_at: datetime,
    allowed_capabilities: Sequence[str] = (),
    prior_exclusions: Sequence[ContextExclusion] = (),
    eligibility_cutoff_at: datetime,
) -> ContextSelectionComputation:
    task_inputs = _bounded(inputs)
    acquired = _bounded(input_from_candidate(candidate) for candidate in candidates)
    considered = task_inputs + acquired
    if len(considered) > MAX_CONSIDERED_INPUTS:
        raise ValueError("context_selection_considered_limit")
    policy = ResearchTaskPolicy.model_validate_json(policy.model_dump_json())
    requirements = tuple(
        EvidenceRequirement.model_validate_json(item.model_dump_json()) for item in requirements
    )
    prior = tuple(
        ContextExclusion.model_validate_json(item.model_dump_json()) for item in prior_exclusions
    )
    capabilities = tuple(allowed_capabilities)
    cutoff_us = parse_rfc3339_microseconds(eligibility_cutoff_at.isoformat())
    parse_rfc3339_microseconds(created_at.isoformat())
    by_id = {item.evidence_id: item for item in task_inputs}
    for item in acquired:
        if item.evidence_id not in by_id:
            by_id[item.evidence_id] = item
    if not by_id:
        raise ValueError("research_context_empty")
    winners = tuple(by_id.values())
    received = {
        item.evidence_id: parse_rfc3339_microseconds(item.received_at.isoformat())
        for item in winners
    }
    temporal = [
        ContextExclusion(evidence_ref=item.evidence_id, reason="received_after_cutoff")
        for item in winners
        if received[item.evidence_id] > cutoff_us
    ]
    temporal_ids = {item.evidence_ref for item in temporal}
    eligible = tuple(item for item in winners if item.evidence_id not in temporal_ids)
    if not eligible:
        raise ValueError("research_context_empty")
    terms = _terms(requirements)
    task_ids = {item.evidence_id for item in task_inputs}
    ranked = tuple(
        sorted(
            eligible,
            key=lambda item: (
                1 if item.evidence_id in task_ids else 0,
                _relevance(item.excerpt, terms),
                _authority(item.authority),
                received[item.evidence_id],
                item.evidence_id,
            ),
            reverse=True,
        )
    )
    selected = ranked[: policy.max_context_items]
    selected_ids = [item.evidence_id for item in selected]
    exclusions = temporal + [
        ContextExclusion(evidence_ref=item.evidence_id, reason="context_budget")
        for item in ranked[policy.max_context_items :]
    ]
    exclusions_by_id = {
        item.evidence_ref: item
        for item in (*prior, *exclusions)
        if item.evidence_ref not in selected_ids
    }
    projected = tuple(
        item.model_copy(
            update={"excerpt": project_excerpt(item.excerpt, terms, policy.max_excerpt_chars)}
        )
        for item in selected
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                {
                    "evidence_id": item.evidence_id,
                    "content_hash": item.content_hash,
                    "excerpt_hash": hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest(),
                }
                for item in projected
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    manifest = ResearchContextManifest(
        manifest_id=f"{manifest_prefix}:r{revision}:{fingerprint[:16]}",
        policy_ref=f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}",
        revision=revision,
        selected_inputs=selected_ids,
        effective_inputs=[item.evidence_id for item in projected],
        excluded_inputs=list(exclusions_by_id.values()),
        rules_ref=RANKING_RULES_REF,
        tool_versions={
            capability: "gateway-contract.v1" for capability in sorted(set(capabilities))
        },
        data_versions={
            f"{policy.policy_id.split('.')[0]}.pack": "v1",
            "evidence.schema": "agentic_research.v1",
        },
        input_fingerprint=fingerprint,
        created_at=created_at.astimezone(UTC),
    )
    return ContextSelectionComputation(
        considered, len(task_inputs), winners, eligible, ranked, projected, manifest
    )
