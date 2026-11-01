"""Server-timed immutable prospective comparison registrations."""

import sqlalchemy as sa
from alembic import op

revision = "0056_prospective_trials"
down_revision = "0055_usage_collection"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prospective_trials",
        sa.Column("trial_id", sa.String(80), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prospective_trials_registered_at", "prospective_trials", ["registered_at"])


def downgrade():
    op.drop_table("prospective_trials")
