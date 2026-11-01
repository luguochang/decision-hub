"""New exact ordering with the frozen v1 behavior as an explicit control."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import pytest

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ResearchInputEvidence,
    ResearchTaskPolicy,
)
from packages.kernel.decision_hub_kernel.application.research_context import (
    build_task_policy,
    select_context,
)
from packages.kernel.decision_hub_kernel.application.research_context_selector_v2 import (
    select_context_v2,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = next(
    case["value"]
    for case in json.loads(
        (ROOT / "tests/fixtures/research_context_selection_v3_cases.json").read_text("utf-8")
    )
    if case["name"] == "v3-live-clock-before-pit"
)
NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _item(
    evidence_id: str, received: datetime, *, excerpt: str = "primary event"
) -> ResearchInputEvidence:
    value = dict(SAMPLE["considered_inputs"][0]["input"])
    value.update(evidence_id=evidence_id, received_at=received, excerpt=excerpt)
    return ResearchInputEvidence.model_validate(value)


class _SelectorOptions(TypedDict):
    requirements: list[EvidenceRequirement]
    policy: ResearchTaskPolicy
    manifest_prefix: str
    revision: int
    created_at: datetime
    eligibility_cutoff_at: datetime


def _options() -> _SelectorOptions:
    requirement = EvidenceRequirement.model_validate(
        SAMPLE["projected_request"]["evidence_requirements"][0]
    )
    policy = build_task_policy(
        research_mode="current_decision",
        requirements=[requirement],
        created_at=NOW,
        max_evidence_rounds=2,
    )
    return {
        "requirements": [requirement],
        "policy": policy,
        "manifest_prefix": "context:test",
        "revision": 1,
        "created_at": NOW,
        "eligibility_cutoff_at": datetime(9999, 12, 31, tzinfo=UTC),
    }


def test_old_float_is_a_versioned_control_and_v2_orders_microseconds() -> None:
    older = datetime(9999, 12, 30, 12, microsecond=1, tzinfo=UTC)
    newer = older + timedelta(microseconds=1)
    items = [_item("z-older", older), _item("a-newer", newer)]
    assert older.timestamp() == newer.timestamp(), "actual v1 float collision must be reached"
    old, manifest = select_context(items, (), **_options())
    current = select_context_v2(items, (), **_options())
    assert [item.evidence_id for item in old] == ["z-older", "a-newer"]
    assert [item.evidence_id for item in current.projected] == ["a-newer", "z-older"]
    assert manifest.rules_ref.endswith(".v1")
    assert current.manifest.rules_ref.endswith(".v2")


def test_real_twelve_item_budget_has_different_thirteenth_exclusion() -> None:
    older = datetime(9999, 12, 30, 12, microsecond=1, tzinfo=UTC)
    items = [_item("z-older", older), _item("a-newer", older + timedelta(microseconds=1))]
    items += [
        _item(f"later-{index}", older + timedelta(hours=1, microseconds=index))
        for index in range(11)
    ]
    old, old_manifest = select_context(items, (), **_options())
    current = select_context_v2(items, (), **_options())
    assert len(old) == len(current.projected) == 12
    assert old_manifest.excluded_inputs[-1].evidence_ref == "a-newer"
    assert current.manifest.excluded_inputs[-1].evidence_ref == "z-older"
    assert len(current.considered) == 13


def test_equal_instants_keep_reverse_id_tie_and_old_excerpt_projection() -> None:
    long_text = "primary event time " * 200
    items = [
        _item("a", NOW, excerpt=long_text),
        _item("z", NOW.astimezone(timezone(timedelta(hours=8))), excerpt=long_text),
    ]
    old, _ = select_context(items, (), **_options())
    current = select_context_v2(items, (), **_options())
    assert [item.evidence_id for item in current.projected] == ["z", "a"]
    assert [item.excerpt for item in old] == [item.excerpt for item in current.projected]


def test_last_duplicate_task_wins_but_considered_keeps_both_originals() -> None:
    items = [_item("same", NOW, excerpt="first"), _item("same", NOW, excerpt="second")]
    current = select_context_v2(items, (), **_options())
    assert len(current.considered) == 2
    assert len(current.winners) == 1
    assert current.projected[0].excerpt == "second"


def test_future_received_is_excluded_without_moving_the_cutoff() -> None:
    options = _options()
    options["eligibility_cutoff_at"] = NOW
    current = select_context_v2(
        [_item("allowed", NOW), _item("future", NOW + timedelta(microseconds=1))], (), **options
    )
    assert current.manifest.selected_inputs == ["allowed"]
    assert current.manifest.excluded_inputs[0].reason == "received_after_cutoff"


def test_bounded_snapshot_rejects_an_infinite_input_iterable() -> None:
    class Infinite:
        def __iter__(self):
            while True:
                yield _item("same", NOW)

    with pytest.raises(ValueError, match="considered_limit"):
        select_context_v2(Infinite(), (), **_options())  # pyright: ignore[reportArgumentType]
