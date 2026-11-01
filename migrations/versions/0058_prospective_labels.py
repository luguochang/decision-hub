"""Common archived market labels and fenced collection attempts for paired predictions."""

import sqlalchemy as sa
from alembic import op

revision = "0058_prospective_labels"
down_revision = "0057_prospective_pairs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prospective_labels",
        sa.Column("pair_id", sa.String(80), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
    )
    op.create_index("ix_prospective_labels_status", "prospective_labels", ["status"])
    op.create_index(
        "ix_prospective_labels_next_attempt_at", "prospective_labels", ["next_attempt_at"]
    )
    op.create_table(
        "prospective_label_attempts",
        sa.Column("pair_id", sa.String(80), primary_key=True),
        sa.Column("attempt", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
    )


def downgrade():
    op.drop_table("prospective_label_attempts")
    op.drop_table("prospective_labels")
