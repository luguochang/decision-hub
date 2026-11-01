"""Keep historical scope unknown; distinguish new Harness and direction evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0060_evolution_execution_scope"
down_revision = "0059_dsh_model_step_budget"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("candidate_versions", "experiments"):
        op.add_column(table, sa.Column("execution_scope", sa.String(32), nullable=True))


def downgrade():
    for table in ("experiments", "candidate_versions"):
        op.drop_column(table, "execution_scope")
