"""Caller-owned Action persistence; DTO integrity is not source authentication.

Writes require an existing physical writer transaction. This repository never
starts/commits/rolls back a transaction, opens another session, or calls a provider.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ..ports.action_monitor import ConditionEvaluationV2
from ..ports.action_plan import (
    ActionPlanHead,
    ActionPlanRevision,
    ConditionEvaluation,
    DecisionSnapshot,
    Opportunity,
    parse_action_plan_revision,
    parse_decision_snapshot,
    parse_opportunity,
)
from ..ports.market_path import aware_utc, canonical_hash
from .action_records import (
    ActionCommandReceiptRecord,
    ActionConditionEvaluationRecord,
    ActionPlanHeadRecord,
    ActionPlanRevisionRecord,
    DecisionOpportunityRecord,
    DecisionSnapshotRecord,
)
from .db import as_utc

_HASH = re.compile(r"^[a-f0-9]{64}$")
_TERMINAL = {"completed", "expired", "invalidated", "superseded"}


class ActionRepositoryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise ActionRepositoryError(code)


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _validated[Model: BaseModel](model: type[Model], value: Model) -> Model:
    # A frozen model can still be manufactured by model_construct/model_copy.
    return model.model_validate(value.model_dump(mode="python"))


def _original[Model: BaseModel](model: type[Model], value: Model, hash_field: str) -> Model:
    value = _validated(model, value)
    digest = canonical_hash(value.model_dump(mode="json", exclude={hash_field}))
    if getattr(value, hash_field) != digest:
        _fail("action_original_hash_mismatch")
    return value


def _parse[Model: BaseModel](model: type[Model], payload: str, hash_field: str) -> Model:
    try:
        return _original(model, model.model_validate_json(payload), hash_field)
    except (ValidationError, TypeError, json.JSONDecodeError) as exc:
        raise ActionRepositoryError("action_original_shape_invalid") from exc


def _versioned(parser, value, hash_field: str | None = None):
    # Route by the actual explicit wire version, never horizon/scope guessing.
    body = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    try:
        parsed = parser(body)
    except (ValueError, TypeError) as exc:
        raise ActionRepositoryError("action_original_shape_invalid") from exc
    if hash_field is not None and getattr(parsed, hash_field) != canonical_hash(
        parsed.model_dump(mode="json", exclude={hash_field})
    ):
        _fail("action_original_hash_mismatch")
    return parsed


def _same(actual: object, expected: object) -> None:
    if actual != expected:
        _fail("action_record_binding_mismatch")


def _identifier(value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or value.strip() != value:
        _fail("action_identity_invalid")


@dataclass(frozen=True)
class StoredCommandReceipt:
    owner_id: str
    request_id: str
    plan_id: str
    operation: str
    payload_hash: str
    response: dict[str, Any]
    accepted_at: datetime
    receipt_hash: str

    def body(self) -> dict[str, Any]:
        return {
            "schema_version": "action-command-receipt.v1",
            "owner_id": self.owner_id,
            "request_id": self.request_id,
            "plan_id": self.plan_id,
            "operation": self.operation,
            "payload_hash": self.payload_hash,
            "response": self.response,
            "accepted_at": aware_utc(self.accepted_at).isoformat(),
        }

    def wire(self) -> dict[str, Any]:
        return {**self.body(), "receipt_hash": self.receipt_hash}


@dataclass(frozen=True)
class ActionMonitorClaim:
    """Internal claim identity; the current writer must verify every fence."""

    head: ActionPlanHead
    revision: ActionPlanRevision
    owner: str
    generation: int
    tick_at: datetime
    as_of: datetime


def make_command_receipt(
    *,
    owner_id: str,
    request_id: str,
    plan_id: str,
    operation: str,
    payload_hash: str,
    response: dict[str, Any],
    accepted_at: datetime,
) -> StoredCommandReceipt:
    for value in (owner_id, request_id, plan_id):
        _identifier(value)
    if (
        not isinstance(operation, str)  # pyright: ignore[reportUnnecessaryIsInstance]
        or not operation
        or len(operation) > 32
        or operation.strip() != operation
        or not isinstance(payload_hash, str)  # pyright: ignore[reportUnnecessaryIsInstance]
        or not _HASH.fullmatch(payload_hash)
    ):
        _fail("action_command_shape_invalid")
    if not isinstance(response, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        _fail("action_command_response_invalid")
    frozen = json.loads(_json(response))
    draft = StoredCommandReceipt(
        owner_id,
        request_id,
        plan_id,
        operation,
        payload_hash,
        frozen,
        aware_utc(accepted_at),
        "",
    )
    return StoredCommandReceipt(
        owner_id,
        request_id,
        plan_id,
        operation,
        payload_hash,
        frozen,
        draft.accepted_at,
        canonical_hash(draft.body()),
    )


class ActionRepository:
    def __init__(self, session: Session):
        self.session = session

    def _writer(self) -> None:
        connection = self.session.connection()
        driver = connection.connection.driver_connection
        if connection.dialect.name == "sqlite" and (
            not isinstance(driver, sqlite3.Connection) or not driver.in_transaction
        ):
            _fail("action_writer_transaction_required")
        if not self.session.in_transaction():
            _fail("action_writer_transaction_required")

    @staticmethod
    def _opportunity(row: DecisionOpportunityRecord) -> Opportunity:
        try:
            return parse_opportunity(
                {
                    "schema_version": row.schema_version,
                    "id": row.opportunity_id,
                    "key": row.key,
                    "scope_id": row.scope_id,
                    "horizon": row.horizon,
                    "kind": row.kind,
                    "event_ref": row.event_ref,
                    "registered_at": as_utc(row.registered_at),
                    "time_start": as_utc(row.start_at),
                    "time_end": as_utc(row.end_at),
                    "target_spec_hash": row.target_hash,
                    "recipe_hash": row.recipe_hash,
                    "cohort": row.cohort,
                    "status": row.status,
                    "reason": row.reason,
                }
            )
        except ValidationError as exc:
            raise ActionRepositoryError("action_record_binding_mismatch") from exc

    def get_opportunity(self, opportunity_id: str) -> Opportunity | None:
        row = self.session.get(DecisionOpportunityRecord, opportunity_id, populate_existing=True)
        return self._opportunity(row) if row is not None else None

    def get_opportunity_by_key(self, key: str) -> Opportunity | None:
        row = self.session.scalar(
            select(DecisionOpportunityRecord)
            .where(
                DecisionOpportunityRecord.key == key,
            )
            .execution_options(populate_existing=True)
        )
        return self._opportunity(row) if row is not None else None

    def _required_opportunity(self, opportunity_id: str) -> Opportunity:
        value = self.get_opportunity(opportunity_id)
        if value is None:
            _fail("action_reference_not_found")
        return value

    def register_opportunity(self, value: Opportunity) -> Opportunity:
        self._writer()
        value = _versioned(parse_opportunity, value)
        by_id, by_key = self.get_opportunity(value.id), self.get_opportunity_by_key(value.key)
        for existing in (by_id, by_key):
            if existing is not None and existing.model_dump(
                exclude={"status", "reason"}
            ) != value.model_dump(exclude={"status", "reason"}):
                _fail("action_opportunity_key_conflict")
        if by_id is not None:
            return by_id
        if by_key is not None:
            return by_key
        if value.status != "open" or value.reason is not None:
            _fail("action_opportunity_initial_status_invalid")
        self.session.add(
            DecisionOpportunityRecord(
                opportunity_id=value.id,
                schema_version=value.schema_version,
                key=value.key,
                scope_id=value.scope_id,
                horizon=value.horizon,
                kind=value.kind,
                event_ref=value.event_ref,
                recipe_hash=value.recipe_hash,
                target_hash=value.target_spec_hash,
                registered_at=value.registered_at,
                start_at=value.time_start,
                end_at=value.time_end,
                cohort=value.cohort,
                status=value.status,
                reason=value.reason,
            )
        )
        self.session.flush()
        return value

    def set_opportunity_status(
        self,
        opportunity_id: str,
        *,
        expected_status: str,
        status: str,
        reason: str | None = None,
    ) -> Opportunity:
        self._writer()
        old = self._required_opportunity(opportunity_id)
        if old.status == status and old.reason == reason:
            return old
        if old.status != expected_status:
            _fail("action_opportunity_status_conflict")
        if old.status != "open" or status not in {"decided", "missed", "expired"}:
            _fail("action_opportunity_transition_invalid")
        value = parse_opportunity(
            old.model_dump(mode="python") | {"status": status, "reason": reason}
        )
        result = self.session.execute(
            update(DecisionOpportunityRecord)
            .where(
                DecisionOpportunityRecord.opportunity_id == opportunity_id,
                DecisionOpportunityRecord.status == expected_status,
            )
            .values(status=status, reason=reason)
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            _fail("action_opportunity_status_conflict")
        return value

    def _snapshot_binding(self, value: DecisionSnapshot, opportunity: Opportunity) -> None:
        if (value.opportunity_id, value.scope_id, value.horizon) != (
            opportunity.id,
            opportunity.scope_id,
            opportunity.horizon,
        ):
            _fail("action_scope_horizon_mismatch")
        if not opportunity.time_start <= value.decision_cutoff_at < opportunity.time_end:
            _fail("action_decision_cutoff_outside_opportunity")
        if value.schema_version == "decision-snapshot.v2" and (
            opportunity.schema_version != "decision-opportunity.v2"
            or value.bindings.candidate_target.hash != opportunity.target_spec_hash
        ):
            _fail("action_target_recipe_mismatch")

    def get_snapshot(self, decision_id: str) -> DecisionSnapshot | None:
        row = self.session.get(DecisionSnapshotRecord, decision_id, populate_existing=True)
        if row is None:
            return None
        value = _versioned(parse_decision_snapshot, json.loads(row.payload_json), "snapshot_hash")
        _same(
            (
                row.decision_id,
                row.opportunity_id,
                row.run_id,
                row.source_result_ref,
                row.source_hash,
                as_utc(row.known_at),
                row.payload_hash,
            ),
            (
                value.id,
                value.opportunity_id,
                value.run_id,
                value.source_result_ref,
                value.source_result_hash,
                value.known_at,
                value.snapshot_hash,
            ),
        )
        opportunity = self._required_opportunity(value.opportunity_id)
        _same(row.recipe_hash, opportunity.recipe_hash)
        self._snapshot_binding(value, opportunity)
        return value

    def append_snapshot(self, value: DecisionSnapshot) -> DecisionSnapshot:
        self._writer()
        value = _versioned(parse_decision_snapshot, value, "snapshot_hash")
        opportunity = self._required_opportunity(value.opportunity_id)
        self._snapshot_binding(value, opportunity)
        old = self.get_snapshot(value.id)
        by_source = self.session.scalar(
            select(DecisionSnapshotRecord.decision_id).where(
                DecisionSnapshotRecord.opportunity_id == value.opportunity_id,
                DecisionSnapshotRecord.source_hash == value.source_result_hash,
                DecisionSnapshotRecord.recipe_hash == opportunity.recipe_hash,
            )
        )
        if by_source is not None and by_source != value.id:
            _fail("action_identity_conflict")
        if old is not None:
            if old != value:
                _fail("action_identity_conflict")
            return old
        self.session.add(
            DecisionSnapshotRecord(
                decision_id=value.id,
                opportunity_id=value.opportunity_id,
                run_id=value.run_id,
                source_result_ref=value.source_result_ref,
                source_hash=value.source_result_hash,
                recipe_hash=opportunity.recipe_hash,
                known_at=value.known_at,
                payload_json=_json(value.model_dump(mode="json")),
                payload_hash=value.snapshot_hash,
            )
        )
        self.session.flush()
        return value

    @staticmethod
    def _revision(row: ActionPlanRevisionRecord) -> ActionPlanRevision:
        value = _versioned(parse_action_plan_revision, json.loads(row.payload_json), "payload_hash")
        _same(
            (
                row.revision_id,
                row.plan_id,
                row.revision_no,
                row.decision_id,
                row.opportunity_id,
                row.previous_id,
                row.position_context,
                row.payload_hash,
                as_utc(row.published_at),
                as_utc(row.valid_until),
            ),
            (
                value.revision_id,
                value.plan_id,
                value.revision_no,
                value.decision_id,
                value.opportunity_id,
                value.previous_id,
                value.position_context,
                value.payload_hash,
                value.published_at,
                value.valid_until,
            ),
        )
        return value

    def _revision_binding(self, value: ActionPlanRevision) -> None:
        snapshot = self.get_snapshot(value.decision_id)
        if snapshot is None:
            _fail("action_reference_not_found")
        opportunity = self._required_opportunity(value.opportunity_id)
        if (value.opportunity_id, value.scope_id, value.horizon) != (
            snapshot.opportunity_id,
            snapshot.scope_id,
            snapshot.horizon,
        ):
            _fail("action_scope_horizon_mismatch")
        if value.schema_version == "decision-action-plan-revision.v2":
            if (
                snapshot.schema_version != "decision-snapshot.v2"
                or opportunity.schema_version != "decision-opportunity.v2"
                or value.bindings.candidate_target.hash != opportunity.target_spec_hash
                or value.target_spec_hash != value.bindings.final_target.hash
                or value.target_spec_ref != value.bindings.final_target.ref
                or value.context != snapshot.context
                or value.bindings != snapshot.bindings
            ):
                _fail("action_target_recipe_mismatch")
        elif value.target_spec_hash != opportunity.target_spec_hash:
            _fail("action_target_recipe_mismatch")
        if (
            value.as_of != snapshot.decision_cutoff_at
            or value.published_at < snapshot.known_at
            or value.published_at < opportunity.registered_at
            or value.valid_until > opportunity.time_end
        ):
            _fail("action_revision_time_binding_invalid")
        another = self.session.scalar(
            select(ActionPlanRevisionRecord.plan_id)
            .where(
                ActionPlanRevisionRecord.decision_id == value.decision_id,
                ActionPlanRevisionRecord.position_context == value.position_context,
                ActionPlanRevisionRecord.plan_id != value.plan_id,
            )
            .limit(1)
        )
        if another is not None:
            _fail("action_position_branch_conflict")

    def _predecessor(self, value: ActionPlanRevision) -> None:
        if value.previous_id is None:
            return
        row = self.session.get(ActionPlanRevisionRecord, value.previous_id, populate_existing=True)
        if row is None:
            _fail("action_reference_not_found")
        previous = self._revision(row)
        if (
            previous.plan_id,
            previous.opportunity_id,
            previous.scope_id,
            previous.horizon,
            previous.position_context,
            previous.revision_no + 1,
        ) != (
            value.plan_id,
            value.opportunity_id,
            value.scope_id,
            value.horizon,
            value.position_context,
            value.revision_no,
        ):
            _fail("action_revision_predecessor_mismatch")
        if value.as_of < previous.as_of or value.published_at < previous.published_at:
            _fail("action_revision_time_regressed")

    def get_revision(self, revision_id: str) -> ActionPlanRevision | None:
        row = self.session.get(ActionPlanRevisionRecord, revision_id, populate_existing=True)
        if row is None:
            return None
        value = self._revision(row)
        self._revision_binding(value)
        self._predecessor(value)
        return value

    @staticmethod
    def _head_values(value: ActionPlanHead) -> dict[str, Any]:
        result = value.model_dump(mode="python", exclude={"schema_version"})
        result["next_due_at"] = result.pop("next_due")
        return result

    @staticmethod
    def _head_binding(head: ActionPlanHead, revision: ActionPlanRevision) -> None:
        if (head.plan_id, head.current_revision_id, head.valid_until) != (
            revision.plan_id,
            revision.revision_id,
            revision.valid_until,
        ):
            _fail("action_record_binding_mismatch")
        if (head.lease_owner is None) != (head.lease_expires_at is None):
            _fail("action_head_lease_pair_invalid")
        if head.state in _TERMINAL and head.monitor_state != "stopped":
            _fail("action_head_terminal_monitor_invalid")

    def get_head(self, plan_id: str) -> ActionPlanHead | None:
        row = self.session.get(ActionPlanHeadRecord, plan_id, populate_existing=True)
        if row is None:
            return None
        data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        for name in ("next_due_at", "valid_until", "lease_expires_at", "last_input_watermark"):
            data[name] = as_utc(data[name])
        data["schema_version"] = "decision-action-plan-head.v1"
        data["next_due"] = data.pop("next_due_at")
        try:
            value = ActionPlanHead.model_validate(data)
        except ValidationError as exc:
            raise ActionRepositoryError("action_record_binding_mismatch") from exc
        revision = self.get_revision(value.current_revision_id)
        if revision is None:
            _fail("action_reference_not_found")
        self._head_binding(value, revision)
        return value

    def _insert_revision(self, value: ActionPlanRevision) -> None:
        self.session.add(
            ActionPlanRevisionRecord(
                revision_id=value.revision_id,
                plan_id=value.plan_id,
                revision_no=value.revision_no,
                decision_id=value.decision_id,
                opportunity_id=value.opportunity_id,
                previous_id=value.previous_id,
                position_context=value.position_context,
                published_at=value.published_at,
                valid_until=value.valid_until,
                payload_json=_json(value.model_dump(mode="json")),
                payload_hash=value.payload_hash,
            )
        )
        self.session.flush()

    def create_plan(self, revision: ActionPlanRevision, *, head: ActionPlanHead) -> ActionPlanHead:
        self._writer()
        revision = _versioned(parse_action_plan_revision, revision, "payload_hash")
        head = _validated(ActionPlanHead, head)
        self._revision_binding(revision)
        self._head_binding(head, revision)
        existing_revision = self.get_revision(revision.revision_id)
        existing_head = self.get_head(head.plan_id)
        if existing_revision is not None or existing_head is not None:
            if existing_revision == revision and existing_head is not None:
                return existing_head
            _fail("action_identity_conflict")
        if (
            revision.revision_no != 1
            or revision.previous_id is not None
            or head.state_version != 1
            or head.lease_generation != 0
            or head.lease_owner is not None
            or head.enter_streak != 0
            or head.last_input_watermark is not None
            or head.state not in {"proposed", "armed"}
        ):
            _fail("action_plan_initial_state_invalid")
        self._insert_revision(revision)
        self.session.add(ActionPlanHeadRecord(**self._head_values(head)))
        self.session.flush()
        return head

    def _expected_head(self, plan_id: str, revision_id: str, version: int) -> ActionPlanHead:
        old = self.get_head(plan_id)
        if old is None:
            _fail("action_plan_not_found")
        if old.current_revision_id != revision_id:
            _fail("action_revision_superseded")
        if old.state_version != version:
            _fail("action_state_version_conflict")
        if old.state in _TERMINAL:
            _fail("action_terminal_plan_restart_forbidden")
        return old

    @staticmethod
    def _replacement_head(
        old: ActionPlanHead,
        new: ActionPlanHead,
        *,
        reset_watermark: bool = True,
    ) -> None:
        if (
            new.plan_id != old.plan_id
            or new.state_version != old.state_version + 1
            or new.lease_generation != old.lease_generation + 1
            or new.lease_owner is not None
            or new.lease_expires_at is not None
            or new.enter_streak != 0
            or new.last_input_watermark != (None if reset_watermark else old.last_input_watermark)
            or new.state != old.state
        ):
            _fail("action_head_replacement_invalid")

    def _cas_head(self, old: ActionPlanHead, new: ActionPlanHead) -> None:
        result = self.session.execute(
            update(ActionPlanHeadRecord)
            .where(
                ActionPlanHeadRecord.plan_id == old.plan_id,
                ActionPlanHeadRecord.current_revision_id == old.current_revision_id,
                ActionPlanHeadRecord.state_version == old.state_version,
                ActionPlanHeadRecord.lease_generation == old.lease_generation,
            )
            .values(**self._head_values(new))
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            _fail("action_state_version_conflict")

    def append_revision(
        self,
        revision: ActionPlanRevision,
        *,
        expected_revision_id: str,
        expected_state_version: int,
        next_head: ActionPlanHead,
    ) -> ActionPlanHead:
        self._writer()
        revision = _versioned(parse_action_plan_revision, revision, "payload_hash")
        next_head = _validated(ActionPlanHead, next_head)
        existing = self.get_revision(revision.revision_id)
        if existing is not None:
            if existing != revision:
                _fail("action_identity_conflict")
            head = self.get_head(revision.plan_id)
            if head is None:
                _fail("action_reference_not_found")
            return head
        old = self._expected_head(revision.plan_id, expected_revision_id, expected_state_version)
        if revision.previous_id != expected_revision_id:
            _fail("action_revision_predecessor_mismatch")
        self._predecessor(revision)
        self._revision_binding(revision)
        self._head_binding(next_head, revision)
        self._replacement_head(old, next_head)
        self._insert_revision(revision)
        self._cas_head(old, next_head)
        return next_head

    def change_head(
        self,
        plan_id: str,
        *,
        expected_revision_id: str,
        expected_state_version: int,
        next_head: ActionPlanHead,
    ) -> ActionPlanHead:
        self._writer()
        next_head = _validated(ActionPlanHead, next_head)
        old = self._expected_head(plan_id, expected_revision_id, expected_state_version)
        self._replacement_head(old, next_head, reset_watermark=False)
        if (next_head.current_revision_id, next_head.valid_until) != (
            old.current_revision_id,
            old.valid_until,
        ):
            _fail("action_revision_superseded")
        if next_head.monitor_state not in {"active", "paused"}:
            _fail("action_monitor_command_invalid")
        self._cas_head(old, next_head)
        return next_head

    @staticmethod
    def _monitor_due(now: datetime):
        row = ActionPlanHeadRecord
        return and_(
            row.state.in_(("proposed", "armed", "triggered")),
            or_(row.lease_expires_at.is_(None), row.lease_expires_at <= now),
            or_(
                row.valid_until <= now,
                and_(
                    row.state.in_(("armed", "triggered")),
                    row.monitor_state.in_(("active", "degraded")),
                    row.next_due_at <= now,
                ),
            ),
        )

    def list_due_monitor_plan_ids(
        self,
        *,
        now: datetime,
        max_opportunities: int = 4,
        max_scopes: int = 2,
    ) -> tuple[str, ...]:
        """At most four opportunities / twelve position branches, oldest first."""
        now = aware_utc(now)
        if (
            type(max_opportunities) is not int
            or not 1 <= max_opportunities <= 4
            or type(max_scopes) is not int
            or not 1 <= max_scopes <= 2
        ):
            _fail("action_monitor_batch_invalid")
        head, revision, opportunity = (
            ActionPlanHeadRecord,
            ActionPlanRevisionRecord,
            DecisionOpportunityRecord,
        )
        oldest = func.min(func.coalesce(head.next_due_at, head.valid_until))
        groups = self.session.execute(
            select(opportunity.opportunity_id, opportunity.scope_id, oldest.label("oldest"))
            .join(revision, revision.opportunity_id == opportunity.opportunity_id)
            .join(head, head.current_revision_id == revision.revision_id)
            .where(
                func.json_extract(revision.payload_json, "$.schema_version")
                == "decision-action-plan-revision.v1",
                self._monitor_due(now),
            )
            .group_by(opportunity.opportunity_id, opportunity.scope_id)
            .order_by(oldest, opportunity.opportunity_id)
            .limit(64)
        ).all()
        scopes: set[str] = set()
        selected: list[str] = []
        for group in groups:
            if group.scope_id not in scopes and len(scopes) >= max_scopes:
                continue
            scopes.add(group.scope_id)
            selected.append(group.opportunity_id)
            if len(selected) >= max_opportunities:
                break
        result: list[str] = []
        for opportunity_id in selected:
            ids = self.session.scalars(
                select(head.plan_id)
                .join(revision, head.current_revision_id == revision.revision_id)
                .where(
                    revision.opportunity_id == opportunity_id,
                    func.json_extract(revision.payload_json, "$.schema_version")
                    == "decision-action-plan-revision.v1",
                    self._monitor_due(now),
                )
                .order_by(func.coalesce(head.next_due_at, head.valid_until), head.plan_id)
                .limit(3)
            ).all()
            result.extend(ids)
        return tuple(result)

    def claim_monitor(
        self,
        plan_id: str,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int = 90,
        as_of: datetime | None = None,
    ) -> ActionMonitorClaim | None:
        self._writer()
        _identifier(owner)
        now = aware_utc(now)
        cutoff = aware_utc(as_of) if as_of is not None else now
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300 or cutoff > now:
            _fail("action_monitor_claim_invalid")
        legacy_revision = self.session.scalar(
            select(ActionPlanRevisionRecord.revision_id)
            .join(
                ActionPlanHeadRecord,
                ActionPlanHeadRecord.current_revision_id == ActionPlanRevisionRecord.revision_id,
            )
            .where(
                ActionPlanHeadRecord.plan_id == plan_id,
                func.json_extract(ActionPlanRevisionRecord.payload_json, "$.schema_version")
                == "decision-action-plan-revision.v1",
            )
        )
        if legacy_revision is None:
            return None
        old = self.get_head(plan_id)
        if old is None or old.state in _TERMINAL:
            return None
        if old.lease_expires_at is not None and old.lease_expires_at > now:
            return None
        expired = now >= old.valid_until
        if not expired and (
            old.state not in {"armed", "triggered"}
            or old.monitor_state not in {"active", "degraded"}
            or old.next_due is None
            or old.next_due > now
        ):
            return None
        revision = self.get_revision(old.current_revision_id)
        if (
            revision is None
            or revision.schema_version != "decision-action-plan-revision.v1"
            or revision.published_at > cutoff
        ):
            return None
        new = _validated(
            ActionPlanHead,
            old.model_copy(
                update={
                    "lease_owner": owner,
                    "lease_generation": old.lease_generation + 1,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                }
            ),
        )
        self._cas_head(old, new)
        return ActionMonitorClaim(
            new,
            revision,
            owner,
            new.lease_generation,
            old.next_due or old.valid_until,
            cutoff,
        )

    def _evaluation(
        self,
        row: ActionConditionEvaluationRecord,
    ) -> ConditionEvaluation | ConditionEvaluationV2:
        try:
            schema = json.loads(row.payload_json)["schema_version"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionRepositoryError("action_original_shape_invalid") from exc
        if schema == "decision-condition-evaluation.v1":
            value = _parse(ConditionEvaluation, row.payload_json, "evaluation_hash")
        elif schema == "decision-condition-evaluation.v2":
            value = _parse(ConditionEvaluationV2, row.payload_json, "evaluation_hash")
        else:
            _fail("action_evaluation_schema_unsupported")
        _same(
            (
                row.evaluation_id,
                row.plan_id,
                row.revision_id,
                as_utc(row.tick_at),
                row.lease_generation,
                row.input_hash,
                row.payload_hash,
            ),
            (
                value.id,
                value.plan_id,
                value.revision_id,
                value.tick_at,
                value.lease_generation,
                value.input_hash,
                value.evaluation_hash,
            ),
        )
        revision = self.get_revision(value.revision_id)
        if revision is None or revision.plan_id != value.plan_id:
            _fail("action_evaluation_revision_mismatch")
        if isinstance(value, ConditionEvaluationV2):
            conditions = {
                c.id: c for c in (*revision.entry, *revision.exit, *revision.invalidation)
            }
            if len(value.per_condition) != len(conditions) or {
                c.id for c in value.per_condition
            } != set(conditions):
                _fail("action_evaluation_conditions_mismatch")
            for item in value.per_condition:
                required = conditions[item.id].min_fresh_samples
                if item.confirmation_count > required or item.confirmed != (
                    item.raw_truth == "true"
                    and item.status == "ok"
                    and item.confirmation_count >= required
                ):
                    _fail("action_evaluation_confirmation_invalid")
        return value

    def get_latest_evaluation(
        self,
        plan_id: str,
        *,
        revision_id: str,
    ) -> ConditionEvaluation | ConditionEvaluationV2 | None:
        row = self.session.scalar(
            select(ActionConditionEvaluationRecord)
            .where(
                ActionConditionEvaluationRecord.plan_id == plan_id,
                ActionConditionEvaluationRecord.revision_id == revision_id,
            )
            .order_by(
                ActionConditionEvaluationRecord.tick_at.desc(),
                ActionConditionEvaluationRecord.evaluation_id.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return self._evaluation(row) if row is not None else None

    def get_evaluation_for_tick(
        self,
        plan_id: str,
        *,
        revision_id: str,
        tick_at: datetime,
    ) -> ConditionEvaluation | ConditionEvaluationV2 | None:
        row = self.session.scalar(
            select(ActionConditionEvaluationRecord)
            .where(
                ActionConditionEvaluationRecord.plan_id == plan_id,
                ActionConditionEvaluationRecord.revision_id == revision_id,
                ActionConditionEvaluationRecord.tick_at == aware_utc(tick_at),
            )
            .execution_options(populate_existing=True)
        )
        return self._evaluation(row) if row is not None else None

    def require_monitor_claim(self, claim: ActionMonitorClaim, *, now: datetime) -> ActionPlanHead:
        self._writer()
        old = self.get_head(claim.head.plan_id)
        now = aware_utc(now)
        if (
            old is None
            or old != claim.head
            or old.lease_owner != claim.owner
            or old.lease_generation != claim.generation
            or old.lease_expires_at is None
            or old.lease_expires_at <= now
            or old.state in _TERMINAL
        ):
            _fail("action_monitor_fence_lost")
        actual = self.get_revision(old.current_revision_id)
        if actual != claim.revision:
            _fail("action_monitor_revision_changed")
        return old

    def complete_monitor(
        self,
        claim: ActionMonitorClaim,
        *,
        evaluation: ConditionEvaluationV2,
        next_head: ActionPlanHead,
        now: datetime,
    ) -> ConditionEvaluationV2:
        self._writer()
        now = aware_utc(now)
        value = _original(ConditionEvaluationV2, evaluation, "evaluation_hash")
        head = _validated(ActionPlanHead, next_head)
        existing = self.get_evaluation_for_tick(
            value.plan_id,
            revision_id=value.revision_id,
            tick_at=value.tick_at,
        )
        if existing is not None:
            if existing != value:
                _fail("action_monitor_tick_conflict")
            return value
        old = self.require_monitor_claim(claim, now=now)
        if (
            (
                value.plan_id,
                value.revision_id,
                value.tick_at,
                value.as_of,
                value.lease_generation,
                value.state_version_before,
                value.state_version_after,
                value.state_before,
                value.state_after,
                value.monitor_state_before,
                value.monitor_state_after,
                value.evaluated_at,
            )
            != (
                old.plan_id,
                old.current_revision_id,
                claim.tick_at,
                claim.as_of,
                claim.generation,
                old.state_version,
                old.state_version + 1,
                old.state,
                head.state,
                old.monitor_state,
                head.monitor_state,
                now,
            )
            or head.plan_id != old.plan_id
            or head.current_revision_id != old.current_revision_id
            or head.valid_until != old.valid_until
            or head.state_version != old.state_version + 1
            or head.lease_generation != old.lease_generation
            or head.lease_owner is not None
            or head.lease_expires_at is not None
            or head.enter_streak != 0
            or (
                old.last_input_watermark is not None
                and (
                    head.last_input_watermark is None
                    or head.last_input_watermark < old.last_input_watermark
                )
            )
            or (head.last_input_watermark is not None and head.last_input_watermark > claim.as_of)
        ):
            _fail("action_monitor_completion_binding_invalid")
        if now >= old.valid_until:
            if head.state != ("completed" if old.state == "triggered" else "expired"):
                _fail("action_monitor_expiry_required")
        elif head.state != old.state and (old.state, head.state) not in {
            ("armed", "triggered"),
            ("armed", "invalidated"),
            ("triggered", "completed"),
            ("triggered", "invalidated"),
        }:
            _fail("action_monitor_transition_invalid")
        if head.state in _TERMINAL:
            if head.monitor_state != "stopped" or head.next_due is not None:
                _fail("action_monitor_terminal_due_invalid")
        elif (
            head.monitor_state not in {"active", "degraded"}
            or head.next_due is None
            or head.next_due <= now
        ):
            _fail("action_monitor_next_due_invalid")
        self._head_binding(head, claim.revision)
        record = ActionConditionEvaluationRecord(
            evaluation_id=value.id,
            plan_id=value.plan_id,
            revision_id=value.revision_id,
            tick_at=value.tick_at,
            lease_generation=value.lease_generation,
            input_hash=value.input_hash,
            payload_json=_json(value.model_dump(mode="json")),
            payload_hash=value.evaluation_hash,
        )
        self._evaluation(record)
        self.session.add(record)
        self.session.flush()
        self._cas_head(old, head)
        return value

    def get_command(self, owner_id: str, request_id: str) -> StoredCommandReceipt | None:
        _identifier(owner_id)
        _identifier(request_id)
        row = self.session.get(
            ActionCommandReceiptRecord, (owner_id, request_id), populate_existing=True
        )
        if row is None:
            return None
        try:
            body = json.loads(row.response_json)
            if (
                set(body)
                != {
                    "schema_version",
                    "owner_id",
                    "request_id",
                    "plan_id",
                    "operation",
                    "payload_hash",
                    "response",
                    "accepted_at",
                    "receipt_hash",
                }
                or body["schema_version"] != "action-command-receipt.v1"
            ):
                _fail("action_command_response_invalid")
            value = make_command_receipt(
                owner_id=body["owner_id"],
                request_id=body["request_id"],
                plan_id=body["plan_id"],
                operation=body["operation"],
                payload_hash=body["payload_hash"],
                response=body["response"],
                accepted_at=datetime.fromisoformat(body["accepted_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionRepositoryError("action_command_response_invalid") from exc
        _same(value.receipt_hash, body["receipt_hash"])
        _same(
            (
                row.owner_id,
                row.request_id,
                row.plan_id,
                row.operation,
                row.payload_hash,
                as_utc(row.accepted_at),
            ),
            (
                value.owner_id,
                value.request_id,
                value.plan_id,
                value.operation,
                value.payload_hash,
                value.accepted_at,
            ),
        )
        return value

    def append_command_receipt(self, receipt: StoredCommandReceipt) -> StoredCommandReceipt:
        self._writer()
        checked = make_command_receipt(
            owner_id=receipt.owner_id,
            request_id=receipt.request_id,
            plan_id=receipt.plan_id,
            operation=receipt.operation,
            payload_hash=receipt.payload_hash,
            response=receipt.response,
            accepted_at=receipt.accepted_at,
        )
        _same(checked.receipt_hash, receipt.receipt_hash)
        old = self.get_command(receipt.owner_id, receipt.request_id)
        if old is not None:
            if (old.plan_id, old.operation, old.payload_hash) != (
                receipt.plan_id,
                receipt.operation,
                receipt.payload_hash,
            ):
                _fail("action_command_key_conflict")
            return old
        if self.get_head(receipt.plan_id) is None:
            _fail("action_plan_not_found")
        self.session.add(
            ActionCommandReceiptRecord(
                owner_id=receipt.owner_id,
                request_id=receipt.request_id,
                plan_id=receipt.plan_id,
                operation=receipt.operation,
                payload_hash=receipt.payload_hash,
                response_json=_json(receipt.wire()),
                accepted_at=receipt.accepted_at,
            )
        )
        self.session.flush()
        return receipt
