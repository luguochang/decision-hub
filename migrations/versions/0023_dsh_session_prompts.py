"""Add the durable prompt projection fetched by the DSH Host bridge."""

import sqlalchemy as sa
from alembic import op

revision = "0023_dsh_session_prompts"
down_revision = "0022_dsh_session_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dsh_session_prompts",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("dsh_session_id", sa.String(256), nullable=False),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dsh_session_id", name="uq_dsh_session_prompts_session_id"),
        sa.UniqueConstraint("request_id", name="uq_dsh_session_prompts_request_id"),
    )


def downgrade() -> None:
    op.drop_table("dsh_session_prompts")
