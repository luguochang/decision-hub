"""Persist the submitted model-step watchdog without inventing historical budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0059_dsh_model_step_budget"
down_revision = "0058_prospective_labels"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "dsh_session_links", sa.Column("model_step_timeout_ms", sa.Integer(), nullable=True)
    )


def downgrade():
    op.drop_column("dsh_session_links", "model_step_timeout_ms")
