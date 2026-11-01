"""Add immutable V3 quality receipts without rewriting historical reports."""

import sqlalchemy as sa
from alembic import op

revision = "0075_research_quality_sidecars"
down_revision = "0074_spot_scope_admissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_quality_sidecars",
        sa.Column("sidecar_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("logical_round", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("source_result_hash", sa.String(64), nullable=False),
        sa.Column("projected_result_hash", sa.String(64), nullable=True),
        sa.Column("quality_context_hash", sa.String(64), nullable=False),
        sa.Column("quality_decision_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("publication_mode", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sidecar_id"),
        sa.UniqueConstraint("run_id", "logical_round", "phase", name="uq_research_quality_phase"),
    )
    op.create_index(
        "ix_research_quality_run", "research_quality_sidecars", ["run_id", "created_at"]
    )


def downgrade() -> None:
    raise RuntimeError("research_quality_history_downgrade_forbidden_use_feature_pointer_rollback")
