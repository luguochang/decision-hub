from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate,
    ResearchCapabilityQuery,
    ResearchCapabilityResult,
    ResearchEvaluationCase,
)
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchCapabilityError,
    research_evidence_content_hash,
    research_evidence_instance_id,
)


@dataclass(frozen=True)
class ReplayResearchArchive:
    queries: Mapping[str, Sequence[EvidenceCandidate]]
    requirements: Mapping[str, Sequence[EvidenceCandidate]]


class ReplayResearchCapabilityAdapter:
    supported_modes = frozenset({"replay"})

    def __init__(
        self,
        *,
        capability_id: str,
        fixtures: Mapping[str, Sequence[EvidenceCandidate]],
        requirement_fixtures: Mapping[str, Sequence[EvidenceCandidate]] | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.fixtures = fixtures
        self.requirement_fixtures = requirement_fixtures or {}

    async def execute(self, query: ResearchCapabilityQuery) -> ResearchCapabilityResult:
        archived = self.fixtures.get(query.query) or self.requirement_fixtures.get(
            query.requirement_id
        )
        if archived is None:
            raise ResearchCapabilityError(
                "research_replay_fixture_missing", "no archived result matches the replay query"
            )
        candidates = [_bind_replay_lineage(item, query) for item in archived]
        return ResearchCapabilityResult(
            schema_version="research-capability-result.v1",
            request_id=query.request_id,
            capability_id=query.capability_id,
            provider="archived-replay",
            evidence_candidates=list(candidates),
            cost_usd=0.0,
            completed_at=max(item.received_at for item in candidates),
        )


def load_replay_fixtures(path: Path | None) -> dict[str, tuple[EvidenceCandidate, ...]]:
    """Load an explicit, versioned replay archive without a live fallback."""

    return {
        query: tuple(candidates)
        for query, candidates in load_replay_archive(path).queries.items()
    }


def load_replay_archive(path: Path | None) -> ReplayResearchArchive:
    """Load either the legacy query archive or one immutable evaluation case."""

    if path is None:
        return ReplayResearchArchive({}, {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research_replay_fixture_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("research_replay_fixture_invalid")
    if payload.get("schema_version") == "research-evaluation-case.v1":
        return _evaluation_case_archive(payload)
    if payload.get("schema_version") != "research-replay-fixtures.v1":
        raise ValueError("research_replay_fixture_invalid")
    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise ValueError("research_replay_fixture_invalid")

    fixtures: dict[str, tuple[EvidenceCandidate, ...]] = {}
    for entry in queries:
        if not isinstance(entry, dict):
            raise ValueError("research_replay_fixture_invalid")
        query = entry.get("query")
        raw_candidates = entry.get("evidence_candidates")
        if (
            not isinstance(query, str)
            or not query.strip()
            or not isinstance(raw_candidates, list)
            or not raw_candidates
            or query in fixtures
        ):
            raise ValueError("research_replay_fixture_invalid")
        fixtures[query] = tuple(
            EvidenceCandidate.model_validate(candidate) for candidate in raw_candidates
        )
    return ReplayResearchArchive(fixtures, {})


def _evaluation_case_archive(payload: Mapping[str, object]) -> ReplayResearchArchive:
    try:
        case = ResearchEvaluationCase.model_validate(payload)
    except ValueError as exc:
        raise ValueError("research_replay_fixture_invalid") from exc
    queries: dict[str, tuple[EvidenceCandidate, ...]] = {}
    requirements: dict[str, tuple[EvidenceCandidate, ...]] = {}
    for fixture in case.archived_capability_fixtures:
        if fixture.capability_id != "replay.research":
            continue
        candidates = tuple(fixture.evidence_candidates)
        if fixture.requirement_id in requirements:
            raise ValueError("research_replay_fixture_invalid")
        requirements[fixture.requirement_id] = candidates
        for alias in fixture.query_aliases:
            if alias in queries:
                raise ValueError("research_replay_fixture_invalid")
            queries[alias] = candidates
    if not requirements:
        raise ValueError("research_replay_fixture_invalid")
    return ReplayResearchArchive(queries, requirements)


def _bind_replay_lineage(
    archived: EvidenceCandidate, query: ResearchCapabilityQuery
) -> EvidenceCandidate:
    source_url = str(archived.source_url) if archived.source_url is not None else None
    content_hash = research_evidence_content_hash(
        requirement_id=query.requirement_id,
        kind=archived.kind,
        authority=archived.authority,
        source_id=archived.source_id,
        source_url=source_url,
        published_at=archived.published_at,
        excerpt=archived.excerpt,
        structured_payload_ref=archived.structured_payload_ref,
    )
    return archived.model_copy(
        update={
            "evidence_id": research_evidence_instance_id(
                content_hash=content_hash,
                research_session_id=query.research_session_id,
            ),
            "requirement_id": query.requirement_id,
            "content_hash": content_hash,
            "tool_call_id": query.request_id,
            "research_session_id": query.research_session_id,
            "round": query.round,
            "quality": "candidate",
            "freshness_status": "unknown",
        }
    )
