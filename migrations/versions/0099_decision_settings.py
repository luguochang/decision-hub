"""Frozen settings DDL: immutable revisions and mutable pending heads."""

import sqlalchemy as sa
from alembic import op

revision = "0099_decision_settings"
down_revision = "0098_event_main_flow"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decision_settings_revisions",
        sa.Column("revision_id", sa.String(128), primary_key=True),
        sa.Column("settings_key", sa.String(128), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("account_kind", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("instrument_id", sa.String(192), nullable=False),
        sa.Column("profile_ref", sa.String(256), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_hash", sa.String(64), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("settings_key", "revision_no", name="uq_settings_revision_number"),
        sa.CheckConstraint("revision_no >= 1", name="ck_settings_revision_number"),
        sa.CheckConstraint(
            "account_kind IN ('user_declared','paper')",
            name="ck_settings_revision_kind",
        ),
        sa.CheckConstraint("length(trim(account_id)) > 0", name="ck_settings_revision_account"),
    )
    op.create_index(
        "ix_settings_revision_owner_created",
        "decision_settings_revisions",
        ["owner_id", "created_at"],
    )
    op.create_table(
        "decision_settings_heads",
        sa.Column("settings_key", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("account_kind", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("instrument_id", sa.String(192), nullable=False),
        sa.Column("profile_ref", sa.String(256), nullable=False),
        sa.Column("current_revision_id", sa.String(128), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("reevaluation_pending", sa.Integer(), nullable=False),
        sa.Column("pending_revision_id", sa.String(128), nullable=True),
        sa.Column("pending_state_version", sa.Integer(), nullable=True),
        sa.Column("scan_generation", sa.Integer(), nullable=False),
        sa.Column("scan_after_plan_id", sa.String(128), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "owner_id",
            "account_kind",
            "account_id",
            "instrument_id",
            "profile_ref",
            name="uq_settings_partition",
        ),
        sa.CheckConstraint(
            "account_kind IN ('user_declared','paper')",
            name="ck_settings_head_kind",
        ),
        sa.CheckConstraint("length(trim(account_id)) > 0", name="ck_settings_head_account"),
        sa.CheckConstraint(
            "state_version >= 1 AND scan_generation >= 1", name="ck_settings_versions"
        ),
        sa.CheckConstraint("reevaluation_pending IN (0,1)", name="ck_settings_pending_flag"),
        sa.CheckConstraint(
            "(reevaluation_pending = 1 AND pending_revision_id IS NOT NULL "
            "AND pending_state_version IS NOT NULL AND pending_revision_id = current_revision_id "
            "AND pending_state_version = state_version AND next_due_at IS NOT NULL) OR "
            "(reevaluation_pending = 0 AND pending_revision_id IS NULL "
            "AND pending_state_version IS NULL AND next_due_at IS NULL)",
            name="ck_settings_pending_binding",
        ),
    )
    op.create_index(
        "ix_settings_pending_due",
        "decision_settings_heads",
        ["reevaluation_pending", "next_due_at", "settings_key"],
    )
    op.create_index(
        "ix_settings_owner_partition",
        "decision_settings_heads",
        ["owner_id", "account_kind", "account_id"],
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_decision_settings_revisions_immutable_insert "
            "BEFORE INSERT ON decision_settings_revisions WHEN EXISTS ("
            "SELECT 1 FROM decision_settings_revisions WHERE revision_id = NEW.revision_id "
            "OR (settings_key = NEW.settings_key AND revision_no = NEW.revision_no)) "
            "BEGIN SELECT RAISE(ABORT, "
            "'UNIQUE immutable identity: decision_settings_revisions'); END"
        )
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_decision_settings_revisions_immutable_{operation.lower()} "
                f"BEFORE {operation} ON decision_settings_revisions "
                "BEGIN SELECT RAISE(ABORT, 'decision_settings_revisions_immutable'); END"
            )


def downgrade():
    op.drop_table("decision_settings_heads")
    op.drop_table("decision_settings_revisions")
