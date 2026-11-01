"""Decision/action identities and append-only evidence; amounts stay in DTO JSON.

Mutable heads and opportunity disposition are separate from immutable originals.
Repositories own reference/hash checks and fenced updates inside the caller's
transaction. SQLite triggers also reject direct UPDATE/DELETE of originals.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class DecisionOpportunityRecord(Base):
    __tablename__ = "decision_opportunities"
    __table_args__ = (
        UniqueConstraint("key", name="uq_decision_opportunity_key"),
        CheckConstraint(
            "status IN ('open','decided','missed','expired')", name="ck_decision_opportunity_status"
        ),
        CheckConstraint(
            "kind IN ('scheduled','event','condition')", name="ck_decision_opportunity_kind"
        ),
        CheckConstraint(
            "cohort IN ('prospective','diagnostic')", name="ck_decision_opportunity_cohort"
        ),
        CheckConstraint("end_at > start_at", name="ck_decision_opportunity_window"),
        Index("ix_decision_opportunity_scope_time", "scope_id", "horizon", "start_at"),
    )
    opportunity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    schema_version: Mapped[str] = mapped_column(
        String(64), default="decision-opportunity.v1", server_default="decision-opportunity.v1"
    )
    key: Mapped[str] = mapped_column(String(256))
    scope_id: Mapped[str] = mapped_column(String(192))
    horizon: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    event_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipe_hash: Mapped[str] = mapped_column(String(64))
    target_hash: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cohort: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DecisionSnapshotRecord(Base):
    __tablename__ = "decision_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id", "source_hash", "recipe_hash", name="uq_decision_snapshot_source"
        ),
        Index("ix_decision_snapshot_run_known", "run_id", "known_at"),
    )
    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str] = mapped_column(String(128))
    source_result_ref: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    recipe_hash: Mapped[str] = mapped_column(String(64))
    known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class ActionPlanRevisionRecord(Base):
    __tablename__ = "action_plan_revisions"
    __table_args__ = (
        UniqueConstraint("plan_id", "revision_no", name="uq_action_plan_revision"),
        CheckConstraint("revision_no >= 1", name="ck_action_revision_positive"),
        CheckConstraint("valid_until > published_at", name="ck_action_revision_validity"),
        Index("ix_action_revision_decision_position", "decision_id", "position_context"),
    )
    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    revision_no: Mapped[int] = mapped_column(Integer)
    decision_id: Mapped[str] = mapped_column(String(128))
    opportunity_id: Mapped[str] = mapped_column(String(128))
    previous_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position_context: Mapped[str] = mapped_column(String(16))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionPlanHeadRecord(Base):
    __tablename__ = "action_plan_heads"
    __table_args__ = (
        CheckConstraint("state_version >= 1", name="ck_action_head_version"),
        CheckConstraint("lease_generation >= 0", name="ck_action_head_generation"),
        CheckConstraint("enter_streak >= 0", name="ck_action_head_streak"),
        CheckConstraint(
            "state IN ('proposed','armed','triggered','completed',"
            "'expired','invalidated','superseded')",
            name="ck_action_head_state",
        ),
        CheckConstraint(
            "monitor_state IN ('active','paused','degraded','stopped')",
            name="ck_action_monitor_state",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_action_head_lease_pair",
        ),
        Index("ix_action_head_due", "monitor_state", "next_due_at", "lease_expires_at"),
    )
    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    current_revision_id: Mapped[str] = mapped_column(String(128))
    state_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    state: Mapped[str] = mapped_column(String(16))
    monitor_state: Mapped[str] = mapped_column(String(16))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_input_watermark: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enter_streak: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ActionConditionEvaluationRecord(Base):
    __tablename__ = "action_condition_evaluations"
    __table_args__ = (
        UniqueConstraint("plan_id", "revision_id", "tick_at", name="uq_action_evaluation_tick"),
        CheckConstraint("lease_generation >= 0", name="ck_action_evaluation_generation"),
        Index("ix_action_evaluation_plan_tick", "plan_id", "tick_at"),
    )
    evaluation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    revision_id: Mapped[str] = mapped_column(String(128))
    tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_generation: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class ActionOutcomeRecord(Base):
    __tablename__ = "action_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "revision_id",
            "mode",
            "protocol_hash",
            "version",
            name="uq_action_outcome_version",
        ),
        CheckConstraint("version >= 1", name="ck_action_outcome_version"),
        CheckConstraint("mode IN ('paper','user_reported')", name="ck_action_outcome_mode"),
        CheckConstraint(
            "status IN ('pending','scorable','unscorable','settled')",
            name="ck_action_outcome_status",
        ),
        Index("ix_action_outcome_due", "status", "due_at"),
    )
    outcome_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(128))
    plan_id: Mapped[str] = mapped_column(String(128))
    revision_id: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(16))
    protocol_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class ActionCommandReceiptRecord(Base):
    __tablename__ = "action_command_receipts"
    __table_args__ = (
        CheckConstraint("length(trim(owner_id)) > 0", name="ck_action_command_owner"),
        CheckConstraint("length(trim(request_id)) > 0", name="ck_action_command_request"),
        Index("ix_action_command_plan_time", "plan_id", "accepted_at"),
    )
    owner_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(32))
    payload_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchSkillUseEventRecord(Base):
    __tablename__ = "research_skill_use_events"
    __table_args__ = (
        UniqueConstraint(
            "use_id", "stage", "node_id", "stage_request_id", name="uq_skill_use_request"
        ),
        CheckConstraint("generation >= 1", name="ck_skill_use_generation"),
        CheckConstraint(
            "stage IN ('selected','loaded','executed','consumed','failed')",
            name="ck_skill_use_stage",
        ),
        Index("ix_skill_use_run", "run_id", "generation", "first_known_at"),
        Index("ix_skill_use_version", "skill_version_id", "first_known_at"),
    )
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    use_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str] = mapped_column(String(128))
    generation: Mapped[int] = mapped_column(Integer)
    root_session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    execution_session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    skill_version_id: Mapped[str] = mapped_column(String(128))
    package_hash: Mapped[str] = mapped_column(String(64))
    workflow_hash: Mapped[str] = mapped_column(String(64))
    method_binding_hash: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(16))
    node_id: Mapped[str] = mapped_column(String(128), default="", server_default="")
    stage_request_id: Mapped[str] = mapped_column(String(128))
    parent_event_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_ref: Mapped[str] = mapped_column(Text)
    original_hash: Mapped[str] = mapped_column(String(64))
    first_known_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))


class ExperienceCaseSkillLinkRecord(Base):
    __tablename__ = "experience_case_skill_links"
    __table_args__ = (
        UniqueConstraint(
            "experience_hash",
            "case_hash",
            "candidate_skill_hash",
            "bridge_protocol_hash",
            name="uq_experience_case_skill_bridge",
        ),
        Index("ix_experience_case_skill_scope_known", "scope_id", "available_at"),
    )
    link_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(192))
    experience_id: Mapped[str] = mapped_column(String(128))
    experience_hash: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str] = mapped_column(String(128))
    case_hash: Mapped[str] = mapped_column(String(64))
    candidate_skill_version_id: Mapped[str] = mapped_column(String(128))
    candidate_skill_hash: Mapped[str] = mapped_column(String(64))
    bridge_protocol_hash: Mapped[str] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    disposition: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


APPEND_ONLY_TABLES = (
    DecisionSnapshotRecord.__table__,
    ActionPlanRevisionRecord.__table__,
    ActionConditionEvaluationRecord.__table__,
    ActionOutcomeRecord.__table__,
    ActionCommandReceiptRecord.__table__,
    ResearchSkillUseEventRecord.__table__,
    ExperienceCaseSkillLinkRecord.__table__,
)
for _table in APPEND_ONLY_TABLES:
    # SQLite REPLACE may delete conflicts without firing DELETE triggers when
    # recursive_triggers is off. Reject conflicting identities before INSERT.
    _keys = [tuple(column.name for column in _table.primary_key.columns)]
    _keys.extend(
        tuple(column.name for column in constraint.columns)
        for constraint in _table.constraints
        if isinstance(constraint, UniqueConstraint)
    )
    _conflicts = " OR ".join(
        "(" + " AND ".join(f"{name} = NEW.{name}" for name in key) + ")" for key in _keys
    )
    sa_event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER trg_{_table.name}_immutable_insert BEFORE INSERT ON {_table.name} "
            f"WHEN EXISTS (SELECT 1 FROM {_table.name} WHERE {_conflicts}) "
            f"BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: {_table.name}'); END"
        ).execute_if(dialect="sqlite"),
    )
    for _operation in ("UPDATE", "DELETE"):
        _trigger_name = f"trg_{_table.name}_immutable_{_operation.lower()}"
        sa_event.listen(
            _table,
            "after_create",
            DDL(
                f"CREATE TRIGGER {_trigger_name} BEFORE {_operation} ON {_table.name} "
                f"BEGIN SELECT RAISE(ABORT, '{_table.name}_immutable'); END"
            ).execute_if(dialect="sqlite"),
        )
