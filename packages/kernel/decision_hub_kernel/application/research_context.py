from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from packages.contracts_py.decision_hub_contracts import (
    ContextExclusion,
    EvidenceCandidate,
    EvidenceRequirement,
    ResearchContextManifest,
    ResearchInputEvidence,
    ResearchTaskPolicy,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]{2,}|[\u4e00-\u9fff]{2,}")


CONTEXT_POLICY_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def build_task_policy(
    *,
    research_mode: str,
    requirements: Sequence[EvidenceRequirement],
    created_at: datetime,
    max_evidence_rounds: int,
    research_family: str = "crypto_macro",
    context_inputs: Sequence[ResearchInputEvidence] = (),
) -> ResearchTaskPolicy:
    """Create the small, versioned policy used by one research family.

    The policy is deliberately data, rather than a prompt convention. A change
    to selection limits therefore changes the hash stored with the Run and is
    visible in replay comparisons.
    """

    mode = (
        research_mode
        if research_mode in {"historical_review", "current_decision"}
        else "current_decision"
    )
    requirement_ids = sorted(item.requirement_id for item in requirements)
    review_refs = [
        item.evidence_id for item in context_inputs if item.source_id.startswith("action-review:")
    ]
    if len(review_refs) != len(set(review_refs)) or len(review_refs) > 38:
        raise ValueError("action_review_context_item_budget_exceeded")
    cpi_refs = [
        item.evidence_id for item in context_inputs
        if item.source_id.startswith("cpi-pre-event-context:")
    ]
    if cpi_refs and (
        len(cpi_refs) != 3
        or len(set(cpi_refs)) != 3
        or {ref.rsplit(":", 1)[-1] for ref in cpi_refs} != {"schedule", "nowcast", "prior"}
        or len({ref.rsplit(":", 1)[0] for ref in cpi_refs}) != 1
    ):
        raise ValueError("cpi_context_policy_parts_invalid")
    # CPI context receives its own allowance instead of silently consuming the
    # ordinary twelve slots. Preserve the current protocol ceiling of fifty;
    # at 36-38 review parts this leaves 11-9 ordinary slots, not twelve.
    policy_version = (
        "action-review-cpi-context.v1" if review_refs and cpi_refs
        else "cpi-context.v1" if cpi_refs
        else "action-review.v2" if review_refs else "v1"
    )
    scenario_refs = [item.evidence_id for item in context_inputs if item.source_id.startswith(("event-scenario-context:", "event-owned-scenario-context:"))]
    if len(scenario_refs) > 1:
        raise ValueError("event_scenario_context_policy_multiple")
    if scenario_refs:
        policy_version += ".event-scenario.v1"
    opportunity_refs = [item.evidence_id for item in context_inputs if item.source_id.startswith("event-opportunity-context:")]
    if len(opportunity_refs) > 1:
        raise ValueError("event_opportunity_context_policy_multiple")
    if opportunity_refs:
        policy_version += ".event-opportunity.v1"
    max_context_items = min(50, 12 + len(review_refs) + len(cpi_refs) + len(scenario_refs) + len(opportunity_refs))
    semantic = {
        "policy_id": f"{research_family}.research",
        "policy_version": policy_version,
        "research_mode": mode,
        "requirement_ids": requirement_ids,
        "max_parallel_tasks": 2,
        "max_context_items": max_context_items,
        "max_excerpt_chars": 4000,
        "max_evidence_rounds": max(1, min(10, max_evidence_rounds)),
    }
    content_hash = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return ResearchTaskPolicy(
        policy_id=f"{research_family}.research",
        policy_version=policy_version,
        research_mode=cast(Literal["historical_review", "current_decision"], mode),
        requirement_ids=requirement_ids,
        max_parallel_tasks=2,
        max_context_items=max_context_items,
        max_excerpt_chars=4000,
        max_evidence_rounds=max(1, min(10, max_evidence_rounds)),
        content_hash=content_hash,
        created_at=created_at.astimezone(UTC),
    )


def select_context(
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
    eligibility_cutoff_at: datetime | None = None,
) -> tuple[list[ResearchInputEvidence], ResearchContextManifest]:
    """Select context by deterministic relevance, authority and recency.

    The source body hash stays untouched. Only the prompt projection is
    shortened, and the manifest records every item that was excluded.
    """

    review_refs = tuple(
        item.evidence_id for item in inputs if item.source_id.startswith("action-review:")
    )
    if review_refs:
        from .research_context_selector_v3 import select_context_v3

        computed = select_context_v3(
            inputs,
            candidates,
            requirements=requirements,
            policy=policy,
            manifest_prefix=manifest_prefix,
            revision=revision,
            created_at=created_at,
            allowed_capabilities=allowed_capabilities,
            prior_exclusions=prior_exclusions,
            eligibility_cutoff_at=eligibility_cutoff_at or created_at,
            protected_refs=review_refs,
        )
        return list(computed.projected), computed.manifest
    by_id: dict[str, ResearchInputEvidence] = {item.evidence_id: item for item in inputs}
    for candidate in candidates:
        if candidate.evidence_id not in by_id:
            by_id[candidate.evidence_id] = ResearchInputEvidence(
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
    if not by_id:
        raise ValueError("research_context_empty")

    cutoff = (
        research_context_cutoff(eligibility_cutoff_at)
        if eligibility_cutoff_at is not None
        else None
    )
    temporal_excluded = [
        ContextExclusion(evidence_ref=item.evidence_id, reason="received_after_cutoff")
        for item in by_id.values()
        if cutoff is not None and research_context_cutoff(item.received_at) > cutoff
    ]
    temporal_ids = {item.evidence_ref for item in temporal_excluded}
    eligible = [item for item in by_id.values() if item.evidence_id not in temporal_ids]
    if not eligible:
        raise ValueError("research_context_empty")

    terms = _terms(requirements)
    initial_ids = {item.evidence_id for item in inputs}
    ranked = sorted(
        eligible,
        key=lambda item: (
            1 if item.evidence_id in initial_ids else 0,
            _relevance(item.excerpt, terms),
            _authority(item.authority),
            item.received_at.astimezone(UTC).timestamp(),
            item.evidence_id,
        ),
        reverse=True,
    )
    selected = ranked[: policy.max_context_items]
    selected_ids = [item.evidence_id for item in selected]
    excluded = temporal_excluded + [
        ContextExclusion(evidence_ref=item.evidence_id, reason="context_budget")
        for item in ranked[policy.max_context_items :]
    ]
    # A round may start from an already bounded request. Keep its upstream
    # exclusions visible unless that material is actually selected this time.
    exclusions_by_id = {
        item.evidence_ref: item
        for item in (*prior_exclusions, *excluded)
        if item.evidence_ref not in selected_ids
    }
    excluded = list(exclusions_by_id.values())
    projected = [
        item.model_copy(
            update={"excerpt": project_excerpt(item.excerpt, terms, policy.max_excerpt_chars)}
        )
        for item in selected
    ]
    effective_ids = [item.evidence_id for item in projected]
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
        ).encode("utf-8")
    ).hexdigest()
    manifest = ResearchContextManifest(
        manifest_id=f"{manifest_prefix}:r{revision}:{fingerprint[:16]}",
        policy_ref=f"{policy.policy_id}.{policy.policy_version}:{policy.content_hash[:16]}",
        revision=revision,
        selected_inputs=selected_ids,
        effective_inputs=effective_ids,
        excluded_inputs=excluded,
        rules_ref="research-context.relevance-authority-recency.v1",
        tool_versions={
            capability: "gateway-contract.v1" for capability in sorted(set(allowed_capabilities))
        },
        data_versions={
            f"{policy.policy_id.split('.')[0]}.pack": "v1",
            "evidence.schema": "agentic_research.v1",
        },
        input_fingerprint=fingerprint,
        created_at=created_at.astimezone(UTC),
    )
    return projected, manifest


def research_context_cutoff(value: datetime) -> datetime:
    """Require an explicit timezone for an input admission boundary."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research_context_cutoff_timezone_required")
    return value.astimezone(UTC)


def project_excerpt(text: str, terms: Sequence[str], limit: int = 4000) -> str:
    """Keep a head/tail plus relevant windows instead of always taking a prefix."""

    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    if limit < 1000:
        limit = 1000
    windows: list[str] = []
    half = max(180, min(700, limit // 6))
    for term in terms:
        index = normalized.lower().find(term.lower())
        if index >= 0:
            start = max(0, index - half)
            end = min(len(normalized), index + len(term) + half)
            windows.append(normalized[start:end])
        if len(windows) >= 4:
            break
    head = normalized[: min(700, limit // 5)]
    tail = normalized[-min(700, limit // 5) :]
    chunks: list[str] = [head, *windows, tail]
    result = "\n…\n".join(dict.fromkeys(chunk for chunk in chunks if chunk))
    if len(result) < limit:
        # Repeated source text can collapse the head/window/tail sections to a
        # short string. Fill the remaining bounded budget from the middle so
        # the model receives a stable-size projection without exposing the
        # complete archived body.
        remaining = limit - len(result) - 1
        if remaining > 0:
            middle_start = max(0, (len(normalized) - remaining) // 2)
            result = f"{result}\n…\n{normalized[middle_start : middle_start + remaining]}"
    if len(result) <= limit:
        return result
    # Preserve the first and last sections while trimming the middle.
    tail_budget = min(700, limit // 5)
    trimmed = result[: limit - tail_budget - 1].rstrip() + "…" + result[-tail_budget:].lstrip()
    if len(trimmed) < limit:
        trimmed += normalized[: limit - len(trimmed)]
    return trimmed[:limit]


def _terms(requirements: Sequence[EvidenceRequirement]) -> tuple[str, ...]:
    values: list[str] = []
    for requirement in requirements:
        values.extend(_TOKEN_RE.findall(requirement.description.lower()))
    return tuple(dict.fromkeys(term for term in values if len(term) >= 2))


def _relevance(text: str, terms: Sequence[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def _authority(value: str) -> int:
    return {
        "official": 6,
        "exchange": 5,
        "audited_aggregator": 4,
        "verified_web": 3,
        "search_derived": 2,
        "unverified": 1,
    }.get(value, 0)
