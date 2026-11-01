"""Canonical result accounting shared by Runtime production and original replay."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchSessionResult
from packages.kernel.decision_hub_kernel.application.research_review import (
    bind_reviews_to_result_version,
    research_result_content_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import ResearchToolCallReservationRecord


def normalize_tool_accounting(
    result: ResearchSessionResult,
    executed_this_generation: int,
) -> ResearchSessionResult:
    """Keep an authentic review bound to deterministic Gateway accounting."""
    original_hash = research_result_content_hash(result)
    if any(review.result_hash != original_hash for review in result.reviews or []):
        raise ValueError("research_review_result_version_invalid")
    normalized = result.model_copy(update={"total_tool_calls": executed_this_generation})
    return bind_reviews_to_result_version(normalized)


def normalize_generation_tool_accounting(
    session: Session,
    result: ResearchSessionResult,
    *,
    run_id: str,
    generation: int,
    research_session_id: str,
    max_tool_calls: int,
) -> ResearchSessionResult:
    """Use complete original generation reservations, including before recovery."""
    count = session.scalar(
        select(func.count())
        .select_from(ResearchToolCallReservationRecord)
        .where(
            ResearchToolCallReservationRecord.run_id == run_id,
            ResearchToolCallReservationRecord.generation == generation,
            ResearchToolCallReservationRecord.research_session_id == research_session_id,
        )
    )
    if count is None or not 0 <= count <= max_tool_calls:
        raise ValueError("research_perpetual_tool_count_out_of_bounds")
    return normalize_tool_accounting(result, count)
