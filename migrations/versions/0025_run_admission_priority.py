"""Persist immutable Run admission origin and durable queue priority."""

import sqlalchemy as sa
from alembic import op

revision = "0025_run_admission_priority"
down_revision = "0024_dsh_prompt_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "admission_origin",
                sa.String(length=32),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_index("ix_runs_admission_origin", ["admission_origin"])
        batch.create_index("ix_runs_priority", ["priority"])


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index("ix_runs_priority")
        batch.drop_index("ix_runs_admission_origin")
        batch.drop_column("priority")
        batch.drop_column("admission_origin")
