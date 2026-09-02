"""Allow immutable per-generation prompts in one DSH Session."""

import sqlalchemy as sa
from alembic import op

revision = "0024_dsh_prompt_generations"
down_revision = "0023_dsh_session_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dsh_session_prompts_v2",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("generation", sa.Integer(), primary_key=True),
        sa.Column("dsh_session_id", sa.String(256), nullable=False),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_dsh_session_prompts_v2_request_id"),
    )
    op.execute(
        "INSERT INTO dsh_session_prompts_v2 "
        "(run_id, generation, dsh_session_id, request_id, request_hash, prompt, created_at) "
        "SELECT run_id, 1, dsh_session_id, request_id, request_hash, prompt, created_at "
        "FROM dsh_session_prompts"
    )
    op.drop_table("dsh_session_prompts")
    op.rename_table("dsh_session_prompts_v2", "dsh_session_prompts")
    op.create_index(
        "ix_dsh_session_prompts_dsh_session_id",
        "dsh_session_prompts",
        ["dsh_session_id"],
    )


def downgrade() -> None:
    op.create_table(
        "dsh_session_prompts_v1",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("dsh_session_id", sa.String(256), nullable=False),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dsh_session_id", name="uq_dsh_session_prompts_v1_session_id"),
        sa.UniqueConstraint("request_id", name="uq_dsh_session_prompts_v1_request_id"),
    )
    op.execute(
        "INSERT INTO dsh_session_prompts_v1 "
        "(run_id, dsh_session_id, request_id, request_hash, prompt, created_at) "
        "SELECT run_id, dsh_session_id, request_id, request_hash, prompt, created_at "
        "FROM dsh_session_prompts WHERE generation = 1"
    )
    op.drop_index("ix_dsh_session_prompts_dsh_session_id", table_name="dsh_session_prompts")
    op.drop_table("dsh_session_prompts")
    op.rename_table("dsh_session_prompts_v1", "dsh_session_prompts")
