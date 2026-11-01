"""Time eligibility precedes context budget; original inputs remain immutable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.contracts_py.decision_hub_contracts import ContextExclusion, EvidenceCandidate
from packages.kernel.decision_hub_kernel.application.research_context import (
    build_task_policy,
    select_context,
)
from tests.research.test_context_and_review import (
    _input,  # pyright: ignore[reportPrivateUsage]
    _requirement,  # pyright: ignore[reportPrivateUsage]
)

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


@pytest.mark.parametrize("offset", [0, 8])
def test_boundary_time_is_eligible_in_equivalent_aware_timezone(offset: int) -> None:
    item = _input("equal-boundary", "Known original source")
    original = item.model_dump_json()
    cutoff = NOW.astimezone(timezone(timedelta(hours=offset)))
    selected, manifest = select_context(
        [item], [], requirements=[_requirement()], policy=_policy(),
        manifest_prefix="controlled", revision=1, created_at=NOW,
        eligibility_cutoff_at=cutoff,
    )
    assert [value.evidence_id for value in selected] == [item.evidence_id]
    assert manifest.excluded_inputs == []
    assert item.model_dump_json() == original


def test_inputs_and_candidates_are_time_filtered_before_ranking_and_budget() -> None:
    initial = _input("trigger", "Known trigger")
    late = initial.model_copy(
        update={"evidence_id": "late-input", "received_at": NOW + timedelta(seconds=1)}
    )
    candidate = EvidenceCandidate.model_validate(
        late.model_dump(mode="json")
        | {
            "evidence_id": "late-candidate", "requirement_id": "policy_delta",
            "structured_payload_ref": None, "tool_call_id": None,
            "research_session_id": "controlled", "round": 1, "quality": "accepted",
            "freshness_status": "fresh", "conflict_group": None,
        }
    )
    legal = initial.model_copy(update={"evidence_id": "legal-budget", "published_at": None})
    selected, manifest = select_context(
        [initial, legal, late], [candidate], requirements=[_requirement()],
        policy=_policy().model_copy(update={"max_context_items": 1}),
        manifest_prefix="controlled", revision=1, created_at=NOW,
        eligibility_cutoff_at=NOW,
        prior_exclusions=[ContextExclusion(evidence_ref="parent-private", reason="not_authorized")],
    )
    reasons = {item.evidence_ref: item.reason for item in manifest.excluded_inputs}
    assert reasons["late-input"] == reasons["late-candidate"] == "received_after_cutoff"
    assert reasons["parent-private"] == "not_authorized"
    assert len([reason for reason in reasons.values() if reason == "context_budget"]) == 1
    assert len(selected) == 1
    assert manifest.selected_inputs == manifest.effective_inputs
    assert not {"late-input", "late-candidate"} & set(manifest.selected_inputs)
    assert legal.published_at is None


def test_unknown_publication_stays_null_and_prior_time_exclusion_can_expire() -> None:
    item = _input("known-received-unknown-published", "Original source").model_copy(
        update={"published_at": None}
    )
    selected, manifest = select_context(
        [item], [], requirements=[_requirement()], policy=_policy(),
        manifest_prefix="controlled", revision=2, created_at=NOW,
        eligibility_cutoff_at=NOW,
        prior_exclusions=[
            ContextExclusion(evidence_ref=item.evidence_id, reason="received_after_cutoff")
        ],
    )
    assert selected[0].published_at is None
    assert manifest.excluded_inputs == []


def test_naive_cutoff_is_rejected_without_using_local_timezone() -> None:
    with pytest.raises(ValueError, match="research_context_cutoff_timezone_required"):
        select_context(
            [_input("trigger", "Original source")], [], requirements=[_requirement()],
            policy=_policy(), manifest_prefix="controlled", revision=1, created_at=NOW,
            eligibility_cutoff_at=NOW.replace(tzinfo=None),
        )


def test_all_temporally_excluded_inputs_do_not_gain_fallback_admission() -> None:
    late = _input("late-only", "Original source").model_copy(
        update={"received_at": NOW + timedelta(seconds=1)}
    )
    with pytest.raises(ValueError, match="research_context_empty"):
        select_context(
            [late], [], requirements=[_requirement()], policy=_policy(),
            manifest_prefix="controlled", revision=1, created_at=NOW, eligibility_cutoff_at=NOW,
        )


def _policy():
    return build_task_policy(
        research_mode="current_decision", requirements=[_requirement()],
        created_at=NOW, max_evidence_rounds=3,
    )
