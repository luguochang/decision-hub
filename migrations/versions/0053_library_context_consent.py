"""Freeze library context authorization with the admitted Run."""

import sqlalchemy as sa
from alembic import op

revision = "0053_library_context_consent"
down_revision = "0052_product_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "research_run_scopes",
        sa.Column(
            "allow_remote_library_content", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("research_run_scopes", sa.Column("request_hash", sa.String(64), nullable=True))

    op.add_column(
        "research_run_scopes",
        sa.Column("input_receipts_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("research_run_scopes", "input_receipts_json")
    op.drop_column("research_run_scopes", "request_hash")
    op.drop_column("research_run_scopes", "allow_remote_library_content")
