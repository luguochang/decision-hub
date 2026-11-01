"""Decision/action evidence and skill indexes; preserve every legacy outbox row.

Frozen standalone DDL. Never import current Base/ORM metadata in this revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "0093_decision_product_v1"
down_revision = "0092_research_report_audits"
branch_labels = None
depends_on = None

OLD_SUBJECT_CHECK = (
    "(subject_kind = 'report' AND artifact_id IS NOT NULL AND "
    "(subject_id IS NULL OR subject_id = artifact_id)) OR "
    "(subject_kind = 'risk_alert' AND artifact_id IS NULL AND "
    "subject_id IS NOT NULL AND payload_json IS NOT NULL AND payload_hash IS NOT NULL)"
)
NEW_SUBJECT_CHECK = (
    "(subject_kind = 'report' AND artifact_id IS NOT NULL AND "
    "(subject_id IS NULL OR subject_id = artifact_id)) OR "
    "(subject_kind IN ('risk_alert', 'action_revision') AND artifact_id IS NULL AND "
    "subject_id IS NOT NULL AND payload_json IS NOT NULL AND payload_hash IS NOT NULL)"
)

ACTION_TABLES = (
    "decision_opportunities",
    "decision_snapshots",
    "action_plan_revisions",
    "action_plan_heads",
    "action_condition_evaluations",
    "action_outcomes",
    "action_command_receipts",
    "research_skill_use_events",
    "experience_case_skill_links",
)
APPEND_ONLY_TABLES = (
    "decision_snapshots",
    "action_plan_revisions",
    "action_condition_evaluations",
    "action_outcomes",
    "action_command_receipts",
    "research_skill_use_events",
    "experience_case_skill_links",
)
IMMUTABLE_KEYS = {
    "decision_snapshots": (
        ("decision_id",),
        ("opportunity_id", "source_hash", "recipe_hash"),
    ),
    "action_plan_revisions": (("revision_id",), ("plan_id", "revision_no")),
    "action_condition_evaluations": (
        ("evaluation_id",),
        ("plan_id", "revision_id", "tick_at"),
    ),
    "action_outcomes": (
        ("outcome_id",),
        ("plan_id", "revision_id", "mode", "protocol_hash", "version"),
    ),
    "action_command_receipts": (("owner_id", "request_id"),),
    "research_skill_use_events": (
        ("event_id",),
        ("use_id", "stage", "node_id", "stage_request_id"),
    ),
    "experience_case_skill_links": (
        ("link_id",),
        ("experience_hash", "case_hash", "candidate_skill_hash", "bridge_protocol_hash"),
    ),
}


def upgrade() -> None:
    op.create_table(
        "decision_opportunities",
        sa.UniqueConstraint("key", name="uq_decision_opportunity_key"),
        sa.CheckConstraint(
            "status IN ('open','decided','missed','expired')", name="ck_decision_opportunity_status"
        ),
        sa.CheckConstraint(
            "kind IN ('scheduled','event','condition')", name="ck_decision_opportunity_kind"
        ),
        sa.CheckConstraint(
            "cohort IN ('prospective','diagnostic')", name="ck_decision_opportunity_cohort"
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_decision_opportunity_window"),
        sa.Column("opportunity_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("event_ref", sa.Text, nullable=True),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cohort", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_decision_opportunity_scope_time",
        "decision_opportunities",
        ["scope_id", "horizon", "start_at"],
    )
    op.create_table(
        "decision_snapshots",
        sa.UniqueConstraint(
            "opportunity_id", "source_hash", "recipe_hash", name="uq_decision_snapshot_source"
        ),
        sa.Column("decision_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("opportunity_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("source_result_ref", sa.Text, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_decision_snapshot_run_known", "decision_snapshots", ["run_id", "known_at"])
    op.create_table(
        "action_plan_revisions",
        sa.UniqueConstraint("plan_id", "revision_no", name="uq_action_plan_revision"),
        sa.CheckConstraint("revision_no >= 1", name="ck_action_revision_positive"),
        sa.CheckConstraint("valid_until > published_at", name="ck_action_revision_validity"),
        sa.Column("revision_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("revision_no", sa.Integer, nullable=False),
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("opportunity_id", sa.String(128), nullable=False),
        sa.Column("previous_id", sa.String(128), nullable=True),
        sa.Column("position_context", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_action_revision_decision_position",
        "action_plan_revisions",
        ["decision_id", "position_context"],
    )
    op.create_table(
        "action_plan_heads",
        sa.CheckConstraint("state_version >= 1", name="ck_action_head_version"),
        sa.CheckConstraint("lease_generation >= 0", name="ck_action_head_generation"),
        sa.CheckConstraint("enter_streak >= 0", name="ck_action_head_streak"),
        sa.CheckConstraint(
            "state IN ('proposed','armed','triggered','completed',"
            "'expired','invalidated','superseded')",
            name="ck_action_head_state",
        ),
        sa.CheckConstraint(
            "monitor_state IN ('active','paused','degraded','stopped')",
            name="ck_action_monitor_state",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_action_head_lease_pair",
        ),
        sa.Column("plan_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("current_revision_id", sa.String(128), nullable=False),
        sa.Column("state_version", sa.Integer, server_default="1", nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("monitor_state", sa.String(16), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_generation", sa.Integer, server_default="0", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_input_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enter_streak", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index(
        "ix_action_head_due",
        "action_plan_heads",
        ["monitor_state", "next_due_at", "lease_expires_at"],
    )
    op.create_table(
        "action_condition_evaluations",
        sa.UniqueConstraint("plan_id", "revision_id", "tick_at", name="uq_action_evaluation_tick"),
        sa.CheckConstraint("lease_generation >= 0", name="ck_action_evaluation_generation"),
        sa.Column("evaluation_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("tick_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_generation", sa.Integer, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_action_evaluation_plan_tick", "action_condition_evaluations", ["plan_id", "tick_at"]
    )
    op.create_table(
        "action_outcomes",
        sa.UniqueConstraint(
            "plan_id",
            "revision_id",
            "mode",
            "protocol_hash",
            "version",
            name="uq_action_outcome_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_action_outcome_version"),
        sa.CheckConstraint("mode IN ('paper','user_reported')", name="ck_action_outcome_mode"),
        sa.CheckConstraint(
            "status IN ('pending','scorable','unscorable','settled')",
            name="ck_action_outcome_status",
        ),
        sa.Column("outcome_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("opportunity_id", sa.String(128), nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("protocol_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_action_outcome_due", "action_outcomes", ["status", "due_at"])
    op.create_table(
        "action_command_receipts",
        sa.CheckConstraint("length(trim(owner_id)) > 0", name="ck_action_command_owner"),
        sa.CheckConstraint("length(trim(request_id)) > 0", name="ck_action_command_request"),
        sa.Column("owner_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.Text, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_action_command_plan_time", "action_command_receipts", ["plan_id", "accepted_at"]
    )
    op.create_table(
        "research_skill_use_events",
        sa.UniqueConstraint(
            "use_id", "stage", "node_id", "stage_request_id", name="uq_skill_use_request"
        ),
        sa.CheckConstraint("generation >= 1", name="ck_skill_use_generation"),
        sa.CheckConstraint(
            "stage IN ('selected','loaded','executed','consumed','failed')",
            name="ck_skill_use_stage",
        ),
        sa.Column("event_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("use_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("generation", sa.Integer, nullable=False),
        sa.Column("root_session_id", sa.String(256), nullable=True),
        sa.Column("execution_session_id", sa.String(256), nullable=True),
        sa.Column("skill_version_id", sa.String(128), nullable=False),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("workflow_hash", sa.String(64), nullable=False),
        sa.Column("method_binding_hash", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("node_id", sa.String(128), server_default="", nullable=False),
        sa.Column("stage_request_id", sa.String(128), nullable=False),
        sa.Column("parent_event_ref", sa.Text, nullable=True),
        sa.Column("original_ref", sa.Text, nullable=False),
        sa.Column("original_hash", sa.String(64), nullable=False),
        sa.Column("first_known_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_skill_use_run", "research_skill_use_events", ["run_id", "generation", "first_known_at"]
    )
    op.create_index(
        "ix_skill_use_version", "research_skill_use_events", ["skill_version_id", "first_known_at"]
    )
    op.create_table(
        "experience_case_skill_links",
        sa.UniqueConstraint(
            "experience_hash",
            "case_hash",
            "candidate_skill_hash",
            "bridge_protocol_hash",
            name="uq_experience_case_skill_bridge",
        ),
        sa.Column("link_id", sa.String(128), primary_key=True, nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("experience_id", sa.String(128), nullable=False),
        sa.Column("experience_hash", sa.String(64), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_hash", sa.String(64), nullable=False),
        sa.Column("candidate_skill_version_id", sa.String(128), nullable=False),
        sa.Column("candidate_skill_hash", sa.String(64), nullable=False),
        sa.Column("bridge_protocol_hash", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_experience_case_skill_scope_known",
        "experience_case_skill_links",
        ["scope_id", "available_at"],
    )

    if op.get_bind().dialect.name == "sqlite":
        for table in APPEND_ONLY_TABLES:
            conflicts = " OR ".join(
                "(" + " AND ".join(f"{name} = NEW.{name}" for name in key) + ")"
                for key in IMMUTABLE_KEYS[table]
            )
            op.execute(
                sa.text(
                    f"CREATE TRIGGER trg_{table}_immutable_insert BEFORE INSERT ON {table} "
                    f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {conflicts}) "
                    f"BEGIN SELECT RAISE(ABORT, 'UNIQUE immutable identity: {table}'); END"
                )
            )
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    sa.text(
                        f"CREATE TRIGGER trg_{table}_immutable_{operation.lower()} "
                        f"BEFORE {operation} ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{table}_immutable'); END"
                    )
                )
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_subject_identity", type_="check")
        batch.create_check_constraint("ck_outbox_subject_identity", NEW_SUBJECT_CHECK)


def downgrade() -> None:
    connection = op.get_bind()
    # Check every evidence container BEFORE any destructive DDL.
    for table in ACTION_TABLES:
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("decision_product_downgrade_nonempty_forbidden:" + table)
    if connection.execute(
        sa.text("SELECT 1 FROM outbox WHERE subject_kind = 'action_revision' LIMIT 1")
    ).first():
        raise RuntimeError("decision_product_downgrade_action_notification_forbidden")
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_subject_identity", type_="check")
        batch.create_check_constraint("ck_outbox_subject_identity", OLD_SUBJECT_CHECK)
    for table in reversed(ACTION_TABLES):
        op.drop_table(table)
