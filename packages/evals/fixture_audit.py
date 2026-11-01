"""Read-only, conservative duplicate checks for configured evolution samples."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tools.replay.run_fixture import ReplayFixture

FixtureStage = Literal["replay", "holdout", "shadow"]


class FixtureSplitAudit(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)
    cross_split_groups: int = 0
    within_split_groups: int = 0
    issues: list[str] = Field(default_factory=list)


def default_evolution_fixtures() -> dict[FixtureStage, tuple[Path, ...]]:
    root = Path(__file__).resolve().parents[2] / "fixtures" / "evolution"
    stages: tuple[FixtureStage, ...] = ("replay", "holdout", "shadow")
    return {
        stage: (root / stage / "powell-higher-for-longer.json",)
        for stage in stages
    }


def sample_content_key(fixture: ReplayFixture) -> str:
    # Renaming the file, split, source or fixture ID does not create a new sample.
    # This detects duplicate text, not semantic independence of different wording.
    text = " ".join(unicodedata.normalize("NFKC", fixture.observation.text).casefold().split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def audit_fixture_splits[FixtureKey: str](
    fixtures: Mapping[FixtureKey, Sequence[Path]],
) -> FixtureSplitAudit:
    audit = FixtureSplitAudit()
    groups: dict[str, list[str]] = defaultdict(list)
    if set(fixtures) != {"replay", "holdout", "shadow"}:
        audit.issues.append("evaluation_stage_coverage_missing")
    for stage, paths in fixtures.items():
        audit.counts[stage] = len(paths)
        if not paths:
            audit.issues.append("evaluation_fixture_required")
        for path in paths:
            try:
                fixture = ReplayFixture.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                audit.issues.append("evaluation_fixture_unreadable")
                continue
            if fixture.split != stage:
                audit.issues.append("evaluation_fixture_split_mismatch")
            groups[sample_content_key(fixture)].append(stage)
    audit.cross_split_groups = sum(len(set(stages)) > 1 for stages in groups.values())
    audit.within_split_groups = sum(len(stages) > len(set(stages)) for stages in groups.values())
    if audit.cross_split_groups:
        audit.issues.append("evaluation_cross_split_overlap")
    if audit.within_split_groups:
        audit.issues.append("evaluation_duplicate_sample")
    audit.issues = sorted(set(audit.issues))
    return audit


def validate_fixture_splits[FixtureKey: str](
    fixtures: Mapping[FixtureKey, Sequence[Path]],
) -> None:
    audit = audit_fixture_splits(fixtures)
    if audit.issues:
        raise ValueError(audit.issues[0])
