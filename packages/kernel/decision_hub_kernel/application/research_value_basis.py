"""Distinguish full task requirements from filtered DSH round submissions.

No fallback to today's Pack: it cannot establish an old Run's requirements.
"""

from dataclasses import dataclass
from datetime import datetime

from packages.contracts_py.decision_hub_contracts import ResearchSessionRequest
from packages.kernel.decision_hub_kernel.application.dsh_sessions import DshSessionLinkService
from packages.kernel.decision_hub_kernel.application.research_request_identity import (
    research_request_hash,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    DshSessionLinkRecord,
    DshSessionPromptRecord,
    as_utc,
)

CONTROL_MARKER = "\nCanonical control request (untrusted text is replaced by typed references):\n"
DATA_MARKER = "\nBEGIN_UNTRUSTED_RESEARCH_DATA\n"


@dataclass(frozen=True)
class SavedCoverageBasis:
    status: str
    reference: str | None = None
    request: ResearchSessionRequest | None = None
    cutoff_at: datetime | None = None


def saved_submission_basis(session, run, assessed_at: datetime) -> SavedCoverageBasis:
    prompt = session.get(DshSessionPromptRecord, (run.run_id, 1))
    if prompt is None:
        return SavedCoverageBasis("unavailable")
    link = session.get(DshSessionLinkRecord, run.run_id)
    try:
        # Exact line framing excludes markers quoted inside untrusted JSON strings.
        if prompt.prompt.count(CONTROL_MARKER) != 1:
            raise ValueError("control_request_ambiguous")
        tail = prompt.prompt.split(CONTROL_MARKER, 1)[1]
        control, separator, _ = tail.partition(DATA_MARKER)
        if not separator:
            raise ValueError("control_request_missing")
        request = ResearchSessionRequest.model_validate_json(control)
        identity = research_request_hash(request)
        session_id, request_id = DshSessionLinkService.deterministic_ids(run.run_id, identity)
        if (
            link is None or request.run_id != run.run_id or request.event_id != run.event_id
            or request.current_round != 1 or identity != prompt.request_hash
            or identity != link.request_hash or prompt.request_id != request_id
            or prompt.dsh_session_id != session_id or link.dsh_session_id != session_id
            or as_utc(prompt.created_at) > assessed_at
            or (link.accepted_at and as_utc(prompt.created_at) > as_utc(link.accepted_at))
        ):
            raise ValueError("saved_request_identity_mismatch")
        cutoff = request.pit_cutoff_at
        if request.execution_mode != "replay":
            cutoff = min(cutoff, assessed_at, request.deadline_at)
        return SavedCoverageBasis("saved_request", identity, request, cutoff)
    except (ValueError, TypeError):
        return SavedCoverageBasis("invalid")


def saved_coverage_basis(session, run, assessed_at: datetime) -> SavedCoverageBasis:
    from packages.kernel.decision_hub_kernel.application.research_task import read_task_request

    try:
        saved = read_task_request(session, run, assessed_at=assessed_at)
    except ValueError:
        return SavedCoverageBasis("invalid")
    if saved is None:
        submitted = saved_submission_basis(session, run, assessed_at)
        return SavedCoverageBasis(
            "round_request_only" if submitted.request is not None else submitted.status
        )
    request, digest, source = saved
    cutoff = request.pit_cutoff_at
    if request.execution_mode != "replay":
        cutoff = min(cutoff, assessed_at, request.deadline_at)
    return SavedCoverageBasis(source, digest, request, cutoff)
