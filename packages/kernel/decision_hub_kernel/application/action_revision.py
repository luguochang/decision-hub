"""Same-opportunity revision planning and immutable review schedule readback.

No native authority is accepted here. Publisher authenticates the new source;
this helper reads server-recorded review intents and existing plan originals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.decision.action_derivation import DerivedActionPublication
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionCommandReceiptRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import (
    ActionRepository,
    StoredCommandReceipt,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    RunRecord,
    RunRoleOutputRecord,
    as_utc,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.action_plan import ActionPlanHead, ActionPlanRevision
from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchActionSynthesisSidecar

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.action_review import ActionReviewIntent

OWNER = "system:action-publication:v1"
REVIEW_SCHEMA = "action-review-publication.v1"
POSITIONS = ("flat", "long", "unknown")


@dataclass(frozen=True)
class RevisionChange:
    revision: ActionPlanRevision
    head: ActionPlanHead
    previous_head: ActionPlanHead
    disposition: Literal["updated", "retained"]
    reason: str
    next_review_at: datetime | None


def plan_revision_changes(
    session: Session,
    *,
    derived: DerivedActionPublication,
    intent: ActionReviewIntent,
    sidecar: ResearchActionSynthesisSidecar,
    published_at: datetime,
) -> tuple[RevisionChange, ...]:
    """Read current mutable heads only under Publisher's already held writer."""
    repository = ActionRepository(session)
    if published_at <= intent.captured_at:
        raise ValueError("action_review_publication_not_after_intent")
    parents = {value.position: value for value in intent.branches}
    candidates = {
        item.position_context: item
        for item in sidecar.synthesis.action_intents
        if (item.scope_id, item.horizon)
        == (intent.opportunity.scope_id, intent.opportunity.horizon)
    }
    changes = []
    for branch in derived.branches:
        candidate = branch.revision
        parent = parents[candidate.position_context]
        old = repository.get_revision(parent.revision_id)
        head = repository.get_head(candidate.plan_id)
        if (
            old is None
            or head is None
            or old.payload_hash != parent.revision_hash
            or head.current_revision_id != parent.revision_id
            or old.opportunity_id != intent.opportunity.id
        ):
            raise ValueError("action_review_revision_changed")
        if candidate.valid_until > old.valid_until:
            raise ValueError("action_review_validity_extension_forbidden")
        native = candidates.get(candidate.position_context)
        next_review = native.next_review_at if native else None
        if next_review is not None and next_review <= published_at:
            raise ValueError("action_review_next_due_not_future")
        if next_review is not None and next_review >= min(
            old.valid_until, intent.opportunity.time_end
        ):
            next_review = None  # Explicitly no remaining in-window review; original stays visible.
        if head.state == "triggered":
            reason = "triggered_protection_carryover_pending"
        elif head.state not in {"armed", "proposed"} or head.valid_until <= published_at:
            reason = "terminal_or_expired_plan_retained"
        else:
            reason = "same_opportunity_revision"
        if reason != "same_opportunity_revision":
            changes.append(RevisionChange(old, head, head, "retained", reason, next_review))
            continue
        body = candidate.model_dump(mode="json")
        body.update(revision_no=old.revision_no + 1, previous_id=old.revision_id)
        body["payload_hash"] = canonical_hash(
            {key: value for key, value in body.items() if key != "payload_hash"}
        )
        revision = ActionPlanRevision.model_validate(body)
        monitor = "paused" if head.monitor_state == "paused" else branch.head.monitor_state
        next_head = ActionPlanHead.model_validate(
            head.model_dump(mode="python")
            | {
                "current_revision_id": revision.revision_id,
                "state_version": head.state_version + 1,
                "lease_generation": head.lease_generation + 1,
                "lease_owner": None,
                "lease_expires_at": None,
                "monitor_state": monitor,
                "enter_streak": 0,
                "last_input_watermark": None,
                "next_due": None if monitor == "paused" else published_at,
                "valid_until": revision.valid_until,
            }
        )
        changes.append(RevisionChange(revision, next_head, head, "updated", reason, next_review))
    return tuple(changes)


def read_review_result(
    session: Session,
    request_id: str,
    *,
    now: datetime,
) -> tuple[StoredCommandReceipt, ResearchActionSynthesisSidecar]:
    """Read a committed immutable result, not a caller-authenticated candidate.

    The command hash authenticates its payload; trigger and sidecar hash bindings
    are rechecked here without recursively walking every prior review's schedule.
    """
    repository = ActionRepository(session)
    receipt = repository.get_command(OWNER, request_id)
    if receipt is None or receipt.operation != "publish" or receipt.accepted_at > now:
        raise ValueError("action_review_result_missing")
    body = receipt.response
    if body.get("review_schema") != REVIEW_SCHEMA or body.get("status") not in {
        "published",
        "review_only",
    }:
        raise ValueError("action_review_result_invalid")
    row = session.get(RunRoleOutputRecord, body.get("sidecar_ref"), populate_existing=True)
    if row is None:
        raise ValueError("action_review_result_source_missing")
    wire = json.loads(row.payload_json)
    sidecar = ResearchActionSynthesisSidecar.model_validate(wire)
    opportunity_id = body.get("opportunity_id")
    if not isinstance(opportunity_id, str):
        raise ValueError("action_review_result_invalid")
    opportunity = repository.get_opportunity(opportunity_id)
    if (
        opportunity is None
        or canonical_hash(wire) != row.payload_hash
        or canonical_hash(sidecar.model_dump(mode="json", exclude={"payload_hash"}))
        != sidecar.payload_hash
        or sidecar.payload_hash != body.get("sidecar_hash")
        or sidecar.run_id != body.get("run_id")
        or row.run_id != sidecar.run_id
        or row.role != "research_action_synthesis_" + sidecar.horizon
        or row.schema_version != sidecar.schema_version
        or as_utc(row.created_at) != receipt.accepted_at
        or (sidecar.scope_id, sidecar.horizon) != (opportunity.scope_id, opportunity.horizon)
        or request_id != "publish_" + canonical_hash([sidecar.run_id, opportunity.id])
    ):
        raise ValueError("action_review_result_source_mismatch")
    trigger = session.get(
        RiskResearchTriggerRecord, body.get("review_trigger_key"), populate_existing=True
    )
    if trigger is None:
        raise ValueError("action_review_result_intent_missing")
    trigger_body = json.loads(trigger.payload_json)
    original_intent = trigger_body.get("source", {}).get("review", {})
    if (
        trigger.kind != "action_review"
        or canonical_hash(trigger_body) != trigger.payload_hash
        or original_intent.get("payload_hash") != body.get("review_intent_hash")
        or canonical_hash(
            {key: value for key, value in original_intent.items() if key != "payload_hash"}
        )
        != body.get("review_intent_hash")
        or original_intent.get("opportunity", {}).get("id") != opportunity.id
    ):
        raise ValueError("action_review_result_intent_mismatch")
    request = session.get(RiskResearchRequestRecord, trigger.request_id, populate_existing=True)
    run = session.get(RunRecord, sidecar.run_id, populate_existing=True)
    if (
        request is None
        or run is None
        or request.run_id != sidecar.run_id
        or request.event_id != opportunity.event_ref
        or run.event_id != opportunity.event_ref
        or run.parent_run_id != original_intent.get("parent_run_id")
        or run.study_id != original_intent.get("study_id")
        or run.admission_origin != "automatic"
        or run.status not in {"completed", "degraded"}
        or run.finished_at is None
        or as_utc(run.finished_at) > receipt.accepted_at
        or request.status not in {"queued", "completed"}
    ):
        raise ValueError("action_review_result_run_mismatch")
    branches = body.get("branches")
    if not isinstance(branches, list) or [
        item.get("position_context") for item in branches
    ] != list(POSITIONS):
        raise ValueError("action_review_result_branches_invalid")
    updated = 0
    for branch in branches:
        revision = repository.get_revision(branch.get("revision_id"))
        if (
            revision is None
            or revision.plan_id != branch.get("plan_id")
            or revision.payload_hash != branch.get("revision_hash")
            or revision.opportunity_id != opportunity.id
            or revision.position_context != branch.get("position_context")
            or branch.get("disposition") not in {"updated", "retained"}
        ):
            raise ValueError("action_review_result_revision_mismatch")
        if branch["disposition"] == "updated":
            updated += 1
            if (
                revision.decision_id != body.get("decision_id")
                or revision.published_at != receipt.accepted_at
            ):
                raise ValueError("action_review_result_revision_mismatch")
        else:
            previous = next(
                (
                    item
                    for item in original_intent["branches"]
                    if item["position"] == revision.position_context
                ),
                None,
            )
            if previous is None or (previous["revision_id"], previous["revision_hash"]) != (
                revision.revision_id,
                revision.payload_hash,
            ):
                raise ValueError("action_review_result_retained_mismatch")
        due = branch.get("next_review_at")
        candidate = next(
            (
                item
                for item in sidecar.synthesis.action_intents
                if (item.scope_id, item.horizon, item.position_context)
                == (revision.scope_id, revision.horizon, revision.position_context)
            ),
            None,
        )
        expected = candidate.next_review_at if candidate else None
        if expected is not None and expected >= min(revision.valid_until, opportunity.time_end):
            expected = None
        if due != (expected.isoformat() if expected is not None else None) or (
            expected is not None and expected <= receipt.accepted_at
        ):
            raise ValueError("action_review_schedule_invalid")
    if bool(updated) != (body["status"] == "published"):
        raise ValueError("action_review_result_status_mismatch")
    if updated:
        decision_id = body.get("decision_id")
        if not isinstance(decision_id, str):
            raise ValueError("action_review_result_snapshot_mismatch")
        snapshot = repository.get_snapshot(decision_id)
        if (
            snapshot is None
            or snapshot.snapshot_hash != body.get("snapshot_hash")
            or snapshot.run_id != sidecar.run_id
            or snapshot.opportunity_id != opportunity.id
            or snapshot.source_result_ref != row.output_id
            or snapshot.source_result_hash != sidecar.payload_hash
            or snapshot.known_at != receipt.accepted_at
        ):
            raise ValueError("action_review_result_snapshot_mismatch")
    if not updated and (
        body.get("decision_id") is not None or body.get("snapshot_hash") is not None
    ):
        raise ValueError("action_review_result_unpublished_snapshot")
    return receipt, sidecar


def latest_review_result(session: Session, opportunity_id: str, *, now: datetime):
    row = session.scalar(
        select(ActionCommandReceiptRecord)
        .where(
            ActionCommandReceiptRecord.owner_id == OWNER,
            ActionCommandReceiptRecord.operation == "publish",
            ActionCommandReceiptRecord.accepted_at <= now,
            func.json_extract(ActionCommandReceiptRecord.response_json, "$.response.opportunity_id")
            == opportunity_id,
            func.json_extract(ActionCommandReceiptRecord.response_json, "$.response.review_schema")
            == REVIEW_SCHEMA,
        )
        .order_by(
            ActionCommandReceiptRecord.accepted_at.desc(),
            ActionCommandReceiptRecord.request_id.desc(),
        )
        .limit(1)
    )
    return read_review_result(session, row.request_id, now=now) if row else None
