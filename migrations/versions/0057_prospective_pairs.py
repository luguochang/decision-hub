"""Durable first-input selection and separately claimed comparison arms."""

import sqlalchemy as sa
from alembic import op

revision = "0057_prospective_pairs"
down_revision = "0056_prospective_trials"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prospective_pairs",
        sa.Column("pair_id", sa.String(80), primary_key=True),
        sa.Column("trial_id", sa.String(80), nullable=False),
        sa.Column("study_id", sa.String(128), nullable=False),
        sa.Column("outlook_id", sa.String(128), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("source_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exclusion_reason", sa.String(128), nullable=True),
        sa.UniqueConstraint("trial_id", "study_id", name="uq_trial_study_pair"),
    )
    op.create_index("ix_prospective_pairs_trial_id", "prospective_pairs", ["trial_id"])
    op.create_index("ix_prospective_pairs_deadline_at", "prospective_pairs", ["deadline_at"])
    op.create_table(
        "prospective_arms",
        sa.Column("pair_id", sa.String(80), primary_key=True),
        sa.Column("arm", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("recipe_hash", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
    )
    op.create_index("ix_prospective_arms_status", "prospective_arms", ["status"])


def downgrade():
    op.drop_table("prospective_arms")
    op.drop_table("prospective_pairs")
