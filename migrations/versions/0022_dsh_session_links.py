"""Add the durable Hub Run to DSH Session correlation table."""

import sqlalchemy as sa
from alembic import op

revision = "0022_dsh_session_links"
down_revision = "0021_research_error_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dsh_session_links",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("dsh_session_id", sa.String(256), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("upstream_identity_json", sa.Text(), nullable=False),
        sa.Column("plugin_build_hash", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("trace_ref", sa.String(2048), nullable=True),
        sa.Column("result_ref", sa.String(2048), nullable=True),
        sa.Column("result_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dsh_session_id", name="uq_dsh_session_links_session_id"),
    )
    op.create_index("ix_dsh_session_links_state", "dsh_session_links", ["state"])


def downgrade() -> None:
    op.drop_index("ix_dsh_session_links_state", table_name="dsh_session_links")
    op.drop_table("dsh_session_links")
