"""Add durable and owner-visible research review schedules."""

import sqlalchemy as sa
from alembic import op

revision = "0037_review_schedule_ledger"
down_revision = "0036_strategy_calibration_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_schedules",
        sa.Column("review_plan_id", sa.String(128), primary_key=True),
        sa.Column("parent_run_id", sa.String(128), nullable=False),
        sa.Column("horizon", sa.String(16), nullable=False),
        sa.Column("trigger_type", sa.String(32), nullable=False),
        sa.Column("trigger_ref", sa.String(256), nullable=False),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("child_run_id", sa.String(128), nullable=True),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("trigger_hash", name="uq_review_schedule_trigger"),
    )
    op.create_index("ix_review_schedules_parent_run_id", "review_schedules", ["parent_run_id"])
    op.create_index("ix_review_schedules_horizon", "review_schedules", ["horizon"])
    op.create_index("ix_review_schedules_trigger_type", "review_schedules", ["trigger_type"])
    op.create_index("ix_review_schedules_trigger_at", "review_schedules", ["trigger_at"])
    op.create_index("ix_review_schedules_status", "review_schedules", ["status"])
    op.create_index("ix_review_schedules_child_run_id", "review_schedules", ["child_run_id"])


def downgrade() -> None:
    op.drop_table("review_schedules")
