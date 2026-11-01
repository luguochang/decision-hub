"""Progress from authenticated native lineage; neither thought nor material count."""
from __future__ import annotations

from dataclasses import dataclass

from packages.contracts_py.decision_hub_contracts import (
    EvidenceCandidate, ResearchSessionRequest, ResearchSessionResult,
)

from ..decision.research_quality import _challenge_resolved
from ..ports.market_path import canonical_hash
from .research_quality_projection import QualityProjectionContextV1
from .research_review import has_valid_accepted_independent_review


@dataclass(frozen=True)
class SemanticProgress:
    resolved_challenge_ids: tuple[str, ...] = ()
    independent_review_established: bool = False
    review_refs: tuple[str, ...] = ()
    source_result_hash: str | None = None
    resolved_lineage_ids: tuple[str, ...] = ()

    @property
    def gained(self) -> bool:
        return bool(self.resolved_challenge_ids or self.independent_review_established)


def _bound(context: QualityProjectionContextV1 | None, result: ResearchSessionResult | None):
    return bool(context is not None and result is not None
                and context.bound_result_hash == canonical_hash(result.model_dump(mode="json")))


def accepted_refs_for_progress(evidence: list[EvidenceCandidate], request: ResearchSessionRequest, cutoff):
    """Use the same admission/PIT boundary as quality_input_from_state."""
    return {
        item.evidence_id for item in evidence if item.quality == "accepted"
        and item.received_at <= cutoff and item.observed_at <= cutoff
    } | {
        item.evidence_id for item in request.input_evidence if item.received_at <= cutoff
        and (item.observed_at is None or item.observed_at <= cutoff)
    }


def _independent(context, result, accepted_refs=None) -> bool:
    if not _bound(context, result) or result.status in {"failed", "cancelled"}:
        return False
    # The full result is authenticated above. This qualification-only view
    # never replaces the original result or loosens a final publication gate.
    independent = [item for item in result.reviews or []
                   if item.role == "independent_reviewer"]
    if len(independent) != 1 or independent[0].review_id not in context.authenticated_review_ids:
        return False
    view = result.model_copy(update={"reviews": independent})
    return has_valid_accepted_independent_review(
        view, native_provenance_verified=True,
        additional_allowed_evidence_ids=accepted_refs,
    )


def classify_semantic_progress(
    *, run_id: str, previous_result: ResearchSessionResult | None,
    previous_context: QualityProjectionContextV1 | None,
    result: ResearchSessionResult, context: QualityProjectionContextV1 | None,
    accepted_evidence_refs: set[str],
    previous_accepted_evidence_refs: set[str] | None = None,
    already_resolved_lineage_ids: frozenset[str] = frozenset(),
    independent_review_seen: bool = False,
) -> SemanticProgress:
    """Caller must reauthenticate Graph context and every native proof original.

    DTO or reference presence is not authentication. Only the Server reader can
    derive lineage; public model receipts cannot supply it. This classifier
    controls an existing no-gain counter, never completion or execution rights.
    """
    if not _bound(context, result) or result.status in {"failed", "cancelled"}:
        return SemanticProgress()
    assert context is not None
    result_hash = canonical_hash(result.model_dump(mode="json"))
    gained, lineage_ids = [], []
    if _bound(previous_context, previous_result):
        assert previous_context is not None and previous_result is not None
        prior_hash = canonical_hash(previous_result.model_dump(mode="json"))
        prior_refs = {item.evidence_id for item in previous_result.evidence_candidates
                      if item.quality == "accepted"}
        prior_refs.update(previous_accepted_evidence_refs or set())
        prior = {item.challenge_id: item for item in previous_context.challenges}
        current = {item.challenge_id: item for item in context.challenges}
        # Duplicated/ambiguous lineage cannot multiply semantic work.
        counts = {}
        for link in context.authenticated_review_lineage:
            counts[link.lineage_id] = counts.get(link.lineage_id, 0) + 1
        for link in context.authenticated_review_lineage:
            old, new = prior.get(link.previous_challenge_id), current.get(link.challenge_id)
            if (link.lineage_id in already_resolved_lineage_ids
                    or counts[link.lineage_id] != 1 or old is None or new is None
                    or link.run_id != run_id
                    or link.root_session_id != result.research_session_id
                    or link.root_session_id != previous_result.research_session_id
                    or link.result_hash != result_hash or link.previous_result_hash != prior_hash
                    or link.review_id != new.source_review_id
                    or link.previous_review_id != old.source_review_id
                    or link.review_id not in context.authenticated_review_ids
                    or link.previous_review_id not in previous_context.authenticated_review_ids
                    or old.severity != new.severity
                    or set(old.blocking_scope) != set(new.blocking_scope)
                    or set(old.evidence_refs) != set(new.evidence_refs)
                    or _challenge_resolved(old, prior_hash, prior_refs)
                    or not _challenge_resolved(new, result_hash, accepted_evidence_refs)):
                continue
            gained.append(new.challenge_id)
            lineage_ids.append(link.lineage_id)
    independent = (not independent_review_seen
                   and _independent(context, result, accepted_evidence_refs)
                   and not _independent(previous_context, previous_result,
                                        previous_accepted_evidence_refs))
    return SemanticProgress(
        tuple(sorted(gained)), independent,
        tuple(sorted(item.review_id for item in result.reviews or []
                     if item.role == "independent_reviewer")) if independent else (),
        result_hash, tuple(sorted(lineage_ids)),
    )
