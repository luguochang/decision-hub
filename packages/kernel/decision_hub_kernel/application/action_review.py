"""Due Action review intents; original plans are context, never market facts.

W3.1 draft. No model executor, revision writer or default-enabled automation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.contracts_py.decision_hub_contracts import ResearchInputEvidence
from packages.contracts_py.decision_hub_contracts.generated.research_binding import (
    ResearchInputEvidence as FrozenTaskInputEvidence,
)
from packages.kernel.decision_hub_kernel.persistence.action_records import (
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
)
from packages.kernel.decision_hub_kernel.persistence.action_repository import ActionRepository
from packages.kernel.decision_hub_kernel.persistence.db import (
    Database,
    RunRecord,
    RunRoleOutputRecord,
    StudyRecord,
    as_utc,
    utcnow,
)
from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
    begin_immediate,
    prepare_originals,
)
from packages.kernel.decision_hub_kernel.persistence.risk_research_records import (
    RiskResearchRequestRecord,
    RiskResearchTriggerRecord,
)
from packages.kernel.decision_hub_kernel.ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    DecisionSnapshot,
    Opportunity,
)
from packages.kernel.decision_hub_kernel.ports.market_path import (
    FrozenContract,
    aware_utc,
    canonical_hash,
    spot_scope,
)
from packages.kernel.decision_hub_kernel.ports.research_signal import ResearchActionSynthesisSidecar

KIND = "action_review"
PREFIX = "action-review:"
POSITIONS = ("flat", "long", "unknown")
ACTIVE = {"armed", "triggered"}
PUBLISH_OWNER = "system:action-publication:v1"


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class ReviewBranch(FrozenContract):
    position: Literal["flat", "long", "unknown"]
    revision_id: str
    revision_hash: str = Field(pattern="^[a-f0-9]{64}$")
    decision_id: str
    snapshot_hash: str = Field(pattern="^[a-f0-9]{64}$")
    sidecar_ref: str
    sidecar_hash: str = Field(pattern="^[a-f0-9]{64}$")
    captured_head: ActionPlanHead
    next_review_at: AwareDatetime | None
    schedule_receipt_ref: str | None = None
    schedule_receipt_hash: str | None = None


class ActionReviewIntent(FrozenContract):
    schema_version: Literal["action-review-intent.v1"] = "action-review-intent.v1"
    opportunity: Opportunity
    instrument: Literal["BTC-USDT", "ETH-USDT"]
    parent_run_id: str
    event_id: str
    study_id: str
    branches: tuple[ReviewBranch, ...]
    due_at: AwareDatetime
    captured_at: AwareDatetime
    revision_set_hash: str = Field(pattern="^[a-f0-9]{64}$")
    trigger_key: str
    payload_hash: str = Field(pattern="^[a-f0-9]{64}$")


def _seal(body: dict[str, Any]) -> ActionReviewIntent:
    draft = ActionReviewIntent.model_validate(body | {"payload_hash": "0" * 64})
    value = draft.model_dump(mode="json", exclude={"payload_hash"})
    return ActionReviewIntent.model_validate(value | {"payload_hash": canonical_hash(value)})


def _revision_set(branches: tuple[ReviewBranch, ...]) -> str:
    return canonical_hash([[b.position, b.revision_id, b.revision_hash] for b in branches])


def _key(opportunity_id: str, revision_set: str, due: datetime) -> str:
    return KIND + ":" + canonical_hash([opportunity_id, revision_set, due.isoformat()])


def _branch_original(session: Session, revision: ActionPlanRevision, now: datetime):
    repository = ActionRepository(session)
    decision = repository.get_snapshot(revision.decision_id)
    if decision is None or decision.known_at > now:
        raise ValueError("action_review_decision_missing_or_future")
    row = session.get(RunRoleOutputRecord, decision.source_result_ref, populate_existing=True)
    if row is None:
        raise ValueError("action_review_sidecar_missing")
    body = json.loads(row.payload_json)
    sidecar = ResearchActionSynthesisSidecar.model_validate(body)
    if (
        canonical_hash(body) != row.payload_hash
        or canonical_hash(sidecar.model_dump(mode="json", exclude={"payload_hash"}))
        != sidecar.payload_hash
        or sidecar.payload_hash != decision.source_result_hash
        or (sidecar.run_id, sidecar.scope_id, sidecar.horizon, sidecar.producer_cutoff)
        != (decision.run_id, decision.scope_id, decision.horizon, decision.decision_cutoff_at)
        or row.run_id != decision.run_id
        or row.role != "research_action_synthesis_" + decision.horizon
        or row.schema_version != sidecar.schema_version
        or as_utc(row.created_at) != decision.known_at
    ):
        raise ValueError("action_review_sidecar_mismatch")
    candidates = [
        item
        for item in sidecar.synthesis.action_intents
        if (item.scope_id, item.horizon, item.position_context)
        == (revision.scope_id, revision.horizon, revision.position_context)
    ]
    if revision.position_context == "unknown":
        candidate = None
        branch_body = {
            "position_context": "unknown",
            "action": "wait",
            "source": sidecar.payload_hash,
        }
    elif len(candidates) == 1:
        candidate = candidates[0]
        branch_body = candidate.model_dump(mode="json")
    else:
        raise ValueError("action_review_position_original_missing")
    refs = {item.ref: item.hash for item in revision.basis_refs}
    if refs.get(row.output_id) != sidecar.payload_hash or refs.get(
        row.output_id + "#position=" + revision.position_context
    ) != canonical_hash(branch_body):
        raise ValueError("action_review_branch_binding_mismatch")
    receipt = repository.get_command(
        PUBLISH_OWNER,
        "publish_"
        + canonical_hash(
            [
                decision.run_id,
                revision.opportunity_id,
            ]
        ),
    )
    if (
        receipt is None
        or receipt.operation != "publish"
        or receipt.response.get("status") != "published"
        or receipt.response.get("opportunity_id") != revision.opportunity_id
        or receipt.response.get("decision_id") != decision.id
        or receipt.response.get("snapshot_hash") != decision.snapshot_hash
        or receipt.response.get("sidecar_ref") != row.output_id
        or receipt.response.get("sidecar_hash") != sidecar.payload_hash
        or receipt.accepted_at != revision.published_at
        or not any(
            (
                item.get("plan_id"),
                item.get("revision_id"),
                item.get("revision_hash"),
                item.get("position_context"),
            )
            == (
                revision.plan_id,
                revision.revision_id,
                revision.payload_hash,
                revision.position_context,
            )
            for item in receipt.response.get("branches", [])
        )
    ):
        raise ValueError("action_review_publication_original_missing")
    return decision, sidecar, candidate


def _parent(
    session: Session, run_id: str, opportunity: Opportunity, now: datetime, *, active: bool = True
):
    run = session.get(RunRecord, run_id, populate_existing=True)
    study = session.get(StudyRecord, run.study_id, populate_existing=True) if run else None
    instrument = next(
        (
            item
            for item in ("BTC-USDT", "ETH-USDT")
            if spot_scope(item).scope_id == opportunity.scope_id
        ),
        None,
    )
    if (
        run is None
        or study is None
        or instrument is None
        or (active and study.status != "active")
        or run.event_id != opportunity.event_ref
        or run.strategy_version != "research.v1"
        or run.status not in {"completed", "degraded"}
        or run.finished_at is None
        or not as_utc(run.created_at) <= as_utc(run.finished_at) <= now
        or study.market != "crypto"
        or study.instrument != instrument
        or study.horizon_scope != "30m,24h,72h"
        or study.identity_basis != "exact_spot_monitor"
    ):
        raise ValueError("action_review_parent_scope_invalid")
    return run, study, instrument


def _opportunity(value: Opportunity | None, now: datetime) -> Opportunity:
    if (
        value is None
        or value.cohort != "prospective"
        or value.status != "decided"
        or value.event_ref is None
        or not value.registered_at <= now < value.time_end
    ):
        raise ValueError("action_review_opportunity_not_active")
    return value


def _capture(session: Session, opportunity_id: str, now: datetime) -> ActionReviewIntent:
    repository = ActionRepository(session)
    opportunity = _opportunity(repository.get_opportunity(opportunity_id), now)
    rows = list(
        session.scalars(
            select(ActionPlanRevisionRecord)
            .join(
                ActionPlanHeadRecord,
                ActionPlanHeadRecord.current_revision_id == ActionPlanRevisionRecord.revision_id,
            )
            .where(ActionPlanRevisionRecord.opportunity_id == opportunity_id)
            .order_by(ActionPlanRevisionRecord.position_context)
            .limit(4)
        )
    )
    if len(rows) != 3 or {row.position_context for row in rows} != set(POSITIONS):
        raise ValueError("action_review_three_positions_required")
    from .action_revision import latest_review_result

    scheduled = latest_review_result(session, opportunity_id, now=now)
    branches: list[ReviewBranch] = []
    decisions: list[DecisionSnapshot] = []
    for row in sorted(rows, key=lambda value: POSITIONS.index(value.position_context)):
        revision, head = repository.get_revision(row.revision_id), repository.get_head(row.plan_id)
        if revision is None or head is None:
            raise ValueError("action_review_plan_original_missing")
        decision, _sidecar, candidate = _branch_original(session, revision, now)
        due = candidate.next_review_at if candidate else None
        if scheduled is not None:
            saved = next(
                item
                for item in scheduled[0].response["branches"]
                if item["position_context"] == revision.position_context
            )
            if (saved["revision_id"], saved["revision_hash"]) != (
                revision.revision_id,
                revision.payload_hash,
            ):
                raise ValueError("action_review_schedule_revision_mismatch")
            due = (
                datetime.fromisoformat(saved["next_review_at"]) if saved["next_review_at"] else None
            )
        branches.append(
            ReviewBranch(
                position=revision.position_context,
                revision_id=revision.revision_id,
                revision_hash=revision.payload_hash,
                decision_id=decision.id,
                snapshot_hash=decision.snapshot_hash,
                sidecar_ref=decision.source_result_ref,
                sidecar_hash=decision.source_result_hash,
                captured_head=head,
                next_review_at=due,
                schedule_receipt_ref=scheduled[0].request_id if scheduled else None,
                schedule_receipt_hash=scheduled[0].receipt_hash if scheduled else None,
            )
        )
        decisions.append(decision)
    parent_id = (
        scheduled[1].run_id
        if scheduled
        else max(decisions, key=lambda value: (value.known_at, value.id)).run_id
    )
    parent, study, instrument = _parent(session, parent_id, opportunity, now)
    due_values = [b.next_review_at for b in branches if b.next_review_at is not None]
    if not due_values:
        raise ValueError("action_review_due_original_missing")
    due = min(due_values)
    frozen_branches = tuple(branches)
    revision_set = _revision_set(frozen_branches)
    return _seal(
        dict(
            opportunity=opportunity,
            instrument=instrument,
            parent_run_id=parent.run_id,
            event_id=parent.event_id,
            study_id=study.study_id,
            branches=frozen_branches,
            due_at=due,
            captured_at=now,
            revision_set_hash=revision_set,
            trigger_key=_key(opportunity.id, revision_set, due),
        )
    )


def _eligible(session: Session, intent: ActionReviewIntent, now: datetime) -> tuple[str, ...]:
    repository = ActionRepository(session)
    current = _opportunity(repository.get_opportunity(intent.opportunity.id), now)
    if current != intent.opportunity:
        raise ValueError("action_review_opportunity_changed")
    eligible = []
    for branch in intent.branches:
        head = repository.get_head(branch.captured_head.plan_id)
        if head is None or head.current_revision_id != branch.revision_id:
            raise ValueError("action_review_revision_changed")
        if (
            head.state in ACTIVE
            and head.monitor_state in {"active", "degraded"}
            and head.valid_until > now
            and branch.next_review_at is not None
            and branch.next_review_at <= now
        ):
            eligible.append(head.plan_id)
    return tuple(eligible)


def recognized_action_review(
    session: Session,
    row: RiskResearchRequestRecord,
    triggers: Sequence[RiskResearchTriggerRecord],
    *,
    now: datetime,
):
    selected = [trigger for trigger in triggers if trigger.kind == KIND]
    if not selected:
        return None
    if len(selected) != 1 or len(triggers) != 1:
        raise ValueError("action_review_trigger_mixed")
    trigger = selected[0]
    payload = json.loads(trigger.payload_json)
    if set(payload) != {"instrument", "scope_id", "kind", "source_ref", "source"} or set(
        payload["source"]
    ) != {"review"}:
        raise ValueError("action_review_trigger_shape_invalid")
    intent = ActionReviewIntent.model_validate(payload["source"]["review"])
    if (
        canonical_hash(intent.model_dump(mode="json", exclude={"payload_hash"}))
        != intent.payload_hash
        or tuple(branch.position for branch in intent.branches) != POSITIONS
        or _revision_set(intent.branches) != intent.revision_set_hash
        or intent.trigger_key
        != _key(intent.opportunity.id, intent.revision_set_hash, intent.due_at)
        or intent.due_at
        != min(b.next_review_at for b in intent.branches if b.next_review_at is not None)
        or intent.due_at > intent.captured_at
        or intent.captured_at > now
        or row.request_id
        != "riskresearch_" + canonical_hash([row.scope_id, trigger.trigger_key])[:40]
        or trigger.trigger_key != intent.trigger_key
        or trigger.source_ref != intent.opportunity.id
        or trigger.scope_id != row.scope_id
        or row.instrument != intent.instrument
        or row.scope_id != intent.opportunity.scope_id
        or as_utc(trigger.recorded_at) != intent.captured_at
        or as_utc(row.created_at) != intent.captured_at
        or canonical_hash(payload) != trigger.payload_hash
        or _json(payload) != trigger.payload_json
        or payload
        != {
            "instrument": row.instrument,
            "scope_id": row.scope_id,
            "kind": KIND,
            "source_ref": intent.opportunity.id,
            "source": {"review": intent.model_dump(mode="json")},
        }
    ):
        raise ValueError("action_review_trigger_original_mismatch")
    repository = ActionRepository(session)
    opportunity = repository.get_opportunity(intent.opportunity.id)
    if opportunity is None or opportunity.model_dump(
        exclude={"status", "reason"}
    ) != intent.opportunity.model_dump(exclude={"status", "reason"}):
        raise ValueError("action_review_opportunity_changed")
    from .action_revision import latest_review_result

    scheduled = latest_review_result(session, opportunity.id, now=intent.captured_at)
    parent_sources: list[DecisionSnapshot] = []
    for branch in intent.branches:
        revision = repository.get_revision(branch.revision_id)
        if revision is None:
            raise ValueError("action_review_plan_original_missing")
        decision, _, candidate = _branch_original(session, revision, now)
        parent_sources.append(decision)
        due = candidate.next_review_at if candidate else None
        if scheduled is not None:
            saved = next(
                item
                for item in scheduled[0].response["branches"]
                if item["position_context"] == branch.position
            )
            if (saved["revision_id"], saved["revision_hash"]) != (
                revision.revision_id,
                revision.payload_hash,
            ):
                raise ValueError("action_review_schedule_revision_mismatch")
            due = (
                datetime.fromisoformat(saved["next_review_at"]) if saved["next_review_at"] else None
            )
        if (branch.schedule_receipt_ref, branch.schedule_receipt_hash) != (
            (scheduled[0].request_id, scheduled[0].receipt_hash) if scheduled else (None, None)
        ):
            raise ValueError("action_review_schedule_original_mismatch")
        if (
            revision.payload_hash != branch.revision_hash
            or decision.id != branch.decision_id
            or decision.snapshot_hash != branch.snapshot_hash
            or decision.source_result_ref != branch.sidecar_ref
            or decision.source_result_hash != branch.sidecar_hash
            or branch.position != revision.position_context
            or revision.opportunity_id != opportunity.id
            or branch.captured_head.plan_id != revision.plan_id
            or branch.captured_head.current_revision_id != revision.revision_id
            or branch.captured_head.valid_until != revision.valid_until
            or branch.next_review_at != due
        ):
            raise ValueError("action_review_revision_original_mismatch")
    expected_parent = (
        scheduled[1].run_id
        if scheduled
        else max(parent_sources, key=lambda value: (value.known_at, value.id)).run_id
    )
    if intent.parent_run_id != expected_parent:
        raise ValueError("action_review_parent_original_mismatch")
    run, study, instrument = _parent(session, intent.parent_run_id, opportunity, now, active=False)
    if (run.event_id, study.study_id, instrument) != (
        intent.event_id,
        intent.study_id,
        intent.instrument,
    ):
        raise ValueError("action_review_parent_scope_invalid")
    return intent


def require_action_review_current(session: Session, intent: ActionReviewIntent, *, now: datetime):
    _parent(session, intent.parent_run_id, intent.opportunity, now)
    if not _eligible(session, intent, now):
        raise ValueError("action_review_no_due_active_branch")


def review_for_run_in_session(session: Session, run_id: str) -> ActionReviewIntent | None:
    run = session.get(RunRecord, run_id, populate_existing=True)
    expected_review_child = bool(
        run and run.parent_run_id and (run.idempotency_key or "").startswith("risk-research:")
    )
    rows = list(
        session.scalars(
            select(RiskResearchRequestRecord).where(RiskResearchRequestRecord.protocol_version == "risk-research-request.spot.v1")
            .where(
                RiskResearchRequestRecord.run_id == run_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    if not rows:
        if expected_review_child:
            raise ValueError("action_review_request_original_missing")
        return None
    if len(rows) != 1:
        raise ValueError("action_review_run_identity_ambiguous")
    row = rows[0]
    triggers = list(
        session.scalars(
            select(RiskResearchTriggerRecord)
            .where(
                RiskResearchTriggerRecord.request_id == row.request_id,
            )
            .execution_options(populate_existing=True)
        )
    )
    if run is None:
        raise ValueError("action_review_child_missing")
    # Read original intent at the child's recorded admission boundary, never a
    # wall-clock reconstruction of past context or a later "latest" revision.
    intent = recognized_action_review(session, row, triggers, now=as_utc(run.created_at))
    if intent is None and expected_review_child:
        raise ValueError("action_review_trigger_original_missing")
    if intent is not None and (
        row.event_id != intent.event_id
        or run.event_id != intent.event_id
        or run.parent_run_id != intent.parent_run_id
        or run.study_id != intent.study_id
        or run.admission_origin != "automatic"
        or run.strategy_version != "research.v1"
        or row.status not in {"queued", "completed"}
    ):
        raise ValueError("action_review_child_binding_invalid")
    return intent


def _context_parts(ref: str, body: dict[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Losslessly partition canonical JSON under the existing excerpt contract."""
    text = _json(body)
    if len(text) <= 4000:
        return ((ref, body),)
    envelope = {
        "schema_version": "action-review-context-chunk.v1",
        "branch_ref": ref,
        "body_hash": canonical_hash(body),
        "encoding": "canonical-json-string-fragments",
        "chunk_index": 38,
        "chunk_count": 38,
        "fragment": "",
    }
    parts: list[str] = []
    offset = 0
    while offset < len(text):
        if len(parts) >= 38:
            raise ValueError("action_review_context_item_budget_exceeded")
        low, high = 0, min(4000, len(text) - offset)
        # Bound the ENCODED envelope, including escaped quotes/backslashes.
        while low < high:
            middle = (low + high + 1) // 2
            if len(_json(envelope | {"fragment": text[offset : offset + middle]})) <= 4000:
                low = middle
            else:
                high = middle - 1
        if low == 0:
            raise ValueError("action_review_context_envelope_overflow")
        parts.append(text[offset : offset + low])
        offset += low
    return tuple(
        (
            f"{ref}:chunk:{index:02d}",
            envelope | {"chunk_index": index, "chunk_count": len(parts), "fragment": part},
        )
        for index, part in enumerate(parts, start=1)
    )


def action_review_context_inputs_in_session(
    session: Session, run_id: str
) -> tuple[ResearchInputEvidence, ...]:
    intent = review_for_run_in_session(session, run_id)
    if intent is None:
        return ()
    repository = ActionRepository(session)
    result = []
    for branch in intent.branches:
        revision = repository.get_revision(branch.revision_id)
        assert revision is not None
        body = {
            "original_ref": intent.trigger_key,
            "intent_hash": intent.payload_hash,
            "opportunity_id": intent.opportunity.id,
            "parent_run_id": intent.parent_run_id,
            "scope_id": intent.opportunity.scope_id,
            "horizon": intent.opportunity.horizon,
            "old_revision_id": revision.revision_id,
            "old_revision_hash": revision.payload_hash,
            "position_context": branch.position,
            "captured_head": branch.captured_head.model_dump(mode="json"),
            "action": revision.action,
            "reason": revision.reason,
            "entry": [condition.model_dump(mode="json") for condition in revision.entry],
            "exit": [condition.model_dump(mode="json") for condition in revision.exit],
            "invalidation": [
                condition.model_dump(mode="json") for condition in revision.invalidation
            ],
            "size_policy": revision.size_policy.model_dump(mode="json"),
            "valid_until": revision.valid_until.isoformat(),
            "review_question": (
                "复核主反机制与原行动是否仍成立，指出哪些依据或条件改变；"
                "已触发方案不能再次入场或扩大已有风险。原机会期限不延长。"
                "旧方案是待检验上下文，不是市场事实。"
            ),
        }
        ref = PREFIX + intent.payload_hash + ":" + branch.position
        for part_ref, part in _context_parts(ref, body):
            result.append(
                ResearchInputEvidence(
                    evidence_id=part_ref,
                    source_id=part_ref,
                    kind="document",
                    authority="unverified",
                    source_url=None,
                    published_at=None,
                    observed_at=intent.captured_at,
                    received_at=intent.captured_at,
                    content_hash=canonical_hash(part),
                    excerpt=_json(part),
                )
            )
        if len(result) > 38:
            raise ValueError("action_review_context_item_budget_exceeded")
    return tuple(result)


def validate_action_review_context_in_session(
    session: Session, run_id: str, inputs: Sequence[ResearchInputEvidence | FrozenTaskInputEvidence]
) -> None:
    expected = action_review_context_inputs_in_session(session, run_id)
    actual = tuple(item for item in inputs if item.source_id.startswith(PREFIX))
    # Ranking may reorder protected items; identity and every original byte
    # must still match, with no duplicate IDs hidden by a dictionary conversion.
    expected_by_id = {item.evidence_id: item.model_dump(mode="json") for item in expected}
    actual_by_id = {item.evidence_id: item.model_dump(mode="json") for item in actual}
    if len(actual_by_id) != len(actual) or actual_by_id != expected_by_id:
        raise ValueError("action_review_frozen_inputs_changed")


class ActionReviewService:
    def __init__(
        self, database: Database, *, clock: Callable[[], datetime] = utcnow, enabled: bool = False
    ):
        self.database, self.clock, self.enabled = database, clock, enabled

    def stage(self, opportunity_id: str) -> str | None:
        if not self.enabled:
            return None
        now = aware_utc(self.clock())
        prepared = prepare_originals(
            self.database, lambda session: _capture(session, opportunity_id, now)
        )
        intent = prepared.value
        with self.database.session() as session:
            begin_immediate(session)
            old = session.get(RiskResearchTriggerRecord, intent.trigger_key, populate_existing=True)
            if old is not None:
                row = session.get(RiskResearchRequestRecord, old.request_id, populate_existing=True)
                if row is None:
                    raise ValueError("action_review_request_original_missing")
                recognized_action_review(session, row, [old], now=aware_utc(self.clock()))
                return row.request_id
            prepared.verify(session)
            writer_at = aware_utc(self.clock())
            if not _eligible(session, intent, writer_at):
                return None
            # Record actual writer time, not time spent preparing originals.
            intent = _seal(
                intent.model_dump(mode="python", exclude={"payload_hash"})
                | {"captured_at": writer_at}
            )
            request_id = (
                "riskresearch_"
                + canonical_hash([intent.opportunity.scope_id, intent.trigger_key])[:40]
            )
            payload = {
                "instrument": intent.instrument,
                "scope_id": intent.opportunity.scope_id,
                "kind": KIND,
                "source_ref": opportunity_id,
                "source": {"review": intent.model_dump(mode="json")},
            }
            session.add(
                RiskResearchRequestRecord(
                    request_id=request_id,
                    scope_id=intent.opportunity.scope_id,
                    instrument=intent.instrument,
                    status="pending_admission",
                    created_at=writer_at,
                    updated_at=writer_at,
                    available_at=writer_at,
                    attempts=0,
                    lease_generation=0,
                )
            )
            session.add(
                RiskResearchTriggerRecord(
                    trigger_key=intent.trigger_key,
                    request_id=request_id,
                    kind=KIND,
                    scope_id=intent.opportunity.scope_id,
                    source_ref=opportunity_id,
                    payload_json=_json(payload),
                    payload_hash=canonical_hash(payload),
                    recorded_at=writer_at,
                )
            )
            session.flush()
            prepared.verify_after_writes(session)
            return request_id

    def discover(self, *, max_opportunities: int = 2, after: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"request_ids": [], "next_cursor": None, "errors": []}
        now = aware_utc(self.clock())
        with self.database.session() as session:
            statement = select(
                DecisionOpportunityRecord.opportunity_id, DecisionOpportunityRecord.scope_id
            ).where(
                DecisionOpportunityRecord.cohort == "prospective",
                DecisionOpportunityRecord.status == "decided",
                DecisionOpportunityRecord.end_at > now,
                DecisionOpportunityRecord.scope_id.in_(
                    [spot_scope(value).scope_id for value in ("BTC-USDT", "ETH-USDT")]
                ),
            )
            if after is not None:
                statement = statement.where(DecisionOpportunityRecord.opportunity_id > after)
            rows = session.execute(
                statement.order_by(DecisionOpportunityRecord.opportunity_id).limit(65)
            ).all()
        ids, errors, scopes = [], [], set()
        cursor = None
        for index, (opportunity_id, scope) in enumerate(rows[:64]):
            cursor = opportunity_id
            if scope not in scopes and len(scopes) >= 2:
                break
            scopes.add(scope)
            try:
                request = self.stage(opportunity_id)
                if request is not None:
                    ids.append(request)
            except (ValueError, KeyError) as error:
                errors.append({"opportunity_id": opportunity_id, "reason": str(error)[:160]})
            if len(ids) >= max(1, min(max_opportunities, 2)):
                return {
                    "request_ids": ids,
                    "next_cursor": cursor if index + 1 < len(rows) else None,
                    "errors": errors,
                }
        return {
            "request_ids": ids,
            "next_cursor": cursor if len(rows) > 64 else None,
            "errors": errors,
        }


def read_published_branch_in_session(session: Session, revision: ActionPlanRevision, now: datetime):
    return _branch_original(session, revision, now)
