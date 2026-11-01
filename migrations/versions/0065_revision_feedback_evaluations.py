"""Add immutable owner/revision feedback evaluations; never backfill old opinions."""
import sqlalchemy as sa
from alembic import op

revision = "0065_revision_feedback_evaluations"
down_revision = "0064_evolution_planning_decisions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "revision_feedback_evaluations",
        sa.Column("binding_seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("binding_id", sa.String(128), nullable=False, unique=True),
        sa.Column("feedback_id", sa.String(128), nullable=False, unique=True),
        sa.Column("request_id", sa.String(256), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("revision_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("artifact_id", sa.String(128), nullable=True),
        sa.Column("schema_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("evaluation_id", sa.String(128), nullable=True, unique=True),
        sa.Column("evaluation_version", sa.String(128), nullable=False),
        sa.Column("value_payload_hash", sa.String(64), nullable=True),
        sa.Column("basis_json", sa.Text(), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("basis_hash", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('available', 'unavailable')",
                           name="ck_revision_feedback_status"),
        sa.CheckConstraint(
            "(status = 'available' AND reason_code IS NULL AND evaluation_id IS NOT NULL "
            "AND value_payload_hash IS NOT NULL) OR "
            "(status = 'unavailable' AND reason_code IS NOT NULL AND evaluation_id IS NULL "
            "AND value_payload_hash IS NULL)",
            name="ck_revision_feedback_value_presence",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_revision_feedback_owner_revision", "revision_feedback_evaluations",
                    ["owner_id", "revision_id"])
    op.create_index("ix_revision_feedback_evaluations_run_id", "revision_feedback_evaluations",
                    ["run_id"])


def downgrade():
    op.drop_index("ix_revision_feedback_evaluations_run_id",
                  table_name="revision_feedback_evaluations")
    op.drop_index("ix_revision_feedback_owner_revision",
                  table_name="revision_feedback_evaluations")
    op.drop_table("revision_feedback_evaluations")
