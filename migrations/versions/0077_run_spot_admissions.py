"""Bind fresh first-trial proofs immutably to Run identity."""

import sqlalchemy as sa
from alembic import op

revision = "0077_run_spot_admissions"
down_revision = "0076_legacy_index_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_run_spot_admissions",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("study_id", sa.String(128), nullable=False),
        sa.Column("admission_id", sa.String(80), nullable=False),
        sa.Column("scope_id", sa.String(192), nullable=False),
        sa.Column("proof_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        "ix_run_spot_admission_source",
        "research_run_spot_admissions",
        ["admission_id", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError("run_spot_admission_audit_preservation_required")
